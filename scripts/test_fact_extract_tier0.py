"""Direct fact_extractor test on the existing p40 (Table 2) unit.

Run from any cwd; paths are computed relative to this script's location.

Windows:
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONPATH = "src"
    cd coat-weave
    & "python" scripts\\test_fact_extract_tier0.py

Linux (sandbox):
    cd /sessions/.../coating_1/coating_kg
    PYTHONPATH=src python3 scripts/test_fact_extract_tier0.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

# repo root = parent of this script's parent (scripts/test_fact_extract_tier0.py)
REPO = Path(__file__).resolve().parent.parent
ENV = REPO / ".env"

# Default unit (Table 2 / p40 / property test). Override via command-line:
#   python scripts\test_fact_extract_tier0.py U_WO2026077939A1_p37_b455
DEFAULT_UNIT = "U_WO2026077939A1_p40_b487"
unit_arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_UNIT
UNIT_DIR = REPO / "data" / "units" / unit_arg
if not UNIT_DIR.exists():
    print(f"ERROR: unit dir not found: {UNIT_DIR}")
    print(f"Available units:")
    for d in sorted((REPO / "data" / "units").glob("U_*")):
        print(f"  {d.name}")
    sys.exit(1)

# load .env
if not ENV.exists():
    print(f"ERROR: .env not found at {ENV}")
    sys.exit(1)

for line in ENV.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k] = v.strip().strip('"').strip("'")

# make sure src/ is on the path even if PYTHONPATH not set
sys.path.insert(0, str(REPO / "src"))

from coating_kg.pipeline.fact_extractor import FactExtractor
from coating_kg.db.models import FigureTableUnit

# Build the unit from saved meta + vlm + matched
meta = json.loads((UNIT_DIR / "meta.json").read_text(encoding="utf-8"))
vlm = json.loads((UNIT_DIR / "vlm_description.json").read_text(encoding="utf-8"))
matched = json.loads((UNIT_DIR / "matched_paragraphs.json").read_text(encoding="utf-8"))

unit = FigureTableUnit(
    unit_id=meta["unit_id"],
    unit_type=meta["unit_type"],
    doc_id=meta["doc_id"],
    page=meta["page"],
    region_id=meta["region_id"],
    caption_footnote_text=meta.get("caption_footnote_text") or "",
    extracted_table_html=meta.get("extracted_table_html") or "",
    image_path=meta.get("image_path"),
    vlm_description=json.dumps(vlm, ensure_ascii=False),
    tagged_entities=[],
)

print(f"Unit: {unit.unit_id}")
print(f"  type: {unit.unit_type}, page {unit.page}, region {unit.region_id}")
print(f"  table HTML: {len(unit.extracted_table_html)} chars")
print(f"  matched paras: {len(matched.get('matches', []))}")
print()
print("Calling FactExtractor on Tier-0 reworked code...")
print("  (Qwen-Plus, max_tokens=8192, unit_context schema)")
print()

t0 = time.time()
extractor = FactExtractor()
result = extractor.extract(unit, matched_paragraphs=matched.get("matches", []))
elapsed = time.time() - t0

print(f"=== RESULT ({elapsed:.1f}s) ===")
print(f"  facts:                 {len(result.facts)}")
print(f"  proposed_canonicals:   {len(result.proposed_canonicals)}")
print(f"  coverage:              {result.coverage}")
print()
cov = result.coverage.get("coverage_pct") or 0
exp = result.coverage.get("expected_data_cells") or 0
got = result.coverage.get("extracted_facts") or 0
print(f"Coverage: expected={exp}, extracted={got}, coverage_pct={cov}%")
if cov >= 80:
    print("  HIGH coverage [PASS] -- Tier 0 fix is working")
elif cov >= 60:
    print("  medium coverage -- may need follow-up")
else:
    print("  LOW coverage [FAIL] -- token truncation still happening")

out = UNIT_DIR / "facts_NEW_TIER0.json"
out.write_text(
    json.dumps(
        {"facts": [f.model_dump(mode="json", exclude_none=True) for f in result.facts]},
        ensure_ascii=False, indent=2,
    ),
    encoding="utf-8",
)
print()
print(f"  -> saved to {out}")

props = Counter(f.property for f in result.facts)
rows = Counter(f.evidence_pointer.row for f in result.facts if f.evidence_pointer)
print()
print("Property distribution in new facts:")
for p, n in props.most_common():
    print(f"  {n:>3}x {p}")
print()
print("Distinct row labels (should include DLT v / G(60deg)v / dG / dDLT now):")
for r, n in rows.most_common():
    print(f"  {n:>3}x {r}")
