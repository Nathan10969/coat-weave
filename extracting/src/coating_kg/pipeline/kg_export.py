"""Export implicit pipeline artifacts as explicit KG JSONL nodes and edges.

This module is intentionally a thin orchestration layer over the internal
``kg_projection`` builders. It does not change extraction behavior or mutate
per-unit audit folders.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .kg_projection.audit import _kg_qa_summary, _record_counts
from .kg_projection.canonical_records import (
    _collect_canonical_entities,
    _collect_canonical_relations,
)
from .kg_projection.edges import _EdgeSink
from .kg_projection.evidence_records import (
    _collect_evidence_units,
    _collect_patent_profiles,
    _collect_patents,
)
from .kg_projection.fact_records import _collect_facts_and_examples
from .kg_projection.io import _rel, _write_jsonl


JSON = dict[str, Any]

KG_SCHEMA_VERSION = "kg_projection_v1"


def build_kg_export(
    project_root: Path,
    *,
    out_dir: Path | None = None,
    doc_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build ``data/kg/*.jsonl`` from current pipeline outputs.

    The export makes hidden joins explicit:
    facts point to evidence units, examples, patents, and canonical entities.
    """

    project_root = project_root.resolve()
    data_dir = project_root / "data"
    out_dir = out_dir or data_dir / "kg"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc_filter = _normalise_doc_filter(doc_ids)

    edges = _EdgeSink()

    patents = _collect_patents(project_root, edges, doc_ids=doc_filter)
    patent_profiles = _collect_patent_profiles(project_root, edges, doc_ids=doc_filter)
    canonical_entities = _collect_canonical_entities(project_root)
    canonical_relations = _collect_canonical_relations(project_root, canonical_entities)
    evidence_units = _collect_evidence_units(project_root, edges, doc_ids=doc_filter)
    facts, example_contexts = _collect_facts_and_examples(
        project_root,
        evidence_units,
        edges,
        doc_ids=doc_filter,
    )

    edge_records = edges.records()
    files = {
        "patents": _write_jsonl(out_dir / "patents.jsonl", patents),
        "patent_profiles": _write_jsonl(out_dir / "patent_profiles.jsonl", patent_profiles),
        "example_contexts": _write_jsonl(out_dir / "example_contexts.jsonl", example_contexts),
        "evidence_units": _write_jsonl(out_dir / "evidence_units.jsonl", evidence_units.values()),
        "facts": _write_jsonl(out_dir / "facts.jsonl", facts),
        "canonical_entities": _write_jsonl(out_dir / "canonical_entities.jsonl", canonical_entities),
        "canonical_relations": _write_jsonl(out_dir / "canonical_relations.jsonl", canonical_relations),
        "edges": _write_jsonl(out_dir / "edges.jsonl", edge_records),
    }

    qa = _kg_qa_summary(
        patents=patents,
        patent_profiles=patent_profiles,
        evidence_units=list(evidence_units.values()),
        facts=facts,
        example_contexts=example_contexts,
        canonical_entities=canonical_entities,
        canonical_relations=canonical_relations,
        edges=edge_records,
    )
    summary = {
        "schema_version": KG_SCHEMA_VERSION,
        "out_dir": _rel(out_dir, project_root),
        "doc_ids": sorted(doc_filter) if doc_filter is not None else None,
        "counts": {name: count for name, count in files.items()},
        "record_counts": _record_counts(files),
        "record_types": {
            "patents": "PatentRecord",
            "patent_profiles": "PatentProfileRecord",
            "evidence_units": "EvidenceRecord",
            "facts": "FactRecord",
            "example_contexts": "ExampleContextRecord",
            "canonical_entities": "CanonicalEntityRecord",
            "canonical_relations": "CanonicalRelationRecord",
            "edges": "EdgeRecord",
        },
        "edge_type_counts": dict(sorted(Counter(e["edge_type"] for e in edge_records).items())),
        "files": {name: _rel(out_dir / f"{name}.jsonl", project_root) for name in files},
        "qa": qa,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _normalise_doc_filter(doc_ids: Iterable[str] | None) -> set[str] | None:
    if doc_ids is None:
        return None
    out = {str(doc_id).strip() for doc_id in doc_ids if str(doc_id).strip()}
    return out or None
