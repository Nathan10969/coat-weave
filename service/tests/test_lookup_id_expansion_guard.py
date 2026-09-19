import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

import routing


@pytest.mark.parametrize("tool", ["kg.sql_aggregate", "kg.hybrid_search"])
@pytest.mark.parametrize("planned_first", [True, False])
def test_partial_vocabulary_cannot_add_material_id_intersection(monkeypatch, tool, planned_first):
    lookup = {"tool": "kg.lookup_vocabulary", "query": "ABC", "dimension": "materials"}
    original = {"tool": tool, "filters": {"materials": ["ABC"], "assignees": ["Acme"]}}
    proposed = {**original, "filters": {**original["filters"], "material_canonical_ids": ["MAT_candidate"]}}
    feedback = [{"tool": lookup["tool"], "result": {
        "status": "ok", "dimension": "materials", "query": "ABC",
        "canonical_id_expansion_complete": False,
        "exact_items": [{"canonical_id": "MAT_candidate", "match_type": "exact"}],
    }}]
    before = copy.deepcopy(feedback)
    monkeypatch.setattr(routing, "route_tools_with_qwen", lambda *a, **kw: {"calls": [proposed]})
    monkeypatch.setattr(routing, "_record_route_decision", lambda *a: None)
    route = {"calls": [lookup, original] if planned_first else [lookup]}
    result = routing.replan_tools_after_feedback("ABC patents for Acme", route, feedback)
    call = result["calls"][0]
    assert "material_canonical_ids:incomplete_vocabulary_expansion" in call["unsupported_constraints"]
    assert call["filters"]["materials"] == ["ABC"]
    assert call["filters"]["assignees"] == ["Acme"]
    assert feedback == before


def test_explicit_original_ids_survive_partial_lookup(monkeypatch):
    original = {"tool": "kg.sql_aggregate", "filters": {"material_canonical_ids": ["MAT_explicit"]}}
    monkeypatch.setattr(routing, "route_tools_with_qwen", lambda *a, **kw: {"calls": [original]})
    monkeypatch.setattr(routing, "_record_route_decision", lambda *a: None)
    result = routing.replan_tools_after_feedback("only MAT_explicit", {"calls": [original]}, [{
        "tool": "kg.lookup_vocabulary", "result": {"status": "ok", "dimension": "materials",
        "query": "ABC", "canonical_id_expansion_complete": False},
    }])
    assert not result["calls"][0]["unsupported_constraints"]
    assert result["calls"][0]["filters"]["material_canonical_ids"] == ["MAT_explicit"]


@pytest.mark.parametrize("complete", [None, False, True])
def test_id_expansion_requires_explicit_completeness(complete):
    call = {"tool": "kg.sql_aggregate", "filters": {"material_canonical_ids": ["MAT_candidate"]}}
    result = {"status": "ok", "dimension": "materials"}
    if complete is not None:
        result["canonical_id_expansion_complete"] = complete
    guarded = routing._guard_lookup_id_expansion(call, {}, [], [{"result": result}])
    assert bool(guarded.get("unsupported_constraints")) == (complete is not True)


def test_exact_id_lookup_does_not_authorize_other_candidate_ids():
    lookup = [{"tool": "kg.lookup_vocabulary", "dimension": "materials", "query": "MAT_explicit"}]
    feedback = [{"result": {"status": "ok", "dimension": "materials", "canonical_id_expansion_complete": False}}]
    call = {"tool": "kg.sql_aggregate", "filters": {"material_canonical_ids": ["MAT_explicit"]}}
    assert routing._guard_lookup_id_expansion(call, {}, lookup, feedback) == call
    widened = {**call, "filters": {"material_canonical_ids": ["MAT_explicit", "MAT_candidate"]}}
    guarded = routing._guard_lookup_id_expansion(widened, {}, lookup, feedback)
    assert guarded["lookup_expansion_rejected_ids"] == ["MAT_candidate"]


@pytest.mark.parametrize("field,values", [
    ("materials", ["MAT_candidate"]),
    ("material_groups", [{"values": ["MAT_candidate"]}]),
    ("material_groups", [{"values": ["mat_candidate"]}]),
])
def test_candidate_ids_cannot_bypass_guard_via_name_fields(field, values):
    original = {"tool": "kg.sql_aggregate", "filters": {"materials": ["ABC"]}}
    call = {**original, "filters": {**original["filters"], field: values}}
    feedback = [{"result": {"status": "ok", "dimension": "materials", "query": "ABC",
        "items": [{"canonical_id": "MAT_candidate"}], "canonical_id_expansion_complete": False}}]
    guarded = routing._guard_lookup_id_expansion(call, original, [], feedback)
    assert f"{field}:incomplete_vocabulary_expansion" in guarded["unsupported_constraints"]
    assert [value.casefold() for value in guarded["lookup_expansion_rejected_ids"]] == ["mat_candidate"]
