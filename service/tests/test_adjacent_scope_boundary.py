from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))

import answering
import tool_runtime


DOC = "WO2020000001A1"
OTHER = "WO2020000002A1"


def candidate(doc=DOC, source="source_a", rank=1):
    return {"doc_id": doc, "object_id": f"{source}::{doc}::HE1", "rank": rank, "score": 0.2}


@pytest.mark.parametrize("envelope", ["items", "exact_items", "adjacent_items"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_every_envelope_preserves_source_variants_ranking_and_counters(envelope, wrapped):
    rows = [candidate(rank=9), candidate(OTHER), candidate(source="source_b", rank=3)]
    if wrapped:
        rows = [{"item": row, "unmet_constraints": ["materials:zinc"]} for row in rows]
    original = {
        "status": "ok", "items": [candidate()], "exact_items": [candidate()],
        "adjacent_items": [], "pagination": {"next_offset": 50},
        "summary": {"pool_size": 100, "source_doc_count": 64, "dense_found": 100,
                    "sparse_found": 80, "returned": 1, "exact_returned": 1, "adjacent_returned": 0},
        "warnings": ["existing warning"], envelope: rows,
    }
    before = copy.deepcopy(original)
    scoped = tool_runtime.filter_search_result_by_doc_scope(original, {"doc_ids": [DOC]})
    assert scoped[envelope] == [rows[0], rows[2]]
    assert scoped[envelope][0] is rows[0]
    assert scoped["pagination"] == before["pagination"]
    for key in ("pool_size", "source_doc_count", "dense_found", "sparse_found"):
        assert scoped["summary"][key] == before["summary"][key]
    count_key = {"items": "returned", "exact_items": "exact_returned", "adjacent_items": "adjacent_returned"}[envelope]
    assert scoped["summary"][count_key] == 2
    assert scoped["summary"]["scope_filtered_envelopes"][envelope] == {"from": 3, "to": 2}
    assert scoped["warnings"][0] == "existing warning"
    assert original == before


@pytest.mark.parametrize("row", [
    None, {}, {"item": None},
    {"item": {"doc_id": DOC, "object_id": f"source_a::{OTHER}::HE1"}},
    {"doc_id": OTHER, "item": {"doc_id": DOC}},
])
def test_missing_or_conflicting_identity_is_not_verified(row):
    scoped = tool_runtime.filter_search_result_by_doc_scope(
        {"status": "ok", "items": [], "adjacent_items": [row]}, {"doc_ids": [DOC]},
    )
    assert scoped["adjacent_items"] == []
    assert scoped["summary"]["adjacent_returned"] == 0


@pytest.mark.parametrize("status", ["ok", "unsupported", "error"])
def test_empty_scope_preserves_backend_failure_status(status):
    scoped = tool_runtime.filter_search_result_by_doc_scope(
        {"status": status, "items": [candidate(OTHER)], "exact_items": [candidate(OTHER)]},
        {"doc_ids": [DOC]},
    )
    assert scoped["status"] == ("empty" if status == "ok" else status)
    assert scoped["items"] == scoped["exact_items"] == []


def test_no_scope_is_identity_and_legacy_opaque_binding_is_preserved():
    original = {"status": "ok", "items": [candidate(OTHER)], "adjacent_items": [candidate()]}
    assert tool_runtime.filter_search_result_by_doc_scope(original, {}) is original
    for doc in (DOC, "DOC1"):
        original = {"status": "ok", "items": [{"doc_id": doc, "object_id": "COLL::DOC1::HE1"}]}
        assert tool_runtime.filter_search_result_by_doc_scope(original, {"doc_ids": [doc]}) is original


@pytest.mark.parametrize("fallback", [False, True])
def test_stale_backend_adjacent_cannot_escape_to_compact_or_fallback(monkeypatch, fallback):
    question = "coating candidate ids only"
    call = {
        "tool": "kg.doc_field_scan" if fallback else "kg.hybrid_search", "query": question,
        "doc_ids": [DOC], "filters": {"doc_ids": [DOC]},
    }
    records, searches = [], []
    monkeypatch.setattr(tool_runtime, "_app_value", lambda name, default: default)
    monkeypatch.setattr(tool_runtime, "route_tools", lambda question: {"calls": [copy.deepcopy(call)]})

    def record(question, result, **kwargs):
        observation = {"id": str(len(records)), "result": copy.deepcopy(result), **kwargs}
        records.append(observation)
        return observation

    def search(*args, **kwargs):
        searches.append(kwargs)
        return {
            "status": "empty", "items": [], "exact_items": [],
            "adjacent_items": [{"item": candidate(OTHER), "unmet_constraints": ["materials:zinc"]}],
            "summary": {"adjacent_returned": 1, "source_doc_count": 64},
        }

    monkeypatch.setattr(tool_runtime, "record_tool_observation", record)
    monkeypatch.setattr(tool_runtime, "kg_doc_field_scan", lambda **kwargs: {
        "status": "empty", "items": [], "summary": {"hyperedges_scanned": 0},
    })
    monkeypatch.setattr(tool_runtime, "kg_hybrid_search", search)
    monkeypatch.setattr(tool_runtime, "kg_expand_hyperedge_multihop", lambda *args, **kwargs: pytest.fail("unexpected expand"))
    rows = tool_runtime.maybe_run_tools(question)
    assert searches and searches[0]["filters"]["doc_ids"] == [DOC]
    result = rows[-1]["result"]
    assert result["adjacent_items"] == []
    assert result["summary"]["adjacent_returned"] == 0
    assert result["summary"]["source_doc_count"] == 64
    compact = answering._compact_current_tool_observation(rows[-1])
    assert OTHER not in json.dumps(compact)
    answer = answering.structured_tool_fallback_answer({"tool_observations": rows}, question)
    assert OTHER not in answer
