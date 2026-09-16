"""端到端 V1 pipeline runner: PDF(s) → CSV 一条命令。

把解耦的各 stage 串起来，调用方不用一个个调。

  每篇 PDF 跑的 stages:
    1.   MinerU                                 (经 cli.py ingest)
    0.5  patent_metadata_extractor               (直接调)
    2-7  Examples / units / VLM / paragraphs / facts / polarity   (经 ingest)

  跑批末尾跑一次 (跨所有 PDF):
    9.   build_coatings_csv
    9.5  run_passage_extractor                 (optional --with-layer1)
    KG   build_kg_export                       (optional --with-kg)

用法 (Windows PowerShell):

    cd .\\coating_kg

    # 单 PDF
    & "python" scripts\\run_pipeline.py `
      .\\20260502062406317\\pdf\\WO2026077939A1.pdf

    # 多个特定 PDF
    & "python" scripts\\run_pipeline.py `
      .\\20260502062406317\\pdf\\WO2026077939A1.pdf `
      .\\20260502062406317\\pdf\\WO2026XXXXXX.pdf

    # 目录里前 5 篇 (PowerShell 展开)
    $pdfs = (Get-ChildItem .\\20260502062406317\\pdf\\*.pdf | Select-Object -First 5).FullName
    & "python" scripts\\run_pipeline.py @pdfs

    # 348 全跑 (耐心等 — ~5h, ~¥150)
    $pdfs = (Get-ChildItem .\\20260502062406317\\pdf\\*.pdf).FullName
    & "python" scripts\\run_pipeline.py @pdfs

  Skip flags:
    --no-ingest    跳过 Stage 1-7 (用已缓存的 units/facts)
    --no-meta      跳过 Stage 0.5
    --force-meta   ingest 后额外重跑一次 standalone metadata
    --no-csv       跳过 Stage 9
    --with-layer1  额外生成 data/layer1/<doc_id>/layer1.json
    --with-kg      额外生成 data/kg/*.jsonl
"""
from __future__ import annotations

import argparse
import json
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
DEFAULT_CSV_PATH = OUT_DIR / "coatings_wide.csv"


def log(*args: object, **kwargs: object) -> None:
    """Print progress immediately even when stdout is redirected to a file."""
    print(*args, **kwargs, flush=True)


def elog(*args: object, **kwargs: object) -> None:
    """Print warnings/errors immediately to stderr."""
    print(*args, file=sys.stderr, **kwargs, flush=True)


try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
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


def run_ingest(pdf_path: Path, *, retries: int = 2, log_dir: Path | None = None) -> bool:
    """对单 PDF 跑 cli.py ingest --skip-db。

    成功 True。错误打到 stderr 但不杀整批。
    """
    log_path = log_dir / f"{pdf_path.stem}.log" if log_dir else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log(f"  [Stage 1-7] ingest --skip-db {pdf_path.name}  (log: {log_path})")
    else:
        log(f"  [Stage 1-7] ingest --skip-db {pdf_path.name}")
    t0 = time.time()
    try:
        # 不捕获 output — 让 cli.py 的进度日志直接流到终端，
        # 用户能看到 per-unit fact extraction 那 3 分钟的进度，不会盯着空屏。
        env = {
            **os.environ,
            "PYTHONPATH": str(REPO / "src"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
        if log_path:
            with log_path.open("w", encoding="utf-8") as log_file:
                result = subprocess.run(
                    [PYTHON, "-m", "coating_kg", "ingest", "--skip-db", str(pdf_path)],
                    cwd=REPO,
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
        else:
            result = subprocess.run(
                [PYTHON, "-m", "coating_kg", "ingest", "--skip-db", str(pdf_path)],
                cwd=REPO,
                env=env,
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
            return run_ingest(pdf_path, retries=retries - 1, log_dir=log_dir)
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
            return run_ingest(pdf_path, retries=retries - 1, log_dir=log_dir)
        elog(f"    [FAIL] ingest returned {result.returncode} for {pdf_path.name} after {elapsed:.0f}s")
        if log_path and log_path.exists():
            elog(f"    [FAIL] log: {log_path}")
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
        ingest_ok = run_ingest(pdf, retries=args.ingest_retries, log_dir=args.log_dir)
        if not ingest_ok:
            elapsed = time.time() - per_pdf_t0
            print(f"  [TOTAL]   {elapsed:.0f}s for this PDF")
            return pdf, False, False, elapsed

    # cli.py ingest already runs the patent metadata gate. Count metadata from
    # the artifact it writes unless an explicit standalone pass is requested.
    if not args.no_meta:
        metadata_ok = (
            run_metadata(doc_id)
            if getattr(args, "force_meta", False)
            else metadata_artifact_exists(doc_id)
        )

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


def metadata_artifact_exists(doc_id: str) -> bool:
    path = REPO / "data" / "patents" / f"{doc_id}__patent_meta.json"
    return path.exists() and path.stat().st_size > 0


def run_csv(csv_output: Path | None = None, kg_dir: Path | None = None) -> bool:
    """Stage 9: build_coatings_csv (跑一次，跨所有已加载 units)。"""
    output_path = (csv_output or DEFAULT_CSV_PATH).resolve()
    kg_path = (kg_dir or (REPO / "data" / "kg")).resolve()
    print("  [Stage 9]   build_coatings_csv (from data/kg projection)")
    t0 = time.time()
    try:
        cmd = [
            PYTHON,
            str(REPO / "scripts" / "build_coatings_csv.py"),
            "--kg-dir",
            str(kg_path),
        ]
        if csv_output:
            cmd.extend(["--output", str(output_path)])
        result = subprocess.run(
            cmd,
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
    if not output_path.exists() or output_path.stat().st_size == 0:
        print(f"    [FAIL] CSV output missing or empty: {output_path}", file=sys.stderr)
        return False
    row_count = _count_csv_data_rows(output_path)
    print(f"    [OK]   CSV built in {elapsed:.0f}s -> {output_path} ({row_count} rows)")
    return True


def _count_csv_data_rows(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            return max(0, sum(1 for _ in handle) - 1)
    except Exception:  # noqa: BLE001
        return -1


def run_layer1(enable_tier_b: bool = False) -> bool:
    """Stage 9.5: build Layer 1 passage/figure JSON artifacts."""
    print("  [Stage 9.5] run_passage_extractor (Layer 1)")
    t0 = time.time()
    cmd = [PYTHON, str(REPO / "scripts" / "run_passage_extractor.py")]
    if enable_tier_b:
        cmd.append("--enable-tier-b")
    try:
        result = subprocess.run(
            cmd,
            cwd=REPO,
            env={**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONIOENCODING": "utf-8"},
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    [FAIL] Layer 1 build crashed: {exc}", file=sys.stderr)
        return False
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"    [FAIL] Layer 1 build returned {result.returncode} after {elapsed:.0f}s", file=sys.stderr)
        return False
    print(f"    [OK]   Layer 1 built in {elapsed:.0f}s")
    return True


def run_kg_export(kg_output: Path | None = None, *, doc_ids: list[str] | None = None) -> bool:
    """Build explicit KG JSONL artifacts."""
    output_dir = (kg_output or (REPO / "data" / "kg")).resolve()
    print("  [KG]        build_kg_export")
    t0 = time.time()
    cmd = [PYTHON, str(REPO / "scripts" / "build_kg_export.py")]
    if kg_output:
        cmd.extend(["--out-dir", str(output_dir)])
    for doc_id in doc_ids or []:
        cmd.extend(["--doc-id", doc_id])
    try:
        result = subprocess.run(
            cmd,
            cwd=REPO,
            env={**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONIOENCODING": "utf-8"},
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    [FAIL] KG export crashed: {exc}", file=sys.stderr)
        return False
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"    [FAIL] KG export returned {result.returncode} after {elapsed:.0f}s", file=sys.stderr)
        return False
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists() or manifest_path.stat().st_size == 0:
        print(f"    [FAIL] KG manifest missing or empty: {manifest_path}", file=sys.stderr)
        return False
    print(f"    [OK]   KG export built in {elapsed:.0f}s -> {output_dir}")
    return True


def write_run_manifest(
    *,
    log_dir: Path,
    input_pdfs: list[Path],
    ok_ingest: int,
    fail_ingest: int,
    ok_meta: int,
    fail_meta: int,
    failed_pdfs: list[Path],
    csv_enabled: bool,
    csv_ok: bool,
    csv_path: Path,
    layer1_enabled: bool,
    layer1_ok: bool,
    kg_enabled: bool,
    kg_ok: bool,
    kg_output_dir: Path,
    exit_code: int,
    elapsed_seconds: float,
) -> Path:
    manifest = {
        "schema_version": "run_pipeline_manifest_v1",
        "pdf_count": len(input_pdfs),
        "pdfs": [str(path) for path in input_pdfs],
        "ingest": {"ok": ok_ingest, "failed": fail_ingest},
        "metadata": {"ok": ok_meta, "failed": fail_meta},
        "failed_pdfs": [str(path) for path in sorted(failed_pdfs)],
        "artifacts": {
            "csv": {
                "enabled": csv_enabled,
                "ok": csv_ok,
                "path": str(csv_path),
                "exists": csv_enabled and csv_path.exists(),
                "rows": _count_csv_data_rows(csv_path) if csv_enabled and csv_path.exists() else 0,
            },
            "layer1": {
                "enabled": layer1_enabled,
                "ok": layer1_ok,
                "path": str(REPO / "data" / "layer1"),
            },
            "kg": {
                "enabled": kg_enabled,
                "ok": kg_ok,
                "path": str(kg_output_dir),
                "manifest": str(kg_output_dir / "manifest.json"),
            },
        },
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }
    path = log_dir / "run-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="V1 pipeline: PDF(s) → wide CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("pdfs", nargs="+", type=Path, help="One or more PDF paths")
    ap.add_argument("--no-ingest", action="store_true", help="Skip Stage 1-7 (use cached)")
    ap.add_argument("--no-meta", action="store_true", help="Skip Stage 0.5")
    ap.add_argument("--force-meta", action="store_true", help="Run standalone metadata extraction after ingest")
    ap.add_argument("--no-csv", action="store_true", help="Skip Stage 9")
    ap.add_argument("--csv-output", type=Path, default=None, help="Stage 9 CSV output path")
    ap.add_argument("--with-layer1", action="store_true", help="Also build Stage 9.5 Layer 1 JSON")
    ap.add_argument("--layer1-enable-tier-b", action="store_true", help="Enable Layer 1 Tier B LLM NER")
    ap.add_argument("--with-kg", action="store_true", help="Also build explicit data/kg JSONL export")
    ap.add_argument("--kg-output-dir", type=Path, default=None, help="KG export output directory")
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
    ap.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Directory for per-PDF ingest logs (default: output/run_pipeline_logs/<timestamp>).",
    )
    args = ap.parse_args()

    load_env()
    if args.log_dir is None:
        args.log_dir = OUT_DIR / "run_pipeline_logs" / time.strftime("%Y%m%d_%H%M%S")
    else:
        args.log_dir = args.log_dir.resolve()
    args.log_dir.mkdir(parents=True, exist_ok=True)

    input_pdfs = [p.resolve() for p in args.pdfs]
    pdfs = input_pdfs
    missing = [p for p in pdfs if not p.exists()]
    if missing:
        print("ERROR: missing PDFs:")
        for p in missing:
            print(f"  {p}")
        return 1

    print("=" * 70)
    print(f"V1 Pipeline — {len(pdfs)} PDF(s)")
    print("=" * 70)
    print(f"Per-PDF logs: {args.log_dir}")
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
        if not args.no_meta:
            if metadata_ok:
                ok_meta += 1
            else:
                fail_meta += 1

    print()
    print("=" * 70)
    print("Per-PDF stages done.")
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

    layer1_ok = True
    if args.with_layer1:
        print()
        print("Building Layer 1 JSON artifacts...")
        layer1_ok = run_layer1(enable_tier_b=args.layer1_enable_tier_b)

    kg_ok = True
    if args.with_kg or not args.no_csv:
        print()
        print("Building explicit KG JSONL artifacts...")
        successful_doc_ids = sorted({path.stem for path, ingest_ok, _metadata_ok, _elapsed in results if ingest_ok})
        kg_ok = run_kg_export(args.kg_output_dir, doc_ids=successful_doc_ids)

    csv_ok = True
    if not args.no_csv:
        print()
        print("Building wide CSV from KG projection...")
        if kg_ok:
            csv_ok = run_csv(args.csv_output, args.kg_output_dir)
        else:
            print("    [FAIL] CSV skipped because KG export failed", file=sys.stderr)
            csv_ok = False

    overall_elapsed = time.time() - overall_t0
    print()
    print(f"Wall clock: {overall_elapsed/60:.1f} min")
    csv_path = (args.csv_output or DEFAULT_CSV_PATH).resolve()
    if not args.no_csv and csv_path.exists():
        size_kb = csv_path.stat().st_size / 1024
        print(f"CSV: {csv_path}  ({size_kb:.1f} KB)")
    kg_output_dir = (args.kg_output_dir or (REPO / "data" / "kg")).resolve()

    exit_code = 0
    if fail_ingest:
        exit_code = 2
    elif not kg_ok:
        exit_code = 5
    elif not csv_ok:
        exit_code = 3
    elif not layer1_ok:
        exit_code = 4

    manifest_path = write_run_manifest(
        log_dir=args.log_dir,
        input_pdfs=input_pdfs,
        ok_ingest=ok_ingest,
        fail_ingest=fail_ingest,
        ok_meta=ok_meta,
        fail_meta=fail_meta,
        failed_pdfs=failed_pdfs,
        csv_enabled=not args.no_csv,
        csv_ok=csv_ok,
        csv_path=csv_path,
        layer1_enabled=args.with_layer1,
        layer1_ok=layer1_ok,
        kg_enabled=args.with_kg,
        kg_ok=kg_ok,
        kg_output_dir=kg_output_dir,
        exit_code=exit_code,
        elapsed_seconds=overall_elapsed,
    )
    print(f"Run manifest: {manifest_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
