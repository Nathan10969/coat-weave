from __future__ import annotations

import hashlib
import re
from typing import Any


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
)


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


def extract_doc_ids(text: str) -> list[str]:
    hits: list[tuple[int, str]] = []
    for pattern in DOC_ID_PATTERNS:
        for match in pattern.finditer(text or ""):
            doc_id = normalize_doc_id(match.group(1))
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


KG_SEARCH_SOFT_FILTER_ALIASES: dict[str, tuple[str, ...]] = {
    "application_family": ("application_family", "application_families"),
    "applications": ("applications", "application"),
    "substrates": ("substrates", "substrate"),
    "test_methods": ("test_methods", "test_method"),
    "test_standards": ("test_standards", "test_standard", "standards"),
    "material_roles": ("material_roles", "material_role"),
    "assignees": ("assignees", "assignee", "applicants"),
    "property_families": ("property_families", "property_family"),
    "property_canonical_ids_soft": (
        "property_canonical_ids_soft",
        "soft_property_canonical_ids",
        "property_canonical_ids",
    ),
}

VALID_MATERIAL_ROLES = frozenset(
    {"resin", "curing_agent", "pigment", "filler", "additive", "solvent", "catalyst"}
)
MATERIAL_ROLE_ALIASES = {
    "pigments": "pigment",
    "fillers": "filler",
    "additives": "additive",
    "solvents": "solvent",
    "catalysts": "catalyst",
    "curing agents": "curing_agent",
    "curing_agents": "curing_agent",
    "hardener": "curing_agent",
    "hardeners": "curing_agent",
}


def normalize_material_roles(value: Any) -> list[str]:
    roles: list[str] = []
    for item in normalize_string_list(value):
        normalized = MATERIAL_ROLE_ALIASES.get(item.strip().casefold(), item.strip().casefold())
        if normalized in VALID_MATERIAL_ROLES and normalized not in roles:
            roles.append(normalized)
    return roles


INVALID_EXAMPLE_KIND_FILTER_VALUES = {
    "formulation",
    "formula",
    "recipe",
    "coating system",
    "coating_system",
    "paint system",
    "paint_system",
}


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
    raw = value if isinstance(value, dict) else {}
    qa_policy = str(raw.get("qa_policy") or "include_all").strip() or "include_all"
    if qa_policy not in {"include_all", "core_only", "qa_only"}:
        qa_policy = "include_all"
    filters = {
        "doc_ids": normalize_string_list(raw.get("doc_ids")),
        "properties": normalize_string_list(raw.get("properties")),
        "polarity": normalize_string_list(raw.get("polarity")),
        "example_kind": normalize_example_kind_filter(raw.get("example_kind")),
        "qa_policy": qa_policy,
    }
    for key, aliases in KG_SEARCH_SOFT_FILTER_ALIASES.items():
        filters[key] = normalize_string_list(first_present_filter_value(raw, aliases))
    filters["material_roles"] = normalize_material_roles(
        first_present_filter_value(raw, KG_SEARCH_SOFT_FILTER_ALIASES["material_roles"])
    )
    return filters


def normalize_kg_aggregate_filters(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    filters = normalize_kg_search_filters(raw)
    filters["application_family"] = normalize_string_list(
        raw.get("application_family") or raw.get("application_families")
    )
    return filters
