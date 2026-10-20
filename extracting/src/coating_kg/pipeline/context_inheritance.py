"""Stage 7.5 context inheritance for extracted facts.

Inheritance is intentionally one-way and conservative:

    fact explicit > sample_map/context_assertions > unit_context > matched_paragraphs > doc_profile > unresolved

Only null/unknown fields are filled. Every fill records provenance and caps the
fact-level confidence so inherited context is visible to QA. Broad doc_profile
fallback is limited to application/resin-style fields; sample/process/test
binding must come from model-extracted assertions with evidence provenance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..db.models import FactHyperedge, FigureTableUnit
from .example_id_resolver import example_id_for_fact, extract_example_id


UNKNOWN_VALUES = {None, "", "unknown", "UNKNOWN", "n/a", "N/A", "not_reported"}


@dataclass(frozen=True)
class ContextCandidate:
    field: str
    value: Any
    source: str
    confidence_cap: float
    evidence: str = ""
    source_id: str = ""
    source_field: str = ""
    extra: dict[str, Any] | None = None


def apply_context_inheritance(
    facts: list[FactHyperedge],
    *,
    unit: FigureTableUnit,
    matched_paragraphs: list[dict[str, Any]] | None = None,
    doc_profile: dict[str, Any] | None = None,
    vlm_description_json: dict[str, Any] | None = None,
    table_ocr: dict[str, Any] | None = None,
    sample_map: list[dict[str, Any]] | None = None,
    context_assertions: list[dict[str, Any]] | None = None,
    evidence_ledger: dict[str, Any] | None = None,
) -> list[FactHyperedge]:
    """Fill missing context slots on facts in-place and return the list."""
    base_candidates = _build_candidates(
        unit=unit,
        matched_paragraphs=matched_paragraphs or [],
        doc_profile=doc_profile or {},
    )
    for fact in facts:
        candidates = dict(base_candidates)
        provenance: dict[str, Any] = dict(fact.context_provenance or {})
        applied_caps: list[float] = []
        example_candidate = _sample_map_candidate_for_fact(
            fact,
            sample_map=sample_map or [],
            unit=unit,
        ) or _example_candidate_for_fact(
            fact,
            unit=unit,
            matched_paragraphs=matched_paragraphs or [],
            vlm_description_json=vlm_description_json,
            table_ocr=table_ocr,
        )
        if (
            example_candidate is not None
            and not _is_missing(example_candidate.value, field="example_id")
            and _is_missing(fact.example_id, field="example_id")
        ):
            fact.example_id = example_candidate.value
            provenance["example_id"] = _candidate_provenance(example_candidate, unit.unit_id)
            if example_candidate.source == "sample_map":
                fact.sample_binding_confidence = example_candidate.confidence_cap
                fact.resolution_status = "resolved"
                _maybe_apply_sample_polarity(fact, example_candidate)
            applied_caps.append(example_candidate.confidence_cap)
        assertion_candidates = _context_assertion_candidates_for_fact(
            fact,
            context_assertions=context_assertions or [],
            unit=unit,
        )
        for candidate in assertion_candidates:
            candidates[candidate.field] = candidate
        for field in (
            "application",
            "substrate",
            "resin_system",
            "process",
            "test_method",
            "test_condition",
        ):
            candidate = candidates.get(field)
            if candidate is None or _is_missing(candidate.value, field=field):
                continue
            if not _can_inherit_field(fact, field, candidate.value):
                continue
            if field == "test_condition":
                merged, changed = _merge_missing_test_condition(
                    getattr(fact, field, None), candidate.value,
                )
                if not changed:
                    continue
                setattr(fact, field, merged)
                provenance[field] = _candidate_provenance(candidate, unit.unit_id)
                applied_caps.append(candidate.confidence_cap)
                continue
            if not _is_missing(getattr(fact, field, None), field=field):
                continue
            setattr(fact, field, candidate.value)
            provenance[field] = _candidate_provenance(candidate, unit.unit_id)
            applied_caps.append(candidate.confidence_cap)
        if _is_missing(fact.example_id, field="example_id"):
            fact.resolution_status = fact.resolution_status or "unresolved_sample"
        elif not fact.resolution_status:
            fact.resolution_status = "resolved"
        fact.context_source_summary = _context_source_summary(provenance)
        if applied_caps:
            fact.context_provenance = provenance
            fact.extraction_confidence = min(fact.extraction_confidence, min(applied_caps))
    return facts


def _sample_map_candidate_for_fact(
    fact: FactHyperedge,
    *,
    sample_map: list[dict[str, Any]],
    unit: FigureTableUnit,
) -> ContextCandidate | None:
    if not sample_map:
        return None
    pointer_text = _fact_pointer_text(fact).casefold()
    best: tuple[float, dict[str, Any], str] | None = None
    for row in sample_map:
        if not isinstance(row, dict):
            continue
        aliases = [
            row.get("sample_key"),
            row.get("sample_id"),
            row.get("example_id"),
            *(row.get("aliases") or []),
        ]
        matched_alias = ""
        for alias in aliases:
            alias_text = str(alias or "").strip()
            if not alias_text:
                continue
            if alias_text.casefold() in pointer_text:
                matched_alias = alias_text
                break
        if not matched_alias:
            continue
        try:
            confidence = float(row.get("confidence") or row.get("sample_binding_confidence") or 0.86)
        except (TypeError, ValueError):
            confidence = 0.86
        if best is None or confidence > best[0]:
            best = (confidence, row, matched_alias)
    if best is None:
        return None
    confidence, row, matched_alias = best
    example_id = row.get("example_id") or row.get("sample_key") or row.get("sample_id") or matched_alias
    return ContextCandidate(
        "example_id",
        str(example_id),
        "sample_map",
        max(0.0, min(1.0, confidence)),
        f"matched alias {matched_alias} in {_fact_pointer_text(fact)}",
        source_id=unit.unit_id,
        source_field="sample_map",
        extra={
            "ledger_ids": list(row.get("source_ledger_ids") or row.get("ledger_ids") or []),
            "polarity_hint": row.get("polarity_hint") or row.get("polarity"),
        },
    )


def _fact_pointer_text(fact: FactHyperedge) -> str:
    pointer = fact.evidence_pointer
    parts = [
        getattr(fact, "example_id", None),
        pointer.row,
        pointer.column,
        pointer.cell,
        getattr(fact, "result_value_text", None),
        getattr(fact, "property", None),
    ]
    return " ".join(str(part) for part in parts if part not in (None, ""))


def _maybe_apply_sample_polarity(fact: FactHyperedge, candidate: ContextCandidate) -> None:
    polarity = (candidate.extra or {}).get("polarity_hint")
    if polarity not in {"positive", "negative", "unknown"}:
        return
    if str(getattr(fact, "polarity_hint", "unknown")) == "unknown":
        fact.polarity_hint = polarity  # type: ignore[assignment]


def _context_assertion_candidates_for_fact(
    fact: FactHyperedge,
    *,
    context_assertions: list[dict[str, Any]],
    unit: FigureTableUnit,
) -> list[ContextCandidate]:
    if not context_assertions:
        return []
    out: list[ContextCandidate] = []
    pointer_text = _fact_pointer_text(fact).casefold()
    example_id = str(getattr(fact, "example_id", "") or "").casefold()
    for assertion in context_assertions:
        if not isinstance(assertion, dict):
            continue
        if not _assertion_applies_to_fact(assertion, unit=unit, pointer_text=pointer_text, example_id=example_id):
            continue
        field, value = _normalise_assertion_field_value(assertion.get("field"), assertion.get("value"))
        if not field or _is_missing(value, field=field):
            continue
        out.append(
            ContextCandidate(
                field,
                value,
                "context_assertions",
                _clamped_confidence(assertion.get("confidence") or assertion.get("confidence_cap") or 0.86),
                str(assertion.get("evidence") or assertion.get("text") or assertion.get("source_text") or ""),
                source_id=str(assertion.get("assertion_id") or unit.unit_id),
                source_field=str(assertion.get("field") or field),
                extra={
                    "assertion_id": assertion.get("assertion_id"),
                    "ledger_ids": list(assertion.get("source_ledger_ids") or assertion.get("ledger_ids") or []),
                    "target_scope": assertion.get("target_scope"),
                    "target_sample": assertion.get("target_sample")
                    or assertion.get("sample_key")
                    or assertion.get("example_id"),
                },
            )
        )
    return out


def _assertion_applies_to_fact(
    assertion: dict[str, Any],
    *,
    unit: FigureTableUnit,
    pointer_text: str,
    example_id: str,
) -> bool:
    target_unit = str(assertion.get("target_unit_id") or assertion.get("unit_id") or "")
    if target_unit and target_unit != unit.unit_id:
        return False
    target_region = str(assertion.get("target_region_id") or assertion.get("region_id") or "")
    if target_region and target_region != str(unit.region_id or ""):
        return False

    target_sample = str(
        assertion.get("target_sample")
        or assertion.get("sample_key")
        or assertion.get("sample_id")
        or assertion.get("example_id")
        or ""
    ).strip()
    aliases = [target_sample, *(assertion.get("aliases") or [])]
    sample_scoped = str(assertion.get("target_scope") or "").casefold() in {
        "sample",
        "example",
        "control",
        "comparative",
    }
    if target_sample or sample_scoped:
        for alias in aliases:
            alias_text = str(alias or "").strip()
            if not alias_text:
                continue
            alias_key = alias_text.casefold()
            if alias_key == example_id or alias_key in pointer_text:
                return True
        return False
    return True


def _normalise_assertion_field_value(raw_field: Any, raw_value: Any) -> tuple[str, Any]:
    field = str(raw_field or "").strip().casefold()
    aliases = {
        "test_standard": "test_condition",
        "standard": "test_condition",
        "standard_id": "test_condition",
        "test_condition": "test_condition",
        "substrate": "substrate",
        "tested_substrate": "substrate",
        "application": "application",
        "resin_system": "resin_system",
        "binder": "resin_system",
        "process": "process",
        "test_method": "test_method",
    }
    normalised = aliases.get(field, "")
    if not normalised:
        return "", raw_value
    if normalised == "test_condition":
        if isinstance(raw_value, dict):
            value = dict(raw_value)
            standard = value.pop("test_standard", None) or value.pop("standard", None)
            if standard and not value.get("standard_id"):
                value["standard_id"] = standard
            elif field in {"test_standard", "standard", "standard_id"} and raw_value:
                value.setdefault("standard_id", raw_value)
            return normalised, value
        return normalised, {"standard_id": str(raw_value)}
    if normalised == "substrate":
        if isinstance(raw_value, dict):
            return normalised, raw_value
        return normalised, {"tested": str(raw_value), "claimed": []}
    if normalised == "process":
        if isinstance(raw_value, list):
            return normalised, raw_value
        if isinstance(raw_value, dict):
            return normalised, [raw_value]
        return normalised, [{"canonical_id": str(raw_value), "source": "context_assertions"}]
    return normalised, raw_value


def _clamped_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.86
    return max(0.0, min(1.0, confidence))


def _context_source_summary(provenance: dict[str, Any]) -> str:
    parts: list[str] = []
    for field, value in sorted(provenance.items()):
        if not isinstance(value, dict):
            continue
        source = value.get("source") or value.get("source_type") or "unknown"
        ledger_ids = value.get("ledger_ids") or []
        suffix = f"[{','.join(str(item) for item in ledger_ids[:3])}]" if ledger_ids else ""
        parts.append(f"{field}:{source}{suffix}")
    return "; ".join(parts)


def _example_candidate_for_fact(
    fact: FactHyperedge,
    *,
    unit: FigureTableUnit,
    matched_paragraphs: list[dict[str, Any]],
    vlm_description_json: dict[str, Any] | None,
    table_ocr: dict[str, Any] | None,
) -> ContextCandidate | None:
    pointer = fact.evidence_pointer
    row = pointer.row or ""
    column = pointer.column or ""
    caption = unit.caption_footnote_text or ""
    vlm_description = vlm_description_json or {"description": unit.vlm_description or ""}

    resolved = example_id_for_fact(
        fact.model_dump(mode="json"),
        vlm_description=vlm_description,
        caption=caption,
        table_ocr=table_ocr,
        matched_paragraphs=matched_paragraphs,
    )
    if not resolved:
        return None

    if extract_example_id(column) or extract_example_id(row):
        source = "unit_context.evidence_pointer"
        cap = 0.92
        evidence = f"row={row}; column={column}"
    elif caption and extract_example_id(caption):
        source = "unit_context.caption"
        cap = 0.88
        evidence = caption
    elif matched_paragraphs:
        source = "matched_paragraphs.example_range"
        cap = 0.78
        evidence = " ".join(str(p.get("text") or "") for p in matched_paragraphs[:2])
    else:
        source = "unit_context.vlm_description"
        cap = 0.82
        evidence = unit.vlm_description or ""

    return ContextCandidate(
        "example_id",
        resolved,
        source,
        cap,
        evidence,
        source_id=unit.unit_id,
        source_field="example_id",
    )


def _candidate_provenance(candidate: ContextCandidate, default_source_id: str) -> dict[str, Any]:
    row = {
        "source": candidate.source,  # backwards-compatible KG reader key
        "source_type": _source_type(candidate.source),
        "source_id": candidate.source_id or default_source_id,
        "source_field": candidate.source_field or candidate.field,
        "confidence_cap": candidate.confidence_cap,
        "evidence": candidate.evidence[:300],
        "evidence_excerpt": candidate.evidence[:300],
        "inherited_stage": "stage7_5",
        "rule_version": "context_inheritance_v2",
    }
    if candidate.extra:
        for key, value in candidate.extra.items():
            if value not in (None, "", [], {}):
                row[key] = value
    return row


def _merge_missing_test_condition(current: Any, candidate: Any) -> tuple[dict[str, Any], bool]:
    if isinstance(current, dict):
        merged = dict(current)
    elif current in (None, "", [], {}, "unknown"):
        merged = {}
    else:
        merged = {"description": str(current)}

    if isinstance(candidate, dict):
        candidate_map = candidate
    elif candidate in (None, "", [], {}, "unknown"):
        candidate_map = {}
    else:
        candidate_map = {"description": str(candidate)}

    changed = False
    for key, value in candidate_map.items():
        if value in (None, "", [], {}, "unknown"):
            continue
        if merged.get(key) in (None, "", [], {}, "unknown"):
            merged[key] = value
            changed = True
    return merged, changed


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

    # doc_profile: broad fallback only for document-level application/resin context.
    # Sample/process/test binding must come from Stage 7 assertions with provenance.
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
    profile_id = str(profile.get("profile_id") or profile.get("doc_id") or "")
    app = _profile_value(profile.get("application_family"))
    if app:
        out.append(
            ContextCandidate(
                "application",
                app[0],
                "doc_profile.application_family",
                0.80,
                app[1],
                source_id=profile_id,
                source_field="application_family",
            )
        )

    binder = _profile_value(profile.get("binder_family"), min_confidence=0.74)
    if binder:
        out.append(
            ContextCandidate(
                "resin_system",
                binder[0],
                "doc_profile.binder_family",
                0.72,
                binder[1],
                source_id=profile_id,
                source_field="binder_family",
            )
        )

    return out


def _source_type(source: str) -> str:
    if source.startswith("doc_profile"):
        return "doc_profile"
    if source.startswith("matched_paragraph"):
        return "matched_paragraphs"
    if source.startswith("unit_context"):
        return "unit_context"
    return source or "context_inheritance"


def _profile_value(value: Any, *, min_confidence: float = 0.0) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    try:
        confidence = float(value.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < min_confidence:
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
            out.append(
                ContextCandidate("application", canonical, source, cap, _window(text, m.start()))
            )
            break
    for regex, canonical in _RESIN_PATTERNS:
        m = regex.search(low)
        if m:
            out.append(
                ContextCandidate(
                    "resin_system",
                    canonical,
                    source,
                    min(cap, 0.78),
                    _window(text, m.start()),
                )
            )
            break
    for regex, canonical in _TEST_METHOD_PATTERNS:
        m = regex.search(low)
        if m:
            out.append(
                ContextCandidate(
                    "test_method",
                    canonical,
                    source,
                    min(cap, 0.86),
                    _window(text, m.start()),
                )
            )
            break
    standard = _extract_test_standard(text)
    if standard:
        standard_id, idx = standard
        out.append(
            ContextCandidate(
                "test_condition",
                {"standard_id": standard_id},
                source,
                min(cap, 0.86),
                _window(text, idx),
                source_field="standard_id",
            )
        )
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
    if field == "test_condition":
        if value is None:
            return True
        if isinstance(value, dict):
            return not bool(value)
        return str(value).strip().lower() == "unknown"
    return _is_scalar_unknown(value)


def _can_inherit_field(fact: FactHyperedge, field: str, candidate_value: Any) -> bool:
    if field == "resin_system":
        if _is_formulation_quantity_property(str(getattr(fact, "property", "") or "")):
            return False
        if fact.additives:
            return False
        return True
    if field == "test_condition" and isinstance(candidate_value, dict):
        standard = candidate_value.get("standard_id")
        if standard:
            return _standard_is_compatible_with_fact(str(standard), fact)
    return True


def _is_formulation_quantity_property(property_id: str) -> bool:
    prop = property_id.casefold()
    if prop in _FORMULATION_QUANTITY_PROPERTY_IDS:
        return True
    return any(marker in prop for marker in _FORMULATION_QUANTITY_PROPERTY_MARKERS)


def _standard_is_compatible_with_fact(standard: str, fact: FactHyperedge) -> bool:
    standard_key = re.sub(r"[^A-Z0-9]+", "", standard.upper())
    text = " ".join(
        str(value or "")
        for value in (getattr(fact, "property", None), getattr(fact, "test_method", None))
    ).casefold()
    if not text:
        return True
    compatibility = (
        (("ISO2409", "ASTMD3359"), ("adhesion", "cross_cut", "cross-cut")),
        (("ASTMB117", "ASTMG85"), ("corrosion", "salt", "scribe")),
        (("ASTMD522",), ("flexibility", "bend", "mandrel")),
        (("ASTMD4060",), ("abrasion", "taber", "wear")),
        (("ISO13320",), ("particle", "size", "distribution")),
        (("DIN55654",), ("rain", "erosion", "adhesion", "cross_cut", "cross-cut")),
    )
    for prefixes, hints in compatibility:
        if any(standard_key.startswith(prefix) for prefix in prefixes):
            return any(hint in text for hint in hints)
    return True


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

_RESIN_PATTERNS = [
    (_rx(r"acrylic resin", r"acrylic polyol", r"acrylate binder"), "MAT_acrylic_resin"),
    (_rx(r"polyurethane dispersion", r"polyurethane resin", r"polyurethane binder"), "MAT_polyurethane"),
    (_rx(r"polyester resin", r"polyester binder", r"polyester polyol"), "MAT_polyester_resin"),
    (_rx(r"epoxy resin", r"epoxy binder"), "MAT_epoxy_resin"),
    (_rx(r"alkyd resin", r"alkyd binder"), "MAT_alkyd_resin"),
    (_rx(r"silicone resin", r"polysiloxane binder"), "MAT_silicone_resin"),
    (_rx(r"\bfeve\b", r"fluoroethylene vinyl ether"), "MAT_fluorocarbon_FEVE"),
    (_rx(r"\bpvdf\b", r"polyvinylidene fluoride"), "MAT_fluorocarbon_PVDF"),
]

_PROCESS_PATTERNS = [
    (_rx(r"spray(?:ed|ing)?", r"spray application"), "PROC_spray_apply"),
    (_rx(r"uv[- ]?cur", r"actinic radiation"), "PROC_uv_cure"),
    (_rx(r"baked?", r"thermal cure", r"stoved?"), "PROC_thermal_cure"),
    (_rx(r"ambient cure", r"room temperature cure"), "PROC_ambient_cure"),
    (_rx(r"isocyanate", r"crosslink"), "PROC_isocyanate_crosslinking"),
]

_TEST_METHOD_PATTERNS = [
    (_rx(r"small flame test"), "TEST_small_flame"),
    (_rx(r"rain erosion", r"rain[- ]?erosion"), "TEST_rain_erosion"),
    (_rx(r"salt spray", r"scribe creep"), "TEST_salt_spray"),
    (_rx(r"marine immersion", r"seawater immersion"), "TEST_marine_immersion"),
    (_rx(r"\bmek\b"), "TEST_MEK_rub"),
]

_FORMULATION_QUANTITY_PROPERTY_IDS = {
    "prop_composition_weight_percent",
    "prop_formulation_component_mass",
    "prop_formulation_quantity",
    "prop_formulation_component_amount",
    "prop_component_weight",
    "prop_component_pbw",
    "prop_component_phr",
}
_FORMULATION_QUANTITY_PROPERTY_MARKERS = (
    "composition_weight",
    "formulation_component",
    "formulation_quantity",
    "component_mass",
    "component_weight",
    "component_pbw",
    "component_phr",
    "component_amount",
    "ingredient_quantity",
    "pbw",
    "phr",
)

_STANDARD_RE = re.compile(
    r"\b(?P<org>ISO|ASTM|DIN|GB/T|GB|JIS)\s*[- ]?(?P<code>[A-Z]?\s*\d+[A-Z0-9]*(?:[-/]\d+[A-Z0-9]*)*)",
    re.I,
)


def _extract_test_standard(text: str) -> tuple[str, int] | None:
    match = _STANDARD_RE.search(text or "")
    if not match:
        return None
    org = match.group("org").upper()
    code = re.sub(r"\s+", "", match.group("code").upper())
    if org == "GB":
        org = "GB"
    return f"{org} {code}", match.start()
