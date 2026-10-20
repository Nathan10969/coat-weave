"""Track A regression tests: the model-facing packet is a strict allowlist.

Counterexamples from the 2026-08-16 review (REQUEST CHANGES) that must stay
blocked: DEMO_SUMMARY_MARKER, MISSING_STATUS_MARKER, FUTURE_MEMORY_MARKER,
MEMORY_ONLY_PATH_MARKER. Keep them covered so no later change reopens the
demo-seed leak into LLM messages.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "kg_tools"))
sys.path.insert(0, str(ROOT))

import answering  # noqa: E402

DEMO_SEED_IDS = {
    "mem_project_constraint_single_chat",
    "mem_workflow_agentmemory_borrow",
    "mem_user_preference_chinese",
    "mem_provider_openai_compatible_env",
}


def make_audit_packet() -> dict:
    return {
        "session_id": "s1",
        "built_at": "2026-08-16T00:00:00Z",
        "session_summary": {
            "goal": "DEMO_SUMMARY_MARKER 本地对话记忆 demo",
            "decisions": ["单对话框 DEMO_SUMMARY_MARKER"],
        },
        "project_state": [{"id": "p1", "content": "PLATFORM_CONSTRAINT_MARKER", "status": "active"}],
        "user_preferences": [{"id": "u1", "content": "SUPER_PREF_MARKER", "status": "superseded"}],
        "workflow_rules": [{"id": "w1", "content": "MISSING_STATUS_MARKER"}],
        "active_decisions": [],
        "known_issues": [],
        "relevant_memories": [
            {"id": "m1", "content": "ACTIVE_MEM_MARKER", "status": "active"},
            {"id": "m2", "content": "MISSING_STATUS_MARKER"},
            {"id": "m3", "content": "SUPER_MEM_MARKER", "status": "superseded"},
        ],
        "stale_or_superseded": [{"id": "m3"}],
        "future_memories": [{"id": "f1", "content": "FUTURE_MEMORY_MARKER", "status": "active"}],
        "memory_graph_recall": {
            "query": "q",
            "recall_method": "r",
            "matched_nodes": [
                {"id": "n1", "name": "NODE_OBS_A", "source_kinds": ["observation"]},
                {"id": "n2", "name": "NODE_OBS_B", "source_kinds": ["observation"]},
                {"id": "n3", "name": "客户 demo", "source_kinds": ["memory"]},
            ],
            "paths": [
                {"source_name": "NODE_OBS_A", "target_name": "NODE_OBS_B", "relation": "r1", "source_kinds": ["observation"]},
                {"source_name": "NODE_OBS_A", "target_name": "NODE_OBS_B", "relation": "MEMORY_ONLY_PATH_MARKER", "source_kinds": ["memory"]},
                {"source_name": "客户 demo", "target_name": "NODE_OBS_A", "relation": "r3", "source_kinds": ["memory"]},
            ],
        },
        "recent_turns": [],
        "tool_routing_decisions": [],
        "tool_observations": [],
        "recent_tool_observations": [],
        "active_scope": None,
        "scope_history": [],
        "last_scope_resolution": None,
        "recent_observations": [],
    }


def test_four_review_markers_all_blocked() -> None:
    packet = make_audit_packet()
    content = answering.build_model_messages("环氧防腐涂料配方", packet)[-1]["content"]
    for marker in ("DEMO_SUMMARY_MARKER", "MISSING_STATUS_MARKER", "FUTURE_MEMORY_MARKER", "MEMORY_ONLY_PATH_MARKER"):
        assert marker not in content, f"LEAKED: {marker}"
    for good in ("ACTIVE_MEM_MARKER", "PLATFORM_CONSTRAINT_MARKER", "NODE_OBS_A"):
        assert good in content, f"MISSING: {good}"


def test_model_packet_is_strict_allowlist() -> None:
    projected = answering.project_model_packet(make_audit_packet())
    allowed = set(answering.MODEL_PACKET_CONTEXT_SECTIONS) | set(answering.MODEL_PACKET_MEMORY_SECTIONS) | {
        "relevant_memories",
        "memory_graph_recall",
    }
    assert set(projected) <= allowed, f"unknown sections leaked: {set(projected) - allowed}"
    for audit_only in ("session_id", "built_at", "session_summary", "stale_or_superseded", "future_memories"):
        assert audit_only not in projected, f"{audit_only} must not reach the model"
    assert projected["user_preferences"] == [], "superseded preference leaked"
    assert projected["workflow_rules"] == [], "missing-status record must fail closed"
    assert projected["memory_graph_recall"]["paths"] == [
        {"source_name": "NODE_OBS_A", "target_name": "NODE_OBS_B", "relation": "r1", "source_kinds": ["observation"]}
    ]


def test_audit_packet_is_not_mutated() -> None:
    packet = make_audit_packet()
    answering.build_model_messages("问题", packet)
    assert "stale_or_superseded" in packet
    assert "session_summary" in packet
    assert "future_memories" in packet
    assert len(packet["relevant_memories"]) == 3
    assert len(packet["memory_graph_recall"]["paths"]) == 3
    assert packet["memory_graph_recall"]["matched_nodes"][2]["name"] == "客户 demo"


def test_graph_recall_paths_carry_edge_source_kinds() -> None:
    import demo_storage
    import graph_store

    original_nodes, original_edges = graph_store.GRAPH_NODES, graph_store.GRAPH_EDGES
    try:
        with tempfile.TemporaryDirectory() as tmp:
            graph_store.GRAPH_NODES = Path(tmp) / "graph_nodes.jsonl"
            graph_store.GRAPH_EDGES = Path(tmp) / "graph_edges.jsonl"
            nodes = [
                {"id": "gn_A", "type": "concept", "name": "A", "aliases": [], "source_ids": ["o1"], "source_kinds": ["observation"]},
                {"id": "gn_B", "type": "concept", "name": "B", "aliases": [], "source_ids": ["m1"], "source_kinds": ["memory"]},
            ]
            edges = [
                {"id": "ge1", "source": "gn_A", "target": "gn_B", "source_name": "A", "target_name": "B",
                 "relation": "relates", "weight": 0.9, "reason": "r", "source_ids": ["m1"], "source_kinds": ["memory"]},
            ]
            demo_storage.write_jsonl(graph_store.GRAPH_NODES, nodes)
            demo_storage.write_jsonl(graph_store.GRAPH_EDGES, edges)

            recall = graph_store.graph_recall("A")
            assert recall["paths"][0]["source_kinds"] == ["memory"], "edge source_kinds must survive into paths"

            projected = answering.project_model_packet({"memory_graph_recall": recall})
            assert projected["memory_graph_recall"]["paths"] == [], "memory-only edge leaked through projection"
            assert [node["name"] for node in projected["memory_graph_recall"]["matched_nodes"]] == ["A"]
    finally:
        graph_store.GRAPH_NODES = original_nodes
        graph_store.GRAPH_EDGES = original_edges


def test_extract_graph_records_produce_no_demo_ontology() -> None:
    """A1.1: demo-era canonical rules and keyword special-cases are gone."""
    import graph_store

    source = {
        "id": "src_zh",
        "title": "用户偏好",
        "content": "请用中文回答，并且说明单对话框、可视化图谱和 agentmemory 的使用方式",
        "summary": "本地对话记忆 demo 相关讨论",
    }
    nodes, edges = graph_store.extract_graph_records(source, "observation")
    demo_names = {"客户 demo", "单对话框", "中文解释", "agentmemory轻量骨架", "可视化图谱", "完整 MCP/runtime"}
    node_names = {node["name"] for node in nodes}
    assert not (node_names & demo_names), f"demo ontology regenerated: {node_names & demo_names}"
    edge_names = {e["source_name"] for e in edges} | {e["target_name"] for e in edges}
    assert not (edge_names & demo_names), f"demo edges regenerated: {edge_names & demo_names}"


def test_graph_recall_has_no_demo_fallback() -> None:
    """A1.1: an empty-recall question must not revive 客户 demo nodes from the graph files."""
    import demo_storage
    import graph_store

    original_nodes, original_edges = graph_store.GRAPH_NODES, graph_store.GRAPH_EDGES
    try:
        with tempfile.TemporaryDirectory() as tmp:
            graph_store.GRAPH_NODES = Path(tmp) / "graph_nodes.jsonl"
            graph_store.GRAPH_EDGES = Path(tmp) / "graph_edges.jsonl"
            demo_storage.write_jsonl(graph_store.GRAPH_NODES, [
                {"id": "gn_x", "type": "project", "name": "客户 demo", "aliases": [], "source_ids": ["s1"], "source_kinds": ["observation"]},
            ])
            demo_storage.write_jsonl(graph_store.GRAPH_EDGES, [])
            recall = graph_store.graph_recall("完全不相关的提问")
            assert recall["matched_nodes"] == [], f"demo fallback revived nodes: {recall['matched_nodes']}"
            assert recall["paths"] == [], f"demo fallback revived paths: {recall['paths']}"
    finally:
        graph_store.GRAPH_NODES = original_nodes
        graph_store.GRAPH_EDGES = original_edges


def test_mock_answer_uses_same_projection() -> None:
    out = answering.local_mock_answer("环氧防腐涂料配方", make_audit_packet())
    for marker in ("DEMO_SUMMARY_MARKER", "SUPER_MEM_MARKER", "MISSING_STATUS_MARKER", "客户 demo"):
        assert marker not in out, f"LEAKED: {marker}"
    assert "ACTIVE_MEM_MARKER" in out


def test_fresh_workspace_initializes_without_demo_seeds() -> None:
    from api.orchestrator import LegacyRuntime

    import demo_config
    import demo_storage
    import graph_store
    import memory_core
    import routing
    import scope_state
    import tool_runtime

    runtime = object.__new__(LegacyRuntime)
    runtime.demo_config = demo_config
    runtime.demo_storage = demo_storage
    runtime.memory_core = memory_core
    runtime.answering = answering
    runtime.modules = [demo_config, answering, graph_store, memory_core, scope_state, routing, tool_runtime]
    runtime._lock = threading.RLock()

    with tempfile.TemporaryDirectory() as tmp:
        with runtime.patched_workspace(Path(tmp), "fresh_c1"):
            pass
        rows = demo_storage.read_jsonl(Path(tmp) / "durable_memories.jsonl")
        ids = {row.get("id") for row in rows}
        assert not (DEMO_SEED_IDS & ids), f"demo seeds injected: {DEMO_SEED_IDS & ids}"
        assert "mem_project_constraint_platform_api" in ids
        assert all(row.get("status") == "active" for row in rows), "non-active record in fresh workspace"
