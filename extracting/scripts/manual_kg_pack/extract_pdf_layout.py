"""
Deterministic PDF → text + table cell bbox extractor.

Usage:
    python extract_pdf_layout.py <input.pdf> <output_dir> [--pages 43-62]

Produces:
    <output_dir>/
        page_<NN>.png           # rendered image of each page (300 dpi)
        page_<NN>_text.txt      # text content of the page
        page_<NN>_tables.json   # table cell bbox + text for each table on the page
        meta.json               # PDF-level metadata (page_count, sizes, etc.)

The output is consumed by the manual_kg_pack extraction prompt. The script does
NO interpretation — it only digitizes the PDF so the downstream LLM call can
work from deterministic data instead of fighting PDF rendering.

Cell-bbox model:
    Each cell: [x0, y0, x1, y1] in PDF coordinate space (origin = top-left after
    we flip; pdfplumber's native is bottom-left, we convert).
    Plus image-space:
        image_bbox = [px0, py0, px1, py1] at 300 dpi, origin top-left.
    Both are recorded so downstream UIs can use either.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pdfplumber
import fitz  # PyMuPDF


RENDER_DPI = 300
ZOOM = RENDER_DPI / 72.0  # PDF default is 72 dpi


def parse_pages(spec: str, total: int) -> list[int]:
    """Parse `--pages 43-62` or `--pages 1,3,5` or `--pages all` into a 1-indexed list."""
    if spec == "all":
        return list(range(1, total + 1))
    out: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            a, b = chunk.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(chunk))
    return [p for p in out if 1 <= p <= total]


def render_page_png(fitz_doc: fitz.Document, page_idx_0: int, out_path: Path) -> tuple[int, int]:
    """Render page (0-indexed) to PNG at RENDER_DPI. Returns (width_px, height_px)."""
    page = fitz_doc[page_idx_0]
    mat = fitz.Matrix(ZOOM, ZOOM)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    pix.save(str(out_path))
    return pix.width, pix.height


def pdf_bbox_to_image_bbox(bbox: tuple[float, float, float, float], page_height_pdf: float) -> list[float]:
    """
    pdfplumber returns bbox as (x0, top, x1, bottom) where origin is top-left of page
    in pdf points (1pt = 1/72 inch). When we render at RENDER_DPI, multiply by ZOOM.
    pdfplumber's `top` is already from the top-left, so no flip is needed.
    """
    x0, top, x1, bottom = bbox
    return [x0 * ZOOM, top * ZOOM, x1 * ZOOM, bottom * ZOOM]


def extract_tables_from_page(page: "pdfplumber.page.Page") -> list[dict]:
    """
    For every table on the page, return:
        {
            "table_idx": int,
            "bbox_pdf": [x0, top, x1, bottom],         # pdf points, origin TL
            "bbox_image": [px0, py0, px1, py1],         # pixels at RENDER_DPI
            "n_rows": int,
            "n_cols": int,
            "cells": [
                {
                    "row": int, "col": int,
                    "bbox_pdf": [...], "bbox_image": [...],
                    "text": str,
                }
            ],
            "rows_text": [[...row 0 cell texts...], ...]  # convenience
        }
    """
    out: list[dict] = []
    try:
        tables = page.find_tables()
    except Exception as e:
        print(f"[warn] page {page.page_number}: find_tables failed: {e}", file=sys.stderr)
        return out

    page_h = page.height
    for ti, table in enumerate(tables):
        try:
            table_bbox = list(table.bbox)  # (x0, top, x1, bottom) in pdf points
        except Exception:
            table_bbox = None

        rows_text = table.extract() or []
        n_rows = len(rows_text)
        n_cols = max((len(r) for r in rows_text), default=0)

        cells: list[dict] = []
        # table.cells gives a flat list of (x0, top, x1, bottom) for each cell, row-major
        # but in pdfplumber 0.11 it's table.cells: list of bbox tuples
        try:
            cell_bboxes = table.cells  # list of (x0, top, x1, bottom)
        except Exception:
            cell_bboxes = []

        # pdfplumber returns cells row-major, but the rows from extract() drive (row, col)
        # mapping. Walk the rows and assign cells by position.
        idx = 0
        for r_i, row in enumerate(rows_text):
            for c_i, cell_text in enumerate(row):
                bbox_pdf = None
                bbox_img = None
                if idx < len(cell_bboxes):
                    cb = cell_bboxes[idx]
                    if cb is not None:
                        bbox_pdf = [float(v) for v in cb]
                        bbox_img = pdf_bbox_to_image_bbox(tuple(cb), page_h)
                cells.append({
                    "row": r_i,
                    "col": c_i,
                    "text": cell_text if cell_text is not None else "",
                    "bbox_pdf": bbox_pdf,
                    "bbox_image": bbox_img,
                })
                idx += 1

        out.append({
            "table_idx": ti,
            "bbox_pdf": table_bbox,
            "bbox_image": pdf_bbox_to_image_bbox(tuple(table_bbox), page_h) if table_bbox else None,
            "n_rows": n_rows,
            "n_cols": n_cols,
            "cells": cells,
            "rows_text": rows_text,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="Input PDF path")
    ap.add_argument("out_dir", help="Output directory")
    ap.add_argument("--pages", default="all", help="Page range, e.g. 43-62 or 1,3,5 or all")
    args = ap.parse_args()

    pdf_path = Path(args.pdf).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists():
        print(f"[error] PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    fitz_doc = fitz.open(str(pdf_path))
    total = fitz_doc.page_count
    page_list = parse_pages(args.pages, total)

    meta = {
        "source_pdf": str(pdf_path),
        "source_pdf_name": pdf_path.name,
        "page_count": total,
        "render_dpi": RENDER_DPI,
        "extracted_pages": page_list,
        "page_sizes_pdf": {},   # 1-indexed page -> (w, h)
        "page_sizes_image": {},
    }

    with pdfplumber.open(str(pdf_path)) as pdf:
        for p in page_list:
            page = pdf.pages[p - 1]
            meta["page_sizes_pdf"][str(p)] = [float(page.width), float(page.height)]

            # 1. render PNG via PyMuPDF (faster + clearer than pdfplumber.images)
            png_path = out_dir / f"page_{p:03d}.png"
            w, h = render_page_png(fitz_doc, p - 1, png_path)
            meta["page_sizes_image"][str(p)] = [w, h]

            # 2. text
            txt_path = out_dir / f"page_{p:03d}_text.txt"
            text = page.extract_text() or ""
            txt_path.write_text(text, encoding="utf-8")

            # 3. tables
            tables = extract_tables_from_page(page)
            tbl_path = out_dir / f"page_{p:03d}_tables.json"
            tbl_path.write_text(
                json.dumps({"page": p, "tables": tables}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[ok] page {p}: {len(tables)} table(s), text={len(text)} chars, img={w}x{h}", flush=True)

    fitz_doc.close()
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[done] wrote {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
