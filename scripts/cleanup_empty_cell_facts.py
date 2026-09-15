"""V1.2.5 task #3 — 一次性清理 V1.2.5 前的空 cell facts。

老的 facts.json (V1.2.5 空 cell 守门之前抽的) 包含 cell="" 且
result_value_text="" 且 result_value=None 的 fact —— 来自 LLM 误把
非数据行 (表头、分隔行) emit 成 fact。它们让 build_coatings_csv 的
Property cell 出现 "PROP_X@left:" 这种空尾巴冒号，污染 CSV。

本脚本就地删除它们。新 ingest (V1.2.5 后) 不再产生这种 — fact_extractor
会跳过并 logger.info 报。

跑:
    cd C:\\path\\to\\coat-weave
    python scripts\\cleanup_empty_cell_facts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UNIT_DIR = REPO / "data" / "units"


def is_noise_fact(fact: dict) -> bool:
    """fact 是 noise 的判定：cell 空 且 没有定性 result_value_text
    (定性测量值是合法的，要保留)。"""
    ep = fact.get("evidence_pointer") or {}
    cell = (ep.get("cell") or "").strip() if isinstance(ep, dict) else ""
    rv_text = (fact.get("result_value_text") or "").strip()
    rv = fact.get("result_value")
    return not cell and not rv_text and rv is None


def main() -> int:
    if not UNIT_DIR.exists():
        print(f"ERROR: {UNIT_DIR} does not exist", file=sys.stderr)
        return 1

    n_files = 0
    n_files_changed = 0
    n_facts_total = 0
    n_facts_removed = 0
    samples_removed: list[dict] = []

    for facts_file in sorted(UNIT_DIR.glob("U_*/facts.json")):
        n_files += 1
        try:
            text = facts_file.read_text(encoding="utf-8")
            data = json.loads(text)
        except Exception:
            try:
                data, _ = json.JSONDecoder().raw_decode(text)
            except Exception:
                print(f"  SKIP (cannot parse): {facts_file}", file=sys.stderr)
                continue

        original_facts = data.get("facts", []) or []
        n_facts_total += len(original_facts)

        cleaned = [f for f in original_facts if not is_noise_fact(f)]
        removed = [f for f in original_facts if is_noise_fact(f)]

        if removed:
            n_facts_removed += len(removed)
            n_files_changed += 1
            data["facts"] = cleaned
            facts_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 留几条 sample 给人审
            for r in removed[:2]:
                if len(samples_removed) < 8:
                    ep = r.get("evidence_pointer") or {}
                    samples_removed.append({
                        "fact_id": r.get("fact_id"),
                        "property": r.get("property"),
                        "row": (ep.get("row") or "")[:30],
                        "column": (ep.get("column") or "")[:30],
                    })

    print()
    print("=" * 60)
    print("V1.2.5 task #3 — empty-cell fact cleanup")
    print("=" * 60)
    print(f"  files scanned:      {n_files}")
    print(f"  files changed:      {n_files_changed}")
    print(f"  facts total before: {n_facts_total}")
    print(f"  facts removed:      {n_facts_removed} ({100*n_facts_removed/n_facts_total:.1f}%)")
    print(f"  facts kept:         {n_facts_total - n_facts_removed}")
    print()
    if samples_removed:
        print("Removed samples (showing first 8):")
        for s in samples_removed:
            print(f"  {s['fact_id']}  prop={s['property']}  row={s['row']!r}  col={s['column']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
