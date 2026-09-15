"""Time a 5-PDF MinerU batch end-to-end.

Prints submit / poll / download / total durations. Use to decide whether
real batching is worth deeper integration work.

Usage:
    python scripts/test_batch_5.py            # picks first 5 PDFs in PDF_INPUT_DIR
    python scripts/test_batch_5.py --dry-run  # list candidates, no API calls
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys

import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from coating_kg.config import SETTINGS  # noqa: E402
from coating_kg.pipeline.pdf_layout import (  # noqa: E402
    MinerUError,
    _is_text_pdf,
    download_result,
    submit_pdfs,

    wait_for_completion,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("batch5")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="List candidates and OCR decision, no API calls.")
    ap.add_argument("-n", type=int, default=5, help="Batch size (default 5).")
    args = ap.parse_args()

    pdf_dir = SETTINGS.pdf_input_dir
    if not pdf_dir.exists():
        log.error("PDF_INPUT_DIR does not exist: %s", pdf_dir)
        return 1

    pdfs = sorted(pdf_dir.glob("*.pdf"))[: args.n]
    if len(pdfs) < args.n:
        log.error("Need %d PDFs in %s, found %d.", args.n, pdf_dir, len(pdfs))
        return 1

    log.info("Batch of %d:", len(pdfs))
    for p in pdfs:
        is_text = _is_text_pdf(p)
        log.info(
            "  %-40s %5.1f MB  text-layer=%s -> is_ocr=%s",
            p.name, p.stat().st_size / 1e6, is_text, not is_text,
        )

    if args.dry_run:
        log.info("dry-run; exiting.")
        return 0

    out_root = SETTINGS.mineru_output_dir / "_batch_test"
    if out_root.exists():
        shutil.rmtree(out_root, ignore_errors=True)
    out_root.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        batch_id = submit_pdfs(pdfs)
    except MinerUError as exc:
        log.error("submit failed: %s", exc)
        return 2
    t_submit = time.time() - t0
    log.info("submit (POST + %d PUTs): %.1fs  batch_id=%s", len(pdfs), t_submit, batch_id)

    t1 = time.time()
    try:
        final = wait_for_completion(batch_id, kind="batch")
    except MinerUError as exc:
        log.error("polling failed: %s", exc)
        return 2
    t_poll = time.time() - t1
    log.info("MinerU process + poll:    %.1fs", t_poll)

    results = final.get("extract_result") or final.get("results") or []
    log.info("Got %d result entries.", len(results))

    t2 = time.time()
    for r in results:
        url = r.get("full_zip_url") or r.get("zip_url") or r.get("result_url")
        name = r.get("file_name") or r.get("name") or "unknown"
        if not url:
            log.warning("no zip url for %s 鈥?skipping download", name)
            continue
        download_result(url, out_root / Path(name).stem)
    t_dl = time.time() - t2
    log.info("download all zips:        %.1fs", t_dl)

    total = time.time() - t0
    log.info(
        "=== TOTAL: %.1fs for %d PDFs (%.1fs/PDF amortised) ===",
        total, len(pdfs), total / len(pdfs),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
