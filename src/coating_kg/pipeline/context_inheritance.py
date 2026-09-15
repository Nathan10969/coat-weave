"""Stage 7.5 context inheritance for extracted facts.

Inheritance is intentionally one-way and conservative:

    fact explicit > example_context > unit_context > matched_paragraphs > doc_profile > unknown

Only null/unknown fields are filled. Every fill records provenance and caps the
fact-level confidence so inherited context is visible to QA.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..db.models import FactHyperedge, FigureTableUnit


UNKNOWN_VALUES = {None, "", "unknown", "UNKNOWN", "n/a", "N/A", "not_reported"}


@dataclass(frozen=True)
class ContextCandidate:
    field: str
    value: Any
    source: str
    confidence_cap: float
    evidence: str = ""


def apply_context_inheritance(
    facts: list[FactHyperedge],
    *,
    unit: FigureTableUnit,
    matched_paragraphs: list[dict[str, Any]] | None = None,
    doc_profile: dict[str, Any] | None = None,
) -> list[FactHyperedge]:
    """Fill missing context slots on facts in-place and return the list."""
    candidates = _build_candidates(
        unit=unit,
        matched_paragraphs=matched_paragraphs or [],
        doc_profile=doc_profile or {},
    )
    for fact in facts:
        provenance: dict[str, Any] = dict(fact.context_provenance or {})
        applied_caps: list[float] = []
        for field in ("application", "substrate", "process", "test_method"):
            if not _is_missing(getattr(fact, field, None), field=field):
                continue
            candidate = candidates.get(field)
            if candidate is None or _is_missing(candidate.value, field=field):
                continue
            setattr(fact, field, candidate.value)
            provenance[field] = {
                "source": candidate.source,
                "confidence_cap": candidate.confidence_cap,
                "evidence": candidate.evidence[:300],
            }
            applied_caps.append(candidate.confidence_cap)
        if applied_caps:
            fact.context_provenance = provenance
            fact.extraction_confidence = min(fact.extraction_confidence, min(applied_caps))
    return facts


def _build_candidates(
    *,
    unit: FigureTableUnit,
    matched_paragraphs: list[dict[str, Any]],
    doc_profile: dict[str, Any],
) -> dict[str, ContextCandidate]:
    candidates: dict[str, ContextCandidate] = {}

    # unit_context: caption + VLM description is closer to the table than full doc.
    unit_text = " ".join(
        text for text in (unit.caption_footnote_text, unit.vlm_description) if text
    )
    _add_if_absent(candidates, _infer_from_text(unit_text, "unit_context", 0.88))

    # matched_paragraphs: selected prose context from Stage 6.
    matched_text = "\n".join(
        str(p.get("text") or "") for p in matched_paragraphs if isinstance(p, dict)
    )
    _add_if_absent(candidates, _infer_from_text(matched_text, "matched_paragraphs", 0.84))

    # doc_profile: broad but useful fallback for application/substrate/process.
    _add_if_absent(candidates, _infer_from_doc_profile(doc_profile))
    return candidates


def _add_if_absent(
    target: dict[str, ContextCandidate],
    incoming: Iterable[ContextCandidate],
) -> None:
    for candidate in incoming:
        if candidate.field not in target and not _is_missing(candidate.value, field=candidate.field):
            target[candidate.field] = candidate


def _infer_from_doc_profile(profile: dict[str, Any]) -> list[ContextCandidate]:
    out: list[ContextCandidate] = []
    app = _profile_value(profile.get("application_family"))
    if app:
        out.append(ContextCandidate("application", app[0], "doc_profile.application_family", 0.80, app[1]))

    sub = _profile_value(profile.get("substrate_scope"))
    if sub:
        out.append(ContextCandidate("substrate", {"tested": sub[0], "claimed": []}, "doc_profile.substrate_scope", 0.72, sub[1]))

    cure = _profile_value(profile.get("cure_mechanism"))
    if cure:
        out.append(ContextCandidate("process", [{"canonical_id": cure[0], "source": "doc_profile"}], "doc_profile.cure_mechanism", 0.76, cure[1]))
    return out


def _profile_value(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    canonical = value.get("canonical_id")
    label = value.get("label")
    chosen = canonical or label
    if not chosen or str(chosen).lower() == "unknown":
        return None
    return (str(chosen), str(value.get("evidence") or label or ""))


def _infer_from_text(text: str, source: str, cap: float) -> list[ContextCandidate]:
    low = (text or "").casefold()
    out: list[ContextCandidate] = []
    for regex, canonical in _APPLICATION_PATTERNS:
        m = regex.search(low)
        if m:
            out.append(ContextCandidate("application", canonical, source, cap, _window(text, m.start())))
            break
    for regex, canonical in _SUBSTRATE_PATTERNS:
        m = regex.search(low)
        if m:
            out.append(ContextCandidate("substrate", {"tested": canonical, "claimed": []}, source, min(cap, 0.82), _window(text, m.start())))
            break
    for regex, canonical in _PROCESS_PATTERNS:
        m = regex.search(low)
        if m:
            out.append(ContextCandidate("process", [{"canonical_id": canonical, "source": source}], source, min(cap, 0.84), _window(text, m.start())))
            break
    for regex, canonical in _TEST_METHOD_PATTERNS:
        m = regex.search(low)
        if m:
            out.append(ContextCandidate("test_method", canonical, source, min(cap, 0.86), _window(text, m.start())))
            break
    return out


def _window(text: str, idx: int, radius: int = 120) -> str:
    return text[max(0, idx - radius) : idx + radius].replace("\n", " ").strip()


def _is_missing(value: Any, *, field: str) -> bool:
    if field == "substrate":
        if value is None:
            return True
        if isinstance(value, dict):
            tested = value.get("tested")
            claimed = value.get("claimed")
            return _is_scalar_unknown(tested) and not claimed
        return str(value).strip().lower() == "unknown"
    if field == "process":
        return value == [] or _is_scalar_unknown(value)
    return _is_scalar_unknown(value)


def _is_scalar_unknown(value: Any) -> bool:
    if isinstance(value, (list, dict, set, tuple)):
        return False
    return value in UNKNOWN_VALUES


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.IGNORECASE)


_APPLICATION_PATTERNS = [
    (_rx(r"fouling control", r"anti[- ]?fouling", r"marine coating"), "APP_marine_fouling_control"),
    (_rx(r"wind turbine", r"rotor blade", r"leading edge protection"), "APP_wind_blade_coating"),
    (_rx(r"automotive", r"clearcoat", r"basecoat"), "APP_automotive_oem_clearcoat"),
    (_rx(r"powder coating"), "APP_powder_coating"),
    (_rx(r"anti[- ]?corrosion", r"corrosion protection"), "APP_anti_corrosion_coating"),
]

_SUBSTRATE_PATTERNS = [
    (_rx(r"\bsteel\b", r"\bmetal\b", r"alumini?um", r"tinplate"), "SUB_metal"),
    (_rx(r"plywood", r"\bwood\b"), "SUB_wood"),
    (_rx(r"\bglass\b"), "SUB_glass"),
    (_rx(r"\bplastic\b", r"polypropylene", r"polycarbonate"), "SUB_plastic"),
    (_rx(r"wind turbine blade", r"rotor blade"), "SUB_wind_turbine_blade"),
]

_PROCESS_PATTERNS = [
    (_rx(r"spray(?:ed|ing)?", r"spray application"), "PROC_spray_apply"),
    (_rx(r"uv[- ]?cur", r"actinic radiation"), "PROC_uv_cure"),
    (_rx(r"baked?", r"thermal cure", r"stoved?"), "PROC_thermal_cure"),
    (_rx(r"ambient cure", r"room temperature cure"), "PROC_ambient_cure"),
    (_rx(r"isocyanate", r"crosslink"), "PROC_isocyanate_crosslinking"),
]

_TEST_METHOD_PATTERNS = [
    (_rx(r"astm\s*d3359"), "TEST_ASTM_D3359"),
    (_rx(r"iso\s*2409"), "TEST_ISO_2409"),
    (_rx(r"din\s*55654"), "TEST_DIN_55654"),
    (_rx(r"small flame test"), "TEST_small_flame"),
    (_rx(r"rain erosion", r"rain[- ]?erosion"), "TEST_rain_erosion"),
    (_rx(r"salt spray", r"scribe creep"), "TEST_salt_spray"),
    (_rx(r"marine immersion", r"seawater immersion"), "TEST_marine_immersion"),
    (_rx(r"\bmek\b"), "TEST_MEK_rub"),
]
