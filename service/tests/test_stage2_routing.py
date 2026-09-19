from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

import kg_contract
import routing
import tool_clients
import tool_runtime


def search_call(**filters):
    return {"tool": "kg.hybrid_search", "query": "coating primer", "offset": 50,
            "filters": {"assignees": ["Acme"], "doc_ids": ["DOC1"], **filters}}


def install_runtime(monkeypatch, calls):
    events = []
    monkeypatch.setattr(tool_runtime, "_app_value", lambda name, default: default)
    monkeypatch.setattr(tool_runtime, "route_tools", lambda question: {"calls": copy.deepcopy(calls)})
    monkeypatch.setattr(routing, "_record_route_decision", lambda *args: None)
    monkeypatch.setattr(tool_runtime, "record_tool_observation", lambda question, result, **kwargs: {
        "id": str(len(events)), "result": copy.deepcopy(result), **kwargs,
    })
    monkeypatch.setattr(tool_runtime, "kg_hybrid_search", lambda query, **kwargs: (
        events.append(("search", query, kwargs)) or {"status": "empty", "items": []}
    ))
    monkeypatch.setattr(tool_runtime, "kg_expand_hyperedge_multihop", lambda *args, **kwargs: pytest.fail("unexpected expand"))
    return events


def test_lookup_schema_and_model_arguments_use_shared_contract():
    tools = {row["function"]["name"]: row["function"] for row in routing._kg_tools_openai_format()}
    assert tools["kg_lookup_vocabulary"]["parameters"] == kg_contract.openai_tool_parameters("kg.lookup_vocabulary")
    assert "query" in tools["kg_lookup_vocabulary"]["parameters"]["required"]
    filters = tools["kg_hybrid_search"]["parameters"]["properties"]["filters"]["properties"]
    for name, kind in kg_contract.load_tool_contract()["field_types"].items():
        if kind == "classification_list":
            assert filters[name]["description"] and filters[name]["items"]["enum"]
    parsed = routing._tool_call_to_internal_call({"function": {
        "name": "kg_lookup_vocabulary", "arguments": json.dumps({"query": "primer", "dimension": "coating_layers", "limit": 101}),
    }}, "original question")
    call = routing.sanitize_tool_routing({"calls": [parsed]}, "original question", router="test")["calls"][0]
    assert (call["query"], call["dimension"], call["limit"]) == ("primer", "coating_layers", 100)


def yearly_gma_calls():
    return [
        {"tool": "kg.sql_aggregate", "intent": "group_count", "target": "doc_id",
         "group_by": ["publication_year"], "filters": {"materials": ["GMA"]}, "limit": 200},
        {"tool": "kg.sql_aggregate", "intent": "list", "target": "doc_id",
         "filters": {"materials": ["GMA"], "publication_year": ["unknown"]}, "limit": 200},
    ]


@pytest.mark.parametrize("aggregate_status", ["ok", "unsupported"])
def test_gma_lookup_dimension_retry_resumes_yearly_statistics_once(monkeypatch, aggregate_status):
    question = "\u6309GMA\u7edf\u8ba1\u4e13\u5229\u9010\u5e74\u6570\u91cf\uff0c\u5e76\u5217\u51fa\u672a\u77e5\u5e74\u4efd\u7684\u6240\u6709\u4e13\u5229\u53f7\u3002"
    initial = {"tool": "kg.lookup_vocabulary", "query": "GMA", "dimension": "material", "limit": 10}
    events = install_runtime(monkeypatch, [initial])
    phases, aggregates = [], []

    def lookup(query, **kwargs):
        events.append(("lookup", query, kwargs))
        if kwargs["dimension"] == "material":
            return {"status": "unsupported", "items": [], "unsupported_constraints": ["dimension:material"]}
        return {"status": "ok", "items": [{"dimension": "materials", "value": "GMA"}]}

    def model(original_question, **kwargs):
        assert original_question == question
        feedback = kwargs["tool_feedback"]
        assert feedback["original_question"] == question
        phases.append(feedback["phase"])
        if feedback["phase"] == "parameter_correction":
            assert feedback["observations"][0]["result"]["unsupported_constraints"] == ["dimension:material"]
            return {"calls": [{**initial, "dimension": "materials", "query": "do not accept this rewrite", "limit": 1}]}
        assert feedback["observations"][0]["result"]["items"][0]["value"] == "GMA"
        return {"calls": yearly_gma_calls()}

    monkeypatch.setattr(tool_runtime, "kg_lookup_vocabulary", lookup)
    monkeypatch.setattr(routing, "route_tools_with_qwen", model)
    monkeypatch.setattr(tool_runtime, "kg_sql_aggregate", lambda **kwargs: aggregates.append(kwargs) or {
        "status": aggregate_status, "items": [], "unsupported_constraints": ["classification"] if aggregate_status == "unsupported" else [],
    })
    rows = tool_runtime.maybe_run_tools(question)
    assert phases == ["parameter_correction", "vocabulary_replan"]
    assert [event[0] for event in events] == ["lookup", "lookup"]
    assert events[1][1] == "GMA" and events[1][2] == {"dimension": "materials", "limit": 10}
    assert len(aggregates) == 2
    assert aggregates[0]["intent"] == "group_count" and aggregates[0]["group_by"] == ["publication_year"]
    assert aggregates[1]["intent"] == "list" and aggregates[1]["target"] == "doc_id"
    assert aggregates[1]["filters"]["publication_year"] == ["unknown"]
    assert all(row["filters"]["materials"] for row in aggregates)
    assert [row["tool"] for row in rows] == ["kg.lookup_vocabulary"] * 2 + ["kg.sql_aggregate"] * 2
    assert rows[1]["result"]["parameter_correction"]["unsupported_receipt"]["status"] == "unsupported"


@pytest.mark.parametrize("failure", ["unsupported", "transport", "model_error", "invalid_correction", "recursive_lookup"])
def test_unresolved_lookup_never_becomes_unfiltered_search(monkeypatch, failure):
    initial = {"tool": "kg.lookup_vocabulary", "query": "GMA", "dimension": "material"}
    events = install_runtime(monkeypatch, [initial])
    phases, decisions = [], []

    def lookup(*args, **kwargs):
        events.append(("lookup",))
        if failure == "transport":
            raise OSError("offline lookup")
        return {"status": "unsupported", "items": [], "unsupported_constraints": ["dimension:material"]}

    def model(question, **kwargs):
        phase = kwargs["tool_feedback"]["phase"]
        phases.append(phase)
        if failure == "model_error":
            raise OSError("offline model")
        if phase == "parameter_correction":
            return {"calls": [search_call()]} if failure == "invalid_correction" else {
                "calls": [{**initial, "dimension": "materials"}],
            }
        return {"calls": [initial] if failure == "recursive_lookup" else [search_call()]}

    monkeypatch.setattr(tool_runtime, "kg_lookup_vocabulary", lookup)
    monkeypatch.setattr(routing, "route_tools_with_qwen", model)
    monkeypatch.setattr(routing, "_record_route_decision", lambda question, decision: decisions.append(decision))
    rows = tool_runtime.maybe_run_tools("Yearly GMA patent counts and all unknown-year IDs")
    assert phases.count("parameter_correction") <= 1
    assert len(events) <= 2 and all(event[0] == "lookup" for event in events)
    assert all(row["tool"] == "kg.lookup_vocabulary" for row in rows)
    assert rows[-1]["result"]["lookup_resolution_incomplete"] is True
    assert decisions[-1]["calls"] == [] and decisions[-1]["lookup_resolution_incomplete"] is True
    assert decisions[-1]["pending_question"] == "Yearly GMA patent counts and all unknown-year IDs"


def test_lookup_replan_preserves_existing_multicall_statistics_contract(monkeypatch):
    original = yearly_gma_calls()
    monkeypatch.setattr(routing, "_record_route_decision", lambda *args: None)
    monkeypatch.setattr(routing, "route_tools_with_qwen", lambda *args, **kwargs: {"calls": [search_call()]})
    result = routing.replan_tools_after_feedback("yearly counts and unknown-year IDs", {
        "calls": [{"tool": "kg.lookup_vocabulary", "query": "GMA"}, *original],
    }, [{"result": {"status": "unsupported"}}])
    assert len(result["calls"]) == 2
    for before, after in zip(original, result["calls"]):
        for key in ("tool", "intent", "target", "group_by", "limit"):
            assert after.get(key) == before.get(key)
        assert after["filters"] == kg_contract.normalize_filter_request(before["tool"], before["filters"])["effective_filters"]


@pytest.mark.parametrize("limit,expected", [(None, 20), (0, 1), (101, 100)])
def test_lookup_transport(monkeypatch, limit, expected):
    requests = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"status":"ok","candidates":[]}'
    monkeypatch.setenv("KG_LOOKUP_VOCABULARY_URL", "http://offline/tools/kg.lookup_vocabulary")
    monkeypatch.setattr(tool_clients.urllib.request, "urlopen", lambda req, **kwargs: requests.append(req) or Response())
    result = tool_clients.kg_lookup_vocabulary("material", dimension="materials", limit=limit)
    assert requests[0].full_url == "http://offline/tools/kg.lookup_vocabulary"
    assert json.loads(requests[0].data) == {"query": "material", "dimension": "materials", "limit": expected}
    assert result["tool"] == "kg.lookup_vocabulary"


def test_lookup_replans_before_search_and_keeps_explicit_scope(monkeypatch):
    initial = search_call(coating_layers=["primer"])
    events = install_runtime(monkeypatch, [initial, {"tool": "kg.lookup_vocabulary", "query": "primer"}])
    monkeypatch.setattr(tool_runtime, "kg_lookup_vocabulary", lambda *args, **kwargs: events.append(("lookup",)) or {
        "status": "ok", "candidates": [{"dimension": "coating_layers", "value": "primer"}],
    })
    def model(question, **kwargs):
        events.append(("replan", question, kwargs))
        return {"router": "llm-function-calling", "calls": [search_call(coating_functions=["anticorrosion"])]}
    monkeypatch.setattr(routing, "route_tools_with_qwen", model)
    observations = tool_runtime.maybe_run_tools("Acme DOC1 primer coating candidate ids only")
    assert [event[0] for event in events] == ["lookup", "replan", "search"]
    assert observations[0]["tool"] == "kg.lookup_vocabulary"
    assert events[1][1] == "Acme DOC1 primer coating candidate ids only"
    assert "candidates" in json.dumps(events[1][2]["tool_feedback"])
    kwargs = events[-1][2]
    assert kwargs["filters"]["coating_layers"] == ["primer"]
    assert kwargs["filters"]["assignees"] == ["Acme"]
    assert kwargs["filters"]["doc_ids"] == ["DOC1"]
    assert kwargs["offset"] == 50


def test_query_rewrite_does_not_replace_or_truncate_explicit_conditions():
    question = "Acme epoxy zinc-rich primer for aluminum with 73 hours and unique_marker " + "x" * 600
    rewritten = routing.build_kg_search_query(question)
    assert rewritten.startswith(question)
    assert question in routing.build_kg_search_queries(question, max_variants=1)[0]
    filters = routing.kg_search_filters_for_question(question, {
        "application_family": ["marine"], "material_roles": ["binder"], "properties": ["corrosion_protection"],
    })
    assert filters["application_family"] == ["marine"]
    assert filters["material_roles"] == ["resin"]
    assert filters["properties"] == ["corrosion_protection"]
    aggregate = routing.merge_aggregate_domain_filters(question, {
        "application_family": ["marine"], "material_roles": ["pigment"],
    })
    assert aggregate["application_family"] == ["marine"]
    assert aggregate["material_roles"] == ["pigment"]


def test_sanitizer_keeps_shared_receipt_fields():
    call = routing.sanitize_tool_routing({"calls": [search_call(coating_functions=["primer"])]}, "primer coating", router="test")["calls"][0]
    receipt = kg_contract.normalize_filter_request("kg.hybrid_search", {"coating_functions": ["primer"]})
    assert call["constraint_candidates"] == receipt["constraint_candidates"]
    assert call["classification_registry_version"] == receipt["classification_registry_version"]


def test_correction_preserves_valid_conditions_and_is_attempted_once(monkeypatch):
    initial = search_call(coating_functions=["anticorrosion", "primer"])
    initial.update(kg_contract.normalize_filter_request(initial["tool"], initial["filters"]))
    initial["filters"] = initial["effective_filters"]
    events = install_runtime(monkeypatch, [initial])
    receipt = kg_contract.normalize_filter_request(initial["tool"], initial["requested_filters"])
    def search(query, **kwargs):
        events.append(("search", query, kwargs))
        return {"status": "unsupported", "items": [], **receipt}
    monkeypatch.setattr(tool_runtime, "kg_hybrid_search", search)
    def model(question, **kwargs):
        events.append(("replan", question, kwargs))
        return {"router": "llm-function-calling", "calls": [{
            "tool": "kg.hybrid_search", "query": "changed query", "offset": 0,
            "filters": {"coating_layers": ["primer"]},
        }]}
    monkeypatch.setattr(routing, "route_tools_with_qwen", model)
    observations = tool_runtime.maybe_run_tools("Acme primer anticorrosion coating")
    assert [e[0] for e in events] == ["search", "replan", "search"]
    assert len(observations) == 2
    filters = events[-1][2]["requested_filters"]
    assert filters["coating_functions"] == ["anticorrosion"]
    assert filters["coating_layers"] == ["primer"]
    assert filters["assignees"] == ["Acme"] and filters["doc_ids"] == ["DOC1"]
    assert events[0][1] == events[-1][1]
    assert events[-1][2]["offset"] == 50


def test_lookup_api_configuration_and_dispatch(monkeypatch):
    from api.config import CoatingApiSettings
    from api.orchestrator import CoatingConversationEngine
    settings = CoatingApiSettings.from_mapping({"KG_LOOKUP_VOCABULARY_URL": "http://offline/lookup"})
    assert settings.kg_lookup_vocabulary_url == "http://offline/lookup"
    with monkeypatch.context() as env:
        env.setattr("os.environ", {})
        settings.apply_to_environment()
        import os
        assert os.environ["KG_LOOKUP_VOCABULARY_URL"] == "http://offline/lookup"
    engine = object.__new__(CoatingConversationEngine)
    engine.runtime = SimpleNamespace(tool_clients=SimpleNamespace(kg_lookup_vocabulary=lambda query, **kwargs: {"query": query, **kwargs}))
    assert engine.call_tool("kg.lookup_vocabulary", {"query": "primer"}) == {"query": "primer", "dimension": None, "limit": 20}


def test_lookup_proxy_is_authenticated():
    from fastapi.testclient import TestClient
    from api.app import create_app
    from api.config import CoatingApiSettings
    engine = SimpleNamespace(call_tool=lambda name, payload: {"tool": name, **payload})
    app = create_app(settings=CoatingApiSettings(api_token="test-token"), engine=engine)
    with TestClient(app) as client:
        url = "/api/v1/coating/tools/lookup_vocabulary"
        assert client.post(url, json={"payload": {"query": "primer"}}).status_code == 401
        result = client.post(url, json={"payload": {"query": "primer"}}, headers={"Authorization": "Bearer test-token"})
        assert result.status_code == 200
        assert result.json() == {"tool": "kg.lookup_vocabulary", "query": "primer"}


@pytest.mark.parametrize("failure", ["empty", "transport", "recursive_lookup", "model_error"])
def test_lookup_failure_and_recursive_plan_are_bounded(monkeypatch, failure):
    original = search_call(coating_layers=["primer"])
    events = install_runtime(monkeypatch, [{"tool": "kg.lookup_vocabulary", "query": "primer"}, original])
    def lookup(*args, **kwargs):
        events.append(("lookup",))
        if failure == "transport":
            raise OSError("offline")
        return {"status": "empty", "items": []}
    def replan(*args, **kwargs):
        events.append(("replan",))
        if failure == "model_error":
            raise OSError("offline model")
        return {"calls": [{"tool": "kg.lookup_vocabulary", "query": "again"}]} if failure == "recursive_lookup" else {"calls": []}
    monkeypatch.setattr(tool_runtime, "kg_lookup_vocabulary", lookup)
    monkeypatch.setattr(routing, "route_tools_with_qwen", replan)
    rows = tool_runtime.maybe_run_tools("Acme coating primer candidate ids only")
    assert [e[0] for e in events] == ["lookup", "replan", "search"]
    assert events[-1][2]["filters"]["assignees"] == ["Acme"]
    assert rows[0]["result"]["usage_boundary"] == "vocabulary_candidates_not_patent_evidence"


@pytest.mark.parametrize("filters", [{}, {"coating_layers": ["topcoat"]}, {"coating_layers": ["primer"], "assignees": ["Other"]}])
def test_correction_cannot_drop_or_broaden_explicit_conditions(monkeypatch, filters):
    original = search_call(coating_functions=["primer"])
    receipt = kg_contract.normalize_filter_request(original["tool"], original["filters"])
    original.update(receipt)
    original["filters"] = receipt["effective_filters"]
    untouched = copy.deepcopy(original)
    corrected = routing._corrected_filter_call(original, {"tool": original["tool"], "filters": filters}, receipt)
    if filters.get("coating_layers") == ["primer"]:
        assert corrected["requested_filters"]["assignees"] == ["Acme"]
        assert corrected["requested_filters"]["doc_ids"] == ["DOC1"]
        assert corrected["parameter_correction"]["changes"][0]["candidate"] in receipt["constraint_candidates"]
    else:
        assert corrected is None
    assert original == untouched


def test_missing_candidates_do_not_authorize_correction():
    original = search_call(coating_functions=["unmapped material"])
    receipt = kg_contract.normalize_filter_request(original["tool"], original["filters"])
    assert routing._corrected_filter_call(original, search_call(coating_functions=["anticorrosion"]), receipt) is None


@pytest.mark.parametrize("path", ["aggregate", "doc_fallback", "doc_empty_fallback"])
def test_correction_is_bounded_in_alternate_execution_paths(monkeypatch, path):
    original = search_call(coating_functions=["primer"])
    if path == "aggregate":
        original.update(tool="kg.sql_aggregate", intent="list", target="doc_id")
    else:
        original.update(tool="kg.doc_field_scan", doc_ids=[] if path == "doc_fallback" else ["DOC1"])
    events = install_runtime(monkeypatch, [original, search_call(coating_functions=["primer"])])
    def unsupported(query=None, **kwargs):
        events.append(("unsupported", kwargs))
        receipt = kg_contract.normalize_filter_request("kg.hybrid_search", {"coating_functions": ["primer"]})
        return {"status": "unsupported", "items": [], **receipt}
    monkeypatch.setattr(tool_runtime, "kg_hybrid_search", unsupported)
    monkeypatch.setattr(tool_runtime, "kg_sql_aggregate", unsupported)
    monkeypatch.setattr(tool_runtime, "kg_doc_field_scan", lambda **kwargs: {
        "status": "empty", "items": [], "summary": {"hyperedges_scanned": 0},
    })
    def replan(question, **kwargs):
        events.append(("replan", kwargs))
        prior = kwargs["tool_feedback"]["original_route"]["calls"][0]
        return {"calls": [{**prior, "requested_filters": {"coating_layers": ["primer"]}, "filters": {"coating_layers": ["primer"]}}]}
    monkeypatch.setattr(routing, "route_tools_with_qwen", replan)
    rows = tool_runtime.maybe_run_tools("Acme primer coating candidate ids only")
    assert sum(e[0] == "replan" for e in events) == 1
    assert sum(e[0] == "unsupported" for e in events) == 3
    assert not any(row["tool"] == "kg.expand_hyperedge_multihop" for row in rows)


def test_replan_hard_scope_updates_requested_receipt(monkeypatch):
    monkeypatch.setattr(routing, "_record_route_decision", lambda *args: None)
    monkeypatch.setattr(routing, "route_tools_with_qwen", lambda *args, **kwargs: {"calls": [search_call()]})
    route = routing.replan_tools_after_feedback("coating primer", {
        "calls": [{"tool": "kg.lookup_vocabulary", "query": "primer"}],
        "scope_resolution": {"scope_policy": "hard", "scope_action": "use_active", "doc_ids": ["DOC2"]},
    }, [{"result": {"status": "ok", "items": []}}])
    assert route["calls"][0]["filters"]["doc_ids"] == ["DOC2"]
    assert route["calls"][0]["requested_filters"]["doc_ids"] == ["DOC2"]


def test_scope_guard_does_not_turn_unsupported_into_empty():
    result = tool_runtime.filter_search_result_by_doc_scope({
        "status": "unsupported", "unsupported_constraints": ["coating_layers:unknown"],
        "items": [{"object_id": "C::WO2020000002A1::HE1", "doc_id": "WO2020000002A1"}],
    }, {"doc_ids": ["WO2020000001A1"]})
    assert result["status"] == "unsupported"
    assert result["items"] == []


def test_feedback_reaches_model_with_full_original_question(monkeypatch):
    requests = []
    question = "coating primer " + "explicit_condition " * 80
    feedback = {"phase": "parameter_correction", "constraint_candidates": [
        {"dimension": "coating_layers", "value": "primer", "definition": "layer, not function"},
    ], "unsupported_constraints": ["coating_functions:primer"]}
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"choices":[{"message":{"tool_calls":[]}}]}'
    monkeypatch.setattr(routing, "provider_config", lambda: {"api_key": "offline", "model": "offline", "max_output_tokens": 1000})
    monkeypatch.setattr(routing, "provider_messages_url", lambda cfg: "http://offline/chat/completions")
    monkeypatch.setattr(routing, "provider_headers", lambda cfg: {})
    monkeypatch.setattr(routing, "build_provider_body", lambda cfg, messages, **kwargs: {"messages": messages})
    monkeypatch.setattr(routing.urllib.request, "urlopen", lambda request, **kwargs: requests.append(request) or Response())
    routing.route_tools_with_qwen(question, tool_feedback=feedback)
    body = json.loads(requests[0].data)
    assert body["messages"][1]["content"].endswith(question)
    assert json.dumps(feedback, ensure_ascii=False) in body["messages"][1]["content"]
    assert "untrusted data" in body["messages"][0]["content"]


def test_lookup_continuation_explains_exact_related_and_incomplete_id_expansion():
    prompt = routing._router_system_prompt()
    for field in ("exact_items", "related_items", "match_type", "match_counts", "match_truncated",
                  "suggested_filters", "canonical_id_expansion_complete"):
        assert field in prompt
    assert "not an exhaustive canonical-ID expansion" in prompt
    assert "Even an untruncated exact list does not prove canonical-ID completeness" in prompt
    assert "never replace an original material condition with the returned candidate IDs" in prompt
    assert "or add those IDs as a narrowing filter" in prompt
    assert "User-specified canonical IDs remain explicit constraints" in prompt


@pytest.mark.parametrize("truncated", [False, True])
def test_lookup_feedback_preserves_payload_and_original_conditions(monkeypatch, truncated):
    lookup = {"tool": "kg.lookup_vocabulary", "query": "ABC", "dimension": "materials"}
    exact = {"dimension": "materials", "value": "ABC", "canonical_id": "MAT_ABC", "match_type": "exact"}
    related = {"dimension": "materials", "value": "50% ABC resin", "canonical_id": "MAT_MIX", "match_type": "related"}
    payload = {
        "status": "ok", "items": [exact, related], "exact_items": [exact], "related_items": [related],
        "total": 20 if truncated else 2, "truncated": truncated,
        "match_counts": {"exact": 1, "related": 19 if truncated else 1, "browse": 0},
        "match_truncated": {"exact": False, "related": truncated, "browse": False},
        "suggested_filters": {"materials": ["ABC", "Fixture monomer"]},
        "canonical_id_expansion_complete": False,
        "selection_semantics": "candidate_ids_are_not_exhaustive_query_expansion",
    }
    original = {"tool": "kg.sql_aggregate", "query": "ABC yearly counts", "intent": "group_count",
                "target": "doc_id", "group_by": ["publication_year"],
                "filters": {"materials": ["ABC"], "assignees": ["Fixture company"]}}
    before = copy.deepcopy(payload)
    observed = []
    def model(question, **kwargs):
        observed.append(kwargs["tool_feedback"])
        return {"calls": [{**original, "filters": payload["suggested_filters"]}]}
    monkeypatch.setattr(routing, "route_tools_with_qwen", model)
    monkeypatch.setattr(routing, "_record_route_decision", lambda *args: None)
    result = routing.replan_tools_after_feedback("ABC yearly counts", {"calls": [lookup, original]}, [
        {"call": lookup, "result": payload},
    ])
    assert observed[0]["observations"][0]["result"] == before
    call = result["calls"][0]
    assert call["requested_filters"]["materials"] == ["ABC"]
    assert call["requested_filters"]["assignees"] == ["Fixture company"]
    assert call["group_by"] == ["publication_year"]
    assert not call["filters"].get("material_canonical_ids")
    assert payload == before


def test_lookup_replan_keeps_full_evidence_budget(monkeypatch):
    events = install_runtime(monkeypatch, [{"tool": "kg.lookup_vocabulary", "query": "primer"}])
    monkeypatch.setattr(tool_runtime, "kg_lookup_vocabulary", lambda *args, **kwargs: {"status": "ok", "items": []})
    monkeypatch.setattr(routing, "route_tools_with_qwen", lambda *args, **kwargs: {"calls": [search_call()]})
    def search(query, **kwargs):
        events.append(("search", kwargs))
        return {"status": "ok", "items": [{"object_id": f"C::DOC1::HE{i}", "doc_id": "DOC1"} for i in range(50)]}
    def expand(ids, **kwargs):
        events.append(("expand", ids, kwargs))
        return {"status": "ok", "items": []}
    monkeypatch.setattr(tool_runtime, "kg_hybrid_search", search)
    monkeypatch.setattr(tool_runtime, "kg_expand_hyperedge_multihop", expand)
    tool_runtime.maybe_run_tools("Acme primer coating evidence")
    assert events[0][1]["top_k"] == 50 and events[0][1]["candidate_k"] == 100
    assert len(events[1][1]) == 30 and events[1][2]["evidence_mode"] == "full"


@pytest.mark.parametrize("coverage_fields,complete", [
    ({}, True),
    ({"classification_complete": True, "cohort_available": True}, True),
    ({"classification_complete": False}, False),
    ({"cohort_available": False}, False),
    ({"classification_complete": True, "classification_coverage": {"complete_for_requested_axes": False}}, False),
    ({"classification_coverage": {"complete_for_requested_axes": False, "requested_axes": ["coating_layers"],
                                  "unmapped_profile_count": 2, "unresolved_examples": [{"doc_id": "DOC1"}]}}, False),
])
def test_empty_aggregate_true_zero_requires_available_complete_coverage(coverage_fields, complete):
    original = {"status": "empty", "items": [], **copy.deepcopy(coverage_fields)}
    result = tool_runtime.retry_empty_aggregate(
        lambda **kwargs: pytest.fail("must not retry empty cohort"), original,
        intent="list", target="doc_id", filters={"coating_layers": ["primer"]},
        group_by=[], limit=50, include_examples=False,
    )
    assert (tool_runtime.AGGREGATE_EMPTY_TRUE_ZERO_WARNING in result.get("warnings", [])) == complete
    assert {key: result[key] for key in coverage_fields} == coverage_fields
    if not complete:
        assert any("not_evidence_of_absence" in warning for warning in result["warnings"])


def test_incomplete_aggregate_removes_stale_true_zero_warning():
    result = tool_runtime.retry_empty_aggregate(
        None, {"status": "empty", "classification_complete": False,
               "warnings": [tool_runtime.AGGREGATE_EMPTY_TRUE_ZERO_WARNING, "backend warning"]},
        intent="list", target="doc_id", filters={}, group_by=[], limit=50, include_examples=False,
    )
    assert result["warnings"] == ["backend warning", "classification_coverage_incomplete_not_evidence_of_absence"]


def cursor_observation():
    return {"id": "search1", "tool": "kg.hybrid_search", "created_at": "2026-09-08T01:00:00+00:00",
            "result": {"status": "ok", "query": "Jotun coating candidate ids only", "pagination": {"next_offset": 50},
                       "applied_filters": {"assignees": ["Jotun"]}, "retrieval_budget": {"mode": "candidate_only"}}}


def install_cursor_history(monkeypatch, rows, event=None):
    monkeypatch.setattr(routing, "_app_value", lambda name, default: default)
    monkeypatch.setattr(routing, "read_jsonl", lambda path: copy.deepcopy(rows))
    monkeypatch.setattr(routing, "load_scope_state", lambda: {"last_scope_event": event})


@pytest.mark.parametrize("barrier", ["kg.sql_aggregate", "kg.doc_field_scan", "kg.lookup_vocabulary", "kg.hybrid_search"])
def test_pagination_does_not_cross_latest_noncontinuable_plan(monkeypatch, barrier):
    install_cursor_history(monkeypatch, [cursor_observation(), {"tool": barrier, "result": {"status": "empty"}}])
    assert routing.latest_hybrid_search_observation() is None


@pytest.mark.parametrize("action", ["clear_global", "set_new", "use_history"])
def test_scope_event_invalidates_older_cursor(monkeypatch, action):
    install_cursor_history(monkeypatch, [cursor_observation()], {"scope_action": action, "resolved_at": "2026-09-08T01:01:00+00:00"})
    assert routing.latest_hybrid_search_observation() is None


def test_new_cursor_after_scope_event_can_continue_through_its_expansion(monkeypatch):
    cursor = cursor_observation()
    expansion = {"tool": "kg.expand_hyperedge_multihop", "result": {"planner_parent_tool_observation_id": cursor["id"]}}
    install_cursor_history(monkeypatch, [cursor, expansion], {"scope_action": "clear_global", "resolved_at": "2026-09-08T00:59:00+00:00"})
    assert routing.latest_hybrid_search_observation() == cursor
    expansion["result"] = {}
    install_cursor_history(monkeypatch, [cursor, expansion])
    assert routing.latest_hybrid_search_observation() is None


@pytest.mark.parametrize("barrier", [False, True])
def test_long_candidate_pagination_keeps_recent_plan_boundary(monkeypatch, barrier):
    question = "More candidate ids from the same company, next page only."
    rows = [cursor_observation()]
    if barrier:
        rows.append({"tool": "kg.sql_aggregate", "result": {"status": "ok", "intent": "list", "target": "doc_id"}})
    install_cursor_history(monkeypatch, rows)
    ctx = routing.RouterContext(question, {"scope_action": "none"}, question, False, {"calls": []})
    route = routing._check_keyword_routes(ctx)
    assert route is not None
    if barrier:
        assert route["calls"] == [] and route["router"] == "deterministic_pagination_unavailable"
    else:
        call = route["calls"][0]
        assert call["query"] == rows[0]["result"]["query"]
        assert call["filters"]["assignees"] == ["Jotun"]
        assert call["offset"] == 50 and call["expand_top_k"] == 0


def test_current_scope_change_cannot_restore_cursor(monkeypatch):
    install_cursor_history(monkeypatch, [cursor_observation()])
    ctx = routing.RouterContext("next page", {"scope_action": "clear_global"}, "next page", False, {"calls": []})
    assert routing._check_keyword_routes(ctx)["calls"] == []


@pytest.mark.parametrize("barrier", [None, "aggregate", "clear"])
def test_long_pagination_runtime_never_falls_back_or_expands(monkeypatch, barrier):
    question = "More candidate ids from the same company, next page only."
    events = install_runtime(monkeypatch, [])
    rows = [cursor_observation()]
    if barrier == "aggregate":
        rows.append({"tool": "kg.sql_aggregate", "result": {"status": "ok", "intent": "list", "target": "doc_id"}})
    event = {"scope_action": "clear_global", "resolved_at": "2026-09-08T01:01:00+00:00"} if barrier == "clear" else None
    install_cursor_history(monkeypatch, rows, event)
    ctx = routing.RouterContext(question, {"scope_action": "none"}, question, False, {"calls": [search_call()]})
    monkeypatch.setattr(routing, "_build_router_context", lambda question: ctx)
    monkeypatch.setattr(routing, "route_tools_with_qwen", lambda *args, **kwargs: pytest.fail("pagination must not replan"))
    monkeypatch.setattr(tool_runtime, "route_tools", routing.route_tools)
    observations = tool_runtime.maybe_run_tools(question)
    if barrier:
        assert events == [] and observations == []
    else:
        assert [event[0] for event in events] == ["search"]
        assert events[0][2]["offset"] == 50
        assert observations[0]["result"]["retrieval_budget"]["mode"] == "candidate_only"


def test_scope_event_without_timestamp_fails_closed(monkeypatch):
    install_cursor_history(monkeypatch, [cursor_observation()], {"scope_action": "clear_global"})
    assert routing.latest_hybrid_search_observation() is None


@pytest.mark.parametrize("envelope", ["items", "exact_items", "adjacent_items"])
def test_doc_scope_guards_every_envelope_without_reranking(envelope):
    good = [{"doc_id": "WO2020000001A1", "object_id": f"{source}::WO2020000001A1::HE1", "rank": rank, "score": score}
            for source, rank, score in [("source_a", 9, 0.2), ("source_b", 3, 0.9)]]
    bad = {"doc_id": "WO2020000002A1", "object_id": "source_a::WO2020000002A1::HE2", "rank": 1}
    rows = [good[0], bad, good[1]]
    if envelope == "adjacent_items":
        rows = [{"item": row, "unmet_constraints": ["materials:zinc"], "satisfied_constraints": []} for row in rows]
    original = {"status": "ok", "items": good[:1], "exact_items": good[:1], "adjacent_items": [],
                "pagination": {"next_offset": 50}, "summary": {"pool_size": 100}, envelope: rows}
    before = copy.deepcopy(original)
    scoped = tool_runtime.filter_search_result_by_doc_scope(original, {"doc_ids": ["WO2020000001A1"]})
    assert scoped[envelope] == [rows[0], rows[2]]
    assert scoped["pagination"] == before["pagination"] and scoped["summary"]["pool_size"] == 100
    assert original == before


def test_doc_scope_rejects_conflicting_qualified_identity():
    original = {"status": "ok", "items": [], "adjacent_items": [{"item": {
        "doc_id": "WO2020000001A1", "object_id": "source_a::WO2020000002A1::HE1",
    }}]}
    assert tool_runtime.filter_search_result_by_doc_scope(original, {"doc_ids": ["WO2020000001A1"]})["adjacent_items"] == []


@pytest.mark.parametrize("fallback", [False, True])
def test_stale_backend_adjacent_cannot_escape_to_compact_or_fallback(monkeypatch, fallback):
    import answering
    question = "coating candidate ids only"
    call = {"tool": "kg.doc_field_scan" if fallback else "kg.hybrid_search", "query": question,
            "doc_ids": ["WO2020000001A1"], "filters": {"doc_ids": ["WO2020000001A1"]}}
    install_runtime(monkeypatch, [call])
    monkeypatch.setattr(tool_runtime, "kg_doc_field_scan", lambda **kwargs: {
        "status": "empty", "items": [], "summary": {"hyperedges_scanned": 0},
    })
    monkeypatch.setattr(tool_runtime, "kg_hybrid_search", lambda *args, **kwargs: {
        "status": "empty", "items": [], "exact_items": [], "adjacent_items": [{"item": {
            "doc_id": "WO2020000002A1", "object_id": "source_a::WO2020000002A1::HE1", "rank": 1,
        }, "unmet_constraints": ["materials:zinc"]}], "summary": {"adjacent_returned": 1},
    })
    rows = tool_runtime.maybe_run_tools(question)
    result = rows[-1]["result"]
    assert result["adjacent_items"] == [] and result["summary"]["adjacent_returned"] == 0
    compact = answering._compact_current_tool_observation(rows[-1])
    assert "WO2020000002A1" not in json.dumps(compact)
    answer = answering.structured_tool_fallback_answer({"tool_observations": rows}, question)
    assert "WO2020000002A1" not in answer
