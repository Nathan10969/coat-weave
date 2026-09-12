"""Test patent_metadata_extractor on WO2026077939A1.

Usage (Windows PowerShell):
    cd coat-weave
    & "python" scripts\\test_patent_meta.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV = REPO / ".env"

# load .env
if ENV.exists():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v.strip().strip('"').strip("'")

sys.path.insert(0, str(REPO / "src"))

from coating_kg.pipeline.patent_metadata_extractor import (  # noqa: E402
    extract_patent_metadata,
    find_content_list,
    save_patent_metadata,
)


def main() -> int:
    doc_id = "WO2026077939A1"
    mineru_dir = REPO / "data" / "mineru_output"

    cl = find_content_list(mineru_dir, doc_id)
    if cl is None:
        print(f"ERROR: no content_list.json found under {mineru_dir / doc_id}")
        return 1

    print(f"Reading: {cl}")
    print()

    import time
    t0 = time.time()
    meta = extract_patent_metadata(cl, doc_id)
    elapsed = time.time() - t0

    print(f"=== METADATA ({elapsed:.1f}s) ===")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print()

    out_path = save_patent_metadata(REPO, doc_id, meta)
    print(f"Saved to: {out_path}")

    # sanity checks against the truth we already know
    print()
    print("=== Spot checks ===")
    checks = [
        ("publication_number contains '2026/077939'",
         "2026/077939" in (meta.get("publication_number") or "")),
        ("applicant contains 'BASF'",
         "BASF" in (meta.get("applicant") or "")),
        ("applicant_country == 'DE'",
         meta.get("applicant_country") == "DE"),
        ("publication_date == '2026-04-16'",
         meta.get("publication_date") == "2026-04-16"),
        ("filing_date == '2025-10-07'",
         meta.get("filing_date") == "2025-10-07"),
        ("priority_date == '2024-10-08'",
         meta.get("priority_date") == "2024-10-08"),
        ("title contains 'CLEARCOAT'",
         "CLEARCOAT" in (meta.get("title") or "").upper()),
        ("ipc_codes contains 'C09D 175/04'",
         any("C09D 175/04" in c for c in meta.get("ipc_codes") or [])),
        ("at least 1 inventor",
         len(meta.get("inventor") or []) >= 1),
    ]
    passed = 0
    for name, ok in checks:
        mark = "[PASS]" if ok else "[FAIL]"
        print(f"  {mark} {name}")
        if ok:
            passed += 1
    print()
    print(f"Passed {passed}/{len(checks)} sanity checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
