from __future__ import annotations

import copy
import json
import os
import time
import http.client
import socket
import threading
import queue
import re
from contextlib import contextmanager
import evidence_packet as evidence_delivery
import answer_selection
import urllib.error
import urllib.request
from typing import Any, Iterator

from demo_config import PROJECT_ROOT, ROOT
from demo_text import doc_id_lookup_key
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
        if tool in {"kg.hybrid_search", "kg.doc_field_scan"}:
            for item in result.get("items") or []:
                if isinstance(item, dict):
                    item.pop("text_preview", None)
                    item.pop("metadata", None)
                    if tool == "kg.doc_field_scan":
                        for key in ("facts", "evidence", "materials", "result", "test_condition", "baseline"):
                            item.pop(key, None)
            result["evidence_gate"] = {"status": "candidate_only"}
            continue
        if tool != "kg.expand_hyperedge_multihop":
            continue
        wrapper_status = str(observation.get("status") or "").strip().casefold()
        result_status = str(result.get("status") or "").strip().casefold()
        if wrapper_status != "ok" or result_status not in {"ok", "partial"}:
            result["failed_objects"] = list(result.get("failed_objects") or []) + [
                {"object_id": item.get("object_id"), "reason": f"expand_status:{wrapper_status}/{result_status}"}
                for item in result.get("items") or [] if isinstance(item, dict)
            ]
            result["items"] = []
            result["evidence_gate"] = {
                "status": "blocked",
                "reason": f"expand_status:{wrapper_status or 'missing'}/{result_status or 'missing'}",
                "verified_count": 0,
            }
            continue
        items = result.get("items") if isinstance(result.get("items"), list) else []
        verified = [item for item in items if _expanded_item_is_verified(item)]
        excluded = [{"object_id": item.get("object_id"), "reason": "incomplete_expansion",
                     "unresolved": copy.deepcopy(item.get("unresolved") or {})}
                    for item in items if isinstance(item, dict) and not _expanded_item_is_verified(item)]
        if excluded:
            result["failed_objects"] = list(result.get("failed_objects") or []) + excluded
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
    default_output_tokens = 131072 if model.startswith("qwen3.8-max") else 65536
    max_output_tokens = int(os.environ.get("LLM_MAX_OUTPUT_TOKENS") or os.environ.get("QWEN_MAX_OUTPUT_TOKENS") or default_output_tokens)
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
        return f"\n\n[回答因{label}被截断。前面的内容已保留，剩余部分本轮未完成。]"
    return f"模型流式响应因{label}中止，没有收到可用内容；本轮回答未完成，原问题和筛选条件未改变。"


def set_response_socket_timeout(response: Any, seconds: float) -> None:
    try:
        response.fp.raw._sock.settimeout(max(.01, seconds))
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
        coverage = result.get("classification_coverage") or {}
        warnings = result.get("warnings") or []
        scope_label = f"筛选口径（{scope}）" if applied else "全库范围（没有应用任何筛选条件）"
        if result.get("cohort_available") is False or "aggregate_cohort_unavailable_not_evidence_of_absence" in warnings:
            statements.append(f"已保持原查询条件；{scope_label}的统计数据本轮不可用，不能报告为 0 {unit}。")
            continue
        classification_incomplete = (
            result.get("classification_complete") is False
            or coverage.get("complete_for_requested_axes") is False
            or "classification_coverage_incomplete_not_evidence_of_absence" in warnings
        )
        if result.get("status") == "empty" and classification_incomplete:
            statements.append(
                f"按{scope_label}，本轮已分类记录中观察到 0 {unit}匹配。已保持原查询条件；"
                "分类覆盖不完整，缺失或未映射记录是否相关未知，可能漏检，不能据此断言相关记录不存在。"
            )
            continue
        if not applied:
            statements.append(
                f"全库范围（没有应用任何筛选条件）共 {count} {unit}。"
                "注意：这是整个知识图谱的总数，不能说成问题中某个主题词（如某个领域或基材）的数量。"
            )
        elif result.get("status") == "empty":
            total = result.get("unfiltered_total_distinct_count")
            total_part = f"全库（无筛选）共 {total} {unit}；" if total is not None else ""
            if any("vocabulary_valid" in str(w) for w in warnings):
                statements.append(
                    f"按筛选口径（{scope}）统计命中 0 {unit}。{total_part}"
                    "已保持原筛选条件；当前可查询数据在该口径下没有匹配记录。"
                )
            else:
                statements.append(
                    f"按筛选口径（{scope}）统计命中 0 {unit}。{total_part}"
                    "这只说明该过滤条件没有匹配到记录，不能据此断言知识图谱没有收录相关内容。"
                )
        else:
            statements.append(f"按筛选口径（{scope}）统计，知识图谱命中 {count} {unit}。")
    return statements


def _aggregate_success(row: dict[str, Any]) -> bool:
    result = row.get("result")
    return (
        isinstance(result, dict)
        and row.get("status") in {"ok", "empty"}
        and result.get("status", row.get("status")) in {"ok", "empty"}
        and not row.get("error") and not result.get("error")
        and not result.get("unsupported_constraints")
    )


def _aggregate_coverage_lines(result: dict[str, Any]) -> list[str]:
    coverage = result.get("classification_coverage") or {}
    lines: list[str] = []
    if coverage:
        lines.append("分类覆盖检查（不是筛选命中数；source-doc 按来源与文档的组合计数）：")
        if coverage.get("scope") == "db_cohort_before_profile_axes":
            lines.append("覆盖范围为应用分类轴筛选前的数据集合，与上面的筛选命中范围不同。")
        for key, label, unit in (
            ("source_doc_count", "来源文档", "个 source-doc"),
            ("publication_count", "公开文献", "篇去重公开文献"),
            ("unmapped_profile_count", "存在未映射字段", "个 source-doc"),
        ):
            if coverage.get(key) is not None:
                lines.append(f"{label}：{coverage[key]} {unit}。")
        axes = coverage.get("axes") or {}
        labels = {"mapped": "已映射", "partial": "部分映射", "unclassified": "未分类",
                  "missing_profile": "缺失档案", "missing_source": "缺失来源", "ambiguous_profile": "档案存在歧义"}
        for axis in coverage.get("requested_axes") or []:
            states = axes.get(axis) or {}
            values = "；".join(f"{labels.get(key, key)} {value}" for key, value in states.items())
            lines.append(f"{axis}：{values or '未提供覆盖计数'}（source-doc）。")
    incomplete = (
        result.get("classification_complete") is False
        or coverage.get("complete_for_requested_axes") is False
        or "classification_coverage_incomplete_not_evidence_of_absence" in (result.get("warnings") or [])
    )
    if incomplete:
        lines.append("请求分类轴的覆盖不完整；以上是当前筛选口径下观察到的统计结果，不代表相关专利全集。")
    elif coverage.get("complete_for_requested_axes") is True:
        lines.append("请求分类轴的覆盖检查通过；这不表示所有其他分类轴完整或知识图谱覆盖全部专利。")
    if coverage or incomplete:
        lines.append("缺失或未映射记录是否相关未知，可能漏检；缺失不证明相关或不相关，"
                     "不能据此估计漏检数量或专利密度，也不能将这些计数加到命中专利数中。")
    return lines


def _aggregate_display_value(value: Any) -> str:
    if isinstance(value, dict):
        return "；".join(f"{key}：{_aggregate_display_value(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "、".join(_aggregate_display_value(item) for item in value)
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _render_aggregate_result(result: dict[str, Any]) -> str | None:
    interpretation = result.get("query_interpretation") or {}
    coverage = result.get("classification_coverage") or {}
    if not isinstance(interpretation, dict) or not isinstance(coverage, dict):
        return None
    axes = coverage.get("axes") or {}
    if not isinstance(axes, dict) or not isinstance(coverage.get("requested_axes") or [], list):
        return None
    if any(not isinstance(states, dict) or any(type(value) is not int or value < 0 for value in states.values())
           for states in axes.values()):
        return None
    for key in ("source_doc_count", "publication_count", "unmapped_profile_count"):
        if coverage.get(key) is not None and (type(coverage[key]) is not int or coverage[key] < 0):
            return None
    intent, target = interpretation.get("intent"), interpretation.get("target")
    if intent not in {"distinct_count", "list_distinct", "group_count"} or not isinstance(target, str) or not target:
        return None
    summary, items = result.get("summary"), result.get("items")
    if not isinstance(summary, dict) or not isinstance(items, list):
        return None
    for key in ("distinct_count", "total_count", "total_distinct_docs", "matched_doc_count",
                "classified_doc_count", "unknown_doc_count", "bucket_membership_count", "group_count"):
        value = summary.get(key, result.get(key))
        if value is not None and (type(value) is not int or value < 0):
            return None
    count = summary.get("distinct_count", summary.get("total_count"))
    if type(count) is not int or count < 0:
        return None
    if result.get("status") == "empty" and (count or items):
        return None
    if any(not isinstance(item, dict) or "value" not in item
           or type(item.get("count")) is not int or item["count"] < 0
           or not isinstance(item.get("group_values") or {}, dict) for item in items):
        return None
    bucket_unit = {"patent": "篇专利（去重）", "formulation": "个配方（去重）",
                   "hyperedge": "条超边记录"}.get(result.get("count_unit"))
    if not bucket_unit:
        return None
    distinct_target = summary.get("distinct_count_unit") or target
    if not isinstance(distinct_target, str):
        return None
    for dimensions in (interpretation.get("group_by"), result.get("requested_group_by"), result.get("effective_group_by")):
        if dimensions is not None and (not isinstance(dimensions, list) or any(not isinstance(dim, str) for dim in dimensions)):
            return None
    # Composite document buckets count documents, not distinct axis labels.
    if intent == "group_count" and result.get("effective_group_by") and not summary.get("distinct_count_unit"):
        distinct_target = "doc_id"
    distinct_unit = AGGREGATE_COUNT_UNITS.get(distinct_target, f"个不同 {distinct_target} 值")
    lines = [f"当前筛选口径命中 **{count} {distinct_unit}**。"]
    previous_filters = None
    for key, label in (("requested_filters", "请求筛选"), ("effective_filters", "实际筛选")):
        filters = result.get(key, interpretation.get("filters") or {})
        if not isinstance(filters, dict):
            return None
        applied = {key: value for key, value in filters.items()
                   if key != "qa_policy" and value not in (None, "", [], {})}
        if applied != previous_filters:
            lines.append(label + "：" + (_aggregate_display_value(applied) if applied else "无用户筛选条件（全库口径）"))
        previous_filters = applied
    previous_dimensions = None
    for key, label in (("requested_group_by", "请求分组"), ("effective_group_by", "实际分组")):
        dimensions = result.get(key, interpretation.get("group_by") or [])
        if dimensions and dimensions != previous_dimensions:
            lines.append(label + "：" + _aggregate_display_value(dimensions))
        previous_dimensions = dimensions
    matched_docs = summary.get("total_distinct_docs", summary.get("matched_doc_count"))
    if matched_docs is not None and (distinct_target != "doc_id" or matched_docs != count):
        lines.append(f"匹配专利：{matched_docs} 篇（去重）。")
    for key, label in (
        ("classified_doc_count", "已分类专利"), ("unknown_doc_count", "分类未知专利"),
    ):
        value = summary.get(key, result.get(key))
        if value is not None:
            if type(value) is not int or value < 0:
                return None
            lines.append(f"{label}：{value} 篇（去重）。")
    if intent == "group_count":
        membership = summary.get("bucket_membership_count", result.get("bucket_membership_count"))
        if membership is not None:
            lines.append(f"分组成员关系：{membership}。")
        lines.append("分组可重叠，不能将分组数相加当作去重专利数。")
        total = summary.get("group_count")
        if type(total) is int and total > len(items):
            lines.append(f"已返回 {len(items)}/{total} 个分组，分组清单不完整。")
        else:
            lines.append(f"已返回 {len(items)} 个分组。")
    elif intent == "list_distinct":
        lines.append(f"已返回 {len(items)} 条清单记录；目标去重数量为 {count}，返回条数不等于去重数量。")
    if result.get("truncated") or result.get("has_more"):
        lines.append("工具标记结果尚有未返回部分，本清单不完整。")
    displayed_items = items if intent in {"group_count", "list_distinct"} else []
    if displayed_items:
        dimensions = result.get("effective_group_by") or interpretation.get("group_by") or []
        dimensions = list(dict.fromkeys([*dimensions, *(key for item in items for key in (item.get("group_values") or {}))]))
        dimensions = dimensions or [target]
        lines.extend(["", "| " + " | ".join(_aggregate_display_value(dim) for dim in dimensions)
                      + f" | 数量（{bucket_unit}） |", "| " + " | ".join(["---"] * (len(dimensions) + 1)) + " |"])
        for item in displayed_items:
            group = item.get("group_values") or {dimensions[0]: item["value"]}
            lines.append("| " + " | ".join(_aggregate_display_value(group.get(dim, "未提供")) for dim in dimensions)
                         + f" | {item['count']} |")
    for item in displayed_items:
        unknown_year = (item.get("group_values") or {}).get("publication_year") == "unknown_year" or item["value"] == "unknown_year"
        if intent == "group_count" and not unknown_year and not item.get("doc_ids"):
            continue
        # Examples establish bucket membership only, never formulation/test/page claims.
        docs = {}
        for example in item.get("examples") or []:
            if isinstance(example, dict) and isinstance(example.get("doc_id"), str) and example["doc_id"].strip():
                doc = example["doc_id"].strip()
                docs.setdefault(doc_id_lookup_key(doc) or doc.casefold(), doc)
        for doc in item.get("doc_ids") or []:
            if isinstance(doc, str) and doc.strip():
                docs.setdefault(doc_id_lookup_key(doc) or doc.casefold(), doc)
        doc_count = item.get("doc_count")
        if docs or (intent == "group_count" and doc_count is not None):
            lines.append("\n" + _aggregate_display_value(item.get("group_values") or item["value"]))
            if type(doc_count) is int and doc_count >= 0:
                completeness = "完整" if len(docs) == doc_count else "不完整"
                lines.append(f"文档清单：{len(docs)}/{doc_count}（{completeness}）：" + "、".join(docs.values()))
            else:
                lines.append("返回的文档身份（完整性未知）：" + "、".join(docs.values()))
    if result.get("status") == "empty":
        incomplete = result.get("classification_complete") is False or (
            result.get("classification_coverage") or {}).get("complete_for_requested_axes") is False
        incomplete = incomplete or "classification_coverage_incomplete_not_evidence_of_absence" in (result.get("warnings") or [])
        lines.append("已保持原筛选条件；" + ("本轮已分类记录中未观察到匹配，不能断言相关记录不存在。" if incomplete
                     else "当前可查询数据在该口径下没有匹配记录，不表示其他口径或未收录数据中不存在。"))
    lines.extend(_aggregate_coverage_lines(result))
    return "\n".join(lines)


def structured_aggregate_answer(packet: dict[str, Any]) -> str | None:
    """Deliver only current, successful aggregate receipts without model inference."""
    rows = packet.get("tool_observations") or []
    if not isinstance(rows, list):
        return None
    current_turn = next((turn.get("turn_id") for turn in reversed(packet.get("recent_turns") or [])
                         if isinstance(turn, dict) and turn.get("role") == "user"), None)
    rendered: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return None
        if current_turn and row.get("turn_id") != current_turn:
            return None
        if row.get("tool") == "kg.lookup_vocabulary":
            if _aggregate_success(row):
                continue
            result = row.get("result") or {}
            if not isinstance(result, dict):
                return None
            # A prior unsupported lookup is ancillary only after its own correction succeeds.
            resolved = row.get("status") == "unsupported" and result.get("status") == "unsupported" and any(
                later.get("tool") == "kg.lookup_vocabulary" and _aggregate_success(later)
                and (not result.get("query") or later["result"].get("query") == result["query"])
                and (later["result"].get("parameter_correction") or {}).get("unsupported_receipt") == result
                for later in rows[index + 1:] if isinstance(later, dict)
            )
            if resolved:
                continue
            return None
        if row.get("tool") != "kg.sql_aggregate" or not _aggregate_success(row):
            return None
        result = row["result"]
        if result.get("cohort_available") is False or "aggregate_cohort_unavailable_not_evidence_of_absence" in (result.get("warnings") or []):
            return None
        text = _render_aggregate_result(result)
        if text is None:
            return None
        rendered.append(text)
    return "\n\n".join(rendered) if rendered else None


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
    # Expanded records and aggregate dimensions are semantic input, not previews.
    if copied.get("tool") != "kg.hybrid_search":
        if result.get("exact_items") == result.get("items"):
            result.pop("exact_items", None)
        copied["result"] = result
        return copied
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
                "group_values", "classification", "dimension", "definition", "aliases", "roles",
                "canonical_ids", "vocabulary_scope",
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
    if "exact_items" in result:
        exact_ids = {str(row.get("object_id")) for row in result["exact_items"]}
        result["exact_items"] = [row for row in compact_items if str(row.get("object_id")) in exact_ids]
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
    budget = _int_env("LLM_MODEL_PACKET_MAX_BYTES", 512 * 1024) if max_bytes is None else max_bytes
    if budget < 0:
        raise ValueError("packet byte budget must be nonnegative")
    projected = project_model_packet(packet)
    count_statements = aggregate_count_statements(projected)
    if count_statements:
        projected["authoritative_count_statements"] = count_statements
    projected["tool_observations"] = gate_tool_observations_for_answer(projected.get("tool_observations"))
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
    projected = evidence_delivery.pack_evidence(projected)
    while budget > 0 and evidence_delivery.json_size(projected) > budget:
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
    _prepared: bool = False,
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
        "Classification results describe source document labels, not proof that every sample has a property. "
        "Read classification_complete and classification_coverage: if coverage is incomplete, report the observed "
        "classified matches and explain the missing or unmapped coverage. Zero observed matches is then NOT proof "
        "of absence. If cohort_available is false, the statistic was unavailable, not zero. Never substitute a "
        "different market, function, layer, form, process or sample constraint. Preserve all group_values dimensions; "
        "bucket memberships can exceed distinct patents for multi-valued classifications.\n"
        "Classification coverage counts are source-doc memberships, not unique patents: the same publication "
        "may occur in multiple sources. Do not relabel source_doc_count, unmapped_profile_count or per-axis "
        "missing/unmapped counts as unique patent counts or add them to classified matches. "
        "Missing or unmapped classification proves neither relevance nor irrelevance to the requested topic. "
        "Say their relevance is unknown and relevant records may have been missed; do not assert that "
        "unmapped records are actually relevant, that all are irrelevant, or estimate missed relevant patents "
        "from the missing/unmapped counts.\n"
        "RESPECT THE COUNT THE USER ASKED FOR. When no display count is specified, show up to three distinct samples. "
        "When the user asks for one, focus on one; this does not reduce the evidence reviewed. Honor other explicit "
        "counts subject to verified evidence. Identify samples by source, patent and sample_id, falling back to context_id. "
        "Multiple performance hyperedges of one sample are not multiple formulations. Performance-only or system-only "
        "records must be labeled as such, not presented as complete formulations. Explain insufficient coverage without "
        "inventing missing samples. This run organizes evidence, not a systematic cross-formulation comparison. "
        "facts_refs and evidence_refs resolve into the source_records registries; use their full records and quotes. "
        "Preserve amounts, units, controls and test conditions; missing data remains unknown. "
        "evidence_delivery lists excluded or unprocessed objects; never claim those were fully reviewed. "
        "If the user asks \"多少片/几篇\" (how many "
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
        "知识图谱没有收录/不存在/0篇收录. Mention unfiltered_total_distinct_count when present and preserve the "
        "original conditions without asking the user to rephrase or broaden them. If instead the warnings say the filters are vocabulary-valid "
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
        "kg.doc_field_scan is a doc-scoped candidate locator, not full evidence. Only its following verified "
        "kg.expand_hyperedge_multihop records support formulation, page, test and result claims. "
        "Missing fields in scan previews do not prove missing source data. If expansion fails, report that "
        "coverage was not verified, never claim the patent has no formulation or measured values. "
        "A field scan returning empty must not broaden the active patent scope.\n"
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
    packet = copy.deepcopy(packet) if _prepared else compact_packet_for_answer(packet, max_packet_bytes)
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "Dialogue Memory Packet:\n"
            + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
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


def _display_limit(question: str) -> int:
    """Read an explicit display quantity only; never changes retrieval or filters."""
    match = re.search(r"(?:给我|提供|展示|列出|列|give(?: me)?|show|list)\s*(\d+|one|two|three|一|两|二|三|四|五|六|七|八|九|十)(?![一二三四五六七八九十百])", question, re.IGNORECASE)
    if not match:
        return 3
    value = match.group(1).casefold()
    words = {"one": 1, "two": 2, "three": 3, "一": 1, "两": 2, "二": 2, "三": 3,
             "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return max(1, min(int(value) if value.isdigit() else words[value], 30))


def _display_groups(rows: list[dict], limit: int, *, summaries: bool = False) -> list[list[dict]]:
    groups: dict[tuple, list] = {}
    for row in rows:
        key = tuple(row["sample_key"]) if summaries else evidence_delivery.sample_key(row)
        groups.setdefault(key, []).append(row)
    return list(groups.values())[:limit]


def structured_tool_fallback_answer(packet: dict[str, Any], question: str = "") -> str:
    prepared = compact_packet_for_answer(packet)
    observations = prepared.get("tool_observations") or []
    for observation in observations:
        if observation.get("tool") == "kg.expand_hyperedge_multihop":
            for item in observation.get("result", {}).get("items", []):
                for kind in ("facts", "evidence"):
                    item[kind] = [prepared["source_records"][kind][ref] for ref in item.get(kind + "_refs", [])]
    verified = [item for observation in observations or [] if observation.get("tool") == "kg.expand_hyperedge_multihop"
                for item in observation.get("result", {}).get("items", []) if _expanded_item_is_verified(item)]
    if verified:
        lines = ["回答模型未完成，但以下原始结构化记录已成功展开；这是证据回退结果，不是完整分析："]
        for group in _display_groups(verified, _display_limit(question)):
            identity = evidence_delivery.sample_key(group[0])
            lines.append(f"\n{group[0].get('doc_id')} / {identity[-1]}（按样品合并的原始记录，完整配方覆盖需核对字段）：")
            lines.append(json.dumps([{k: item[k] for k in ("hyperedge_summary", "facts", "evidence") if k in item} for item in group], ensure_ascii=False))
        return "\n".join(lines) + _coverage_note(prepared.get("evidence_delivery") or {})
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
    # Retained entrypoint for older callers; retry never removes current evidence.
    return _request_complete_text(build_model_messages(question, packet), cfg)


def _request_complete_text(messages: list[dict[str, str]], cfg: dict[str, Any], *, deadline: float | None = None) -> str:
    started = time.monotonic()
    stop = min(deadline or float("inf"), started + int(cfg.get("stream_total_timeout_seconds") or 180))
    remaining = stop - started
    if remaining <= 0:
        raise TimeoutError("batch_deadline")
    body = build_provider_body(
        cfg, messages, temperature=0.2, max_tokens=cfg["max_output_tokens"], stream=True,
    )
    req = urllib.request.Request(
        provider_messages_url(cfg),
        data=json.dumps(body).encode("utf-8"),
        headers=provider_headers(cfg),
        method="POST",
    )
    content, plain = [], []
    complete = False
    first_deadline = min(stop, started + int(cfg.get("stream_first_token_timeout_seconds") or 45))
    with _open_provider_response(req, first_deadline) as resp:
        timer = _response_deadline_timer(resp, first_deadline)
        try:
            for raw_line in resp:
                now = time.monotonic()
                if now >= stop or (not content and now >= first_deadline):
                    raise TimeoutError("provider_deadline")
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line == "data: [DONE]":
                    complete = True
                    break
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    complete = _provider_completed(data) or complete
                    delta = extract_stream_delta_text(data, cfg)
                    if delta:
                        if not content:
                            timer.cancel()
                            timer = _response_deadline_timer(resp, stop)
                        content.append(delta)
                elif line and not line.startswith((":", "event:")):
                    plain.append(line)
                next_stop = stop if content else first_deadline
                set_response_socket_timeout(resp, min(float(cfg.get("stream_read_timeout_seconds") or 60), next_stop - time.monotonic()))
        finally:
            timer.cancel()
    if time.monotonic() >= stop:
        raise TimeoutError("provider_deadline")
    if not content and plain:
        payload = json.loads("\n".join(plain))
        _provider_completed(payload)
        content = [extract_provider_text(payload, cfg)]
        complete = True
    if content and not complete:
        raise http.client.IncompleteRead(b"", None)
    answer = "".join(content).strip()
    if not answer:
        raise ValueError("empty_provider_answer")
    return answer


@contextmanager
def _open_provider_response(request: urllib.request.Request, header_deadline: float):
    """Cancel the actual connection while HTTP headers are still being read."""
    connections = []
    expired = threading.Event()

    def cancel():
        expired.set()
        for connection in connections:
            for sock in (connection.sock, getattr(connection, "deadline_socket", None)):
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

    def factory(base):
        def create(*args, **kwargs):
            connection = base(*args, **kwargs)
            connections.append(connection)
            def connect_before_deadline(address, timeout=None, source_address=None):
                if expired.is_set():
                    raise TimeoutError("provider_header_deadline")
                resolved = queue.Queue(maxsize=1)
                # OS DNS cannot be cancelled; the read-only daemon cannot open a
                # late connection. Only this deadline-bound caller connects.
                def resolve():
                    try:
                        resolved.put((socket.getaddrinfo(*address, 0, socket.SOCK_STREAM), None))
                    except Exception as exc:
                        resolved.put((None, exc))
                threading.Thread(target=resolve, daemon=True).start()
                try:
                    addresses, error = resolved.get(timeout=max(.001, header_deadline - time.monotonic()))
                except queue.Empty:
                    raise TimeoutError("provider_dns_deadline") from None
                if error:
                    raise error
                last_error = OSError("DNS returned no connection addresses")
                for family, kind, proto, _, endpoint in addresses:
                    if expired.is_set() or time.monotonic() >= header_deadline:
                        raise TimeoutError("provider_header_deadline")
                    sock = socket.socket(family, kind, proto)
                    connection.deadline_socket = sock
                    try:
                        sock.settimeout(max(.001, header_deadline - time.monotonic()))
                        if source_address:
                            sock.bind(source_address)
                        sock.connect(endpoint)
                        if expired.is_set() or time.monotonic() >= header_deadline:
                            raise TimeoutError("provider_header_deadline")
                        sock.settimeout(max(.001, header_deadline - time.monotonic()))
                        return sock
                    except OSError as exc:
                        sock.close()
                        last_error = exc
                raise last_error
            connection._create_connection = connect_before_deadline
            return connection
        return create

    class HTTP(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(factory(http.client.HTTPConnection), req)

    class HTTPS(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(factory(http.client.HTTPSConnection), req, context=self._context)

    timer = threading.Timer(max(.001, header_deadline - time.monotonic()), cancel)
    timer.daemon = True
    timer.start()
    response = None
    try:
        response = urllib.request.build_opener(HTTP(), HTTPS()).open(request, timeout=max(.001, header_deadline - time.monotonic()))
        timer.cancel()
        if expired.is_set() or time.monotonic() >= header_deadline:
            raise TimeoutError("provider_header_deadline")
        yield response
    finally:
        timer.cancel()
        if response is not None:
            response.close()


def _provider_completed(payload: dict[str, Any]) -> bool:
    reasons = [choice.get("finish_reason") for choice in payload.get("choices") or []]
    reasons += [payload.get("stop_reason"), (payload.get("delta") or {}).get("stop_reason")]
    if any(reason in {"length", "max_tokens", "content_filter"} for reason in reasons):
        raise ValueError("provider_incomplete_termination")
    return payload.get("type") == "message_stop" or any(reason in {"stop", "end_turn", "stop_sequence"} for reason in reasons)


def _response_deadline_timer(response: Any, deadline: float) -> threading.Timer:
    # A read timeout is idle-only; shutdown also interrupts a slowly dripped line.
    def interrupt_read():
        try:
            response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass
    timer = threading.Timer(max(.001, deadline - time.monotonic()), interrupt_read)
    timer.daemon = True
    timer.start()
    return timer


def _validate_batch_summary(text: str, batch: dict[str, Any], reference_aliases: dict | None = None) -> list[dict[str, Any]]:
    payload = json.loads(text)
    rows = payload.get("summaries") if isinstance(payload, dict) else None
    expected = {str(i["object_id"]): i for i in evidence_delivery.expanded_items(batch)}
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("summary_coverage_mismatch")
    found = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("object_id") not in expected or row["object_id"] in found:
            raise ValueError("summary_object_mismatch")
        source = expected[row["object_id"]]
        if not isinstance(row.get("summary"), str) or not row["summary"].strip():
            raise ValueError("empty_sample_summary")
        for kind in ("facts", "evidence"):
            refs = row.get(kind + "_refs")
            if reference_aliases and isinstance(refs, list) and all(isinstance(ref, str) for ref in refs):
                row[kind + "_refs"] = refs = [reference_aliases[kind].get(ref, ref) for ref in refs]
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in source.get(kind + "_refs", []) for ref in refs):
                raise ValueError("summary_reference_mismatch")
            if set(refs) != set(source.get(kind + "_refs") or []):
                raise ValueError("summary_incomplete_reference_coverage")
        # Identity is copied from the actual source, not trusted to the model.
        row["sample_key"] = list(evidence_delivery.sample_key(source))
        row["doc_id"] = source.get("doc_id")
        row["validation"] = "reference_coverage_only_not_independent_semantic_verification"
        if reference_aliases:
            row["reference_aliases"] = {kind: {short: ref for short, ref in mapping.items()
                                               if ref in row.get(kind + "_refs", [])}
                                        for kind, mapping in reference_aliases.items()}
        found.add(row["object_id"])
    return rows


def _coverage_note(receipt: dict[str, Any]) -> str:
    failed = receipt.get("failed_objects") or []
    if not failed:
        return ""
    listing = "；".join(f"{r.get('object_id') or '上下文'}（{r['reason']}）" for r in failed)
    return "\n\n本轮未完整覆盖以下记录，不将其作为已验证结论：" + listing + "。"


def _selected_evidence_answer(question: str, prepared: dict[str, Any], cfg: dict[str, Any],
                              choices: dict, *, deadline: float | None = None, source_packet: dict | None = None) -> tuple[str, dict]:
    limit = _display_limit(question)
    model_packet = copy.deepcopy(prepared)
    model_packet["answer_selection_catalog"] = choices
    messages = build_model_messages(question, model_packet, _prepared=True)
    messages[0]["content"] += answer_selection.instructions(limit)
    receipt = {"display_limit": limit, "available_samples": len(choices), "attempts": 0,
               "displayed_samples": 0, "selected_samples": [], "validation_failures": []}
    if not choices:
        receipt["answer_outcome"] = "selection_fallback"
        return answer_selection.fallback(choices, limit), receipt
    try:
        source = source_packet if source_packet is not None else prepared
        views = answer_selection.attach_source_views(choices, source)
    except (ValueError, KeyError, TypeError) as exc:
        receipt.update(answer_outcome="source_binding_failure", failure_reason=type(exc).__name__)
        return answer_selection.fallback({}, limit), receipt
    for attempt in range(2):
        if deadline is not None and time.monotonic() >= deadline:
            receipt["validation_failures"].append("batch_deadline")
            break
        receipt["attempts"] += 1
        try:
            text = _request_complete_text(messages, cfg, deadline=deadline)
            payload = answer_selection.validate(text, choices, limit)
            receipt.update(answer_outcome="model_complete", displayed_samples=len(payload["selected_samples"]),
                selection_coverage_note=payload.get("coverage_note", ""),
                selected_samples=[{"handle": row["sample_handle"], **choices[row["sample_handle"]]}
                                  for row in payload["selected_samples"]],
                validation="sample_identity_and_reference_binding_not_semantic_entailment")
            receipt["render_mode"] = "source_fields_and_verbatim_evidence_not_model_quantitative_paraphrase"
            return answer_selection.render(payload, choices, views), receipt
        except (ValueError, KeyError, TypeError, OSError, http.client.HTTPException) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 413, 422):
                try:
                    provider_error = exc.read(4096).decode("utf-8", "replace").casefold()
                finally:
                    exc.close()
                if exc.code == 413 or any(code in provider_error for code in (
                    "context_length_exceeded", "maximum context length", "context window", "input length", "input tokens")):
                    receipt.update(answer_outcome="provider_context_exceeded")
                    return "", receipt
            error = type(exc).__name__ + ":" + str(exc)[:160]
            receipt["validation_failures"].append(error)
            if attempt == 0:
                # Keep the entire original input; do not replay invalid model prose.
                messages.append({"role": "user", "content": "Answer validation failed: " + error +
                    ". Return a corrected JSON answer using the same full evidence and catalog. No new evidence was added."})
    receipt["answer_outcome"] = "selection_fallback"
    return answer_selection.fallback(choices, limit), receipt


def _stream_selected_answer(question: str, prepared: dict[str, Any], cfg: dict[str, Any], diag_request_id=None):
    started = time.monotonic()
    yield {"type": "progress", "stage": "sample_selection", "completed": 0}
    choices = answer_selection.catalog(prepared)
    answer, selection = _selected_evidence_answer(question, prepared, cfg, choices,
        deadline=started + _int_env("LLM_EVIDENCE_BATCH_TOTAL_SECONDS", 600))
    if selection["answer_outcome"] == "provider_context_exceeded":
        # A provider token rejection triggers whole-sample batching, not deletion.
        budget = _int_env("LLM_CONTEXT_REJECT_BATCH_BYTES", 512 * 1024)
        plan = evidence_delivery.plan_batches(prepared, max(1, budget), output_tokens=cfg["max_output_tokens"])
        plan["requires_batching"] = True
        yield from _stream_batched_answer(question, prepared, plan, cfg, diag_request_id,
            deadline=started + _int_env("LLM_EVIDENCE_BATCH_TOTAL_SECONDS", 600), budget_override=max(1, budget))
        return
    receipt = copy.deepcopy(prepared.get("evidence_delivery") or {})
    receipt.update(selection)
    receipt.update(packet_bytes=evidence_delivery.json_size(prepared), batch_count=1,
        submitted_objects=[row["object_id"] for row in evidence_delivery.expanded_items(prepared)],
        elapsed_seconds=round(time.monotonic() - started, 3))
    StreamDiagnostics(request_id=diag_request_id, conversation_id="", layer="core.answering.selection").event(
        "evidence_delivery_complete", **receipt)
    provider = provider_label(cfg) if selection["answer_outcome"] == "model_complete" else "structured-tool-fallback"
    yield {"type": "delta", "content": sanitize_customer_answer(answer) + _coverage_note(receipt), "provider": provider, "model": cfg["model"]}
    yield {"type": "done", "provider": provider, "model": cfg["model"], "evidence_delivery": receipt}


def _stream_batched_answer(question: str, prepared: dict[str, Any], plan: dict[str, Any], cfg: dict[str, Any], diag_request_id: str | None = None,
                           *, deadline: float | None = None, budget_override: int | None = None):
    started = time.monotonic()
    deadline = deadline if deadline is not None else started + _int_env("LLM_EVIDENCE_BATCH_TOTAL_SECONDS", 600)
    budget = budget_override if budget_override is not None else _int_env("LLM_MODEL_PACKET_MAX_BYTES", 512 * 1024)
    receipt = copy.deepcopy(prepared.get("evidence_delivery") or {})
    receipt.update({"failed_objects": copy.deepcopy(plan["failed_objects"]), "batches": [], "covered_objects": []})
    summaries = []
    diag = StreamDiagnostics(request_id=diag_request_id, conversation_id="", layer="core.answering.evidence_batches")
    instructions = (
        "This is one evidence batch, not a final customer answer. Read every supplied item and return JSON only: "
        '{"summaries":[{"object_id":"exact source ID","summary":"Chinese factual coverage summary",'
        '"facts_refs":["source registry key"],"evidence_refs":["source registry key"]}]}. '
        "Include exactly one entry per supplied object. Preserve sample identity, complete formulation amounts/units, "
        "processing, test conditions/results and controls when present; explicitly state missing data. Cite only "
        "ALL SHORT registry keys belonging to that object, including every fact and evidence reference. "
        "Use the short handles in facts_refs/evidence_refs, not the long source_ref_map values. "
        "Do not invent values or compare performance across formulations. "
        "This is evidence organization, not new reasoning or formulation optimization."
    )
    for index, batch in enumerate(plan["batches"]):
        ids = [str(i["object_id"]) for i in evidence_delivery.expanded_items(batch)]
        entry = {"index": index, "input_objects": ids, "packet_bytes": evidence_delivery.json_size(batch), "attempts": 0}
        receipt["batches"].append(entry)
        yield {"type": "progress", "stage": "evidence_batch", "batch": index + 1, "batch_count": len(plan["batches"]), "completed": len(receipt["covered_objects"])}
        transport, aliases = evidence_delivery.batch_transport(batch, f"b{index}")
        messages = build_model_messages(question, transport, _prepared=True)
        messages[0]["content"] += "\n" + instructions
        parsed, error = None, "batch_deadline"
        for attempt in range(2):
            if time.monotonic() >= deadline:
                break
            entry["attempts"] += 1
            try:
                parsed = _validate_batch_summary(_request_complete_text(messages, cfg, deadline=deadline), batch, aliases)
                break
            except (ValueError, KeyError, TypeError, OSError, http.client.HTTPException) as exc:
                error = type(exc).__name__ + ":" + str(exc)[:160]
                entry.setdefault("validation_failures", []).append(error)
                if attempt == 0 and isinstance(exc, (ValueError, KeyError, TypeError)):
                    messages.append({"role": "user", "content": "Batch validation failed: " + error +
                        ". Correct the JSON against the same complete evidence batch. Include every object and "
                        "its full reference set using the supplied short handles. Do not omit evidence or add facts."})
        if parsed is None:
            entry["status"] = "failed"
            entry["failure_reason"] = error
            receipt["failed_objects"].extend({"object_id": oid, "reason": error} for oid in ids)
        else:
            entry["status"] = "ok"
            summaries.extend(parsed)
            receipt["covered_objects"].extend(ids)
        diag.event("evidence_batch_complete", **entry)
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
    final_packet = evidence_delivery.synthesis_context(prepared)
    final_packet.update({"evidence_delivery": receipt, "evidence_batch_summaries": summaries})
    answer = ""
    if summaries and time.monotonic() < deadline and (budget == 0 or evidence_delivery.json_size(final_packet) <= budget):
        choices = answer_selection.catalog(prepared, receipt["covered_objects"])
        answer, selection = _selected_evidence_answer(question, final_packet, cfg, choices, deadline=deadline, source_packet=prepared)
        receipt["answer_selection"] = selection
    if not answer:
        # Reference coverage does not establish semantic entailment of a summary.
        choices = answer_selection.catalog(prepared, receipt["covered_objects"])
        answer = answer_selection.fallback(choices, _display_limit(question))
        receipt["answer_outcome"] = "selection_fallback"
    diag.event("evidence_delivery_complete", **receipt)
    yield {"type": "delta", "content": sanitize_customer_answer(answer) + _coverage_note(receipt), "provider": provider_label(cfg), "model": cfg["model"]}
    yield {"type": "done", "provider": provider_label(cfg), "model": cfg["model"], "evidence_delivery": receipt}


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
    # Both packet builders isolate current turns; all delivery paths share Track A's allowlist.
    aggregate_answer = structured_aggregate_answer(project_model_packet(packet))
    if aggregate_answer is not None:
        receipt = copy.deepcopy(packet.get("evidence_delivery") or {})
        receipt.update(answer_outcome="structured_aggregate", attempts=0,
                       aggregate_observation_count=sum(row.get("tool") == "kg.sql_aggregate"
                                                       for row in packet.get("tool_observations") or []))
        yield {"type": "delta", "content": aggregate_answer, "provider": "structured-aggregate", "model": model}
        yield {"type": "done", "provider": "structured-aggregate", "model": model, "evidence_delivery": receipt}
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

    prepared = compact_packet_for_answer(packet)
    plan = evidence_delivery.plan_batches(prepared, _int_env("LLM_MODEL_PACKET_MAX_BYTES", 512 * 1024),
                                         output_tokens=cfg["max_output_tokens"])
    if plan["requires_batching"]:
        yield from _stream_batched_answer(question, prepared, plan, cfg, diag_request_id)
        return
    if list(evidence_delivery.expanded_items(prepared)):
        yield from _stream_selected_answer(question, prepared, cfg, diag_request_id)
        return
    observations = prepared.get("tool_observations") or []
    expansion_failed = any(row.get("tool") in {"kg.expand_hyperedge_multihop", "kg.doc_field_scan"}
        and (row.get("status") == "error" or (row.get("result") or {}).get("status") == "error")
        for row in observations)
    aggregate_ready = any(row.get("tool") == "kg.sql_aggregate" and row.get("status") == "ok" for row in observations)
    if expansion_failed and not aggregate_ready:
        receipt = copy.deepcopy(prepared.get("evidence_delivery") or {})
        receipt.update(answer_outcome="no_verified_expansion", attempts=0, displayed_samples=0)
        text = "本轮未能完成原始证据展开，因此暂不能核实具体配方和测试值。这不表示专利中没有这些数据。"
        yield {"type": "delta", "content": text + _coverage_note(receipt), "provider": "structured-tool-fallback", "model": model}
        yield {"type": "done", "provider": "structured-tool-fallback", "model": model, "evidence_delivery": receipt}
        return
    messages = build_model_messages(question, prepared, _prepared=True)
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
    response_timer = None
    completed = False
    attempts = 1
    outcome = "model_complete"
    failure_reason = None

    def delivery_receipt():
        receipt = copy.deepcopy(prepared.get("evidence_delivery") or {})
        receipt.update({"packet_bytes": evidence_delivery.json_size(prepared), "batch_count": 1,
                        "attempts": attempts, "answer_outcome": outcome,
                        "failure_reason": failure_reason,
                        "submitted_objects": [str(row["object_id"]) for row in evidence_delivery.expanded_items(prepared)],
                        "first_content_seconds": None if first_content_at is None else round(first_content_at - started_at, 3),
                        "elapsed_seconds": round(time.monotonic() - started_at, 3)})
        diag.event("evidence_delivery_complete", **receipt)
        return receipt

    try:
        initial_timeout = min(
            max(1, int(cfg.get("stream_read_timeout_seconds") or 60)),
            max(1, int(cfg.get("stream_first_token_timeout_seconds") or 45)),
        )
        with _open_provider_response(req, started_at + initial_timeout) as resp:
            response_timer = _response_deadline_timer(resp, started_at + initial_timeout)
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
                    completed = True
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    diag.line_event("data_unparseable")
                    continue
                completed = _provider_completed(payload) or completed
                content = extract_stream_delta_text(payload, cfg)
                diag.line_event("data_content" if content else "data_no_content")
                if content:
                    now = time.monotonic()
                    if first_content_at is None:
                        first_content_at = now
                        response_timer.cancel()
                        response_timer = _response_deadline_timer(resp, started_at + int(cfg.get("stream_total_timeout_seconds") or 180))
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
        if response_timer:
            response_timer.cancel()
        timeout_reason = timeout_reason or stream_timeout_reason(now=time.monotonic(), started_at=started_at,
            first_content_at=first_content_at, last_content_at=last_content_at, cfg=cfg)
        if timeout_reason:
            failure_reason = timeout_reason
            diag.event(
                "upstream_timeout",
                kind=timeout_reason,
                emitted=emitted,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
            retry_answer = ""
            if not emitted:
                try:
                    attempts = 2
                    retry_answer = retry_compact_answer(question, packet, cfg)
                    diag.event("full_evidence_retry_succeeded", packet_limit_bytes=_int_env("LLM_MODEL_PACKET_MAX_BYTES", 512 * 1024))
                except Exception as exc:  # noqa: BLE001
                    diag.event("full_evidence_retry_failed", error=str(exc))
            content = (retry_answer + _coverage_note(prepared.get("evidence_delivery") or {})) if retry_answer else structured_tool_fallback_answer(packet, question)
            outcome = "model_retry_complete" if retry_answer else "structured_fallback"
            provider = provider_label(cfg) if retry_answer else "structured-tool-fallback"
            yield {"type": "delta", "content": content, "provider": provider, "model": model}
            yield {"type": "done", "provider": provider, "model": model, "evidence_delivery": delivery_receipt()}
            return
        if not emitted and plain_lines:
            payload = json.loads("\n".join(plain_lines))
            _provider_completed(payload)
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
                completed = True
            else:
                raise ValueError("empty_provider_answer")
        if emitted and not completed:
            raise http.client.IncompleteRead(b"", None)
        if not emitted and not plain_lines:
            failure_reason = "empty_provider_answer"
            try:
                attempts = 2
                answer = retry_compact_answer(question, packet, cfg)
                outcome = "model_retry_complete"
            except Exception:  # same complete evidence, then a structural fallback
                answer = structured_tool_fallback_answer(packet, question)
                outcome = "structured_fallback"
            yield {"type": "delta", "content": answer, "provider": provider_label(cfg), "model": model}
        note = _coverage_note(prepared.get("evidence_delivery") or {})
        if note:
            yield {"type": "delta", "content": note, "provider": provider_label(cfg), "model": model}
        receipt = delivery_receipt()
        diag.event("upstream_loop_done", emitted=emitted, evidence_delivery=receipt)
        yield {"type": "done", "provider": provider_label(cfg), "model": model, "evidence_delivery": receipt}
    except (OSError, KeyError, ValueError, http.client.HTTPException) as exc:
        failure_reason = type(exc).__name__
        diag.event("upstream_exception", error=str(exc))
        retry_answer = ""
        if not emitted and attempts < 2:
            try:
                attempts = 2
                retry_answer = retry_compact_answer(question, packet, cfg)
                diag.event("full_evidence_retry_succeeded", packet_limit_bytes=_int_env("LLM_MODEL_PACKET_MAX_BYTES", 512 * 1024))
            except Exception as retry_exc:  # noqa: BLE001
                diag.event("full_evidence_retry_failed", error=str(retry_exc))
        outcome = "model_retry_complete" if retry_answer else "structured_fallback"
        yield {
            "type": "delta",
            "content": (retry_answer + _coverage_note(prepared.get("evidence_delivery") or {})) if retry_answer else structured_tool_fallback_answer(packet, question),
            "provider": provider_label(cfg) if retry_answer else "structured-tool-fallback",
            "model": model,
        }
        yield {"type": "done", "provider": provider_label(cfg) if retry_answer else "structured-tool-fallback", "model": model, "evidence_delivery": delivery_receipt()}
    finally:
        if response_timer:
            response_timer.cancel()


def call_qwen(question: str, packet: dict[str, Any]) -> dict[str, Any]:
    # Buffered and live endpoints share evidence delivery, budgets and retries.
    result: dict[str, Any] = {"answer": ""}
    for event in stream_qwen(question, packet):
        if event.get("type") == "delta":
            result["answer"] += event.get("content") or ""
        for field in ("provider", "model", "evidence_delivery"):
            if field in event:
                result[field] = event[field]
    return result
