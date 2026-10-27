"""Pi exports use the existing Python schemas and actual expansion HTTP handler."""
from __future__ import annotations

from contextlib import nullcontext
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

SERVICE = Path(__file__).resolve().parents[1]
PI = SERVICE / "pi-harness"
sys.path.insert(0, str(SERVICE / "core"))

from kg_contract import load_tool_contract, openai_tool_parameters, retrieval_policy
from test_full_evidence_expand import fixture, expand, service


def test_pi_catalog_is_generated_from_the_shared_contract():
    spec = importlib.util.spec_from_file_location("pi_kg_export", PI / "scripts/export-kg-tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    catalog = module.catalog()
    assert json.loads((PI / "src/kg-tools-schema.json").read_text(encoding="utf-8")) == catalog
    assert catalog["retrieval_policy"] == retrieval_policy()
    assert {tool["endpoint"] for tool in catalog["tools"]} == set(load_tool_contract()["tools"])
    for tool in catalog["tools"]:
        assert tool["parameters"] == openai_tool_parameters(tool["endpoint"])


def test_pi_client_reuses_the_real_full_expand_http_handler(monkeypatch):
    store, db, raw, quote = fixture()
    monkeypatch.setattr(service, "AUTH_TOKEN", "synthetic-pi-kg-token")
    monkeypatch.setattr(service, "get_store", lambda: store)
    monkeypatch.setattr(service, "resolve_kg_sources", lambda: ((), ()))
    monkeypatch.setattr(expand, "load_pg_env", lambda *args: {})
    monkeypatch.setattr(expand, "pg_connect", lambda *args: nullcontext(None))
    monkeypatch.setattr(expand, "fetch_db_hyperedges", lambda *args, **kw: db)
    server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code = """
import { queryKgTool, loadKgConfig } from './src/kg-tools.ts';
const config = loadKgConfig({PI_KG_TOOLS_URL: process.argv[1], PI_KG_API_TOKEN: 'synthetic-pi-kg-token'});
const response = await queryKgTool('kg_expand_hyperedge_multihop', {object_ids: ['D1::H1']}, undefined, config);
process.stdout.write(JSON.stringify(response));
"""
        result = subprocess.run(
            [shutil.which("node"), "--import", "tsx", "--input-type=module", "-e", code,
             f"http://127.0.0.1:{server.server_port}"],
            cwd=PI, text=True, capture_output=True, check=True, timeout=15,
            env={key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ},
        )
        response = json.loads(result.stdout)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    assert not thread.is_alive()
    assert response["evidence_mode"] == "full"
    item = response["items"][0]
    assert item["hyperedge_raw"] == raw
    assert len(item["facts"]) == 25
    assert len(item["evidence"]) == 7
    assert all(row["quote"] == quote for row in item["evidence"])
    assert item["coverage"]["complete"] is True
