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


def read_manifest_rows(batch_dir: Path) -> list[dict[str, str]]:
    manifest_path = batch_dir / "input_manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing input manifest: {manifest_path}")
    with manifest_path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def append_jsonl(input_path: Path, output_path: Path) -> int:
    if not input_path.exists():
        raise FileNotFoundError(f"Missing JSONL file: {input_path}")
    count = 0
    with input_path.open(encoding="utf-8") as src, output_path.open("a", encoding="utf-8") as dst:
        for line_number, line in enumerate(src, start=1):
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError(f"Non-object JSONL row: {input_path}:{line_number}")
            dst.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def append_csv(input_path: Path, output_path: Path, expected_header: list[str] | None) -> tuple[list[str], int]:
    if not input_path.exists():
        raise FileNotFoundError(f"Missing projected CSV file: {input_path}")
    with input_path.open(encoding="utf-8-sig", newline="") as src:
        reader = csv.reader(src)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"Empty CSV file: {input_path}") from exc
        if expected_header is not None and header != expected_header:
            raise ValueError(f"CSV header mismatch: {input_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if output_path.exists() else "w"
        with output_path.open(mode, encoding="utf-8-sig", newline="") as dst:
            writer = csv.writer(dst)
            if mode == "w":
                writer.writerow(header)
            rows = 0
            for row in reader:
                writer.writerow(row)
                rows += 1
    return header, rows


def merge_batch(batch_dir: Path | str, output_dir: Path | str | None = None) -> dict[str, Any]:
    batch_path = Path(batch_dir).resolve()
    out_path = Path(output_dir).resolve() if output_dir is not None else batch_path / "merged_kg_pack"
    out_path.mkdir(parents=True, exist_ok=True)

    for file_name in JSONL_FILES.values():
        target = out_path / file_name
        if target.exists():
            target.unlink()
    csv_out = out_path / "projected_same_columns.csv"
    if csv_out.exists():
        csv_out.unlink()

    rows = read_manifest_rows(batch_path)
    merged_counts = {key: 0 for key in JSONL_FILES}
    per_patent: list[dict[str, Any]] = []
    csv_header: list[str] | None = None
    csv_rows_total = 0

    for row in rows:
        kg_pack_dir = Path(row["kg_pack_dir"])
        patent_counts: dict[str, int] = {}
        for key, file_name in JSONL_FILES.items():
            count = append_jsonl(kg_pack_dir / file_name, out_path / file_name)
            merged_counts[key] += count
            patent_counts[key] = count
        csv_header, csv_rows = append_csv(kg_pack_dir / "projected_same_columns.csv", csv_out, csv_header)
        csv_rows_total += csv_rows
        per_patent.append(
            {
                "rank": row.get("rank"),
                "patent_id": row.get("patent_id"),
                "kg_pack_dir": str(kg_pack_dir),
                "counts": patent_counts,
                "csv_rows": csv_rows,
            }
        )

    manifest = {
        "schema_version": "agent_native_vision_merged_kg_pack_v1",
        "source_batch_dir": str(batch_path),
        "input_manifest": str(batch_path / "input_manifest.csv"),
        "patent_count": len(rows),
        "counts": merged_counts,
        "csv_rows": csv_rows_total,
        "csv_columns": len(csv_header or []),
        "files": {
            **{key: file_name for key, file_name in JSONL_FILES.items()},
            "manifest": "manifest.json",
            "projected_csv": "projected_same_columns.csv",
        },
        "per_patent": per_patent,
        "qa_flags": [],
    }
    (out_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge per-patent vision KG packs mechanically.")
    parser.add_argument("batch_dir", type=Path)
    parser.add_argument("output_dir", type=Path, nargs="?")
    args = parser.parse_args()

    manifest = merge_batch(args.batch_dir, args.output_dir)
    print(
        "Merged "
        f"{manifest['patent_count']} patents, "
        f"{manifest['counts']['hyperedges']} hyperedges, "
        f"{manifest['csv_rows']} CSV rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
