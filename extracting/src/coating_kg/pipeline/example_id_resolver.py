"""Conservative example-id resolver shared by CSV and KG export.

The resolver intentionally prefers explicit evidence. It only uses table OCR
headers, matched paragraph ranges, or same-column neighbours when a fact does
not already carry a usable example id.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Mapping, Sequence

JSON = Mapping[str, Any]

_BARE_DIGIT_RE = re.compile(r"^\s*(\d+)\s*$")
_COLUMN_INDEX_RE = re.compile(r"^\s*(?:col(?:umn)?\.?)\s*(\d+)\s*$", re.I)
_PREFIXED_EXAMPLE_ID_RE = re.compile(r"^[CEI](?:\d|[_-][A-Za-z0-9]+|[A-Z]\d)", re.I)
_OCR_FIXES: dict[str, str] = {
    "11": "I1",
    "12": "I2",
    "13": "I3",
    "l1": "I1",
    "l2": "I2",
    "l3": "I3",
    "Cl": "C1",
    "Cl1": "C1",
}
_VLM_EXAMPLE_KEYWORDS = ("example", "examples", "beispiel", "ejemplo", "ejemplos")


def extract_example_id(text: str | None) -> str | None:
    """Extract a normalized example id from row/column/caption text.

    Examples:
    - ``Example 1`` / ``Ex1`` -> ``E1``
    - ``Ex1/2/3`` -> ``E1/2/3`` (group-valued table column)
    - ``Examples 24-32`` -> ``E24-32``
    - ``Comparative Example 2`` / ``CE2`` -> ``C2``
    - ``I1`` / ``C1`` -> unchanged
    """
    if not text:
        return None
    value = str(text).strip()
    if not value:
        return None

    for pattern, prefix, group_name in _PATTERN_RULES:
        match = pattern.search(value)
        if not match:
            continue
        token = _extract_rule_group(match, group_name)
        raw = _normalize_example_token(prefix, token) if prefix else _normalize_direct_token(token)
        return _OCR_FIXES.get(raw, raw)
    return None


def example_id_for_fact(
    fact: Mapping[str, Any],
    *,
    vlm_description: Mapping[str, Any] | None = None,
    caption: str | None = None,
    table_ocr: Mapping[str, Any] | None = None,
    matched_paragraphs: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    neighbor_column_examples: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve ``fact.example_id`` using explicit, then contextual evidence."""

    explicit = _resolve_explicit_fact_id(fact)
    if explicit:
        return explicit

    pointer = fact.get("evidence_pointer")
    ep = pointer if isinstance(pointer, Mapping) else {}
    column = str(ep.get("column") or "")
    row = str(ep.get("row") or "")

    direct = extract_example_id(column) or extract_example_id(row)
    if direct:
        return direct

    if caption:
        cap_id = extract_example_id(caption)
        if cap_id:
            return cap_id

    context_text = _context_text(vlm_description, caption, matched_paragraphs)
    header_id = _resolve_from_sample_header(column, table_ocr, vlm_description, context_text)
    if header_id:
        return header_id

    if neighbor_column_examples:
        neighbor = neighbor_column_examples.get(_column_key(column))
        if neighbor:
            return neighbor

    range_id = _resolve_from_context_range(column, context_text)
    if range_id:
        return range_id

    if _looks_like_example_table(context_text):
        for text in (column, row):
            match = _BARE_DIGIT_RE.match(text)
            if match:
                return f"E{match.group(1)}"

    return None


def build_neighbor_column_examples(
    facts: Sequence[Mapping[str, Any]],
    *,
    vlm_description: Mapping[str, Any] | None = None,
    caption: str | None = None,
    table_ocr: Mapping[str, Any] | None = None,
    matched_paragraphs: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Build unambiguous same-column example-id hints for a unit."""

    seen: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        pointer = fact.get("evidence_pointer")
        ep = pointer if isinstance(pointer, Mapping) else {}
        key = _column_key(str(ep.get("column") or ""))
        if not key:
            continue
        resolved = example_id_for_fact(
            fact,
            vlm_description=vlm_description,
            caption=caption,
            table_ocr=table_ocr,
            matched_paragraphs=matched_paragraphs,
            neighbor_column_examples=None,
        )
        if resolved:
            seen[key].add(resolved)

    return {key: next(iter(values)) for key, values in seen.items() if len(values) == 1}


def matched_paragraph_list(
    value: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        matches = value.get("matches")
        if isinstance(matches, list):
            return [m for m in matches if isinstance(m, Mapping)]
        return []
    if isinstance(value, list):
        return [m for m in value if isinstance(m, Mapping)]
    return []


def _resolve_explicit_fact_id(fact: Mapping[str, Any]) -> str | None:
    raw_value = fact.get("example_id")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    raw = raw_value.strip()

    parsed = extract_example_id(raw)
    if parsed:
        return parsed

    if _PREFIXED_EXAMPLE_ID_RE.match(raw) and not re.match(r"^ex", raw, re.I):
        return raw

    polarity = fact.get("polarity_hint")
    if polarity == "negative":
        return f"C{raw}"
    if polarity == "positive":
        return f"E{raw}"
    return raw


def _normalize_example_token(prefix: str, token: str) -> str:
    cleaned = (
        str(token)
        .strip()
        .replace("–", "-")
        .replace("—", "-")
    )
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"(?i)\bto\b", "-", cleaned)
    parts = re.findall(r"\d+|[A-Z]", cleaned, re.I)
    if not parts:
        return ""
    if "-" in cleaned and len(parts) >= 2:
        return f"{prefix}{parts[0]}-{parts[-1]}"
    if len(parts) == 1:
        return f"{prefix}{parts[0]}"
    return f"{prefix}{'/'.join(parts)}"


def _normalize_direct_token(token: str) -> str:
    cleaned = re.sub(r"\s+", "", str(token).strip())
    if not cleaned:
        return ""
    if re.match(r"^[CEI]\d+", cleaned, re.I):
        return cleaned[:1].upper() + cleaned[1:]
    return cleaned


_TOKEN = r"(?P<token>\d+(?:\s*(?:/|,|;|-|to|and)\s*\d+)*)"
_PATTERN_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(rf"\bComparative\s+Examples?\s+{_TOKEN}\b", re.I), "C", "token"),
    (re.compile(rf"\bComp(?:arative|\.)?\s*Ex(?:ample|\.)?\s*{_TOKEN}\b", re.I), "C", "token"),
    (re.compile(rf"\bCE[\s.-]*{_TOKEN}\b", re.I), "C", "token"),
    (re.compile(rf"\bExamples?\s+{_TOKEN}\b", re.I), "E", "token"),
    (re.compile(rf"\bEx\.?\s*{_TOKEN}\b", re.I), "E", "token"),
    (re.compile(r"\b([CEI]\d+)(?:[A-Z]\d+)?\b"), "", "direct"),
    (re.compile(r"\b([CEI])\s*(\d+)\b", re.I), "", "spaced"),
    (re.compile(r"used\s*for\s*([CI]?\d+)", re.I), "", "usedfor"),
    (re.compile(r"\bVergleichsbeispiel\s+(\d+|[A-Z])\b", re.I), "C", "word"),
    (re.compile(r"\bBeispiel\s+(\d+|[A-Z])\b", re.I), "E", "word"),
    (re.compile(r"^\s*Comparative\s*$", re.I), "C", "implicit_one"),
]


def _extract_rule_group(match: re.Match[str], group_name: str) -> str:
    if group_name == "direct":
        return match.group(1)
    if group_name == "spaced":
        return f"{match.group(1).upper()}{match.group(2)}"
    if group_name == "usedfor":
        value = match.group(1)
        if value[:1].upper() in {"C", "I"}:
            return value.upper()
        return f"I{value[1:]}" if value.startswith("1") else value
    if group_name == "word":
        return match.group(1)
    if group_name == "implicit_one":
        return "1"
    return match.group(group_name)


# Rebuild rules with a small wrapper marker: using a helper keeps the main
# extraction loop fast and avoids lambdas that confuse type checkers.
_PATTERN_RULES = [
    (pattern, prefix, group_name)
    for pattern, prefix, group_name in _PATTERN_RULES
]


def _sample_headers_from(
    table_ocr: Mapping[str, Any] | None,
    vlm_description: Mapping[str, Any] | None,
) -> list[str]:
    headers: list[str] = []
    for source in (table_ocr, vlm_description):
        if not isinstance(source, Mapping):
            continue
        metadata = source.get("metadata")
        if isinstance(metadata, Mapping):
            _extend_headers(headers, metadata.get("sample_headers"))
        _extend_headers(headers, source.get("sample_headers"))
    return headers


def _extend_headers(headers: list[str], value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            if item not in (None, ""):
                headers.append(str(item))
    elif isinstance(value, str) and value.strip():
        headers.extend(part.strip() for part in re.split(r"[|,;]", value) if part.strip())


def _resolve_from_sample_header(
    column: str,
    table_ocr: Mapping[str, Any] | None,
    vlm_description: Mapping[str, Any] | None,
    context_text: str,
) -> str | None:
    index = _column_index(column)
    if index is None:
        return None
    headers = _sample_headers_from(table_ocr, vlm_description)
    if index < 1 or index > len(headers):
        return None
    header = headers[index - 1]
    direct = extract_example_id(header)
    if direct:
        return direct
    if _looks_like_example_table(context_text):
        match = _BARE_DIGIT_RE.match(header)
        if match:
            return f"E{match.group(1)}"
    return None


def _resolve_from_context_range(column: str, context_text: str) -> str | None:
    index = _column_index(column)
    if index is None:
        return None
    sequence = _example_sequence_from_context(context_text)
    if 1 <= index <= len(sequence):
        return sequence[index - 1]
    return None


def _example_sequence_from_context(text: str) -> list[str]:
    if not text:
        return []
    for pattern, prefix in (
        (re.compile(r"\bComparative\s+Examples?\s+(\d+)\s*(?:-|to|through|–|—)\s*(\d+)\b", re.I), "C"),
        (re.compile(r"\bExamples?\s+(\d+)\s*(?:-|to|through|–|—)\s*(\d+)\b", re.I), "E"),
    ):
        match = pattern.search(text)
        if not match:
            continue
        start, end = int(match.group(1)), int(match.group(2))
        if end < start or end - start > 50:
            continue
        return [f"{prefix}{idx}" for idx in range(start, end + 1)]
    return []


def _context_text(
    vlm_description: Mapping[str, Any] | None,
    caption: str | None,
    matched_paragraphs: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None,
) -> str:
    parts: list[str] = []
    if caption:
        parts.append(caption)
    if isinstance(vlm_description, Mapping):
        for key in (
            "description",
            "table_subject",
            "table_type",
            "corrected_table_html",
            "table_markdown_exact",
        ):
            value = vlm_description.get(key)
            if value:
                parts.append(str(value))
    for paragraph in matched_paragraph_list(matched_paragraphs):
        for key in ("text", "reason"):
            value = paragraph.get(key)
            if value:
                parts.append(str(value))
    return "\n".join(parts)


def _looks_like_example_table(text: str) -> bool:
    low = text.casefold()
    return any(keyword in low for keyword in _VLM_EXAMPLE_KEYWORDS)


def _column_index(column: str) -> int | None:
    match = _COLUMN_INDEX_RE.match(column)
    if match:
        return int(match.group(1))
    return None


def _column_key(column: str) -> str:
    return re.sub(r"\s+", " ", str(column or "").strip().casefold())
