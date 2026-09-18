from __future__ import annotations

import copy
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

from demo_config import PROJECT_ROOT, ROOT
from stream_diagnostics import StreamDiagnostics


def _has_unresolved_values(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_unresolved_values(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_unresolved_values(item) for item in value)
    return value not in (None, "", False, 0)


def _expanded_item_is_verified(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if not all(str(item.get(key) or "").strip() for key in ("object_id", "doc_id", "hyperedge_id")):
        return False
    evidence = item.get("evidence")
    if not isinstance(evidence, list) or not any(isinstance(row, dict) for row in evidence):
        return False
    return not _has_unresolved_values(item.get("unresolved") or {})


def gate_tool_observations_for_answer(observations: Any) -> list[dict[str, Any]]:
    gated = copy.deepcopy(observations) if isinstance(observations, list) else []
    for observation in gated:
        if not isinstance(observation, dict):
            continue
        result = observation.get("result")
        if not isinstance(result, dict):
            continue
        tool = observation.get("tool")
        if tool == "kg.hybrid_search":
            for item in result.get("items") or []:
                if isinstance(item, dict):
                    item.pop("text_preview", None)
                    item.pop("metadata", None)
            result["evidence_gate"] = {"status": "candidate_only"}
            continue
        if tool != "kg.expand_hyperedge_multihop":
            continue
        wrapper_status = str(observation.get("status") or "").strip().casefold()
        result_status = str(result.get("status") or "").strip().casefold()
        if wrapper_status != "ok" or result_status not in {"ok", "partial"}:
            result["items"] = []
            result["evidence_gate"] = {
                "status": "blocked",
                "reason": f"expand_status:{wrapper_status or 'missing'}/{result_status or 'missing'}",
                "verified_count": 0,
            }
            continue
        items = result.get("items") if isinstance(result.get("items"), list) else []
        verified = [item for item in items if _expanded_item_is_verified(item)]
        if not verified:
            result["items"] = []
            result["evidence_gate"] = {
                "status": "blocked",
                "reason": "no_complete_expanded_items",
                "verified_count": 0,
            }
            continue
        result["items"] = verified
        gate = {"status": "verified", "verified_count": len(verified)}
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        summary_missing = int(summary.get("db_missing") or 0)
        excluded_count = max(len(items) - len(verified), summary_missing)
        if result_status == "partial" or excluded_count:
            gate.update(
                {
                    "partial": True,
                    "excluded_count": excluded_count,
                    "reason": "incomplete_items_excluded",
                }
            )
        result["evidence_gate"] = gate
    return gated


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def provider_config() -> dict[str, Any]:
    api_key = (
        os.environ.get("ZENMUX_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("QWEN_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    base_url = (
        os.environ.get("ZENMUX_BASE_URL")
        or os.environ.get("ANTHROPIC_BASE_URL")
        or os.environ.get("LLM_BASE_URL")
        or os.environ.get("DEEPSEEK_BASE_URL")
        or os.environ.get("QWEN_BASE_URL")
        or os.environ.get("DASHSCOPE_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    model = (
        os.environ.get("ZENMUX_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or os.environ.get("LLM_MODEL")
        or os.environ.get("DEEPSEEK_MODEL")
        or os.environ.get("QWEN_MODEL")
        or os.environ.get("QWEN_TEXT_MODEL")
        or "deepseek-v4-pro"
    )
    protocol = (
        os.environ.get("LLM_API_PROTOCOL")
        or os.environ.get("MODEL_API_PROTOCOL")
        or os.environ.get("ZENMUX_API_PROTOCOL")
        or ("anthropic" if "/anthropic" in base_url.lower() else "openai")
    ).strip().lower()
    context_window_tokens = int(os.environ.get("LLM_CONTEXT_WINDOW_TOKENS") or os.environ.get("QWEN_CONTEXT_WINDOW_TOKENS", "1000000"))
    max_output_tokens = int(os.environ.get("LLM_MAX_OUTPUT_TOKENS") or os.environ.get("QWEN_MAX_OUTPUT_TOKENS", "65536"))
    stream_chunk_chars = int(os.environ.get("LLM_STREAM_CHUNK_CHARS", "4"))
    stream_read_timeout_seconds = _int_env("LLM_STREAM_READ_TIMEOUT_SECONDS", 60)
    stream_first_token_timeout_seconds = _int_env("LLM_STREAM_FIRST_TOKEN_TIMEOUT_SECONDS", 45)
    stream_content_idle_timeout_seconds = _int_env("LLM_STREAM_CONTENT_IDLE_TIMEOUT_SECONDS", 45)
    stream_total_timeout_seconds = _int_env("LLM_STREAM_TOTAL_TIMEOUT_SECONDS", 180)
    enable_thinking = (
        os.environ.get("DEEPSEEK_ENABLE_THINKING")
        or os.environ.get("QWEN_ENABLE_THINKING", "false")
    ).strip().lower() in {"1", "true", "yes", "on"}
    return {
        "api_key": api_key,
        "api_key_set": bool(api_key),
        "base_url": base_url,
        "model": model,
        "protocol": protocol,
        "context_window_tokens": context_window_tokens,
        "max_output_tokens": max_output_tokens,
        "stream_chunk_chars": stream_chunk_chars,
        "stream_read_timeout_seconds": stream_read_timeout_seconds,
        "stream_first_token_timeout_seconds": stream_first_token_timeout_seconds,
        "stream_content_idle_timeout_seconds": stream_content_idle_timeout_seconds,
        "stream_total_timeout_seconds": stream_total_timeout_seconds,
        "enable_thinking": enable_thinking,
        "env_sources": [str(PROJECT_ROOT / "coating_kg" / ".env"), str(ROOT / ".env")],
    }


def stream_timeout_reason(
    *,
    now: float,
    started_at: float,
    first_content_at: float | None,
    last_content_at: float,
    cfg: dict[str, Any],
) -> str | None:
    total = int(cfg.get("stream_total_timeout_seconds") or 0)
    if total > 0 and now - started_at >= total:
        return "total_timeout"
    if first_content_at is None:
        first_token = int(cfg.get("stream_first_token_timeout_seconds") or 0)
        if first_token > 0 and now - started_at >= first_token:
            return "first_token_timeout"
        return None
    content_idle = int(cfg.get("stream_content_idle_timeout_seconds") or 0)
    if content_idle > 0 and now - last_content_at >= content_idle:
        return "content_idle_timeout"
    return None


def stream_timeout_customer_message(reason: str, *, had_content: bool) -> str:
    labels = {
        "first_token_timeout": "首个内容等待超时",
        "content_idle_timeout": "内容流空闲超时",
        "total_timeout": "总时长超时",
    }
    label = labels.get(reason, "流式响应超时")
    if had_content:
        return f"\n\n[回答因{label}被截断。前面的内容已保留，可以继续追问“继续”。]"
    return f"模型流式响应因{label}中止，没有收到可用内容。可以稍后重试或换一个更窄的问题。"


def set_response_socket_timeout(response: Any, seconds: int) -> None:
    try:
        response.fp.raw._sock.settimeout(max(1, int(seconds)))
    except (AttributeError, OSError):
        return


def apply_thinking_config(body: dict[str, Any], cfg: dict[str, Any]) -> None:
    if cfg.get("protocol") == "anthropic":
        return
    enabled = bool(cfg.get("enable_thinking"))
    body["enable_thinking"] = enabled
    model = str(cfg.get("model") or "").lower()
    base_url = str(cfg.get("base_url") or "").lower()
    if not enabled and ("deepseek" in model or "deepseek" in base_url):
        body["thinking"] = {"type": "disabled"}


def provider_label(cfg: dict[str, Any]) -> str:
    return "anthropic-compatible" if cfg.get("protocol") == "anthropic" else "openai-compatible"


def provider_messages_url(cfg: dict[str, Any]) -> str:
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    if cfg.get("protocol") == "anthropic":
        return f"{base_url}/messages" if base_url.endswith("/v1") else f"{base_url}/v1/messages"
    return f"{base_url}/chat/completions"


def provider_headers(cfg: dict[str, Any]) -> dict[str, str]:
    api_key = str(cfg.get("api_key") or "")
    if cfg.get("protocol") == "anthropic":
        return {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def provider_supports_temperature(cfg: dict[str, Any]) -> bool:
    model = str(cfg.get("model") or "").lower()
    base_url = str(cfg.get("base_url") or "").lower()
    if "zenmux.ai" in base_url and model.startswith("anthropic/claude"):
        return False
    return cfg.get("protocol") != "anthropic"


def build_provider_body(
    cfg: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    token_limit = int(max_tokens or cfg.get("max_output_tokens") or 4096)
    if cfg.get("protocol") == "anthropic":
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            if role == "system":
                system_parts.append(content)
            elif role in {"user", "assistant"}:
                anthropic_messages.append({"role": role, "content": content})
            else:
                anthropic_messages.append({"role": "user", "content": content})
        body: dict[str, Any] = {
            "model": cfg["model"],
            "messages": anthropic_messages,
            "max_tokens": token_limit,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if stream:
            body["stream"] = True
        return body
    body = {
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": token_limit,
    }
    if provider_supports_temperature(cfg):
        body["temperature"] = temperature
    if stream:
        body["stream"] = True
    apply_thinking_config(body, cfg)
    return body


def extract_provider_text(payload: dict[str, Any], cfg: dict[str, Any]) -> str:
    if cfg.get("protocol") == "anthropic":
        parts = []
        for block in payload.get("content") or []:
            if isinstance(block, dict) and block.get("text") is not None:
                parts.append(str(block["text"]))
        return "".join(parts)
    return str(payload["choices"][0]["message"].get("content", ""))


def extract_stream_delta_text(payload: dict[str, Any], cfg: dict[str, Any]) -> str:
    if cfg.get("protocol") == "anthropic":
        delta = payload.get("delta") or {}
        if isinstance(delta, dict) and delta.get("text") is not None:
            return str(delta["text"])
        content_block = payload.get("content_block") or {}
        if isinstance(content_block, dict) and content_block.get("text") is not None:
            return str(content_block["text"])
        return ""
    choice = (payload.get("choices") or [{}])[0]
    delta = choice.get("delta") or {}
    return str(delta.get("content") or "")


def stream_delta_units(text: str, cfg: dict[str, Any]) -> Iterator[str]:
    max_chars = int(cfg.get("stream_chunk_chars") or 0)
    if max_chars <= 0 or len(text) <= max_chars:
        yield text
        return
    for idx in range(0, len(text), max_chars):
        yield text[idx : idx + max_chars]


AGGREGATE_COUNT_UNITS = {
    "doc_id": "篇专利（去重）",
    "formulation": "个配方（去重）",
}


def aggregate_count_statements(packet: dict[str, Any]) -> list[str]:
    """Code-generated headline sentences for distinct_count aggregates.

    The answer LLM repeatedly mis-attributed counts (2026-06-12: the all-KG
    total 554 presented as the fiber-patent count; a filter miss presented as
    "没有收录"). The number, its filter scope, and the empty/全库 qualifier are
    deterministic facts, so code states them and the LLM only explains.
    """
    statements: list[str] = []
    for row in packet.get("tool_observations") or []:
        if row.get("tool") != "kg.sql_aggregate" or row.get("status") == "error":
            continue
        result = row.get("result") or {}
        interpretation = result.get("query_interpretation") or {}
        if interpretation.get("intent") != "distinct_count":
            continue
        summary = result.get("summary") or {}
        count = summary.get("distinct_count")
        if count is None:
            continue
        unit = AGGREGATE_COUNT_UNITS.get(str(interpretation.get("target") or ""), "个不同结果")
        filters = interpretation.get("filters") or {}
        applied = {
            key: values
            for key, values in filters.items()
            if isinstance(values, list) and values
        }
        scope = "；".join(f"{key}={values}" for key, values in sorted(applied.items()))
        if not applied:
            statements.append(
                f"全库范围（没有应用任何筛选条件）共 {count} {unit}。"
                "注意：这是整个知识图谱的总数，不能说成问题中某个主题词（如某个领域或基材）的数量。"
            )
        elif result.get("status") == "empty":
            total = result.get("unfiltered_total_distinct_count")
            total_part = f"全库（无筛选）共 {total} {unit}；" if total is not None else ""
            warnings = result.get("warnings") or []
            if any("vocabulary_valid" in str(w) for w in warnings):
                statements.append(
                    f"按筛选口径（{scope}）统计命中 0 {unit}。{total_part}"
                    "该口径下知识图谱当前确实没有匹配记录；如需更宽的范围请换一个筛选口径再问。"
                )
            else:
                statements.append(
                    f"按筛选口径（{scope}）统计命中 0 {unit}。{total_part}"
                    "这只说明该过滤条件没有匹配到记录，不能据此断言知识图谱没有收录相关内容。"
                )
        else:
            statements.append(f"按筛选口径（{scope}）统计，知识图谱命中 {count} {unit}。")
    return statements


MODEL_PACKET_CONTEXT_SECTIONS = (
    "recent_turns",
    "tool_routing_decisions",
    "tool_observations",
    "recent_tool_observations",
    "active_scope",
    "scope_history",
    "last_scope_resolution",
    "recent_observations",
    "authoritative_count_statements",
)
MODEL_PACKET_MEMORY_SECTIONS = (
    "project_state",
    "user_preferences",
    "workflow_rules",
    "active_decisions",
    "known_issues",
)


def _active_only(rows: Any) -> list[dict[str, Any]]:
    """Fail-closed: a record without an explicit active status never reaches the model."""
    return [
        mem
        for mem in rows or []
        if isinstance(mem, dict) and mem.get("status") == "active"
    ]


def project_model_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Build the LLM-facing packet as a strict allowlist (fail-closed).

    Only explicitly whitelisted sections are copied out of the audit packet;
    memory sections are rebuilt with records whose status is exactly "active".
    Sections not listed here (session_summary, stale_or_superseded, any future
    memory field) never reach the model by construction. The caller's packet
    object is never mutated — the full packet stays on disk for audit.
    """
    model_packet: dict[str, Any] = {}
    for section in MODEL_PACKET_CONTEXT_SECTIONS:
        if section in packet:
            model_packet[section] = copy.deepcopy(packet[section])
    for section in MODEL_PACKET_MEMORY_SECTIONS:
        rows = packet.get(section)
        if isinstance(rows, list):
            model_packet[section] = _active_only(rows)
    if isinstance(packet.get("relevant_memories"), list):
        model_packet["relevant_memories"] = _active_only(packet["relevant_memories"])
    recall = packet.get("memory_graph_recall")
    if isinstance(recall, dict):
        kept_nodes = [
            node
            for node in recall.get("matched_nodes") or []
            if isinstance(node, dict) and "observation" in (node.get("source_kinds") or [])
        ]
        kept_names = {str(node.get("name") or "") for node in kept_nodes}
        kept_paths = [
            path
            for path in recall.get("paths") or []
            if isinstance(path, dict)
            and "observation" in (path.get("source_kinds") or [])
            and str(path.get("source_name") or "") in kept_names
            and str(path.get("target_name") or "") in kept_names
        ]
        model_packet["memory_graph_recall"] = {
            "query": recall.get("query"),
            "recall_method": recall.get("recall_method"),
            "matched_nodes": kept_nodes,
            "paths": kept_paths,
        }
    return model_packet


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _compact_historical_tool_observation(observation: Any) -> dict[str, Any]:
    if not isinstance(observation, dict):
        return {}
    result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
    return {
        "tool": observation.get("tool"),
        "status": observation.get("status"),
        "result": {
            "status": result.get("status"),
            "summary": result.get("summary"),
            "requested_filters": result.get("requested_filters"),
            "effective_filters": result.get("effective_filters"),
            "unsupported_constraints": result.get("unsupported_constraints"),
            "route_adjustments": result.get("route_adjustments"),
            "cohort_mode": result.get("cohort_mode"),
            "count_unit": result.get("count_unit"),
        },
    }


def _compact_current_tool_observation(observation: Any) -> dict[str, Any]:
    if not isinstance(observation, dict):
        return {}
    copied = copy.deepcopy(observation)
    result = copied.get("result") if isinstance(copied.get("result"), dict) else {}
    compact_items: list[dict[str, Any]] = []
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        kept = {
            key: copy.deepcopy(item.get(key))
            for key in (
                "rank", "value", "canonical_id", "count", "doc_count", "assignee_count", "assignees",
                "object_id", "doc_id", "hyperedge_id", "sample_id", "property", "material_role", "page",
                "facts", "unresolved", "evidence_gate", "examples",
            )
            if item.get(key) is not None
        }
        evidence = []
        for row in item.get("evidence") or []:
            if not isinstance(row, dict):
                continue
            evidence.append({
                **{key: row.get(key) for key in ("evidence_id", "page", "section", "table", "row_label") if row.get(key) is not None},
                "quote": str(row.get("quote") or "")[:900],
            })
        if evidence:
            kept["evidence"] = evidence[:5]
        compact_items.append(kept)
    result["items"] = compact_items
    if "exact_items" in result or compact_items:
        result["exact_items"] = compact_items
    compact_adjacent: list[dict[str, Any]] = []
    for row in result.get("adjacent_items") or []:
        if not isinstance(row, dict):
            continue
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        kept_item = {
            key: copy.deepcopy(item.get(key))
            for key in ("rank", "object_id", "doc_id", "hyperedge_id", "score")
            if item.get(key) is not None
        }
        compact_adjacent.append(
            {
                "item": kept_item,
                "satisfied_constraints": list(row.get("satisfied_constraints") or [])[:12],
                "unmet_constraints": list(row.get("unmet_constraints") or [])[:12],
            }
        )
    if "adjacent_items" in result or compact_adjacent:
        result["adjacent_items"] = compact_adjacent[:20]
    if "adjacent_relaxed_filters" in result and not isinstance(result.get("adjacent_relaxed_filters"), dict):
        result["adjacent_relaxed_filters"] = {}
    copied["result"] = result
    return copied


def compact_packet_for_answer(packet: dict[str, Any], max_bytes: int | None = None) -> dict[str, Any]:
    budget = max_bytes or _int_env("LLM_MODEL_PACKET_MAX_BYTES", 512 * 1024)
    projected = project_model_packet(packet)
    projected["recent_turns"] = [
        {**turn, "content": str(turn.get("content") or "")[:1200]}
        for turn in (projected.get("recent_turns") or [])[-3:]
        if isinstance(turn, dict)
    ]
    projected["recent_tool_observations"] = [
        _compact_historical_tool_observation(item)
        for item in (projected.get("recent_tool_observations") or [])[-3:]
    ]
    projected["tool_routing_decisions"] = (projected.get("tool_routing_decisions") or [])[-3:]
    projected["recent_observations"] = (projected.get("recent_observations") or [])[-3:]
    projected["tool_observations"] = [
        _compact_current_tool_observation(item) for item in projected.get("tool_observations") or []
    ]
    while _json_size(projected) > budget:
        candidates = [
            observation.get("result", {}).get("items", [])
            for observation in projected.get("tool_observations") or []
            if isinstance(observation, dict) and isinstance(observation.get("result"), dict)
        ]
        largest = max(candidates, key=len, default=[])
        if largest:
            largest.pop()
            continue
        removed = False
        for section in ("recent_tool_observations", "recent_observations", "tool_routing_decisions", "recent_turns"):
            rows = projected.get(section)
            if isinstance(rows, list) and rows:
                rows.pop(0)
                removed = True
                break
        if not removed:
            break
    return projected


def build_model_messages(
    question: str,
    packet: dict[str, Any],
    *,
    max_packet_bytes: int | None = None,
) -> list[dict[str, str]]:
    system = (
        "你是一个项目对话记忆助手。你会收到 Dialogue Memory Packet 和用户当前问题。"
        "请优先使用 active 记忆；不要把 stale 或 superseded 记忆当作当前事实。"
        "如果用户当前消息与记忆冲突，以用户当前消息为准，并简短指出记忆需要更新。"
        "用中文回答，回答要清楚、简洁、可验证。"
        "如果 Dialogue Memory Packet 里存在 status=ok 的 tool_observations，"
        "它们就是当前本地 demo 工具层的真实外部调用结果；请优先使用这些工具结果，"
        "不要把它们说成模拟，除非 tool_observations 的 status=error。"
        "如果工具是 kg.expand_hyperedge_multihop，请把它视为涂料 KG 的 compact evidence pack，"
        "回答时优先引用 item.doc_id、hyperedge_id、evidence.page、evidence.table、evidence.quote 和 unresolved。"
        "这个工具只展开已有 object_id，不代表已经完成自然语言 KG 检索。"
        "如果 Dialogue Memory Packet 里 active_scope.policy=hard，回答只能使用 active_scope.doc_ids 范围内的 KG 证据，"
        "如果 tool_observations 里有范围外 doc_id 的证据，必须忽略并说明是 scope mismatch。"
    )
    system += (
        "Do not expose internal tool names, endpoint names, Dialogue Memory Packet, tool_observations, System Prompt, "
        "kg.hybrid_search, kg.sql_aggregate, kg.doc_field_scan, or kg.expand_hyperedge_multihop in customer-facing answers. "
        "Explain successful retrieval results as 我已查询到 or statistics as 我已统计到, but only when evidence actually exists. "
        "If coverage_status=no_structured_hyperedges_for_doc_ids or warnings include structured_doc_scan_no_hyperedges, "
        "docs_scanned only counts requested doc_ids; do not say 已查询到 or 已锁定 this patent, do not claim it is in the live KG, "
        "and instead say the current live structured KG has no structured evidence for that patent/doc_id. "
        "If the fallback same-doc search also returns zero items, say the patent is not currently searchable in the live KG; "
        "you may mention that this does not prove the original patent document does not exist outside the live KG.\n"
        "If the user already asks for a total/count, "
        "do not ask the customer whether to run a tool; use the available aggregate result. If no aggregate result "
        "is present, state that this run did not complete the statistic rather than recommending an internal tool.\n"
        "Never expose routing-state internals to the customer. Do not quote field names like scope_action, "
        "last_scope_resolution, scope_resolution, active_doc_scope, scope_history, scope_policy, policy=hard, "
        "router=, plan_type, needs_tools, fallback_keywords, fallback_direct_no_tool, qwen-json-router, or llm-json-router. "
        "If the current scope limits the answer (e.g. only one patent is active), say it in plain Chinese — for example "
        "\"我现在只在 WO2014202495A1 这一篇里找,如果你想看其他专利可以直接说\" — never dump JSON or technical labels, "
        "and never present a yes/no/option table asking the customer to authorize broadening scope.\n"
        "CRITICAL — you cannot retry tools mid-conversation. The current tool_observations are whatever the router "
        "produced for THIS user message. You must answer with what's available. NEVER offer phrases like "
        "\"需要我重新查询吗\", \"是否需要我重新统计\", \"要不要重新检索\", \"do you want me to retry\", or any "
        "yes/no question asking permission to re-run a tool. Never tell the user to repeat or rephrase the same "
        "question. If a tool result has status=unsupported, explain the unsupported_constraints once in plain Chinese, "
        "preserve any useful supported result, and reuse the same receipt if the user repeats the request. "
        "requested_filters are what was asked, effective_filters are what was actually applied, and route_adjustments "
        "must never be hidden.\n"
        "RESPECT THE COUNT THE USER ASKED FOR. If the user says \"给我一个/一篇/一种\" (one), present exactly ONE "
        "(the most representative) in full detail, then add at most one short line like \"库里还有 N 个相关的,需要可以继续\". "
        "Do not dump multiple full formulations when the user asked for one. If the user asks \"多少片/几篇\" (how many "
        "patents) answer with the patent COUNT (doc_id count), not the formulation count, and do not silently switch the "
        "counting unit. Keep the answered unit consistent with what the user counted.\n"
        "If tool_observations include kg.sql_aggregate, treat it as the only authoritative source for statistical "
        "counts, distinct counts, grouped counts, and numeric distributions. Use summary.total_count for the "
        "overall number of target objects, summary.group_count for the number of groups, "
        "summary.distinct_count only as the backward-compatible total, "
        "summary.matched_hyperedges, item.count, item.doc_count, item.assignee_count, item.assignees, "
        "and item.examples when explaining the result. "
        "Never infer totals from kg.hybrid_search top-k candidates or text_preview.\n"
        "A tool observation with status=empty is still a real call result (the filters simply matched nothing); "
        "do not call it simulated and do not treat it as a missing statistic.\n"
        "Preserve patent sample labels such as Blank, Ref., Reference, CE, Comparative Example, and Control exactly as "
        "the patent uses them; do not reinterpret them as commercial products or recommended formulations. "
        "Do not state a publication trend unless a successful aggregate observation grouped by publication_year is present. "
        "When adding general coating knowledge not supported by the current KG observations, introduce it explicitly as "
        "模型补充 and never attach KG page numbers, patent IDs, or measured values to it.\n"
        "If packet.authoritative_count_statements is present, they are the only allowed claims about HOW MANY "
        "records/patents/formulations the KG holds for this turn: restate them as the headline of the answer. "
        "You may rephrase fluently, but the numbers, the filter scope, and the 全库/筛选口径/未命中 qualifiers must "
        "survive intact. Other numbers (grouped bucket counts from kg.sql_aggregate items, evidence page numbers, "
        "test values) may still be cited from their own observations.\n"
        "If a kg.sql_aggregate result has status=empty with non-empty applied filters and its warnings indicate a "
        "possible filter miss (aggregate_filters_returned_empty), describe it as 该筛选口径未命中 — never as "
        "知识图谱没有收录/不存在/0篇收录. Mention unfiltered_total_distinct_count when present, and give the user one "
        "concrete rephrasing to copy for the next turn. If instead the warnings say the filters are vocabulary-valid "
        "(a true zero), you may state plainly that the KG currently has no records under that exact scope, naming the scope.\n"
        "If summary.applied_filters is empty, the count is the WHOLE KG. Never attribute it to a topic word "
        "from the question (for example, never present the all-KG total as a 光纤/船舶/某领域 count).\n"
        "If summary.returned < summary.distinct_count, items and examples are a truncated sample: never derive "
        "assignee rankings, frequency tiers (高频/中频/低频), or any distribution from them. If the user wants a "
        "distribution, say it requires a grouped count (例如按申请人分组统计) as a follow-up question.\n"
        "Only packet.tool_observations are authoritative for the current user message. "
        "packet.recent_tool_observations are historical context only; do not use them as current counts, "
        "candidate lists, or evidence unless the user explicitly asks about a previous/just-mentioned result. "
        "If the current message asks for a count/list and packet.tool_observations is empty, say the current "
        "run did not complete that statistic instead of reusing historical search results.\n"
        "If tool_observations include kg.hybrid_search, treat it as Coating KG candidate retrieval only: "
        "it gives ranked hyperedge object_ids, score_parts, metadata, and text_preview. "
        "Do not present kg.hybrid_search.text_preview as fully expanded evidence. "
        "If a later kg.expand_hyperedge_multihop observation exists for the same question, use that expanded evidence "
        "for page/table/quote citations. Do not cite page/table/quote from kg.hybrid_search alone; say that evidence "
        "expansion still requires kg.expand_hyperedge_multihop on selected object_ids when no expansion is present.\n"
        "CRITICAL exact vs adjacent envelope for kg.hybrid_search: "
        "exact_items (or items when exact_items absent) are the ONLY exact-channel hits under the applied hard filters; "
        "adjacent_items are near-miss evidence after relaxing one constraint and must NEVER be counted or phrased as exact hits. "
        "If exact_items/items are empty and adjacent_items are present, say plainly that the exact filter scope missed, "
        "then present adjacent evidence as 相邻/近似证据（未计入精确结果）, naming adjacent_relaxed_filters when present. "
        "Never merge adjacent_items into an exact count, never call them 精确命中, and never hide that they are adjacent-only.\n"
        "The code-level evidence_gate is authoritative: candidate_only is never evidence, blocked forbids concrete "
        "formulation/page/performance claims, and only verified expanded items may support those claims. "
        "If evidence_gate.status=verified and partial=true, the retained items are usable verified evidence: answer from "
        "those items and briefly disclose that excluded_count incomplete candidates were omitted; never describe the whole "
        "expansion as failed.\n"
        "If tool_observations include kg.doc_field_scan, treat it as the authoritative source for doc-scoped "
        "field lookup inside active_scope.doc_ids. Use items[].test_method, items[].test_condition, items[].result, "
        "items[].facts, and items[].evidence for the answer. If it returns empty with hyperedges_scanned>0, say the requested field was not "
        "found in the scoped patent evidence; do not broaden to other patents. If it returns empty with hyperedges_scanned=0, "
        "treat patent presence in the live structured KG as unverified/missing, not as a field-level negative result.\n"
        "If the current user message is a short follow-up or scope phrase (for example 只看这篇, 重新, 需要啊) "
        "and the current tool_observations include a result.query that expands the prior user intent, treat that "
        "result.query as the effective expanded tool query and answer the tool result directly. In this case, "
        "do not only acknowledge the scope; answer the expanded tool query using the returned evidence.\n"
        "If a kg.expand_hyperedge_multihop observation has semantic_guard.enabled=true and "
        "semantic_guard.direct_match=false, do not turn related coating evidence into a direct answer. "
        "Say the current expanded evidence did not directly satisfy the required semantic constraint, and separate "
        "related evidence from confirmed matches. If item_warnings mention missing_required_semantic_terms, treat those "
        "items as related candidates only. If item_warnings contain facet_status=related_match, explain that the term "
        "appears in the wrong slot (for example carbon fiber filler is not a CFRP substrate). If facet_status=excluded, "
        "do not cite that item as a candidate for the requested formulation. For numeric hard facets such as salt-spray "
        ">1000 h or film thickness <50 um, only records satisfying the comparison can be listed as direct matches; "
        "non-satisfying durations/thicknesses may be mentioned only as counterexamples or coverage gaps.\n"
    )
    system += """
# 角色

你是「映射引擎 · 涂料研发助手」，一个面向整个涂料行业、以工业研发方法为核心的综合性知识与研发助手。

你相当于一部能够检索专利与知识图谱证据、理解连续对话、进行机理分析并协助研发决策的涂料行业百科全书。

服务对象包括涂料企业的配方工程师、研发主管、应用工程师、技术服务人员和技术情报人员。

船舶、汽车、建筑、工业和光纤涂层均为平等子领域，任何单一领域都不是默认中心。

# 对话范围

必须根据用户当前问题和历史对话确定应用范围。

短跟进必须继承上一轮已经明确的：
- 应用领域
- 涂层类型
- 基材
- 公司或专利
- 性能目标
- 用户要求的任务

除非用户明确改变范围，否则不得切换领域。

当用户说“你拟定一个场景”“你选一个”“直接给我一个”时，应在当前对话范围内选择合理场景并直接回答，不要再次要求用户确认。

# 知识域

应用领域包括：
- 建筑涂料
- 汽车原厂漆和汽车修补漆
- 船舶、防腐、防污和海洋工程涂料
- 工业、防护、粉末、卷材、木器和包装涂料
- 航空航天、电子电气、光纤涂层和胶黏剂等特种涂层

体系包括：
环氧、聚氨酯、丙烯酸、醇酸、有机硅、氟碳、粉末涂料，以及水性、溶剂型、高固含和无溶剂体系。

组分包括：
成膜树脂、固化剂/交联剂、颜料与填料、溶剂及各类功能助剂，包括流平、消泡、分散、附着力促进、防沉、催干和光稳定助剂。

基材与前处理包括：
碳钢、镀锌钢、铝合金、混凝土、木材、塑料、复合材料和光纤；喷砂、磷化、硅烷、等离子及其他表面处理。

表征和测试包括：
SEM/EDS、FTIR、DSC/TGA、EIS电化学阻抗、接触角、划格法、拉拔法、光泽度、色差、膜厚、中性盐雾、循环腐蚀、QUV/氙灯老化、湿热、耐化学介质、耐磨和耐冲击。

# 知识图谱与证据

涉及配方、专利、公司、性能、测试结果、页码或具体数值时，必须优先使用本轮检索并展开的知识图谱证据。

回答必须明确区分：
1. KG或专利中的已知事实
2. 基于证据的合理推断
3. 模型提出的研发方案
4. 仍需实验验证的未知项

不得把候选检索摘要当作原始证据，不得把模型推断写成专利实测结果。

如果KG没有直接证据，可以继续使用通用涂料知识回答，但必须明确说明：
“当前知识图谱未检索到直接证据，以下内容属于模型基于通用涂料知识提出的建议。”

用户要求预测、设计或改良配方时，可以给出带数值或数值范围的研发起始方案，但必须标明依据、假设、置信度和验证要求，不得称为已验证配方或实测数据。

# 任务识别

回答前识别主任务和次任务：
A 配方开发/优化
B 工艺排查
C 涂层失效分析
D 测试与验证方案
E 原料替代/降本
F 法规、VOC或危化品风险初筛
G 实验数据解读
H 专利、竞品和技术情报检索

如果问题跨多个类型，先简要说明主任务和次任务。

# 缺失信息处理

优先识别：基材、前处理方式、施工方式、干膜厚度、服役环境、目标寿命、固化条件、VOC限值、成本上限和目标性能。

最多追问三个真正影响方案排序的问题。

如果用户要求直接给方案、允许助手自行假设，或者暂时无法补充信息，则不再追问；应基于明确假设继续回答，并写明：
- 当前假设
- 关键未知项
- 未知项如何影响方案排序

# 回答结构

配方开发、配方优化、原料替代或降本类问题，使用以下结构：

1.【问题拆解】
把性能目标翻译成可测量指标与验收标准。

2.【候选方案】
通常给出2–4个方向，每个注明：核心改动、预期效果、作用机理、主要风险和成本方向。
如果用户明确只要一个方案，则只完整给出一个最优先方案，不为满足格式额外罗列多个方案。

3.【机理说明】
用高分子物理、电化学、界面科学或涂膜形成机理解释方案为什么可能有效。

4.【验证计划】
说明必做实验、样品数、测试标准、周期、判据和下一轮迭代逻辑。

5.【置信度】
标注高、中或低，并说明取决于哪些未知信息。

工艺排查或涂层失效分析类问题，使用：
现象描述 → 可能根因（按概率排序） → 区分性实验 → 对应措施 → 复验方案。

简单事实查询、数量统计和单篇专利查询不强制套用上述结构，应直接回答用户问题。

# 验证计划要求

验证计划必须包含：
- 基准样、空白对照和候选样
- 每组建议至少2–3个重复样，除非用户说明只是快速筛选
- 区分单因素实验和组合实验
- 明确测试周期、失效判据和下一轮迭代决策
- 不允许只给测试名称而不给判据逻辑

# 标准与测试边界

引用测试方法时优先使用准确的标准编号，例如ISO 12944、ISO 9227、ISO 4628、ASTM B117、ASTM D3359、ASTM D4587、GB/T 1720和GB/T 1771；无法确认编号或版本时不要猜测，应明确说明需核对现行版本。

引用标准时必须说明：
- 标准适用对象
- 不适用或容易误用的边界
- 测试能够证明什么
- 测试不能证明什么

ASTM B117、ISO 9227和GB/T 1771主要用于加速对比筛选，不能单独等同于真实户外服役寿命。

ISO 12944相关判断必须结合基材、表面处理、涂层体系、干膜厚度和实际服役环境。

# 行为准则

- 不虚构实测值、专利数据、页码、标准条款或法规要求。
- 没有依据的数值应说明“需实验确定”；可以给出典型范围，但必须标注为行业经验值，而非该体系实测值。
- 可以提出预测配方，但必须标注为研发起始方案。
- 不给出不做实验就能定案的结论；输出候选方案、优先级、风险和验证路径。
- 不说“这样一定能解决”，应说“优先验证”“可能改善”或“需通过实验确认”。
- 跨体系迁移必须说明适用边界，例如溶剂型经验不一定适用于水性体系，环氧规律不一定适用于聚氨酯体系。
- 专利实施例是技术证据和研发参考，不等同于成熟商品配方。
- 不把小试结果直接外推到量产，必须提示施工窗口、批次稳定性、储存稳定性和放大验证风险。
- 涉及REACH、VOC限值、危化品分类或防污剂管控时，提示以最新法规、SDS和企业EHS审核为准。
- 涉及异氰酸酯、有机溶剂或重金属颜料时，提示相应防护、通风、废弃物处置和合规要求。
- 自主方案使用化学类别、结构特征和关键指标描述原料，不以具体供应商品牌作为必要条件。
- 引用专利证据时可以保留原文商品名，但应同时说明其化学类别；不得把专利商品名变成无依据的采购推荐。
- 用户要一个就给一个；用户要求更多时继承已有范围和分页，不重复返回同一批候选。

# 语言

使用中文和涂料行业习惯表达，例如附着力、盐雾时长、固含量、玻璃化转变温度、颜基比、PVC/CPVC、干膜厚度、交联密度、屏蔽性、阴极剥离和水汽透过率。

面向工程师，优先给结论、依据、风险和验证路径，不做与问题无关的背景铺垫。
"""
    count_statements = aggregate_count_statements(packet)
    if count_statements:
        packet = {**packet, "authoritative_count_statements": count_statements}
    packet = compact_packet_for_answer(packet, max_packet_bytes)
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "Dialogue Memory Packet:\n"
            + json.dumps(packet, ensure_ascii=False)
            + "\n\nCurrent user message:\n"
            + question,
        },
    ]


def scope_clarification_answer(packet: dict[str, Any]) -> str | None:
    resolution = packet.get("last_scope_resolution") if isinstance(packet, dict) else None
    if not isinstance(resolution, dict) or resolution.get("scope_action") != "clarify":
        return None
    doc_ids = ", ".join(resolution.get("doc_ids") or [])
    if not doc_ids:
        return "这里需要先确认一下：你是要在当前限定范围内比较，还是要切到全库/所有专利里比较？"
    return (
        f"这里需要先确认一下：你是要在当前 {doc_ids} 这篇里比较，"
        "还是要切到全库/所有专利里比较？我先不调用 KG，避免把当前篇和全库结果混在一起。"
    )


def local_mock_answer(question: str, packet: dict[str, Any]) -> str:
    packet = project_model_packet(packet)
    clarification = scope_clarification_answer(packet)
    if clarification:
        return clarification
    bullets = []
    for mem in packet.get("relevant_memories", [])[:4]:
        bullets.append(f"- {mem.get('type')}: {mem.get('content')}")
    return (
        "【本地 mock 回复：未检测到 DeepSeek/DashScope/OpenAI-compatible API key】\n\n"
        "我会根据当前 Dialogue Memory Packet 回答。当前命中的记忆包括：\n"
        + ("\n".join(bullets) if bullets else "- 暂时没有命中的长期记忆。")
        + f"\n\n你的问题是：{question}\n\n"
        "这个 demo 已经保存了本轮 raw turn、生成 observation，并会在右侧图谱中显示记忆节点。"
    )


def sanitize_customer_answer(answer: str) -> str:
    """Keep implementation details out of the customer-facing chat surface."""
    text = str(answer or "")
    replacements = {
        "`kg.sql_aggregate`": "结构化统计结果",
        "kg.sql_aggregate": "结构化统计结果",
        "`kg.hybrid_search`": "候选检索结果",
        "kg.hybrid_search": "候选检索结果",
        "`kg.doc_field_scan`": "单篇证据扫描结果",
        "kg.doc_field_scan": "单篇证据扫描结果",
        "`kg.expand_hyperedge_multihop`": "证据展开结果",
        "kg.expand_hyperedge_multihop": "证据展开结果",
        "`tool_observations`": "本轮工具结果",
        "tool_observations": "本轮工具结果",
        "`Dialogue Memory Packet`": "当前上下文",
        "Dialogue Memory Packet": "当前上下文",
        "`System Prompt`": "系统指令",
        "System Prompt": "系统指令",
        "application_family": "应用领域",
        "target=formulation": "统计对象=配方",
        "target=test_method": "统计对象=测试方法",
        "property=corrosion_protection": "性能属性=corrosion_protection",
        "filter=": "筛选条件=",
        "distinct_count=": "去重数量=",
        "matched_hyperedges=": "匹配记录数=",
        # Routing-state internals — never surface these field names to the customer.
        "last_scope_resolution.scope_action": "",
        "last_scope_resolution": "",
        "scope_resolution": "",
        "scope_action=none": "",
        "scope_action=clarify": "",
        "scope_action=use_active": "",
        "scope_action=use_history": "",
        "scope_action=set_new": "",
        "scope_action=clear_global": "",
        "scope_action": "",
        "active_doc_scope": "当前文档",
        "scope_history": "",
        "scope_policy": "",
        "policy=hard": "",
        "qwen-json-router": "",
        "llm-json-router": "",
        "fallback_contextual_followup_preferred": "",
        "fallback_aggregate_preferred": "",
        "fallback_keywords_after_low_confidence_qwen": "",
        "fallback_keywords_after_qwen_error": "",
        "fallback_direct_no_tool": "",
        "fallback_keywords": "",
        "explicit_hyperedge_object_id": "",
        "needs_tools": "",
        "plan_type": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def structured_tool_fallback_answer(packet: dict[str, Any]) -> str:
    observations = packet.get("tool_observations") if isinstance(packet, dict) else []
    for observation in reversed(observations or []):
        if not isinstance(observation, dict):
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        status = str(result.get("status") or observation.get("status") or "").casefold()
        unsupported = result.get("unsupported_constraints") or []
        if status == "unsupported" or unsupported:
            fields = "、".join(str(item) for item in unsupported) or "当前请求条件"
            return f"本轮检索已完成能力校验，但当前工具还不能应用：{fields}。我没有用全库结果冒充筛选结果。"
        if observation.get("tool") == "kg.sql_aggregate" and status in {"ok", "empty"}:
            summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
            count = summary.get("total_count")
            if count is None:
                count = summary.get("distinct_count")
            if count is None:
                count = summary.get("matched_doc_count")
            if count is None:
                count = summary.get("matched_hyperedges")
            unit = {"patent": "篇专利", "formulation": "个配方", "hyperedge": "条记录"}.get(
                result.get("count_unit"), "条记录"
            )
            if count is not None:
                return f"回答模型超时，但结构化统计已经完成：当前筛选口径共 {count} {unit}。"
            if status == "empty":
                return "回答模型超时，但结构化统计已经完成：当前筛选口径未命中记录。"
        if observation.get("tool") == "kg.hybrid_search" and status in {"ok", "empty"}:
            exact_items = result.get("exact_items")
            if not isinstance(exact_items, list):
                exact_items = result.get("items") or []
            doc_ids = list(dict.fromkeys(
                str(item.get("doc_id")) for item in exact_items
                if isinstance(item, dict) and item.get("doc_id")
            ))
            if doc_ids:
                return "回答模型超时，但候选检索已经完成。精确命中的专利包括：" + "、".join(doc_ids[:10]) + "。"
            adjacent = result.get("adjacent_items") or []
            if adjacent:
                adj_ids = []
                for row in adjacent:
                    item = row.get("item") if isinstance(row, dict) else None
                    if isinstance(item, dict) and item.get("doc_id"):
                        adj_ids.append(str(item["doc_id"]))
                adj_ids = list(dict.fromkeys(adj_ids))
                if adj_ids:
                    return (
                        "回答模型超时，但候选检索已经完成：精确口径未命中；"
                        "仅有相邻证据（未计入精确结果）：" + "、".join(adj_ids[:10]) + "。"
                    )
            return "回答模型超时，但候选检索已经完成：当前筛选口径未命中记录。"
    return "回答模型本轮超时，且没有可安全渲染的结构化工具结果。"


def retry_compact_answer(question: str, packet: dict[str, Any], cfg: dict[str, Any]) -> str:
    retry_budget = _int_env("LLM_MODEL_PACKET_RETRY_MAX_BYTES", 256 * 1024)
    body = build_provider_body(
        cfg,
        build_model_messages(question, packet, max_packet_bytes=retry_budget),
        temperature=0.2,
        max_tokens=cfg["max_output_tokens"],
        stream=False,
    )
    req = urllib.request.Request(
        provider_messages_url(cfg),
        data=json.dumps(body).encode("utf-8"),
        headers=provider_headers(cfg),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=max(1, int(cfg.get("stream_read_timeout_seconds") or 60))) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return sanitize_customer_answer(extract_provider_text(payload, cfg))


def stream_qwen(
    question: str,
    packet: dict[str, Any],
    *,
    diag_request_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    clarification = scope_clarification_answer(packet)
    cfg = provider_config()
    model = cfg["model"]
    if clarification:
        yield {
            "type": "delta",
            "content": sanitize_customer_answer(clarification),
            "provider": "scope-resolver",
            "model": model,
        }
        yield {"type": "done", "provider": "scope-resolver", "model": model}
        return
    api_key = cfg["api_key"]
    base_url = cfg["base_url"]
    if not api_key:
        answer = sanitize_customer_answer(local_mock_answer(question, packet))
        for idx in range(0, len(answer), 64):
            yield {
                "type": "delta",
                "content": answer[idx : idx + 64],
                "provider": "local-mock",
                "model": "mock-memory-demo",
            }
        yield {"type": "done", "provider": "local-mock", "model": "mock-memory-demo"}
        return

    messages = build_model_messages(question, packet)
    body = build_provider_body(
        cfg,
        messages,
        temperature=0.4,
        max_tokens=cfg["max_output_tokens"],
        stream=True,
    )
    req = urllib.request.Request(
        provider_messages_url(cfg),
        data=json.dumps(body).encode("utf-8"),
        headers=provider_headers(cfg),
        method="POST",
    )
    emitted = False
    plain_lines: list[str] = []
    started_at = time.monotonic()
    first_content_at: float | None = None
    last_content_at = started_at
    timeout_reason: str | None = None
    # observation-only; never raises. When A/B thread diag_request_id down, all three
    # layers share one id (robust correlation); else auto-id + thread_id/ts fallback.
    diag = StreamDiagnostics(request_id=diag_request_id, conversation_id="", layer="core.answering.stream_qwen")
    diag.event(
        "upstream_read_start",
        packet_bytes=len(json.dumps(messages, ensure_ascii=False).encode("utf-8")),
        packet_limit_bytes=_int_env("LLM_MODEL_PACKET_MAX_BYTES", 512 * 1024),
    )
    try:
        initial_timeout = min(
            max(1, int(cfg.get("stream_read_timeout_seconds") or 60)),
            max(1, int(cfg.get("stream_first_token_timeout_seconds") or 45)),
        )
        with urllib.request.urlopen(req, timeout=initial_timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    diag.line_event("empty_line")
                    timeout_reason = stream_timeout_reason(
                        now=time.monotonic(),
                        started_at=started_at,
                        first_content_at=first_content_at,
                        last_content_at=last_content_at,
                        cfg=cfg,
                    )
                    if timeout_reason:
                        break
                    continue
                if not line.startswith("data:"):
                    diag.line_event("non_data")
                    plain_lines.append(line)
                    timeout_reason = stream_timeout_reason(
                        now=time.monotonic(),
                        started_at=started_at,
                        first_content_at=first_content_at,
                        last_content_at=last_content_at,
                        cfg=cfg,
                    )
                    if timeout_reason:
                        break
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    diag.line_event("data_done")
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    diag.line_event("data_unparseable")
                    continue
                content = extract_stream_delta_text(payload, cfg)
                diag.line_event("data_content" if content else "data_no_content")
                if content:
                    now = time.monotonic()
                    if first_content_at is None:
                        first_content_at = now
                        set_response_socket_timeout(resp, int(cfg.get("stream_read_timeout_seconds") or 60))
                    last_content_at = now
                    emitted = True
                    for unit in stream_delta_units(sanitize_customer_answer(content), cfg):
                        yield {
                            "type": "delta",
                            "content": unit,
                            "provider": provider_label(cfg),
                            "model": payload.get("model") or model,
                        }
                timeout_reason = stream_timeout_reason(
                    now=time.monotonic(),
                    started_at=started_at,
                    first_content_at=first_content_at,
                    last_content_at=last_content_at,
                    cfg=cfg,
                )
                if timeout_reason:
                    break
        if timeout_reason:
            diag.event(
                "upstream_timeout",
                kind=timeout_reason,
                emitted=emitted,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
            retry_answer = ""
            if not emitted:
                try:
                    retry_answer = retry_compact_answer(question, packet, cfg)
                    diag.event("compact_retry_succeeded", packet_limit_bytes=_int_env("LLM_MODEL_PACKET_RETRY_MAX_BYTES", 256 * 1024))
                except Exception as exc:  # noqa: BLE001
                    diag.event("compact_retry_failed", error=str(exc))
            content = retry_answer or structured_tool_fallback_answer(packet)
            yield {"type": "delta", "content": content, "provider": provider_label(cfg), "model": model}
            yield {"type": "done", "provider": provider_label(cfg), "model": model}
            return
        if not emitted and plain_lines:
            payload = json.loads("\n".join(plain_lines))
            content = extract_provider_text(payload, cfg)
            answer = sanitize_customer_answer(content)
            if answer:
                for unit in stream_delta_units(answer, cfg):
                    yield {
                        "type": "delta",
                        "content": unit,
                        "provider": provider_label(cfg),
                        "model": payload.get("model") or model,
                    }
        diag.event("upstream_loop_done", emitted=emitted)
        yield {"type": "done", "provider": provider_label(cfg), "model": model}
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError, json.JSONDecodeError) as exc:
        diag.event("upstream_exception", error=str(exc))
        retry_answer = ""
        if not emitted:
            try:
                retry_answer = retry_compact_answer(question, packet, cfg)
                diag.event("compact_retry_succeeded", packet_limit_bytes=_int_env("LLM_MODEL_PACKET_RETRY_MAX_BYTES", 256 * 1024))
            except Exception as retry_exc:  # noqa: BLE001
                diag.event("compact_retry_failed", error=str(retry_exc))
        yield {
            "type": "delta",
            "content": retry_answer or structured_tool_fallback_answer(packet),
            "provider": provider_label(cfg) if retry_answer else "structured-tool-fallback",
            "model": model,
        }
        yield {"type": "done", "provider": provider_label(cfg) if retry_answer else "structured-tool-fallback", "model": model}


def call_qwen(question: str, packet: dict[str, Any]) -> dict[str, Any]:
    clarification = scope_clarification_answer(packet)
    if clarification:
        cfg = provider_config()
        return {"answer": clarification, "provider": "scope-resolver", "model": cfg["model"]}
    cfg = provider_config()
    api_key = cfg["api_key"]
    base_url = cfg["base_url"]
    model = cfg["model"]
    if not api_key:
        return {
            "answer": sanitize_customer_answer(local_mock_answer(question, packet)),
            "provider": "local-mock",
            "model": "mock-memory-demo",
        }

    body = build_provider_body(
        cfg,
        build_model_messages(question, packet),
        temperature=0.4,
        max_tokens=cfg["max_output_tokens"],
        stream=False,
    )
    req = urllib.request.Request(
        provider_messages_url(cfg),
        data=json.dumps(body).encode("utf-8"),
        headers=provider_headers(cfg),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        answer = sanitize_customer_answer(extract_provider_text(payload, cfg))
        return {"answer": answer, "provider": provider_label(cfg), "model": model}
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as exc:
        return {
            "answer": f"模型 API 调用失败，已保留本地记忆链路。错误：{exc}",
            "provider": "openai-compatible-error",
            "model": model,
        }
