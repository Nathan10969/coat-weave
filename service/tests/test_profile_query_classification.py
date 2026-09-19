from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from io import BytesIO
import json
from unittest.mock import create_autospec

import pytest

SERVICE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SERVICE / "core"), str(SERVICE / "kg_tools")]
import kg_contract
import kg_expand_http_service as kg
REAL_REGISTRY = kg_contract.query_classification
REAL_CONTRACT_LOADER = kg_contract.load_tool_contract

AXES = ("application_domains", "coating_functions", "coating_layers", "supply_forms", "film_processes")


class Registry:
    def load_registry(self):
        return {**REAL_REGISTRY.load_registry(), "version": "fixture-v1", "property_families": kg.PROPERTY_FAMILY_CANONICAL_IDS}

    def axis_definitions(self):
        return {axis: {"values": ["a", "b"]} for axis in AXES}

    def project_profile(self, profile, source_id):
        values = {axis: list(profile.get(axis, [])) for axis in AXES}
        return {"version": "fixture-v1", "values": values, "mappings": [],
                "missing_axes": [axis for axis in AXES if not values[axis]],
                "unmapped": profile.get("unmapped", [])}

    def normalize_axis(self, axis, values):
        values = [] if values is None else values if isinstance(values, list) else [values]
        return {"values": [v for v in values if v in {"a", "b"}],
                "unsupported": [v for v in values if v not in {"a", "b"}], "adjustments": [], "candidates": []}

    def lookup(self, query, dimension=None, limit=20):
        return {"version": "fixture-v1", "items": [{"dimension": dimension or AXES[0], "value": "a"}]}


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(kg, "classification", Registry(), raising=False)
    contract = copy.deepcopy(kg_contract.load_tool_contract())
    monkeypatch.setattr(kg_contract, "query_classification", kg.classification)
    for name in ("kg.hybrid_search", "kg.sql_aggregate"):
        contract["tools"][name]["filter_fields"] = list(dict.fromkeys(contract["tools"][name]["filter_fields"] + list(AXES)))
    monkeypatch.setattr(kg_contract, "load_tool_contract", lambda: contract)
    monkeypatch.setattr(kg, "AGGREGATE_TARGETS", kg.AGGREGATE_TARGETS | set(AXES))
    monkeypatch.setattr(kg, "AGGREGATE_GROUP_BY", kg.AGGREGATE_GROUP_BY | set(AXES))
    monkeypatch.setattr(kg, "DOCUMENT_LEVEL_GROUP_BY", kg.DOCUMENT_LEVEL_GROUP_BY | set(AXES))


def store_fixture():
    store = kg.expand.KgStore()
    for source, doc, he, axes in [
        ("source-a", "WO1A1", "H1", {AXES[0]: ["a"], AXES[1]: ["a", "b"]}),
        ("source-a", "WO1A1", "H2", {AXES[0]: ["a"], AXES[1]: ["a", "b"]}),
        ("source-b", "WO1A1", "H1", {AXES[0]: ["b"]}),
        ("source-a", "WO2A1", "H3", None),
    ]:
        provenance = {"source_id": source, "object_id": f"{source}::{doc}::{he}", "file": "fixture.jsonl", "sha256": "fixture-sha"}
        store.add_hyperedge({"doc_id": doc, "hyperedge_id": he, "property": {"name": "adhesion", "canonical_id": "P_ADH"}}, provenance=provenance)
        if axes is not None:
            store.add_patent_profile({"doc_id": doc, **axes}, provenance={"source_id": source, "file": "patent_profiles.jsonl"})
    return store


def install_store(monkeypatch, store):
    monkeypatch.setattr(kg, "get_store", lambda: store)
    def cohort(filters, warnings):
        return {record["provenance"]["object_id"] for (kind, _), records in store.full_records_by_doc.items()
                if kind == "hyperedges" for record in records}
    monkeypatch.setattr(kg, "fetch_allowed_hyperedge_keys", create_autospec(kg.fetch_allowed_hyperedge_keys, side_effect=cohort))


def test_source_view_keeps_variants_without_mutating_store():
    store = store_fixture()
    before = copy.deepcopy(store.full_records_by_doc)
    view = kg.get_query_classification_view(store)
    assert len(view["rows"]) == 4
    a, _, b, missing = view["rows"]
    assert kg.profile_query_matches(a, {AXES[0]: ["a"]})
    assert not kg.profile_query_matches(b, {AXES[0]: ["a"]})
    assert not kg.profile_query_matches(missing, {AXES[0]: ["a"]})
    assert a["_query_classification"]["provenance"]["sha256"] == "fixture-sha"
    assert store.full_records_by_doc == before


@pytest.mark.parametrize("filters,expected", [
    ({AXES[0]: ["a", "b"]}, True),
    ({AXES[0]: ["a"], AXES[1]: ["b"]}, True),
    ({AXES[0]: ["a"], AXES[2]: ["a"]}, False),
])
def test_both_predicates_share_profile_hard_semantics(filters, expected):
    raw = kg.get_query_classification_view(store_fixture())["rows"][0]
    assert kg.raw_matches_search_hard_filters(raw, filters) is expected
    assert kg.raw_matches_aggregate_filters(raw, {}, filters) is expected


@pytest.mark.parametrize("tool", ["search", "aggregate"])
def test_unknown_property_is_unsupported_before_io(monkeypatch, tool):
    monkeypatch.setattr(kg, "get_store", lambda: pytest.fail("unsupported query accessed store"))
    filters = {"property_families": ["never_registered"]}
    result = (kg.run_hybrid_search({"query": "coating", "filters": filters}) if tool == "search"
              else kg.run_sql_aggregate({"intent": "distinct_count", "target": "doc_id", "filters": filters}))
    assert result["status"] == "unsupported"
    assert result["items"] == []
    assert result["unsupported_constraints"]


def test_known_property_is_hard_but_soft_hint_is_not():
    family, ids = next(iter(kg.PROPERTY_FAMILY_CANONICAL_IDS.items()))
    raw = {"doc_id": "D", "hyperedge_id": "H", "property_canonical_id": ids[0], "property": "test"}
    other = dict(raw, property_canonical_id="OTHER")
    for predicate in (lambda r, f: kg.raw_matches_search_hard_filters(r, f), lambda r, f: kg.raw_matches_aggregate_filters(r, {}, f)):
        assert predicate(raw, {"property_families": [family]})
        assert not predicate(other, {"property_families": [family]})
        assert predicate(other, {"property_canonical_ids_soft": list(ids)})


def test_group_counts_profiles_once_and_reports_pre_filter_coverage(monkeypatch):
    install_store(monkeypatch, store_fixture())
    result = kg.run_sql_aggregate({"intent": "group_count", "target": "doc_id", "group_by": [AXES[1]], "filters": {AXES[0]: ["a"]}})
    assert result["status"] == "ok"
    assert result["count_unit"] == "patent"
    assert {row["value"]: row["count"] for row in result["items"]} == {"a": 1, "b": 1}
    coverage = result["classification_coverage"]
    assert coverage["source_doc_count"] == 3
    assert coverage["axes"][AXES[0]]["missing_profile"] == 1
    assert result["summary"]["bucket_membership_count"] == 2


def test_db_empty_never_falls_back_to_raw(monkeypatch):
    install_store(monkeypatch, store_fixture())
    monkeypatch.setattr(kg, "fetch_allowed_hyperedge_keys", lambda filters, warnings: set())
    result = kg.run_sql_aggregate({"intent": "distinct_count", "target": "doc_id", "filters": {}})
    assert result["status"] == "empty"
    assert result["items"] == []


def test_db_failure_has_no_plausible_counts(monkeypatch):
    monkeypatch.setattr(kg, "get_store", store_fixture)
    monkeypatch.setattr(kg.expand, "load_pg_env", lambda path: {})
    def unavailable(env):
        raise OSError("private connection detail")
    monkeypatch.setattr(kg.expand, "pg_connect", unavailable)
    result = kg.run_sql_aggregate({"intent": "distinct_count", "target": "doc_id", "filters": {}})
    assert result["status"] == "error"
    assert result["error_code"] == "cohort_unavailable"
    assert result["summary"] == {}
    assert "private" not in str(result)


def test_invalid_aggregate_returns_receipt_not_unbound_local():
    result = kg.run_sql_aggregate({"intent": "bad", "target": "bad"})
    assert result["status"] == "error"
    assert result["items"] == []


def test_classification_lookup_uses_registry(monkeypatch):
    monkeypatch.setattr(kg, "get_store", lambda: pytest.fail("classification lookup loaded corpus"))
    result = kg.run_lookup_vocabulary({"query": "a", "dimension": AXES[0]})
    assert result["tool"] == "kg.lookup_vocabulary"
    assert result["items"][0]["value"] == "a"


def test_search_and_aggregate_same_source_and_local_recall_cohort(monkeypatch):
    store = store_fixture()
    install_store(monkeypatch, store)
    a = "source-a::WO1A1::H1"
    b = "source-b::WO1A1::H1"
    monkeypatch.setattr(kg, "fetch_allowed_hyperedge_keys", lambda filters, warnings: {a, b})
    monkeypatch.setattr(kg, "embed_query", lambda query: ([], {}))
    monkeypatch.setattr(kg.expand, "load_pg_env", lambda path: {})
    monkeypatch.setattr(kg.expand, "pg_connect", lambda env: nullcontext(None))
    candidates = [{"object_id": oid, "doc_id": "WO1A1", "score": 1, "metadata": {}} for oid in (a, b)]
    monkeypatch.setattr(kg, "search_dense", lambda conn, vector, filters, limit: candidates)
    monkeypatch.setattr(kg, "search_sparse", lambda conn, vector, filters, limit: [])
    monkeypatch.setattr(kg, "search_soft_property_candidates", lambda conn, filters, limit: [])
    for name in ("numeric_fact_recall_rows", "test_fact_recall_rows", "material_fact_recall_rows", "substrate_fact_recall_rows"):
        monkeypatch.setattr(kg, name, lambda store, query, filters, limit: [])
    filters = {AXES[0]: ["a"]}
    facts = create_autospec(kg.get_facts_map, side_effect=kg.get_facts_map)
    monkeypatch.setattr(kg, "get_facts_map", facts)
    search = kg.run_hybrid_search({"query": "coating", "filters": filters})
    assert facts.call_count <= 2
    aggregate = kg.run_sql_aggregate({"intent": "distinct_count", "target": "doc_id", "filters": filters})
    assert [item["object_id"] for item in search["items"]] == [a]
    assert search["items"][0]["classification"]["source_id"] == "source-a"
    assert aggregate["summary"]["matched_hyperedges"] == 1
    assert search["classification_coverage"] == aggregate["classification_coverage"]
    raw_only = {"object_id": "source-a::WO1A1::H2", "doc_id": "WO1A1"}
    assert kg.filter_search_items_by_hard_material_roles([raw_only], store, filters, allowed_object_ids={a, b}) == []


@pytest.mark.parametrize("axis", AXES)
@pytest.mark.parametrize("channel", ["dense", "sparse"])
def test_profile_scope_precedes_global_top_100_in_real_recall_sql(monkeypatch, axis, channel):
    store = kg.expand.KgStore()
    ranked = []
    eligible = set()
    for i in range(206):
        matching = i >= 101
        source = "matching-source" if matching else "other-source"
        doc = f"WO{i % 4 + 1}A1"
        he = f"H{i}"
        oid = f"{source}::{doc}::{he}"
        store.add_hyperedge({"doc_id": doc, "hyperedge_id": he}, provenance={"source_id": source, "object_id": oid})
        store.add_patent_profile({"doc_id": doc, axis: ["a" if matching else "b"]}, provenance={"source_id": source})
        ranked.append({"object_type": "hyperedge", "object_id": oid, "doc_id": doc,
                       "text_for_embedding": "fixture", "metadata": {}, f"{channel}_score": 1 / (i + 1)})
        if matching:
            eligible.add(oid)
    # Matching raw-only data must not enter the authoritative DB cohort.
    store.add_hyperedge({"doc_id": "WO1A1", "hyperedge_id": "raw-only"},
                        provenance={"source_id": "matching-source", "object_id": "raw-only"})
    install_store(monkeypatch, store)
    db_keys = {row["object_id"] for row in ranked} | {"unbound-db-object"}
    monkeypatch.setattr(kg, "fetch_allowed_hyperedge_keys", lambda filters, warnings: db_keys.copy())
    statements = []

    class Cursor:
        def execute(self, sql, params=None):
            if sql.strip().startswith("SET LOCAL"):
                return
            statements.append((sql, params))
            rows = ranked if ("SUM(p.weight" in sql) == (channel == "sparse") else []
            if "o.object_id = ANY(%s)" in sql:
                scope = next(value for value in params if isinstance(value, list))
                rows = [row for row in rows if row["object_id"] in scope]
            self.rows = rows[:params[-1]]
            self.description = [(key,) for key in ranked[0]]

        def fetchall(self):
            return [tuple(row.values()) for row in self.rows]

    monkeypatch.setattr(kg, "embed_query", lambda query: ([1.0], {"token": 1.0}))
    monkeypatch.setattr(kg.expand, "load_pg_env", lambda path: {})
    monkeypatch.setattr(kg.expand, "pg_connect", lambda env: nullcontext(SimpleNamespace(cursor=lambda: nullcontext(Cursor()))))
    for name in ("numeric_fact_recall_rows", "test_fact_recall_rows", "material_fact_recall_rows", "substrate_fact_recall_rows"):
        def local_recall(store, query, filters, *, limit):
            assert filters.get("_allowed_object_ids") == eligible
            return []
        monkeypatch.setattr(kg, name, local_recall)
    request = {"query": "corrosion protective coating", "filters": {axis: ["a"]}, "candidate_k": 100, "top_k": 50}
    aggregate = kg.run_sql_aggregate({"intent": "distinct_count", "target": "doc_id", "filters": request["filters"]})
    result = kg.run_hybrid_search(request)
    assert aggregate["summary"]["total_count"] == 4
    assert aggregate["summary"]["matched_hyperedges"] == 105
    assert result["status"] == "ok"
    assert result["summary"][f"{channel}_found"] == 100
    assert len(result["items"]) == 50
    assert len({item["doc_id"] for item in result["items"]}) == 4
    assert {item["object_id"] for item in result["items"]} <= eligible
    assert result["requested_filters"][axis] == result["effective_filters"][axis] == ["a"]
    assert "_allowed_object_ids" not in json.dumps(result)
    assert result["classification_coverage"] == aggregate["classification_coverage"]
    assert result["classification_pre_recall_scope"]["eligible_object_count"] == 105
    for sql, params in statements:
        assert "o.object_id = ANY(%s)" in sql
        assert sorted(eligible) in params
        assert params[-1] == 100
        if "dense_embedding" in sql:
            assert "AS MATERIALIZED" in sql
            assert "FROM scoped_objects o" in sql


def test_profile_empty_prerecall_scope_stops_before_embedding(monkeypatch):
    install_store(monkeypatch, store_fixture())
    monkeypatch.setattr(kg, "embed_query", lambda query: pytest.fail("empty scope reached embedding"))
    result = kg.run_hybrid_search({"query": "coating", "filters": {AXES[2]: ["a"]}})
    assert result["status"] == "empty"
    assert result["classification_pre_recall_scope"]["eligible_object_count"] == 0
    assert not result["classification_complete"]
    assert result["effective_filters"][AXES[2]] == ["a"]


def test_private_object_scope_is_exact_and_empty_is_not_unrestricted():
    a, _, b, _ = kg.get_query_classification_view(store_fixture())["rows"]
    oid = kg.raw_hyperedge_object_id(a)
    filters = {"_allowed_object_ids": {oid}}
    assert kg.raw_matches_search_hard_filters(a, filters)
    assert not kg.raw_matches_search_hard_filters(b, filters)
    assert not kg.raw_matches_search_hard_filters(a, {"_allowed_object_ids": set()})
    clauses, params = kg.search_filter_sql("o", {**kg.normalize_search_filters({}), "_allowed_object_ids": set()})
    assert "o.object_id = ANY(%s)" in clauses
    assert [] in params


@pytest.mark.parametrize("recall", ["numeric_fact_recall_rows", "test_fact_recall_rows", "material_fact_recall_rows", "substrate_fact_recall_rows"])
def test_local_recall_object_scope_is_applied_before_limit(monkeypatch, recall):
    store = store_fixture()
    fact = {"doc_id": "WO1A1", "fact_id": "F", "material": "fixture"}
    store.add_fact(fact, provenance={"source_id": "source-a"})
    monkeypatch.setattr(kg, "raw_fact_rows", lambda raw, facts: [fact])
    monkeypatch.setattr(kg, "query_requests_test_fact_recall", lambda query: True)
    monkeypatch.setattr(kg, "test_fact_immersion_score", lambda raw, facts, evidence: 1.0)
    monkeypatch.setattr(kg, "parse_numeric_query_comparisons", lambda query: [("duration_hours", "gt", 0)])
    monkeypatch.setattr(kg, "numeric_candidates_for_raw", lambda raw, facts, evidence, kind: [(10.0, "fixture")])
    monkeypatch.setattr(kg, "substrate_fact_match_score", lambda values, query_terms: 1.0)
    monkeypatch.setattr(kg, "substrate_query_terms", lambda query: {"fixture"})
    allowed = "source-a::WO1A1::H2"
    rows = getattr(kg, recall)(store, "fixture", {AXES[0]: ["a"], "_allowed_object_ids": {allowed}}, limit=1)
    assert [row["object_id"] for row in rows] == [allowed]


def test_soft_property_sql_uses_same_exact_scope_before_limit():
    statements = []
    class Cursor:
        description = []
        def execute(self, sql, params):
            statements.append((sql, params))
        def fetchall(self):
            return []
    conn = SimpleNamespace(cursor=lambda: nullcontext(Cursor()))
    filters = {**kg.normalize_search_filters({"property_canonical_ids_soft": ["P_ADH"]}),
               "_allowed_object_ids": {"source-a::WO1A1::H2"}}
    kg.search_soft_property_candidates(conn, filters, 1)
    sql, params = statements[0]
    assert sql.index("o.object_id = ANY(%s)") < sql.index("LIMIT %s")
    assert params[0] == ["source-a::WO1A1::H2"]
    assert sql.count("%s") == len(params)


@pytest.mark.parametrize("filters,expected", [
    ({AXES[0]: ["a", "b"]}, {"source-a::WO1A1::H1", "source-a::WO1A1::H2", "source-b::WO1A1::H1"}),
    ({AXES[0]: ["a", "b"], AXES[1]: ["b"]}, {"source-a::WO1A1::H1", "source-a::WO1A1::H2"}),
    ({AXES[0]: ["b"], AXES[1]: ["b"]}, set()),
])
def test_prerecall_preserves_axis_or_and_semantics(monkeypatch, filters, expected):
    install_store(monkeypatch, store_fixture())
    seen = []
    monkeypatch.setattr(kg, "embed_query", lambda query: ([], {}))
    monkeypatch.setattr(kg.expand, "load_pg_env", lambda path: {})
    monkeypatch.setattr(kg.expand, "pg_connect", lambda env: nullcontext(None))
    def dense(conn, vector, scoped_filters, limit):
        seen.append(scoped_filters["_allowed_object_ids"])
        return [{"object_id": oid, "doc_id": "WO1A1", "dense_score": 1.0, "metadata": {}} for oid in sorted(expected)]
    monkeypatch.setattr(kg, "search_dense", dense)
    monkeypatch.setattr(kg, "search_sparse", lambda conn, vector, filters, limit: [])
    monkeypatch.setattr(kg, "search_soft_property_candidates", lambda conn, filters, limit: [])
    result = kg.run_hybrid_search({"query": "coating", "filters": filters})
    assert seen == ([expected] if expected else [])
    assert {row["object_id"] for row in result["items"]} == expected
    assert result["classification_pre_recall_scope"]["eligible_object_count"] == len(expected)


def test_existing_adjacent_pass_keeps_profile_scope_before_recall(monkeypatch):
    store = store_fixture()
    install_store(monkeypatch, store)
    expected = {"source-a::WO1A1::H1", "source-a::WO1A1::H2"}
    monkeypatch.setattr(kg, "embed_query", lambda query: ([], {}))
    monkeypatch.setattr(kg.expand, "load_pg_env", lambda path: {})
    monkeypatch.setattr(kg.expand, "pg_connect", lambda env: nullcontext(None))
    seen = []
    def dense(conn, vector, filters, limit):
        seen.append(filters.get("_allowed_object_ids"))
        return []
    monkeypatch.setattr(kg, "search_dense", dense)
    monkeypatch.setattr(kg, "search_sparse", lambda conn, vector, filters, limit: [])
    monkeypatch.setattr(kg, "search_soft_property_candidates", lambda conn, filters, limit: [])
    items, relaxed, _ = kg.build_adjacent_search_items(
        {AXES[0]: ["a"], "materials": ["fixture-unmatched"]}, store, query="coating", candidate_k=100, top_k=10)
    assert seen == [expected]
    assert items
    assert {row["item"]["object_id"] for row in items} <= expected
    assert AXES[0] not in relaxed


LEGACY_SOURCE = "/root/coating/embedding/data/kg_286_aggregate"


def legacy_binding_fixture(source=LEGACY_SOURCE, *, raw_bare=False):
    store = kg.expand.KgStore()
    doc, he = "396_WO2020011839A1", "HE_396_WO2020011839A1_0001"
    raw = {"doc_id": doc, "hyperedge_id": he, "context_id": "CTX_396_WO2020011839A1_0007", "evidence_ids": ["E1"]}
    store.add_hyperedge(raw, provenance={"source_id": source, "object_id": he if raw_bare else f"{doc}::{he}", "file": "hyperedges.jsonl"})
    store.add_patent_profile({"doc_id": doc, AXES[0]: ["a"]}, provenance={"source_id": source})
    metadata = {"source": "hyperedges.jsonl", "context_id": raw["context_id"], "evidence_ids": ["E1"]}
    return store, raw, metadata


def test_inverse_legacy_binding_retains_qualified_db_id_and_raw_provenance():
    store, raw, metadata = legacy_binding_fixture(raw_bare=True)
    oid = f"{raw['doc_id']}::{raw['hyperedge_id']}"
    metadata["raw_hyperedge_id"] = raw["hyperedge_id"]
    before = copy.deepcopy(store.full_records_by_doc)
    view = kg.bind_query_classification_view(store, kg.HyperedgeCohort([(oid, raw["doc_id"], metadata)]))
    assert list(view["by_object_id"]) == [oid]
    bound = view["rows"][0]
    assert bound["_query_db_binding"] == {"method": "legacy_source_doc_hyperedge",
                                        "raw_object_id": raw["hyperedge_id"], "db_object_id": oid}
    assert bound["_query_classification"]["source_id"] == LEGACY_SOURCE
    assert bound["_query_classification"]["provenance"]["object_id"] == raw["hyperedge_id"]
    assert store.full_records_by_doc == before


@pytest.mark.parametrize("value", [None, "", " ", [], {}, 123, True, "wrong-HE", "different-doc::HE"])
def test_inverse_legacy_binding_rejects_malformed_or_conflicting_raw_id(value):
    store, raw, metadata = legacy_binding_fixture(raw_bare=True)
    metadata["raw_hyperedge_id"] = value
    oid = f"{raw['doc_id']}::{raw['hyperedge_id']}"
    cohort = kg.HyperedgeCohort([(oid, raw["doc_id"], metadata)])
    assert kg.bind_query_classification_view(store, cohort)["rows"] == []


@pytest.mark.parametrize("change", ["missing_raw_id", "qualified_id", "doc", "source", "v2", "refinish",
                                   "context", "evidence", "hyperedge_id", "metadata_doc", "metadata_source",
                                   "ambiguous_source", "provenance_conflict", "sticky_db_conflict"])
def test_inverse_legacy_binding_rejects_identity_and_provenance_conflicts(change):
    source = "/manifest/" + change if change in {"v2", "refinish"} else LEGACY_SOURCE
    store, raw, metadata = legacy_binding_fixture(source, raw_bare=True)
    doc = raw["doc_id"]
    oid = f"{doc}::{raw['hyperedge_id']}"
    metadata["raw_hyperedge_id"] = raw["hyperedge_id"]
    if change == "missing_raw_id":
        metadata.pop("raw_hyperedge_id")
    elif change == "qualified_id":
        oid = "different-doc::" + raw["hyperedge_id"]
    elif change == "doc":
        doc = "different-doc"
    elif change in {"source", "context", "evidence", "hyperedge_id", "metadata_doc", "metadata_source"}:
        key = {"source": "source", "context": "context_id", "evidence": "evidence_ids",
               "hyperedge_id": "hyperedge_id", "metadata_doc": "doc_id", "metadata_source": "source_id"}[change]
        metadata[key] = ["contradiction"] if change == "evidence" else "contradiction"
    elif change == "ambiguous_source":
        store.add_hyperedge(raw, provenance={"source_id": "/different/source", "object_id": "other::H"})
    elif change == "provenance_conflict":
        metadata.update(source_id=LEGACY_SOURCE, provenance={"source_id": "/different/source"})
    row = (oid, doc, metadata)
    rows = [row, (oid, "contradiction", metadata), row] if change == "sticky_db_conflict" else [row]
    assert kg.bind_query_classification_view(store, kg.HyperedgeCohort(rows))["rows"] == []


@pytest.mark.parametrize("prefixed", [False, True])
def test_db_binding_retains_actual_id_without_mutating_source(prefixed):
    store, raw, metadata = legacy_binding_fixture()
    oid = f"{raw['doc_id']}::{raw['hyperedge_id']}" if prefixed else raw["hyperedge_id"]
    before = copy.deepcopy(store.full_records_by_doc)
    cohort = kg.HyperedgeCohort([(oid, raw["doc_id"], metadata)])
    view = kg.bind_query_classification_view(store, cohort)
    assert list(view["by_object_id"]) == [oid]
    bound = view["rows"][0]
    assert bound["object_id"] == oid
    assert bound["_query_classification"]["source_id"] == LEGACY_SOURCE
    assert bound["_query_classification"]["provenance"]["object_id"] == f"{raw['doc_id']}::{raw['hyperedge_id']}"
    assert store.full_records_by_doc == before


def test_existing_exact_binding_does_not_reinterpret_db_source_identifier():
    store, raw, metadata = legacy_binding_fixture("/manifest/v2-root")
    oid = f"{raw['doc_id']}::{raw['hyperedge_id']}"
    metadata.update(source_id="serialized-collection-id", context_id="old-db-context")
    cohort = kg.HyperedgeCohort([(oid, raw["doc_id"], metadata)])
    view = kg.bind_query_classification_view(store, cohort)
    assert view["by_object_id"][oid]["_query_classification"]["source_id"] == "/manifest/v2-root"
    assert view["by_object_id"][oid]["_query_db_binding"]["method"] == "exact_object_id"


@pytest.mark.parametrize("change", ["doc", "source", "v2", "refinish", "context", "evidence", "metadata_source_id", "ambiguous_source"])
def test_legacy_bare_binding_rejects_contradiction_or_source_ambiguity(change):
    source = "/root/coating/embedding/data/kg_v2" if change == "v2" else "/root/coating/embedding/data/refinish" if change == "refinish" else LEGACY_SOURCE
    store, raw, metadata = legacy_binding_fixture(source)
    db_doc = raw["doc_id"]
    if change == "doc":
        db_doc = "different-doc"
    elif change == "source":
        metadata["source"] = "other.jsonl"
    elif change == "context":
        metadata["context_id"] = "contradiction"
    elif change == "evidence":
        metadata["evidence_ids"] = ["contradiction"]
    elif change == "metadata_source_id":
        metadata["source_id"] = "/different/source"
    elif change == "ambiguous_source":
        store.add_hyperedge(raw, provenance={"source_id": "/different/source", "object_id": "different-source::H"})
    cohort = kg.HyperedgeCohort([(raw["hyperedge_id"], db_doc, metadata)])
    assert kg.bind_query_classification_view(store, cohort)["rows"] == []


def test_bare_binding_allows_absent_optional_context_not_contradictions():
    store, raw, _ = legacy_binding_fixture()
    cohort = kg.HyperedgeCohort([(raw["hyperedge_id"], raw["doc_id"], {"source": "hyperedges.jsonl"})])
    assert len(kg.bind_query_classification_view(store, cohort)["rows"]) == 1


def test_conflicting_db_identity_is_sticky_after_third_duplicate():
    store, raw, metadata = legacy_binding_fixture()
    row = (raw["hyperedge_id"], raw["doc_id"], metadata)
    cohort = kg.HyperedgeCohort([row, (row[0], "different-doc", metadata), row])
    view = kg.bind_query_classification_view(store, cohort)
    assert view["rows"] == []
    assert view["ambiguous_object_ids"] == [row[0]]


def test_explicit_legacy_source_hint_disambiguates_natural_key():
    store, raw, metadata = legacy_binding_fixture()
    store.add_hyperedge(raw, provenance={"source_id": "/different/source", "object_id": "other::H"})
    metadata["source_id"] = LEGACY_SOURCE
    cohort = kg.HyperedgeCohort([(raw["hyperedge_id"], raw["doc_id"], metadata)])
    view = kg.bind_query_classification_view(store, cohort)
    assert view["rows"][0]["_query_classification"]["source_id"] == LEGACY_SOURCE


@pytest.mark.parametrize("inverse", [False, True])
def test_legacy_binding_flows_through_aggregate_search_and_local_recall(monkeypatch, inverse):
    store, raw, metadata = legacy_binding_fixture(raw_bare=inverse)
    oid = f"{raw['doc_id']}::{raw['hyperedge_id']}" if inverse else raw["hyperedge_id"]
    if inverse:
        metadata["raw_hyperedge_id"] = raw["hyperedge_id"]
    cohort = kg.HyperedgeCohort([(oid, raw["doc_id"], metadata)])
    monkeypatch.setattr(kg, "get_store", lambda: store)
    monkeypatch.setattr(kg, "fetch_allowed_hyperedge_keys", lambda filters, warnings: cohort)
    monkeypatch.setattr(kg, "embed_query", lambda query: ([], {}))
    monkeypatch.setattr(kg.expand, "load_pg_env", lambda path: {})
    monkeypatch.setattr(kg.expand, "pg_connect", lambda env: nullcontext(None))
    captured = []
    def dense(conn, vector, filters, limit):
        assert filters["_allowed_object_ids"] == {oid}
        captured.append(filters)
        return [{"object_id": oid, "doc_id": raw["doc_id"], "dense_score": 1.0, "metadata": metadata}]
    monkeypatch.setattr(kg, "search_dense", dense)
    monkeypatch.setattr(kg, "search_sparse", lambda conn, vector, filters, limit: [])
    monkeypatch.setattr(kg, "search_soft_property_candidates", lambda conn, filters, limit: [])
    filters = {AXES[0]: ["a"]}
    aggregate = kg.run_sql_aggregate({"intent": "distinct_count", "target": "doc_id", "filters": filters})
    search = kg.run_hybrid_search({"query": "coating", "filters": filters})
    assert aggregate["summary"]["matched_hyperedges"] == aggregate["summary"]["total_count"] == 1
    assert [item["object_id"] for item in search["items"]] == [oid]
    assert search["classification_coverage"]["unbound_db_object_count"] == 0
    assert search["classification_coverage"] == aggregate["classification_coverage"]
    monkeypatch.setattr(kg, "query_requests_test_fact_recall", lambda query: True)
    monkeypatch.setattr(kg, "test_fact_immersion_score", lambda raw, facts, evidence: 1.0)
    rows = kg.test_fact_recall_rows(store, "fixture", captured[0], limit=1)
    assert [row["object_id"] for row in rows] == [oid]


@pytest.mark.parametrize("field", ["applications", "substrates", "test_methods", "test_standards"])
def test_explicit_legacy_conditions_are_hard_in_both(field):
    raw = {"doc_id": "D", "hyperedge_id": "H", field: ["fixture-value"]}
    for value, expected in [("fixture-value", True), ("absent-value", False)]:
        filters = {field: [value]}
        assert kg.raw_matches_search_hard_filters(raw, filters) is expected
        assert kg.raw_matches_aggregate_filters(raw, {}, filters) is expected


def test_source_bound_fact_ids_do_not_borrow_other_source():
    store = store_fixture()
    for source, value in [("source-a", "a"), ("source-b", "b")]:
        store.add_fact({"doc_id": "WO1A1", "fact_id": "F", "applications": [value]}, provenance={"source_id": source})
    rows = kg.get_query_classification_view(store)["rows"]
    a = dict(rows[0], fact_ids=["F"])
    b = dict(rows[2], fact_ids=["F"])
    assert kg.raw_matches_search_hard_filters(a, {"applications": ["a"]})
    assert not kg.raw_matches_search_hard_filters(b, {"applications": ["a"]})


def test_lookup_handler_uses_unified_name_without_http_server():
    handler = object.__new__(kg.Handler)
    handler.path = "/tools/kg.lookup_vocabulary"
    body = json.dumps({"query": "a", "dimension": AXES[0]}).encode()
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)
    handler.authorized = lambda: True
    responses = []
    handler.send_json = lambda payload, status=200: responses.append((payload, status))
    handler.do_POST()
    assert responses[0][1] == 200
    assert responses[0][0]["tool"] == "kg.lookup_vocabulary"


def test_material_lookup_is_existing_kg_vocabulary(monkeypatch):
    store = {"hyperedges": [{"doc_id": "D", "hyperedge_id": "H", "resin": [{"name": "Fixture resin", "canonical_id": "CE_FIXTURE"}]}]}
    monkeypatch.setattr(kg, "get_store", lambda: store)
    result = kg.run_lookup_vocabulary({"query": "fixture", "dimension": "materials"})
    assert result["items"][0]["canonical_id"] == "CE_FIXTURE"
    assert result["items"][0]["vocabulary_scope"] == "loaded_kg_not_db_cohort"


def test_material_lookup_ranks_exact_aliases_before_related_without_id_expansion(monkeypatch):
    monkeypatch.setattr(kg, "expand_material_values", lambda items: [*items, "Fixture monomer", "MAT_ABC"])
    store = {"hyperedges": [{"doc_id": "D", "hyperedge_id": "H", "resin": [
        {"name": "50% ABC resin", "canonical_id": "MAT_50_ABC"},
        {"name": "ABC", "canonical_id": "MAT_ABC"},
        {"name": "Fixture monomer", "canonical_id": "MAT_MONOMER"},
        {"name": "xABCx unrelated", "canonical_id": "MAT_X"}]}]}
    monkeypatch.setattr(kg, "get_store", lambda: store)
    result = kg.run_lookup_vocabulary({"query": "ABC", "dimension": "materials", "limit": 2})
    assert [item["value"] for item in result["items"]] == ["ABC", "Fixture monomer"]
    assert result["items"] == result["exact_items"]
    assert result["related_items"] == []
    assert result["match_counts"] == {"exact": 2, "related": 1, "browse": 0}
    assert result["match_truncated"] == {"exact": False, "related": True, "browse": False}
    assert result["total"] == 3 and result["truncated"]
    assert result["canonical_id_expansion_complete"] is False
    assert result["suggested_filters"] == {"materials": ["ABC", "Fixture monomer", "MAT_ABC"]}
    assert "material_canonical_ids" not in result["suggested_filters"]


def test_material_lookup_related_is_not_exact_and_canonical_lookup_is_exact(monkeypatch):
    store = {"hyperedges": [{"doc_id": "D", "hyperedge_id": "H", "resin": [
        {"name": "50% ABC resin", "canonical_id": "MAT_50_ABC"}]}]}
    monkeypatch.setattr(kg, "get_store", lambda: store)
    related = kg.run_lookup_vocabulary({"query": "ABC", "dimension": "materials"})
    assert related["exact_items"] == []
    assert related["related_items"] == related["items"]
    exact = kg.run_lookup_vocabulary({"query": "MAT_50_ABC", "dimension": "materials"})
    assert exact["exact_items"] == exact["items"]
    assert exact["items"][0]["match_basis"] == "canonical_id"
    browse = kg.run_lookup_vocabulary({"query": "", "dimension": "materials"})
    assert browse["items"][0]["match_type"] == "browse"
    assert browse["suggested_filters"] == {}


def test_material_lookup_real_closed_alias_family_is_reused(monkeypatch):
    monkeypatch.setattr(kg, "get_store", lambda: {"hyperedges": [{"doc_id": "D", "hyperedge_id": "H", "monomer": [
        {"name": "glycidyl methacrylate", "canonical_id": "MAT_glycidyl_methacrylate"}]}]})
    result = kg.run_lookup_vocabulary({"query": "GMA", "dimension": "materials"})
    assert result["items"][0]["match_type"] == "exact"
    assert result["items"][0]["match_basis"] == "registered_alias"
    assert "glycidyl methacrylate" in result["suggested_filters"]["materials"]


def test_material_lookup_preserves_source_qualified_canonical_rows(monkeypatch):
    store = kg.expand.KgStore()
    for source in ["source-a", "source-b"]:
        store.add_hyperedge({"doc_id": "D", "hyperedge_id": "H", "resin": [{"name": "ABC", "canonical_id": "MAT_ABC"}]},
                            provenance={"source_id": source, "object_id": f"{source}::D::H"})
    monkeypatch.setattr(kg, "get_store", lambda: store)
    result = kg.run_lookup_vocabulary({"query": "ABC", "dimension": "materials"})
    assert result["total"] == result["match_counts"]["exact"] == 2
    assert [item["source_id"] for item in result["exact_items"]] == ["source-a", "source-b"]
    for item in result["items"]:
        assert {example["source_id"] for example in item["examples"]} == {item["source_id"]}
        assert {example["object_id"] for example in item["examples"]} == {f"{item['source_id']}::D::H"}
    assert result["canonical_id_expansion_complete"] is False


def test_classification_only_lookup_does_not_suggest_material_constraint(monkeypatch):
    monkeypatch.setattr(kg, "get_store", lambda: {"hyperedges": []})
    result = kg.run_lookup_vocabulary({"query": "a"})
    assert result["exact_items"]
    assert result["suggested_filters"] == {}


def test_real_registry_projection_and_normalization_round_trip(monkeypatch):
    monkeypatch.setattr(kg, "classification", REAL_REGISTRY)
    monkeypatch.setattr(kg_contract, "query_classification", REAL_REGISTRY)
    monkeypatch.setattr(kg_contract, "load_tool_contract", REAL_CONTRACT_LOADER)
    source = "/root/coating/embedding/data/kg_286_aggregate"
    store = kg.expand.KgStore()
    store.add_hyperedge({"doc_id": "WO1A1", "hyperedge_id": "H"}, provenance={"source_id": source, "object_id": "O"})
    store.add_patent_profile({"doc_id": "WO1A1", "coating_family": "anticorrosive_primer"}, provenance={"source_id": source})
    install_store(monkeypatch, store)
    result = kg.run_sql_aggregate({"intent": "group_count", "target": "doc_id", "group_by": ["coating_layers"],
                                  "filters": {"coating_functions": ["Anti-Corrosion"]}})
    assert result["status"] == "ok"
    assert result["effective_filters"]["coating_functions"] == ["anticorrosion"]
    assert result["items"][0]["value"] == "primer"
    assert result["items"][0]["count"] == 1
    assert result["classification_registry_version"] == REAL_REGISTRY.load_registry()["version"]


def test_cohort_query_is_unconditional_and_retains_exact_object_ids(monkeypatch):
    statements = []
    class Cursor:
        def execute(self, sql, params):
            statements.append((sql, params))
        def fetchall(self):
            return [("source-a::WO1::H", "WO1", {}), ("source-b::WO1::H", "WO1", {})]
    conn = SimpleNamespace(cursor=lambda: nullcontext(Cursor()))
    monkeypatch.setattr(kg.expand, "load_pg_env", lambda path: {})
    monkeypatch.setattr(kg.expand, "pg_connect", lambda env: nullcontext(conn))
    result = kg.fetch_allowed_hyperedge_keys({}, [])
    assert result == {"source-a::WO1::H", "source-b::WO1::H"}
    assert result.records["source-a::WO1::H"] == ("WO1", {})
    assert "SELECT o.object_id" in statements[0][0]
    assert "object_type = 'hyperedge'" in statements[0][0]


def test_search_db_failure_is_structured_before_embedding(monkeypatch):
    monkeypatch.setattr(kg, "get_store", store_fixture)
    def failed(filters, warnings):
        raise kg.CohortUnavailable("private")
    monkeypatch.setattr(kg, "fetch_allowed_hyperedge_keys", failed)
    monkeypatch.setattr(kg, "embed_query", lambda query: pytest.fail("DB failure reached embedding"))
    result = kg.run_hybrid_search({"query": "coating"})
    assert result["error_code"] == "cohort_unavailable"
    assert result["exact_items"] == []
    assert result["summary"] == {}


def test_ambiguous_object_ids_are_not_joined_by_natural_key():
    store = kg.expand.KgStore()
    for source in ["source-a", "source-b"]:
        store.add_hyperedge({"doc_id": "D", "hyperedge_id": "H"}, provenance={"source_id": source, "object_id": "O"})
    view = kg.get_query_classification_view(store)
    assert view["rows"] == []
    assert view["ambiguous_object_ids"] == ["O"]


def test_lookup_limit_and_no_new_kind_field(monkeypatch):
    store = {"hyperedges": [{"doc_id": "D", "hyperedge_id": "H", "resin": [
        {"name": f"Material {i:03}", "canonical_id": f"CE_{i}"} for i in range(105)]}]}
    monkeypatch.setattr(kg, "get_store", lambda: store)
    result = kg.run_lookup_vocabulary({"query": "", "dimension": "materials", "limit": 100})
    assert len(result["items"]) == 100
    assert result["total"] == 105
    assert result["truncated"]
    assert kg.run_lookup_vocabulary({"kind": "material"})["status"] == "unsupported"


def test_group_memberships_do_not_shrink_with_limit(monkeypatch):
    install_store(monkeypatch, store_fixture())
    result = kg.run_sql_aggregate({"intent": "group_count", "target": AXES[1], "filters": {AXES[0]: ["a"]}, "limit": 1})
    assert len(result["items"]) == 1
    assert result["summary"]["bucket_membership_count"] == 2


def test_coverage_exposes_unmapped_separately_from_missing_profile(monkeypatch):
    store = store_fixture()
    store.add_patent_profile({"doc_id": "WO2A1", "unmapped": [{"raw": "unreviewed", "reason": "unreviewed_value"}]}, provenance={"source_id": "source-a"})
    install_store(monkeypatch, store)
    result = kg.run_sql_aggregate({"intent": "distinct_count", "target": "doc_id", "filters": {AXES[0]: ["b"]}})
    coverage = result["classification_coverage"]
    assert coverage["unmapped_profile_count"] == 1
    assert coverage["axes"][AXES[0]]["missing_profile"] == 0
    assert coverage["unresolved_examples"][0]["unmapped"][0]["raw"] == "unreviewed"


def test_resin_distinct_intent_is_not_rewritten_as_group_count(monkeypatch):
    store = {"hyperedges": [{"doc_id": "WO1A1", "hyperedge_id": "H1", "resin_system": "epoxy"},
                             {"doc_id": "WO1A1", "hyperedge_id": "H2", "resin_system": "epoxy"}]}
    monkeypatch.setattr(kg, "get_store", lambda: store)
    monkeypatch.setattr(kg, "fetch_allowed_hyperedge_keys", lambda filters, warnings: {"WO1A1::H1", "WO1A1::H2"})
    result = kg.run_sql_aggregate({"intent": "distinct_count", "target": "resin_system"})
    assert result["query_interpretation"]["intent"] == "distinct_count"
    assert result["count_unit"] == "patent"
    assert result["summary"]["distinct_count_unit"] == "resin_system"
    assert result["summary"]["distinct_count"] == 1
    assert result["items"][0]["count"] == result["items"][0]["doc_count"] == 1


@pytest.mark.parametrize("intent", ["group_count", "distinct_count"])
@pytest.mark.parametrize("systems,known_count", [(["epoxy", "epoxy", None], 1), ([None], 0), ([], 0)])
def test_resin_summary_total_is_patents_not_classes_or_page(intent, systems, known_count):
    rows = [{"doc_id": f"WO{i + 1}A1", "hyperedge_id": f"H{i}", "resin_system": system}
            for i, system in enumerate(systems)]
    if rows:
        rows.append(dict(rows[0], hyperedge_id="duplicate-doc"))
    result = kg.run_resin_system_aggregate(
        matched=rows, all_hyperedges=rows, patents={}, evidence_map={}, target="resin_system",
        group_by=[], filters={}, limit=1, include_examples=False, warnings=[], intent=intent,
    )
    assert result["summary"]["total_count"] == len(systems)
    assert result["count_unit"] == "patent"
    assert result["cohort_mode"] == "document"
    assert result["summary"]["matched_doc_count"] == len(systems)
    assert result["summary"]["distinct_count"] == known_count
    assert result["summary"]["distinct_count_unit"] == "resin_system"
    assert result["summary"]["unknown_doc_count"] == systems.count(None)
    assert result["summary"]["matched_hyperedges"] == len(rows)
    assert all(item["count"] == item["doc_count"] for item in result["items"])
    assert len(result["items"]) <= 1


@pytest.mark.parametrize("aggregate_request", [
    {"intent": "distinct_count", "target": "resin_system"},
    {"intent": "group_count", "target": "resin_system"},
])
def test_resin_total_survives_aggregate_wrapper_and_real_answer_consumer(monkeypatch, aggregate_request):
    import answering

    rows = [{"doc_id": doc, "hyperedge_id": he, "resin_system": system}
            for doc, he, system in [("WO1A1", "H1", "epoxy"), ("WO1A1", "H2", "epoxy"),
                                    ("WO2A1", "H3", "epoxy"), ("WO3A1", "H4", None)]]
    monkeypatch.setattr(kg, "get_store", lambda: {"hyperedges": rows})
    monkeypatch.setattr(kg, "fetch_allowed_hyperedge_keys", lambda filters, warnings: {
        f"{row['doc_id']}::{row['hyperedge_id']}" for row in rows})
    result = kg.run_sql_aggregate({**aggregate_request, "limit": 1})
    answer = answering.structured_tool_fallback_answer({"tool_observations": [
        {"tool": "kg.sql_aggregate", "status": result["status"], "result": result}]})
    assert " 3 " in answer
    assert "\u7bc7\u4e13\u5229" in answer
    assert result["summary"]["total_count"] == 3
    assert result["count_unit"] == "patent"
    assert result["summary"]["distinct_count"] == 1
    assert result["summary"]["returned"] == 1
    assert result["items"][0]["count"] == 2


def test_profile_projection_runs_once_per_source_document(monkeypatch):
    project = create_autospec(kg.classification.project_profile, side_effect=kg.classification.project_profile)
    monkeypatch.setattr(kg.classification, "project_profile", project)
    kg.get_query_classification_view(store_fixture())
    assert project.call_count == 2


def test_incomplete_classification_is_explicit_not_true_zero(monkeypatch):
    install_store(monkeypatch, store_fixture())
    result = kg.run_sql_aggregate({"intent": "distinct_count", "target": "doc_id", "filters": {AXES[2]: ["a"]}})
    assert result["status"] == "empty"
    assert result["cohort_available"] is True
    assert result["classification_complete"] is False
    assert "classification_coverage_incomplete_not_evidence_of_absence" in result["warnings"]


def test_conflicting_context_record_id_stays_excluded():
    store = store_fixture()
    for value in ("first", "second", "third"):
        store.add_fact({"doc_id": "WO1A1", "fact_id": "F", "applications": [value]}, provenance={"source_id": "source-a"})
    raw = dict(kg.get_query_classification_view(store)["rows"][0], fact_ids=["F"])
    assert kg.raw_fact_rows(raw, {"F": {"fact_id": "F", "applications": ["third"]}}) == []


def test_material_recall_never_crosses_source_fact_ids():
    store = kg.expand.KgStore()
    for source, material in [("source-a", "Zinc dust"), ("source-b", "Titanium dioxide")]:
        origin = {"source_id": source, "object_id": source + "::D::H"}
        store.add_hyperedge({"doc_id": "D", "hyperedge_id": "H", "context_id": "C", "fact_ids": ["F"]}, provenance=origin)
        store.add_fact({"doc_id": "D", "fact_id": "F", "context_id": "C", "material": material, "material_role": "resin"}, provenance=origin)
    zinc = kg.material_fact_recall_rows(store, "zinc dust", {}, limit=20)
    titanium = kg.material_fact_recall_rows(store, "titanium dioxide", {}, limit=20)
    assert {row["object_id"] for row in zinc} == {"source-a::D::H"}
    assert {row["object_id"] for row in titanium} == {"source-b::D::H"}
