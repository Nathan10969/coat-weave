from __future__ import annotations

import copy
import json
import sys
import zipfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

KG_TOOLS = Path(__file__).resolve().parents[1] / "kg_tools"
sys.path.insert(0, str(KG_TOOLS))

import expand_hyperedge_multihop as expand
import kg_expand_http_service as service


def fixture():
    store = expand.KgStore()
    quote = "  " + "evidence line\n" * 100 + "  "
    raw = {
        "doc_id": "D1", "hyperedge_id": "H1", "context_id": "C1",
        "fact_ids": [f"F{i}" for i in range(25)],
        "evidence_ids": [f"E{i}" for i in range(7)],
        "result": {"value": 0, "detail": quote},
        "custom_nested": {"all": list(range(40))},
        "provenance": {"page": 7, "quote": quote},
    }
    store.add_hyperedge(raw)
    for i in range(25):
        store.add_fact({"doc_id": "D1", "fact_id": f"F{i}", "context_id": "C1",
                        "evidence_id": f"E{i % 7}", "value": i,
                        "nested": {"quote": quote}})
    for i in range(7):
        store.add_evidence({"doc_id": "D1", "evidence_id": f"E{i}", "quote": quote,
                            "provenance": {"page": i, "bbox": [1, 2, 3, 4]}})
    store.add_patent({"doc_id": "D1", "title": "Test", "custom": {"original": True}})
    store.add_patent_profile({"doc_id": "D1", "custom": {"original": True}})
    db = {"D1::H1": {"object_id": "D1::H1", "object_type": "hyperedge",
                      "doc_id": "D1", "metadata": {"hyperedge_id": "H1"}}}
    return store, db, raw, quote


def run(store, db, **request):
    with patch.object(service, "get_store", return_value=store), \
         patch.object(service, "resolve_kg_sources", return_value=((), ())), \
         patch.object(expand, "load_pg_env", return_value={}), \
         patch.object(expand, "pg_connect", return_value=nullcontext(None)), \
         patch.object(expand, "fetch_db_hyperedges", return_value=db):
        return service.run_expand({"object_ids": list(db), **request})


def test_full_preserves_all_direct_records_and_quote_fidelity():
    store, db, raw, quote = fixture()
    response = run(store, db, evidence_mode="full", max_context_facts=1, max_evidence_per_item=1)
    item = response["items"][0]
    assert response["status"] == "ok"
    assert response["evidence_mode"] == "full"
    assert item["hyperedge_raw"] == raw
    assert item["hyperedge_summary"]["custom_nested"] == raw["custom_nested"]
    assert len(item["facts"]) == 25
    assert len(item["evidence"]) == 7
    assert all(row["quote"] == quote and row["raw"]["quote"] == quote for row in item["evidence"])
    assert item["facts"][0]["raw"]["nested"]["quote"] == quote
    assert item["patent"]["custom"] == {"original": True}
    assert item["patent_profile"]["custom"] == {"original": True}
    assert item["coverage"]["complete"] is True


def test_lower_layer_full_supplements_context_without_consuming_core_limit():
    store, db, _, _ = fixture()
    for i in range(4):
        store.add_fact({"doc_id": "D1", "fact_id": f"CTX{i}", "context_id": "C1",
                        "evidence_id": "CTX-E"})
    store.add_evidence({"doc_id": "D1", "evidence_id": "CTX-E", "quote": "context"})
    payload = expand.build_output(object_ids=list(db), db_rows=db, store=store,
                                  kg_dirs=(), kg_zips=(), pg_env=Path("unused"),
                                  max_context_facts=2, evidence_mode="full")
    item = payload["results"][0]
    assert len(item["facts"]) == 25
    assert len(item["context_facts"]) == 2
    assert item["coverage"]["context_facts"] == {
        "available": 4, "selected": 2, "returned": 2, "dropped": 0, "limit": 2, "truncated": True,
    }
    assert item["coverage"]["direct_complete"] is True
    assert item["coverage"]["supplementary_complete"] is False
    assert len(item["evidence"]) == 7
    assert [row["evidence_id"] for row in item["context_evidence"]] == ["CTX-E"]
    disabled = run(store, db, evidence_mode="full", max_context_facts=0)["items"][0]
    assert len(disabled["facts"]) == 25
    assert disabled["context_facts"] == []
    assert disabled["context_evidence"] == []


def test_full_follows_source_linked_facts_and_their_evidence():
    store, db, _, _ = fixture()
    store.add_fact({"doc_id": "D1", "fact_id": "LINK", "source_hyperedge_id": "H1",
                    "evidence_ids": ["FACT-E", "MISSING-E"]})
    store.add_evidence({"doc_id": "D1", "evidence_id": "FACT-E", "quote": "fact only"})
    response = run(store, db, evidence_mode="full", max_context_facts=0)
    item = response["items"][0]
    assert len(item["facts"]) == 26
    assert "FACT-E" in [row["evidence_id"] for row in item["evidence"]]
    assert item["unresolved"]["evidence_ids"] == ["MISSING-E"]
    assert response["status"] == "partial"


def test_missing_references_and_db_rows_preserve_partial_success():
    store, db, _, _ = fixture()
    db["D1::H1"]["metadata"].update(fact_ids=["NO-FACT"], evidence_ids=["NO-EVIDENCE"])
    response = run(store, db, object_ids=["D1::H1", "D2::ABSENT"], evidence_mode="full")
    good, missing = response["items"]
    assert response["status"] == "partial"
    assert len(good["facts"]) == 25
    assert good["unresolved"]["fact_ids"] == ["NO-FACT"]
    assert good["unresolved"]["evidence_ids"] == ["NO-EVIDENCE"]
    assert good["coverage"]["complete"] is False
    assert missing["hyperedge_raw"] is None
    assert missing["unresolved"]["db_object_ids"] == ["D2::ABSENT"]
    assert missing["coverage"]["complete"] is False
    assert run(store, {}, object_ids=["absent"], evidence_mode="full")["status"] == "not_found"


def test_same_document_conflicts_report_all_variants_without_overwriting():
    store, db, _, quote = fixture()
    original = copy.deepcopy(store.facts_by_id[("D1", "F0")])
    store.add_fact(original)
    store.add_fact({**original, "value": "conflict"})
    store.add_evidence({"doc_id": "D1", "evidence_id": "E0", "quote": "conflict"})
    response = run(store, db, evidence_mode="full")
    item = response["items"][0]
    assert store.facts_by_id[("D1", "F0")] == original
    assert store.evidence_by_id[("D1", "E0")]["quote"] == quote
    conflicts = item["unresolved"]["conflicts"]
    assert {(c["entity_type"], c["id"]) for c in conflicts} == {("facts", "F0"), ("evidence", "E0")}
    assert all(len(c["variants"]) == 2 for c in conflicts)
    assert len([row for row in item["facts"] if row["fact_id"] == "F0"]) == 2
    assert response["status"] == "partial"


def test_same_ids_in_other_documents_do_not_collide_or_leak():
    store, db, _, _ = fixture()
    store.add_fact({"doc_id": "D2", "fact_id": "F0", "value": "other doc"})
    store.add_evidence({"doc_id": "D2", "evidence_id": "E0", "quote": "other doc"})
    item = run(store, db, evidence_mode="full")["items"][0]
    assert item["unresolved"]["conflicts"] == []
    assert all(row["raw"]["doc_id"] == "D1" for row in item["facts"] + item["evidence"])


def test_namespaced_sources_preserve_original_raw_and_scope_references():
    store = expand.KgStore()
    db = {}
    originals = {}
    for collection in ("A", "B"):
        source = expand.KgDirectorySource(Path(collection), collection, None, "retrieval_object_v2")
        raw = {"doc_id": "D1", "hyperedge_id": "H1", "fact_ids": ["F1"], "evidence_ids": ["E1"]}
        originals[collection] = copy.deepcopy(raw)
        for filename, row in (
            ("hyperedges.jsonl", raw),
            ("facts.jsonl", {"doc_id": "D1", "fact_id": "F1", "value": collection}),
            ("evidence_units.jsonl", {"doc_id": "D1", "evidence_id": "E1", "quote": collection}),
            ("patents.jsonl", {"doc_id": "D1", "title": collection}),
            ("patent_profiles.jsonl", {"doc_id": "D1", "profile_id": collection}),
        ):
            expand.add_row(store, filename, row, None, source)
        object_id = f"{collection}::D1::H1"
        db[object_id] = {"object_id": object_id, "doc_id": "D1", "metadata": {}}
    response = run(store, db, evidence_mode="full")
    assert response["status"] == "ok"
    for collection, item in zip(("A", "B"), response["items"]):
        assert item["hyperedge_raw"] == originals[collection]
        assert item["facts"][0]["value"] == collection
        assert item["evidence"][0]["quote"] == collection
        assert item["patent"]["title"] == collection
        assert item["patent_profile"]["profile_id"] == collection
        assert item["unresolved"]["conflicts"] == []
        assert item["facts"][0]["provenance"]["collection_id"] == collection


def test_compact_default_keeps_legacy_projection_limits_and_context_fallback():
    store, db, _, quote = fixture()
    default = run(store, db)
    explicit = run(store, db, evidence_mode="compact")
    assert default["items"] == explicit["items"]
    item = default["items"][0]
    assert "hyperedge_raw" not in item
    assert "coverage" not in item
    assert len(item["facts"]) == 20
    assert len(item["evidence"]) == 5
    assert len(item["evidence"][0]["quote"]) <= service.MAX_QUOTE_CHARS
    assert item["evidence"][0]["quote"] != quote
    assert default["summary"]["fact_count"] == 25
    assert len(run(store, db, max_context_facts=0)["items"][0]["facts"]) == 20
    fallback = expand.KgStore()
    fallback.add_hyperedge({"doc_id": "D1", "hyperedge_id": "H1", "context_id": "C1"})
    for i in range(4):
        fallback.add_fact({"doc_id": "D1", "fact_id": str(i), "context_id": "C1"})
    assert len(run(fallback, db, max_context_facts=2)["items"][0]["facts"]) == 2


def test_invalid_mode_rejected_before_accessing_store_or_db():
    with patch.object(service, "get_store", side_effect=AssertionError("must not access store")):
        response = service.run_expand({"object_ids": ["D1::H1"], "evidence_mode": "unknown"})
    assert response["status"] == "error"
    assert "evidence_mode" in response["error"]


def test_missing_raw_hyperedge_still_expands_db_metadata_references():
    store, db, _, _ = fixture()
    db["D1::H1"]["metadata"].update(hyperedge_id="MISSING", fact_ids=["F0"], evidence_ids=["E0"])
    db["D1::MISSING"] = db.pop("D1::H1")
    item = run(store, db, evidence_mode="full")["items"][0]
    assert item["hyperedge_raw"] is None
    assert item["unresolved"]["hyperedge_ids"] == ["MISSING"]
    assert len(item["facts"]) == 1
    assert len(item["evidence"]) == 1
    assert item["coverage"]["complete"] is False


def test_source_linked_fact_conflict_is_not_hidden_by_context_limit():
    store, db, _, _ = fixture()
    store.add_fact({"doc_id": "D1", "fact_id": "LINK", "source_hyperedge_id": "H1", "value": 1})
    store.add_fact({"doc_id": "D1", "fact_id": "LINK", "context_id": "C1", "value": 2})
    item = run(store, db, evidence_mode="full", max_context_facts=0)["items"][0]
    assert [row["value"] for row in item["facts"] if row["fact_id"] == "LINK"] == [1, 2]
    assert item["unresolved"]["conflicts"][0]["id"] == "LINK"


def test_missing_namespaced_source_does_not_borrow_another_source():
    store = expand.KgStore()
    source = expand.KgDirectorySource(Path("A"), "A", None, "retrieval_object_v2")
    for filename, row in (
        ("hyperedges.jsonl", {"doc_id": "D1", "hyperedge_id": "H1", "fact_ids": ["F1"]}),
        ("facts.jsonl", {"doc_id": "D1", "fact_id": "F1", "value": "wrong source"}),
    ):
        expand.add_row(store, filename, row, None, source)
    db = {"B::D1::H1": {"object_id": "B::D1::H1", "doc_id": "D1", "metadata": {"fact_ids": ["F1"]}}}
    item = run(store, db, evidence_mode="full")["items"][0]
    assert item["hyperedge_raw"] is None
    assert item["facts"] == []
    assert item["unresolved"]["fact_ids"] == ["F1"]


def test_local_zip_loader_preserves_raw_and_record_provenance(tmp_path):
    archive = tmp_path / "frozen.zip"
    raw = {"doc_id": "D1", "hyperedge_id": "H1", "fact_ids": ["F1"], "evidence_ids": ["E1"]}
    fact = {"doc_id": "D1", "fact_id": "F1", "value_text": "  exact  "}
    evidence = {"doc_id": "D1", "evidence_id": "E1", "quote": "line\n" * 300}
    with zipfile.ZipFile(archive, "w") as package:
        for name, row in (("hyperedges.jsonl", raw), ("facts.jsonl", fact), ("evidence_units.jsonl", evidence)):
            package.writestr("D1/" + name, json.dumps(row) + "\n")
    before = archive.read_bytes()
    store = expand.load_kg_store((), (archive,))
    db = {"D1::H1": {"object_id": "D1::H1", "doc_id": "D1", "metadata": {}}}
    item = run(store, db, evidence_mode="full")["items"][0]
    assert item["hyperedge_raw"] == raw
    assert item["facts"][0]["raw"] == fact
    assert item["evidence"][0]["raw"] == evidence
    assert item["facts"][0]["provenance"]["archive"] == str(archive)
    assert item["facts"][0]["provenance"]["file"] == "D1/facts.jsonl"
    assert item["facts"][0]["provenance"]["record_number"] == 1
    assert item["unresolved"]["patent_doc_ids"] == ["D1"]
    assert archive.read_bytes() == before


def test_full_summary_keeps_duplicate_and_nested_material_rows():
    store = expand.KgStore()
    material = {"material": {"label": "resin"}, "amount": {"value": [1, 2], "unit": "g"}}
    raw = {"doc_id": "D1", "hyperedge_id": "H1", "resins": [material, copy.deepcopy(material)]}
    store.add_hyperedge(raw)
    db = {"D1::H1": {"object_id": "D1::H1", "doc_id": "D1", "metadata": {}}}
    item = run(store, db, evidence_mode="full")["items"][0]
    assert item["hyperedge_summary"]["resins"] == [material, material]
    assert item["hyperedge_raw"] == raw


def test_full_normalizes_string_db_metadata_and_keeps_zero_values():
    store, db, _, _ = fixture()
    db["D1::H1"]["metadata"] = json.dumps({"hyperedge_id": "H1", "fact_ids": ["F0"]})
    response = json.loads(json.dumps(run(store, db, evidence_mode="full")))
    item = response["items"][0]
    assert item["facts"][0]["value"] == 0
    assert item["hyperedge_summary"]["result"]["value"] == 0


def test_context_conflict_reports_variant_without_context_binding():
    store, db, _, _ = fixture()
    store.add_fact({"doc_id": "D1", "fact_id": "CTX", "context_id": "C1", "value": 1})
    store.add_fact({"doc_id": "D1", "fact_id": "CTX", "value": 2})
    item = lower_full(store, db, max_context_facts=1)
    assert item["context_facts"] == []
    assert item["unresolved"]["conflicts"] == []
    assert item["supplementary_unresolved"]["conflicts"][0]["id"] == "CTX"
    assert len(item["supplementary_unresolved"]["conflicts"][0]["variants"]) == 2
    assert item["coverage"]["complete"] is True


def lower_full(store, db, max_context_facts=20):
    return expand.build_output(
        object_ids=list(db), db_rows=db, store=store, kg_dirs=(), kg_zips=(),
        pg_env=Path("unused"), max_context_facts=max_context_facts, evidence_mode="full",
    )["results"][0]


def test_optional_missing_evidence_drops_only_unsafe_context_and_orphan_evidence():
    import answering

    store, db, _, _ = fixture()
    store.add_fact({"doc_id": "D1", "fact_id": "BAD", "context_id": "C1",
                    "evidence_ids": ["MISSING", "ORPHAN"]})
    store.add_fact({"doc_id": "D1", "fact_id": "GOOD", "context_id": "C1", "evidence_id": "GOOD-E"})
    for evidence_id in ("ORPHAN", "GOOD-E"):
        store.add_evidence({"doc_id": "D1", "evidence_id": evidence_id, "quote": "support"})
    item = lower_full(store, db)
    assert item["coverage"]["complete"] is True
    assert item["coverage"]["supplementary_complete"] is False
    assert not any(item["unresolved"].values())
    assert item["supplementary_unresolved"]["context_evidence_ids"] == ["MISSING"]
    assert [row["fact_id"] for row in item["context_facts"]] == ["GOOD"]
    assert [row["evidence_id"] for row in item["context_evidence"]] == ["GOOD-E"]
    assert item["coverage"]["context_facts"]["selected"] == 2
    assert item["coverage"]["context_facts"]["dropped"] == 1
    assert item["coverage"]["context_evidence"]["dropped"] == 1
    assert len(item["facts"]) == 25 and len(item["evidence"]) == 7
    response = run(store, db, evidence_mode="full")
    projected = response["items"][0]
    assert projected["supplementary_unresolved"] == item["supplementary_unresolved"]
    assert response["status"] == "ok"
    assert answering._expanded_item_is_verified(projected)
    assert "supplementary_unresolved" not in run(store, db)["items"][0]


def test_optional_profile_missing_or_conflicting_does_not_block_answer_gate():
    import answering

    for conflicting in (False, True):
        store, db, _, _ = fixture()
        if conflicting:
            store.add_patent_profile({"doc_id": "D1", "binder_family": "untrusted conflict"})
        else:
            store.full_records.pop(("patent_profile", "D1", "D1"))
        item = lower_full(store, db)
        assert not any(item["unresolved"].values())
        assert any(item["supplementary_unresolved"].values())
        assert item["patent_profile"]["raw"] is None
        assert item["coverage"]["complete"] is True
        assert item["coverage"]["supplementary_complete"] is False
        response = run(store, db, evidence_mode="full")
        projected = response["items"][0]
        assert response["status"] == "ok"
        assert projected["patent_profile"] == {}
        assert projected["hyperedge_summary"]["resin_system"] is None
        assert answering._expanded_item_is_verified(projected)


def test_supplementary_evidence_conflict_or_missing_binding_is_not_verified():
    store, db, _, _ = fixture()
    store.add_fact({"doc_id": "D1", "fact_id": "BAD", "context_id": "C1", "evidence_id": "CONFLICT"})
    store.add_fact({"doc_id": "D1", "fact_id": "UNBOUND", "context_id": "C1", "value": "no support"})
    for quote in ("first", "second"):
        store.add_evidence({"doc_id": "D1", "evidence_id": "CONFLICT", "quote": quote})
    item = lower_full(store, db)
    assert item["context_facts"] == [] and item["context_evidence"] == []
    assert item["coverage"]["complete"] is True
    assert item["coverage"]["supplementary_complete"] is False
    assert item["coverage"]["context_facts"]["dropped"] == 2
    assert item["supplementary_unresolved"]["conflicts"][0]["id"] == "CONFLICT"
    assert set(item["supplementary_unresolved"]["context_fact_ids"]) == {"BAD", "UNBOUND"}


def test_missing_direct_evidence_remains_blocking_and_drops_dependent_context():
    import answering

    store, db, _, _ = fixture()
    db["D1::H1"]["metadata"]["evidence_ids"] = ["MISSING"]
    store.add_fact({"doc_id": "D1", "fact_id": "CTX", "context_id": "C1", "evidence_id": "MISSING"})
    item = lower_full(store, db)
    assert item["unresolved"]["evidence_ids"] == ["MISSING"]
    assert item["coverage"]["complete"] is False
    assert item["coverage"]["direct_complete"] is False
    assert item["context_facts"] == []
    projected = run(store, db, evidence_mode="full")["items"][0]
    assert not answering._expanded_item_is_verified(projected)
