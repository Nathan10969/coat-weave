"""Build the canonical merge review queue from per-unit proposals."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from coating_kg.config import SETTINGS  # noqa: E402
from coating_kg.pipeline.canonical_merge_queue import (  # noqa: E402
    build_merge_queue,
    save_merge_queue,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units-dir", type=Path, default=SETTINGS.project_root / "data" / "units")
    parser.add_argument(
        "--out",
        type=Path,
        default=SETTINGS.project_root / "data" / "canonical_merge_queue.json",
    )
    args = parser.parse_args()

    queue = build_merge_queue(args.units_dir)
    out_path = save_merge_queue(queue, args.out)
    print(f"canonical merge queue: {len(queue['items'])} items -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
