"""Stage 1 — local MinerU 6-way parallel batch runner.

Runs MinerU on all PDFs in $PDF_INPUT_DIR, writing per-doc output dirs
under $MINERU_OUTPUT_DIR. Idempotent: skips PDFs whose output already
contains a *_content_list.json file.

★★★ THIS IS THE ONE FILE YOU NEED TO EDIT. ★★★

Specifically: replace the body of `run_mineru_for_pdf()` with the exact
command line you tested for 1-PDF MinerU on your A100 (your "magic-pdf"
or "mineru" CLI invocation, with whatever flags you found that work for
6-way parallelism).

Everything else — directory layout, parallelism, idempotency, error
handling — is already wired and should not need changes.

Usage on A100:
    cd coating_kg/scripts/a100_stages_0_3
    python 01_batch_mineru.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration (env-driven, with sane defaults)
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parents[2]
PDF_INPUT_DIR = Path(os.environ.get("PDF_INPUT_DIR", str(REPO / "data" / "pdf")))
MINERU_OUTPUT_DIR = Path(
    os.environ.get("MINERU_OUTPUT_DIR", str(REPO / "data" / "mineru_output"))
)
N_GPU_WORKERS = int(os.environ.get("MINERU_N_WORKERS", "6"))
MAX_FAILURES = int(os.environ.get("MINERU_MAX_FAILURES", "10"))


def _default_mineru_cmd() -> str:
    sibling = Path(sys.executable).resolve().parent / "mineru"
    return str(sibling) if sibling.exists() else "mineru"


def _content_list_done(out_dir: Path) -> bool:
    return any(
        (out_dir / d).exists()
        and (
            any((out_dir / d).glob("*_content_list.json"))
            or any((out_dir / d).glob("*_content_list_v2.json"))
        )
        for d in (".", "auto", "ocr", "txt")
    )


def _normalize_mineru_output(out_dir: Path) -> bool:
    """Normalize MinerU CLI output to <doc_id>/<method>/*_content_list.json.

    The installed A100 `mineru` CLI writes one extra layer:
        <out_dir>/<pdfstem>/<method>/*_content_list.json
    Downstream stages expect:
        <out_dir>/<method>/*_content_list.json
    """
    candidates = sorted(out_dir.rglob("*_content_list.json")) or sorted(
        out_dir.rglob("*_content_list_v2.json")
    )
    if not candidates:
        return False

    source_method_dir = candidates[0].parent
    method = source_method_dir.name if source_method_dir.name in {"auto", "ocr", "txt"} else "auto"
    normalized = out_dir / method

    if source_method_dir.resolve() != normalized.resolve():
        if normalized.exists():
            shutil.rmtree(normalized, ignore_errors=True)
        shutil.copytree(source_method_dir, normalized)

    # Compatibility for unit_materializer: it resolves relative img_path values
    # against <mineru_root>/<doc_id>/images/...
    images_src = normalized / "images"
    images_dst = out_dir / "images"
    if images_src.exists():
        shutil.copytree(images_src, images_dst, dirs_exist_ok=True)

    return _content_list_done(out_dir)


# ---------------------------------------------------------------------------
# ★★★ EDIT THIS FUNCTION ★★★
# ---------------------------------------------------------------------------
def run_mineru_for_pdf(pdf_path: Path, out_dir: Path) -> int:
    """Invoke local MinerU on a single PDF.

    INPUTS:
      pdf_path: absolute path to the source PDF
      out_dir:  absolute path to per-PDF output dir
                (will already exist; mkdir done by caller)

    REQUIREMENT:
      after this function returns 0, the directory structure must be either:
        out_dir/auto/<uuid>_content_list.json
      or:
        out_dir/<uuid>_content_list.json
      Anything else and stage 2 (unit_extractor) won't find the layout.

    REPLACE THE COMMAND BELOW with the exact flags you verified work for
    6-way parallel MinerU on your A100. Examples that match the schema:

        magic-pdf -p <pdf> -o <out_dir> -m auto
        mineru -p <pdf> -o <out_dir> --backend pipeline
        python -m mineru.cli.batch <pdf> -o <out_dir>

    Return the subprocess return code (0 = success).
    """
    cmd = [
        os.environ.get("MINERU_CMD", _default_mineru_cmd()),
        "--path", str(pdf_path),
        "--output", str(out_dir),
        "--backend", os.environ.get("MINERU_BACKEND", "pipeline"),
        "--method", os.environ.get("MINERU_METHOD", "auto"),
        "--lang", os.environ.get("MINERU_LANG", "en"),
        "--formula", os.environ.get("MINERU_FORMULA", "false"),
        "--table", os.environ.get("MINERU_TABLE", "true"),
    ]
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).resolve().parent) + os.pathsep + env.get("PATH", "")
    env.setdefault("MINERU_MODEL_SOURCE", "local")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env)
    if proc.returncode != 0:
        print(f"    stderr: {proc.stderr[:300]}", file=sys.stderr)
    return proc.returncode


# ---------------------------------------------------------------------------
# Worker (top-level so it pickles for ProcessPoolExecutor)
# ---------------------------------------------------------------------------
def _process_one(pdf_path: Path) -> tuple[str, str, float]:
    """Returns (doc_id, status, elapsed_seconds).
    status ∈ {"ok", "skip", "fail"}.
    """
    doc_id = pdf_path.stem
    out_dir = MINERU_OUTPUT_DIR / doc_id

    # Idempotency: skip if any *_content_list.json already exists
    if _content_list_done(out_dir):
        return (doc_id, "skip", 0.0)

    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        rc = run_mineru_for_pdf(pdf_path, out_dir)
    except Exception as exc:
        print(f"    [{doc_id}] subprocess crashed: {exc}", file=sys.stderr)
        return (doc_id, "fail", time.time() - t0)
    elapsed = time.time() - t0
    if rc != 0:
        return (doc_id, "fail", elapsed)
    # Sanity check: did MinerU actually write content_list.json?
    found = _normalize_mineru_output(out_dir)
    return (doc_id, "ok" if found else "fail", elapsed)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    if not PDF_INPUT_DIR.exists():
        print(f"ERROR: PDF_INPUT_DIR does not exist: {PDF_INPUT_DIR}", file=sys.stderr)
        print("       Set env var PDF_INPUT_DIR to your PDFs folder.", file=sys.stderr)
        return 2

    MINERU_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(PDF_INPUT_DIR.glob("*.pdf"))
    print("=" * 70)
    print(f"Stage 1 — MinerU batch")
    print("=" * 70)
    print(f"  PDF input:       {PDF_INPUT_DIR}  ({len(pdfs)} files)")
    print(f"  MinerU output:   {MINERU_OUTPUT_DIR}")
    print(f"  Parallel workers: {N_GPU_WORKERS}")
    print()

    counts = {"ok": 0, "skip": 0, "fail": 0}
    failures: list[str] = []
    overall_t0 = time.time()

    with ProcessPoolExecutor(max_workers=N_GPU_WORKERS) as pool:
        futures = {pool.submit(_process_one, p): p for p in pdfs}
        for i, fut in enumerate(as_completed(futures), 1):
            doc_id, status, elapsed = fut.result()
            counts[status] += 1
            tag = {"ok": "[OK]  ", "skip": "[SKIP]", "fail": "[FAIL]"}[status]
            print(f"  [{i:3d}/{len(pdfs)}] {tag} {doc_id}  ({elapsed:.0f}s)")
            if status == "fail":
                failures.append(doc_id)

    overall_elapsed = time.time() - overall_t0
    print()
    print("=" * 70)
    print(f"Done in {overall_elapsed/60:.1f} min")
    print(f"  ok:    {counts['ok']}")
    print(f"  skip:  {counts['skip']} (already done)")
    print(f"  fail:  {counts['fail']}")
    if failures:
        print()
        print("Failed PDFs:")
        for d in failures[:20]:
            print(f"  {d}")
        if len(failures) > 20:
            print(f"  ... ({len(failures) - 20} more)")

        failure_manifest = MINERU_OUTPUT_DIR / "_failed_pdfs.txt"
        failure_manifest.write_text(
            "\n".join(failures) + "\n",
            encoding="utf-8",
        )
        print()
        print(f"Failure manifest: {failure_manifest}")

    if counts["ok"] + counts["skip"] == 0:
        print("STOP: no PDFs produced usable MinerU output.", file=sys.stderr)
        return 1
    if counts["fail"] > MAX_FAILURES:
        print(
            f"STOP: {counts['fail']} failures exceed MINERU_MAX_FAILURES={MAX_FAILURES}.",
            file=sys.stderr,
        )
        return 1
    if counts["fail"]:
        print(
            f"Continuing with {counts['fail']} failed PDF(s); "
            f"MINERU_MAX_FAILURES={MAX_FAILURES}."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
