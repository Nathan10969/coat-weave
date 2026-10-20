from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


JSONL_FILES = {
    "patents": "patents.jsonl",
    "patent_profiles": "patent_profiles.jsonl",
    "evidence_units": "evidence_units.jsonl",
    "example_contexts": "example_contexts.jsonl",
    "facts": "facts.jsonl",
    "canonical_entities": "canonical_entities.jsonl",
    "canonical_relations": "canonical_relations.jsonl",
    "edges": "edges.jsonl",
    "hyperedges": "hyperedges.jsonl",
}

MATERIAL_COLUMNS = [
    "Material:Resin / binder",
    "Material:Crosslinker / Curing agent",
    "Material:Pigment",
    "Material:Filler",
    "Material:Additive",
    "Material:Solvent",
    "Material:Monomer",
]

PROPERTY_COLUMNS = [
    "Property:Appearance",
    "Property:Mechanical",
    "Property:Surface",
    "Property:Aging / Corrosion / Durability",
    "Property:Chemical resistance",
    "Property:Thermal",
    "Property:Environmental / Regulatory",
    "Property:Application properties",
    "Property:Formulation",
]

ID_FIELDS = [
    "node_id",
    "patent_id",
    "doc_id",
    "profile_id",
    "evidence_id",
    "fact_id",
    "context_id",
    "example_context_id",
    "hyperedge_id",
    "canonical_id",
    "canonical_entity_id",
    "relation_id",
    "edge_id",
    "sample_id",
]


def read_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        errors.append(f"missing_file:{path.name}")
        return rows
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"invalid_json:{path.name}:{line_number}:{exc.msg}")
                continue
            if not isinstance(value, dict):
                errors.append(f"non_object_jsonl:{path.name}:{line_number}")
                continue
            rows.append(value)
    return rows


def _scalar_ids(value: Any) -> set[str]:
    if value in (None, "", [], {}):
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (int, float)):
        return {str(value)}
    if isinstance(value, list):
        out: set[str] = set()
        for item in value:
            out.update(_scalar_ids(item))
        return out
    if isinstance(value, dict):
        for key in ("id", "evidence_id", "context_id", "fact_id", "hyperedge_id", "canonical_id", "node_id"):
            if value.get(key):
                return _scalar_ids(value.get(key))
    return set()


def collect_evidence_ids(row: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("evidence_id", "evidence_ids", "supporting_evidence_ids"):
        ids.update(_scalar_ids(row.get(key)))
    evidence = row.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            ids.update(_scalar_ids(item))
    elif isinstance(evidence, dict):
        ids.update(_scalar_ids(evidence))
    return ids


def collect_node_ids(groups: dict[str, list[dict[str, Any]]]) -> set[str]:
    ids: set[str] = set()
    for rows in groups.values():
        for row in rows:
            for key in ID_FIELDS:
                ids.update(_scalar_ids(row.get(key)))
            for key in ("primary_l2_context_ids", "linked_l2_context_ids", "inherited_by_l2_context_ids"):
                ids.update(_scalar_ids(row.get(key)))
    return ids


def csv_projection_profile(path: Path, errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        errors.append(f"missing_file:{path.name}")
        return {"rows": 0, "columns": 0, "header": [], "filled": {}}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        rows = list(reader)
        filled = {
            column: sum(1 for row in rows if str(row.get(column) or "").strip())
            for column in header
        }
        if not header:
            errors.append(f"empty_csv:{path.name}")
        return {"rows": len(rows), "columns": len(header), "header": header, "filled": filled}


def csv_shape(path: Path, errors: list[str]) -> tuple[int, int]:
    profile = csv_projection_profile(path, errors)
    return int(profile["rows"]), int(profile["columns"])


def _row_text(row: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("fact_type", "hyperedge_type", "bundle_type", "source_fact_type", "relation", "predicate"):
        value = row.get(key)
        if value not in (None, "", [], {}):
            values.append(str(value).lower())
    return " ".join(values)


def _has_any_signal(rows: list[dict[str, Any]], tokens: tuple[str, ...]) -> bool:
    return any(any(token in _row_text(row) for token in tokens) for row in rows)


def _any_filled(profile: dict[str, Any], columns: list[str]) -> bool:
    filled = profile.get("filled") if isinstance(profile.get("filled"), dict) else {}
    return any(int(filled.get(column) or 0) > 0 for column in columns)


def _append_projection_errors(
    groups: dict[str, list[dict[str, Any]]],
    csv_profile: dict[str, Any],
    hyperedge_with_trace: int,
    errors: list[str],
) -> None:
    """Fail packs whose valid JSON cannot project into the expert CSV slots."""

    if not csv_profile.get("rows") or not groups["hyperedges"]:
        return
    facts = groups["facts"]
    hyperedges = groups["hyperedges"]

    formulation_tokens = (
        "formulation",
        "component",
        "commercial_material",
        "material",
        "binder",
        "resin",
        "additive",
        "pigment",
        "filler",
        "solvent",
        "curing",
        "crosslink",
    )
    performance_tokens = (
        "performance",
        "result",
        "test",
        "measurement",
        "salt",
        "corrosion",
        "adhesion",
        "hardness",
        "appearance",
        "weather",
        "crack",
        "resistance",
    )

    if _has_any_signal(facts + hyperedges, formulation_tokens) and not _any_filled(csv_profile, MATERIAL_COLUMNS):
        errors.append("projection_gap:material_slots_empty")
    if _has_any_signal(facts + hyperedges, performance_tokens) and not (
        _any_filled(csv_profile, PROPERTY_COLUMNS) or _any_filled(csv_profile, ["TestMethod", "TestStandard"])
    ):
        errors.append("projection_gap:property_or_test_slots_empty")
    if hyperedge_with_trace and not _any_filled(csv_profile, ["Evidence"]):
        errors.append("projection_gap:evidence_column_empty")


def validate_pack(kg_dir: Path | str, *, strict_projection: bool = True) -> dict[str, Any]:
    kg_path = Path(kg_dir)
    errors: list[str] = []
    groups = {name: read_jsonl(kg_path / file_name, errors) for name, file_name in JSONL_FILES.items()}
    counts = {name: len(rows) for name, rows in groups.items()}

    manifest_path = kg_path / "manifest.json"
    manifest: dict[str, Any] = {}
    if not manifest_path.exists():
        errors.append("missing_file:manifest.json")
    else:
        try:
            manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest_value, dict):
                manifest = manifest_value
            else:
                errors.append("manifest_not_object")
        except json.JSONDecodeError as exc:
            errors.append(f"invalid_json:manifest.json:{exc.msg}")

    expected_counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    for key, expected in expected_counts.items():
        if key in counts and counts[key] != expected:
            errors.append(f"count_mismatch:{key}:manifest={expected}:actual={counts[key]}")

    evidence_ids = {
        str(row.get("evidence_id"))
        for row in groups["evidence_units"]
        if row.get("evidence_id")
    }
    context_ids = {
        str(row.get("context_id") or row.get("example_context_id"))
        for row in groups["example_contexts"]
        if row.get("context_id") or row.get("example_context_id")
    }
    node_ids = collect_node_ids(groups)

    fact_with_trace = 0
    for row in groups["facts"]:
        refs = collect_evidence_ids(row)
        if refs and refs <= evidence_ids:
            fact_with_trace += 1
        else:
            errors.append(f"fact_evidence_ref:{row.get('fact_id') or row.get('node_id') or 'unknown'}")
        context_id = row.get("context_id")
        if context_id and str(context_id) not in context_ids:
            errors.append(f"fact_context_ref:{row.get('fact_id') or row.get('node_id') or 'unknown'}")

    hyperedge_with_trace = 0
    for row in groups["hyperedges"]:
        refs = collect_evidence_ids(row)
        if refs and refs <= evidence_ids:
            hyperedge_with_trace += 1
        else:
            errors.append(f"hyperedge_evidence_ref:{row.get('hyperedge_id') or row.get('node_id') or 'unknown'}")
        context_id = row.get("context_id")
        if context_id and str(context_id) not in context_ids:
            errors.append(f"hyperedge_context_ref:{row.get('hyperedge_id') or row.get('node_id') or 'unknown'}")

    for row in groups["edges"]:
        edge_id = row.get("edge_id") or "unknown"
        src = row.get("src") or row.get("source") or row.get("source_id")
        dst = row.get("dst") or row.get("target") or row.get("target_id")
        if src and str(src) not in node_ids:
            errors.append(f"dangling_edge_src:{edge_id}")
        if dst and str(dst) not in node_ids:
            errors.append(f"dangling_edge_dst:{edge_id}")

    csv_profile = csv_projection_profile(kg_path / "projected_same_columns.csv", errors)
    if strict_projection:
        _append_projection_errors(groups, csv_profile, hyperedge_with_trace, errors)
    csv_rows = int(csv_profile["rows"])
    csv_columns = int(csv_profile["columns"])
    fact_total = counts["facts"]
    hyperedge_total = counts["hyperedges"]
    return {
        "status": "ok" if not errors else "error",
        "errors": errors,
        "counts": counts,
        "fact_evidence_trace_rate": fact_with_trace / fact_total if fact_total else 1.0,
        "hyperedge_evidence_trace_rate": hyperedge_with_trace / hyperedge_total if hyperedge_total else 1.0,
        "csv_rows": csv_rows,
        "csv_columns": csv_columns,
        "csv_projection_fill": csv_profile["filled"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a vision KG pack.")
    parser.add_argument("kg_dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--no-strict-projection",
        action="store_true",
        help="Skip expert CSV slot-fill checks and only validate JSON/reference structure.",
    )
    args = parser.parse_args()

    report = validate_pack(args.kg_dir, strict_projection=not args.no_strict_projection)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
