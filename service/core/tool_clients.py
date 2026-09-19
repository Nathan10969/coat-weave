from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

from demo_config import (
    KG_DOC_FIELD_SCAN_TIMEOUT_SECONDS,
    KG_DOC_FIELD_SCAN_URL,
    KG_EXPAND_TIMEOUT_SECONDS,
    KG_EXPAND_TOKEN,
    KG_EXPAND_URL,
    KG_HYBRID_DEFAULT_CANDIDATE_K,
    KG_HYBRID_DEFAULT_TOP_K,
    KG_HYBRID_SEARCH_TIMEOUT_SECONDS,
    KG_HYBRID_SEARCH_URL,
    KG_SQL_AGGREGATE_TIMEOUT_SECONDS,
    KG_SQL_AGGREGATE_URL,
)
from demo_text import clamp_int, normalize_kg_aggregate_filters, normalize_kg_search_filters, normalize_string_list


def kg_lookup_vocabulary(
    query: str, *, dimension: str | None = None, limit: int = 20,
) -> dict[str, Any]:
    endpoint = os.environ.get(
        "KG_LOOKUP_VOCABULARY_URL",
        KG_HYBRID_SEARCH_URL.rsplit("/", 1)[0] + "/kg.lookup_vocabulary",
    )
    body = {"query": query, "limit": clamp_int(limit, 20, 1, 100)}
    if dimension is not None:
        body["dimension"] = dimension
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if KG_EXPAND_TOKEN:
        headers["Authorization"] = f"Bearer {KG_EXPAND_TOKEN}"
    req = urllib.request.Request(
        endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST",
    )
    timeout = clamp_int(os.environ.get("KG_LOOKUP_VOCABULARY_TIMEOUT_SECONDS"), KG_HYBRID_SEARCH_TIMEOUT_SECONDS, 1, 600)
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    payload.setdefault("tool", "kg.lookup_vocabulary")
    payload["endpoint"] = endpoint
    payload["elapsed_ms"] = round((time.time() - started) * 1000)
    return payload


def kg_expand_hyperedge_multihop(
    object_ids: list[str],
    *,
    max_context_facts: int = 20,
    max_evidence_per_item: int = 5,
    evidence_mode: str = "full",
) -> dict[str, Any]:
    body = {
        "object_ids": object_ids,
        "max_context_facts": max_context_facts,
        "max_evidence_per_item": max_evidence_per_item,
        "evidence_mode": evidence_mode,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "dialogue-memory-demo-kg-expand-tool",
    }
    if KG_EXPAND_TOKEN:
        headers["Authorization"] = f"Bearer {KG_EXPAND_TOKEN}"
    req = urllib.request.Request(
        KG_EXPAND_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=KG_EXPAND_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    payload.setdefault("tool", "kg.expand_hyperedge_multihop")
    payload["endpoint"] = KG_EXPAND_URL
    payload["elapsed_ms"] = round((time.time() - started) * 1000)
    return payload

def kg_hybrid_search(
    query: str,
    *,
    top_k: int = KG_HYBRID_DEFAULT_TOP_K,
    candidate_k: int = KG_HYBRID_DEFAULT_CANDIDATE_K,
    offset: int = 0,
    filters: dict[str, Any] | None = None,
    requested_filters: dict[str, Any] | None = None,
    unsupported_constraints: list[str] | None = None,
    route_adjustments: list[str] | None = None,
) -> dict[str, Any]:
    body = {
        "query": query,
        "top_k": top_k,
        "candidate_k": candidate_k,
        "offset": offset,
        "filters": normalize_kg_search_filters(filters),
        "requested_filters": requested_filters if isinstance(requested_filters, dict) else (filters or {}),
        "unsupported_constraints": normalize_string_list(unsupported_constraints),
        "route_adjustments": normalize_string_list(route_adjustments),
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "dialogue-memory-demo-kg-hybrid-search-tool",
    }
    if KG_EXPAND_TOKEN:
        headers["Authorization"] = f"Bearer {KG_EXPAND_TOKEN}"
    req = urllib.request.Request(
        KG_HYBRID_SEARCH_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=KG_HYBRID_SEARCH_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    payload.setdefault("tool", "kg.hybrid_search")
    payload["endpoint"] = KG_HYBRID_SEARCH_URL
    payload["elapsed_ms"] = round((time.time() - started) * 1000)
    return payload

def kg_sql_aggregate(
    *,
    intent: str,
    target: str,
    filters: dict[str, Any] | None = None,
    group_by: list[str] | None = None,
    limit: int = 50,
    include_examples: bool = True,
    requested_filters: dict[str, Any] | None = None,
    unsupported_constraints: list[str] | None = None,
    route_adjustments: list[str] | None = None,
) -> dict[str, Any]:
    body = {
        "intent": intent,
        "target": target,
        "filters": normalize_kg_aggregate_filters(filters),
        "group_by": normalize_string_list(group_by),
        "limit": clamp_int(limit, 50, 1, 200),
        "include_examples": bool(include_examples),
        "requested_filters": requested_filters if isinstance(requested_filters, dict) else (filters or {}),
        "unsupported_constraints": normalize_string_list(unsupported_constraints),
        "route_adjustments": normalize_string_list(route_adjustments),
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "dialogue-memory-demo-kg-sql-aggregate-tool",
    }
    if KG_EXPAND_TOKEN:
        headers["Authorization"] = f"Bearer {KG_EXPAND_TOKEN}"
    req = urllib.request.Request(
        KG_SQL_AGGREGATE_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=KG_SQL_AGGREGATE_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    payload.setdefault("tool", "kg.sql_aggregate")
    payload["endpoint"] = KG_SQL_AGGREGATE_URL
    payload["elapsed_ms"] = round((time.time() - started) * 1000)
    return payload

def kg_doc_field_scan(
    *,
    doc_ids: list[str],
    query: str,
    field_groups: list[str] | None = None,
    limit: int = 200,
    include_evidence: bool = True,
) -> dict[str, Any]:
    body = {
        "doc_ids": normalize_string_list(doc_ids),
        "query": str(query or ""),
        "field_groups": normalize_string_list(field_groups)
        or ["test_method", "test_condition", "property", "result", "facts", "materials", "evidence"],
        "limit": clamp_int(limit, 200, 1, 500),
        "include_evidence": bool(include_evidence),
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "dialogue-memory-demo-kg-doc-field-scan-tool",
    }
    if KG_EXPAND_TOKEN:
        headers["Authorization"] = f"Bearer {KG_EXPAND_TOKEN}"
    req = urllib.request.Request(
        KG_DOC_FIELD_SCAN_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=KG_DOC_FIELD_SCAN_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    payload.setdefault("tool", "kg.doc_field_scan")
    payload["endpoint"] = KG_DOC_FIELD_SCAN_URL
    payload["elapsed_ms"] = round((time.time() - started) * 1000)
    return payload
