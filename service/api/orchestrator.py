from __future__ import annotations

import importlib
import json
import os
import queue
import sys
import threading
import time
import types
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .config import CoatingApiSettings
from .schemas import ChatRequest
from .workspace import conversation_workspace
from storage.repository import ServiceRepository


SERVICE_ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = SERVICE_ROOT / "core"


def _stream_diagnostics(request_id: str, conversation_id: str, *, layer: str):
    try:
        if str(CORE_DIR) not in sys.path:
            sys.path.insert(0, str(CORE_DIR))
        from stream_diagnostics import StreamDiagnostics

        return StreamDiagnostics(request_id=request_id, conversation_id=conversation_id, layer=layer)
    except Exception:
        return None


def _stream_event_queue_maxsize() -> int:
    try:
        return max(1, int(os.environ.get("COATING_STREAM_EVENT_QUEUE_MAXSIZE", "1024")))
    except (TypeError, ValueError):
        return 1024


@dataclass
class ChatResult:
    answer: str
    provider: str
    model: str
    packet: dict[str, Any]
    route: list[dict[str, Any]]
    tool_observations: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    examples: list[dict[str, Any]]
    debug: dict[str, Any] | None = None


class LegacyRuntime:
    """Run the local demo planner against a per-conversation workspace.

    The demo modules were originally file-backed globals. For the first A100
    service migration we keep their behavior intact and patch paths inside a
    short critical section, while Postgres/Redis hold the platform state and
    request coordination around this compatibility layer.
    """

    PATH_NAMES = (
        "DATA",
        "RAW_TURNS",
        "OBSERVATIONS",
        "DURABLE",
        "SUMMARY",
        "PACKET",
        "GRAPH_NODES",
        "GRAPH_EDGES",
        "TOOL_OBSERVATIONS",
        "TOOL_ROUTING",
        "SCOPE_STATE",
    )

    def __init__(self, settings: CoatingApiSettings) -> None:
        self.settings = settings
        self.settings.apply_to_environment()
        if str(CORE_DIR) not in sys.path:
            sys.path.insert(0, str(CORE_DIR))
        self.demo_config = importlib.import_module("demo_config")
        self.demo_storage = importlib.import_module("demo_storage")
        self.answering = importlib.import_module("answering")
        self.graph_store = importlib.import_module("graph_store")
        self.memory_core = importlib.import_module("memory_core")
        self.scope_state = importlib.import_module("scope_state")
        self.routing = importlib.import_module("routing")
        self.tool_clients = importlib.import_module("tool_clients")
        self.tool_runtime = importlib.import_module("tool_runtime")
        self.modules = [
            self.demo_config,
            self.answering,
            self.graph_store,
            self.memory_core,
            self.scope_state,
            self.routing,
            self.tool_runtime,
        ]
        self._lock = threading.RLock()

    def workspace_paths(self, workspace: Path, conversation_id: str) -> dict[str, Any]:
        return {
            "SESSION_ID": conversation_id,
            "DATA": workspace,
            "RAW_TURNS": workspace / "raw_turns.jsonl",
            "OBSERVATIONS": workspace / "observations.jsonl",
            "DURABLE": workspace / "durable_memories.jsonl",
            "SUMMARY": workspace / "session_summary.json",
            "PACKET": workspace / "latest_packet.json",
            "GRAPH_NODES": workspace / "graph_nodes.jsonl",
            "GRAPH_EDGES": workspace / "graph_edges.jsonl",
            "TOOL_OBSERVATIONS": workspace / "tool_observations.jsonl",
            "TOOL_ROUTING": workspace / "tool_routing_decisions.jsonl",
            "SCOPE_STATE": workspace / "scope_state.json",
        }

    @contextmanager
    def patched_workspace(self, workspace: Path, conversation_id: str) -> Iterator[None]:
        workspace.mkdir(parents=True, exist_ok=True)
        values = self.workspace_paths(workspace, conversation_id)
        old_values: list[tuple[Any, str, Any]] = []
        old_app = sys.modules.get("app")
        with self._lock:
            shim = types.ModuleType("app")
            for key, value in values.items():
                setattr(shim, key, value)
            for module in self.modules:
                for key, value in values.items():
                    if hasattr(module, key):
                        old_values.append((module, key, getattr(module, key)))
                        setattr(module, key, value)
            sys.modules["app"] = shim
            try:
                self.memory_core.ensure_seed_data()
                self._ensure_platform_seed_data()
                yield
            finally:
                for module, key, value in reversed(old_values):
                    setattr(module, key, value)
                if old_app is None:
                    sys.modules.pop("app", None)
                else:
                    sys.modules["app"] = old_app

    def _ensure_platform_seed_data(self) -> None:
        rows = self.demo_storage.read_jsonl(self.demo_config.DURABLE)
        changed = False
        for row in rows:
            if row.get("id") in {
                "mem_project_constraint_single_chat",
                "mem_user_preference_chinese",
                "mem_provider_openai_compatible_env",
            }:
                row["status"] = "superseded"
                changed = True
        if not any(row.get("id") == "mem_project_constraint_platform_api" for row in rows):
            rows.append(
                {
                    "id": "mem_project_constraint_platform_api",
                    "type": "project_constraint",
                    "title": "A100 platform coating API service",
                    "content": (
                        "当前运行环境是公司平台的 A100 coating API 后端服务。公司网页前端调用 HTTP API；"
                        "回答需要面向网页客户，保持中文、简洁、可验证，并优先使用结构化证据和统计结果。"
                    ),
                    "status": "active",
                    "confidence": 0.98,
                    "source": "a100_platform_migration",
                    "created_at": self.demo_storage.now_iso(),
                    "last_confirmed_at": self.demo_storage.now_iso(),
                    "supersedes": ["mem_project_constraint_single_chat"],
                }
            )
            changed = True
        if not any(row.get("id") == "mem_provider_platform_env" for row in rows):
            rows.append(
                {
                    "id": "mem_provider_platform_env",
                    "type": "project_constraint",
                    "title": "Platform model provider config",
                    "content": (
                        "模型配置来自 A100 服务目录的 .env：默认 deepseek-v4-pro，OpenAI-compatible chat completions，"
                        "thinking 关闭，最大上下文和输出上限由环境变量控制。真实 API key 不进入代码和响应。"
                    ),
                    "status": "active",
                    "confidence": 0.98,
                    "source": "a100_platform_migration",
                    "created_at": self.demo_storage.now_iso(),
                    "last_confirmed_at": self.demo_storage.now_iso(),
                    "supersedes": ["mem_provider_openai_compatible_env"],
                }
            )
            changed = True
        provider_cfg = self.answering.provider_config()
        provider_memory = {
            "id": "mem_provider_platform_env",
            "type": "project_constraint",
            "title": "Platform model provider config",
            "content": (
                f"模型配置来自 A100 服务目录的 .env：当前模型 {provider_cfg.get('model')}，"
                f"协议 {provider_cfg.get('protocol')}，base_url {provider_cfg.get('base_url')}。"
                "thinking 关闭；最大上下文和输出上限由环境变量控制。真实 API key 不进入代码和响应。"
            ),
            "status": "active",
            "confidence": 0.98,
            "source": "a100_platform_migration",
            "created_at": self.demo_storage.now_iso(),
            "last_confirmed_at": self.demo_storage.now_iso(),
            "supersedes": ["mem_provider_openai_compatible_env"],
        }
        existing_provider = next((row for row in rows if row.get("id") == "mem_provider_platform_env"), None)
        if existing_provider:
            created_at = existing_provider.get("created_at") or provider_memory["created_at"]
            existing_provider.update(provider_memory)
            existing_provider["created_at"] = created_at
        else:
            rows.append(provider_memory)
        changed = True
        if changed:
            self.demo_storage.write_jsonl(self.demo_config.DURABLE, rows)

    def build_packet(self, question: str) -> dict[str, Any]:
        read_jsonl = self.demo_storage.read_jsonl
        read_json = self.demo_storage.read_json
        turns = read_jsonl(self.demo_config.RAW_TURNS)
        observations = read_jsonl(self.demo_config.OBSERVATIONS)
        memories = read_jsonl(self.demo_config.DURABLE)
        tool_observations = read_jsonl(self.demo_config.TOOL_OBSERVATIONS)
        tool_routing = read_jsonl(self.demo_config.TOOL_ROUTING)
        scope_state = self.scope_state.load_scope_state()
        current_turn_id = self.scope_state.latest_user_turn_id(turns)
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
        summary = read_json(self.demo_config.SUMMARY, {})
        packet = {
            "session_id": self.demo_config.SESSION_ID,
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
            "relevant_memories": self.memory_core.relevant_memories(question, memories),
            "memory_graph_recall": self.graph_store.graph_recall(question),
            "tool_routing_decisions": tool_routing[-5:],
            "tool_observations": current_tool_observations,
            "recent_tool_observations": recent_tool_observations,
            "active_scope": scope_state.get("active_doc_scope"),
            "scope_history": scope_state.get("scope_history", [])[:5],
            "last_scope_resolution": scope_state.get("last_scope_resolution"),
            "recent_observations": observations[-8:],
            "stale_or_superseded": stale[-5:],
            "built_at": self.demo_storage.now_iso(),
        }
        self.demo_storage.write_json(self.demo_config.PACKET, packet)
        return packet

    def prepare_user_turn(self, question: str) -> dict[str, Any]:
        user_turn = {
            "id": f"turn_{int(time.time() * 1000)}_user",
            "session_id": self.demo_config.SESSION_ID,
            "role": "user",
            "content": question,
            "created_at": self.demo_storage.now_iso(),
        }
        self.demo_storage.append_jsonl(self.demo_config.RAW_TURNS, user_turn)
        obs = self.memory_core.make_observation(user_turn)
        self.demo_storage.append_jsonl(self.demo_config.OBSERVATIONS, obs)
        self.graph_store.update_graph_from_record(obs, "observation")
        promoted = self.memory_core.maybe_promote_memory(obs)
        if promoted:
            self.demo_storage.append_jsonl(self.demo_config.DURABLE, promoted)
            self.graph_store.update_graph_from_record(promoted, "memory")
        self.tool_runtime.maybe_run_tools(question, turn_id=user_turn["id"])
        self.memory_core.update_summary(question)
        return self.build_packet(question)

    def save_assistant_turn(self, question: str, answer: str, provider: str, model: str) -> dict[str, Any]:
        assistant_turn = {
            "id": f"turn_{int(time.time() * 1000)}_assistant",
            "session_id": self.demo_config.SESSION_ID,
            "role": "assistant",
            "content": answer,
            "created_at": self.demo_storage.now_iso(),
            "provider": provider,
            "model": model,
        }
        self.demo_storage.append_jsonl(self.demo_config.RAW_TURNS, assistant_turn)
        self.memory_core.update_summary(question, answer)
        return self.build_packet(question)

    def call_model(self, question: str, packet: dict[str, Any], *, mock_model: bool = False) -> dict[str, Any]:
        if mock_model:
            cfg = self.answering.provider_config()
            return {
                "answer": self.answering.sanitize_customer_answer(
                    self.answering.local_mock_answer(question, packet)
                ),
                "provider": "local-mock",
                "model": cfg["model"],
            }
        return self.answering.call_qwen(question, packet)

    def stream_model(
        self,
        question: str,
        packet: dict[str, Any],
        *,
        mock_model: bool = False,
        diag_request_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        if mock_model:
            cfg = self.answering.provider_config()
            answer = self.answering.sanitize_customer_answer(
                self.answering.local_mock_answer(question, packet)
            )
            for idx in range(0, len(answer), 64):
                yield {
                    "type": "delta",
                    "content": answer[idx : idx + 64],
                    "provider": "local-mock",
                    "model": cfg["model"],
                }
            yield {"type": "done", "provider": "local-mock", "model": cfg["model"]}
            return
        yield from self.answering.stream_qwen(question, packet, diag_request_id=diag_request_id)


class CoatingConversationEngine:
    def __init__(
        self,
        settings: CoatingApiSettings,
        *,
        repository: ServiceRepository | None = None,
        runtime: LegacyRuntime | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository or ServiceRepository(settings)
        self.runtime = runtime or LegacyRuntime(settings)

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "service": "coating-api"}

    def ready(self) -> dict[str, Any]:
        deps = self.repository.ready()
        kg_tools = _probe_url(self.settings.kg_hybrid_search_url.rsplit("/", 1)[0].rsplit("/", 1)[0] + "/health")
        llm_configured = bool(self.settings.llm_api_key)
        return {
            "status": "ok",
            "dependencies": {
                **deps,
                "kg_tools": {"ok": kg_tools},
                "llm": {"ok": llm_configured, "model": self.settings.llm_model},
            },
        }

    def chat(self, request: ChatRequest) -> ChatResult:
        request_id = str(uuid.uuid4())
        started = time.time()
        workspace = conversation_workspace(self.settings, request.conversation_id)
        route: list[dict[str, Any]] = []
        error: str | None = None
        with self.repository.conversation_lock(request.conversation_id):
            try:
                self.repository.record_turn(
                    conversation_id=request.conversation_id,
                    user_id=request.user_id,
                    role="user",
                    content=request.question,
                )
                with self.runtime.patched_workspace(workspace, request.conversation_id):
                    packet = self.runtime.prepare_user_turn(request.question)
                route = _route_summary(packet)
                model_result = self.runtime.call_model(
                    request.question,
                    packet,
                    mock_model=request.options.mock_model,
                )
                with self.runtime.patched_workspace(workspace, request.conversation_id):
                    packet = self.runtime.save_assistant_turn(
                        request.question,
                        model_result["answer"],
                        model_result["provider"],
                        model_result["model"],
                    )
                self.repository.record_turn(
                    conversation_id=request.conversation_id,
                    user_id=request.user_id,
                    role="assistant",
                    content=model_result["answer"],
                    provider=model_result["provider"],
                    model=model_result["model"],
                )
                self.repository.sync_conversation_snapshot(
                    conversation_id=request.conversation_id,
                    user_id=request.user_id,
                    workspace=workspace,
                    packet=packet,
                )
                tool_observations = packet.get("tool_observations") or []
                debug = None
                if request.options.include_debug:
                    debug = {
                        "request_id": request_id,
                        "packet": packet,
                        "workspace": str(workspace),
                        "tool_observations": tool_observations,
                        "route": route,
                    }
                return ChatResult(
                    answer=model_result["answer"],
                    provider=model_result["provider"],
                    model=model_result["model"],
                    packet=packet,
                    route=route,
                    tool_observations=tool_observations,
                    citations=_extract_citations(tool_observations),
                    examples=_extract_examples(tool_observations),
                    debug=debug,
                )
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                elapsed_ms = int((time.time() - started) * 1000)
                self.repository.record_trace(
                    request_id=request_id,
                    conversation_id=request.conversation_id,
                    route=route,
                    elapsed_ms=elapsed_ms,
                    error=error,
                )

    def stream_chat(self, request: ChatRequest) -> Iterator[dict[str, Any]]:
        result = self.chat(request)
        yield {"event": "route", "data": {"tool_calls": [call for call in result.route]}}
        for obs in result.tool_observations:
            yield {
                "event": "tool",
                "data": {
                    "tool": obs.get("tool"),
                    "status": obs.get("status"),
                    "summary": (obs.get("result") or {}).get("summary") or {},
                },
            }
        for idx in range(0, len(result.answer), 32):
            yield {"event": "delta", "data": {"delta": result.answer[idx : idx + 32]}}
        yield {"event": "done", "data": {"answer": result.answer, "model": result.model}}

    def stream_openai_chat_completion(
        self,
        request: ChatRequest,
        *,
        diag_request_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        request_id = str(uuid.uuid4())
        diagnostic_id = diag_request_id or request_id
        diag = _stream_diagnostics(
            diagnostic_id,
            request.conversation_id,
            layer="api.orchestrator.stream",
        )
        events: queue.Queue[dict[str, Any] | object] = queue.Queue(maxsize=_stream_event_queue_maxsize())
        sentinel = object()
        dropped_client_deltas = 0

        def remember_dropped_delta() -> None:
            nonlocal dropped_client_deltas
            dropped_client_deltas += 1
            if diag and (dropped_client_deltas == 1 or dropped_client_deltas % 100 == 0):
                diag.event("stream_queue_dropped_delta", dropped=dropped_client_deltas)

        def enqueue_client_event(event: dict[str, Any]) -> None:
            if event.get("type") == "delta":
                try:
                    events.put_nowait(event)
                except queue.Full:
                    remember_dropped_delta()
                return
            enqueue_terminal_event(event)

        def enqueue_terminal_event(item: dict[str, Any] | object) -> None:
            while True:
                try:
                    events.put_nowait(item)
                    return
                except queue.Full:
                    try:
                        dropped = events.get_nowait()
                    except queue.Empty:
                        continue
                    if isinstance(dropped, dict) and dropped.get("type") == "delta":
                        remember_dropped_delta()

        def produce() -> None:
            started = time.time()
            workspace = conversation_workspace(self.settings, request.conversation_id)
            route: list[dict[str, Any]] = []
            error: str | None = None
            provider = "openai-compatible"
            model = self.settings.llm_model
            answer_parts: list[str] = []
            packet: dict[str, Any] = {}
            tool_observations: list[dict[str, Any]] = []
            lock_wait_started = 0.0
            lock_acquired_at: float | None = None
            if diag:
                diag.event("stream_request_start", trace_request_id=request_id)
            try:
                if diag:
                    diag.event("lock_wait_start", lock_name="conversation_lock")
                lock_wait_started = time.monotonic()
                try:
                    with self.repository.conversation_lock(request.conversation_id):
                        lock_acquired_at = time.monotonic()
                        if diag:
                            diag.event(
                                "lock_acquired",
                                lock_name="conversation_lock",
                                wait_ms=int((lock_acquired_at - lock_wait_started) * 1000),
                            )
                        self.repository.record_turn(
                            conversation_id=request.conversation_id,
                            user_id=request.user_id,
                            role="user",
                            content=request.question,
                        )
                        if diag:
                            diag.event("user_turn_saved")
                            diag.event("kg_prepare_start")
                        with self.runtime.patched_workspace(workspace, request.conversation_id):
                            packet = self.runtime.prepare_user_turn(request.question)
                        route = _route_summary(packet)
                        tool_observations = packet.get("tool_observations") or []
                        if diag:
                            diag.event(
                                "kg_prepare_done",
                                route_count=len(route),
                                tool_observation_count=len(tool_observations),
                            )
                            diag.event("llm_stream_start")
                        for event in self.runtime.stream_model(
                            request.question,
                            packet,
                            mock_model=request.options.mock_model,
                            diag_request_id=diagnostic_id,
                        ):
                            if event.get("provider"):
                                provider = str(event["provider"])
                            if event.get("model"):
                                model = str(event["model"])
                            if event.get("type") == "delta":
                                content = str(event.get("content") or "")
                                if content:
                                    answer_parts.append(content)
                                    if diag:
                                        diag.event(
                                            "before_orchestrator_yield",
                                            chars=len(content),
                                        )
                                    enqueue_client_event({"type": "delta", "content": content, "model": model})
                                    if diag:
                                        diag.event(
                                            "after_orchestrator_yield",
                                            chars=len(content),
                                        )

                        answer = "".join(answer_parts)
                        if diag:
                            diag.event("upstream_done_seen", chars=len(answer))
                            diag.event("assistant_save_start", chars=len(answer))
                        with self.runtime.patched_workspace(workspace, request.conversation_id):
                            packet = self.runtime.save_assistant_turn(request.question, answer, provider, model)
                        self.repository.record_turn(
                            conversation_id=request.conversation_id,
                            user_id=request.user_id,
                            role="assistant",
                            content=answer,
                            provider=provider,
                            model=model,
                        )
                        self.repository.sync_conversation_snapshot(
                            conversation_id=request.conversation_id,
                            user_id=request.user_id,
                            workspace=workspace,
                            packet=packet,
                        )
                        if diag:
                            diag.event("assistant_save_done", chars=len(answer))
                            diag.event("before_orchestrator_done_yield")
                        enqueue_terminal_event(
                            {
                                "type": "done",
                                "provider": provider,
                                "model": model,
                                "route": route,
                                "tool_calls": compact_tool_calls(tool_observations),
                            }
                        )
                        if diag:
                            diag.event("after_orchestrator_done_yield")
                finally:
                    if diag and lock_acquired_at is not None:
                        diag.event(
                            "lock_released",
                            lock_name="conversation_lock",
                            held_ms=int((time.monotonic() - lock_acquired_at) * 1000),
                        )
            except Exception as exc:
                error = str(exc)
                if diag:
                    diag.event("stream_request_exception", error=error)
                message = f"Streaming request failed: {exc}"
                enqueue_client_event({"type": "delta", "content": message, "model": model})
                enqueue_terminal_event({"type": "done", "provider": "coating-api-error", "model": model})
            finally:
                elapsed_ms = int((time.time() - started) * 1000)
                if diag:
                    diag.event("stream_request_closed", elapsed_ms=elapsed_ms, error=error)
                self.repository.record_trace(
                    request_id=request_id,
                    conversation_id=request.conversation_id,
                    route=route,
                    elapsed_ms=elapsed_ms,
                    error=error,
                )
                enqueue_terminal_event(sentinel)

        producer = threading.Thread(
            target=produce,
            name=f"coating-stream-producer-{request.conversation_id}",
            daemon=True,
        )
        producer.start()
        while True:
            item = events.get()
            if item is sentinel:
                break
            yield item

    def call_tool(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "kg.hybrid_search":
            return self.runtime.tool_clients.kg_hybrid_search(
                str(payload.get("query") or ""),
                top_k=payload.get("top_k"),
                candidate_k=payload.get("candidate_k"),
                filters=payload.get("filters"),
            )
        if name == "kg.expand_hyperedge_multihop":
            return self.runtime.tool_clients.kg_expand_hyperedge_multihop(
                payload.get("object_ids") or [],
                max_context_facts=payload.get("max_context_facts"),
                max_evidence_per_item=payload.get("max_evidence_per_item"),
            )
        if name == "kg.sql_aggregate":
            return self.runtime.tool_clients.kg_sql_aggregate(
                intent=str(payload.get("intent") or "distinct_count"),
                target=str(payload.get("target") or "test_method"),
                filters=payload.get("filters") or {},
                group_by=payload.get("group_by") or [],
                limit=payload.get("limit"),
                include_examples=payload.get("include_examples", True),
            )
        if name == "kg.doc_field_scan":
            return self.runtime.tool_clients.kg_doc_field_scan(
                doc_ids=payload.get("doc_ids") or [],
                query=str(payload.get("query") or ""),
                field_groups=payload.get("field_groups") or [],
                limit=payload.get("limit") or 200,
                include_evidence=payload.get("include_evidence", True),
            )
        raise ValueError(f"unsupported tool: {name}")


def _probe_url(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return 200 <= resp.status < 500
    except Exception:
        return False


def _route_summary(packet: dict[str, Any]) -> list[dict[str, Any]]:
    route_rows = packet.get("tool_routing_decisions") or []
    summary: list[dict[str, Any]] = []
    for row in route_rows[-3:]:
        decision = row.get("decision") or {}
        for call in decision.get("calls") or []:
            summary.append(
                {
                    "tool": call.get("tool"),
                    "plan_type": decision.get("plan_type"),
                    "confidence": decision.get("confidence"),
                    "filters": call.get("filters"),
                }
            )
    return summary


def _extract_citations(tool_observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for obs in tool_observations:
        result = obs.get("result") or {}
        if obs.get("tool") == "kg.expand_hyperedge_multihop":
            for item in result.get("items") or []:
                for ev in item.get("evidence") or []:
                    citations.append(
                        {
                            "doc_id": item.get("doc_id"),
                            "object_id": item.get("object_id"),
                            "page": ev.get("page"),
                            "section": ev.get("section"),
                            "table": ev.get("table"),
                            "quote": ev.get("quote"),
                        }
                    )
        if obs.get("tool") == "kg.sql_aggregate":
            for item in result.get("items") or []:
                for ev in item.get("examples") or []:
                    citations.append(
                        {
                            "doc_id": ev.get("doc_id"),
                            "object_id": ev.get("object_id"),
                            "page": ev.get("page"),
                            "section": ev.get("section"),
                            "table": ev.get("table"),
                            "quote": ev.get("quote"),
                        }
                    )
        if obs.get("tool") == "kg.doc_field_scan":
            for item in result.get("items") or []:
                for ev in item.get("evidence") or []:
                    citations.append(
                        {
                            "doc_id": item.get("doc_id"),
                            "object_id": item.get("object_id"),
                            "page": ev.get("page"),
                            "section": ev.get("section"),
                            "table": ev.get("table"),
                            "quote": ev.get("quote"),
                        }
                    )
    return citations[:12]


def _extract_examples(tool_observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for obs in tool_observations:
        result = obs.get("result") or {}
        if obs.get("tool") == "kg.sql_aggregate":
            for item in result.get("items") or []:
                for ev in item.get("examples") or []:
                    examples.append({"value": item.get("value"), **ev})
    return examples[:12]


def compact_tool_calls(tool_observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls = []
    for obs in tool_observations:
        result = obs.get("result") or {}
        calls.append(
            {
                "tool": str(obs.get("tool") or "unknown"),
                "status": str(obs.get("status") or result.get("status") or "unknown"),
                "summary": result.get("summary") or {},
            }
        )
    return calls
