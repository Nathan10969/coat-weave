"""Deterministic Examples-section detection for patent layouts.

The public ``split_examples_section(text)`` helper is kept for backwards
compatibility, but Stage 2 should prefer ``locate_examples_blocks`` so figure
and table filtering follows MinerU block order instead of fragile character
offsets.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Final, Iterable


@dataclass(frozen=True)
class SectionSpan:
    """Examples span with audit metadata.

    ``start`` and ``end`` are character offsets in the synthetic text view.
    ``start_block`` and ``end_block`` are block-index bounds where ``end_block``
    is exclusive. They are ``None`` for plain-text callers.
    """

    start: int
    end: int
    mode: str
    confidence: str
    start_reason: str
    end_reason: str
    start_block: int | None = None
    end_block: int | None = None
    start_page: int | None = None
    end_page: int | None = None
    anchors_seen: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Line:
    idx: int
    start: int
    end: int
    text: str
    norm: str
    block_index: int | None = None
    page: int | None = None
    text_level: int | None = None


@dataclass(frozen=True)
class _Candidate:
    line_idx: int
    score: int
    mode: str
    reason: str
    zone: str
    anchors: tuple[str, ...]


_LEADING_PARA_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:\[\s*\d{1,5}\s*\]\s*)?"
)
_DIRECT_EXAMPLE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:\[\s*\d{1,5}\s*\]\s*)?"
    r"(?:(?:comparative|reference|working|preparation|preparative)\s+)?"
    r"examples?\b\s*(?:no\.?\s*)?(?:\d+[A-Z]?|[A-Z]\b)",
    re.IGNORECASE,
)
_DIRECT_EMBODIMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:\[\s*\d{1,5}\s*\]\s*)?embodiments?\s*(?:\d+[A-Z]?|[A-Z])\b",
    re.IGNORECASE,
)
_DIRECT_GERMAN_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:vergleichs)?beispiel\s*(?:\d+[A-Z]?|[A-Z])\b",
    re.IGNORECASE,
)
_NUMBERED_SECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*\d+(?:\.\d+)*[.)]?\s*"
    r"(?:examples?|embodiments?|experimental|working examples?)\s*$",
    re.IGNORECASE,
)
_WEAK_OPENER_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:invention|disclosure|present application)\b.{0,100}\b"
    r"(?:illustrated|described|explained|demonstrated)\b.{0,80}\b"
    r"(?:following\s+)?(?:non[- ]limiting\s+)?examples\b",
    re.IGNORECASE,
)
_HARD_END_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:claims?|what\s+is\s+claimed|abstract|industrial\s+applicability|"
    r"sequence\s+listing|patentanspr\w*)\b",
    re.IGNORECASE,
)
_BACKGROUND_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:background|prior\s+art|related\s+art|state\s+of\s+the\s+art)\b",
    re.IGNORECASE,
)
_DESCRIPTION_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:detailed\s+description|description|specific\s+embodiments?|"
    r"description\s+of\s+embodiments?)\b",
    re.IGNORECASE,
)
_INTERNAL_EXAMPLE_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:(?:comparative|reference|working|preparation|preparative)\s+)?"
    r"examples?\b|^\s*(?:table|fig(?:ure)?|scheme)\s+\d+\b|"
    r"^\s*(?:test\s+results?|results?|experimental)\b",
    re.IGNORECASE,
)
_EXPERIMENTAL_SIGNAL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:example\s+\d+|comparative\s+example|table\s+\d+|fig(?:ure)?\s+\d+|"
    r"scheme\s+\d+|prepared|formulated|mixed|coated|cured|tested|measured|"
    r"evaluated|composition|coating|sample|substrate|wt\.?\s*%|g\b|rating)\b",
    re.IGNORECASE,
)


def locate_examples_section(text: str) -> SectionSpan | None:
    """Return an audited Examples span for plain text, or ``None``."""

    if not text:
        return None
    return _locate_examples(_lines_from_text(text), text_length=len(text))


def split_examples_section(text: str) -> tuple[int, int] | None:
    """Return ``(start_idx, end_idx)`` for the Examples section.

    This compatibility wrapper intentionally returns the same shape as the old
    implementation. New callers that need diagnostics should use
    ``locate_examples_section`` or ``locate_examples_blocks``.
    """

    span = locate_examples_section(text)
    if span is None:
        return None
    return (span.start, span.end)


def locate_examples_blocks(blocks: Iterable[dict[str, Any]]) -> SectionSpan | None:
    """Return an audited Examples span over MinerU blocks.

    The block interval is ``[start_block, end_block)`` and therefore safely
    includes non-text blocks such as figures/tables that occur after the start
    anchor and before the end anchor.
    """

    lines, text_length, block_count = _lines_from_blocks(list(blocks))
    span = _locate_examples(lines, text_length=text_length, block_count=block_count)
    return span


def examples_block_range(blocks: Iterable[dict[str, Any]]) -> tuple[int, int] | None:
    """Return the ``[start_block, end_block)`` Examples block range."""

    span = locate_examples_blocks(blocks)
    if span is None or span.start_block is None or span.end_block is None:
        return None
    return (span.start_block, span.end_block)


def extract_examples_text(text: str) -> str | None:
    """Return the Examples slice or ``None``."""

    span = split_examples_section(text)
    if span is None:
        return None
    return text[span[0] : span[1]]


def _locate_examples(
    lines: list[_Line],
    *,
    text_length: int,
    block_count: int | None = None,
) -> SectionSpan | None:
    candidates = _find_start_candidates(lines)
    if not candidates:
        return None

    winner = sorted(candidates, key=_candidate_sort_key)[0]
    start_line = lines[winner.line_idx]
    end_line, end_reason = _find_end_line(lines, winner.line_idx + 1)

    end = end_line.start if end_line is not None else text_length
    end_block = _exclusive_end_block(end_line, block_count)
    end_page = _end_page(lines, winner.line_idx, end_line)

    return SectionSpan(
        start=start_line.start,
        end=end,
        mode=winner.mode,
        confidence=_confidence_for(winner),
        start_reason=winner.reason,
        end_reason=end_reason,
        start_block=start_line.block_index,
        end_block=end_block,
        start_page=start_line.page,
        end_page=end_page,
        anchors_seen=winner.anchors,
    )


def _find_start_candidates(lines: list[_Line]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    zone = "front_matter"

    for i, line in enumerate(lines):
        if not line.norm:
            continue
        if _is_hard_end(line.norm):
            zone = "claims"
        elif _is_background_heading(line):
            zone = "background"
        elif _is_description_heading(line):
            zone = "description"

        if zone == "claims":
            continue

        candidate = _classify_start(lines, i, zone)
        if candidate is not None:
            candidates.append(candidate)

    return candidates


def _classify_start(lines: list[_Line], idx: int, zone: str) -> _Candidate | None:
    line = lines[idx]
    text = _strip_leading_para(line.norm)
    heading_like = _is_heading_like(line)
    anchors = _confirmation_anchors(lines, idx)

    if _is_examples_heading(text, heading_like):
        return _Candidate(
            line_idx=idx,
            score=110,
            mode="explicit_heading",
            reason=f"heading-like examples anchor: {line.norm[:80]}",
            zone=zone,
            anchors=anchors,
        )

    if _NUMBERED_SECTION_RE.match(text):
        return _Candidate(
            line_idx=idx,
            score=105,
            mode="numbered_heading",
            reason=f"numbered examples heading: {line.norm[:80]}",
            zone=zone,
            anchors=anchors,
        )

    if _is_bare_example_heading(text, heading_like):
        if _has_confirmation(anchors, min_count=2):
            return _Candidate(
                line_idx=idx,
                score=98,
                mode="bare_example_heading",
                reason=f"bare Example heading confirmed by nearby anchors: {line.norm[:80]}",
                zone=zone,
                anchors=anchors,
            )
        return None

    if _DIRECT_EXAMPLE_RE.match(text) or _DIRECT_EMBODIMENT_RE.match(text) or _DIRECT_GERMAN_RE.match(text):
        if zone == "background":
            return None
        return _Candidate(
            line_idx=idx,
            score=92,
            mode="direct_example",
            reason=f"direct example anchor: {line.norm[:80]}",
            zone=zone,
            anchors=anchors,
        )

    if _looks_like_chinese_example(text):
        return _Candidate(
            line_idx=idx,
            score=94,
            mode="direct_example",
            reason=f"Chinese example anchor: {line.norm[:80]}",
            zone=zone,
            anchors=anchors,
        )

    if _WEAK_OPENER_RE.search(text):
        if zone == "background" or not _has_confirmation(anchors, min_count=2):
            return None
        return _Candidate(
            line_idx=idx,
            score=96,
            mode="opening_sentence",
            reason=f"examples opening sentence confirmed by nearby anchors: {line.norm[:80]}",
            zone=zone,
            anchors=anchors,
        )

    return None


def _find_end_line(lines: list[_Line], start_idx: int) -> tuple[_Line | None, str]:
    for line in lines[start_idx:]:
        text = _strip_leading_para(line.norm)
        if _is_hard_end(text):
            return line, f"hard end heading: {line.norm[:80]}"
    return None, "end of document"


def _candidate_sort_key(candidate: _Candidate) -> tuple[int, int, int]:
    zone_bonus = 8 if candidate.zone == "description" else 0
    anchor_bonus = min(len(candidate.anchors), 5)
    # Highest score wins; for equal scores prefer the later description-zone
    # anchor to avoid front-matter summary examples.
    return (-(candidate.score + zone_bonus + anchor_bonus), -candidate.line_idx, candidate.line_idx)


def _confidence_for(candidate: _Candidate) -> str:
    if candidate.score >= 100:
        return "strong"
    if candidate.score >= 90:
        return "medium"
    return "weak"


def _exclusive_end_block(end_line: _Line | None, block_count: int | None) -> int | None:
    if block_count is None:
        return None
    if end_line is None or end_line.block_index is None:
        return block_count
    return end_line.block_index


def _end_page(lines: list[_Line], start_idx: int, end_line: _Line | None) -> int | None:
    if end_line is None:
        for line in reversed(lines[start_idx:]):
            if line.page is not None:
                return line.page
        return None
    end_idx = max(end_line.idx - 1, start_idx)
    for line in reversed(lines[start_idx : end_idx + 1]):
        if line.page is not None:
            return line.page
    return None


def _confirmation_anchors(lines: list[_Line], idx: int, *, window: int = 40) -> tuple[str, ...]:
    anchors: list[str] = []
    for line in lines[idx : min(len(lines), idx + window)]:
        text = _strip_leading_para(line.norm)
        if _DIRECT_EXAMPLE_RE.match(text) or _DIRECT_EMBODIMENT_RE.match(text) or _DIRECT_GERMAN_RE.match(text):
            anchors.append(f"direct:{line.idx}")
        elif _looks_like_chinese_example(text):
            anchors.append(f"cn:{line.idx}")
        elif _EXPERIMENTAL_SIGNAL_RE.search(text):
            anchors.append(f"signal:{line.idx}")
        if len(anchors) >= 6:
            break
    return tuple(anchors)


def _has_confirmation(anchors: tuple[str, ...], *, min_count: int) -> bool:
    return len(set(anchors)) >= min_count


def _lines_from_text(text: str) -> list[_Line]:
    lines: list[_Line] = []
    cursor = 0
    for idx, raw in enumerate(text.splitlines(keepends=True)):
        line_text = raw.rstrip("\r\n")
        lines.append(
            _Line(
                idx=idx,
                start=cursor,
                end=cursor + len(line_text),
                text=line_text,
                norm=_normalize(line_text),
            )
        )
        cursor += len(raw)
    if text and not text.endswith(("\n", "\r")) and not lines:
        lines.append(_Line(idx=0, start=0, end=len(text), text=text, norm=_normalize(text)))
    return lines


def _lines_from_blocks(blocks: list[dict[str, Any]]) -> tuple[list[_Line], int, int]:
    lines: list[_Line] = []
    cursor = 0
    for block_idx, block in enumerate(blocks):
        text = _block_text(block)
        if not text:
            continue
        page = _block_page(block)
        text_level = _block_text_level(block)
        for raw in text.splitlines() or [text]:
            line_text = raw.strip()
            line_start = cursor
            line_end = cursor + len(line_text)
            lines.append(
                _Line(
                    idx=len(lines),
                    start=line_start,
                    end=line_end,
                    text=line_text,
                    norm=_normalize(line_text),
                    block_index=block_idx,
                    page=page,
                    text_level=text_level,
                )
            )
            cursor = line_end + 1
    return lines, cursor, len(blocks)


def _block_text(block: dict[str, Any]) -> str:
    for key in ("text", "content"):
        value = block.get(key)
        if isinstance(value, str):
            return value
    return ""


def _block_page(block: dict[str, Any]) -> int | None:
    try:
        return int(block.get("page_idx", 0)) + 1
    except (TypeError, ValueError):
        return None


def _block_text_level(block: dict[str, Any]) -> int | None:
    try:
        value = block.get("text_level")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_leading_para(text: str) -> str:
    return _LEADING_PARA_RE.sub("", text).strip()


def _is_examples_heading(text: str, heading_like: bool) -> bool:
    if not heading_like:
        return False
    normalized = text.strip(" :.;").lower()
    examples_headings = {
        "examples",
        "working examples",
        "preparation examples",
        "preparative examples",
        "comparative examples",
        "examples and comparative examples",
        "experimental examples",
        "experimental",
        "embodiments",
        "beispiele",
    }
    if normalized in examples_headings:
        return True
    return normalized.startswith(("examples -", "examples:"))


def _is_bare_example_heading(text: str, heading_like: bool) -> bool:
    if not heading_like:
        return False
    return text.strip(" :.;").lower() in {"example", "working example", "experimental example"}


def _is_heading_like(line: _Line) -> bool:
    text = _strip_leading_para(line.norm)
    if not text or len(text) > 120:
        return False
    if line.text_level == 1:
        return True
    if text.endswith(".") and len(text.split()) > 4:
        return False
    if text.isupper() and len(text) > 2:
        return True
    if re.match(r"^\d+(?:\.\d+)*[.)]?\s+[A-Z]", text):
        return True
    return bool(re.match(r"^[A-Z][A-Za-z0-9 /(),-]{0,80}:?$", text))


def _is_major_heading(line: _Line) -> bool:
    text = _strip_leading_para(line.norm)
    if not _is_heading_like(line):
        return False
    if _is_internal_examples_heading(text):
        return False
    return bool(
        _is_hard_end(text)
        or _BACKGROUND_RE.match(text)
        or _DESCRIPTION_RE.match(text)
        or (text.isupper() and len(text.split()) <= 6)
    )


def _is_internal_examples_heading(text: str) -> bool:
    return bool(_INTERNAL_EXAMPLE_HEADING_RE.match(text))


def _is_hard_end(text: str) -> bool:
    return bool(_HARD_END_RE.match(text)) or text.startswith("权利要求")


def _is_background_heading(line: _Line) -> bool:
    return _is_heading_like(line) and bool(_BACKGROUND_RE.match(_strip_leading_para(line.norm)))


def _is_description_heading(line: _Line) -> bool:
    return _is_heading_like(line) and bool(_DESCRIPTION_RE.match(_strip_leading_para(line.norm)))


def _looks_like_chinese_example(text: str) -> bool:
    stripped = text.strip()
    return bool(
        re.match(r"^(?:比较)?(?:具体)?实施例\s*\d*", stripped)
        or re.match(r"^(?:姣旇緝)?(?:鍏蜂綋)?瀹炴柦渚.*\d*", stripped)
    )
