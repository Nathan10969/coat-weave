"""Export per-dimension filter vocabularies from the KG aggregate data dir.

The router-side vocabulary guard (core/routing.py:validate_aggregate_filter_vocab)
needs to know which application_family values actually exist, because the KG
backend matches that dimension by exact lowercase string equality. Re-run this
script whenever the KG data pack under KG_AGGREGATE_DIR is rebuilt.

Usage (on the serving host):
    python3 scripts/export_kg_vocab.py [output_path]

Defaults: reads $KG_AGGREGATE_DIR (or the live A100 data dir), writes
config/kg_filter_vocab.json next to this service.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

KG_DIR = Path(os.environ.get("KG_AGGREGATE_DIR", "/root/coating/embedding/data/kg_286_aggregate"))
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "config" / "kg_filter_vocab.json"


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
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    profiles_path = KG_DIR / "patent_profiles.jsonl"
    if not profiles_path.exists():
        print(f"ERROR: {profiles_path} not found (set KG_AGGREGATE_DIR)", file=sys.stderr)
        return 1

    families: Counter[str] = Counter()
    records = 0
    with profiles_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            records += 1
            # Singular key only — it is the only key the serving side reads
            # (kg_expand_http_service.py merge_patent_record), so a plural-key
            # value would enter the vocabulary yet never match at query time.
            for value in iter_values(record.get("application_family")):
                families[value] += 1

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(profiles_path),
        "records": records,
        "dimensions": {
            "application_family": dict(sorted(families.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out_path}: {len(families)} application_family values from {records} profiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
