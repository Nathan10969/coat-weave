from __future__ import annotations

import copy
import json
import http.client
import socket
import threading
import time
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import answering
import evidence_packet as delivery


def item(n=1, *, sample=None, quote_size=1000, collection="c"):
    return {
        "object_id": f"{collection}::D::H{n}", "doc_id": "D", "hyperedge_id": f"H{n}",
        "hyperedge_summary": {"sample_id": sample or f"S{n}", "context_id": f"C{n}",
            "materials": [{"material": "resin", "value": 0, "unit": "wt%"}],
            "baseline": "CONTROL", "test_condition": {"duration": "100 h"}},
        "hyperedge_raw": {"hyperedge_id": f"H{n}", "extra_source_field": "preserve"},
        "facts": [{"fact_id": "F", "value": 0, "unit": "wt%", "evidence_ids": ["E"]}],
        "evidence": [{"evidence_id": "E", "quote": "x" * quote_size + "TAIL", "page": 7}],
        "unresolved": {},
    }


def packet(items):
    return {"tool_observations": [{"tool": "kg.expand_hyperedge_multihop", "status": "ok",
        "result": {"status": "ok", "items": items, "exact_items": copy.deepcopy(items)}}]}


def test_full_fields_quotes_and_source_packet_survive():
    raw = packet([item()])
    before = copy.deepcopy(raw)
    prepared = answering.compact_packet_for_answer(raw)
    assert raw == before
    record = prepared["tool_observations"][0]["result"]["items"][0]
    assert record["hyperedge_summary"] == raw["tool_observations"][0]["result"]["items"][0]["hyperedge_summary"]
    assert record["hyperedge_raw"]["extra_source_field"] == "preserve"
    assert any(r["quote"].endswith("TAIL") for r in prepared["source_records"]["evidence"].values())
    assert "exact_items" not in prepared["tool_observations"][0]["result"]


def test_dedup_scopes_and_conflicts():
    prepared = delivery.pack_evidence(packet([item(1), item(2), item(3, collection="other")]))
    assert len(prepared["source_records"]["evidence"]) == 2
    assert len(prepared["source_records"]["facts"]) == 2
    conflicting = item(2)
    conflicting["evidence"][0]["quote"] = "DIFFERENT"
    bad = delivery.pack_evidence(packet([item(1), conflicting]))
    assert not bad["tool_observations"][0]["result"]["items"]
    assert len(bad["evidence_delivery"]["failed_objects"]) == 2
    assert all(r["reason"] == "reference_conflict" for r in bad["evidence_delivery"]["failed_objects"])


def test_stable_batches_keep_a_sample_together_and_resolve_all_refs():
    raw = packet([item(1, sample="A"), item(2, sample="A"), item(3, collection="other")])
    prepared = delivery.pack_evidence(raw)
    one = delivery.pack_evidence(packet([item(1, sample="A"), item(2, sample="A")]))
    budget = delivery.json_size(one) + 300
    plan = delivery.plan_batches(prepared, budget)
    assert len(plan["batches"]) == 2
    assert not plan["failed_objects"]
    assert plan == delivery.plan_batches(prepared, budget)
    assert [len(b["tool_observations"][0]["result"]["items"]) for b in plan["batches"]] == [2, 1]
    for b in plan["batches"]:
        assert delivery.json_size(b) <= budget
        for o in b["tool_observations"]:
            for row in o["result"].get("items", []):
                for kind in ("facts", "evidence"):
                    assert all(ref in b["source_records"][kind] for ref in row[kind + "_refs"])


def test_oversized_sample_is_reported_not_sliced():
    prepared = delivery.pack_evidence(packet([item(1, quote_size=10000), item(2, collection="other", quote_size=10)]))
    plan = delivery.plan_batches(prepared, 5000)
    assert len(plan["batches"]) == 1
    assert plan["failed_objects"][0]["reason"] == "oversized_item"
    assert plan["failed_objects"][0]["object_id"] == "c::D::H1"


def test_large_current_evidence_is_not_popped_and_history_is_first_to_go():
    raw = packet([item(1, quote_size=10000)])
    raw["recent_turns"] = [{"role": "user", "content": "old" * 1000}]
    prepared = answering.compact_packet_for_answer(raw, 2000)
    assert len(prepared["tool_observations"][0]["result"]["items"]) == 1
    assert not prepared.get("recent_turns")
    assert delivery.json_size(prepared) > 2000


def test_message_prompt_defaults_three_not_one():
    messages = answering.build_model_messages("give formulations", packet([item()]))
    assert "exactly ONE" not in messages[0]["content"]
    assert "up to three distinct samples" in messages[0]["content"]


def test_provider_capacity_mode_preserves_large_packet_without_summary_calls(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_PACKET_MAX_BYTES", "0")
    raw = packet([item(quote_size=700000)])
    original = copy.deepcopy(raw)
    prepared = answering.compact_packet_for_answer(raw)
    assert delivery.json_size(prepared) > 512 * 1024
    plan = delivery.plan_batches(prepared, 0, output_tokens=131072)
    assert not plan["requires_batching"]
    assert plan["batches"] == [prepared]
    assert not plan["failed_objects"]
    assert raw == original
    messages = answering.build_model_messages("q", prepared, _prepared=True)
    assert "x" * 700000 + "TAIL" in messages[-1]["content"]


def test_qwen_model_output_default_and_explicit_override(monkeypatch):
    for key in ("ZENMUX_MODEL", "ANTHROPIC_MODEL", "DEEPSEEK_MODEL", "QWEN_MAX_OUTPUT_TOKENS", "LLM_MAX_OUTPUT_TOKENS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_MODEL", "qwen3.8-max")
    cfg = answering.provider_config()
    assert cfg["max_output_tokens"] == 131072
    body = answering.build_provider_body(cfg, [{"role": "user", "content": "q"}], temperature=0, stream=False)
    assert body["max_tokens"] == 131072
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "8192")
    assert answering.provider_config()["max_output_tokens"] == 8192


def test_router_uses_same_provider_output_limit(monkeypatch):
    import routing
    captured = []
    monkeypatch.setattr(routing, "provider_config", lambda: {
        "api_key": "test", "base_url": "https://example.invalid", "model": "qwen3.8-max",
        "protocol": "openai", "max_output_tokens": 131072, "enable_thinking": False,
    })
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return b'{"choices":[{"message":{"content":"Hello","tool_calls":[]}}]}'
    def opened(request, **kwargs):
        captured.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr(routing.urllib.request, "urlopen", opened)
    monkeypatch.setattr(routing, "sanitize_tool_routing", lambda decision, *a, **k: decision)
    routing.route_tools_with_qwen("Hello")
    assert captured[0]["max_tokens"] == 131072


def test_retry_keeps_identical_evidence_budget(monkeypatch):
    calls = []
    monkeypatch.setenv("LLM_MODEL_PACKET_RETRY_MAX_BYTES", "100")
    monkeypatch.setattr(answering, "_request_complete_text", lambda messages, cfg, **kw: calls.append(messages) or "ok")
    raw = packet([item(quote_size=3000)])
    answering.retry_compact_answer("q", raw, {"max_output_tokens": 100})
    assert "TAIL" in calls[0][-1]["content"]
    assert "extra_source_field" in calls[0][-1]["content"]


@pytest.mark.parametrize("failure", ["exception", "timeout", "empty"])
@pytest.mark.parametrize("retry_ok", [True, False])
def test_nonbatch_failure_receipt_preserves_submission_and_outcome(monkeypatch, failure, retry_ok):
    monkeypatch.setattr(answering, "provider_config", lambda: {
        "api_key": "test", "base_url": "https://example.invalid", "model": "test", "protocol": "openai",
        "context_window_tokens": 1000000, "max_output_tokens": 1024, "enable_thinking": False,
    })
    calls = []
    def complete(messages, cfg, **kw):
        calls.append(copy.deepcopy(messages))
        if len(calls) == 1 or not retry_ok:
            if failure == "empty":
                return ""
            if failure == "timeout":
                raise TimeoutError("synthetic_timeout")
            raise http.client.IncompleteRead(b"synthetic")
        return selection_for(decode_message(messages))
    monkeypatch.setattr(answering, "_request_complete_text", complete)
    missing = item(2)
    missing["unresolved"] = {"evidence_ids": ["MISSING"]}
    raw = packet([item(), missing])
    events = list(answering.stream_qwen("give formulations", raw))
    receipt = events[-1]["evidence_delivery"]
    assert len(calls) == 2
    assert decode_message(calls[0]) == decode_message(calls[1])
    assert "TAIL" in str(decode_message(calls[1]))
    assert receipt["attempts"] == 2
    assert receipt["submitted_objects"] == ["c::D::H1"]
    assert receipt["answer_outcome"] == ("model_complete" if retry_ok else "selection_fallback")
    assert receipt["validation_failures"]
    assert receipt["elapsed_seconds"] >= 0
    assert "c::D::H2" in "".join(event.get("content", "") for event in events)


def test_dns_stall_obeys_deadline_without_late_connect(monkeypatch):
    finished = threading.Event()
    def slow_dns(*args):
        time.sleep(.15)
        finished.set()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 9))]
    monkeypatch.setattr(answering.socket, "getaddrinfo", slow_dns)
    monkeypatch.setattr(answering.socket, "socket", lambda *a, **kw: pytest.fail("late connect after DNS deadline"))
    request = answering.urllib.request.Request("http://127.0.0.1:9")
    start = time.monotonic()
    with pytest.raises((TimeoutError, answering.urllib.error.URLError)):
        with answering._open_provider_response(request, start + .04):
            pytest.fail("DNS should time out")
    assert time.monotonic() - start < .13
    assert finished.wait(.5)


def test_under_budget_uses_no_batch_model_call(monkeypatch):
    monkeypatch.setattr(answering, "_request_complete_text", lambda *a, **kw: pytest.fail("unexpected batch call"))
    planned = delivery.plan_batches(answering.compact_packet_for_answer(packet([item()])), 512 * 1024)
    assert len(planned["batches"]) == 1
    assert not planned["requires_batching"]


def decode_message(messages):
    content = next(m["content"] for m in reversed(messages) if "Dialogue Memory Packet:\n" in m["content"])
    return json.loads(content.split("Dialogue Memory Packet:\n", 1)[1].split("\n\nCurrent user message:", 1)[0])


def selection_for(batch):
    return json.dumps({"selected_samples": [{"sample_handle": handle, "claims": [
        {"text": "Source-supported sample record", "facts_refs": row["facts_refs"], "evidence_refs": row["evidence_refs"]}]}
        for handle, row in list(batch["answer_selection_catalog"].items())[:3]], "coverage_note": "Subset shown."})


def summary_for(batch):
    return json.dumps({"summaries": [{"object_id": i["object_id"], "summary": "verified source data",
        "facts_refs": i["facts_refs"], "evidence_refs": i["evidence_refs"]}
        for i in delivery.expanded_items(batch)]})


def test_batch_retry_preserves_input_and_final_receipt(monkeypatch):
    raw = packet([item(1, quote_size=2000), item(2, quote_size=2000, collection="other")])
    prepared = answering.compact_packet_for_answer(raw)
    plan = delivery.plan_batches(prepared, 5000)
    assert len(plan["batches"]) == 2
    calls = []
    def complete(messages, cfg, **kw):
        calls.append(copy.deepcopy(messages))
        if len(calls) == 1:
            raise TimeoutError("synthetic timeout")
        batch = decode_message(messages)
        return selection_for(batch) if "answer_selection_catalog" in batch else summary_for(batch)
    monkeypatch.setattr(answering, "_request_complete_text", complete)
    events = list(answering._stream_batched_answer("q", prepared, plan, {"model": "test", "protocol": "openai"}))
    assert calls[0] == calls[1]
    assert len(calls) == 4  # failed batch attempt, retry, next batch, synthesis
    receipt = events[-1]["evidence_delivery"]
    assert set(receipt["covered_objects"]) == {"c::D::H1", "other::D::H2"}
    assert not receipt["failed_objects"]
    assert len([e for e in events if e["type"] == "progress"]) == 2


def test_batch_partial_failure_does_not_erase_success(monkeypatch):
    prepared = answering.compact_packet_for_answer(packet([item(1, quote_size=2000), item(2, quote_size=2000, collection="other")]))
    plan = delivery.plan_batches(prepared, 5000)
    def complete(messages, cfg, **kw):
        batch = decode_message(messages)
        items = list(delivery.expanded_items(batch))
        if items and items[0]["object_id"].startswith("other"):
            raise TimeoutError("synthetic")
        return summary_for(batch) if items else selection_for(batch)
    monkeypatch.setattr(answering, "_request_complete_text", complete)
    events = list(answering._stream_batched_answer("q", prepared, plan, {"model": "test"}))
    receipt = events[-1]["evidence_delivery"]
    assert receipt["covered_objects"] == ["c::D::H1"]
    assert receipt["failed_objects"][0]["object_id"] == "other::D::H2"
    assert receipt["batches"][1]["attempts"] == 2
    assert "other::D::H2" in events[-2]["content"]


def test_batch_deadline_never_renders_unverified_summary_prose(monkeypatch):
    prepared = answering.compact_packet_for_answer(packet([item()]))
    plan = delivery.plan_batches(prepared, 512 * 1024)
    clock = [0.0]
    monkeypatch.setattr(answering.time, "monotonic", lambda: clock[0])
    def complete(messages, cfg, **kw):
        raw = json.loads(summary_for(decode_message(messages)))
        raw["summaries"][0]["summary"] = "UNSUPPORTED_QUANTITATIVE_CLAIM"
        clock[0] = 601
        return json.dumps(raw)
    monkeypatch.setattr(answering, "_request_complete_text", complete)
    events = list(answering._stream_batched_answer("q", prepared, plan, {"model": "test"}))
    assert "UNSUPPORTED_QUANTITATIVE_CLAIM" not in events[-2]["content"]
    assert events[-1]["evidence_delivery"]["answer_outcome"] == "selection_fallback"
    assert events[-1]["evidence_delivery"]["covered_objects"] == ["c::D::H1"]


def test_batch_deadline_keeps_completed_and_reports_remaining(monkeypatch):
    prepared = answering.compact_packet_for_answer(packet([item(1, quote_size=2000), item(2, quote_size=2000, collection="other")]))
    plan = delivery.plan_batches(prepared, 5000)
    clock = [0.0]
    monkeypatch.setattr(answering.time, "monotonic", lambda: clock[0])
    def complete(messages, cfg, **kw):
        clock[0] = 601
        return summary_for(decode_message(messages))
    monkeypatch.setattr(answering, "_request_complete_text", complete)
    events = list(answering._stream_batched_answer("q", prepared, plan, {"model": "test"}))
    receipt = events[-1]["evidence_delivery"]
    assert receipt["batches"][1]["attempts"] == 0
    assert receipt["failed_objects"][0]["reason"] == "batch_deadline"
    assert receipt["covered_objects"] == ["c::D::H1"]


def test_summary_rejects_unbound_object_and_evidence():
    prepared = answering.compact_packet_for_answer(packet([item()]))
    payload = json.loads(summary_for(prepared))
    payload["summaries"][0]["evidence_refs"] = ["invented"]
    with pytest.raises(ValueError, match="reference_mismatch"):
        answering._validate_batch_summary(json.dumps(payload), prepared)


def test_short_batch_handles_roundtrip_without_changing_source_ids():
    prepared = answering.compact_packet_for_answer(packet([item(), item(2)]))
    original = copy.deepcopy(prepared)
    transport, aliases = delivery.batch_transport(prepared, "b7")
    assert prepared == original
    for kind in ("facts", "evidence"):
        for short, ref in aliases[kind].items():
            assert transport["source_records"][kind][short] == original["source_records"][kind][ref]
    summaries = answering._validate_batch_summary(summary_for(transport), prepared, aliases)
    for row, source in zip(summaries, delivery.expanded_items(prepared)):
        assert row["facts_refs"] == source["facts_refs"]
        assert row["evidence_refs"] == source["evidence_refs"]
        assert row["reference_aliases"]


def test_overbudget_batches_reserve_output_space_and_keep_every_object():
    prepared = answering.compact_packet_for_answer(packet([item(i, collection=str(i)) for i in range(8)]))
    budget = delivery.json_size(prepared) - 1
    plan = delivery.plan_batches(prepared, budget, output_tokens=1100)
    assert plan["requires_batching"]
    assert not plan["failed_objects"]
    assert len(plan["batches"]) == 8
    assert sum(len(list(delivery.expanded_items(b))) for b in plan["batches"]) == 8
    assert all(delivery.estimated_summary_tokens(b) <= 1100 for b in plan["batches"])
    assert all(delivery.json_size(delivery.batch_transport(b)[0]) <= budget for b in plan["batches"])


def test_output_oversize_never_splits_one_sample():
    prepared = answering.compact_packet_for_answer(packet([item(1, sample="A"), item(2, sample="A"), item(3, collection="other")]))
    plan = delivery.plan_batches(prepared, delivery.json_size(prepared) - 1, output_tokens=1100)
    assert {row["object_id"] for row in plan["failed_objects"]} == {"c::D::H1", "c::D::H2"}
    assert all(row["budget_axis"] == "summary_output_tokens" for row in plan["failed_objects"])
    assert [i["object_id"] for b in plan["batches"] for i in delivery.expanded_items(b)] == ["other::D::H3"]


def test_buffered_uses_same_delivery_and_does_not_store_progress(monkeypatch):
    monkeypatch.setattr(answering, "stream_qwen", lambda *a: iter([
        {"type": "progress", "content": "not an answer"},
        {"type": "delta", "content": "answer", "provider": "test", "model": "test"},
        {"type": "done", "evidence_delivery": {"covered_objects": ["x"]}},
    ]))
    result = answering.call_qwen("q", {})
    assert result["answer"] == "answer"
    assert result["evidence_delivery"]["covered_objects"] == ["x"]


def test_derived_counts_are_planned_not_added_after_budget():
    raw = packet([item()])
    raw["tool_observations"].append({"tool": "kg.sql_aggregate", "status": "ok", "result": {
        "status": "ok", "count_unit": "patent", "query_interpretation": {"intent": "distinct_count", "target": "doc_id"},
        "summary": {"total_count": 37, "distinct_count": 37}, "items": []}})
    prepared = answering.compact_packet_for_answer(raw)
    assert "37" in str(prepared["authoritative_count_statements"])
    rendered = decode_message(answering.build_model_messages("q", prepared, _prepared=True))
    assert delivery.json_size(rendered) == delivery.json_size(prepared)


def test_synthesis_keeps_current_aggregate_and_scope(monkeypatch):
    raw = packet([item(1, quote_size=2000), item(2, quote_size=2000, collection="other")])
    for row in raw["tool_observations"][0]["result"]["items"]:
        row["doc_id"] = "WO2021204975A9"
    raw["active_scope"] = {"policy": "hard", "doc_ids": ["WO2021204975A9"]}
    raw["tool_observations"].append({"tool": "kg.sql_aggregate", "status": "ok", "result": {
        "status": "ok", "count_unit": "patent", "query_interpretation": {"intent": "distinct_count", "target": "doc_id"},
        "summary": {"total_count": 37, "distinct_count": 37}, "items": []}})
    prepared = answering.compact_packet_for_answer(raw)
    plan = delivery.plan_batches(prepared, 5000)
    finals = []
    def complete(messages, cfg, **kw):
        batch = decode_message(messages)
        if "evidence_batch_summaries" in batch:
            finals.append(batch)
            return selection_for(batch)
        return summary_for(batch)
    monkeypatch.setattr(answering, "_request_complete_text", complete)
    list(answering._stream_batched_answer("q", prepared, plan, {"model": "test"}))
    assert finals[0]["active_scope"]["doc_ids"] == ["WO2021204975A9"]
    assert "37" in str(finals[0]["authoritative_count_statements"])


def test_transport_incomplete_read_retries_batch_and_keeps_receipt(monkeypatch):
    prepared = answering.compact_packet_for_answer(packet([item()]))
    plan = delivery.plan_batches(prepared, 5000)
    count = [0]
    def complete(*args, **kwargs):
        count[0] += 1
        raise http.client.IncompleteRead(b"interrupted")
    monkeypatch.setattr(answering, "_request_complete_text", complete)
    events = list(answering._stream_batched_answer("q", prepared, plan, {"model": "test"}))
    assert count[0] == 2
    assert len(events[-1]["evidence_delivery"]["failed_objects"]) == 1


def test_summary_cannot_silently_skip_fact_references():
    prepared = answering.compact_packet_for_answer(packet([item()]))
    payload = json.loads(summary_for(prepared))
    payload["summaries"][0]["facts_refs"] = []
    with pytest.raises(ValueError, match="incomplete_reference_coverage"):
        answering._validate_batch_summary(json.dumps(payload), prepared)


def test_invalid_batch_gets_feedback_without_losing_original_input(monkeypatch):
    prepared = answering.compact_packet_for_answer(packet([item()]))
    original = copy.deepcopy(prepared)
    plan = delivery.plan_batches(prepared, 5000)
    transport, _ = delivery.batch_transport(plan['batches'][0], 'b0')
    calls = []
    def complete(messages, cfg, **kwargs):
        calls.append(copy.deepcopy(messages))
        if len(calls) == 1:
            return '{"summaries":[]}'
        return summary_for(transport)
    monkeypatch.setattr(answering, '_request_complete_text', complete)
    monkeypatch.setattr(answering, '_selected_evidence_answer', lambda *a, **kw: ('verified', {}))
    events = list(answering._stream_batched_answer('q', prepared, plan, {'model': 'test'}))
    assert len(calls) == 2
    assert calls[1][:-1] == calls[0]
    assert 'validation failed' in calls[1][-1]['content']
    assert 'summary_' in calls[1][-1]['content']
    assert prepared == original
    assert events[-1]['evidence_delivery']['covered_objects'] == ['c::D::H1']


class Response:
    def __init__(self, rows):
        self.rows = rows
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def __iter__(self):
        return iter(self.rows)


@pytest.mark.parametrize("termination", [b"", b'data: {"choices":[{"finish_reason":"length"}]}\n'])
def test_incomplete_provider_termination_is_not_success(monkeypatch, termination):
    rows = [b'data: {"choices":[{"delta":{"content":"fragment"}}]}\n']
    if termination:
        rows.append(termination)
    monkeypatch.setattr(answering, "_open_provider_response", lambda *a, **kw: Response(rows))
    with pytest.raises((http.client.IncompleteRead, ValueError)):
        answering._request_complete_text([], {"model": "test", "base_url": "https://example.invalid", "max_output_tokens": 100})


def test_fallback_groups_same_sample_and_defaults_three():
    raw = packet([item(n, sample="A" if n < 3 else f"S{n}") for n in range(1, 7)])
    text = answering.structured_tool_fallback_answer(raw)
    assert "D / A" in text and "D / S4" in text
    assert "D / S5" not in text and "D / S6" not in text
    one = answering.structured_tool_fallback_answer(raw, "给我一个配方")
    assert "D / A" in one and "D / S3" not in one


def test_wrapper_provenance_differences_keep_raw_and_each_binding():
    first, second = item(1), item(2)
    for index, obj in enumerate((first, second)):
        original = copy.deepcopy(obj["facts"][0])
        obj["facts"][0].update(raw=original, match_mode="fact_ids" if not index else "source_hyperedge_id",
            provenance={"source_id": "source", "line": index + 1}, origins=[{"line": index + 1}])
    prepared = delivery.pack_evidence(packet([first, second]))
    assert not prepared["evidence_delivery"]["failed_objects"]
    assert len(prepared["source_records"]["facts"]) == 1
    assert list(prepared["source_records"]["facts"].values())[0]["value"] == 0
    assert [i["facts_bindings"][0]["provenance"]["line"] for i in delivery.expanded_items(prepared)] == [1, 2]


def test_header_deadline_interrupts_slow_local_http_headers():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    stop = threading.Event()
    def slow_server():
        with server.accept()[0] as conn:
            conn.recv(65536)
            try:
                for byte in b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}":
                    if stop.wait(.015):
                        break
                    conn.sendall(bytes([byte]))
            except OSError:
                pass
    worker = threading.Thread(target=slow_server, daemon=True)
    worker.start()
    start = time.monotonic()
    try:
        request = answering.urllib.request.Request(f"http://127.0.0.1:{server.getsockname()[1]}/")
        with pytest.raises((OSError, http.client.HTTPException)):
            with answering._open_provider_response(request, start + .08):
                pytest.fail("header deadline did not interrupt")
        assert time.monotonic() - start < .4
    finally:
        stop.set()
        server.close()
        worker.join(2)
    assert not worker.is_alive()
