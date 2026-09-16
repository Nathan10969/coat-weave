from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path
from typing import Any


def load_csv_builder() -> Any:
    repo = Path(__file__).resolve().parents[2]
    path = repo / "scripts" / "build_coatings_csv.py"
    spec = importlib.util.spec_from_file_location("build_coatings_csv", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load CSV builder: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_csv(kg_dir: Path | str, output_path: Path | str | None = None) -> dict[str, Any]:
    kg_path = Path(kg_dir)
    hyperedges_path = kg_path / "hyperedges.jsonl"
    if not hyperedges_path.exists():
        raise FileNotFoundError(f"Missing hyperedges.jsonl: {hyperedges_path}")

    csv_builder = load_csv_builder()
    rows, _manifest = csv_builder.build_rows_from_hyperedges(kg_path)
    names = csv_builder.fieldnames()
    out = Path(output_path) if output_path is not None else kg_path / "projected_same_columns.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)
    return {"output": str(out), "rows": len(rows), "columns": len(names)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Project same-column CSV from vision KG hyperedges.")
    parser.add_argument("kg_dir", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    args = parser.parse_args()

    summary = project_csv(args.kg_dir, args.output)
    print(f"Wrote {summary['rows']} rows x {summary['columns']} columns to {summary['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
