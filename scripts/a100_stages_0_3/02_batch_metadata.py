"""Stage 0.5 — patent metadata extraction + IPC gate annotation.

Calls qwen-plus once per PDF (DashScope API) on the first-page text from
MinerU output, extracting title / IPC codes / applicant / abstract /
filing date, then runs the V1.2.5 4-signal `is_coating_patent`
classifier and saves the annotated JSON to
``data/patents/<doc_id>__patent_meta.json``.

NO CHANGES NEEDED — this script imports the production functions
directly from the coating_kg package. If you need to swap to a different
API endpoint, set OPENAI_BASE_URL / OPENAI_API_KEY in your environment.

Idempotent: skips PDFs whose patent_meta.json already exists.
Parallelism: 32-way thread pool (DashScope qwen-plus default concurrency
ceiling). IO-bound, so threads are sufficient.

Cost: ~¥0.01 per PDF, ~¥3.5 for 348.
Time: ~3-5 minutes for 348 PDFs.

Usage on A100:
    cd coating_kg/scripts/a100_stages_0_3
    python 02_batch_metadata.py
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from coating_kg.config import SETTINGS  # noqa: E402
from coating_kg.pipeline.patent_metadata_extractor import (  # noqa: E402
    extract_patent_metadata,
    find_content_list,
    save_patent_metadata,
)

N_API_WORKERS = int(os.environ.get("METADATA_N_WORKERS", "32"))


def _run_one(doc_id: str) -> tuple[str, str, str, float]:
    """Returns (doc_id, status, message, elapsed). status ∈ {ok, skip, fail}."""
    out_path = REPO / "data" / "patents" / f"{doc_id}__patent_meta.json"
    if out_path.exists():
        return (doc_id, "skip", "patent_meta already exists", 0.0)

    cl_path = find_content_list(SETTINGS.mineru_output_dir, doc_id)
    if cl_path is None:
        return (doc_id, "skip", "no content_list.json (MinerU not done?)", 0.0)

    t0 = time.time()
    try:
        meta = extract_patent_metadata(cl_path, doc_id)
        save_patent_metadata(REPO, doc_id, meta)
    except Exception as exc:
        return (doc_id, "fail", f"{type(exc).__name__}: {str(exc)[:120]}", time.time() - t0)

    elapsed = time.time() - t0
    is_coat = meta.get("is_coating_patent")
    reason = meta.get("is_coating_reason") or ""
    return (doc_id, "ok", f"is_coating={is_coat} ({reason})", elapsed)


def main() -> int:
    mineru_dir = SETTINGS.mineru_output_dir
    if not mineru_dir.exists():
        print(f"ERROR: MinerU output dir does not exist: {mineru_dir}", file=sys.stderr)
        print("       Run 01_batch_mineru.py first.", file=sys.stderr)
        return 2

    if not SETTINGS.openai.api_key:
        print("ERROR: OPENAI_API_KEY not set — stage 0.5 needs DashScope key.", file=sys.stderr)
        return 2

    doc_ids = sorted(p.name for p in mineru_dir.iterdir() if p.is_dir())
    if not doc_ids:
        print(f"ERROR: no doc dirs found under {mineru_dir} — run stage 1 first.", file=sys.stderr)
        return 2

    out_dir = REPO / "data" / "patents"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"Stage 0.5 — patent metadata + IPC gate")
    print("=" * 70)
    print(f"  MinerU dir:       {mineru_dir}  ({len(doc_ids)} doc_ids)")
    print(f"  Output dir:       {out_dir}")
    print(f"  Parallel workers: {N_API_WORKERS}  (thread pool)")
    print(f"  Model:            {SETTINGS.models.qwen_text}")
    print(f"  Base URL:         {SETTINGS.openai.base_url}")
    print()

    counts = {"ok": 0, "skip": 0, "fail": 0}
    coat_yes = coat_no = 0
    failures: list[tuple[str, str]] = []
    overall_t0 = time.time()

    with ThreadPoolExecutor(max_workers=N_API_WORKERS) as pool:
        futures = {pool.submit(_run_one, d): d for d in doc_ids}
        for i, fut in enumerate(as_completed(futures), 1):
            doc_id, status, msg, elapsed = fut.result()
            counts[status] += 1
            if status == "ok":
                if "is_coating=True" in msg:
                    coat_yes += 1
                elif "is_coating=False" in msg:
                    coat_no += 1
                # only print every Nth ok line to keep terminal sane
                if i % 20 == 0 or i == len(doc_ids):
                    print(f"  [{i:3d}/{len(doc_ids)}] [OK]   {doc_id}  {msg}  ({elapsed:.1f}s)")
            elif status == "skip":
                if i % 20 == 0:
                    print(f"  [{i:3d}/{len(doc_ids)}] [SKIP] {doc_id}  {msg}")
            else:
                print(f"  [{i:3d}/{len(doc_ids)}] [FAIL] {doc_id}  {msg}", file=sys.stderr)
                failures.append((doc_id, msg))

    overall_elapsed = time.time() - overall_t0
    print()
    print("=" * 70)
    print(f"Done in {overall_elapsed:.0f}s")
    print(f"  ok:    {counts['ok']}  (coating={coat_yes}, non-coating={coat_no})")
    print(f"  skip:  {counts['skip']}")
    print(f"  fail:  {counts['fail']}")
    if failures:
        print()
        print("Failed:")
        for d, m in failures[:20]:
            print(f"  {d}: {m}")
    return 0 if counts["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
