"""Canonical entity record builders for KG projection."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..canonical_resolver import CanonicalResolver, SUB_TYPES
from .ids import _prefix
from .io import _read_json, _read_text

JSON = dict[str, Any]


def _collect_canonical_entities(project_root: Path) -> list[JSON]:
    resolver = CanonicalResolver(seed_dir=project_root / "db")
    aliases_by_canonical: dict[str, list[str]] = defaultdict(list)
    for alias, canonical_id in resolver.aliases.items():
        aliases_by_canonical[canonical_id].append(alias)

    records: dict[str, JSON] = {}
    for canonical_id, node in sorted(resolver.nodes_by_id.items()):
        records[canonical_id] = {
            "canonical_id": canonical_id,
            "node_id": canonical_id,
            "node_type": node.get("type"),
            "record_type": "CanonicalEntityRecord",
            "canonical_name": node.get("name"),
            "sub_type": SUB_TYPES.get(canonical_id),
            "aliases": sorted(aliases_by_canonical.get(canonical_id, [])),
            "status": "approved",
            "source": "seed",
        }

    for proposal in _iter_proposed_canonicals(project_root):
        proposed_id = proposal.get("proposed_id") or proposal.get("canonical_id")
        if not proposed_id:
            continue
        proposed_id = str(proposed_id)
        source = _proposal_source(proposal)
        if proposed_id in records:
            records[proposed_id].setdefault("proposal_sources", [])
            records[proposed_id]["proposal_sources"].append(source)
            records[proposed_id]["proposal_occurrences"] = len(records[proposed_id]["proposal_sources"])
            continue
        records[proposed_id] = {
            "canonical_id": proposed_id,
            "node_id": proposed_id,
            "node_type": proposal.get("kind") or _prefix(proposed_id),
            "record_type": "CanonicalEntityRecord",
            "canonical_name": proposal.get("canonical_name") or proposed_id,
            "sub_type": proposal.get("sub_type"),
            "aliases": [],
            "status": "proposed",
            "source": "proposed_canonicals",
            "proposal_confidence": proposal.get("confidence"),
            "proposal_sources": [source],
            "proposal_occurrences": 1,
        }
    return sorted(records.values(), key=lambda row: str(row.get("canonical_id")))

def _collect_canonical_relations(
    project_root: Path,
    canonical_entities: list[JSON],
) -> list[JSON]:
    """Expose canonical lifecycle relations as KG records.

    Canonical entities carry approved/proposed status. This relation sidecar
    makes alias, must-merge, and forbidden-merge curation visible without
    mutating approved seeds or silently merging proposed nodes.
    """

    records: dict[str, JSON] = {}
    canonical_ids = {str(row.get("canonical_id")) for row in canonical_entities if row.get("canonical_id")}

    for entity in canonical_entities:
        canonical_id = str(entity.get("canonical_id") or "")
        if not canonical_id:
            continue
        for alias in entity.get("aliases") or []:
            rec: JSON = {
                "record_type": "CanonicalRelationRecord",
                "relation_type": "alias",
                "alias_text": str(alias),
                "canonical_id": canonical_id,
                "target_status": entity.get("status"),
                "source": "canonical_resolver_alias",
            }
            rec["relation_id"] = _canonical_relation_id(rec)
            records[rec["relation_id"]] = rec

    for row in _iter_must_merge_rows(project_root):
        rec = {
            "record_type": "CanonicalRelationRecord",
            "relation_type": "must_merge",
            "alias_text": row.get("alias_text"),
            "canonical_id": row.get("canonical_id"),
            "target_status": "known" if row.get("canonical_id") in canonical_ids else "missing",
            "merge_type": row.get("merge_type"),
            "confidence": row.get("confidence"),
            "source_evidence": row.get("source_evidence"),
            "source": "seed_must_merge_starter",
        }
        rec["relation_id"] = _canonical_relation_id(rec)
        records[rec["relation_id"]] = rec

    for row in _iter_forbidden_merge_rows(project_root):
        rec = {
            "record_type": "CanonicalRelationRecord",
            "relation_type": "forbidden_merge",
            "entity_a": row.get("entity_a"),
            "entity_b": row.get("entity_b"),
            "reason_type": row.get("reason_type"),
            "explanation": row.get("explanation"),
            "risk_severity": row.get("risk_severity"),
            "source": "seed_forbidden_merge_starter",
        }
        rec["relation_id"] = _canonical_relation_id(rec)
        records[rec["relation_id"]] = rec

    return sorted(records.values(), key=lambda row: str(row.get("relation_id")))

def _iter_proposed_canonicals(project_root: Path) -> Iterable[JSON]:
    for path in sorted((project_root / "data" / "units").glob("U_*/proposed_canonicals.json")):
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        for row in data.get("proposed_canonicals") or []:
            if isinstance(row, dict):
                enriched = dict(row)
                enriched["unit_id"] = path.parent.name
                yield enriched

    aggregate = _read_json(project_root / "data" / "proposed_canonicals_aggregated.json")
    if isinstance(aggregate, dict):
        for row in aggregate.get("proposed_canonicals") or []:
            if isinstance(row, dict):
                yield row

def _proposal_source(proposal: JSON) -> JSON:
    sources = proposal.get("sources")
    if isinstance(sources, list):
        return {
            "sources": sources,
            "confidence": proposal.get("confidence"),
        }
    return {
        "unit_id": proposal.get("unit_id"),
        "source_text": proposal.get("source_text"),
        "confidence": proposal.get("confidence"),
    }

def _iter_must_merge_rows(project_root: Path) -> Iterable[JSON]:
    text = _read_text(project_root / "db" / "seed_must_merge_starter.sql") or ""
    for values in _iter_sql_value_tuples(text):
        if len(values) < 2:
            continue
        alias_text, canonical_id = values[:2]
        if not alias_text or not canonical_id:
            continue
        yield {
            "alias_text": alias_text,
            "canonical_id": canonical_id,
            "merge_type": values[2] if len(values) > 2 else None,
            "confidence": values[3] if len(values) > 3 else None,
            "source_evidence": values[4] if len(values) > 4 else None,
        }

def _iter_forbidden_merge_rows(project_root: Path) -> Iterable[JSON]:
    text = _read_text(project_root / "db" / "seed_forbidden_merge_starter.sql") or ""
    for values in _iter_sql_value_tuples(text):
        if len(values) < 2:
            continue
        entity_a, entity_b = values[:2]
        if not entity_a or not entity_b:
            continue
        yield {
            "entity_a": entity_a,
            "entity_b": entity_b,
            "reason_type": values[2] if len(values) > 2 else None,
            "explanation": values[3] if len(values) > 3 else None,
            "risk_severity": values[4] if len(values) > 4 else None,
        }

def _iter_sql_value_tuples(sql: str) -> Iterable[list[str]]:
    """Parse simple seed INSERT value tuples.

    The seed files are controlled fixtures with single-quoted string columns.
    This intentionally stays narrow instead of becoming a general SQL parser.
    """

    for tuple_text in _iter_parenthesized_tuples(sql):
        values = re.findall(r"'((?:''|[^'])*)'", tuple_text)
        if values:
            yield [value.replace("''", "'") for value in values]

def _iter_parenthesized_tuples(sql: str) -> Iterable[str]:
    in_quote = False
    depth = 0
    start: int | None = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            if in_quote and i + 1 < len(sql) and sql[i + 1] == "'":
                i += 2
                continue
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                if depth == 0:
                    start = i + 1
                depth += 1
            elif ch == ")" and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    yield sql[start:i]
                    start = None
        i += 1

def _canonical_relation_id(row: JSON) -> str:
    payload = json.dumps(
        {k: v for k, v in row.items() if k != "relation_id"},
        ensure_ascii=False,
        sort_keys=True,
    )
    return "CREL_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
