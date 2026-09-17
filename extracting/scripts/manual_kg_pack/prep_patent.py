"""
Per-patent prep: assembles everything the manual_kg_pack extraction prompt needs.

Inputs:
    pdf            absolute path to the patent PDF
    mineru_dir     the MinerU output root for this patent
                   (must contain <patent>/<patent>/ocr/<patent>_middle.json + .md)
    out_dir        where to write the prepared input

Outputs in out_dir/:
    pages/page_NNN.png          rendered images (300 dpi, JPEG-style)
    kg_input.json               single deterministic file the LLM consumes
    mineru.md                   copy of MinerU markdown (paragraph text)
    middle.json                 copy of MinerU middle.json (block tree + bboxes)

kg_input.json schema:
{
    "doc_id": "WO2015132366A1",
    "source_pdf_name": "...",
    "source_pdf_path": "...",
    "page_count": 68,
    "render_dpi": 300,
    "zoom": 4.166...,
    "examples_scope": {
        "detected_pages": [43, 44, ...],
        "first_example_page": 43,
        "marker_hits": [{"page": 43, "snippet": "Examples"}, ...]
    },
    "pages": [
        {
            "page": 43,
            "page_size_pdf": [w, h],
            "page_size_image": [w, h],
            "image_path": "pages/page_043.png",
            "in_examples_scope": true,
            "paragraphs": [
                {"block_id": "p43_b0", "type": "title|text|interline_equation",
                 "bbox_pdf": [...], "bbox_image": [...], "text": "..."}
            ],
            "tables": [
                {
                    "block_id": "p46_t0",
                    "table_caption": "Table 1 - Basic formulation of epoxy-based paints",
                    "caption_bbox_pdf": [...], "caption_bbox_image": [...],
                    "bbox_pdf": [...], "bbox_image": [...],
                    "html": "<table>...</table>",
                    "rows": [
                        {"row_idx": 0, "row_bbox_pdf": [...], "row_bbox_image": [...],
                         "cells": [{"col_idx": 0, "text": "...", "rowspan": 1, "colspan": 1}, ...]}
                    ]
                }
            ],
            "figures": [
                {"block_id": "p43_f0", "bbox_pdf": [...], "bbox_image": [...],
                 "caption": "...", "image_subpath": "..."}
            ]
        }
    ]
}

The point of this file: the LLM in the downstream prompt does NOT have to fight
PDF rendering, OCR layout, table parsing, or cell HTML extraction. It just reads
this JSON + the page PNGs.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import fitz  # PyMuPDF

RENDER_DPI = 300
ZOOM = RENDER_DPI / 72.0

# Examples-section markers (English / German / Chinese / French)
EXAMPLES_MARKERS = [
    r"\bExamples?\b",
    r"\bBeispiele?\b",
    r"实\s*施\s*例",
    r"\bExemples?\b",
]
EXAMPLES_RE = re.compile("|".join(EXAMPLES_MARKERS), re.IGNORECASE)
# Stop markers (claims / end-of-examples)
STOP_RE = re.compile(r"\b(Claims?|Ansprüche|权\s*利\s*要\s*求|Revendications?|We claim)\b", re.IGNORECASE)


def pdf_bbox_to_image_bbox(bbox: list[float] | None) -> list[float] | None:
    if not bbox:
        return None
    x0, y0, x1, y1 = bbox[:4]
    return [x0 * ZOOM, y0 * ZOOM, x1 * ZOOM, y1 * ZOOM]


def collect_block_text(block: dict) -> str:
    """Walk a MinerU block and concatenate all text spans."""
    out: list[str] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            if span.get("type") in ("text", "inline_equation"):
                t = span.get("content", "")
                if t:
                    out.append(t)
    return " ".join(out).strip()


def collect_table_html(table_block: dict) -> tuple[str, list[float] | None, str, list[float] | None]:
    """Extract HTML + bbox for table_body, plus caption text+bbox if present."""
    html = ""
    body_bbox = None
    caption = ""
    caption_bbox = None
    for sub in table_block.get("blocks", []):
        t = sub.get("type")
        if t == "table_body":
            body_bbox = sub.get("bbox")
            for line in sub.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("type") == "table":
                        html = span.get("html", "")
        elif t in ("table_caption", "table_footnote"):
            txt = collect_block_text(sub)
            if t == "table_caption":
                caption = txt
                caption_bbox = sub.get("bbox")
            else:
                caption = (caption + " | " + txt).strip(" |") if caption else txt
    return html, body_bbox, caption, caption_bbox


def parse_html_table(html: str) -> list[dict]:
    """Parse a MinerU-style <table><tr><td rowspan=N colspan=M>...</td>...</tr>...</table>
    into rows of cells. Very tolerant — these are NOT well-formed HTML."""
    # Strip <table>...</table> wrappers
    inner = html
    inner = re.sub(r"</?table[^>]*>", "", inner, flags=re.IGNORECASE)
    rows_html = re.findall(r"<tr[^>]*>(.*?)</tr>", inner, flags=re.IGNORECASE | re.DOTALL)
    parsed: list[dict] = []
    for r_i, row_html in enumerate(rows_html):
        cell_re = re.finditer(
            r"<td(?P<attrs>[^>]*)>(?P<inner>.*?)</td>", row_html, flags=re.IGNORECASE | re.DOTALL
        )
        cells: list[dict] = []
        for c_i, m in enumerate(cell_re):
            attrs = m.group("attrs") or ""
            rs = re.search(r"rowspan\s*=\s*['\"]?(\d+)", attrs)
            cs = re.search(r"colspan\s*=\s*['\"]?(\d+)", attrs)
            txt = m.group("inner")
            # strip nested tags
            txt = re.sub(r"<[^>]+>", "", txt)
            txt = txt.replace("&nbsp;", " ").strip()
            cells.append({
                "col_idx": c_i,
                "text": txt,
                "rowspan": int(rs.group(1)) if rs else 1,
                "colspan": int(cs.group(1)) if cs else 1,
            })
        parsed.append({"row_idx": r_i, "cells": cells})
    return parsed


def estimate_row_bboxes(rows: list[dict], body_bbox: list[float] | None) -> None:
    """Given a list of parsed rows and the table body bbox in PDF coords, set
    `row_bbox_pdf` and `row_bbox_image` on each row by even slicing.
    Approximation — accurate enough for `click row to see cropped image`."""
    if not body_bbox or not rows:
        for r in rows:
            r["row_bbox_pdf"] = None
            r["row_bbox_image"] = None
        return
    x0, y0, x1, y1 = body_bbox
    h = (y1 - y0) / max(len(rows), 1)
    for i, r in enumerate(rows):
        ry0 = y0 + h * i
        ry1 = y0 + h * (i + 1)
        r["row_bbox_pdf"] = [x0, ry0, x1, ry1]
        r["row_bbox_image"] = pdf_bbox_to_image_bbox(r["row_bbox_pdf"])


def detect_examples_scope(pages: list[dict]) -> dict:
    """Scan paragraphs across all pages for Examples markers + Claims stop markers."""
    marker_hits: list[dict] = []
    stop_page: int | None = None
    for p in pages:
        for para in p["paragraphs"]:
            t = para["text"]
            if EXAMPLES_RE.search(t):
                marker_hits.append({"page": p["page"], "snippet": t[:80]})
            if STOP_RE.search(t) and stop_page is None and len(t) < 120:
                stop_page = p["page"]
    first = marker_hits[0]["page"] if marker_hits else None
    end = stop_page if stop_page else (pages[-1]["page"] if pages else None)
    if first is None:
        return {"detected_pages": [], "first_example_page": None, "stop_page": stop_page, "marker_hits": []}
    detected = [p["page"] for p in pages if p["page"] >= first and (stop_page is None or p["page"] < stop_page)]
    return {
        "detected_pages": detected,
        "first_example_page": first,
        "stop_page": stop_page,
        "marker_hits": marker_hits[:20],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="Source PDF path")
    ap.add_argument("mineru_dir", help="MinerU output root for this patent (must contain ocr/*_middle.json)")
    ap.add_argument("out_dir", help="Output directory for kg_input.json + pages/")
    ap.add_argument("--force-pages", default=None, help="Override examples scope, e.g. 43-62")
    args = ap.parse_args()

    pdf_path = Path(args.pdf).resolve()
    mineru_dir = Path(args.mineru_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pages").mkdir(exist_ok=True)

    # Locate MinerU middle.json and md
    middle_jsons = list(mineru_dir.rglob("*_middle.json"))
    if not middle_jsons:
        # Try recursive
        middle_jsons = list(Path(args.mineru_dir).rglob("*_middle.json"))
    if not middle_jsons:
        print(f"[error] no MinerU middle.json under {mineru_dir}", file=sys.stderr)
        return 2
    middle_path = middle_jsons[0]
    md_paths = list(middle_path.parent.glob("*.md"))
    md_path = md_paths[0] if md_paths else None

    # Copy MinerU artifacts
    shutil.copyfile(middle_path, out_dir / "middle.json")
    if md_path:
        shutil.copyfile(md_path, out_dir / "mineru.md")

    middle = json.loads(middle_path.read_text(encoding="utf-8"))
    pages_info = middle.get("pdf_info", [])

    # Open PDF for rendering
    fitz_doc = fitz.open(str(pdf_path))
    total_pages = fitz_doc.page_count
    if len(pages_info) != total_pages:
        print(f"[warn] middle.json has {len(pages_info)} pages, PDF has {total_pages}", file=sys.stderr)

    pages_out: list[dict] = []
    for pi, page_block in enumerate(pages_info):
        page_num = pi + 1
        page_size_pdf = page_block.get("page_size", [0, 0])
        para_blocks = page_block.get("para_blocks", [])

        paragraphs: list[dict] = []
        tables: list[dict] = []
        figures: list[dict] = []

        para_counter = tbl_counter = fig_counter = 0
        for blk in para_blocks:
            btype = blk.get("type")
            bbox = blk.get("bbox")
            if btype in ("text", "title", "interline_equation", "list"):
                txt = collect_block_text(blk)
                if txt:
                    paragraphs.append({
                        "block_id": f"p{page_num}_b{para_counter}",
                        "type": btype,
                        "bbox_pdf": bbox,
                        "bbox_image": pdf_bbox_to_image_bbox(bbox),
                        "text": txt,
                    })
                    para_counter += 1
            elif btype == "table":
                html, body_bbox, caption, cap_bbox = collect_table_html(blk)
                rows = parse_html_table(html) if html else []
                estimate_row_bboxes(rows, body_bbox)
                tables.append({
                    "block_id": f"p{page_num}_t{tbl_counter}",
                    "table_caption": caption,
                    "caption_bbox_pdf": cap_bbox,
                    "caption_bbox_image": pdf_bbox_to_image_bbox(cap_bbox),
                    "bbox_pdf": body_bbox or bbox,
                    "bbox_image": pdf_bbox_to_image_bbox(body_bbox or bbox),
                    "html": html,
                    "n_rows": len(rows),
                    "n_cols": max((len(r["cells"]) for r in rows), default=0),
                    "rows": rows,
                })
                tbl_counter += 1
            elif btype in ("image", "figure"):
                # Extract caption
                fig_caption = ""
                for sub in blk.get("blocks", []):
                    if sub.get("type") in ("image_caption", "figure_caption"):
                        fig_caption = collect_block_text(sub)
                figures.append({
                    "block_id": f"p{page_num}_f{fig_counter}",
                    "bbox_pdf": bbox,
                    "bbox_image": pdf_bbox_to_image_bbox(bbox),
                    "caption": fig_caption,
                })
                fig_counter += 1

        # Render page PNG
        page_png = out_dir / "pages" / f"page_{page_num:03d}.png"
        page = fitz_doc[pi]
        pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), alpha=False)
        pix.save(str(page_png))
        page_size_image = [pix.width, pix.height]

        pages_out.append({
            "page": page_num,
            "page_size_pdf": page_size_pdf,
            "page_size_image": page_size_image,
            "image_path": f"pages/page_{page_num:03d}.png",
            "paragraphs": paragraphs,
            "tables": tables,
            "figures": figures,
        })

    fitz_doc.close()

    # Detect Examples scope
    if args.force_pages:
        a, _, b = args.force_pages.partition("-")
        try:
            scope = {"detected_pages": list(range(int(a), int(b) + 1)),
                     "first_example_page": int(a), "stop_page": int(b) + 1,
                     "marker_hits": [], "source": "user_override"}
        except ValueError:
            print(f"[error] bad --force-pages {args.force_pages}", file=sys.stderr)
            return 2
    else:
        scope = detect_examples_scope(pages_out)
        scope["source"] = "auto_detect"

    # Mark in_examples_scope
    examples_set = set(scope["detected_pages"])
    for p in pages_out:
        p["in_examples_scope"] = p["page"] in examples_set

    kg_input = {
        "doc_id": pdf_path.stem.split(" - ")[0].replace("331_", "").strip()
                   if "WO" in pdf_path.stem.split(" - ")[0]
                   else pdf_path.stem,
        "source_pdf_name": pdf_path.name,
        "source_pdf_path": str(pdf_path),
        "page_count": total_pages,
        "render_dpi": RENDER_DPI,
        "zoom": ZOOM,
        "examples_scope": scope,
        "pages": pages_out,
    }
    (out_dir / "kg_input.json").write_text(
        json.dumps(kg_input, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Brief summary
    n_pages_in_scope = sum(1 for p in pages_out if p["in_examples_scope"])
    n_tables_in_scope = sum(len(p["tables"]) for p in pages_out if p["in_examples_scope"])
    n_tbl_rows = sum(sum(t["n_rows"] for t in p["tables"]) for p in pages_out if p["in_examples_scope"])
    n_paras = sum(len(p["paragraphs"]) for p in pages_out if p["in_examples_scope"])
    print(f"[done] doc_id={kg_input['doc_id']} "
          f"total_pages={total_pages} "
          f"examples_pages={n_pages_in_scope} (scope={scope['detected_pages'][:3]}..{scope['detected_pages'][-3:] if scope['detected_pages'] else []}) "
          f"tables_in_scope={n_tables_in_scope} table_rows={n_tbl_rows} paragraphs={n_paras}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
