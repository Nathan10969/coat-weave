"""Remap patent_profiles.jsonl application_family from 454 free-text labels
to the closed family taxonomy (config/application_family_taxonomy.json).

For each profile record:
  - application_family_raw  <- the original value(s), preserved verbatim
    (drill-down queries and future re-taxonomy keep working from raw)
  - application_family      <- unique ordered list of canonical family keys
    for raw values whose family kind == "application"; axis-mismatch values
    (function words, coating-form words, malformed labels) emit NO family.

Idempotent: a record that already has application_family_raw is re-derived
from raw, so re-running after a taxonomy update is safe.

Usage (on the serving host):
    python3 scripts/remap_application_family.py          # dry-run, prints stats
    python3 scripts/remap_application_family.py --apply  # backup + rewrite

Restart coating-kg-tools afterwards (it loads the file at startup), then
re-run scripts/export_kg_vocab.py so the vocabulary guard sees family keys.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

KG_DIR = Path(os.environ.get("KG_AGGREGATE_DIR", "/root/coating/embedding/data/kg_286_aggregate"))
TAXONOMY_PATH = Path(__file__).resolve().parent / "application_family_taxonomy.json"


def iter_values(raw: object) -> list[str]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("value") or item.get("canonical_id") or ""
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def main() -> int:
    apply = "--apply" in sys.argv
    taxonomy = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    value_to_family: dict[str, str] = {}
    application_keys: set[str] = set()
    for family in taxonomy["families"]:
        if family["kind"] == "application":
            application_keys.add(family["key"])
        for value in family["values"]:
            value_to_family[value] = family["key"] if family["kind"] == "application" else ""

    profiles_path = KG_DIR / "patent_profiles.jsonl"
    records = []
    n_remapped = n_emptied = 0
    unknown: set[str] = set()
    with profiles_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            raw = record.get("application_family_raw", record.get("application_family"))
            raw_values = iter_values(raw)
            families: list[str] = []
            for value in raw_values:
                family = value_to_family.get(value)
                if family is None:
                    unknown.add(value)
                elif family and family not in families:
                    families.append(family)
            record["application_family_raw"] = raw
            record["application_family"] = families
            if raw_values and not families:
                n_emptied += 1
            if families:
                n_remapped += 1
            records.append(record)

    print(f"profiles: {len(records)}, with canonical family: {n_remapped}, "
          f"raw-only (axis-mismatch values only): {n_emptied}")
    if unknown:
        print(f"WARNING: {len(unknown)} raw values not in taxonomy (left raw-only): {sorted(unknown)[:10]}")

    from collections import Counter
    coverage: Counter[str] = Counter()
    for record in records:
        for family in record["application_family"]:
            coverage[family] += 1
    print("per-family distinct-patent coverage:")
    for family, count in coverage.most_common():
        print(f"  {count:4d}  {family}")

    if not apply:
        print("\nDRY-RUN (no file written). Re-run with --apply to rewrite.")
        return 0

    backup = profiles_path.with_suffix(f".jsonl.bak_{time.strftime('%Y%m%d_%H%M%S')}")
    profiles_path.rename(backup)
    with profiles_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"\nAPPLIED. backup: {backup}")
    print("next: systemctl restart coating-kg-tools && python3 scripts/export_kg_vocab.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
