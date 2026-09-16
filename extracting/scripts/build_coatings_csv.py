"""Build the boss-facing coatings wide CSV from KG JSONL.

Stage 9 is a view over the KG projection. It must not read per-unit audit
folders, re-resolve example IDs, or create new semantic facts. Upstream stages
and ``data/kg/*.jsonl`` own extraction, normalization, context, and graph links.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

JSON = dict[str, Any]


MATERIAL_SUB_TYPES = [
    "Resin / binder",
    "Crosslinker / Curing agent",
    "Pigment",
    "Filler",
    "Additive",
    "Solvent",
    "Monomer",
]

PROPERTY_SUB_TYPES = [
    "Appearance",
    "Mechanical",
    "Surface",
    "Aging / Corrosion / Durability",
    "Chemical resistance",
    "Thermal",
    "Environmental / Regulatory",
    "Application properties",
    "Formulation",
]

MATERIAL_ROLE_ALIASES = {
    "binder": "Resin / binder",
    "epoxy binder": "Resin / binder",
    "resin": "Resin / binder",
    "crosslinker": "Crosslinker / Curing agent",
    "curing agent": "Crosslinker / Curing agent",
    "pigment": "Pigment",
    "aluminum effect pigment": "Pigment",
    "filler": "Filler",
    "solvent": "Solvent",
    "monomer": "Monomer",
    "additive": "Additive",
    "component": "Additive",
    "material": "Additive",
    "binder_or_resin": "Resin / binder",
    "resin / binder": "Resin / binder",
    "pigment_or_filler": "Filler",
    "catalyst_or_initiator": "Additive",
    "defoamer": "Additive",
    "thickener": "Additive",
    "surfactant": "Additive",
    "dispersant": "Additive",
    "release material": "Additive",
    "humectant": "Additive",
    "biocide": "Additive",
    "colloidal agent": "Additive",
    "light diffusing particle intermediate": "Additive",
}

GENERIC_SLOT_MATERIAL_ROLES = {
    "",
    "component",
    "material",
    "formulation component",
    "coating formulation component",
    "table component",
    "additive_or_component",
    "additive_or_auxiliary",
}

RESIN_ROLE_CONFLICT_HINTS = (
    "rheology control",
    "surface active",
    "surface-active",
    "surface_active",
    "surfactant",
    "defoamer",
    "biocide",
    "catalyst",
    "initiator",
    "neutraliz",
    "dimethylethanolamine",
    "diethylethanolamine",
    "pigment",
    "monomer",
    "solvent",
    " water",
    " acid",
    "crosslinker",
    "curing agent",
)

GENERIC_NON_MATERIAL_LABELS = {
    "composition",
    "coating composition",
    "component",
    "material",
    "table",
}

FORMULATION_QUANTITY_PROPERTY_IDS = {
    "PROP_composition_weight_percent",
    "PROP_formulation_component_mass",
    "PROP_formulation_quantity",
    "PROP_formulation_component_amount",
    "PROP_component_weight",
    "PROP_component_pbw",
    "PROP_component_phr",
}

CORE_KG_FILES = {
    "facts": "facts.jsonl",
    "example_contexts": "example_contexts.jsonl",
    "patents": "patents.jsonl",
    "canonical_entities": "canonical_entities.jsonl",
}

HYPEREDGE_FILE = "hyperedges.jsonl"


def short_id(canonical_id: str) -> str:
    """Return a compact display label without changing the KG value."""

    if not canonical_id:
        return ""
    out = re.sub(r"^[A-Z]+_", "", str(canonical_id))
    out = re.sub(r"_generic$", "", out)
    return out


def fmt_num(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def fact_result_text(fact: JSON) -> str:
    result = fmt_num(fact.get("result_value"))
    if not result:
        result = str(fact.get("result_value_text") or "").strip()
    return result


def extract_test_standard(fact: JSON) -> str:
    condition = fact.get("test_condition")
    if not isinstance(condition, dict):
        return ""
    for key in ("standard_id", "test_standard", "standard"):
        value = condition.get(key)
        if value:
            return str(value)
    return ""


def material_candidate_ids(fact: JSON) -> list[str]:
    candidates: list[str] = []
    resin = fact.get("resin_system")
    if isinstance(resin, str) and resin:
        candidates.append(resin)
    for value in fact.get("additives") or []:
        if isinstance(value, str) and value:
            candidates.append(value)
    return candidates


def is_formulation_quantity_fact(fact: JSON, subtype_map: dict[str, str]) -> bool:
    """Projection-only split between material amount cells and properties."""

    prop = str(fact.get("property") or "")
    return prop in FORMULATION_QUANTITY_PROPERTY_IDS or subtype_map.get(prop) == "Formulation"


def material_projection_entries(fact: JSON, subtype_map: dict[str, str]) -> list[tuple[str, str]]:
    quantity = fact_result_text(fact)
    entries: list[tuple[str, str]] = []
    for canonical_id in material_candidate_ids(fact):
        sub_type = subtype_map.get(canonical_id)
        if sub_type in MATERIAL_SUB_TYPES:
            entries.append((sub_type, f"{short_id(canonical_id)}:{quantity}"))
    return entries


def load_kg_projection_inputs(kg_dir: Path) -> tuple[list[JSON], list[JSON], dict[str, JSON], dict[str, JSON], JSON]:
    """Load the KG JSONL files required by the CSV view."""

    if not kg_dir.exists():
        raise FileNotFoundError(f"KG directory does not exist: {kg_dir}")
    manifest_path = kg_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"KG manifest is required before CSV projection: {manifest_path}")

    missing = [name for name in CORE_KG_FILES.values() if not (kg_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"KG projection is incomplete; missing: {', '.join(missing)}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    facts = _read_jsonl(kg_dir / CORE_KG_FILES["facts"])
    contexts = _read_jsonl(kg_dir / CORE_KG_FILES["example_contexts"])
    patents = {
        str(row.get("doc_id")): row
        for row in _read_jsonl(kg_dir / CORE_KG_FILES["patents"])
        if row.get("doc_id")
    }
    canonicals = {
        str(row.get("canonical_id")): row
        for row in _read_jsonl(kg_dir / CORE_KG_FILES["canonical_entities"])
        if row.get("canonical_id")
    }
    return facts, contexts, patents, canonicals, manifest


def load_hyperedge_projection_inputs(kg_dir: Path) -> tuple[list[JSON], list[JSON], dict[str, JSON], dict[str, JSON], JSON]:
    """Load the hyperedge KG view inputs.

    ``hyperedges.jsonl`` is optional for legacy KG directories. When present it
    is the L2 semantic fact table for the boss-facing CSV, while ``facts.jsonl``
    remains the lower-level cell/context record surface.
    """

    _facts, contexts, patents, canonicals, manifest = load_kg_projection_inputs(kg_dir)
    hyperedge_path = kg_dir / HYPEREDGE_FILE
    if not hyperedge_path.exists():
        raise FileNotFoundError(f"KG hyperedge projection is incomplete; missing: {hyperedge_path}")
    hyperedges = _read_jsonl(hyperedge_path)
    evidence_path = kg_dir / "evidence_units.jsonl"
    if evidence_path.exists():
        evidence_by_id = {
            str(row.get("evidence_id")): row
            for row in _read_jsonl(evidence_path)
            if row.get("evidence_id")
        }
        hyperedges = [_with_projectable_evidence(row, evidence_by_id) for row in hyperedges]
    return hyperedges, contexts, patents, canonicals, manifest


def _with_projectable_evidence(hyperedge: JSON, evidence_by_id: dict[str, JSON]) -> JSON:
    """Attach projection-only evidence labels from existing L1 evidence units."""

    if hyperedge.get("evidence") not in (None, "", [], {}):
        return hyperedge
    evidence_items: list[JSON] = []
    for evidence_id in _scalar_values(hyperedge.get("evidence_ids")):
        unit = evidence_by_id.get(evidence_id)
        if not unit:
            continue
        evidence_items.append(
            {
                "evidence_id": evidence_id,
                "page": unit.get("page"),
                "table": unit.get("table") or unit.get("section") or unit.get("evidence_type"),
                "row": unit.get("row") or unit.get("sample_id"),
                "unit_id": unit.get("unit_id") or unit.get("evidence_id"),
            }
        )
    if not evidence_items:
        return hyperedge
    out = dict(hyperedge)
    out["evidence"] = evidence_items
    return out


def _scalar_values(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        for key in ("evidence_id", "id", "value"):
            if value.get(key):
                return _scalar_values(value.get(key))
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_scalar_values(item))
        return out
    return []


def group_facts_by_context(facts: list[JSON]) -> dict[str, list[JSON]]:
    grouped: dict[str, list[JSON]] = defaultdict(list)
    for fact in facts:
        context_id = fact.get("context_id")
        if context_id:
            grouped[str(context_id)].append(fact)
    return grouped


def build_subtype_map(canonicals: dict[str, JSON]) -> dict[str, str]:
    return {
        canonical_id: str(row.get("sub_type"))
        for canonical_id, row in canonicals.items()
        if row.get("sub_type")
    }


def property_sub_type_for_fact(
    fact: JSON,
    prop: str,
    subtype_map: dict[str, str],
    canonical_meta: dict[str, JSON],
) -> str:
    """Return the display property bucket without creating semantic facts."""

    override = property_bucket_override(fact, prop, canonical_meta)
    if override:
        return override
    return subtype_map.get(prop, "")


def property_bucket_override(fact: JSON, prop: str, canonical_meta: dict[str, JSON]) -> str:
    """Correct display-only buckets when KG evidence carries an exact method signal."""

    meta = canonical_meta.get(prop) or {}
    condition = fact.get("test_condition") if isinstance(fact.get("test_condition"), dict) else {}
    evidence_pointer = fact.get("evidence_pointer") if isinstance(fact.get("evidence_pointer"), dict) else {}
    standard = str(condition.get("standard_id") or condition.get("test_standard") or "")
    standard_key = re.sub(r"[^A-Z0-9]+", "", standard.upper())
    text = " ".join(
        str(value or "")
        for value in (
            prop,
            meta.get("canonical_name"),
            fact.get("test_method"),
            standard,
            evidence_pointer.get("row"),
            evidence_pointer.get("column"),
            evidence_pointer.get("cell"),
        )
    ).casefold()
    if standard_key.startswith("ASTMD5402") or "mek" in text or "solvent resistance" in text:
        return "Chemical resistance"
    if "chemical resistance" in text:
        return "Chemical resistance"
    return ""


def build_row(
    context: JSON,
    facts: list[JSON],
    subtype_map: dict[str, str],
    canonical_meta: dict[str, JSON],
    patent: JSON | None,
) -> dict[str, str]:
    """Project one ExampleContextRecord into one CSV row."""

    doc_id = str(context.get("doc_id") or _first_non_empty(f.get("doc_id") for f in facts))
    example_id = str(context.get("example_id") or "unknown")
    context_id = str(context.get("context_id") or "")

    polarity = _dominant_polarity(context, facts)
    material_buckets: dict[str, list[str]] = defaultdict(list)
    property_buckets: dict[str, list[str]] = defaultdict(list)

    for fact in facts:
        if is_formulation_quantity_fact(fact, subtype_map):
            for sub_type, value in material_projection_entries(fact, subtype_map):
                _append_unique(material_buckets[sub_type], value)
            continue

        prop = str(fact.get("property") or "")
        sub_type = property_sub_type_for_fact(fact, prop, subtype_map, canonical_meta)
        if sub_type in PROPERTY_SUB_TYPES:
            label = _property_cell(fact, prop)
            if label:
                _append_unique(property_buckets[sub_type], label)

    use_context = context.get("use_context") if isinstance(context.get("use_context"), dict) else {}
    primary_context = (
        context.get("primary_context")
        if isinstance(context.get("primary_context"), dict)
        else {}
    )

    test_methods = _context_values(context, use_context, "test_methods", "test_method")
    test_standards = _context_values(context, use_context, "test_standards", "test_standard")
    if not test_standards:
        test_standards = _unique(extract_test_standard(fact) for fact in facts)

    row = {
        "patent_id": doc_id,
        "example_id": example_id,
        "context_id": context_id,
        "polarity": polarity,
    }
    for sub_type in MATERIAL_SUB_TYPES:
        row[f"Material:{sub_type}"] = "; ".join(material_buckets.get(sub_type, []))
    for sub_type in PROPERTY_SUB_TYPES:
        row[f"Property:{sub_type}"] = "; ".join(property_buckets.get(sub_type, []))

    row["TestMethod"] = "; ".join(short_id(value) for value in test_methods)
    row["TestStandard"] = "; ".join(test_standards)
    row["Application"] = _display_context_value(
        primary_context.get("application") or _first_non_empty(use_context.get("applications") or [])
    )
    row["Substrate"] = _display_context_value(
        primary_context.get("substrate") or _first_non_empty(use_context.get("substrates") or [])
    )
    row["Process"] = "; ".join(short_id(value) for value in _context_values(context, use_context, "processes", "process"))
    row["Evidence"] = _evidence_label(context, facts)

    fact_ids = [str(fact.get("fact_id")) for fact in facts if fact.get("fact_id")]
    row["fact_ids"] = ",".join(fact_ids)
    row["kg_fact_count"] = str(len(fact_ids))
    row["evidence_count"] = str(len(context.get("evidence_ids") or []))
    row["unit_coverage_pct_min"] = _coverage_min(context)

    patent = patent or {}
    row["applicant"] = _display_context_value(patent.get("applicant"))
    row["filing_date"] = _display_context_value(patent.get("filing_date"))
    row["title"] = _display_context_value(patent.get("title"))

    confidences = [
        float(fact["extraction_confidence"])
        for fact in facts
        if isinstance(fact.get("extraction_confidence"), (int, float))
    ]
    row["extraction_confidence_min"] = f"{min(confidences):.2f}" if confidences else ""
    row["extraction_confidence_mean"] = (
        f"{sum(confidences) / len(confidences):.2f}" if confidences else ""
    )

    statuses = _canonical_status_counts(facts, canonical_meta)
    row["canonical_status_summary"] = "; ".join(
        f"{status}:{count}" for status, count in sorted(statuses.items())
    )
    row["proposed_canonical_count"] = str(statuses.get("proposed", 0))
    row["comparison_status"] = _comparison_status(context)
    row["qa_flags"] = _qa_flags(context)
    row["resolution_status"] = _resolution_status(context, facts)
    row["sample_binding_confidence"] = _sample_binding_confidence(context, facts)
    row["context_source_summary"] = _context_source_summary(facts)
    return row


def fieldnames() -> list[str]:
    names = ["patent_id", "example_id", "context_id", "polarity"]
    names += [f"Material:{sub_type}" for sub_type in MATERIAL_SUB_TYPES]
    names += [f"Property:{sub_type}" for sub_type in PROPERTY_SUB_TYPES]
    names += [
        "TestMethod",
        "TestStandard",
        "Application",
        "Substrate",
        "Process",
        "Evidence",
        "fact_ids",
        "kg_fact_count",
        "evidence_count",
        "unit_coverage_pct_min",
        "applicant",
        "filing_date",
        "title",
        "extraction_confidence_min",
        "extraction_confidence_mean",
        "canonical_status_summary",
        "proposed_canonical_count",
        "comparison_status",
        "qa_flags",
        "resolution_status",
        "sample_binding_confidence",
        "context_source_summary",
    ]
    return names


def _property_cell(fact: JSON, prop: str) -> str:
    result = fact_result_text(fact)
    if not result:
        return ""
    evidence_pointer = fact.get("evidence_pointer") or {}
    column = str(evidence_pointer.get("column") or "")
    loc = column.replace("Measurement position", "").strip(" ,")
    prop_short = short_id(prop)
    return f"{prop_short}@{loc}:{result}" if loc else f"{prop_short}:{result}"


def _dominant_polarity(context: JSON, facts: list[JSON]) -> str:
    result_context = context.get("result_context")
    if isinstance(result_context, dict) and result_context.get("dominant_polarity"):
        return str(result_context["dominant_polarity"])
    counts = Counter(str(fact.get("polarity_hint") or "unknown") for fact in facts)
    for value, _ in counts.most_common():
        if value != "unknown":
            return value
    return "unknown"


def _context_values(context: JSON, use_context: JSON, plural_key: str, field: str) -> list[str]:
    values = use_context.get(plural_key)
    if isinstance(values, list) and values:
        return _unique(_iter_scalar_values(values))
    context_fields = context.get("context_fields")
    if isinstance(context_fields, dict):
        rows = context_fields.get(field) or []
        return _unique(row.get("value") for row in rows if isinstance(row, dict))
    return []


def _display_context_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return "unknown"
    if isinstance(value, list):
        return "; ".join(_display_context_value(item) for item in value if item not in (None, ""))
    if isinstance(value, dict):
        tested = value.get("tested")
        if tested:
            return _display_context_value(tested)
        return "; ".join(f"{key}={val}" for key, val in sorted(value.items()) if val not in (None, "", [], {}))
    return str(value)


def _evidence_label(context: JSON, facts: list[JSON]) -> str:
    labels: list[str] = []
    for unit in ((context.get("source_trace") or {}).get("table_units") or []):
        if not isinstance(unit, dict):
            continue
        page = unit.get("page")
        region = unit.get("region_id") or unit.get("unit_id") or ""
        label = f"p{page} {region}".strip()
        _append_unique(labels, label)
    if labels:
        return "; ".join(labels)

    for fact in facts:
        ep = fact.get("evidence_pointer") or {}
        page = ep.get("page")
        region = ep.get("region_id") or fact.get("source_unit_id") or ""
        label = f"p{page} {region}".strip()
        _append_unique(labels, label)
    return "; ".join(labels)


def _coverage_min(context: JSON) -> str:
    coverage = context.get("coverage_summary")
    if not isinstance(coverage, dict):
        return ""
    value = coverage.get("min_coverage_pct")
    if isinstance(value, (int, float)):
        # Some legacy contexts store 0-1, newer coverage uses 0-100.
        pct = float(value) * 100.0 if 0 <= float(value) <= 1 else float(value)
        return f"{pct:.1f}"
    return ""


def _canonical_status_counts(facts: list[JSON], canonical_meta: dict[str, JSON]) -> Counter[str]:
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for fact in facts:
        for canonical_id in _canonical_ids_from_fact(fact):
            if canonical_id in seen:
                continue
            seen.add(canonical_id)
            status = str((canonical_meta.get(canonical_id) or {}).get("status") or "missing")
            counts[status] += 1
    return counts


def _canonical_ids_from_fact(fact: JSON) -> list[str]:
    values: list[str] = []
    for field in ("application", "property", "resin_system", "test_method"):
        value = fact.get(field)
        if isinstance(value, str) and _looks_like_canonical(value):
            values.append(value)
    substrate = fact.get("substrate")
    if isinstance(substrate, dict):
        for value in [substrate.get("tested"), *(substrate.get("claimed") or [])]:
            if isinstance(value, str) and _looks_like_canonical(value):
                values.append(value)
    elif isinstance(substrate, str) and _looks_like_canonical(substrate):
        values.append(substrate)
    for value in fact.get("additives") or []:
        if isinstance(value, str) and _looks_like_canonical(value):
            values.append(value)
    for step in fact.get("process") or []:
        if isinstance(step, dict):
            value = step.get("canonical_id") or step.get("step")
            if isinstance(value, str) and _looks_like_canonical(value):
                values.append(value)
    return values


def _comparison_status(context: JSON) -> str:
    comparison = context.get("comparison_context")
    if not isinstance(comparison, dict):
        return "missing"
    resolution = comparison.get("comparison_resolution_status") or "missing"
    signal = comparison.get("comparison_signal_status") or "missing"
    eligible = comparison.get("trend_eligible")
    return f"{resolution}|{signal}|trend_eligible={bool(eligible)}"


def _qa_flags(context: JSON) -> str:
    flags = context.get("qa_flags")
    if not isinstance(flags, dict):
        return ""
    active = [key for key, value in sorted(flags.items()) if bool(value)]
    return "; ".join(active)


def _resolution_status(context: JSON, facts: list[JSON]) -> str:
    value = context.get("resolution_status")
    if value:
        return str(value)
    statuses = _unique(fact.get("resolution_status") for fact in facts)
    if len(statuses) == 1:
        return statuses[0]
    return "; ".join(statuses)


def _sample_binding_confidence(context: JSON, facts: list[JSON]) -> str:
    quality = context.get("quality") if isinstance(context.get("quality"), dict) else {}
    value = quality.get("min_sample_binding_confidence")
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    confidences = [
        float(fact["sample_binding_confidence"])
        for fact in facts
        if isinstance(fact.get("sample_binding_confidence"), (int, float))
    ]
    return f"{min(confidences):.2f}" if confidences else ""


def _context_source_summary(facts: list[JSON]) -> str:
    return "; ".join(_unique(fact.get("context_source_summary") for fact in facts))


def _iter_scalar_values(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            item = value.get("canonical_id") or value.get("value") or value.get("step")
            if item:
                out.append(str(item))
    return out


def _unique(values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        if value in (None, "", "unknown", [], {}):
            continue
        text = str(value)
        if text not in out:
            out.append(text)
    return out


def _append_unique(target: list[str], value: str) -> None:
    if value and value not in target:
        target.append(value)


def _first_non_empty(values: Any) -> Any:
    if isinstance(values, str):
        return values if values not in ("", "unknown") else None
    for value in values:
        if value not in (None, "", "unknown", [], {}):
            return value
    return None


def _looks_like_canonical(value: str) -> bool:
    return bool(re.match(r"^(MAT|PROP|APP|SUB|PROC|TEST)_", value))


def normalize_material_role(role: str) -> str:
    role = str(role or "").strip()
    if role in MATERIAL_SUB_TYPES:
        return role
    return MATERIAL_ROLE_ALIASES.get(role.lower(), role)


def projection_slot_material_role(role: Any, default_role: str) -> str:
    """Keep the KG slot as projection evidence when the raw role is generic."""

    raw = str(role or "").strip()
    if raw.lower() in GENERIC_SLOT_MATERIAL_ROLES:
        return default_role
    normalized = normalize_material_role(raw)
    return normalized if normalized in MATERIAL_SUB_TYPES else raw


def _read_jsonl(path: Path) -> list[JSON]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def group_hyperedges_by_context(hyperedges: list[JSON]) -> dict[str, list[JSON]]:
    grouped: dict[str, list[JSON]] = defaultdict(list)
    for hyperedge in hyperedges:
        context_id = hyperedge.get("context_id")
        if context_id:
            grouped[str(context_id)].append(hyperedge)
    return grouped


def build_hyperedge_row(
    context: JSON,
    hyperedges: list[JSON],
    canonical_meta: dict[str, JSON],
    patent: JSON | None,
) -> dict[str, str]:
    """Project one ExampleContextRecord from semantic L2 hyperedges."""

    doc_id = str(context.get("doc_id") or context.get("patent_id") or _first_non_empty(h.get("patent_id") for h in hyperedges))
    example_id = str(context.get("example_id") or context.get("sample_id") or "unknown")
    context_id = str(context.get("context_id") or "")

    material_buckets: dict[str, list[str]] = defaultdict(list)
    property_buckets: dict[str, list[str]] = defaultdict(list)

    for hyperedge in hyperedges:
        for material in hyperedge_materials(hyperedge):
            role = normalize_material_role(str(material.get("role") or ""))
            canonical_id = str(material.get("canonical_id") or "")
            if role not in MATERIAL_SUB_TYPES:
                role = str((canonical_meta.get(canonical_id) or {}).get("sub_type") or "")
            if role in MATERIAL_SUB_TYPES and is_display_grade_hyperedge_material(material, role):
                _append_unique(material_buckets[role], hyperedge_material_cell(material))

        sub_type = hyperedge_property_sub_type(hyperedge, canonical_meta)
        if sub_type in PROPERTY_SUB_TYPES and sub_type != "Formulation":
            label = hyperedge_property_cell(hyperedge)
            if label:
                _append_unique(property_buckets[sub_type], label)

    row = {
        "patent_id": doc_id,
        "example_id": example_id,
        "context_id": context_id,
        "polarity": dominant_hyperedge_polarity(context, hyperedges),
    }
    for sub_type in MATERIAL_SUB_TYPES:
        row[f"Material:{sub_type}"] = "; ".join(material_buckets.get(sub_type, []))
    for sub_type in PROPERTY_SUB_TYPES:
        row[f"Property:{sub_type}"] = "; ".join(property_buckets.get(sub_type, []))

    row["TestMethod"] = "; ".join(_unique(hyperedge_test_method_label(h) for h in hyperedges))
    row["TestStandard"] = "; ".join(_unique(hyperedge_standard_label(h) for h in hyperedges))
    row["Application"] = _display_context_value(_first_non_empty(hyperedge_application_label(h) for h in hyperedges))
    row["Substrate"] = _display_context_value(_first_non_empty(hyperedge_substrate_label(h) for h in hyperedges))
    row["Process"] = "; ".join(_unique(value for h in hyperedges for value in hyperedge_process_labels(h)))
    row["Evidence"] = "; ".join(_unique(hyperedge_evidence_label(h) for h in hyperedges))

    hyperedge_ids = [str(h.get("hyperedge_id")) for h in hyperedges if h.get("hyperedge_id")]
    row["fact_ids"] = ",".join(hyperedge_ids)
    row["kg_fact_count"] = str(len(hyperedge_ids))
    row["evidence_count"] = str(
        len(
            {
                evidence.get("evidence_id")
                for h in hyperedges
                for evidence in hyperedge_evidence_items(h)
                if evidence.get("evidence_id")
            }
        )
    )
    row["unit_coverage_pct_min"] = _coverage_min(context)

    patent = patent or {}
    row["applicant"] = _display_context_value(patent.get("applicant") or patent.get("applicants"))
    row["filing_date"] = _display_context_value(patent.get("filing_date"))
    row["title"] = _display_context_value(patent.get("title"))

    confidences = [float(h["confidence"]) for h in hyperedges if isinstance(h.get("confidence"), (int, float))]
    row["extraction_confidence_min"] = f"{min(confidences):.2f}" if confidences else ""
    row["extraction_confidence_mean"] = (
        f"{sum(confidences) / len(confidences):.2f}" if confidences else ""
    )

    statuses = _canonical_status_counts_for_ids(
        (canonical_id for h in hyperedges for canonical_id in _canonical_ids_from_hyperedge(h)),
        canonical_meta,
    )
    row["canonical_status_summary"] = "; ".join(
        f"{status}:{count}" for status, count in sorted(statuses.items())
    )
    row["proposed_canonical_count"] = str(statuses.get("proposed", 0))
    row["comparison_status"] = _comparison_status(context)
    row["qa_flags"] = "; ".join(_unique(value for h in hyperedges for value in h.get("qa_flags") or []))
    row["resolution_status"] = hyperedge_resolution_status(context, hyperedges)
    row["sample_binding_confidence"] = _sample_binding_confidence(context, [])
    if not row["sample_binding_confidence"] and confidences:
        row["sample_binding_confidence"] = f"{min(confidences):.2f}"
    row["context_source_summary"] = "; ".join(_unique(h.get("context_source_summary") for h in hyperedges))
    return row


def build_rows_from_hyperedges(kg_dir: Path) -> tuple[list[dict[str, str]], JSON]:
    hyperedges, contexts, patents, canonicals, manifest = load_hyperedge_projection_inputs(kg_dir)
    hyperedges_by_context = group_hyperedges_by_context(hyperedges)

    rows: list[dict[str, str]] = []
    seen_contexts: set[str] = set()
    for context in sorted(contexts, key=lambda row: str(row.get("context_id"))):
        context_id = str(context.get("context_id") or "")
        if context_id in seen_contexts:
            continue
        seen_contexts.add(context_id)
        context_hyperedges = hyperedges_by_context.get(context_id, [])
        if not context_hyperedges:
            continue
        doc_id = str(context.get("doc_id") or context.get("patent_id") or "")
        rows.append(build_hyperedge_row(context, context_hyperedges, canonicals, patents.get(doc_id)))
    return rows, manifest


def _as_projection_list(value: Any) -> list[Any]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        return value
    return [value]


def hyperedge_materials(hyperedge: JSON) -> list[JSON]:
    out: list[JSON] = []
    for key, default_role in [("resin", "Resin / binder"), ("additives", "Additive")]:
        for item in _as_projection_list(hyperedge.get(key)):
            if isinstance(item, dict):
                row = dict(item)
                row["role"] = projection_slot_material_role(row.get("role"), default_role)
                out.append(row)
            elif isinstance(item, str) and item.strip():
                out.append({"name": item.strip(), "role": default_role})
    material_value = hyperedge.get("material")
    if material_value not in (None, "", [], {}):
        role = str(hyperedge.get("material_role") or hyperedge.get("material_role_class") or "material")
        if isinstance(material_value, dict):
            row = dict(material_value)
            row.setdefault("role", role)
            out.append(row)
        else:
            for item in _as_projection_list(material_value):
                if isinstance(item, dict):
                    row = dict(item)
                    row.setdefault("role", role)
                    out.append(row)
                elif isinstance(item, str) and item.strip():
                    out.append(
                        {
                            "name": item.strip(),
                            "role": role,
                            "amount_text": hyperedge.get("value") or (hyperedge.get("result") or {}).get("text")
                            if isinstance(hyperedge.get("result"), dict)
                            else "",
                            "unit": hyperedge.get("unit") or "",
                        }
                    )
    formulation = hyperedge.get("formulation")
    if isinstance(formulation, dict):
        nested_keys = [
            ("resin", "Resin / binder"),
            ("binder", "Resin / binder"),
            ("binders", "Resin / binder"),
            ("crosslinker", "Crosslinker / Curing agent"),
            ("crosslinkers", "Crosslinker / Curing agent"),
            ("curing_agent", "Crosslinker / Curing agent"),
            ("curing_agents", "Crosslinker / Curing agent"),
            ("pigment", "Pigment"),
            ("pigments", "Pigment"),
            ("filler", "Filler"),
            ("fillers", "Filler"),
            ("additive", "Additive"),
            ("additives", "Additive"),
            ("solvent", "Solvent"),
            ("solvents", "Solvent"),
            ("monomer", "Monomer"),
            ("monomers", "Monomer"),
        ]
        for key, default_role in nested_keys:
            for item in _as_projection_list(formulation.get(key)):
                if isinstance(item, dict):
                    row = dict(item)
                    row["role"] = projection_slot_material_role(row.get("role"), default_role)
                    out.append(row)
                elif isinstance(item, str) and item.strip():
                    out.append({"name": item.strip(), "role": default_role})
    return out


def hyperedge_material_cell(material: JSON) -> str:
    canonical_id = str(material.get("canonical_id") or "")
    amount_raw = material.get("amount")
    amount = amount_raw if isinstance(amount_raw, dict) else {}
    value = str(
        amount.get("value_text")
        or amount.get("value")
        or material.get("amount_text")
        or (amount_raw if isinstance(amount_raw, (int, float, str)) else "")
        or ""
    ).strip()
    unit = str(amount.get("unit") or material.get("unit") or "").strip()
    unit_context = str(amount.get("unit_context_source") or material.get("basis") or "").strip()
    label = short_id(canonical_id) or str(material.get("name") or "")
    suffix = f"{value} {unit}".strip()
    if value and not unit and unit_context:
        suffix = f"{value} [unit_context:{unit_context}]"
    return f"{label}:{suffix}" if suffix else label


def is_display_grade_hyperedge_material(material: JSON, role: str) -> bool:
    """Keep CSV material slots conservative; broad candidates stay in KG QA.

    Answer hyperedges can carry propagated material pools so QA can inspect what
    was available in the local context. The boss-facing CSV should not render
    those candidate pools as if every candidate were a confirmed sample resin.
    """

    if role != "Resin / binder":
        return True
    label = material_display_label(material).lower()
    if not label:
        return False
    if label.strip(" _-:;") in GENERIC_NON_MATERIAL_LABELS:
        return False
    if material_conflicts_with_resin_role(label):
        return False
    if material.get("display_grade") is True or material.get("visually_confirmed") is True:
        return True
    return material_has_nonzero_display_amount(material)


def material_display_label(material: JSON) -> str:
    canonical_id = str(material.get("canonical_id") or "")
    return short_id(canonical_id) or str(material.get("name") or "")


def material_has_nonzero_display_amount(material: JSON) -> bool:
    amount_raw = material.get("amount")
    amount = amount_raw if isinstance(amount_raw, dict) else {}
    value = str(
        amount.get("value_text")
        or amount.get("value")
        or material.get("amount_text")
        or (amount_raw if isinstance(amount_raw, (int, float, str)) else "")
        or ""
    ).strip()
    if not value:
        return False
    numeric_values = re.findall(r"[-+]?\d+(?:\.\d+)?", value.replace(",", "."))
    if not numeric_values:
        return False
    try:
        return any(float(item) != 0.0 for item in numeric_values)
    except ValueError:
        return False


def material_conflicts_with_resin_role(label: str) -> bool:
    padded = f" {label} "
    return any(hint in padded for hint in RESIN_ROLE_CONFLICT_HINTS)


def hyperedge_property_cell(hyperedge: JSON) -> str:
    prop_raw = hyperedge.get("property")
    prop = prop_raw if isinstance(prop_raw, dict) else {}
    result = hyperedge.get("result") if isinstance(hyperedge.get("result"), dict) else {}
    value = str(result.get("value_text") or result.get("text") or result.get("value") or "").strip()
    if not value:
        return ""
    unit = str(result.get("unit") or "").strip()
    unit_context = str(result.get("unit_context_source") or "").strip()
    display_value = f"{value} {unit}".strip()
    if value and not unit and unit_context:
        display_value = f"{value} [unit_context:{unit_context}]"
    evidence = next(iter(hyperedge_evidence_items(hyperedge)), {})
    loc = str(evidence.get("row") or evidence.get("table") or "").strip()
    prop_id = str(prop.get("canonical_id") or "")
    prop_label = short_id(prop_id) or str(prop.get("name") or prop_raw or "")
    return f"{prop_label}@{loc}:{display_value}" if loc else f"{prop_label}:{display_value}"


def hyperedge_property_sub_type(hyperedge: JSON, canonical_meta: dict[str, JSON]) -> str:
    prop_raw = hyperedge.get("property")
    if isinstance(prop_raw, dict):
        canonical_id = str(prop_raw.get("canonical_id") or "")
        return str(prop_raw.get("sub_type") or (canonical_meta.get(canonical_id) or {}).get("sub_type") or "")
    prop = str(prop_raw or "").lower()
    if any(value in prop for value in ["formulation", "composition", "amount", "component", "ingredient"]):
        return "Formulation"
    if "chemical" in prop or "solvent" in prop or "mek" in prop or "acid" in prop or "alkali" in prop:
        return "Chemical resistance"
    if any(value in prop for value in ["hardness", "adhesion", "scratch", "abrasion", "flexibility", "tensile", "elongation", "impact"]):
        return "Mechanical"
    if any(value in prop for value in ["gloss", "contact angle", "surface", "slip", "haze"]):
        return "Surface"
    if any(value in prop for value in ["corrosion", "weather", "aging", "humidity", "salt"]):
        return "Aging / Corrosion / Durability"
    if any(value in prop for value in ["viscosity", "application", "spray", "flow"]):
        return "Application properties"
    if any(value in prop for value in ["appearance", "color", "colour"]):
        return "Appearance"
    if any(value in prop for value in ["thermal", "temperature", "heat"]):
        return "Thermal"
    return ""


def hyperedge_test_method_label(hyperedge: JSON) -> str:
    raw = hyperedge.get("test_method")
    if isinstance(raw, dict):
        return str(raw.get("standard_id") or short_id(str(raw.get("canonical_id") or "")) or raw.get("name") or raw.get("value") or "")
    if isinstance(raw, list):
        return "; ".join(_unique(hyperedge_test_method_label({"test_method": item}) for item in raw))
    return str(raw or "")


def hyperedge_standard_label(hyperedge: JSON) -> str:
    condition = hyperedge.get("test_condition") if isinstance(hyperedge.get("test_condition"), dict) else {}
    method = hyperedge.get("test_method") if isinstance(hyperedge.get("test_method"), dict) else {}
    if condition.get("standard_id") or method.get("standard_id"):
        return str(condition.get("standard_id") or method.get("standard_id") or "")
    raw_method = hyperedge.get("test_method")
    if isinstance(raw_method, str) and raw_method.upper().startswith(("ASTM", "ISO", "DIN", "GB/", "JIS")):
        return raw_method
    return ""


def hyperedge_application_label(hyperedge: JSON) -> str:
    raw = hyperedge.get("application")
    if isinstance(raw, dict):
        return str(raw.get("value") or short_id(str(raw.get("canonical_id") or "")) or raw.get("name") or "")
    if isinstance(raw, list):
        return "; ".join(_unique(hyperedge_application_label({"application": item}) for item in raw))
    return str(raw or "")


def hyperedge_substrate_label(hyperedge: JSON) -> str:
    raw = hyperedge.get("substrate")
    if isinstance(raw, dict):
        tested = raw.get("tested")
        if isinstance(tested, dict):
            return str(tested.get("name") or tested.get("value") or short_id(str(tested.get("canonical_id") or "")) or "")
        if isinstance(tested, list):
            return "; ".join(_unique(str(item) for item in tested if item))
        return str(raw.get("tested_text") or short_id(str(tested or "")) or raw.get("value") or raw.get("name") or "")
    if isinstance(raw, list):
        return "; ".join(_unique(hyperedge_substrate_label({"substrate": item}) for item in raw))
    return str(raw or "")


def hyperedge_process_labels(hyperedge: JSON) -> list[str]:
    out: list[str] = []
    for step in _as_projection_list(hyperedge.get("process")):
        if not isinstance(step, dict):
            if isinstance(step, str) and step.strip():
                _append_unique(out, step.strip())
            continue
        label = str(step.get("condition") or short_id(str(step.get("canonical_id") or "")) or "")
        _append_unique(out, label)
    return out


def hyperedge_evidence_label(hyperedge: JSON) -> str:
    labels: list[str] = []
    for evidence in hyperedge_evidence_items(hyperedge):
        page = evidence.get("page")
        region = evidence.get("table") or evidence.get("row") or evidence.get("unit_id") or evidence.get("evidence_id") or evidence.get("source_block_id") or ""
        _append_unique(labels, f"p{page} {region}".strip())
    return "; ".join(labels)


def hyperedge_evidence_items(hyperedge: JSON) -> list[JSON]:
    evidence = hyperedge.get("evidence")
    if isinstance(evidence, dict):
        return [evidence]
    if isinstance(evidence, list):
        return [item for item in evidence if isinstance(item, dict)]
    return []


def dominant_hyperedge_polarity(context: JSON, hyperedges: list[JSON]) -> str:
    result_context = context.get("result_context")
    if isinstance(result_context, dict) and result_context.get("dominant_polarity"):
        return str(result_context["dominant_polarity"])
    counts = Counter(str(h.get("polarity") or "unknown") for h in hyperedges)
    for value, _ in counts.most_common():
        if value != "unknown":
            return value
    return "unknown"


def hyperedge_resolution_status(context: JSON, hyperedges: list[JSON]) -> str:
    unresolved = sorted({str(field) for h in hyperedges for field in h.get("unresolved_fields") or []})
    if unresolved:
        return "scoped_unresolved:" + ",".join(unresolved)
    return str(context.get("resolution_status") or "resolved")


def _canonical_status_counts_for_ids(values: Any, canonical_meta: dict[str, JSON]) -> Counter[str]:
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for canonical_id in values:
        if not canonical_id or canonical_id in seen:
            continue
        seen.add(str(canonical_id))
        status = str((canonical_meta.get(str(canonical_id)) or {}).get("status") or "missing")
        counts[status] += 1
    return counts


def _canonical_ids_from_hyperedge(hyperedge: JSON) -> list[str]:
    out: list[str] = []
    for value in (
        (hyperedge.get("application") or {}).get("canonical_id") if isinstance(hyperedge.get("application"), dict) else "",
        (hyperedge.get("substrate") or {}).get("tested") if isinstance(hyperedge.get("substrate"), dict) else "",
        (hyperedge.get("property") or {}).get("canonical_id") if isinstance(hyperedge.get("property"), dict) else "",
        (hyperedge.get("test_method") or {}).get("canonical_id") if isinstance(hyperedge.get("test_method"), dict) else "",
    ):
        if isinstance(value, str) and _looks_like_canonical(value):
            out.append(value)
    for material in hyperedge_materials(hyperedge):
        value = material.get("canonical_id")
        if isinstance(value, str) and _looks_like_canonical(value):
            out.append(value)
    for step in hyperedge.get("process") or []:
        if isinstance(step, dict):
            value = step.get("canonical_id")
            if isinstance(value, str) and _looks_like_canonical(value):
                out.append(value)
    return out


def build_rows_from_kg(kg_dir: Path) -> tuple[list[dict[str, str]], JSON]:
    if (kg_dir / HYPEREDGE_FILE).exists():
        return build_rows_from_hyperedges(kg_dir)

    facts, contexts, patents, canonicals, manifest = load_kg_projection_inputs(kg_dir)
    facts_by_context = group_facts_by_context(facts)
    subtype_map = build_subtype_map(canonicals)

    rows: list[dict[str, str]] = []
    seen_contexts: set[str] = set()
    for context in sorted(contexts, key=lambda row: str(row.get("context_id"))):
        context_id = str(context.get("context_id") or "")
        if context_id in seen_contexts:
            continue
        seen_contexts.add(context_id)
        context_facts = facts_by_context.get(context_id, [])
        if not context_facts:
            continue
        doc_id = str(context.get("doc_id") or "")
        rows.append(
            build_row(
                context,
                context_facts,
                subtype_map,
                canonicals,
                patents.get(doc_id),
            )
        )
    return rows, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build wide coatings CSV from data/kg JSONL.")
    parser.add_argument(
        "--kg-dir",
        type=Path,
        default=REPO / "data" / "kg",
        help="KG JSONL directory (default: data/kg).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO / "output" / "coatings_wide.csv",
        help="CSV output path (default: output/coatings_wide.csv).",
    )
    args = parser.parse_args()

    kg_dir = args.kg_dir.resolve()
    csv_path = args.output.resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        rows, manifest = build_rows_from_kg(kg_dir)
    except Exception as exc:
        print(f"ERROR: cannot build CSV from KG projection: {exc}", file=sys.stderr)
        return 1

    names = fieldnames()
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Reading KG from: {kg_dir}")
    print(f"Writing CSV to: {csv_path}")
    print(f"KG schema: {manifest.get('schema_version', 'unknown')}")
    print(f"Wrote {len(rows)} rows x {len(names)} columns")
    if rows:
        print("Sample:")
        for row in rows[:2]:
            print(f"  {row['patent_id']} / {row['example_id']} / {row['polarity']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
