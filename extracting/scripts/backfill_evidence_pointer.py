"""V1.2.5 backfill：让现有 facts.json 都带上 evidence_pointer.unit_id。

V1.2.5 之前抽的老 fact 没有 evidence_pointer.unit_id。它们的 unit_id
隐含在 fact_id 里 (格式 F_<unit_id>_<seq:03d>)，反向解出来灌进去。
bbox 这里没法 backfill — 需要重跑 unit_extractor + V1.2.5 _extract_bbox，
也就是从 MinerU 输出重 ingest。V1.2.5 后的新 ingest 自动填 bbox；
老 ingest 在下次 re-ingest 前 bbox=None。

跑 (Windows):
    cd <repository-root>\\extracting
    & "python" scripts\\backfill_evidence_pointer.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UNIT_DIR = REPO / "data" / "units"

# fact_id 格式: F_<unit_id>_<seq:03d>
# unit_id 格式: U_<doc_id>_p<page>_b<block_idx>
# 所以 fact_id = F_U_WO123_p38_b455_001 → unit_id = U_WO123_p38_b455
_FACT_ID_RE = re.compile(r"^F_(U_.+?)_\d{3}$")


def main() -> int:
    if not UNIT_DIR.exists():
        print(f"ERROR: {UNIT_DIR} does not exist", file=sys.stderr)
        return 1

    n_files = 0
    n_files_changed = 0
    n_facts_total = 0
    n_facts_backfilled = 0
    n_facts_already_had_id = 0
    n_facts_unparseable = 0

    for facts_file in sorted(UNIT_DIR.glob("U_*/facts.json")):
        n_files += 1
        try:
            text = facts_file.read_text(encoding="utf-8")
            data = json.loads(text)
        except Exception as exc:
            # 试 raw_decode 跳过尾部噪声
            try:
                data, _ = json.JSONDecoder().raw_decode(text)
            except Exception:
                print(f"  SKIP (cannot parse): {facts_file}", file=sys.stderr)
                continue

        changed = False
        for fact in data.get("facts", []) or []:
            n_facts_total += 1
            fid = fact.get("fact_id", "")
            ep = fact.get("evidence_pointer") or {}
            if not isinstance(ep, dict):
                continue
            if ep.get("unit_id"):
                n_facts_already_had_id += 1
                continue
            m = _FACT_ID_RE.match(fid)
            if not m:
                n_facts_unparseable += 1
                continue
            ep["unit_id"] = m.group(1)
            fact["evidence_pointer"] = ep
            n_facts_backfilled += 1
            changed = True

        if changed:
            facts_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            n_files_changed += 1

    print()
    print("=" * 60)
    print("V1.2.5 evidence_pointer.unit_id backfill")
    print("=" * 60)
    print(f"  files scanned:          {n_files}")
    print(f"  files changed:          {n_files_changed}")
    print(f"  facts total:            {n_facts_total}")
    print(f"  facts backfilled:       {n_facts_backfilled}")
    print(f"  facts already had id:   {n_facts_already_had_id}")
    print(f"  facts unparseable id:   {n_facts_unparseable}")
    print()
    print("NOTE: bbox NOT backfilled — requires re-ingest. New ingests will")
    print("      populate bbox automatically via _extract_bbox helper.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
