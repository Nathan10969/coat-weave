"""Stage 1 (OXR variant) — convert OXR JSON into the MinerU output layout.

Reads one OXR result per PDF from $OXR_JSON_DIR/<doc_id>.json (a raw
/parse/sync response, its ``result``, or merged ``{"pages": ...}``) and writes
$MINERU_OUTPUT_DIR/<doc_id>/auto/<doc_id>_content_list.json plus cropped
images, so stages 0.5 and 2+ run unchanged. Crops need the source PDF at
$PDF_INPUT_DIR/<doc_id>.pdf and PyMuPDF. Idempotent: skips existing outputs.

Usage:
    OXR_JSON_DIR=... PDF_INPUT_DIR=... MINERU_OUTPUT_DIR=... python 01_oxr_to_mineru.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from coating_kg.pipeline.oxr_adapter import write_mineru_layout  # noqa: E402

OXR_JSON_DIR = Path(os.environ.get("OXR_JSON_DIR", str(REPO / "data" / "oxr_output")))
PDF_INPUT_DIR = Path(os.environ.get("PDF_INPUT_DIR", str(REPO / "data" / "pdf")))
MINERU_OUTPUT_DIR = Path(
    os.environ.get("MINERU_OUTPUT_DIR", str(REPO / "data" / "mineru_output"))
)


def main() -> int:
    sources = sorted(OXR_JSON_DIR.glob("*.json"))
    if not sources:
        print(f"ERROR: no OXR JSON under {OXR_JSON_DIR}", file=sys.stderr)
        return 2
    failures = 0
    for source in sources:
        doc_id = source.stem
        target = MINERU_OUTPUT_DIR / doc_id / "auto" / f"{doc_id}_content_list.json"
        if target.exists():
            print(f"  [SKIP] {doc_id}")
            continue
        pdf_path = PDF_INPUT_DIR / f"{doc_id}.pdf"
        if not pdf_path.exists():
            print(f"  [WARN] {doc_id}: no PDF at {pdf_path}; figures/tables get no crops")
        try:
            written = write_mineru_layout(
                json.loads(source.read_text(encoding="utf-8")),
                MINERU_OUTPUT_DIR,
                doc_id,
                pdf_path=pdf_path if pdf_path.exists() else None,
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  [FAIL] {doc_id}: {exc}", file=sys.stderr)
            continue
        blocks = json.loads(written.read_text(encoding="utf-8"))
        tables = sum(b["type"] == "table" for b in blocks)
        print(f"  [OK]   {doc_id}: {len(blocks)} blocks, {tables} tables -> {written}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
