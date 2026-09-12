"""端到端 V1 pipeline runner: PDF(s) → CSV 一条命令。

把解耦的各 stage 串起来，调用方不用一个个调。

  每篇 PDF 跑的 stages:
    1.   MinerU                                 (经 cli.py ingest)
    0.5  patent_metadata_extractor               (直接调)
    2-7  Examples / units / VLM / paragraphs / facts / polarity   (经 ingest)

  跑批末尾跑一次 (跨所有 PDF):
    9.   build_coatings_csv

用法 (Windows PowerShell):

    cd coat-weave

    # 单 PDF
    & "python" scripts\\run_pipeline.py `
      data/pdf/WO2026077939A1.pdf

    # 多个特定 PDF
    & "python" scripts\\run_pipeline.py `
      data/pdf/WO2026077939A1.pdf `
      data/pdf/WO2026XXXXXX.pdf

    # 目录里前 5 篇 (PowerShell 展开)
    $pdfs = (Get-ChildItem data/pdf/*.pdf | Select-Object -First 5).FullName
    & "python" scripts\\run_pipeline.py @pdfs

    # 348 全跑 (耐心等 — ~5h, ~¥150)
    $pdfs = (Get-ChildItem data/pdf/*.pdf).FullName
    & "python" scripts\\run_pipeline.py @pdfs

  Skip flags:
    --no-ingest    跳过 Stage 1-7 (用已缓存的 units/facts)
    --no-meta      跳过 Stage 0.5
    --no-csv       跳过 Stage 9
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV = REPO / ".env"
PYTHON = sys.executable  # 调起我们的那个 Python
MINERU_DIR = REPO / "data" / "mineru_output"
OUT_DIR = REPO / "output"


def log(*args: object, **kwargs: object) -> None:
    """Print progress immediately even when stdout is redirected to a file."""
    print(*args, **kwargs, flush=True)


def elog(*args: object, **kwargs: object) -> None:
    """Print warnings/errors immediately to stderr."""
    print(*args, file=sys.stderr, **kwargs, flush=True)


try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except AttributeError:
    pass


def load_env() -> None:
    if not ENV.exists():
        print(f"WARN: .env not found at {ENV} — Stage 0.5 may fail if API key isn't already in env")
        return
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def run_ingest(pdf_path: Path, *, retries: int = 2) -> bool:
    """对单 PDF 跑 cli.py ingest --skip-db。

    成功 True。错误打到 stderr 但不杀整批。
    """
    log(f"  [Stage 1-7] ingest --skip-db {pdf_path.name}")
    t0 = time.time()
    try:
        # 不捕获 output — 让 cli.py 的进度日志直接流到终端，
        # 用户能看到 per-unit fact extraction 那 3 分钟的进度，不会盯着空屏。
        result = subprocess.run(
            [PYTHON, "-m", "coating_kg", "ingest", "--skip-db", str(pdf_path)],
            cwd=REPO,
            env={
                **os.environ,
                "PYTHONPATH": str(REPO / "src"),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
            },
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        if retries > 0:
            delay = min(60, 10 * (3 - retries))
            elog(
                f"    [RETRY] subprocess crashed for {pdf_path.name}: {exc}; "
                f"retrying in {delay}s"
            )
            time.sleep(delay)
            return run_ingest(pdf_path, retries=retries - 1)
        elog(f"    [FAIL] subprocess crashed for {pdf_path.name}: {exc}")
        return False
    elapsed = time.time() - t0
    if result.returncode != 0:
        if retries > 0:
            delay = min(60, 10 * (3 - retries))
            elog(
                f"    [RETRY] ingest returned {result.returncode} for {pdf_path.name} "
                f"after {elapsed:.0f}s; retrying in {delay}s"
            )
            time.sleep(delay)
            return run_ingest(pdf_path, retries=retries - 1)
        elog(f"    [FAIL] ingest returned {result.returncode} for {pdf_path.name} after {elapsed:.0f}s")
        return False
    log(f"    [OK]   ingest finished for {pdf_path.name} in {elapsed:.0f}s")
    return True


def run_one_pdf(pdf: Path, index: int, total: int, args: argparse.Namespace) -> tuple[Path, bool, bool, float]:
    """Run per-PDF stages for one PDF and return status for aggregation."""
    doc_id = pdf.stem
    print()
    print(f"[{index}/{total}] {doc_id}")
    per_pdf_t0 = time.time()

    ingest_ok = True
    metadata_ok = True

    if not args.no_ingest:
        ingest_ok = run_ingest(pdf, retries=args.ingest_retries)
        if not ingest_ok:
            elapsed = time.time() - per_pdf_t0
            print(f"  [TOTAL]   {elapsed:.0f}s for this PDF")
            return pdf, False, False, elapsed

    # cli.py ingest already runs the patent metadata gate. This standalone
    # metadata pass is retained only for explicit force_meta use.
    if not args.no_meta and getattr(args, "force_meta", False):
        metadata_ok = run_metadata(doc_id)

    elapsed = time.time() - per_pdf_t0
    print(f"  [TOTAL]   {elapsed:.0f}s for this PDF")
    return pdf, ingest_ok, metadata_ok, elapsed


def run_metadata(doc_id: str) -> bool:
    """Stage 0.5：对单 doc 跑 patent_metadata_extractor。"""
    sys.path.insert(0, str(REPO / "src"))
    try:
        from coating_kg.pipeline.patent_metadata_extractor import (  # type: ignore[import-not-found]
            extract_patent_metadata,
            find_content_list,
            save_patent_metadata,
        )
    except ImportError as exc:
        print(f"    [FAIL] cannot import patent_metadata_extractor: {exc}", file=sys.stderr)
        return False

    print(f"  [Stage 0.5] patent_metadata for {doc_id}")
    cl = find_content_list(MINERU_DIR, doc_id)
    if cl is None:
        print(f"    [SKIP] no MinerU content_list found for {doc_id}", file=sys.stderr)
        return False
    t0 = time.time()
    try:
        meta = extract_patent_metadata(cl, doc_id)
        out_path = save_patent_metadata(REPO, doc_id, meta)
    except Exception as exc:  # noqa: BLE001
        print(f"    [FAIL] metadata extraction crashed: {exc}", file=sys.stderr)
        return False
    elapsed = time.time() - t0
    applicant = meta.get("applicant") or "(unknown)"
    print(f"    [OK]   {applicant} — saved to {out_path.name} ({elapsed:.0f}s)")
    return True


def run_csv() -> bool:
    """Stage 9: build_coatings_csv (跑一次，跨所有已加载 units)。"""
    print(f"  [Stage 9]   build_coatings_csv (across all units in data/units/)")
    t0 = time.time()
    try:
        result = subprocess.run(
            [PYTHON, str(REPO / "scripts" / "build_coatings_csv.py")],
            cwd=REPO,
            env={**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONIOENCODING": "utf-8"},
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    [FAIL] CSV build crashed: {exc}", file=sys.stderr)
        return False
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"    [FAIL] CSV build returned {result.returncode} after {elapsed:.0f}s", file=sys.stderr)
        return False
    print(f"    [OK]   CSV built in {elapsed:.0f}s — output/coatings_wide.csv")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="V1 pipeline: PDF(s) → wide CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("pdfs", nargs="+", type=Path, help="One or more PDF paths")
    ap.add_argument("--no-ingest", action="store_true", help="Skip Stage 1-7 (use cached)")
    ap.add_argument("--no-meta", action="store_true", help="Skip Stage 0.5")
    ap.add_argument("--no-csv", action="store_true", help="Skip Stage 9")
    ap.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Number of PDFs to ingest concurrently (default: 6). Use 3 if API 429s persist.",
    )
    ap.add_argument(
        "--ingest-retries",
        type=int,
        default=2,
        help="Retry a failed PDF ingest this many times before marking it failed (default: 2).",
    )
    args = ap.parse_args()

    load_env()

    pdfs = [p.resolve() for p in args.pdfs]
    missing = [p for p in pdfs if not p.exists()]
    if missing:
        print("ERROR: missing PDFs:")
        for p in missing:
            print(f"  {p}")
        return 1

    print("=" * 70)
    print(f"V1 Pipeline — {len(pdfs)} PDF(s)")
    print("=" * 70)
    overall_t0 = time.time()
    ok_ingest, ok_meta, fail_ingest, fail_meta = 0, 0, 0, 0

    print(f"Workers: {max(1, args.workers)}")
    max_workers = max(1, args.workers)
    if max_workers == 1 or len(pdfs) == 1:
        results = [run_one_pdf(pdf, i, len(pdfs), args) for i, pdf in enumerate(pdfs, 1)]
    else:
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(run_one_pdf, pdf, i, len(pdfs), args): pdf
                for i, pdf in enumerate(pdfs, 1)
            }
            for fut in as_completed(futures):
                pdf = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    print(f"    [FAIL] worker crashed for {pdf.name}: {exc}", file=sys.stderr)
                    results.append((pdf, False, False, 0.0))

    failed_pdfs: list[Path] = []
    for _pdf, ingest_ok, metadata_ok, _elapsed in results:
        if ingest_ok:
            ok_ingest += 1
        else:
            fail_ingest += 1
            failed_pdfs.append(_pdf)
        if not args.no_meta and getattr(args, "force_meta", False):
            if metadata_ok:
                ok_meta += 1
            else:
                fail_meta += 1

    # Per-PDF work was already handled above, possibly concurrently.
    pdfs = []

    for i, pdf in enumerate(pdfs, 1):
        doc_id = pdf.stem
        print()
        print(f"[{i}/{len(pdfs)}] {doc_id}")
        per_pdf_t0 = time.time()

        if not args.no_ingest:
            ok = run_ingest(pdf)
            if ok:
                ok_ingest += 1
            else:
                fail_ingest += 1
                # ingest 失败时跳过 metadata（没 MinerU 输出）
                continue

        # V1.2.5 task #4b: cli.py ingest_cmd 已经在内部抽 patent metadata
        # (MinerU 后、Stage 3/6 LLM 前) 做早期 IPC 闸门。这里独立的
        # metadata pass 只在显式 --force-meta 时跑（罕见）。
        if not args.no_meta and getattr(args, "force_meta", False):
            ok = run_metadata(doc_id)
            if ok:
                ok_meta += 1
            else:
                fail_meta += 1

        per_pdf_elapsed = time.time() - per_pdf_t0
        print(f"  [TOTAL]   {per_pdf_elapsed:.0f}s for this PDF")

    print()
    print("=" * 70)
    print(f"Per-PDF stages done.")
    print(f"  ingest:    {ok_ingest} ok, {fail_ingest} failed")
    print(f"  metadata:  {ok_meta} ok, {fail_meta} failed")
    if failed_pdfs:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        failed_path = OUT_DIR / f"run_pipeline_failed_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        failed_path.write_text(
            "\n".join(str(p) for p in sorted(failed_pdfs)) + "\n",
            encoding="utf-8",
        )
        print(f"  failed list: {failed_path}")
    print("=" * 70)

    if not args.no_csv:
        print()
        print("Building wide CSV across all units accumulated so far...")
        run_csv()

    overall_elapsed = time.time() - overall_t0
    print()
    print(f"Wall clock: {overall_elapsed/60:.1f} min")
    csv_path = REPO / "output" / "coatings_wide.csv"
    if csv_path.exists():
        size_kb = csv_path.stat().st_size / 1024
        print(f"CSV: {csv_path}  ({size_kb:.1f} KB)")
    return 0 if fail_ingest == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
