"""Fact and ExampleContext record builders for KG projection."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .edges import _EdgeSink
from .ids import (
    _context_id,
    _doc_from_unit,
    _evidence_id,
    _fact_node_id,
    _looks_like_canonical,
    _patent_id,
    _stable_key,
)
from .io import _read_json, _rel

JSON = dict[str, Any]


def _is_unresolved_example(value: Any) -> bool:
    return value in (None, "", "unknown", "UNKNOWN", "n/a", "N/A", "not_reported")


def _collect_facts_and_examples(
    project_root: Path,
    evidence_units: dict[str, JSON],
    edges: "_EdgeSink",
    *,
    doc_ids: set[str] | None = None,
) -> tuple[list[JSON], list[JSON]]:
    units_dir = project_root / "data" / "units"
    facts: list[JSON] = []
    examples: dict[str, JSON] = {}

    for raw_path in sorted(units_dir.glob("U_*/facts.json")):
        if not _unit_in_doc_filter(raw_path.parent, doc_ids):
            continue
        normalized_path = raw_path.parent / "normalized_facts.json"
        if not normalized_path.exists():
            raise FileNotFoundError(
                "KG export requires Stage 7.5 normalized facts; "
                f"missing {normalized_path}"
            )

    for path in sorted(units_dir.glob("U_*/normalized_facts.json")):
        if not _unit_in_doc_filter(path.parent, doc_ids):
            continue
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        unit_id = path.parent.name
        raw_facts = data.get("facts") or []

        for raw_fact in raw_facts:
            if not isinstance(raw_fact, dict):
                continue
            fact = dict(raw_fact)
            fact_id = str(fact.get("fact_id") or "")
            if not fact_id:
                continue
            doc_id = str(fact.get("doc_id") or _doc_from_unit(unit_id) or "")
            evidence_pointer = fact.get("evidence_pointer") or {}
            pointer_unit_id = str(evidence_pointer.get("unit_id") or unit_id)
            evidence_id = _evidence_id(pointer_unit_id)
            raw_example_id = fact.get("example_id")
            if _is_unresolved_example(raw_example_id):
                example_id = "unknown"
                fact["example_id"] = example_id
                fact.setdefault("resolution_status", "unresolved_sample")
                unresolved_key = f"UNRESOLVED_{pointer_unit_id}_{_stable_key(fact_id)}"
                fact.setdefault("unresolved_context_key", unresolved_key)
                context_id = _context_id(doc_id, unresolved_key)
            else:
                example_id = str(raw_example_id)
                fact["example_id"] = example_id
                fact.setdefault("resolution_status", "resolved")
                context_id = _context_id(doc_id, example_id)

            rec = dict(fact)
            rec["node_id"] = _fact_node_id(fact_id)
            rec["record_type"] = "FactRecord"
            rec["source_path"] = _rel(path, project_root)
            rec["source_unit_id"] = pointer_unit_id
            rec["evidence_id"] = evidence_id
            rec["context_id"] = context_id
            facts.append(rec)

            if doc_id:
                edges.add(_patent_id(doc_id), "HAS_FACT", rec["node_id"])
            edges.add(rec["node_id"], "SUPPORTED_BY", evidence_id)
            edges.add(rec["node_id"], "HAS_CONTEXT", context_id)
            _add_fact_canonical_edges(rec, edges)

            ctx = examples.setdefault(
                context_id,
                {
                    "schema_version": "example_context_v1",
                    "context_id": context_id,
                    "node_id": context_id,
                    "node_type": "EXAMPLE_CONTEXT",
                    "record_type": "ExampleContextRecord",
                    "derived_from": "FactRecord",
                    "doc_id": doc_id,
                    "example_id": example_id,
                    "example_id_normalized": example_id,
                    "raw_example_ids": [],
                    "example_kind": "unknown",
                    "example_kind_conflict": False,
                    "example_kind_evidence": [],
                    "resolution_statuses": [],
                    "fact_ids": [],
                    "evidence_ids": [],
                    "formulation_fact_ids": [],
                    "performance_fact_ids": [],
                    "applications": [],
                    "properties": [],
                    "substrates": [],
                    "resin_systems": [],
                    "additives": [],
                    "processes": [],
                    "test_methods": [],
                    "test_standards": [],
                    "polarity_counts": {},
                    "context_sources": {},
                    "context_fields": {},
                    "primary_context": {},
                    "source_evidence": [],
                    "source_trace": {
                        "evidence_ids": [],
                        "primary_evidence_id": None,
                        "source_pages": [],
                        "table_units": [],
                        "row_labels": [],
                        "column_labels": [],
                    },
                    "comparison_context": {
                        "comparison_groups": [],
                        "baseline_context_ids": [],
                        "compares_to_context_ids": [],
                        "comparison_signal_status": "missing",
                        "comparison_resolution_status": "missing",
                    },
                    "coverage_summary": {},
                    "formulation_context": {},
                    "use_context": {},
                    "result_context": {},
                    "qa_flags": {},
                    "quality": {
                        "fact_count": 0,
                        "evidence_count": 0,
                        "source_evidence_count": 0,
                        "mean_fact_confidence": None,
                        "min_fact_confidence": None,
                        "mean_sample_binding_confidence": None,
                        "min_sample_binding_confidence": None,
                        "low_confidence_fact_count": 0,
                        "conflict_count": 0,
                    },
                    "_confidence_values": [],
                    "_sample_binding_confidences": [],
                    "_evidence_counts": {},
                    "_example_kind_counts": {},
                    "_formulation_processes": [],
                    "_performance_processes": [],
                    "_formulation_properties": [],
                    "_performance_properties": [],
                },
            )
            _update_example_context(ctx, rec, evidence_units.get(evidence_id))
            if doc_id:
                edges.add(_patent_id(doc_id), "HAS_EXAMPLE", context_id)
                edges.add(context_id, "IN_PATENT", _patent_id(doc_id))
            edges.add(context_id, "HAS_FACT", rec["node_id"])
            if evidence_id in evidence_units:
                edges.add(context_id, "HAS_EVIDENCE", evidence_id)
            _add_example_canonical_edges(context_id, rec, edges)

    finalized_examples = [_finalize_example_context(ctx) for ctx in examples.values()]
    _resolve_comparisons(facts, finalized_examples, edges)
    return sorted(facts, key=lambda row: str(row.get("fact_id"))), sorted(
        finalized_examples, key=lambda row: str(row.get("context_id"))
    )


def _unit_in_doc_filter(unit_dir: Path, doc_ids: set[str] | None) -> bool:
    if doc_ids is None:
        return True
    meta = _read_json(unit_dir / "meta.json")
    doc_id = ""
    if isinstance(meta, dict):
        doc_id = str(meta.get("doc_id") or "")
    if not doc_id:
        doc_id = str(_doc_from_unit(unit_dir.name) or "")
    return doc_id in doc_ids

def _add_fact_canonical_edges(fact: JSON, edges: "_EdgeSink") -> None:
    for field, edge_type in (
        ("application", "HAS_APPLICATION"),
        ("property", "HAS_PROPERTY"),
        ("resin_system", "HAS_RESIN_SYSTEM"),
        ("test_method", "HAS_TEST_METHOD"),
    ):
        value = fact.get(field)
        if _looks_like_canonical(value):
            edges.add(fact["node_id"], edge_type, str(value))

    substrate = fact.get("substrate")
    if isinstance(substrate, dict):
        for value in [substrate.get("tested"), *(substrate.get("claimed") or [])]:
            if _looks_like_canonical(value):
                edges.add(fact["node_id"], "HAS_SUBSTRATE", str(value))

    for value in fact.get("additives") or []:
        if _looks_like_canonical(value):
            edges.add(fact["node_id"], "HAS_ADDITIVE", str(value))

    for step in fact.get("process") or []:
        if not isinstance(step, dict):
            continue
        value = step.get("canonical_id") or step.get("step")
        if _looks_like_canonical(value):
            edges.add(fact["node_id"], "HAS_PROCESS", str(value))

def _add_example_canonical_edges(context_id: str, fact: JSON, edges: "_EdgeSink") -> None:
    pseudo_fact = dict(fact)
    pseudo_fact["node_id"] = context_id
    _add_fact_canonical_edges(pseudo_fact, edges)

def _update_example_context(ctx: JSON, fact: JSON, evidence: JSON | None) -> None:
    _append_unique(ctx["fact_ids"], fact.get("fact_id"))
    _append_unique(ctx["evidence_ids"], fact.get("evidence_id"))
    _append_unique(ctx["source_trace"]["evidence_ids"], fact.get("evidence_id"))
    _append_unique(ctx["raw_example_ids"], fact.get("example_id"))
    _append_unique(ctx["resolution_statuses"], fact.get("resolution_status"))
    _append_unique(ctx["applications"], fact.get("application"))
    _append_unique(ctx["properties"], fact.get("property"))
    _append_unique(ctx["resin_systems"], fact.get("resin_system"))
    _append_unique(ctx["test_methods"], fact.get("test_method"))
    _append_unique(ctx["test_standards"], _fact_test_standard(fact))
    _append_confidence(ctx, fact.get("extraction_confidence"))
    _append_sample_binding_confidence(ctx, fact.get("sample_binding_confidence"))

    fact_id = str(fact.get("fact_id") or "")
    evidence_id = str(fact.get("evidence_id") or "")
    if evidence_id:
        ctx["_evidence_counts"][evidence_id] = int(ctx["_evidence_counts"].get(evidence_id, 0)) + 1
    _append_fact_bucket(ctx, fact, evidence)
    _append_fact_trace(ctx, fact, evidence)

    _append_example_kind_evidence(ctx, fact, evidence_id)

    provenance = fact.get("context_provenance") if isinstance(fact.get("context_provenance"), dict) else {}

    _add_context_field(ctx, "application", fact.get("application"), fact_id, evidence_id, provenance)
    _add_context_field(ctx, "property", fact.get("property"), fact_id, evidence_id, provenance)
    _add_context_field(ctx, "resin_system", fact.get("resin_system"), fact_id, evidence_id, provenance)
    _add_context_field(ctx, "test_method", fact.get("test_method"), fact_id, evidence_id, provenance)
    _add_context_field(ctx, "test_standard", _fact_test_standard(fact), fact_id, evidence_id, provenance)

    substrate = fact.get("substrate")
    if isinstance(substrate, dict):
        _append_unique(ctx["substrates"], substrate.get("tested"))
        _add_context_field(ctx, "substrate", substrate.get("tested"), fact_id, evidence_id, provenance)
        for value in substrate.get("claimed") or []:
            _append_unique(ctx["substrates"], value)
            _add_context_field(ctx, "substrate", value, fact_id, evidence_id, provenance)

    for value in fact.get("additives") or []:
        _append_unique(ctx["additives"], value)
        _add_context_field(ctx, "additive", value, fact_id, evidence_id, provenance)

    for step in fact.get("process") or []:
        if isinstance(step, dict):
            process_value = step.get("canonical_id") or step.get("step")
            _append_unique(ctx["processes"], process_value)
            _add_context_field(ctx, "process", process_value, fact_id, evidence_id, provenance)

    polarity = str(fact.get("polarity_hint") or "unknown")
    ctx["polarity_counts"][polarity] = int(ctx["polarity_counts"].get(polarity, 0)) + 1

    if isinstance(provenance, dict):
        for field, value in provenance.items():
            ctx["context_sources"].setdefault(field, []).append(value)

    if evidence:
        _append_source_evidence(ctx, evidence, fact.get("example_id"))

def _fact_test_standard(fact: JSON) -> str:
    condition = fact.get("test_condition")
    if not isinstance(condition, dict):
        return ""
    for key in ("standard_id", "test_standard", "standard"):
        value = condition.get(key)
        if value:
            return str(value)
    return ""

def _add_context_field(
    ctx: JSON,
    field: str,
    value: Any,
    fact_id: str,
    evidence_id: str,
    provenance: JSON,
) -> None:
    if value in (None, "", "unknown", [], {}):
        return
    if isinstance(value, (dict, list)):
        value_key = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        value_key = str(value)
    field_map = ctx["context_fields"].setdefault(field, {})
    rec = field_map.setdefault(
        value_key,
        {
            "value": value,
            "fact_ids": [],
            "evidence_ids": [],
            "sources": [],
            "source_refs": [],
        },
    )
    _append_unique(rec["fact_ids"], fact_id)
    _append_unique(rec["evidence_ids"], evidence_id)
    source = _context_source_for_field(field, provenance)
    if source:
        _append_unique(rec["sources"], source)
    ref: JSON = {"source": source or "fact_explicit", "fact_id": fact_id, "evidence_id": evidence_id}
    prov = _provenance_for_field(field, provenance)
    if prov:
        if prov.get("confidence") is not None:
            ref["confidence"] = prov.get("confidence")
        if prov.get("confidence_cap") is not None:
            ref["confidence_cap"] = prov.get("confidence_cap")
        if prov.get("ledger_ids"):
            ref["ledger_ids"] = prov.get("ledger_ids")
        if prov.get("assertion_id"):
            ref["assertion_id"] = prov.get("assertion_id")
    _append_unique_by_key(rec["source_refs"], ref, ("source", "fact_id", "evidence_id"), limit=12)

def _context_source_for_field(field: str, provenance: JSON) -> str:
    prov = _provenance_for_field(field, provenance)
    if prov:
        return str(prov.get("source") or "context_inheritance")
    return "fact_explicit"

def _provenance_for_field(field: str, provenance: JSON) -> JSON | None:
    aliases = {
        "application": ("application",),
        "substrate": ("substrate",),
        "process": ("process",),
        "test_method": ("test_method",),
        "test_standard": ("test_standard", "test_condition"),
        "resin_system": ("resin_system",),
        "additive": ("additives", "additive"),
        "property": ("property",),
    }
    for key in aliases.get(field, (field,)):
        if key in provenance and isinstance(provenance[key], dict):
            return provenance[key]
    return None

def _append_fact_bucket(ctx: JSON, fact: JSON, evidence: JSON | None) -> None:
    is_formulation = _is_formulation_fact(fact, evidence)
    target = ctx["formulation_fact_ids"] if is_formulation else ctx["performance_fact_ids"]
    _append_unique(target, fact.get("fact_id"))
    property_target = ctx["_formulation_properties"] if is_formulation else ctx["_performance_properties"]
    _append_unique(property_target, fact.get("property"))
    process_target = ctx["_formulation_processes"] if is_formulation else ctx["_performance_processes"]
    for step in fact.get("process") or []:
        if isinstance(step, dict):
            _append_unique(process_target, step.get("canonical_id") or step.get("step"))

def _is_formulation_fact(fact: JSON, evidence: JSON | None) -> bool:
    text_parts = [
        fact.get("property"),
        fact.get("property_sub_type"),
        fact.get("sub_type"),
        evidence.get("table_subject") if evidence else None,
        evidence.get("table_type") if evidence else None,
    ]
    text = " ".join(str(part).casefold() for part in text_parts if part)
    formulation_markers = (
        "formulation",
        "composition",
        "weight",
        "wt",
        "amount",
        "ingredient",
        "component",
        "phr",
        "pbw",
    )
    return any(marker in text for marker in formulation_markers)

def _append_fact_trace(ctx: JSON, fact: JSON, evidence: JSON | None) -> None:
    pointer = fact.get("evidence_pointer") if isinstance(fact.get("evidence_pointer"), dict) else {}
    page = pointer.get("page")
    if page is None and evidence:
        page = evidence.get("page")
    _append_unique(ctx["source_trace"]["source_pages"], page)
    _append_unique(ctx["source_trace"]["row_labels"], pointer.get("row"))
    _append_unique(ctx["source_trace"]["column_labels"], pointer.get("column"))

    comparison_group = fact.get("comparison_group")
    _append_unique(ctx["comparison_context"]["comparison_groups"], comparison_group)

    if evidence:
        _append_table_unit_trace(ctx, evidence)

def _append_table_unit_trace(ctx: JSON, evidence: JSON) -> None:
    unit_id = evidence.get("unit_id")
    if not unit_id:
        return
    rec = {
        "evidence_id": evidence.get("evidence_id"),
        "unit_id": unit_id,
        "evidence_type": evidence.get("evidence_type"),
        "page": evidence.get("page"),
        "region_id": evidence.get("region_id"),
        "bbox": evidence.get("bbox"),
        "table_subject": evidence.get("table_subject"),
        "table_type": evidence.get("table_type"),
        "caption_excerpt": str(evidence.get("caption") or "")[:240],
        "paths": evidence.get("paths") or {},
        "coverage": evidence.get("coverage"),
    }
    _append_unique_by_key(ctx["source_trace"]["table_units"], rec, ("unit_id",), limit=24)

def _infer_example_kind(fact: JSON) -> str:
    example = str(fact.get("example_id") or "").casefold()
    polarity = str(fact.get("polarity_hint") or "").casefold()
    if "compar" in example or re.match(r"^c\d+", example):
        return "comparative"
    if "commercial" in example or "reference" in example or "ref" in example:
        return "reference"
    if "positive" in polarity or re.match(r"^(e|i|inv)\d+", example):
        return "inventive"
    if "negative" in polarity:
        return "comparative"
    return "unknown"

def _append_example_kind_evidence(ctx: JSON, fact: JSON, evidence_id: str) -> None:
    kind = _infer_example_kind(fact)
    if kind == "unknown":
        return
    ctx["_example_kind_counts"][kind] = int(ctx["_example_kind_counts"].get(kind, 0)) + 1
    rec = {
        "kind": kind,
        "fact_id": fact.get("fact_id"),
        "evidence_id": evidence_id,
        "example_id": fact.get("example_id"),
        "polarity_hint": fact.get("polarity_hint"),
    }
    _append_unique_by_key(ctx["example_kind_evidence"], rec, ("kind", "fact_id", "evidence_id"), limit=24)

def _append_source_evidence(ctx: JSON, evidence: JSON, example_id: Any) -> None:
    evidence_id = str(evidence.get("evidence_id") or "")
    if not evidence_id:
        return
    for source, text, score, reason in _evidence_snippets(evidence, str(example_id or "")):
        if not text:
            continue
        rec = {
            "evidence_id": evidence_id,
            "source": source,
            "page": evidence.get("page"),
            "text": text[:500],
        }
        if score is not None:
            rec["score"] = score
        if reason:
            rec["reason"] = reason[:240]
        _append_unique_by_key(ctx["source_evidence"], rec, ("evidence_id", "source", "text"), limit=16)

def _evidence_snippets(evidence: JSON, example_id: str) -> list[tuple[str, str, Any, str | None]]:
    snippets: list[tuple[str, str, Any, str | None]] = []
    caption = str(evidence.get("caption") or "").strip()
    if caption:
        snippets.append(("caption", caption, None, None))
    description = str(evidence.get("vlm_description") or "").strip()
    if description:
        snippets.append(("vlm_description", description, None, None))

    example_norm = example_id.casefold()
    for para in evidence.get("related_paragraphs") or []:
        if not isinstance(para, dict):
            continue
        text = str(para.get("text") or "").strip()
        if not text:
            continue
        score = para.get("score")
        mentions_example = bool(
            example_norm and example_norm != "unknown" and example_norm in text.casefold()
        )
        try:
            high_score = float(score or 0.0) >= 0.9
        except (TypeError, ValueError):
            high_score = False
        if mentions_example or high_score:
            snippets.append(("matched_paragraph", text, score, para.get("reason")))
    return snippets

def _append_unique_by_key(target: list[JSON], value: JSON, keys: tuple[str, ...], *, limit: int) -> None:
    identity = tuple(value.get(key) for key in keys)
    for row in target:
        if tuple(row.get(key) for key in keys) == identity:
            return
    if len(target) < limit:
        target.append(value)

def _append_confidence(ctx: JSON, value: Any) -> None:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return
    if 0.0 <= conf <= 1.0:
        ctx["_confidence_values"].append(conf)


def _append_sample_binding_confidence(ctx: JSON, value: Any) -> None:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return
    if 0.0 <= conf <= 1.0:
        ctx["_sample_binding_confidences"].append(conf)


def _finalize_example_context(ctx: JSON) -> JSON:
    conflicts: dict[str, bool] = {}
    for field, values in list(ctx.get("context_fields", {}).items()):
        if not isinstance(values, dict):
            continue
        rows = []
        for rec in values.values():
            fact_count = len(rec.get("fact_ids") or [])
            evidence_count = len(rec.get("evidence_ids") or [])
            out = {
                "value": rec.get("value"),
                "fact_count": fact_count,
                "evidence_count": evidence_count,
                "fact_ids": rec.get("fact_ids") or [],
                "evidence_ids": rec.get("evidence_ids") or [],
                "sources": rec.get("sources") or [],
                "source_refs": rec.get("source_refs") or [],
                "confidence": _context_field_confidence(rec),
            }
            rows.append(out)
        rows.sort(key=lambda row: (-int(row["fact_count"]), str(row["value"])))
        ctx["context_fields"][field] = rows
        conflicts[field] = len(rows) > 1
        if rows and field in {"application", "substrate", "test_method"} and _is_unambiguous_primary(rows):
            ctx["primary_context"][field] = rows[0]["value"]

    confidence_values = ctx.pop("_confidence_values", [])
    ctx["quality"]["fact_count"] = len(ctx.get("fact_ids") or [])
    ctx["quality"]["evidence_count"] = len(ctx.get("evidence_ids") or [])
    ctx["quality"]["source_evidence_count"] = len(ctx.get("source_evidence") or [])
    if confidence_values:
        ctx["quality"]["mean_fact_confidence"] = round(sum(confidence_values) / len(confidence_values), 3)
        ctx["quality"]["min_fact_confidence"] = round(min(confidence_values), 3)
        ctx["quality"]["low_confidence_fact_count"] = sum(1 for value in confidence_values if value < 0.85)
    sample_binding_confidences = ctx.pop("_sample_binding_confidences", [])
    if sample_binding_confidences:
        ctx["quality"]["mean_sample_binding_confidence"] = round(
            sum(sample_binding_confidences) / len(sample_binding_confidences), 3
        )
        ctx["quality"]["min_sample_binding_confidence"] = round(min(sample_binding_confidences), 3)
    statuses = [str(value) for value in ctx.get("resolution_statuses") or [] if value]
    if statuses:
        ctx["resolution_status"] = statuses[0] if len(set(statuses)) == 1 else "mixed"
    _finalize_example_kind(ctx)
    ctx["conflicts"] = {field: value for field, value in sorted(conflicts.items()) if value}
    ctx["quality"]["conflict_count"] = len(ctx["conflicts"])
    _finalize_source_trace(ctx)
    _finalize_domain_views(ctx)
    ctx["coverage_summary"] = _coverage_summary(ctx)
    ctx["qa_flags"] = _example_qa_flags(ctx)
    for key in list(ctx.keys()):
        if key.startswith("_"):
            ctx.pop(key, None)
    return ctx

def _finalize_example_kind(ctx: JSON) -> None:
    counts = ctx.get("_example_kind_counts") if isinstance(ctx.get("_example_kind_counts"), dict) else {}
    polarity_counts = ctx.get("polarity_counts") if isinstance(ctx.get("polarity_counts"), dict) else {}
    known_polarities = {key for key, value in polarity_counts.items() if key != "unknown" and value}
    if not counts:
        ctx["example_kind"] = "unknown"
        return
    if len(counts) > 1 or len(known_polarities) > 1:
        ctx["example_kind"] = "ambiguous"
        ctx["example_kind_conflict"] = True
        return
    ctx["example_kind"] = sorted(counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[0][0]

def _finalize_source_trace(ctx: JSON) -> None:
    source_trace = ctx.get("source_trace") if isinstance(ctx.get("source_trace"), dict) else {}
    source_trace["source_pages"] = sorted(
        source_trace.get("source_pages") or [], key=lambda value: str(value)
    )
    source_trace["primary_evidence_id"] = None
    evidence_counts = ctx.get("_evidence_counts") if isinstance(ctx.get("_evidence_counts"), dict) else {}
    if evidence_counts:
        source_trace["primary_evidence_id"] = sorted(
            evidence_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )[0][0]

def _finalize_domain_views(ctx: JSON) -> None:
    ctx["formulation_context"] = {
        "resin_systems": ctx.get("resin_systems") or [],
        "additives": ctx.get("additives") or [],
        "properties": ctx.get("_formulation_properties") or [],
        "processes": ctx.get("_formulation_processes") or [],
        "formulation_fact_ids": ctx.get("formulation_fact_ids") or [],
    }
    ctx["use_context"] = {
        "applications": ctx.get("applications") or [],
        "substrates": ctx.get("substrates") or [],
        "processes": ctx.get("_performance_processes") or [],
        "test_methods": ctx.get("test_methods") or [],
        "test_standards": ctx.get("test_standards") or [],
        "performance_fact_ids": ctx.get("performance_fact_ids") or [],
    }
    polarity_counts = ctx.get("polarity_counts") or {}
    known_polarities = {key: value for key, value in polarity_counts.items() if key != "unknown" and value}
    dominant = None
    if polarity_counts and len(known_polarities) <= 1:
        dominant = sorted(polarity_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[0][0]
    ctx["result_context"] = {
        "properties": ctx.get("_performance_properties") or [],
        "polarity_counts": polarity_counts,
        "dominant_polarity": dominant,
        "polarity_conflict": len(known_polarities) > 1,
    }
    groups = ctx["comparison_context"].get("comparison_groups") or []
    if groups:
        ctx["comparison_context"]["comparison_signal_status"] = "has_comparison_group"
        ctx["comparison_context"]["comparison_resolution_status"] = "unresolved_baseline"
    else:
        ctx["comparison_context"]["comparison_signal_status"] = "missing"
        ctx["comparison_context"]["comparison_resolution_status"] = "missing"

def _coverage_summary(ctx: JSON) -> JSON:
    table_units = ((ctx.get("source_trace") or {}).get("table_units") or [])
    values = [_coverage_score(unit) for unit in table_units if isinstance(unit, dict)]
    known_values = [value for value in values if value is not None]
    missing_count = len(values) - len(known_values)
    basis = sorted(
        {
            str((unit.get("coverage") or {}).get("coverage_basis"))
            for unit in table_units
            if isinstance(unit, dict)
            and isinstance(unit.get("coverage"), dict)
            and (unit.get("coverage") or {}).get("coverage_basis")
        }
    )
    return {
        "coverage_values": known_values,
        "min_coverage_pct": round(min(known_values), 3) if known_values else None,
        "coverage_missing_count": missing_count,
        "coverage_unknown": missing_count > 0,
        "coverage_basis_set": basis,
    }

def _example_qa_flags(ctx: JSON) -> JSON:
    source_trace = ctx.get("source_trace") or {}
    table_units = source_trace.get("table_units") or []
    coverage_summary = ctx.get("coverage_summary") if isinstance(ctx.get("coverage_summary"), dict) else {}
    coverage_scores = coverage_summary.get("coverage_values") or []
    low_coverage = [value for value in coverage_scores if value is not None and value < 0.8]
    html_missing = any(
        isinstance(unit, dict)
        and unit.get("evidence_type") == "table"
        and "raw_table_html" not in (unit.get("paths") or {})
        for unit in table_units
    )
    image_missing = any(
        isinstance(unit, dict)
        and unit.get("evidence_type") == "table"
        and not ({"table_image_jpg", "table_image_png"} & set((unit.get("paths") or {}).keys()))
        for unit in table_units
    )
    return {
        "missing_application": not bool(ctx.get("applications")),
        "missing_resin_system": not bool(ctx.get("resin_systems")),
        "missing_substrate": not bool(ctx.get("substrates")),
        "missing_test_method": not bool(ctx.get("test_methods")),
        "missing_test_standard": not bool(ctx.get("test_standards")),
        "mixed_polarity": bool((ctx.get("result_context") or {}).get("polarity_conflict")),
        "multi_table_context": len(table_units) > 1,
        "example_kind_conflict": bool(ctx.get("example_kind_conflict")),
        "unresolved_comparison": (ctx.get("comparison_context") or {}).get("comparison_resolution_status")
        == "unresolved_baseline",
        "low_confidence_fact_count": int((ctx.get("quality") or {}).get("low_confidence_fact_count") or 0),
        "ocr_or_html_missing": html_missing or image_missing,
        "coverage_missing": bool(coverage_summary.get("coverage_unknown")),
        "coverage_below_threshold": bool(low_coverage),
    }

def _coverage_score(unit: JSON) -> float | None:
    coverage = unit.get("coverage")
    if not isinstance(coverage, dict):
        return None
    for key in ("semantic_coverage", "coverage_pct", "completeness_score", "row_coverage"):
        try:
            value = float(coverage[key])
        except (KeyError, TypeError, ValueError):
            continue
        if value > 1.0:
            value /= 100.0
        return max(0.0, min(1.0, value))
    return None

def _resolve_comparisons(facts: list[JSON], contexts: list[JSON], edges: "_EdgeSink") -> None:
    """Resolve comparable facts into context-level baseline relations.

    This is an export-only resolver. It never edits facts; it only enriches
    ExampleContext records and emits explicit comparison edges.
    """

    contexts_by_id = {str(ctx.get("context_id")): ctx for ctx in contexts if ctx.get("context_id")}
    facts_by_id = {str(fact.get("fact_id")): fact for fact in facts if fact.get("fact_id")}
    by_scope: dict[tuple[str, str, str, str, str], list[JSON]] = defaultdict(list)

    for fact in facts:
        if not fact.get("context_id") or not fact.get("property"):
            continue
        if _is_formulation_fact(fact, None):
            continue
        role = _comparison_fact_role(fact)
        if role == "unknown" and not fact.get("comparison_group"):
            continue
        by_scope[_comparison_scope(fact)].append(fact)

    for scope, rows in sorted(by_scope.items(), key=lambda item: str(item[0])):
        ctx_ids = {str(row.get("context_id")) for row in rows if row.get("context_id")}
        if len(ctx_ids) < 2:
            continue
        if not any(_comparison_fact_role(row) in {"inventive", "baseline", "reference"} for row in rows):
            continue

        rows_by_ctx: dict[str, list[JSON]] = defaultdict(list)
        for row in rows:
            rows_by_ctx[str(row.get("context_id"))].append(row)

        roles = {ctx_id: _comparison_context_role(scope_rows) for ctx_id, scope_rows in rows_by_ctx.items()}
        positives = [ctx_id for ctx_id, role in roles.items() if role == "inventive"]
        baselines = [ctx_id for ctx_id, role in roles.items() if role in {"baseline", "reference"}]
        if not positives:
            continue

        for positive_ctx_id in positives:
            positive_rows = rows_by_ctx.get(positive_ctx_id) or []
            target_ctx_id, method, confidence = _resolve_baseline_context(
                positive_rows,
                baselines,
                rows_by_ctx,
                facts_by_id,
                contexts_by_id,
            )
            if not target_ctx_id:
                _mark_unresolved_comparison(
                    contexts_by_id.get(positive_ctx_id),
                    scope,
                    positive_rows,
                    reason="multiple_baselines" if len(baselines) > 1 else "no_baseline",
                )
                continue

            baseline_rows = rows_by_ctx.get(target_ctx_id) or []
            _record_comparison_pair(
                scope,
                positive_ctx_id,
                target_ctx_id,
                positive_rows,
                baseline_rows,
                method,
                confidence,
                contexts_by_id,
                edges,
            )

    for ctx in contexts:
        _finalize_comparison_context(ctx)

def _comparison_scope(fact: JSON) -> tuple[str, str, str, str, str]:
    return (
        str(fact.get("doc_id") or ""),
        str(fact.get("evidence_id") or fact.get("source_unit_id") or ""),
        str(fact.get("property") or ""),
        _stable_key(fact.get("test_method")),
        _measurement_key(fact),
    )

def _measurement_key(fact: JSON) -> str:
    pointer = fact.get("evidence_pointer") if isinstance(fact.get("evidence_pointer"), dict) else {}
    row = str(pointer.get("row") or "").strip()
    column = str(pointer.get("column") or "").strip()
    example_id = str(fact.get("example_id") or "").strip()
    if _label_matches_example(row, example_id):
        return _stable_key({"condition": column, "axis": "column"})
    if _label_matches_example(column, example_id):
        return _stable_key({"condition": row, "axis": "row"})
    test_condition = fact.get("test_condition")
    if test_condition not in (None, "", [], {}, "unknown"):
        return _stable_key(test_condition)
    return _stable_key({"row": row, "column": column})

def _label_matches_example(label: str, example_id: str) -> bool:
    if not label or not example_id:
        return False
    label_norm = _normalize_label(label)
    example_norm = _normalize_label(example_id)
    if not label_norm or not example_norm:
        return False
    if label_norm == example_norm:
        return True
    patterns = {
        example_norm,
        f"example{example_norm}",
        f"comparativeexample{example_norm.removeprefix('c')}",
        f"coating{example_norm}",
        f"modelcoating{example_norm.removeprefix('mc')}",
    }
    return label_norm in patterns or example_norm in label_norm

def _normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())

def _comparison_fact_role(fact: JSON) -> str:
    polarity = str(fact.get("polarity_hint") or "unknown").casefold()
    example = str(fact.get("example_id") or "").casefold()
    pointer = fact.get("evidence_pointer") if isinstance(fact.get("evidence_pointer"), dict) else {}
    labels = " ".join(str(pointer.get(key) or "") for key in ("row", "column")).casefold()
    text = " ".join([example, labels])
    if polarity == "negative" or "comparative" in text or re.match(r"^c\d+", example):
        return "baseline"
    if "commercial" in text or "reference" in text or re.match(r"^ref", example):
        return "reference"
    if polarity == "positive" or re.match(r"^(e|i|inv)\d+", example):
        return "inventive"
    return "unknown"

def _comparison_context_role(rows: list[JSON]) -> str:
    roles = {_comparison_fact_role(row) for row in rows}
    if "inventive" in roles and not ({"baseline", "reference"} & roles):
        return "inventive"
    if "baseline" in roles and "inventive" not in roles:
        return "baseline"
    if "reference" in roles and "inventive" not in roles:
        return "reference"
    if len(roles - {"unknown"}) > 1:
        return "ambiguous"
    return "unknown"

def _resolve_baseline_context(
    positive_rows: list[JSON],
    baselines: list[str],
    rows_by_ctx: dict[str, list[JSON]],
    facts_by_id: dict[str, JSON],
    contexts_by_id: dict[str, JSON],
) -> tuple[str | None, str, float]:
    explicit = _explicit_baseline_contexts(positive_rows, facts_by_id, contexts_by_id)
    valid_explicit = [ctx_id for ctx_id in explicit if ctx_id in baselines]
    if len(valid_explicit) == 1:
        return valid_explicit[0], "explicit_comparison_group", 0.9
    if len(valid_explicit) > 1:
        return None, "ambiguous_explicit_comparison_group", 0.0

    if len(baselines) == 1:
        baseline_role = _comparison_context_role(rows_by_ctx.get(baselines[0]) or [])
        confidence = 0.78 if baseline_role == "reference" else 0.84
        return baselines[0], "unique_baseline_same_scope", confidence
    return None, "unresolved", 0.0

def _explicit_baseline_contexts(
    positive_rows: list[JSON],
    facts_by_id: dict[str, JSON],
    contexts_by_id: dict[str, JSON],
) -> list[str]:
    out: list[str] = []
    for fact in positive_rows:
        group = str(fact.get("comparison_group") or "").strip()
        if not group:
            continue
        if group in facts_by_id:
            _append_unique(out, facts_by_id[group].get("context_id"))
        doc_id = str(fact.get("doc_id") or "")
        for context_id in _candidate_context_ids_from_group(doc_id, group):
            if context_id in contexts_by_id:
                _append_unique(out, context_id)
    return out

def _candidate_context_ids_from_group(doc_id: str, group: str) -> list[str]:
    values = [group]
    if group.startswith("FACT_"):
        values.append(group.removeprefix("FACT_"))
    if group.startswith("F_"):
        values.append(group.removeprefix("F_"))
    if group.startswith("CTX_"):
        values.append(group)
    candidates: list[str] = []
    for value in values:
        if not value:
            continue
        if value.startswith("CTX_"):
            _append_unique(candidates, value)
        else:
            _append_unique(candidates, _context_id(doc_id, value))
    return candidates

def _record_comparison_pair(
    scope: tuple[str, str, str, str, str],
    positive_ctx_id: str,
    baseline_ctx_id: str,
    positive_rows: list[JSON],
    baseline_rows: list[JSON],
    method: str,
    confidence: float,
    contexts_by_id: dict[str, JSON],
    edges: "_EdgeSink",
) -> None:
    positive_ctx = contexts_by_id.get(positive_ctx_id)
    baseline_ctx = contexts_by_id.get(baseline_ctx_id)
    if not positive_ctx or not baseline_ctx:
        return

    fact_ids = [str(row.get("fact_id")) for row in positive_rows if row.get("fact_id")]
    baseline_fact_ids = [str(row.get("fact_id")) for row in baseline_rows if row.get("fact_id")]
    evidence_ids = sorted(
        {
            str(row.get("evidence_id"))
            for row in [*positive_rows, *baseline_rows]
            if row.get("evidence_id")
        }
    )
    comparison_groups = sorted(
        {
            str(row.get("comparison_group"))
            for row in [*positive_rows, *baseline_rows]
            if row.get("comparison_group")
        }
    )
    doc_id, evidence_id, prop, test_method, measurement_key = scope
    pair = {
        "baseline_context_id": baseline_ctx_id,
        "compared_context_id": positive_ctx_id,
        "property": prop,
        "test_method": test_method or None,
        "measurement_key": measurement_key or None,
        "evidence_ids": evidence_ids,
        "fact_ids": fact_ids,
        "baseline_fact_ids": baseline_fact_ids,
        "comparison_groups": comparison_groups,
        "resolution_method": method,
        "confidence": confidence,
    }
    _append_unique_by_key(
        positive_ctx["comparison_context"].setdefault("resolved_pairs", []),
        pair,
        ("baseline_context_id", "property", "test_method", "measurement_key"),
        limit=300,
    )
    _append_unique(positive_ctx["comparison_context"]["baseline_context_ids"], baseline_ctx_id)

    baseline_pair = dict(pair)
    baseline_pair["role"] = "baseline"
    _append_unique_by_key(
        baseline_ctx["comparison_context"].setdefault("resolved_pairs", []),
        baseline_pair,
        ("compared_context_id", "property", "test_method", "measurement_key"),
        limit=300,
    )
    _append_unique(baseline_ctx["comparison_context"]["compares_to_context_ids"], positive_ctx_id)
    baseline_ctx["comparison_context"].setdefault("baseline_for_context_ids", [])
    _append_unique(baseline_ctx["comparison_context"]["baseline_for_context_ids"], positive_ctx_id)

    edge_props = {
        "doc_id": doc_id,
        "property": prop,
        "test_method": test_method or None,
        "measurement_key": measurement_key or None,
        "evidence_id": evidence_id or None,
        "evidence_ids": evidence_ids,
        "fact_ids": fact_ids,
        "baseline_fact_ids": baseline_fact_ids,
        "comparison_groups": comparison_groups,
        "resolution_method": method,
        "confidence": confidence,
    }
    edges.add(positive_ctx_id, "COMPARES_TO_BASELINE", baseline_ctx_id, **edge_props)
    edges.add(baseline_ctx_id, "BASELINE_FOR", positive_ctx_id, **edge_props)

    if len(baseline_fact_ids) == 1:
        for fact_id in fact_ids:
            edges.add(
                _fact_node_id(fact_id),
                "FACT_COMPARES_TO_BASELINE",
                _fact_node_id(baseline_fact_ids[0]),
                **edge_props,
            )

def _mark_unresolved_comparison(
    ctx: JSON | None,
    scope: tuple[str, str, str, str, str],
    rows: list[JSON],
    *,
    reason: str,
) -> None:
    if not ctx:
        return
    doc_id, evidence_id, prop, test_method, measurement_key = scope
    rec = {
        "reason": reason,
        "doc_id": doc_id,
        "evidence_id": evidence_id,
        "property": prop,
        "test_method": test_method or None,
        "measurement_key": measurement_key or None,
        "fact_ids": [row.get("fact_id") for row in rows if row.get("fact_id")],
        "comparison_groups": sorted(
            {str(row.get("comparison_group")) for row in rows if row.get("comparison_group")}
        ),
    }
    _append_unique_by_key(
        ctx["comparison_context"].setdefault("unresolved_reasons", []),
        rec,
        ("reason", "property", "test_method", "measurement_key"),
        limit=100,
    )

def _finalize_comparison_context(ctx: JSON) -> None:
    comparison = ctx.get("comparison_context")
    if not isinstance(comparison, dict):
        return
    has_pairs = bool(comparison.get("resolved_pairs"))
    has_unresolved = bool(comparison.get("unresolved_reasons"))
    has_groups = bool(comparison.get("comparison_groups"))
    if has_pairs and not has_unresolved:
        comparison["comparison_signal_status"] = "resolved_pairs"
        comparison["comparison_resolution_status"] = "resolved_unique_baseline"
    elif has_pairs:
        comparison["comparison_signal_status"] = "resolved_pairs"
        comparison["comparison_resolution_status"] = "partially_resolved"
    elif has_groups or has_unresolved:
        comparison["comparison_signal_status"] = "has_comparison_group"
        comparison["comparison_resolution_status"] = "unresolved_baseline"
    else:
        comparison["comparison_signal_status"] = "missing"
        comparison["comparison_resolution_status"] = "missing"
    if isinstance(ctx.get("qa_flags"), dict):
        ctx["qa_flags"]["unresolved_comparison"] = comparison["comparison_resolution_status"] in {
            "unresolved_baseline",
            "partially_resolved",
        }
    comparison["trend_eligible"] = (
        comparison["comparison_resolution_status"] == "resolved_unique_baseline"
        and not (ctx.get("qa_flags") or {}).get("mixed_polarity")
        and not (ctx.get("qa_flags") or {}).get("coverage_below_threshold")
        and not (ctx.get("qa_flags") or {}).get("coverage_missing")
    )

def _context_field_confidence(rec: JSON) -> float:
    sources = set(rec.get("sources") or [])
    fact_count = len(rec.get("fact_ids") or [])
    evidence_count = len(rec.get("evidence_ids") or [])
    score = 0.55 + min(0.25, 0.05 * fact_count) + min(0.15, 0.05 * evidence_count)
    if "fact_explicit" in sources:
        score += 0.05
    if any(str(source).startswith("doc_profile") for source in sources):
        score = min(score, 0.80)
    return round(max(0.0, min(0.95, score)), 3)

def _is_unambiguous_primary(rows: list[JSON]) -> bool:
    if len(rows) == 1:
        return True
    first = int(rows[0].get("fact_count") or 0)
    second = int(rows[1].get("fact_count") or 0)
    return first >= max(2, second * 2)

def _append_unique(target: list[Any], value: Any) -> None:
    if value in (None, "", "unknown", [], {}):
        return
    if value not in target:
        target.append(value)
