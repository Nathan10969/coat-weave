from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz


def parse_pages(spec: str, total_pages: int) -> list[int]:
    if spec.strip().lower() == "all":
        return list(range(1, total_pages + 1))

    pages: set[int] = set()
    for part in spec.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            pages.add(int(token))
    return [page for page in sorted(pages) if 1 <= page <= total_pages]


def render_pdf_pages(
    pdf_path: Path | str,
    output_dir: Path | str,
    pages: str = "all",
    dpi: int = 300,
) -> dict[str, Any]:
    pdf = Path(pdf_path).resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[dict[str, Any]] = []
    with fitz.open(pdf) as doc:
        selected = parse_pages(pages, doc.page_count)
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page_number in selected:
            page = doc.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            file_path = out_dir / f"page_{page_number:04d}.png"
            pixmap.save(file_path)
            written.append(
                {
                    "page": page_number,
                    "file": str(file_path),
                    "width": pixmap.width,
                    "height": pixmap.height,
                }
            )
        page_count = doc.page_count

    manifest = {
        "schema_version": "pdf_page_raster_manifest_v1",
        "source_pdf": str(pdf),
        "output_dir": str(out_dir),
        "page_count": page_count,
        "dpi": dpi,
        "rendered_pages": [item["page"] for item in written],
        "files": written,
    }
    (out_dir / "render_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Render PDF pages to PNG images.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--pages", default="all")
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()

    manifest = render_pdf_pages(args.pdf, args.output_dir, pages=args.pages, dpi=args.dpi)
    print(f"Wrote {len(manifest['files'])} page images to {manifest['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
