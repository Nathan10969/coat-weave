from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator


TARGET_DOC = "WO2020157150A1"


def rows(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            row = json.loads(line)
            if isinstance(row, dict):
                yield line_number, row


def doc_id(row: dict[str, Any]) -> str:
    return str(
        row.get("doc_id")
        or row.get("patent_id")
        or row.get("publication_number")
        or ""
    ).strip()


def contains_zinc_dust(value: Any) -> bool:
    return "zinc dust" in json.dumps(value, ensure_ascii=False).casefold()


def main() -> None:
    root = Path(sys.argv[1])
    facts: list[dict[str, Any]] = []
    fact_ids: set[str] = set()
    evidence_ids: set[str] = set()

    for line_number, row in rows(root / "facts.jsonl"):
        if doc_id(row) != TARGET_DOC or not contains_zinc_dust(row):
            continue
        role = row.get("material_role") or row.get("role")
        if role != "additive":
            continue
        fact_id = str(row.get("fact_id") or row.get("id") or "")
        fact_ids.add(fact_id)
        row_evidence = {
            str(value)
            for value in (row.get("evidence_ids") or [row.get("evidence_id")])
            if value
        }
        evidence_ids.update(row_evidence)
        facts.append(
            {
                "line": line_number,
                "fact_id": fact_id,
                "context_id": row.get("context_id"),
                "material": row.get("material") or row.get("material_name"),
                "material_role": role,
                "value": row.get("value"),
                "unit": row.get("unit"),
                "evidence_ids": sorted(row_evidence),
            }
        )

    hyperedges: list[dict[str, Any]] = []
    for line_number, row in rows(root / "hyperedges.jsonl"):
        if doc_id(row) != TARGET_DOC:
            continue
        row_fact_ids = {str(value) for value in row.get("fact_ids") or []}
        nested = json.dumps(row, ensure_ascii=False).casefold()
        if not (row_fact_ids & fact_ids) and "zinc dust" not in nested:
            continue
        hyperedges.append(
            {
                "line": line_number,
                "hyperedge_id": row.get("hyperedge_id") or row.get("id"),
                "context_id": row.get("context_id"),
                "matched_fact_ids": sorted(row_fact_ids & fact_ids),
                "fact_ids": sorted(row_fact_ids),
                "pigments": row.get("pigments"),
                "additives": row.get("additives"),
                "formulation": row.get("formulation"),
            }
        )

    evidence: list[dict[str, Any]] = []
    role_evidence: list[dict[str, Any]] = []
    for line_number, row in rows(root / "evidence_units.jsonl"):
        if doc_id(row) != TARGET_DOC:
            continue
        evidence_id = str(row.get("evidence_id") or row.get("id") or "")
        text = str(row.get("quote") or row.get("text") or row.get("content") or "")
        compact = {
            "line": line_number,
            "evidence_id": evidence_id,
            "page": row.get("page") or row.get("page_number"),
            "table": row.get("table") or row.get("table_id"),
            "text": text,
        }
        if evidence_id in evidence_ids:
            evidence.append(compact)
        folded = text.casefold()
        if "zinc dust" in folded and "anticorrosive" in folded:
            role_evidence.append(compact)

    print(
        json.dumps(
            {
                "doc_id": TARGET_DOC,
                "affected_fact_count": len(facts),
                "affected_hyperedge_count": len(hyperedges),
                "fact_evidence_count": len(evidence),
                "role_evidence_count": len(role_evidence),
                "facts": facts,
                "hyperedges": hyperedges,
                "fact_evidence": evidence,
                "role_evidence": role_evidence,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
