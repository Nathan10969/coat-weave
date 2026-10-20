"""Stable ID helpers for KG projection records."""

from __future__ import annotations

import json
import re
from typing import Any


def _stable_key(value: Any) -> str:
    if value in (None, "", [], {}, "unknown"):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)

def _prefix(value: str) -> str | None:
    head = value.split("_", 1)[0]
    return head if head in {"MAT", "APP", "SUB", "PROP", "PROC", "TEST", "EVD", "PAT"} else None

def _looks_like_canonical(value: Any) -> bool:
    return isinstance(value, str) and _prefix(value) is not None and value.lower() != "unknown"

def _patent_id(doc_id: str) -> str:
    return f"PAT_{_safe_id(doc_id)}"

def _profile_id(doc_id: str) -> str:
    return f"PROFILE_{_safe_id(doc_id)}"

def _context_id(doc_id: str, example_id: str) -> str:
    return f"CTX_{_safe_id(doc_id)}_{_safe_id(example_id or 'unknown')}"

def _evidence_id(raw_id: str) -> str:
    if not raw_id:
        return ""
    return f"EVD_{_safe_id(raw_id)}"

def _fact_node_id(fact_id: str) -> str:
    return f"FACT_{_safe_id(fact_id)}"

def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_")

def _doc_from_unit(unit_id: str) -> str | None:
    match = re.match(r"^U_(.+?)_p\d+_b\d+", unit_id)
    return match.group(1) if match else None
