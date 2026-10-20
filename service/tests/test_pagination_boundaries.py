from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

import routing
import tool_runtime


LONG_NEXT_PAGE = "Please continue the same ranked candidate pool from this company, next page only."


def cursor_observation():
    return {
        "id": "search1", "tool": "kg.hybrid_search", "created_at": "2026-09-08T01:00:00+00:00",
        "result": {
            "status": "ok", "query": "Jotun coating candidate ids only",
            "pagination": {"next_offset": 50},
            "applied_filters": {"assignees": ["Jotun"], "doc_ids": ["DOC1"]},
            "retrieval_budget": {"mode": "candidate_only"},
        },
    }


def install_history(monkeypatch, rows, event=None):
    monkeypatch.setattr(routing, "_app_value", lambda name, default: default)
    monkeypatch.setattr(routing, "read_jsonl", lambda path: copy.deepcopy(rows))
    monkeypatch.setattr(routing, "load_scope_state", lambda: {"last_scope_event": event})


def context(question=LONG_NEXT_PAGE, action="none"):
    return routing.RouterContext(
        question, {"scope_action": action}, question, False,
        {"calls": [{"tool": "kg.expand_hyperedge_multihop", "object_ids": ["OLD::DOC1::HE1"]}]},
    )


@pytest.mark.parametrize("question", [LONG_NEXT_PAGE, "\u8bf7\u7ee7\u7eed" + "\u540c\u4e00\u516c\u53f8\u7684\u5019\u9009\u7ed3\u679c" * 5 + "\u4e0b\u4e00\u9875"])
def test_long_explicit_next_page_retains_cursor_company_and_candidate_only(monkeypatch, question):
    assert len(question) > 40
    previous = cursor_observation()
    install_history(monkeypatch, [previous])
    result = routing._check_keyword_routes(context(question))
    assert result["router"] == "deterministic_candidate_pagination"
    call = result["calls"][0]
    assert call["query"] == previous["result"]["query"]
    assert call["filters"]["assignees"] == ["Jotun"]
    assert call["filters"]["doc_ids"] == ["DOC1"]
    assert call["offset"] == 50
    assert call["candidate_k"] == 100 and call["top_k"] == 50
    assert call["expand_top_k"] == 0
    assert call["retrieval_budget"]["mode"] == "candidate_only"


def test_long_generic_continue_does_not_gain_new_pagination_intent():
    assert routing.pagination_followup_request("continue")
    assert not routing.pagination_followup_request("Continue investigating the coating formulation and compare its curing mechanisms.")


@pytest.mark.parametrize("tool", ["kg.sql_aggregate", "kg.doc_field_scan", "kg.hybrid_search"])
def test_latest_noncontinuable_plan_is_a_boundary(monkeypatch, tool):
    install_history(monkeypatch, [cursor_observation(), {"tool": tool, "result": {"status": "empty"}}])
    assert routing.latest_hybrid_search_observation() is None
    result = routing._check_keyword_routes(context())
    assert result["router"] == "deterministic_pagination_unavailable"
    assert result["calls"] == []
    assert result["needs_tools"] is False


@pytest.mark.parametrize("row", [None, {}, {"tool": "kg.sql_aggregate", "result": None}])
def test_malformed_latest_observation_does_not_resurrect_older_search(monkeypatch, row):
    install_history(monkeypatch, [cursor_observation(), row])
    assert routing.latest_hybrid_search_observation() is None


@pytest.mark.parametrize("envelope", ["row", "result"])
@pytest.mark.parametrize("status", ["error", "unsupported"])
def test_failed_search_cannot_supply_cursor(monkeypatch, envelope, status):
    latest = cursor_observation()
    (latest if envelope == "row" else latest["result"])["status"] = status
    install_history(monkeypatch, [cursor_observation(), latest])
    assert routing.latest_hybrid_search_observation() is None


@pytest.mark.parametrize("parent", [None, "different-search"])
def test_unrelated_expansion_blocks_cursor(monkeypatch, parent):
    expansion = {"tool": "kg.expand_hyperedge_multihop", "result": {"planner_parent_tool_observation_id": parent}}
    install_history(monkeypatch, [cursor_observation(), expansion])
    assert routing.latest_hybrid_search_observation() is None


def test_own_expansions_can_follow_a_search_after_scope_change(monkeypatch):
    previous = cursor_observation()
    expansion = {"tool": "kg.expand_hyperedge_multihop", "result": {"planner_parent_tool_observation_id": previous["id"]}}
    install_history(monkeypatch, [previous, expansion, expansion], {"scope_action": "set_new", "resolved_at": "2026-09-08T00:59:00+00:00"})
    assert routing.latest_hybrid_search_observation() == previous


@pytest.mark.parametrize("action", ["clear_global", "set_new", "use_history"])
def test_current_scope_change_blocks_cursor_without_fallback_expand(monkeypatch, action):
    install_history(monkeypatch, [cursor_observation()])
    result = routing._check_keyword_routes(context(action=action))
    assert result["router"] == "deterministic_pagination_unavailable"
    assert result["calls"] == []


@pytest.mark.parametrize("event", [
    {"scope_action": "clear_global", "resolved_at": "2026-09-08T01:01:00+00:00"},
    {"scope_action": "set_new", "resolved_at": "2026-09-08T01:00:00+00:00"},
    {"scope_action": "use_history"},
    {"scope_action": "set_new", "resolved_at": "invalid"},
    {"scope_action": "set_new", "resolved_at": "2026-09-08T00:59:00"},
])
def test_new_or_unorderable_scope_event_invalidates_old_search(monkeypatch, event):
    install_history(monkeypatch, [cursor_observation()], event)
    assert routing.latest_hybrid_search_observation() is None


def test_missing_search_timestamp_with_scope_event_fails_closed(monkeypatch):
    previous = cursor_observation()
    previous.pop("created_at")
    install_history(monkeypatch, [previous], {"scope_action": "set_new", "resolved_at": "2026-09-08T00:59:00+00:00"})
    assert routing.latest_hybrid_search_observation() is None


def test_empty_history_stops_pagination_before_fallback_expand(monkeypatch):
    install_history(monkeypatch, [])
    result = routing._check_keyword_routes(context())
    assert result["router"] == "deterministic_pagination_unavailable"
    assert result["calls"] == []


def test_exhausted_pool_stays_exhausted(monkeypatch):
    previous = cursor_observation()
    previous["result"]["pagination"]["next_offset"] = None
    install_history(monkeypatch, [previous])
    result = routing._check_keyword_routes(context())
    assert result["router"] == "deterministic_pagination_exhausted"
    assert result["calls"] == []


@pytest.mark.parametrize("barrier", [None, "aggregate", "scope_event", "new_scope", "unrelated_expand"])
def test_stage1_runtime_never_replans_or_expands_across_pagination_boundary(monkeypatch, barrier):
    rows = [cursor_observation()]
    if barrier == "aggregate":
        rows.append({"tool": "kg.sql_aggregate", "result": {"status": "ok"}})
    elif barrier == "unrelated_expand":
        rows.append({"tool": "kg.expand_hyperedge_multihop", "result": {}})
    event = {"scope_action": "clear_global", "resolved_at": "2026-09-08T01:01:00+00:00"} if barrier == "scope_event" else None
    install_history(monkeypatch, rows, event)
    ctx = context(action="set_new" if barrier == "new_scope" else "none")
    monkeypatch.setattr(routing, "_build_router_context", lambda question: ctx)
    monkeypatch.setattr(routing, "_record_route_decision", lambda *args: None)
    monkeypatch.setattr(routing, "route_tools_with_qwen", lambda *args, **kwargs: pytest.fail("pagination must not replan"))
    monkeypatch.setattr(tool_runtime, "_app_value", lambda name, default: default)
    monkeypatch.setattr(tool_runtime, "route_tools", routing.route_tools)
    searches = []
    def search(query, **kwargs):
        searches.append((query, kwargs))
        return {"status": "ok", "items": [{"object_id": "COLL::DOC1::HE1", "doc_id": "DOC1"}]}
    monkeypatch.setattr(tool_runtime, "kg_hybrid_search", search)
    monkeypatch.setattr(tool_runtime, "kg_expand_hyperedge_multihop", lambda *args, **kwargs: pytest.fail("pagination must not expand"))
    monkeypatch.setattr(tool_runtime, "record_tool_observation", lambda question, result, **kwargs: {
        "id": "new-search", "result": copy.deepcopy(result), **kwargs,
    })
    observations = tool_runtime.maybe_run_tools(LONG_NEXT_PAGE)
    if barrier:
        assert searches == [] and observations == []
    else:
        assert len(searches) == 1 and len(observations) == 1
        query, call = searches[0]
        assert query == rows[0]["result"]["query"]
        assert call["offset"] == 50
        assert call["filters"]["assignees"] == ["Jotun"]
        assert call["filters"]["doc_ids"] == ["DOC1"]
        assert observations[0]["result"]["retrieval_budget"]["mode"] == "candidate_only"
