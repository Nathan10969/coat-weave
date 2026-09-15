"""Build explicit KG JSONL exports from current pipeline artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from coating_kg.config import SETTINGS  # noqa: E402
from coating_kg.pipeline.kg_export import build_kg_export  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=SETTINGS.project_root)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    summary = build_kg_export(args.project_root, out_dir=args.out_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
