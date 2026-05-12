"""V1.2.5 task #3：抽样 result_value=null 的 fact 并分类。

目的：动手修之前，先看 19% 的 fact 为啥没 result_value。
分类:
  A_empty_cell           cell="" — 非数据行 (e.g. 表头)
  A1_ingredient_name     row 是化学品名 (Acetic Acid, Defoamer 等)
  B_qualitative_range    cell 以 <, >, trace, OK, ~, n.d. 等开头
  C_OCR_garble           cell 含多 token / 不可解析的数字
  D_LLM_missed_numeric   cell 是干净数字但 result_value=None  <- prompt 修复目标
  E_other                没匹中任何启发式
"""
import json
import glob
import os
import random
import re
import collections

random.seed(42)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

nulls = []
total = 0
for fp in sorted(glob.glob(os.path.join(REPO, "data", "units", "U_*", "facts.json"))):
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception:
        continue
    unit = os.path.basename(os.path.dirname(fp))
    for f in d.get("facts", []):
        total += 1
        if f.get("result_value") is None:
            ep = f.get("evidence_pointer", {}) or {}
            nulls.append({
                "unit": unit,
                "row": (ep.get("row") or "")[:32],
                "col": (ep.get("column") or "")[:28],
                "cell": (ep.get("cell") or "")[:28],
                "property": (f.get("property") or "")[:32],
                "rvtext": f.get("result_value_text"),
            })

print(f"Total facts: {total}")
print(f"result_value=null: {len(nulls)} ({100*len(nulls)/total:.0f}%)")
print()


def categorize(n):
    cell = (n.get("cell") or "").strip()
    rvtext = (n.get("rvtext") or "").strip() if n.get("rvtext") else ""
    row = (n.get("row") or "").strip()

    # D: cell 干净数字 — LLM 应该填 result_value
    if cell and re.match(r"^-?\d+(?:[.,]\d+)?(?:\s*[%a-zA-Z]+)?$", cell):
        return "D_LLM_missed_clean_numeric"

    # B: 定性 / 范围
    if rvtext and re.match(r"^(<|>|trace|OK|n\.d\.|—|~|≤|≥)", rvtext, re.I):
        return "B_qualitative_or_range"
    if cell and re.match(r"^(<|>|trace|OK|—|~|≤|≥|n\.d\.|n/a)", cell, re.I):
        return "B_qualitative_or_range"

    # C: OCR 噪声
    if cell and re.search(r"\s\d.*\d|n\.d\.|n/a", cell, re.I):
        return "C_OCR_garble"

    # A1: row 含化学品名
    if row and re.search(
        r"(acid|defoamer|polyol|amine|polymer|resin|silane|isocyanate|oxide|black|white|carbon|titanium|aluminum|toluene|xylene)",
        row,
        re.I,
    ):
        return "A1_ingredient_name_in_row"

    # A: 空 cell
    if not cell:
        return "A_empty_cell"

    return "E_other"


sample = nulls if len(nulls) <= 100 else random.sample(nulls, 100)
counts = collections.Counter(categorize(n) for n in sample)

print("=== Categorization (100 sample) ===")
for k in sorted(counts.keys()):
    pct = 100 * counts[k] / len(sample)
    print(f"  {counts[k]:3d} ({pct:.0f}%)  {k}")
print()

print("=== 4 examples per category ===")
by_cat = collections.defaultdict(list)
for n in sample:
    by_cat[categorize(n)].append(n)
for cat in sorted(by_cat.keys()):
    print(f"--- {cat} ({len(by_cat[cat])}) ---")
    for n in by_cat[cat][:4]:
        print(
            f"  row={n['row']!r:34} col={n['col']!r:30} cell={n['cell']!r:22} rvtext={n['rvtext']!r}"
        )
    print()
