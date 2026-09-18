from __future__ import annotations

import hashlib
import re
from typing import Any

from kg_contract import load_tool_contract, normalize_filter_request, normalize_material_roles as contract_material_roles


def tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", text)
        if token.strip()
    }


def stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(parts).lower()
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]
    return f"{prefix}_{digest}"


HYPEREDGE_OBJECT_ID_PATTERNS = (
    re.compile(r"\bHE_[A-Za-z0-9_]+\b"),
    re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_:-]*::HYP_[A-Za-z0-9_:-]+\b"),
)

DOC_ID_PATTERNS = (
    re.compile(r"\bHE_([0-9]+_WO[0-9]{6,}[A-Z0-9]*)_[A-Za-z0-9_]+\b", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])([0-9]+_WO[0-9]{6,}[A-Z0-9]*)(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_])(WO[0-9]{6,}[A-Z0-9]*)(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_])(O[0-9]{6,}[A-Z][A-Z0-9]*)(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_])(WO\s*\d{4}\s*/\s*\d{6}\s*[A-Z]\d)(?![A-Za-z0-9])", re.IGNORECASE),
)

PUBLICATION_ID_PATTERN = re.compile(r"(?:^|::)(?:\d+_)?(WO\d{10}[A-Z]\d)(?:$|::)", re.IGNORECASE)


def extract_hyperedge_object_ids(text: str) -> list[str]:
    hits: list[tuple[int, str]] = []
    for pattern in HYPEREDGE_OBJECT_ID_PATTERNS:
        for match in pattern.finditer(text or ""):
            hits.append((match.start(), match.group(0).strip(".,;:，。；：)）]】")))
    out: list[str] = []
    seen: set[str] = set()
    for _, object_id in sorted(hits, key=lambda item: item[0]):
        if object_id and object_id not in seen:
            seen.add(object_id)
            out.append(object_id)
    return out


def normalize_doc_id(value: Any) -> str:
    text = str(value or "").strip().strip(".,;:，。；：)）]】")
    if not text:
        return ""
    if "::" in text:
        text = text.split("::", 1)[0]
    he_match = re.match(r"^HE_([0-9]+_WO[0-9]{6,}[A-Z0-9]*)_", text, flags=re.IGNORECASE)
    if he_match:
        return he_match.group(1).upper()
    prefixed_match = re.match(r"^([0-9]+_WO[0-9]{6,}[A-Z0-9]*)$", text, flags=re.IGNORECASE)
    if prefixed_match:
        return prefixed_match.group(1).upper()
    wo_match = re.match(r"^(WO[0-9]{6,}[A-Z0-9]*)$", text, flags=re.IGNORECASE)
    if wo_match:
        return wo_match.group(1).upper()
    missing_w_match = re.match(r"^(O[0-9]{6,}[A-Z][A-Z0-9]*)$", text, flags=re.IGNORECASE)
    if missing_w_match:
        return f"W{missing_w_match.group(1).upper()}"
    return ""


def doc_id_lookup_key(value: Any) -> str:
    """Return a lookup-only publication key without changing stored IDs."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    text = text.replace("/", "")
    segments = [segment for segment in text.split("::") if segment]
    for segment in segments or [text]:
        match = re.search(r"(?:^|_)(WO\d{10}[A-Z]\d)$", segment, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
        missing_w = re.search(r"(?:^|_)(O\d{10}[A-Z]\d)$", segment, flags=re.IGNORECASE)
        if missing_w:
            return f"W{missing_w.group(1).upper()}"
    match = PUBLICATION_ID_PATTERN.search(text)
    return match.group(1).upper() if match else ""


def extract_doc_ids(text: str) -> list[str]:
    hits: list[tuple[int, str]] = []
    for pattern in DOC_ID_PATTERNS:
        for match in pattern.finditer(text or ""):
            doc_id = normalize_doc_id(match.group(1))
            if not doc_id:
                doc_id = doc_id_lookup_key(match.group(1))
            if doc_id:
                hits.append((match.start(), doc_id))
    out: list[str] = []
    seen: set[str] = set()
    for _, doc_id in sorted(hits, key=lambda item: item[0]):
        if doc_id not in seen:
            seen.add(doc_id)
            out.append(doc_id)
    return out


def normalize_object_ids(value: Any, fallback_text: str = "") -> list[str]:
    object_ids: list[str] = []
    if isinstance(value, list):
        object_ids.extend(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, str):
        object_ids.extend(extract_hyperedge_object_ids(value))
    object_ids.extend(extract_hyperedge_object_ids(fallback_text))
    out: list[str] = []
    seen: set[str] = set()
    for object_id in object_ids:
        if object_id not in seen:
            seen.add(object_id)
            out.append(object_id)
    return out


def int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp_int(value: Any, default: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, int_or_default(value, default)))


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


_KG_CONTRACT = load_tool_contract()
KG_SEARCH_SOFT_FILTER_ALIASES: dict[str, tuple[str, ...]] = {
    key: tuple(values)
    for key, values in (_KG_CONTRACT.get("filter_aliases") or {}).items()
}
VALID_MATERIAL_ROLES = frozenset((_KG_CONTRACT.get("material_roles") or {}).get("values") or [])
MATERIAL_ROLE_ALIASES = dict((_KG_CONTRACT.get("material_roles") or {}).get("aliases") or {})


def normalize_material_roles(value: Any) -> list[str]:
    return contract_material_roles(value, _KG_CONTRACT)[0]


INVALID_EXAMPLE_KIND_FILTER_VALUES = set(_KG_CONTRACT.get("invalid_example_kind_values") or [])


def normalize_example_kind_filter(value: Any) -> list[str]:
    return [
        item
        for item in normalize_string_list(value)
        if item.strip().lower() not in INVALID_EXAMPLE_KIND_FILTER_VALUES
    ]


def first_present_filter_value(raw: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if raw.get(key) is not None:
            return raw.get(key)
    return None


def normalize_kg_search_filters(value: Any) -> dict[str, Any]:
    filters = normalize_filter_request("kg.hybrid_search", value)["effective_filters"]
    filters["example_kind"] = normalize_example_kind_filter(filters.get("example_kind"))
    return filters


def normalize_kg_aggregate_filters(value: Any) -> dict[str, Any]:
    filters = normalize_filter_request("kg.sql_aggregate", value)["effective_filters"]
    filters["example_kind"] = normalize_example_kind_filter(filters.get("example_kind"))
    return filters
