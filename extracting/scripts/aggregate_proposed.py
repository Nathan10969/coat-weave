"""Aggregate per-unit proposed_canonicals.json into one review file.

Walks every ``data/units/U_*/proposed_canonicals.json``, dedups by
``(kind, proposed_id)``, and writes
``data/proposed_canonicals_aggregated.json`` for human curation.

The dedup keeps the highest-confidence variant; lower-confidence
duplicates are listed under ``aliases`` so reviewer sees who else
proposed the same ID and from where.

Usage:
    python scripts/aggregate_proposed.py
    python scripts/aggregate_proposed.py --filter-kind MAT
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from coating_kg.config import SETTINGS  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aggregate")


_CONF_RANK = {"high": 3, "medium": 2, "low": 1, "": 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter-kind", help="Restrict to one kind (MAT/PROP/APP/SUB/PROC/TEST)")
    ap.add_argument(
        "--out", default=str(SETTINGS.project_root / "data" / "proposed_canonicals_aggregated.json"),
    )
    args = ap.parse_args()

    units_root = SETTINGS.project_root / "data" / "units"
    if not units_root.exists():
        log.error("no data/units directory at %s", units_root)
        return 1

    # (kind, proposed_id) -> aggregated entry
    grouped: dict[tuple[str, str], dict] = {}

    n_units_with_props = 0
    n_proposals = 0
    for d in sorted(units_root.iterdir()):
        if not d.is_dir():
            continue
        path = d / "proposed_canonicals.json"
        if not path.exists():
            continue
        n_units_with_props += 1
        data = json.loads(path.read_text(encoding="utf-8"))
        for prop in data.get("proposed_canonicals", []):
            kind = prop.get("kind", "")
            pid = prop.get("proposed_id", "")
            if not kind or not pid:
                continue
            if args.filter_kind and kind != args.filter_kind:
                continue
            n_proposals += 1
            key = (kind, pid)
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = {
                    "kind": kind,
                    "sub_type": prop.get("sub_type", ""),
                    "proposed_id": pid,
                    "canonical_name": prop.get("canonical_name", ""),
                    "confidence": prop.get("confidence", ""),
                    "occurrences": 1,
                    "sources": [
                        {"unit_id": d.name, "source_text": prop.get("source_text", "")}
                    ],
                }
            else:
                existing["occurrences"] += 1
                existing["sources"].append(
                    {"unit_id": d.name, "source_text": prop.get("source_text", "")}
                )
                # keep the higher-confidence canonical_name / sub_type
                if _CONF_RANK.get(prop.get("confidence", ""), 0) > _CONF_RANK.get(existing["confidence"], 0):
                    existing["confidence"] = prop.get("confidence", "")
                    existing["canonical_name"] = prop.get("canonical_name", "") or existing["canonical_name"]

    # Order: by kind, then by occurrences desc
    out_list = sorted(grouped.values(), key=lambda x: (x["kind"], -x["occurrences"], x["proposed_id"]))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"proposed_canonicals": out_list}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Summary table
    print()
    print(f"Walked {n_units_with_props} unit folder(s) with proposals; total raw proposals: {n_proposals}")
    print(f"Deduped to {len(out_list)} unique (kind, proposed_id):")
    print()
    by_kind: dict[str, list] = defaultdict(list)
    for x in out_list:
        by_kind[x["kind"]].append(x)
    for kind in sorted(by_kind):
        rows = by_kind[kind]
        print(f"  [{kind}] {len(rows)} unique")
        for r in rows[:10]:
            sub = f" ({r['sub_type']})" if r['sub_type'] else ""
            print(f"    × {r['occurrences']:>2}  conf={r['confidence']:<6} {r['proposed_id']}{sub} — {r['canonical_name']}")
        if len(rows) > 10:
            print(f"    ... and {len(rows)-10} more")
        print()
    print(f"Written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
