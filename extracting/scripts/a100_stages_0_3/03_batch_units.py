"""Stages 2 + 3 — unit extraction (rule-based) + materialization (disk IO).

Walks every doc_id under data/mineru_output/, loads its content_list.json
(via the existing helper from coating_kg.pipeline.pdf_layout), extracts
figure/table units inside the Examples section (V1.2.5: examples_only=True),
and materializes each unit folder under data/units/<unit_id>/.

NO CHANGES NEEDED — this script imports from coating_kg.pipeline directly,
no logic is duplicated. CPU-only and disk-IO-only, completes in a few
minutes for 348 PDFs.

Idempotent on the unit level: materialize_unit overwrites files in place,
which is fine; the LLM stages tomorrow only need the latest content.

Usage on A100:
    cd coating_kg/scripts/a100_stages_0_3
    python 03_batch_units.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from coating_kg.config import SETTINGS  # noqa: E402
from coating_kg.pipeline import unit_extractor  # noqa: E402
from coating_kg.pipeline.pdf_layout import _load_layout_json  # noqa: E402
from coating_kg.pipeline.unit_materializer import materialize_unit  # noqa: E402


def _process_one(doc_id: str) -> tuple[str, int, str]:
    """Returns (doc_id, n_units, error_or_empty)."""
    try:
        layout = _load_layout_json(SETTINGS.mineru_output_dir, doc_id)
    except Exception as exc:
        return (doc_id, 0, f"layout load failed: {type(exc).__name__}: {str(exc)[:100]}")

    try:
        units = list(unit_extractor.iter_figure_table_units(layout, examples_only=True))
    except Exception as exc:
        return (doc_id, 0, f"unit extraction failed: {type(exc).__name__}: {str(exc)[:100]}")

    if not units:
        return (doc_id, 0, "")

    units_root = REPO / "data" / "units"
    units_root.mkdir(parents=True, exist_ok=True)

    err = ""
    n_ok = 0
    for unit in units:
        try:
            materialize_unit(
                unit, units_root, mineru_root=SETTINGS.mineru_output_dir,
            )
            n_ok += 1
        except Exception as exc:
            err = f"materialize failed at {unit.unit_id}: {type(exc).__name__}: {str(exc)[:80]}"
            # keep going — bad unit shouldn't poison the rest
    return (doc_id, n_ok, err)


def main() -> int:
    mineru_dir = SETTINGS.mineru_output_dir
    if not mineru_dir.exists():
        print(f"ERROR: MinerU output dir does not exist: {mineru_dir}", file=sys.stderr)
        return 2

    doc_ids = sorted(p.name for p in mineru_dir.iterdir() if p.is_dir())
    if not doc_ids:
        print(f"ERROR: no doc dirs under {mineru_dir} — run stage 1 first.", file=sys.stderr)
        return 2

    print("=" * 70)
    print(f"Stages 2+3 — unit extraction + materialization")
    print("=" * 70)
    print(f"  MinerU dir:  {mineru_dir}  ({len(doc_ids)} doc_ids)")
    print(f"  Units dir:   {REPO / 'data' / 'units'}")
    print()

    n_total_units = 0
    n_zero = 0
    failures: list[tuple[str, str]] = []
    overall_t0 = time.time()

    for i, doc_id in enumerate(doc_ids, 1):
        t0 = time.time()
        doc_id_, n_units, err = _process_one(doc_id)
        elapsed = time.time() - t0
        n_total_units += n_units
        if n_units == 0 and err == "":
            n_zero += 1
        if err:
            failures.append((doc_id, err))
            print(f"  [{i:3d}/{len(doc_ids)}] [WARN] {doc_id}: {err}", file=sys.stderr)
        else:
            tag = "[OK]  " if n_units > 0 else "[ZERO]"
            if i % 20 == 0 or n_units > 0 or i == len(doc_ids):
                print(f"  [{i:3d}/{len(doc_ids)}] {tag} {doc_id}: {n_units} units ({elapsed:.1f}s)")

    elapsed = time.time() - overall_t0
    print()
    print("=" * 70)
    print(f"Done in {elapsed:.0f}s")
    print(f"  total units materialized: {n_total_units}")
    print(f"  patents with 0 units:     {n_zero}  (no Examples section / parse fail)")
    print(f"  partial-failure patents:  {len(failures)}")
    if failures:
        print()
        print("First 10 failures:")
        for d, e in failures[:10]:
            print(f"  {d}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
