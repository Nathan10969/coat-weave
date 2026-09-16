"""Evidence and patent record builders for KG projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .edges import _EdgeSink
from .ids import _doc_from_unit, _evidence_id, _looks_like_canonical, _patent_id, _profile_id
from .io import _read_json, _read_jsonl, _read_text, _rel

JSON = dict[str, Any]


def _collect_patents(
    project_root: Path,
    edges: "_EdgeSink",
    *,
    doc_ids: set[str] | None = None,
) -> list[JSON]:
    patents_dir = project_root / "data" / "patents"
    records: list[JSON] = []
    for path in sorted(patents_dir.glob("*__patent_meta.json")):
        doc_id = path.name[: -len("__patent_meta.json")]
        if doc_ids is not None and doc_id not in doc_ids:
            continue
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        rec = dict(data)
        rec.setdefault("doc_id", doc_id)
        rec["node_id"] = _patent_id(doc_id)
        rec["node_type"] = "PAT"
        rec["record_type"] = "PatentRecord"
        rec["source_path"] = _rel(path, project_root)
        records.append(rec)
    return records

def _collect_patent_profiles(
    project_root: Path,
    edges: "_EdgeSink",
    *,
    doc_ids: set[str] | None = None,
) -> list[JSON]:
    patents_dir = project_root / "data" / "patents"
    records: list[JSON] = []
    for path in sorted(patents_dir.glob("*__coating_profile.json")):
        doc_id = path.name[: -len("__coating_profile.json")]
        if doc_ids is not None and doc_id not in doc_ids:
            continue
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        rec = dict(data)
        rec.setdefault("doc_id", doc_id)
        rec["profile_id"] = _profile_id(doc_id)
        rec["node_id"] = rec["profile_id"]
        rec["node_type"] = "PATENT_PROFILE"
        rec["record_type"] = "PatentProfileRecord"
        rec["source_path"] = _rel(path, project_root)
        records.append(rec)
        edges.add(_patent_id(doc_id), "HAS_PROFILE", rec["profile_id"])
    return records

def _collect_evidence_units(
    project_root: Path,
    edges: "_EdgeSink",
    *,
    doc_ids: set[str] | None = None,
) -> dict[str, JSON]:
    units_dir = project_root / "data" / "units"
    evidence: dict[str, JSON] = {}
    for unit_dir in sorted(units_dir.glob("U_*")):
        if not unit_dir.is_dir():
            continue
        meta = _read_json(unit_dir / "meta.json")
        if not isinstance(meta, dict):
            continue
        unit_id = str(meta.get("unit_id") or unit_dir.name)
        evidence_id = _evidence_id(unit_id)
        doc_id = str(meta.get("doc_id") or _doc_from_unit(unit_id) or "")
        if doc_ids is not None and doc_id not in doc_ids:
            continue
        rec: JSON = {
            "evidence_id": evidence_id,
            "node_id": evidence_id,
            "node_type": "EVD",
            "record_type": "EvidenceRecord",
            "context_only": False,
            "evidence_type": meta.get("unit_type"),
            "doc_id": doc_id,
            "unit_id": unit_id,
            "page": meta.get("page"),
            "region_id": meta.get("region_id"),
            "bbox": meta.get("bbox"),
            "caption": _read_text(unit_dir / "caption.txt") or meta.get("caption_footnote_text"),
            "paths": _unit_paths(unit_dir, project_root),
        }
        desc = _read_json(unit_dir / "vlm_description.json")
        if isinstance(desc, dict):
            rec["vlm_description"] = desc.get("description")
            rec["tagged_entities"] = desc.get("identified_entities") or []
            rec["figure_subtype"] = desc.get("subtype")
            rec["table_subject"] = desc.get("table_subject")
            rec["table_type"] = desc.get("table_type")
            rec["chemical_candidates"] = desc.get("chemical_candidates") or []
        route = _read_json(unit_dir / "route_decision.json")
        if isinstance(route, dict):
            rec["route_decision"] = route
            if route.get("route") == "register_layer1":
                rec["context_only"] = True
                rec["context_source"] = "L1 Retrieval Context"
        matched = _read_json(unit_dir / "matched_paragraphs.json")
        if isinstance(matched, dict):
            rec["related_paragraphs"] = matched.get("matches") or []
        coverage = _read_json(unit_dir / "coverage.json")
        if isinstance(coverage, dict):
            rec["coverage"] = coverage
        cell_annotations = _read_json(unit_dir / "cell_annotations.json")
        if isinstance(cell_annotations, dict):
            rec["cell_annotations"] = cell_annotations.get("cell_annotations") or []
        evidence_ledger = _read_json(unit_dir / "evidence_ledger.json")
        if isinstance(evidence_ledger, dict):
            rec["evidence_ledger_stats"] = evidence_ledger.get("stats") or {}
            rec["evidence_ledger_entry_ids"] = [
                str(row.get("ledger_id"))
                for row in evidence_ledger.get("entries") or []
                if isinstance(row, dict) and row.get("ledger_id")
            ]
        extraction_plan = _read_json(unit_dir / "extraction_plan.json")
        if isinstance(extraction_plan, dict):
            rec["extraction_plan"] = extraction_plan.get("extraction_plan") or extraction_plan
        stage7_validation = _read_json(unit_dir / "stage7_validation.json")
        if isinstance(stage7_validation, dict):
            rec["stage7_validation"] = stage7_validation
        schema_rejects = _read_json(unit_dir / "schema_rejects.json")
        if isinstance(schema_rejects, dict):
            rec["schema_rejects"] = schema_rejects.get("schema_rejects") or []

        evidence[evidence_id] = rec
        if doc_id:
            edges.add(_patent_id(doc_id), "HAS_EVIDENCE", evidence_id)
        for canonical_id in rec.get("tagged_entities") or []:
            if _looks_like_canonical(canonical_id):
                edges.add(evidence_id, "MENTIONS", str(canonical_id))

    _merge_layer1_evidence(project_root, evidence, edges, doc_ids=doc_ids)
    return evidence

def _merge_layer1_evidence(
    project_root: Path,
    evidence: dict[str, JSON],
    edges: "_EdgeSink",
    *,
    doc_ids: set[str] | None = None,
) -> None:
    layer1_dir = project_root / "data" / "layer1"
    if not layer1_dir.exists():
        return
    for doc_dir in sorted(p for p in layer1_dir.iterdir() if p.is_dir()):
        doc_id = doc_dir.name
        if doc_ids is not None and doc_id not in doc_ids:
            continue
        layer1 = _read_json(doc_dir / "layer1.json")
        if isinstance(layer1, dict):
            for passage in layer1.get("passages") or []:
                if isinstance(passage, dict):
                    _add_passage_evidence(doc_id, passage, evidence, edges)
            for figure in layer1.get("figures") or []:
                if isinstance(figure, dict):
                    _add_figure_evidence(doc_id, figure, evidence, edges)
        pending = doc_dir / "pending_figures.jsonl"
        if pending.exists():
            for figure in _read_jsonl(pending):
                if isinstance(figure, dict):
                    _add_figure_evidence(doc_id, figure, evidence, edges)

def _add_passage_evidence(
    doc_id: str,
    passage: JSON,
    evidence: dict[str, JSON],
    edges: "_EdgeSink",
) -> None:
    passage_id = str(passage.get("passage_id") or "")
    if not passage_id:
        return
    evidence_id = _evidence_id(passage_id)
    rec = {
        "evidence_id": evidence_id,
        "node_id": evidence_id,
        "node_type": "EVD",
        "record_type": "EvidenceRecord",
        "context_only": True,
        "context_source": "L1 Retrieval Context",
        "evidence_type": "passage",
        "doc_id": doc_id,
        "passage_id": passage_id,
        "page": passage.get("page"),
        "section_type": passage.get("section_type"),
        "text_excerpt": passage.get("text_excerpt"),
        "tagged_entities": passage.get("entities") or [],
        "entities_provenance": passage.get("entities_provenance") or {},
        "confidence": passage.get("confidence"),
    }
    evidence[evidence_id] = rec
    edges.add(_patent_id(doc_id), "HAS_EVIDENCE", evidence_id)
    for canonical_id in rec["tagged_entities"]:
        if _looks_like_canonical(canonical_id):
            edges.add(evidence_id, "MENTIONS", str(canonical_id))

def _add_figure_evidence(
    doc_id: str,
    figure: JSON,
    evidence: dict[str, JSON],
    edges: "_EdgeSink",
) -> None:
    unit_id = figure.get("unit_id")
    evidence_id = _evidence_id(str(unit_id or figure.get("figure_id") or ""))
    if not evidence_id:
        return
    rec = evidence.setdefault(
        evidence_id,
        {
            "evidence_id": evidence_id,
            "node_id": evidence_id,
            "node_type": "EVD",
            "record_type": "EvidenceRecord",
            "context_only": True,
            "context_source": "L1 Retrieval Context",
            "evidence_type": "figure",
            "doc_id": doc_id,
        },
    )
    rec.setdefault("record_type", "EvidenceRecord")
    rec.setdefault("context_only", True)
    rec.setdefault("context_source", "L1 Retrieval Context")
    rec.update(
        {
            "figure_id": figure.get("figure_id"),
            "unit_id": unit_id,
            "page": figure.get("page", rec.get("page")),
            "figure_subtype": figure.get("subtype", rec.get("figure_subtype")),
            "image_path": figure.get("image_path", rec.get("image_path")),
            "vlm_description": figure.get("vlm_description", rec.get("vlm_description")),
            "caption": figure.get("caption", rec.get("caption")),
            "tagged_entities": figure.get("tagged_entities") or rec.get("tagged_entities") or [],
            "reference_numerals": figure.get("reference_numerals") or rec.get("reference_numerals") or [],
            "chemical_candidates": figure.get("chemical_candidates") or rec.get("chemical_candidates") or [],
            "related_paragraphs": figure.get("related_paragraphs") or rec.get("related_paragraphs") or [],
            "confidence": figure.get("confidence", rec.get("confidence")),
        }
    )
    edges.add(_patent_id(doc_id), "HAS_EVIDENCE", evidence_id)
    for canonical_id in rec.get("tagged_entities") or []:
        if _looks_like_canonical(canonical_id):
            edges.add(evidence_id, "MENTIONS", str(canonical_id))

def _unit_paths(unit_dir: Path, project_root: Path) -> JSON:
    names = {
        "meta": "meta.json",
        "caption": "caption.txt",
        "raw_table_html": "table.html",
        "table_image_jpg": "table.jpg",
        "table_image_png": "table.png",
        "corrected_table_html": "table_corrected.html",
        "table_ocr": "table_ocr.json",
        "cell_annotations": "cell_annotations.json",
        "vlm_description": "vlm_description.json",
        "matched_paragraphs": "matched_paragraphs.json",
        "evidence_ledger": "evidence_ledger.json",
        "sample_map": "sample_map.json",
        "context_assertions": "context_assertions.json",
        "coverage_audit": "coverage_audit.json",
        "extraction_plan": "extraction_plan.json",
        "stage7_validation": "stage7_validation.json",
        "schema_rejects": "schema_rejects.json",
        "coverage": "coverage.json",
        "facts": "facts.json",
        "normalized_facts": "normalized_facts.json",
        "route_decision": "route_decision.json",
    }
    return {
        key: _rel(unit_dir / name, project_root)
        for key, name in names.items()
        if (unit_dir / name).exists()
    }
