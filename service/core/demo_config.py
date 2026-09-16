from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
STATIC = ROOT / "static"
DATA = ROOT / "data"
SESSION_ID = "demo_session"

RAW_TURNS = DATA / "raw_turns.jsonl"
OBSERVATIONS = DATA / "observations.jsonl"
DURABLE = DATA / "durable_memories.jsonl"
SUMMARY = DATA / "session_summary.json"
PACKET = DATA / "latest_packet.json"
GRAPH_NODES = DATA / "graph_nodes.jsonl"
GRAPH_EDGES = DATA / "graph_edges.jsonl"
TOOL_OBSERVATIONS = DATA / "tool_observations.jsonl"
TOOL_ROUTING = DATA / "tool_routing_decisions.jsonl"
SCOPE_STATE = DATA / "scope_state.json"


def load_env_file(path: Path, *, override: bool = False) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value


load_env_file(PROJECT_ROOT / "coating_kg" / ".env")
load_env_file(ROOT / ".env", override=True)

DEFAULT_PORT = int(os.environ.get("MEMORY_DEMO_PORT", "8788"))
KG_EXPAND_URL = os.environ.get(
    "KG_EXPAND_URL",
    "http://127.0.0.1:18021/tools/kg.expand_hyperedge_multihop",
)
KG_EXPAND_TOKEN = os.environ.get("KG_EXPAND_TOKEN", "").strip()
KG_EXPAND_TIMEOUT_SECONDS = int(os.environ.get("KG_EXPAND_TIMEOUT_SECONDS", "60"))
KG_HYBRID_SEARCH_URL = os.environ.get(
    "KG_HYBRID_SEARCH_URL",
    "http://127.0.0.1:18021/tools/kg.hybrid_search",
)
KG_HYBRID_SEARCH_TIMEOUT_SECONDS = int(os.environ.get("KG_HYBRID_SEARCH_TIMEOUT_SECONDS", "60"))
KG_HYBRID_DEFAULT_TOP_K = int(os.environ.get("KG_HYBRID_TOP_K", "20"))
KG_HYBRID_DEFAULT_CANDIDATE_K = int(os.environ.get("KG_HYBRID_CANDIDATE_K", "100"))
KG_SQL_AGGREGATE_URL = os.environ.get(
    "KG_SQL_AGGREGATE_URL",
    "http://127.0.0.1:18021/tools/kg.sql_aggregate",
)
KG_SQL_AGGREGATE_TIMEOUT_SECONDS = int(os.environ.get("KG_SQL_AGGREGATE_TIMEOUT_SECONDS", "60"))
KG_DOC_FIELD_SCAN_URL = os.environ.get(
    "KG_DOC_FIELD_SCAN_URL",
    "http://127.0.0.1:18021/tools/kg.doc_field_scan",
)
KG_DOC_FIELD_SCAN_TIMEOUT_SECONDS = int(os.environ.get("KG_DOC_FIELD_SCAN_TIMEOUT_SECONDS", "60"))
