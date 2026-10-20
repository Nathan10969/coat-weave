"""Build a low-coverage table benchmark manifest.

The default mode is read-only: find the lowest-coverage table units and write a
CSV manifest for manual QA. If a table image artifact exists, ``--run-qwen-ocr``
can also run the configured Qwen table OCR model and store the recovered JSON
next to that unit for HTML-only vs image+HTML comparison.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from coating_kg.pipeline.vlm_describe import QwenVLClient  # noqa: E402


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _count_json_list(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    return len(data) if isinstance(data, list) else 0


def _table_image(unit_dir: Path) -> Path | None:
    names = (
        "table.png",
        "table.jpg",
        "table.jpeg",
        "image.png",
        "image.jpg",
        "image.jpeg",
    )
    for name in names:
        candidate = unit_dir / name
        if candidate.exists():
            return candidate
    return None


def _iter_units(units_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for unit_dir in sorted(units_dir.glob("U_*")):
        if not unit_dir.is_dir():
            continue
        meta = _read_json(unit_dir / "meta.json")
        if meta.get("unit_type") != "table":
            continue
        coverage = _read_json(unit_dir / "coverage.json")
        coverage_pct = coverage.get("coverage_pct")
        try:
            pct_sort = float(coverage_pct) if coverage_pct is not None else 101.0
        except (TypeError, ValueError):
            pct_sort = 101.0
        table_html = unit_dir / "table.html"
        matched_path = unit_dir / "matched_paragraphs.json"
        image_path = _table_image(unit_dir)
        rows.append({
            "unit_id": unit_dir.name,
            "doc_id": meta.get("doc_id", ""),
            "page": meta.get("page", ""),
            "region_id": meta.get("region_id", ""),
            "coverage_pct": coverage_pct,
            "coverage_basis": coverage.get("coverage_basis", "legacy_or_missing"),
            "expected_data_cells": coverage.get("expected_data_cells", ""),
            "expected_result_cells": coverage.get("expected_result_cells", ""),
            "extracted_facts": coverage.get("extracted_facts", ""),
            "validated_facts": coverage.get("validated_facts", _count_json_list(unit_dir / "facts.json")),
            "raw_numeric_data_cells": coverage.get("raw_numeric_data_cells", ""),
            "has_table_html": table_html.exists(),
            "table_html_chars": len(table_html.read_text(encoding="utf-8")) if table_html.exists() else 0,
            "has_table_image": image_path is not None,
            "table_image": str(image_path.relative_to(REPO)).replace("\\", "/") if image_path else "",
            "matched_paragraphs": _count_json_list(matched_path),
            "unit_dir": str(unit_dir.relative_to(REPO)).replace("\\", "/"),
            "_pct_sort": pct_sort,
        })
    return sorted(rows, key=lambda row: (row["_pct_sort"], row["unit_id"]))


def _run_qwen_ocr(rows: list[dict[str, Any]], model: str | None) -> None:
    client = QwenVLClient(table_ocr_model=model)
    for row in rows:
        unit_dir = REPO / row["unit_dir"]
        image_rel = row.get("table_image")
        if not image_rel:
            row["qwen_ocr_status"] = "missing_table_image"
            continue
        table_html_path = unit_dir / "table.html"
        caption_path = unit_dir / "caption.txt"
        try:
            payload = client.describe_table_image(
                REPO / image_rel,
                table_html_path.read_text(encoding="utf-8") if table_html_path.exists() else "",
                caption_path.read_text(encoding="utf-8") if caption_path.exists() else "",
                [],
            )
        except Exception as exc:  # noqa: BLE001
            row["qwen_ocr_status"] = f"error: {exc}"
            continue
        out_path = unit_dir / "table_ocr_vlm_description.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        row["qwen_ocr_status"] = "ok"
        row["qwen_ocr_output"] = str(out_path.relative_to(REPO)).replace("\\", "/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units-dir", type=Path, default=REPO / "data" / "units")
    parser.add_argument("--out", type=Path, default=REPO / "output" / "low_coverage_benchmark.csv")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--run-qwen-ocr", action="store_true")
    parser.add_argument("--model", default=None, help="Override QWEN_TABLE_OCR_MODEL for OCR runs.")
    args = parser.parse_args()

    rows = _iter_units(args.units_dir)[: args.limit]
    if args.run_qwen_ocr:
        _run_qwen_ocr(rows, args.model)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    for row in rows:
        row.pop("_pct_sort", None)
    fieldnames = sorted({key for row in rows for key in row})
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} benchmark rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
