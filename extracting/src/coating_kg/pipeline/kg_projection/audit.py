"""Manifest and quality audits for KG projection."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .ids import _looks_like_canonical

JSON = dict[str, Any]


def _record_counts(files: dict[str, int]) -> JSON:
    return {
        "patents": files.get("patents", 0),
        "patent_profiles": files.get("patent_profiles", 0),
        "evidence_records": files.get("evidence_units", 0),
        "fact_records": files.get("facts", 0),
        "example_context_records": files.get("example_contexts", 0),
        "canonical_entity_records": files.get("canonical_entities", 0),
        "canonical_relation_records": files.get("canonical_relations", 0),
        "edge_records": files.get("edges", 0),
    }

def _kg_qa_summary(
    *,
    patents: list[JSON],
    patent_profiles: list[JSON],
    evidence_units: list[JSON],
    facts: list[JSON],
    example_contexts: list[JSON],
    canonical_entities: list[JSON],
    canonical_relations: list[JSON],
    edges: list[JSON],
) -> JSON:
    node_ids = _node_universe(
        patents=patents,
        patent_profiles=patent_profiles,
        evidence_units=evidence_units,
        facts=facts,
        example_contexts=example_contexts,
        canonical_entities=canonical_entities,
    )
    evidence_ids = {str(row.get("evidence_id")) for row in evidence_units if row.get("evidence_id")}
    context_ids = {str(row.get("context_id")) for row in example_contexts if row.get("context_id")}
    canonical_ids = {str(row.get("canonical_id")) for row in canonical_entities if row.get("canonical_id")}
    referenced_canonicals = _referenced_canonical_ids(facts, evidence_units)

    missing_evidence = [
        _fact_ref_example(fact, "evidence_id")
        for fact in facts
        if not fact.get("evidence_id") or str(fact.get("evidence_id")) not in evidence_ids
    ]
    missing_context = [
        _fact_ref_example(fact, "context_id")
        for fact in facts
        if not fact.get("context_id") or str(fact.get("context_id")) not in context_ids
    ]
    missing_canonicals = sorted(referenced_canonicals - canonical_ids)
    dangling = _dangling_edges(edges, node_ids)

    return {
        "dangling_edges": {
            "count": len(dangling),
            "examples": dangling[:25],
        },
        "missing_evidence": {
            "fact_count": len(missing_evidence),
            "examples": missing_evidence[:25],
        },
        "missing_context": {
            "fact_count": len(missing_context),
            "examples": missing_context[:25],
        },
        "coverage": _coverage_qa(example_contexts),
        "stage7_validation": _stage7_validation_qa(evidence_units),
        "canonical": _canonical_qa(
            canonical_entities=canonical_entities,
            canonical_relations=canonical_relations,
            canonical_ids=canonical_ids,
            missing_canonicals=missing_canonicals,
        ),
    }

def _node_universe(
    *,
    patents: list[JSON],
    patent_profiles: list[JSON],
    evidence_units: list[JSON],
    facts: list[JSON],
    example_contexts: list[JSON],
    canonical_entities: list[JSON],
) -> set[str]:
    nodes: set[str] = set()
    for rows in (patents, patent_profiles, evidence_units, facts, example_contexts, canonical_entities):
        for row in rows:
            node_id = row.get("node_id")
            if node_id:
                nodes.add(str(node_id))
            canonical_id = row.get("canonical_id")
            if canonical_id:
                nodes.add(str(canonical_id))
    return nodes

def _dangling_edges(edges: list[JSON], node_ids: set[str]) -> list[JSON]:
    out: list[JSON] = []
    for edge in edges:
        src = str(edge.get("src") or "")
        dst = str(edge.get("dst") or "")
        missing: list[str] = []
        if src not in node_ids:
            missing.append("src")
        if dst not in node_ids:
            missing.append("dst")
        if missing:
            out.append(
                {
                    "edge_id": edge.get("edge_id"),
                    "src": src,
                    "edge_type": edge.get("edge_type"),
                    "dst": dst,
                    "missing_endpoint": ",".join(missing),
                }
            )
    return out

def _fact_ref_example(fact: JSON, missing_field: str) -> JSON:
    return {
        "fact_id": fact.get("fact_id"),
        "doc_id": fact.get("doc_id"),
        "context_id": fact.get("context_id"),
        "evidence_id": fact.get("evidence_id"),
        "missing_field": missing_field,
    }

def _coverage_qa(example_contexts: list[JSON]) -> JSON:
    missing_count = 0
    below_threshold_count = 0
    basis_counts: Counter[str] = Counter()
    values: list[float] = []
    for context in example_contexts:
        coverage = context.get("coverage_summary")
        if not isinstance(coverage, dict) or coverage.get("coverage_unknown"):
            missing_count += 1
            continue
        for basis in coverage.get("coverage_basis_set") or []:
            basis_counts[str(basis)] += 1
        value = coverage.get("min_coverage_pct")
        if isinstance(value, (int, float)):
            pct = float(value) * 100.0 if 0 <= float(value) <= 1 else float(value)
            values.append(pct)
            if pct < 80.0:
                below_threshold_count += 1
    return {
        "missing_count": missing_count,
        "below_threshold_count": below_threshold_count,
        "basis_counts": dict(sorted(basis_counts.items())),
        "min_coverage_pct": min(values) if values else None,
        "mean_coverage_pct": (sum(values) / len(values)) if values else None,
    }


def _stage7_validation_qa(evidence_units: list[JSON]) -> JSON:
    statuses: Counter[str] = Counter()
    issue_codes: Counter[str] = Counter()
    examples: list[JSON] = []
    for unit in evidence_units:
        validation = unit.get("stage7_validation")
        if not isinstance(validation, dict):
            continue
        status = str(validation.get("final_status") or "unknown")
        statuses[status] += 1
        issues = validation.get("issues") if isinstance(validation.get("issues"), list) else []
        for issue in issues:
            if isinstance(issue, dict) and issue.get("code"):
                issue_codes[str(issue["code"])] += 1
        if status not in {"ok", "warning"} and len(examples) < 25:
            examples.append(
                {
                    "unit_id": unit.get("unit_id"),
                    "doc_id": unit.get("doc_id"),
                    "status": status,
                    "validated_coverage_pct": validation.get("validated_coverage_pct"),
                    "issues": issues[:5],
                }
            )
    return {
        "status_counts": dict(sorted(statuses.items())),
        "issue_code_counts": dict(sorted(issue_codes.items())),
        "blocked_or_repair_count": sum(
            count for status, count in statuses.items() if status not in {"ok", "warning"}
        ),
        "examples": examples,
    }


def _canonical_qa(
    *,
    canonical_entities: list[JSON],
    canonical_relations: list[JSON],
    canonical_ids: set[str],
    missing_canonicals: list[str],
) -> JSON:
    proposed_count = sum(1 for row in canonical_entities if row.get("status") == "proposed")
    entity_count = len(canonical_entities)
    missing_must_merge = [
        {
            "relation_id": row.get("relation_id"),
            "alias_text": row.get("alias_text"),
            "canonical_id": row.get("canonical_id"),
        }
        for row in canonical_relations
        if row.get("relation_type") == "must_merge"
        and row.get("canonical_id")
        and str(row.get("canonical_id")) not in canonical_ids
    ]
    conflicts = _forbidden_conflicts(canonical_relations)
    return {
        "proposed_count": proposed_count,
        "proposed_rate": round(proposed_count / entity_count, 4) if entity_count else 0.0,
        "referenced_missing_count": len(missing_canonicals),
        "referenced_missing_examples": missing_canonicals[:25],
        "must_merge_target_missing_count": len(missing_must_merge),
        "must_merge_target_missing_examples": missing_must_merge[:25],
        "forbidden_conflict_count": len(conflicts),
        "forbidden_conflict_examples": conflicts[:25],
    }

def _forbidden_conflicts(canonical_relations: list[JSON]) -> list[JSON]:
    must_pairs: dict[tuple[str, str], JSON] = {}
    forbidden_pairs: dict[tuple[str, str], JSON] = {}
    for row in canonical_relations:
        relation_type = row.get("relation_type")
        if relation_type == "must_merge":
            alias = _norm_relation_endpoint(row.get("alias_text"))
            canonical_id = _norm_relation_endpoint(row.get("canonical_id"))
            if alias and canonical_id:
                must_pairs[_unordered_pair(alias, canonical_id)] = row
        elif relation_type == "forbidden_merge":
            entity_a = _norm_relation_endpoint(row.get("entity_a"))
            entity_b = _norm_relation_endpoint(row.get("entity_b"))
            if entity_a and entity_b:
                forbidden_pairs[_unordered_pair(entity_a, entity_b)] = row

    conflicts: list[JSON] = []
    for pair, must_row in must_pairs.items():
        forbidden_row = forbidden_pairs.get(pair)
        if not forbidden_row:
            continue
        conflicts.append(
            {
                "must_merge_relation_id": must_row.get("relation_id"),
                "forbidden_relation_id": forbidden_row.get("relation_id"),
                "pair": list(pair),
            }
        )
    return conflicts

def _norm_relation_endpoint(value: Any) -> str:
    return str(value or "").strip().casefold()

def _unordered_pair(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))

def _referenced_canonical_ids(facts: list[JSON], evidence_units: list[JSON]) -> set[str]:
    ids: set[str] = set()
    for fact in facts:
        for field in ("application", "property", "resin_system", "test_method"):
            value = fact.get(field)
            if _looks_like_canonical(value):
                ids.add(str(value))
        substrate = fact.get("substrate")
        if isinstance(substrate, dict):
            for value in [substrate.get("tested"), *(substrate.get("claimed") or [])]:
                if _looks_like_canonical(value):
                    ids.add(str(value))
        elif _looks_like_canonical(substrate):
            ids.add(str(substrate))
        for value in fact.get("additives") or []:
            if _looks_like_canonical(value):
                ids.add(str(value))
        for step in fact.get("process") or []:
            if isinstance(step, dict):
                value = step.get("canonical_id") or step.get("step")
                if _looks_like_canonical(value):
                    ids.add(str(value))
    for evidence in evidence_units:
        for value in evidence.get("tagged_entities") or []:
            if _looks_like_canonical(value):
                ids.add(str(value))
    return ids
