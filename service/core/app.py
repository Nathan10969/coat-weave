from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from answering import (
    build_provider_body,
    build_model_messages,
    call_qwen,
    extract_stream_delta_text,
    local_mock_answer,
    provider_config,
    provider_headers,
    provider_label,
    provider_messages_url,
    sanitize_customer_answer,
    scope_clarification_answer,
)
from demo_config import (
    DATA,
    DEFAULT_PORT,
    DURABLE,
    GRAPH_EDGES,
    GRAPH_NODES,
    KG_HYBRID_DEFAULT_CANDIDATE_K,
    KG_HYBRID_DEFAULT_TOP_K,
    OBSERVATIONS,
    PACKET,
    RAW_TURNS,
    SCOPE_STATE,
    SESSION_ID,
    STATIC,
    SUMMARY,
    TOOL_OBSERVATIONS,
    TOOL_ROUTING,
)
from demo_storage import append_jsonl, now_iso, read_json, read_jsonl, write_json, write_jsonl
from demo_text import (
    clamp_int,
    extract_doc_ids,
    extract_hyperedge_object_ids,
    int_or_default,
    normalize_doc_id,
    normalize_kg_aggregate_filters,
    normalize_kg_search_filters,
    normalize_object_ids,
    normalize_string_list,
    stable_id,
    tokenize,
)
from graph_store import (
    extract_graph_records,
    graph_edge,
    graph_node,
    graph_recall,
    rebuild_graph_index,
    text_matches_rule,
    update_graph_from_record,
    upsert_graph_records,
)
from memory_core import (
    classify_observation,
    ensure_seed_data,
    make_observation,
    maybe_promote_memory,
    relevant_memories,
    update_summary,
)
from scope_state import (
    AMBIGUOUS_COMPARISON_TERMS,
    GLOBAL_SCOPE_TERMS,
    PREVIOUS_SCOPE_TERMS,
    ambiguous_global_comparison,
    default_scope_state,
    document_local_question,
    has_any,
    latest_user_turn_id,
    load_scope_state,
    make_doc_scope,
    make_scope_resolution,
    normalize_scope_state,
    remember_scope_resolution,
    save_scope_state,
    scope_applies_to_kg_search,
)
from routing import (
    AGGREGATE_INTENTS,
    AGGREGATE_TARGETS,
    CORROSION_EXPANSION_TERMS,
    DOC_FIELD_SCAN_GROUPS,
    aggregate_context_question,
    aggregate_filters_for_question,
    aggregate_group_by_for_question,
    analyze_kg_query_semantics,
    append_unique_terms,
    apply_scope_to_route,
    available_tools_for_router,
    build_aggregate_call,
    build_kg_search_queries,
    build_kg_search_query,
    clarify_scope_route,
    coating_scope_topic,
    contains_any,
    doc_field_scan_route,
    missing_doc_scope_route,
    extract_json_object,
    fallback_tool_routing,
    infer_aggregate_intent,
    infer_aggregate_target,
    infer_plan_type,
    kg_expand_top_k_for_question,
    kg_search_only_requested,
    resolve_scope,
    route_tools,
    route_tools_with_qwen,
    sanitize_tool_routing,
    scoped_kg_search_route,
    should_use_doc_field_scan,
    should_force_scoped_kg_search,
    total_count_followup,
    unresolved_doc_local_reference,
    wants_coating_kg_search,
    wants_kg_aggregate,
    wants_web_search,
)
from tool_clients import (
    DuckDuckGoLiteParser,
    brave_web_search,
    current_weather,
    duckduckgo_web_search,
    github_recent_high_star_repos,
    infer_recent_days,
    infer_weather_location,
    kg_doc_field_scan,
    kg_expand_hyperedge_multihop,
    kg_hybrid_search,
    kg_sql_aggregate,
    wants_github_recent_repo_search,
    wants_weather,
    web_search,
)
from tool_runtime import (
    filter_search_result_by_doc_scope,
    item_doc_id,
    kg_expand_selection_score,
    maybe_run_tools,
    merge_kg_search_results,
    record_tool_observation,
    run_kg_hybrid_search_for_call,
    select_expand_object_ids,
    semantic_guard_for_expand_result,
    semantic_text_from_expand_item,
)


def build_packet(question: str) -> dict[str, Any]:
    turns = read_jsonl(RAW_TURNS)
    observations = read_jsonl(OBSERVATIONS)
    memories = read_jsonl(DURABLE)
    tool_observations = read_jsonl(TOOL_OBSERVATIONS)
    tool_routing = read_jsonl(TOOL_ROUTING)
    scope_state = load_scope_state()
    current_turn_id = latest_user_turn_id(turns)
    current_tool_observations = [
        row
        for row in tool_observations
        if current_turn_id and row.get("turn_id") == current_turn_id
    ]
    recent_tool_observations = [
        row
        for row in tool_observations
        if not (current_turn_id and row.get("turn_id") == current_turn_id)
    ][-5:]
    active = [m for m in memories if m.get("status", "active") == "active"]
    stale = [m for m in memories if m.get("status") in {"stale", "superseded"}]
    summary = read_json(SUMMARY, {})
    packet = {
        "session_id": SESSION_ID,
        "recent_turns": [
            {"role": t["role"], "content": t["content"], "turn_id": t["id"]}
            for t in turns[-10:]
        ],
        "session_summary": summary,
        "project_state": [m for m in active if m.get("type") == "project_constraint"],
        "user_preferences": [m for m in active if m.get("type") == "user_preference"],
        "workflow_rules": [m for m in active if m.get("type") == "workflow_rule"],
        "active_decisions": [m for m in active if m.get("type") == "decision"],
        "known_issues": [m for m in active if m.get("type") == "known_issue"],
        "relevant_memories": relevant_memories(question, memories),
        "memory_graph_recall": graph_recall(question),
        "tool_routing_decisions": tool_routing[-5:],
        "tool_observations": current_tool_observations,
        "recent_tool_observations": recent_tool_observations,
        "active_scope": scope_state.get("active_doc_scope"),
        "scope_history": scope_state.get("scope_history", [])[:5],
        "last_scope_resolution": scope_state.get("last_scope_resolution"),
        "recent_observations": observations[-8:],
        "stale_or_superseded": stale[-5:],
        "built_at": now_iso(),
    }
    write_json(PACKET, packet)
    return packet












def graph_state() -> dict[str, Any]:
    turns = read_jsonl(RAW_TURNS)
    observations = read_jsonl(OBSERVATIONS)
    memories = read_jsonl(DURABLE)
    tool_observations = read_jsonl(TOOL_OBSERVATIONS)
    tool_routing = read_jsonl(TOOL_ROUTING)
    graph_nodes = read_jsonl(GRAPH_NODES)
    graph_edges = read_jsonl(GRAPH_EDGES)
    summary = read_json(SUMMARY, {})
    packet = read_json(PACKET, {})
    cfg = provider_config()
    displayed_observations = observations[-12:]
    displayed_memories = memories[-16:]
    displayed_turns_by_id = {turn["id"]: turn for turn in turns[-12:]}
    for obs in displayed_observations:
        for turn_id in obs.get("source_turn_ids", []):
            if turn_id not in displayed_turns_by_id:
                source_turn = next((turn for turn in turns if turn.get("id") == turn_id), None)
                if source_turn:
                    displayed_turns_by_id[turn_id] = source_turn
    nodes: list[dict[str, Any]] = [
        {
            "data": {
                "id": "session",
                "label": "demo_session",
                "kind": "session",
                "detail": summary.get("goal", "本地 demo session"),
            }
        },
        {
            "data": {
                "id": "packet",
                "label": "Dialogue Packet",
                "kind": "packet",
                "detail": f"built_at={packet.get('built_at', 'not yet')}",
            }
        },
    ]
    edges: list[dict[str, Any]] = []
    node_ids = {"session", "packet"}

    def add_edge(edge_id: str, source: str, target: str, label: str) -> None:
        if source in node_ids and target in node_ids:
            edges.append({"data": {"id": edge_id, "source": source, "target": target, "label": label}})

    add_edge("session_packet", "session", "packet", "builds")
    for turn in displayed_turns_by_id.values():
        tid = turn["id"]
        nodes.append(
            {
                "data": {
                    "id": tid,
                    "label": f"{turn['role']}: {turn['content'][:22]}",
                    "kind": f"turn_{turn['role']}",
                    "detail": turn["content"],
                }
            }
        )
        node_ids.add(tid)
        add_edge(f"session_{tid}", "session", tid, "turn")
    for obs in displayed_observations:
        oid = obs["id"]
        nodes.append(
            {
                "data": {
                    "id": oid,
                    "label": f"{obs['type']}: {obs['title'][:22]}",
                    "kind": "observation",
                    "detail": obs["summary"],
                }
            }
        )
        node_ids.add(oid)
        add_edge(f"{oid}_packet", oid, "packet", "feeds")
        for tid in obs.get("source_turn_ids", []):
            add_edge(f"{tid}_{oid}", tid, oid, "compress")
    for mem in displayed_memories:
        mid = mem["id"]
        nodes.append(
            {
                "data": {
                    "id": mid,
                    "label": f"{mem.get('type')}: {mem.get('title', '')[:20]}",
                    "kind": f"memory_{mem.get('status', 'active')}",
                    "detail": mem.get("content", ""),
                }
            }
        )
        node_ids.add(mid)
        add_edge(f"{mid}_packet", mid, "packet", mem.get("status", "active"))
        for oid in mem.get("source_observation_ids", []):
            add_edge(f"{oid}_{mid}", oid, mid, "promote")
    for tool_obs in tool_observations[-8:]:
        tid = tool_obs["id"]
        result = tool_obs.get("result") or {}
        top_items = result.get("items") or []
        top_label = top_items[0].get("full_name") if top_items else tool_obs.get("status", "tool")
        nodes.append(
            {
                "data": {
                    "id": tid,
                    "label": f"tool: {tool_obs.get('tool')} {top_label}",
                    "kind": "tool_observation",
                    "detail": json.dumps(tool_obs, ensure_ascii=False, indent=2),
                }
            }
        )
        node_ids.add(tid)
        add_edge(f"{tid}_packet", tid, "packet", "injects")
    for route_obs in tool_routing[-5:]:
        rid = route_obs["id"]
        decision = route_obs.get("decision") or {}
        calls = decision.get("calls") or []
        label = "no tool"
        if calls:
            label = ", ".join(call.get("tool", "tool") for call in calls[:2])
        nodes.append(
            {
                "data": {
                    "id": rid,
                    "label": f"router: {label}",
                    "kind": "tool_router",
                    "detail": json.dumps(route_obs, ensure_ascii=False, indent=2),
                }
            }
        )
        node_ids.add(rid)
        add_edge(f"{rid}_packet", rid, "packet", "routes")
    displayed_graph_nodes = graph_nodes[-24:]
    for gnode in displayed_graph_nodes:
        gid = gnode["id"]
        nodes.append(
            {
                "data": {
                    "id": gid,
                    "label": f"{gnode.get('type')}: {gnode.get('name')}",
                    "kind": f"kg_{gnode.get('type', 'concept')}",
                    "detail": json.dumps(gnode, ensure_ascii=False, indent=2),
                }
            }
        )
        node_ids.add(gid)
    for gedge in graph_edges[-48:]:
        add_edge(
            gedge["id"],
            gedge.get("source", ""),
            gedge.get("target", ""),
            gedge.get("relation", "related_to"),
        )
        for source_id in gedge.get("source_ids", []):
            if source_id in node_ids:
                add_edge(f"{source_id}_{gedge['id']}", source_id, gedge.get("source", ""), "evidence")
    return {
        "nodes": nodes,
        "edges": edges,
        "summary": summary,
        "packet": packet,
        "provider": {
            "api_key_set": cfg["api_key_set"],
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "context_window_tokens": cfg["context_window_tokens"],
            "max_output_tokens": cfg["max_output_tokens"],
            "enable_thinking": cfg["enable_thinking"],
            "env_sources": cfg["env_sources"],
        },
        "counts": {
            "turns": len(turns),
            "observations": len(observations),
            "memories": len(memories),
            "tool_observations": len(tool_observations),
            "tool_routing_decisions": len(tool_routing),
            "graph_nodes": len(graph_nodes),
            "graph_edges": len(graph_edges),
        },
    }


def reset_demo() -> None:
    for path in [
        RAW_TURNS,
        OBSERVATIONS,
        DURABLE,
        SUMMARY,
        PACKET,
        GRAPH_NODES,
        GRAPH_EDGES,
        TOOL_OBSERVATIONS,
        TOOL_ROUTING,
        SCOPE_STATE,
    ]:
        if path.exists():
            path.unlink()
    ensure_seed_data()


class Handler(BaseHTTPRequestHandler):
    server_version = "DialogueMemoryDemo/0.3"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{now_iso()}] {self.address_string()} {fmt % args}")

    def send_json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def start_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def send_sse(self, event: str, payload: Any) -> None:
        raw = (
            f"event: {event}\n"
            f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        ).encode("utf-8")
        self.wfile.write(raw)
        self.wfile.flush()

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        ensure_seed_data()
        if self.path == "/" or self.path == "/index.html":
            self.serve_file(STATIC / "index.html", "text/html; charset=utf-8")
            return
        if self.path == "/api/state":
            self.send_json(graph_state())
            return
        if self.path.startswith("/static/"):
            rel = self.path.removeprefix("/static/").split("?", 1)[0]
            path = (STATIC / rel).resolve()
            if not str(path).startswith(str(STATIC.resolve())) or not path.exists():
                self.send_error(404)
                return
            mime = "text/css" if path.suffix == ".css" else "application/javascript"
            self.serve_file(path, mime)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        ensure_seed_data()
        if self.path == "/api/reset":
            reset_demo()
            self.send_json({"ok": True, "state": graph_state()})
            return
        if self.path == "/api/chat_stream":
            self.handle_chat_stream()
            return
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            body = self.read_body()
            question = str(body.get("message", "")).strip()
            if not question:
                self.send_json({"error": "message is required"}, 400)
                return
            user_turn = {
                "id": f"turn_{int(time.time() * 1000)}_user",
                "session_id": SESSION_ID,
                "role": "user",
                "content": question,
                "created_at": now_iso(),
            }
            append_jsonl(RAW_TURNS, user_turn)
            obs = make_observation(user_turn)
            append_jsonl(OBSERVATIONS, obs)
            update_graph_from_record(obs, "observation")
            promoted = maybe_promote_memory(obs)
            if promoted:
                append_jsonl(DURABLE, promoted)
                update_graph_from_record(promoted, "memory")
            maybe_run_tools(question)
            update_summary(question)
            packet = build_packet(question)
            model_result = call_qwen(question, packet)
            assistant_turn = {
                "id": f"turn_{int(time.time() * 1000)}_assistant",
                "session_id": SESSION_ID,
                "role": "assistant",
                "content": model_result["answer"],
                "created_at": now_iso(),
                "provider": model_result["provider"],
                "model": model_result["model"],
            }
            append_jsonl(RAW_TURNS, assistant_turn)
            update_summary(question, model_result["answer"])
            packet = build_packet(question)
            self.send_json(
                {
                    "answer": model_result["answer"],
                    "provider": model_result["provider"],
                    "model": model_result["model"],
                    "packet": packet,
                    "state": graph_state(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 500)

    def prepare_user_turn(self, question: str) -> dict[str, Any]:
        user_turn = {
            "id": f"turn_{int(time.time() * 1000)}_user",
            "session_id": SESSION_ID,
            "role": "user",
            "content": question,
            "created_at": now_iso(),
        }
        append_jsonl(RAW_TURNS, user_turn)
        obs = make_observation(user_turn)
        append_jsonl(OBSERVATIONS, obs)
        update_graph_from_record(obs, "observation")
        promoted = maybe_promote_memory(obs)
        if promoted:
            append_jsonl(DURABLE, promoted)
            update_graph_from_record(promoted, "memory")
        maybe_run_tools(question, turn_id=user_turn["id"])
        update_summary(question)
        return build_packet(question)

    def save_assistant_turn(self, question: str, answer: str, provider: str, model: str) -> dict[str, Any]:
        assistant_turn = {
            "id": f"turn_{int(time.time() * 1000)}_assistant",
            "session_id": SESSION_ID,
            "role": "assistant",
            "content": answer,
            "created_at": now_iso(),
            "provider": provider,
            "model": model,
        }
        append_jsonl(RAW_TURNS, assistant_turn)
        update_summary(question, answer)
        return build_packet(question)

    def handle_chat_stream(self) -> None:
        try:
            body = self.read_body()
            question = str(body.get("message", "")).strip()
            if not question:
                self.send_json({"error": "message is required"}, 400)
                return
            packet = self.prepare_user_turn(question)
            cfg = provider_config()
            clarification = scope_clarification_answer(packet)
            provider = "scope-resolver-stream" if clarification else "openai-compatible-stream"
            model = cfg["model"]
            self.start_sse()
            self.send_sse(
                "meta",
                {
                    "provider": provider if (clarification or cfg["api_key"]) else "local-mock-stream",
                    "model": model if (clarification or cfg["api_key"]) else "mock-memory-demo",
                    "context_window_tokens": cfg["context_window_tokens"],
                    "max_output_tokens": cfg["max_output_tokens"],
                },
            )

            chunks: list[str] = []
            if clarification:
                for idx in range(0, len(clarification), 24):
                    chunk = clarification[idx : idx + 24]
                    chunks.append(chunk)
                    self.send_sse("token", {"delta": chunk})
                    time.sleep(0.03)
            elif not cfg["api_key"]:
                answer = sanitize_customer_answer(local_mock_answer(question, packet))
                for idx in range(0, len(answer), 24):
                    chunk = answer[idx : idx + 24]
                    chunks.append(chunk)
                    self.send_sse("token", {"delta": chunk})
                    time.sleep(0.03)
                provider = "local-mock-stream"
                model = "mock-memory-demo"
            else:
                body_payload = build_provider_body(
                    cfg,
                    build_model_messages(question, packet),
                    temperature=0.4,
                    max_tokens=cfg["max_output_tokens"],
                    stream=True,
                )
                req = urllib.request.Request(
                    provider_messages_url(cfg),
                    data=json.dumps(body_payload).encode("utf-8"),
                    headers=provider_headers(cfg),
                    method="POST",
                )
                provider = provider_label(cfg)
                with urllib.request.urlopen(req, timeout=120) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            break
                        try:
                            payload = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        delta = extract_stream_delta_text(payload, cfg)
                        if not delta:
                            continue
                        chunks.append(delta)
                        self.send_sse("token", {"delta": delta})

            answer = sanitize_customer_answer("".join(chunks))
            packet = self.save_assistant_turn(question, answer, provider, model)
            self.send_sse(
                "done",
                {
                    "answer": answer,
                    "provider": provider,
                    "model": model,
                    "packet": packet,
                    "state": graph_state(),
                },
            )
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001
            try:
                self.send_sse("error", {"error": str(exc)})
                self.close_connection = True
            except Exception:
                self.send_json({"error": str(exc)}, 500)

    def serve_file(self, path: Path, content_type: str) -> None:
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    ensure_seed_data()
    port = DEFAULT_PORT
    cfg = provider_config()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Dialogue memory demo running at http://127.0.0.1:{port}/")
    print(f"Data directory: {DATA}")
    print(
        "OpenAI-compatible provider: "
        f"key_set={cfg['api_key_set']} model={cfg['model']} "
        f"context={cfg['context_window_tokens']} max_output={cfg['max_output_tokens']} "
        f"thinking={cfg['enable_thinking']} "
        f"base_url={cfg['base_url']}"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
