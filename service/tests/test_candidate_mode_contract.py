from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))

import kg_contract
import routing
import tool_clients
import tool_runtime


QUESTION = "Search for Jotun epoxy coating candidates by semantic relevance. Only list candidates; do not expand evidence."
POLICY = {"candidate_k": 100, "top_k": 50, "expand_top_k": 30}


def model_call(mode="candidates"):
    return {
        "tool": "kg.hybrid_search", "query": QUESTION, "result_mode": mode,
        "filters": {"assignees": ["Jotun"], "doc_ids": ["DOC1"]}, "offset": 50,
        "candidate_k": 1, "top_k": 1, "expand_top_k": 1,
    }


def decode_and_sanitize(call, question=QUESTION):
    decoded = routing._tool_call_to_internal_call({"function": {
        "name": "kg_hybrid_search", "arguments": json.dumps(call),
    }}, question)
    return routing.sanitize_tool_routing({"calls": [decoded]}, question, router="test")["calls"][0]


def install_runtime(monkeypatch, call):
    searches, expansions, records = [], [], []
    monkeypatch.setattr(tool_runtime, "_app_value", lambda name, default: default)
    monkeypatch.setattr(routing, "_decide_route", lambda question: {"calls": [copy.deepcopy(call)]})
    monkeypatch.setattr(routing, "_record_route_decision", lambda *args: None)
    monkeypatch.setattr(tool_runtime, "route_tools", routing.route_tools)

    def search(query, **kwargs):
        searches.append(kwargs)
        return {
            "status": "ok", "query": query, "applied_filters": kwargs["filters"],
            "pagination": {"offset": 50, "next_offset": 75},
            "items": [{"doc_id": "DOC1", "object_id": f"source::DOC1::HE{i}", "rank": i + 1} for i in range(50)],
        }

    def expand(ids, **kwargs):
        expansions.append((ids, kwargs))
        return {"status": "ok", "items": [{"object_id": oid} for oid in ids]}

    def record(question, result, **kwargs):
        row = {"id": str(len(records)), "result": copy.deepcopy(result), **kwargs}
        records.append(row)
        return row

    monkeypatch.setattr(tool_runtime, "kg_hybrid_search", search)
    monkeypatch.setattr(tool_runtime, "kg_expand_hyperedge_multihop", expand)
    monkeypatch.setattr(tool_runtime, "record_tool_observation", record)
    return searches, expansions, records


def test_model_schema_exposes_mode_but_not_numeric_budget():
    schema = kg_contract.openai_tool_parameters("kg.hybrid_search")
    spec = schema["properties"]["result_mode"]
    assert spec["enum"] == ["candidates", "evidence"]
    assert spec["default"] == "evidence" and "evidence expansion" in spec["description"]
    assert "result_mode" not in schema["required"]
    assert not set(POLICY).intersection(schema["properties"])
    tool = next(tool for tool in routing._kg_tools_openai_format() if tool["function"]["name"] == "kg_hybrid_search")
    assert tool["function"]["parameters"] == schema


@pytest.mark.parametrize("mode,expand", [("candidates", 0), ("evidence", 30)])
@pytest.mark.parametrize("budget", [0, 1, 500])
def test_explicit_mode_survives_all_layers_with_fixed_budget(monkeypatch, mode, expand, budget):
    original = model_call(mode)
    original.update(dict.fromkeys(POLICY, budget))
    cleaned = decode_and_sanitize(original)
    assert cleaned["result_mode"] == mode
    assert cleaned["offset"] == 50 and cleaned["filters"]["assignees"] == ["Jotun"]
    assert cleaned["filters"]["doc_ids"] == ["DOC1"]
    assert {key: cleaned[key] for key in POLICY} == {**POLICY, "expand_top_k": expand}
    assert kg_contract.apply_retrieval_policy(cleaned) == cleaned
    searches, expansions, records = install_runtime(monkeypatch, cleaned)
    tool_runtime.maybe_run_tools(QUESTION)
    assert searches[0]["offset"] == 50
    assert searches[0]["top_k"] == 50 and searches[0]["candidate_k"] == 100
    assert "result_mode" not in searches[0]
    result = records[0]["result"]
    assert result["result_mode"] == mode
    assert result["retrieval_budget"]["actual"]["selected"] == expand
    assert result["pagination"] == {"offset": 50, "next_offset": 75}
    assert sum(len(ids) for ids, kwargs in expansions) == expand
    next_call = routing.pagination_route_from_observation("next page", records[0])["calls"][0]
    assert next_call["result_mode"] == mode and next_call["expand_top_k"] == expand
    assert next_call["offset"] == 75 and next_call["filters"] == cleaned["filters"]


@pytest.mark.parametrize("mode", ["unknown", "", None, ["candidates"], {"mode": "candidates"}])
@pytest.mark.parametrize("sanitize", [False, True])
def test_unknown_mode_is_unsupported_without_backend_or_expansion(monkeypatch, mode, sanitize):
    call = model_call(mode)
    if sanitize:
        call = decode_and_sanitize(call)
    searches, expansions, records = install_runtime(monkeypatch, call)
    tool_runtime.maybe_run_tools(QUESTION)
    assert not searches and not expansions
    assert len(records) == 1 and records[0]["result"]["status"] == "unsupported"
    result = records[0]["result"]
    assert result["result_mode"] == mode
    assert any(issue.startswith("result_mode:") for issue in result["unsupported_constraints"])
    assert result["retrieval_budget"]["effective"]["expand_top_k"] == 0


def test_legacy_phrase_is_fallback_only_and_missing_mode_defaults_evidence():
    call = model_call()
    call.pop("result_mode")
    assert decode_and_sanitize(call, "coating formulation")["result_mode"] == "evidence"
    assert decode_and_sanitize(call, "candidate ids only")["result_mode"] == "candidates"
    call["result_mode"] = "evidence"
    cleaned = decode_and_sanitize(call, "candidate ids only")
    assert cleaned["result_mode"] == "evidence" and cleaned["expand_top_k"] == 30
    assert kg_contract.apply_retrieval_policy(cleaned, candidate_only=True)["expand_top_k"] == 30


def test_real_client_wire_does_not_receive_mode(monkeypatch):
    call = decode_and_sanitize(model_call())
    bodies = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"status":"empty","items":[]}'

    def urlopen(request, **kwargs):
        bodies.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr(tool_runtime, "_app_value", lambda name, default: default)
    monkeypatch.setattr(tool_runtime, "kg_hybrid_search", tool_clients.kg_hybrid_search)
    monkeypatch.setattr(tool_clients.urllib.request, "urlopen", urlopen)
    result = tool_runtime.run_kg_hybrid_search_for_call(call, QUESTION, call["query"])
    assert result["result_mode"] == "candidates"
    assert "result_mode" not in bodies[0] and "expand_top_k" not in bodies[0]
    assert bodies[0]["top_k"] == 50 and bodies[0]["candidate_k"] == 100
    assert bodies[0]["offset"] == 50
