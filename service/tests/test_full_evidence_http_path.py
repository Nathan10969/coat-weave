"""Local HTTP integration with synthetic KG, never the production database."""
from __future__ import annotations

import copy
from contextlib import nullcontext
from http.server import ThreadingHTTPServer
import json
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import answering
from test_full_evidence_expand import fixture, expand, service


def test_full_http_response_to_model_preserves_direct_reference_sets(monkeypatch):
    store, db, raw, quote = fixture()
    monkeypatch.setattr(service, "AUTH_TOKEN", "")
    monkeypatch.setattr(service, "get_store", lambda: store)
    monkeypatch.setattr(service, "resolve_kg_sources", lambda: ((), ()))
    monkeypatch.setattr(expand, "load_pg_env", lambda *args: {})
    monkeypatch.setattr(expand, "pg_connect", lambda *args: nullcontext(None))
    monkeypatch.setattr(expand, "fetch_db_hyperedges", lambda *args, **kw: db)
    server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/tools/kg.expand_hyperedge_multihop",
            data=json.dumps({"object_ids": list(db), "evidence_mode": "full",
                             "max_context_facts": 1, "max_evidence_per_item": 1}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    assert not thread.is_alive()
    packet = {"tool_observations": [{"tool": "kg.expand_hyperedge_multihop", "status": "ok", "result": payload}]}
    before = copy.deepcopy(packet)
    messages = answering.build_model_messages("give related formulations", packet)
    rendered = json.loads(messages[1]["content"].split("Dialogue Memory Packet:\n")[1].split("\n\nCurrent user message:")[0])
    item = rendered["tool_observations"][0]["result"]["items"][0]
    assert item["hyperedge_raw"] == raw
    assert len(item["facts_refs"]) == 25
    assert len(item["evidence_refs"]) == 7
    facts = rendered["source_records"]["facts"]
    evidence = rendered["source_records"]["evidence"]
    assert {facts[ref]["fact_id"] for ref in item["facts_refs"]} == set(raw["fact_ids"])
    assert {evidence[ref]["evidence_id"] for ref in item["evidence_refs"]} == set(raw["evidence_ids"])
    assert all(evidence[ref]["quote"] == quote for ref in item["evidence_refs"])
    assert all(facts[ref]["nested"]["quote"] == quote for ref in item["facts_refs"])
    assert packet == before
