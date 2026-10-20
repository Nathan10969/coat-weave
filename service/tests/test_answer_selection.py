import copy
import json
import sys
import io
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import answer_selection as selection
import answering
from test_answer_evidence_delivery import item, packet


def fixture():
    prepared = answering.compact_packet_for_answer(packet([item(1), item(2, sample="S1"), item(3), item(4), item(5)]))
    return prepared, selection.catalog(prepared)


def response(choices, handles=None):
    return json.dumps({"selected_samples": [{"sample_handle": h, "claims": [{"text": "Source-supported sample record.",
        "facts_refs": c["facts_refs"], "evidence_refs": c["evidence_refs"]}]} for h, c in choices.items()
        if handles is None or h in handles], "coverage_note": "Other records not displayed."})


def test_catalog_merges_samples_without_mutating_full_input():
    prepared, choices = fixture()
    before = copy.deepcopy(prepared)
    assert len(choices) == 4
    assert len(choices["S1"]["object_ids"]) == 2
    assert len(selection.validate(response(choices, ["S1", "S2", "S3"]), choices, 3)["selected_samples"]) == 3
    assert prepared == before


def test_rejects_excess_and_duplicate_samples():
    _, choices = fixture()
    with pytest.raises(ValueError, match="display_count"):
        selection.validate(response(choices), choices, 3)
    raw = json.loads(response(choices, ["S1"]))
    raw["selected_samples"] *= 2
    with pytest.raises(ValueError, match="sample_identity"):
        selection.validate(json.dumps(raw), choices, 2)


def test_rejects_unknown_reference_and_cross_patent_claim():
    _, choices = fixture()
    raw = json.loads(response(choices, ["S1"]))
    claim = raw["selected_samples"][0]["claims"][0]
    claim["evidence_refs"] = ["invented"]
    with pytest.raises(ValueError, match="unbound_reference"):
        selection.validate(json.dumps(raw), choices, 1)
    claim["evidence_refs"] = choices["S1"]["evidence_refs"]
    claim["text"] = "WO2019115747A1 has this formulation."
    with pytest.raises(ValueError, match="cross_patent"):
        selection.validate(json.dumps(raw), choices, 1)


def test_underfilled_selection_is_corrected_not_reported_complete(monkeypatch):
    prepared, choices = fixture()
    attempts = []
    def complete(*args, **kwargs):
        attempts.append(1)
        return response(choices, ["S1"] if len(attempts) == 1 else ["S1", "S2", "S3"])
    monkeypatch.setattr(answering, "_request_complete_text", complete)
    answer, receipt = answering._selected_evidence_answer("show related samples", prepared, {}, choices)
    assert receipt["attempts"] == 2
    assert receipt["displayed_samples"] == 3
    assert "answer_display_count" in receipt["validation_failures"][0]
    assert receipt["selection_coverage_note"]
    assert "样品来源标识" not in answer


def test_handle_only_selection_binds_full_sources_without_generated_claims():
    prepared, choices = fixture()
    result = selection.validate(json.dumps({'selected_handles': ['S1', 'S2', 'S3'],
        'coverage_note': 'Relevant samples'}), choices, 3)
    assert result['selected_samples'][0]['claims'][0]['facts_refs'] == choices['S1']['facts_refs']
    assert len(result['selected_samples']) == 3
    with pytest.raises(ValueError, match='sample_identity'):
        selection.validate('{"selected_handles":["INVENTED"]}', choices, 1)


def test_one_sample_render_and_covered_only_catalog():
    prepared, choices = fixture()
    payload = selection.validate(response(choices, ["S1"]), choices, 1)
    assert selection.render(payload, choices).count("### ") == 1
    assert len(selection.catalog(prepared, ["c::D::H1"])) == 1
    assert not selection.catalog(prepared, [])


def test_full_input_single_call_and_one_corrective_retry(monkeypatch):
    prepared, choices = fixture()
    captured = []
    def complete(messages, cfg, **kw):
        captured.append(copy.deepcopy(messages))
        return response(choices) if len(captured) == 1 else response(choices, ["S1", "S2", "S3"])
    monkeypatch.setattr(answering, "_request_complete_text", complete)
    answer, receipt = answering._selected_evidence_answer("give formulations", prepared, {}, choices)
    assert receipt["attempts"] == 2
    assert receipt["displayed_samples"] == 3
    assert answer.count("### ") == 3
    assert "TAIL" in captured[0][-1]["content"] and "TAIL" in captured[1][-2]["content"]


def test_twice_failed_returns_only_verified_inventory(monkeypatch):
    prepared, choices = fixture()
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("synthetic")
    monkeypatch.setattr(answering, "_request_complete_text", fail)
    answer, receipt = answering._selected_evidence_answer("give one", prepared, {}, choices)
    assert len(calls) == 2 and receipt["answer_outcome"] == "selection_fallback"
    assert answer.count("- D /") == 1
    assert "synthetic" not in answer


def test_hard_scope_and_adjacent_label():
    prepared, choices = fixture()
    prepared["active_scope"] = {"policy": "hard", "doc_ids": ["OTHER"]}
    assert not selection.catalog(prepared)
    prepared.pop("active_scope")
    prepared["tool_observations"].append({"tool": "kg.hybrid_search", "result": {
        "items": [], "adjacent_items": [{"item": {"object_id": "c::D::H1"}}]}})
    choices = selection.catalog(prepared)
    payload = selection.validate(response(choices, ["S1"]), choices, 1)
    assert "未计入精确结果" in selection.render(payload, choices)


def test_context_rejection_is_not_silent_input_truncation(monkeypatch):
    prepared, choices = fixture()
    captured = []
    def rejected(messages, cfg, **kw):
        captured.append(copy.deepcopy(messages))
        raise urllib.error.HTTPError("https://example.invalid", 400, "Bad request", {},
            io.BytesIO(b'{"error":{"code":"context_length_exceeded"}}'))
    monkeypatch.setattr(answering, "_request_complete_text", rejected)
    _, receipt = answering._selected_evidence_answer("q", prepared, {}, choices)
    assert receipt["answer_outcome"] == "provider_context_exceeded"
    assert len(captured) == 1 and "TAIL" in captured[0][-1]["content"]
    batches = []
    def batched(question, full, plan, cfg, *args, **kwargs):
        batches.append((full, plan))
        yield {"type": "done"}
    monkeypatch.setattr(answering, "_stream_batched_answer", batched)
    list(answering._stream_selected_answer("q", prepared, {"max_output_tokens": 131072}))
    assert batches[0][0] == prepared
    assert sum(len(list(answering.evidence_delivery.expanded_items(p))) for p in batches[0][1]["batches"]) == 5


def test_broken_source_binding_is_not_retried_as_model_error(monkeypatch):
    prepared, choices = fixture()
    prepared["source_records"]["evidence"].clear()
    monkeypatch.setattr(answering, "_request_complete_text", lambda *a, **kw: pytest.fail("provider must not run"))
    _, receipt = answering._selected_evidence_answer("q", prepared, {}, choices)
    assert receipt["answer_outcome"] == "source_binding_failure"
    assert receipt["attempts"] == 0


def test_exact_empty_is_preserved_through_compaction():
    raw = packet([item()])
    raw["tool_observations"].append({"tool": "kg.hybrid_search", "status": "ok", "result": {
        "status": "ok", "items": [{"object_id": "c::D::H1"}], "exact_items": [],
        "adjacent_items": [{"item": {"object_id": "c::D::H1"}, "unmet_constraints": ["materials"]}]}})
    prepared = answering.compact_packet_for_answer(raw)
    choices = selection.catalog(prepared)
    assert choices["S1"]["match_scope"] == "adjacent"
    assert choices["S1"]["unmet_constraints"] == ["materials"]


def test_expansion_facet_exclusion_and_related_override_search_exact():
    raw = packet([item(1), item(2)])
    raw["tool_observations"][0]["result"]["semantic_guard"] = {"item_warnings": [
        {"object_id": "c::D::H1", "facet_status": "excluded"},
        {"object_id": "c::D::H2", "facet_status": "related_match", "reason": "wrong_material_role"}]}
    raw["tool_observations"].append({"tool": "kg.hybrid_search", "result": {
        "items": [{"object_id": "c::D::H1"}, {"object_id": "c::D::H2"}]}})
    choices = selection.catalog(answering.compact_packet_for_answer(raw))
    assert len(choices) == 1
    assert choices["S1"]["object_ids"] == ["c::D::H2"]
    assert choices["S1"]["match_scope"] == "adjacent"


def test_failed_expansion_cannot_turn_preview_omissions_into_negative_claims(monkeypatch):
    raw = packet([item()])
    raw["tool_observations"][0]["status"] = "error"
    monkeypatch.setattr(answering, "provider_config", lambda: {
        "api_key": "test", "base_url": "https://example.invalid", "model": "test", "max_output_tokens": 1024})
    monkeypatch.setattr(answering, "_open_provider_response", lambda *a, **kw: pytest.fail("no provider call"))
    events = list(answering.stream_qwen("complete formulation", raw))
    assert "不表示专利中没有" in events[-2]["content"]
    assert events[-1]["evidence_delivery"]["answer_outcome"] == "no_verified_expansion"


def test_source_render_cannot_drop_material_or_invent_test_binding():
    row = item()
    row["hyperedge_summary"]["materials"].append({"material": "Garamite 1958", "amount": {"value": 1, "unit": "parts"}})
    row["hyperedge_summary"]["test_method"] = {"standard_id": ["ISO 4624"]}
    row["evidence"][0]["quote"] = "Reactive diluent 6.2; pull-off adhesion 4.1 MPa"
    prepared = answering.compact_packet_for_answer(packet([row]))
    choices = selection.catalog(prepared)
    payload = json.loads(response(choices))
    payload["selected_samples"][0]["claims"][0]["text"] = "Incorrect complete formula omitting filler and claiming ISO 9227."
    validated = selection.validate(json.dumps(payload), choices, 3)
    answer = selection.render(validated, choices, selection.attach_source_views(choices, prepared))
    assert "Garamite 1958" in answer and "Reactive diluent 6.2" in answer
    assert "ISO 4624" in answer and "ISO 9227" not in answer
    assert "不宣称覆盖专利完整配方表" in answer
