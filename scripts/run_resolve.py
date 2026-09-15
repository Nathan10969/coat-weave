"""Apply the offline CanonicalResolver to cached facts.json files and report
hit / miss / reject statistics. No DB needed — reads seed SQL into memory.

Usage:
    python scripts/run_resolve.py WO2026077939A1
    python scripts/run_resolve.py --all       # walk every U_*/ folder
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from coating_kg.config import SETTINGS  # noqa: E402
from coating_kg.pipeline.canonical_resolver import CanonicalResolver  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("resolve")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id", nargs="?", help="doc_id; omit with --all to walk all units")
    ap.add_argument("--all", action="store_true", help="process every cached unit")
    args = ap.parse_args()

    units_root = SETTINGS.project_root / "data" / "units"
    if args.all:
        unit_dirs = [d for d in units_root.iterdir() if d.is_dir()]
    else:
        if not args.doc_id:
            log.error("provide doc_id or --all")
            return 1
        unit_dirs = [d for d in units_root.iterdir()
                     if d.is_dir() and d.name.startswith(f"U_{args.doc_id}_")]
    if not unit_dirs:
        log.error("no unit folders found")
        return 1

    resolver = CanonicalResolver()
    log.info("resolver loaded: %d nodes, %d aliases",
             len(resolver.nodes_by_id), len(resolver.aliases))

    # totals
    total_facts = 0
    passed = 0  # hard rule #3 satisfied
    failed_unresolved: Counter[str] = Counter()  # field name → count of facts that failed it
    failure_examples: dict[str, list[str]] = {}  # field name → up to 3 sample LLM strings
    optional_resolved: Counter[str] = Counter()  # slot → count of facts where slot resolved
    optional_attempted: Counter[str] = Counter()  # slot → count of facts where slot was non-empty

    sample_pass: list[dict] = []
    sample_fail: list[dict] = []

    for d in sorted(unit_dirs):
        facts_path = d / "facts.json"
        if not facts_path.exists():
            continue
        facts = json.loads(facts_path.read_text(encoding="utf-8")).get("facts", [])
        for f in facts:
            total_facts += 1
            ok, unresolved = resolver.validate_fact(f)
            if ok:
                passed += 1
                if len(sample_pass) < 3:
                    sample_pass.append(f)
            else:
                if len(sample_fail) < 3:
                    sample_fail.append(f)
                for fld in unresolved:
                    failed_unresolved[fld] += 1
                    bucket = failure_examples.setdefault(fld, [])
                    if len(bucket) < 5:
                        bucket.append(f.get(fld))

            # optional slot survey
            opt = resolver.resolve_optional_slots(f)
            for slot in ("resin_system", "test_method", "substrate.tested"):
                v_in = f.get(slot.split(".")[0])
                if slot == "substrate.tested":
                    v_in = (f.get("substrate") or {}).get("tested")
                if v_in:
                    optional_attempted[slot] += 1
                    if opt[slot] is not None:
                        optional_resolved[slot] += 1
            for slot, ids in (("additives", opt["additives"]),
                              ("process.step", opt["process.step"])):
                attempted = sum(1 for x in (ids or []) if x is not None) + sum(
                    1 for x in (ids or []) if x is None
                )
                # we want: attempted = how many entries exist; resolved = non-None
                attempted = len(ids or [])
                resolved = sum(1 for x in (ids or []) if x is not None)
                optional_attempted[slot] += attempted
                optional_resolved[slot] += resolved

    print()
    print(f"=== facts processed: {total_facts} ===")
    print(f"  passed §7.5 hard rule #3 (application + property both resolve) : "
          f"{passed} / {total_facts}  ({100*passed/total_facts if total_facts else 0:.0f}%)")
    print(f"  rejected                                                       : "
          f"{total_facts - passed}")
    print()
    print("Required-field rejection breakdown:")
    for fld, n in failed_unresolved.most_common():
        print(f"  {fld:<15} {n:>4} facts failed")
        for ex in failure_examples[fld][:5]:
            print(f"     example LLM string: {ex!r}")
    print()
    print("Optional slot resolution rate (when slot was non-empty):")
    for slot in ("resin_system", "test_method", "substrate.tested", "additives", "process.step"):
        a = optional_attempted[slot]
        r = optional_resolved[slot]
        pct = (100 * r / a) if a else 0
        print(f"  {slot:<22} {r}/{a}  ({pct:.0f}%)")
    print()
    if sample_pass:
        print("Sample PASS fact:")
        f = sample_pass[0]
        print(f"  application = {f.get('application')!r:<45}  property = {f.get('property')!r}")
    if sample_fail:
        print("Sample FAIL fact:")
        f = sample_fail[0]
        print(f"  application = {f.get('application')!r:<45}  property = {f.get('property')!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
