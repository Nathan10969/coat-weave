from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


CSV_FIELDS = [
    "rank",
    "patent_id",
    "source_name",
    "source_path",
    "patent_dir",
    "pages_dir",
    "agent_outputs_dir",
    "kg_pack_dir",
    "status",
]


def parse_rank(path: Path, fallback: int) -> str:
    match = re.match(r"^(\d{3})[_\-\s]", path.name)
    if match:
        return match.group(1)
    return f"{fallback:03d}"


def parse_patent_id(path: Path) -> str:
    match = re.search(r"(?<![A-Za-z0-9])(WO\d{6,}[A-Z]\d?)(?![A-Za-z0-9])", path.stem, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    stem = re.sub(r"^\d{3}[_\-\s]+", "", path.stem)
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-")
    return clean or path.stem


def select_pdfs(input_dir: Path, limit: int = 150) -> list[Path]:
    pdfs = sorted(input_dir.glob("*.pdf"), key=lambda item: item.name.casefold())
    return pdfs[:limit]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def initialize_batch(input_dir: Path | str, output_dir: Path | str, limit: int = 150) -> list[dict[str, str]]:
    input_path = Path(input_dir).resolve()
    output_path = Path(output_dir).resolve()
    patents_root = output_path / "patents"
    qa_root = output_path / "qa"
    patents_root.mkdir(parents=True, exist_ok=True)
    qa_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    for index, pdf_path in enumerate(select_pdfs(input_path, limit), start=1):
        rank = parse_rank(pdf_path, index)
        patent_id = parse_patent_id(pdf_path)
        patent_dir = patents_root / patent_id
        pages_dir = patent_dir / "pages"
        agent_outputs_dir = patent_dir / "agent_outputs"
        kg_pack_dir = patent_dir / "kg_pack"
        pages_dir.mkdir(parents=True, exist_ok=True)
        agent_outputs_dir.mkdir(parents=True, exist_ok=True)
        kg_pack_dir.mkdir(parents=True, exist_ok=True)

        rows.append(
            {
                "rank": rank,
                "patent_id": patent_id,
                "source_name": pdf_path.name,
                "source_path": str(pdf_path.resolve()),
                "patent_dir": str(patent_dir),
                "pages_dir": str(pages_dir),
                "agent_outputs_dir": str(agent_outputs_dir),
                "kg_pack_dir": str(kg_pack_dir),
                "status": "initialized",
            }
        )

    write_csv(output_path / "input_manifest.csv", rows)
    (output_path / "input_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "agent_native_vision_batch_manifest_v1",
                "input_dir": str(input_path),
                "output_dir": str(output_path),
                "limit": limit,
                "count": len(rows),
                "records": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_path = qa_root / "validation_summary.csv"
    if not summary_path.exists():
        with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "rank",
                    "patent_id",
                    "status",
                    "errors",
                    "evidence_units",
                    "facts",
                    "hyperedges",
                    "csv_rows",
                    "fact_evidence_trace_rate",
                    "hyperedge_evidence_trace_rate",
                ]
            )
    failures_path = qa_root / "failures.json"
    if not failures_path.exists():
        failures_path.write_text(
            json.dumps(
                {
                    "schema_version": "agent_native_vision_batch_failures_v1",
                    "failures": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a vision KG batch folder and input manifest.")
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--limit", type=int, default=150)
    args = parser.parse_args()

    rows = initialize_batch(args.input_dir, args.output_dir, args.limit)
    print(f"Initialized {len(rows)} PDFs at {Path(args.output_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
