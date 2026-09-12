"""Stage 9：把 per-unit facts.json 透视成宽表 CSV (一行一个 Example)。

读:
  data/units/U_*/facts.json
  data/units/U_*/proposed_canonicals.json   (proposed ID 的 sub_type)
  data/units/U_*/coverage.json              (unit 级抽取质量)
  data/patents/<doc_id>__patent_meta.json   (Stage 0.5 — applicant/date；可选)

写:
  output/coatings_wide.csv

布局：option A (sub_type 作子列，多值 cell 用 `; ` 分隔)。

跑:
    cd coat-weave
    & "python" scripts\\build_coatings_csv.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from coating_kg.pipeline.canonical_resolver import SUB_TYPES  # noqa: E402


# ---------------------------------------------------------------------------
# 配置：列结构 (option A)
# ---------------------------------------------------------------------------
# Material sub_types (父列名 "Material")
MATERIAL_SUB_TYPES = [
    "Resin / binder",
    "Crosslinker / Curing agent",
    "Pigment",
    "Filler",
    "Additive",
    "Solvent",
    "Monomer",
]

# Property sub_types (父列名 "Property")
PROPERTY_SUB_TYPES = [
    "Appearance",
    "Mechanical",
    "Surface",
    "Aging / Corrosion / Durability",
    "Chemical resistance",
    "Thermal",
    "Environmental / Regulatory",
    "Application properties",
    "Formulation",
]

# Application 分类 (单值)
APP_CATEGORIES = {"Automotive", "Industrial protective", "Specialty", "Architectural"}
SUB_CATEGORIES = {"Metal", "Plastic", "Mineral"}

# 涂料相关 IPC 前缀。专利的 (51) IPC 列表里如果一个都没有，
# 视为非涂料 BASF imposter (CO2 capture / 粘合剂 / 洗涤剂等)，CSV 里跳过。
COATING_IPC_PREFIXES = (
    "C09D",  # ★ 涂料组合物（主）
    "C09K",  # 助剂 / 添加剂
    "C08G",  # 聚合物（常作 resin systems）
    "C08K",  # 无机 / 非高分子物质
    "C08L",  # 高分子化合物组合物
    "B05D",  # 涂层工艺（喷涂、浸涂、辊涂）
    "B05B",  # 喷涂设备
    "C09J",  # 粘合剂（边角 — 跟涂料有重叠）
    "C25D",  # 电沉积（CED 是涂层工艺）
)


def is_coating_patent(meta: dict | None) -> bool:
    """patent_meta 的 IPC 含至少一个涂料前缀就 True。
    None / 空 → True（无信号时不过滤，Stage 0.5 可能还没跑）。
    """
    if meta is None:
        return True
    ipcs = meta.get("ipc_codes") or []
    if not ipcs:
        return True
    for code in ipcs:
        for prefix in COATING_IPC_PREFIXES:
            if code.strip().startswith(prefix):
                return True
    return False

# 行如何识别 Example。每个 pattern 是 (regex, mapper) 对：mapper 从匹配组拼出
# 规一化的 "C\d" / "E\d" / "I\d" id。**顺序敏感** — 更具体的 pattern 必须在前
# (例如 "Comparative Example" 必须在裸 "Example" 前测试)。
#
# 规一化输出约定:
#   C\d  = comparative example (negative polarity)
#   E\d  = inventive/working example (positive polarity)
#   I\d  = inventive example (positive polarity, BASF 新风格)
_EXAMPLE_ID_PATTERN_RULES: list[tuple[re.Pattern[str], "callable"]] = [
    # 1. BASF 新风格：裸 "C1" / "I1" / "I1B1" (复合，取前缀)
    (re.compile(r"\b([CI]\d+)(?:[A-Z]\d+)?\b"),
     lambda m: m.group(1)),
    # 2. OCR "usedforC1" / "usedfor11" — 大写 I 被读成数字 1
    (re.compile(r"used\s*for\s*([CI]?\d+)", re.I),
     lambda m: m.group(1) if m.group(1)[0] in "CI" else f"I{m.group(1)[1:]}" if m.group(1).startswith("1") else m.group(1)),
    # 3. 带空格 "C 1" / "I 2"
    (re.compile(r"\b([CI])\s*(\d+)\b"),
     lambda m: f"{m.group(1)}{m.group(2)}"),
    # 4. 老 BASF "Comparative Example N" — V1.2.3 加 (+其他前缀)
    (re.compile(r"\bComp(?:arative|\.)?\s*Ex(?:ample|\.)?\s*(\d+|[A-Z])\b", re.I),
     lambda m: f"C{m.group(1)}"),
    # 5. 老 positive "Example 1" / "Example A"
    (re.compile(r"\bExample\s+(\d+|[A-Z])\b", re.I),
     lambda m: f"E{m.group(1)}"),
    # 6. 老 positive 简写 "Ex. 1" / "Ex 2"
    (re.compile(r"\bEx\.?\s+(\d+|[A-Z])\b", re.I),
     lambda m: f"E{m.group(1)}"),
    # 7. 德语 "Vergleichsbeispiel 1" (comparative) vs "Beispiel 1"
    (re.compile(r"\bVergleichsbeispiel\s+(\d+|[A-Z])\b", re.I),
     lambda m: f"C{m.group(1)}"),
    (re.compile(r"\bBeispiel\s+(\d+|[A-Z])\b", re.I),
     lambda m: f"E{m.group(1)}"),
    # 8. 中文：对比例 vs 实施例
    (re.compile(r"对比例\s*(\d+|[A-Z])"),
     lambda m: f"C{m.group(1)}"),
    (re.compile(r"实施例\s*(\d+|[A-Z])"),
     lambda m: f"E{m.group(1)}"),
    # 9. 裸 "Comparative" 无编号（罕见，老 PDF 见过）：默认映 "C1"。
    #    一篇里多个无编号 comparative 会撞，V1 demo 接受。
    (re.compile(r"^\s*Comparative\s*$", re.I),
     lambda m: "C1"),
    # 10. 代码前缀的 inventive/comparative ("CC-A1B1 (inventive)" /
    #     "PC-B3 (comparative)" / "BC-X2A1 (Comparative Example)")。
    #     代码前缀是产品线 (CC=clearcoat, PC=primer, BC=basecoat 等)；
    #     取破折号后段 + 括号里的极性标记。
    (re.compile(r"\b(?:CC|CL|PC|BC|TC|CD)[-_]?([A-Z]?\d+[A-Z]?\d*)\s*\(\s*invent",
                re.I),
     lambda m: f"E_{m.group(1)}"),
    (re.compile(r"\b(?:CC|CL|PC|BC|TC|CD)[-_]?([A-Z]?\d+[A-Z]?\d*)\s*\(\s*comp",
                re.I),
     lambda m: f"C_{m.group(1)}"),
]

# 裸数字 fallback (置信度最低) — 单独应用，因为需要 VLM 确认这是 example 表，
# 不是列是 time points / positions / page numbers 那种表。
_BARE_DIGIT_RE = re.compile(r"^\s*(\d+)\s*$")
_VLM_EXAMPLE_KEYWORDS = ("example", "实施例", "beispiel", "ejemplo", "ejemplos")

# OCR 噪声：BASF 上下文里 "11" 实际是 "I1" (有 C1, C2, ..., I1, I2)；
# 大写 I 被识别成数字 1。
_OCR_FIXES: dict[str, str] = {
    "11": "I1", "12": "I2", "13": "I3",
    "l1": "I1", "l2": "I2", "l3": "I3",
    "Cl": "C1", "Cl1": "C1",
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def extract_example_id(text: str) -> str | None:
    """从 row / column 字符串里抽 example_id (``C1`` / ``I1`` / ``E1``)。

    返回规一化的 prefix id (``C<n>`` comparative, ``I<n>`` / ``E<n>`` inventive)
    供 polarity-fallback 使用。

    支持的输入示例:
        "Amount[wt.-%] usedforC1"                                -> C1
        "Amount[wt.-%] usedfor11"                                -> I1  (OCR)
        "Substrate with C1B1 as clearcoat layer (comparative)"   -> C1
        "Example 1"                                               -> E1  (老风格)
        "Comparative Example 2"                                   -> C2
        "Comp. Ex. 3"                                             -> C3
        "Beispiel 4"                                              -> E4  (德语)
        "Vergleichsbeispiel 5"                                    -> C5
        "实施例 6"                                                  -> E6  (中文)
        "对比例 7"                                                  -> C7
    """
    if not text:
        return None
    for pat, mapper in _EXAMPLE_ID_PATTERN_RULES:
        m = pat.search(text)
        if not m:
            continue
        try:
            raw = mapper(m)
        except Exception:
            continue
        if not raw:
            continue
        # OCR 修复 (例如 "11" → "I1")，仅对 BASF 大写 I 误读情况
        return _OCR_FIXES.get(raw, raw)
    return None


def example_id_for_fact(
    fact: dict,
    vlm_description: dict | None = None,
    caption: str | None = None,
) -> str | None:
    """example_id 多级解析 (V1.2.3 → V1.2.4):

      0.   ★ V1.2.4：LLM 在 fact["example_id"] 出字面 sample id
           (e.g. "E1", "I1", "A1B1", "5"); polarity 单独在 polarity_hint 里。
           Level 0 对 C/E/I 前缀的 id 直接返回；裸 id 根据 polarity_hint
           加 C/E 前缀拼成 grouping key。老 V1.2.3 fact 没 example_id 字段，
           落到 Level 1 regex。
      1.   row/column 上跑硬 regex (10+ 命名模式：Example 1, C1, I1B1,
           Vergleichsbeispiel 2, 实施例 3, 等)。
      1.5  caption fallback — 整个 unit 可能属于 caption 里的 Example
           (e.g. "Neutralization Ladder Example 12")，row/column 不直接
           带 id 时用 caption 兜底。救阶梯 / 扫描表 (一行是 example 内的
           子步骤，不是独立 example_id)。
      2.   裸数字 fallback (e.g. row="3")，需 VLM 确认是 example 表 —
           避免把 "Hour 2" / "Position 3" 这种列误认成 example_id。
      3.   None — 上层走 polarity_hint → polarity_map 的 fallback。
    """
    # Level 0 (V1.2.4): 信任 LLM 输出的字面 example_id。
    # 新设计：LLM 出裸 sample id ("1" / "A1B1" / "I1")。polarity 单源在
    # polarity_hint。Stage 9 用极性 + id 的组合 key 分组，避免不同极性的
    # 两个 "1" 行折成一行。
    llm_ex = fact.get("example_id")
    if isinstance(llm_ex, str) and llm_ex.strip():
        raw = llm_ex.strip()
        # 已有 C/E/I 前缀 → 直接用 (兼容 regex 抽出的 "Example 1"→"E1"
        # 和 BASF "C1" / "I1" 风格)。
        if raw and raw[0].upper() in ("C", "E", "I"):
            return raw
        # 裸 id ("1" / "A1B1" / "5") — 跟 polarity 组合做 grouping key
        pol = fact.get("polarity_hint")
        if pol == "negative":
            return f"C{raw}"
        if pol == "positive":
            return f"E{raw}"
        return raw  # polarity unknown — 留裸字面，可能撞行

    ep = fact.get("evidence_pointer") or {}
    column = ep.get("column", "") or ""
    row = ep.get("row", "") or ""

    # Level 1: row/column 上跑硬 regex
    direct = extract_example_id(column) or extract_example_id(row)
    if direct:
        return direct

    # Level 1.5: caption fallback — 整个 unit 可能属 caption 里的 Example
    # ("Neutralization Ladder Example 12")。row/column 不直接带 id 时用。
    if caption:
        cap_id = extract_example_id(caption)
        if cap_id:
            return cap_id

    # Level 2: 裸数字 + VLM 上下文
    if vlm_description is not None:
        vlm_text = " ".join(
            str(vlm_description.get(k) or "")
            for k in ("description", "table_subject")
        ).lower()
        is_example_table = any(kw in vlm_text for kw in _VLM_EXAMPLE_KEYWORDS)
        if is_example_table:
            for txt in (column, row):
                m = _BARE_DIGIT_RE.match(txt)
                if m:
                    return f"E{m.group(1)}"

    return None


def _polarity_from_example_id(ex_id: str) -> str | None:
    """BASF / 涂料专利约定：C 前缀 = comparative (negative)，
    I/E 前缀 = inventive (positive)。当 polarity_hint 是 "unknown" 但
    example_id 已知时作 fallback 用。"""
    if not ex_id:
        return None
    head = ex_id[0].upper()
    if head == "C":
        return "negative"
    if head in ("I", "E"):
        return "positive"
    return None


def build_polarity_map(grouped_with_ids: dict) -> dict[str, dict[str, str]]:
    """每篇 doc 学 polarity → example_id 映射。

    两个来源（按序）:
      1. polarity_hint 显式 "positive" / "negative" 的 fact。
      2. polarity_hint 是 "unknown" 但 example_id 有约定前缀 (C* / I* / E*) 的，
         从前缀推 polarity。

    返回 {doc_id: {"positive": "I1", "negative": "C1"}}。
    歧义情况（同一 doc 同极性多个不同 example_id，如 C1/C2 都 negative）
    保留第一个见到的 — fallback 仍然有用，只在边角损失精度。
    """
    learned: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for (doc_id, ex_id), facts in grouped_with_ids.items():
        for f in facts:
            pol = f.get("polarity_hint")
            if pol not in ("positive", "negative"):
                pol = _polarity_from_example_id(ex_id)
            if pol in ("positive", "negative"):
                learned[doc_id][pol].add(ex_id)

    out: dict[str, dict[str, str]] = {}
    for doc_id, by_pol in learned.items():
        # 多 example_id 共享同极性时（C1/C2 都 negative），取字典序最小的
        # 保证确定性。
        out[doc_id] = {
            pol: sorted(ids)[0] for pol, ids in by_pol.items() if ids
        }
    return out


def short_id(canonical_id: str) -> str:
    """剥 TYPE_ 前缀和尾部 _generic，给 cell 用人类可读名。

    MAT_PAC1_acrylic_polyol_dispersion -> PAC1_acrylic_polyol_dispersion
    MAT_defoamer_generic               -> defoamer
    PROP_gloss_60deg                   -> gloss_60deg
    """
    if not canonical_id:
        return ""
    out = re.sub(r"^[A-Z]+_", "", canonical_id)
    out = re.sub(r"_generic$", "", out)
    return out


def build_subtype_map(units_dir: Path) -> dict[str, str]:
    """合并 seed SUB_TYPES + 所有 unit 提议的 sub_types。

    proposed ID 不在 seed dict 里，没这个就没法把
    ``MAT_PAC1_acrylic_polyol_dispersion`` 归到 ``Resin / binder``。
    """
    merged: dict[str, str] = dict(SUB_TYPES)
    for prop_file in units_dir.glob("U_*/proposed_canonicals.json"):
        try:
            data = json.loads(prop_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for prop in data.get("proposed_canonicals", []) or []:
            cid = prop.get("proposed_id")
            sub = prop.get("sub_type")
            if cid and sub:
                merged.setdefault(cid, sub)
    return merged


def app_category(canonical_id: str) -> str:
    """APP_automotive_oem_clearcoat -> 'Automotive'，否则回退到 sub_type 或 'unknown'。"""
    if not canonical_id:
        return "unknown"
    sub = SUB_TYPES.get(canonical_id, "")
    if sub in APP_CATEGORIES:
        return sub
    return "unknown"


def sub_category(canonical_id: str) -> str:
    """SUB_steel_panel -> 'Metal'。"""
    if not canonical_id:
        return "unknown"
    sub = SUB_TYPES.get(canonical_id, "")
    if sub in SUB_CATEGORIES:
        return sub
    return "unknown"


def fmt_num(v) -> str:
    """整数 float 去掉尾部 .0：21.0 -> '21'，0.05 -> '0.05'。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# ---------------------------------------------------------------------------
# 行构建
# ---------------------------------------------------------------------------
def build_row(
    doc_id: str,
    example_id: str,
    facts: list[dict],
    subtype_map: dict[str, str],
    coverage_pct_min: float | None,
    patent_meta: dict | None,
) -> dict[str, str]:
    """把同 (doc_id, example_id) 的所有 facts 拼成一行 CSV。"""

    # --- Polarity (取第一个非 unknown 的投票) ---
    polarities = [f.get("polarity_hint") for f in facts]
    polarity = next((p for p in polarities if p in {"positive", "negative"}), "unknown")

    # V1.2.4 sanity check: example_id 前缀应跟 polarity 一致。
    # 如 LLM 出 "I1" 但 polarity_hint=negative (或 "C2" 但 positive)，
    # 是内部矛盾，warn 出来。
    if example_id and example_id[:1] in ("C", "E", "I"):
        head = example_id[0].upper()
        expected = "negative" if head == "C" else "positive"
        if polarity in ("positive", "negative") and polarity != expected:
            import logging
            logging.getLogger(__name__).warning(
                "polarity-prefix conflict on %s/%s: prefix says %s but polarity_hint=%s",
                doc_id, example_id, expected, polarity,
            )

    # --- Material 列：按 sub_type 分桶 ingredient facts ---
    # 一条 ingredient fact 即 property == PROP_composition_weight_percent
    # 且有单个 resin_system / additives 项，cell 值是 canonical_id:wt%。
    material_buckets: dict[str, list[str]] = defaultdict(list)
    for f in facts:
        if f.get("property") != "PROP_composition_weight_percent":
            continue
        wt_pct = fmt_num(f.get("result_value"))
        # 两种槽位可承载 ingredient: resin_system 或 additives[]
        candidates: list[str] = []
        rs = f.get("resin_system")
        if rs:
            candidates.append(rs)
        for ad in f.get("additives") or []:
            candidates.append(ad)
        for cid in candidates:
            sub = subtype_map.get(cid, "Additive")  # 未知默认进 Additive 桶
            material_buckets[sub].append(f"{short_id(cid)}:{wt_pct}")

    # --- Property 列：按 sub_type 分桶 measurement facts ---
    property_buckets: dict[str, list[str]] = defaultdict(list)
    for f in facts:
        prop = f.get("property") or ""
        if prop == "PROP_composition_weight_percent":
            continue  # composition 是 Material 范畴，不是 measurement
        sub = subtype_map.get(prop, "")
        if not sub:
            continue
        ep = f.get("evidence_pointer") or {}
        col = ep.get("column", "")
        # V1.2.5 task #3 闭环：定性 fact 的 result_value=None 但
        # result_value_text 有合法值 ("Pass"/"Hazy"/"5B" 等)。
        # 回退到 text 让 CSV 显示真实测量，不是空尾巴冒号。
        result = fmt_num(f.get("result_value"))
        if not result:
            result = (f.get("result_value_text") or "").strip()
        # 把位置信息 (left/central/right 或 h/v) 编进 cell 字面
        loc = col.replace("Measurement position", "").strip(" ,")
        prop_short = short_id(prop)
        cell = f"{prop_short}@{loc}:{result}" if loc else f"{prop_short}:{result}"
        property_buckets[sub].append(cell)

    # --- TestMethod (去重聚合) ---
    test_methods: list[str] = []
    for f in facts:
        tm = f.get("test_method")
        if tm and tm not in test_methods:
            test_methods.append(tm)

    # --- Application / Substrate (单值分类，跨 facts 投票) ---
    app_votes: list[str] = []
    sub_votes: list[str] = []
    for f in facts:
        a = f.get("application")
        if a:
            app_votes.append(app_category(a))
        s = (f.get("substrate") or {}).get("tested") if isinstance(f.get("substrate"), dict) else None
        if isinstance(s, list):
            for item in s:
                if item:
                    sub_votes.append(sub_category(item))
        elif s:
            sub_votes.append(sub_category(s))

    application = max(set(app_votes), key=app_votes.count) if app_votes else "unknown"
    substrate = max(set(sub_votes), key=sub_votes.count) if sub_votes else "unknown"

    # --- Process: 收集去重的 step+condition 字符串 ---
    process_strs: list[str] = []
    for f in facts:
        steps = f.get("process") or []
        if not isinstance(steps, list):
            continue
        for st in steps:
            if not isinstance(st, dict):
                continue
            step_id = short_id(st.get("step", ""))
            cond = st.get("condition", "")
            entry = f"{step_id} ({cond})" if cond else step_id
            if entry and entry not in process_strs:
                process_strs.append(entry)

    # --- Evidence: 去重 (page, region) 对 ---
    evidence_keys: list[str] = []
    for f in facts:
        ep = f.get("evidence_pointer") or {}
        page = ep.get("page")
        region_id = ep.get("region_id", "")
        key = f"p{page} {region_id}".strip()
        if key and key not in evidence_keys:
            evidence_keys.append(key)

    # --- fact_ids: 逗号列表，可追溯 ---
    fact_ids = [f.get("fact_id") for f in facts if f.get("fact_id")]

    # --- patent metadata (Stage 0.5 — 可能缺失) ---
    pm = patent_meta or {}

    row = {
        "patent_id": doc_id,
        "example_id": example_id,
        "polarity": polarity,
    }
    for sub in MATERIAL_SUB_TYPES:
        row[f"Material:{sub}"] = "; ".join(material_buckets.get(sub, []))
    for sub in PROPERTY_SUB_TYPES:
        row[f"Property:{sub}"] = "; ".join(property_buckets.get(sub, []))
    row["TestMethod"] = "; ".join(test_methods)
    row["Application"] = application
    row["Substrate"] = substrate
    row["Process"] = "; ".join(process_strs)
    row["Evidence"] = "; ".join(evidence_keys)
    row["fact_ids"] = ",".join(fact_ids)
    row["unit_coverage_pct_min"] = "" if coverage_pct_min is None else f"{coverage_pct_min:.1f}"
    row["applicant"] = pm.get("applicant", "")
    row["filing_date"] = pm.get("filing_date", "")
    row["title"] = pm.get("title", "")

    # V1.2.5 task A: 每行 extraction_confidence 聚合。
    # min 暴露最弱的 fact (人审筛选用)；mean 是整体健康度。0.0-1.0。
    confidences = [
        f.get("extraction_confidence")
        for f in facts
        if isinstance(f.get("extraction_confidence"), (int, float))
    ]
    if confidences:
        row["extraction_confidence_min"] = f"{min(confidences):.2f}"
        row["extraction_confidence_mean"] = f"{sum(confidences) / len(confidences):.2f}"
    else:
        row["extraction_confidence_min"] = ""
        row["extraction_confidence_mean"] = ""

    return row


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def collect_facts(units_dir: Path) -> tuple[
    dict[tuple[str, str], list[dict]],
    dict[tuple[str, str], float],
    list[tuple[str, str, str]],  # 没法分配 example_id 的 fact
]:
    """遍历所有 unit 目录，按 (doc_id, example_id) 分组 facts。

    两遍:
      Pass 1: row/column 能 parse 时直接分配 example_id。
      Pass 2: orphans 走 polarity → example_id 映射 fallback
              (从 pass-1 学到的同 doc fact)。
    """
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    coverage_min: dict[tuple[str, str], float] = {}
    pending: list[tuple[Path, dict, float | None]] = []  # (unit_dir, fact, cov_pct)

    for unit_dir in sorted(units_dir.glob("U_*")):
        facts_file = unit_dir / "facts.json"
        if not facts_file.exists():
            continue
        text = facts_file.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            # 防御：文件是 "valid JSON + 尾部噪声" (Copy-Item 没完全截断时偶发)，
            # raw_decode 抓第一个完整 JSON 对象。
            try:
                obj, _ = json.JSONDecoder().raw_decode(text)
                data = obj
                print(f"  RECOVERED: {facts_file.name} had trailing junk after char "
                      f"{_}; parsed first {_} chars.", file=sys.stderr)
            except Exception as exc2:
                print(f"  WARN: cannot parse {facts_file}: {exc} / {exc2}", file=sys.stderr)
                continue
        except Exception as exc:
            print(f"  WARN: cannot parse {facts_file}: {exc}", file=sys.stderr)
            continue

        cov_pct: float | None = None
        cov_file = unit_dir / "coverage.json"
        if cov_file.exists():
            try:
                cov_pct = json.loads(cov_file.read_text(encoding="utf-8")).get("coverage_pct")
            except Exception:
                pass

        # V1.2.3：读这个 unit 的 VLM 描述，给 example_id_for_fact 的裸数字
        # fallback 用——只有 VLM 确认是 example 表才放行，避免把 "Hour 1" /
        # "Position 2" 这种列误认成 example_id。
        vlm_desc: dict | None = None
        vlm_file = unit_dir / "vlm_description.json"
        if vlm_file.exists():
            try:
                vlm_desc = json.loads(vlm_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # V1.2.3 Level 1.5：拼 unit 级 caption 文本，给 example_id_for_fact 用
        # ——row/column 不直接带 id 时回退到 "整 unit 属于 Example N" (例如
        # 一个 Example 内的 ladder / sweep 表)。
        caption_parts: list[str] = []
        meta_file = unit_dir / "meta.json"
        if meta_file.exists():
            try:
                m = json.loads(meta_file.read_text(encoding="utf-8"))
                cf = m.get("caption_footnote_text")
                if cf:
                    caption_parts.append(cf)
            except Exception:
                pass
        cap_file = unit_dir / "caption.txt"
        if cap_file.exists():
            try:
                txt = cap_file.read_text(encoding="utf-8").strip()
                if txt:
                    caption_parts.append(txt)
            except Exception:
                pass
        unit_caption = " ".join(caption_parts) if caption_parts else None

        for fact in data.get("facts", []) or []:
            ex_id = example_id_for_fact(fact, vlm_description=vlm_desc, caption=unit_caption)
            doc_id = fact.get("doc_id") or ""
            if ex_id and doc_id:
                key = (doc_id, ex_id)
                grouped[key].append(fact)
                if cov_pct is not None:
                    prev = coverage_min.get(key)
                    coverage_min[key] = cov_pct if prev is None else min(prev, cov_pct)
            else:
                pending.append((unit_dir, fact, cov_pct))

    # Pass 2: orphans 走 polarity fallback
    polarity_map = build_polarity_map(grouped)
    orphans: list[tuple[str, str, str]] = []
    for unit_dir, fact, cov_pct in pending:
        doc_id = fact.get("doc_id") or ""
        pol = fact.get("polarity_hint")
        ex_id = polarity_map.get(doc_id, {}).get(pol)
        if ex_id and doc_id:
            key = (doc_id, ex_id)
            grouped[key].append(fact)
            if cov_pct is not None:
                prev = coverage_min.get(key)
                coverage_min[key] = cov_pct if prev is None else min(prev, cov_pct)
        else:
            orphans.append((unit_dir.name, fact.get("fact_id", "?"),
                            (fact.get("evidence_pointer") or {}).get("row", "")))

    return grouped, coverage_min, orphans


def load_patent_meta(repo: Path, doc_id: str) -> dict | None:
    p = repo / "data" / "patents" / f"{doc_id}__patent_meta.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    units_dir = REPO / "data" / "units"
    output_dir = REPO / "output"
    output_dir.mkdir(exist_ok=True)
    csv_path = output_dir / "coatings_wide.csv"

    if not units_dir.exists():
        print(f"ERROR: {units_dir} does not exist", file=sys.stderr)
        return 1

    print(f"Reading from: {units_dir}")
    print(f"Writing to:   {csv_path}")
    print()

    subtype_map = build_subtype_map(units_dir)
    print(f"Subtype map: {len(subtype_map)} canonical IDs ({len(SUB_TYPES)} seeded + {len(subtype_map) - len(SUB_TYPES)} proposed)")

    grouped, coverage_min, orphans = collect_facts(units_dir)
    print(f"Grouped {sum(len(v) for v in grouped.values())} facts into {len(grouped)} (patent, example) rows")
    if orphans:
        print(f"WARN: {len(orphans)} facts could not be assigned an example_id (skipped)")
        for unit, fid, row in orphans[:5]:
            print(f"   - {unit} fact={fid} row={row!r}")
        if len(orphans) > 5:
            print(f"   ... ({len(orphans) - 5} more)")

    # 构建 rows
    rows: list[dict[str, str]] = []
    seen_doc_ids: set[str] = set()
    skipped_imposters: list[str] = []
    patent_meta_cache: dict[str, dict | None] = {}
    for (doc_id, ex_id), facts in sorted(grouped.items()):
        if doc_id not in patent_meta_cache:
            patent_meta_cache[doc_id] = load_patent_meta(REPO, doc_id)
        meta = patent_meta_cache[doc_id]

        # V1.2.3 Fix B: 按 IPC 跳过非涂料专利 (imposter)。
        # 它们的 facts 仍在磁盘上 (V2 / 调试用)，只是不进 CSV。
        if not is_coating_patent(meta):
            if doc_id not in skipped_imposters:
                skipped_imposters.append(doc_id)
            continue

        seen_doc_ids.add(doc_id)
        rows.append(build_row(
            doc_id, ex_id, facts, subtype_map,
            coverage_min.get((doc_id, ex_id)),
            meta,
        ))

    if skipped_imposters:
        print()
        print(f"Skipped {len(skipped_imposters)} non-coating patents (imposter filter):")
        for did in skipped_imposters:
            ipcs = (patent_meta_cache.get(did) or {}).get("ipc_codes") or []
            print(f"  {did}  IPCs: {', '.join(ipcs[:5])}")

    # 列序固定
    fieldnames: list[str] = ["patent_id", "example_id", "polarity"]
    fieldnames += [f"Material:{s}" for s in MATERIAL_SUB_TYPES]
    fieldnames += [f"Property:{s}" for s in PROPERTY_SUB_TYPES]
    fieldnames += [
        "TestMethod", "Application", "Substrate", "Process",
        "Evidence", "fact_ids", "unit_coverage_pct_min",
        "applicant", "filing_date", "title",
        # V1.2.5 task A: confidence 聚合列
        "extraction_confidence_min", "extraction_confidence_mean",
    ]

    with csv_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print()
    print(f"Wrote {len(rows)} rows × {len(fieldnames)} columns to {csv_path}")
    print()
    print("Sample (first 2 rows):")
    for r in rows[:2]:
        print(f"  {r['patent_id']} / {r['example_id']} / {r['polarity']}")
        for sub in MATERIAL_SUB_TYPES:
            v = r[f"Material:{sub}"]
            if v:
                print(f"    Material:{sub:38} = {v[:80]}{'...' if len(v) > 80 else ''}")
        for sub in PROPERTY_SUB_TYPES:
            v = r[f"Property:{sub}"]
            if v:
                print(f"    Property:{sub:38} = {v[:80]}{'...' if len(v) > 80 else ''}")
        for k in ["TestMethod", "Application", "Substrate", "Process", "Evidence", "applicant", "filing_date"]:
            v = r[k]
            if v:
                print(f"    {k:46} = {v[:80]}{'...' if len(v) > 80 else ''}")
        print()

    if not patent_meta_cache or all(v is None for v in patent_meta_cache.values()):
        print("NOTE: applicant/filing_date columns are empty — Stage 0.5 (patent_metadata_extractor)")
        print("      hasn't been built yet. Run it once to populate data/patents/<doc_id>__patent_meta.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
