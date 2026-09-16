"""One-shot driver for stages 0/1/0.5/2/3 on A100.

Runs the three batch scripts in order; stops on first non-zero exit.
Prefer running each step manually first (especially `01_batch_mineru.py`
on a single PDF) to confirm your MinerU command works.

Usage:
    cd coating_kg/scripts/a100_stages_0_3
    python run_all.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable

STEPS = [
    ("Stage 1 — MinerU layout (6-way GPU)", HERE / "01_batch_mineru.py"),
    ("Stage 0.5 — patent metadata (32-way DashScope)", HERE / "02_batch_metadata.py"),
    ("Stages 2+3 — units (sequential CPU/IO)", HERE / "03_batch_units.py"),
]


def main() -> int:
    overall_t0 = time.time()
    for label, script in STEPS:
        print()
        print("#" * 70)
        print(f"# {label}")
        print(f"# {script.name}")
        print("#" * 70)
        t0 = time.time()
        rc = subprocess.run([PYTHON, str(script)], check=False).returncode
        elapsed = time.time() - t0
        print()
        print(f"  → {script.name} exited {rc} in {elapsed/60:.1f} min")
        if rc != 0:
            print(f"  STOP: {script.name} failed.", file=sys.stderr)
            return rc

    print()
    print("=" * 70)
    print(f"All three stages done in {(time.time() - overall_t0)/60:.1f} min")
    print("=" * 70)
    print()
    print("Next: tomorrow run stages 4-9 via")
    print("  python -m coating_kg ingest <pdf>   (per-PDF)")
    print("or in batch:")
    print("  python scripts/run_pipeline.py @pdfs.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
