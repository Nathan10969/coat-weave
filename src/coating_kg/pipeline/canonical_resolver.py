"""把 LLM 输出的 entity 字符串解析成已 seed 的 canonical ID。

V1.2.2 §7.5 硬规则 #3：每条 fact 的 ``application`` 和 ``property`` 必须
能解析到 canonical 节点。本模块就是这道闸。

离线模式 (PoC)：把 ``seed_canonical_starter.sql`` /
``seed_property_directionality.sql`` / ``seed_must_merge_starter.sql``
读进内存；不需要活 DB。策略**严格**:

1. ``canonical_id`` 精确匹配 (e.g. ``"APP_automotive_oem_clearcoat"``)
2. ``must_merge`` 里 alias 匹配（``alias_text`` 大小写不敏感）
3. 否则 → ``None``（caller reject 这条 fact）

刻意不做 fuzzy / lexical / embedding fallback —— §7.5 strict。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable

from ..config import SETTINGS

logger = logging.getLogger(__name__)


_DEFAULT_SEEDS = (
    "seed_canonical_starter.sql",
    "seed_property_directionality.sql",
)
_MUST_MERGE_SEED = "seed_must_merge_starter.sql"


# canonical_id → sub_type 标签。驱动层级化 prompt 渲染，告诉 LLM 提议的新 ID
# 该归到哪个分类。在 sub_type 升级到 schema 列前 (V1.3+)，这里是 taxonomy
# 唯一来源。
SUB_TYPES: dict[str, str] = {
    # ---- MAT: Resin / binder ----
    "MAT_acrylic_resin":             "Resin / binder",
    "MAT_pure_acrylic_emulsion":     "Resin / binder",
    "MAT_styrene_acrylic_latex":     "Resin / binder",
    "MAT_silicone_acrylic_emulsion": "Resin / binder",
    "MAT_polyurethane_dispersion":   "Resin / binder",
    "MAT_2K_PU_OH_acrylic":          "Resin / binder",
    "MAT_fluorocarbon_FEVE":         "Resin / binder",
    "MAT_fluorocarbon_PVDF":         "Resin / binder",
    # ---- MAT: Crosslinker / Curing agent ----
    "MAT_HDI_trimer":                "Crosslinker / Curing agent",
    # ---- MAT: Pigment ----
    "MAT_titanium_dioxide_rutile":   "Pigment",
    # ---- MAT: Filler ----
    "MAT_calcium_carbonate":         "Filler",
    # ---- MAT: Additive ----
    "MAT_HALS_Tinuvin_292":          "Additive",
    "MAT_UV_absorber_Tinuvin_1577":  "Additive",
    "MAT_silane_kh550":              "Additive",
    "MAT_silane_kh560":              "Additive",
    # ---- MAT: Solvent / Monomer 在 seed 里空；让 LLM 提议 ----

    # ---- PROP: Appearance ----
    "PROP_gloss_20deg":              "Appearance",
    "PROP_gloss_60deg":              "Appearance",
    "PROP_water_whitening":          "Appearance",
    "PROP_opacity":                  "Appearance",
    # ---- PROP: Mechanical ----
    "PROP_pencil_hardness":          "Mechanical",
    "PROP_scratch_resistance":       "Mechanical",
    "PROP_impact":                   "Mechanical",
    "PROP_flexibility":              "Mechanical",
    "PROP_scrub_resistance":         "Mechanical",
    # ---- PROP: Surface ----
    "PROP_contact_angle":            "Surface",
    "PROP_adhesion_cross_cut":       "Surface",
    "PROP_adhesion_pull_off":        "Surface",
    # ---- PROP: Aging / Corrosion / Durability ----
    "PROP_weatherability_QUV":       "Aging / Corrosion / Durability",
    "PROP_chalk_resistance":         "Aging / Corrosion / Durability",
    "PROP_dirt_pickup_resistance":   "Aging / Corrosion / Durability",
    "PROP_salt_spray":               "Aging / Corrosion / Durability",
    # ---- PROP: Chemical resistance ----
    "PROP_water_absorption":         "Chemical resistance",
    # ---- PROP: Environmental / Regulatory ----
    "PROP_VOC":                      "Environmental / Regulatory",
    # ---- PROP: Application properties (flow / film / storage) ----
    "PROP_viscosity":                "Application properties",
    "PROP_film_thickness":           "Application properties",
    "PROP_drying_time":              "Application properties",
    "PROP_freeze_thaw":              "Application properties",
    # ---- PROP: Formulation ----
    "PROP_composition_weight_percent": "Formulation",

    # ---- APP ----
    "APP_automotive_oem_clearcoat":         "Automotive",
    "APP_automotive_oem_basecoat":          "Automotive",
    "APP_industrial_protective_coating":    "Industrial protective",
    "APP_architectural_exterior_coating":   "Architectural",
    "APP_architectural_interior_coating":   "Architectural",

    # ---- SUB ----
    "SUB_steel_CRS_phosphated":      "Metal",
    "SUB_steel_HDG":                 "Metal",
    "SUB_aluminum":                  "Metal",
    "SUB_steel_panel":               "Metal",
    "SUB_cement_mortar_block":       "Mineral",
    "SUB_gypsum_board":              "Mineral",

    # ---- TEST 和 PROC: 扁平 (无 sub_type) ----
}


# 各 node 类型允许的 sub_type — LLM 提议新 ID 时校验它声明的分类。
ALLOWED_SUB_TYPES: dict[str, set[str]] = {
    "MAT": {
        "Resin / binder", "Crosslinker / Curing agent", "Pigment",
        "Filler", "Additive", "Solvent", "Monomer",
    },
    "PROP": {
        "Appearance", "Mechanical", "Surface",
        "Aging / Corrosion / Durability", "Chemical resistance",
        "Thermal", "Environmental / Regulatory",
        "Application properties", "Formulation",
    },
    "APP": {"Automotive", "Industrial protective", "Specialty", "Architectural"},
    "SUB": {"Metal", "Plastic", "Mineral"},
}

# 匹配 INSERT VALUES 元组前 3 列:
#   ('CANONICAL_ID', 'NODE_TYPE', 'CANONICAL_NAME', ...)
_NODE_TUPLE_RE = re.compile(
    r"\(\s*'([A-Z]+_[^']+)'\s*,\s*'([A-Z]+)'\s*,\s*'([^']*)'"
)
# 匹配 must_merge 前 2 列:
#   ('alias_text', 'CANONICAL_ID', ...)
_ALIAS_TUPLE_RE = re.compile(
    r"\(\s*'([^']+)'\s*,\s*'([A-Z]+_[^']+)'"
)


class CanonicalResolver:
    """In-memory resolver。每进程构造一次复用。"""

    def __init__(self, seed_dir: Path | None = None) -> None:
        self.seed_dir = seed_dir or (SETTINGS.project_root / "db")
        # canonical_id -> {"type": "APP", "name": "..."}
        self.nodes_by_id: dict[str, dict[str, str]] = {}
        # alias_lowered -> canonical_id
        self.aliases: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        for fname in _DEFAULT_SEEDS:
            path = self.seed_dir / fname
            if not path.exists():
                logger.warning("seed file missing: %s", path)
                continue
            text = path.read_text(encoding="utf-8")
            for cid, ntype, name in _NODE_TUPLE_RE.findall(text):
                # 同 canonical_id 可能在多个 seed 文件出现 (PROP 节点同时在
                # nodes 表和 seed_property_directionality.sql 的 property_registry
                # insert 里都有)；先到先得，去重无害。
                self.nodes_by_id.setdefault(cid, {"type": ntype, "name": name})

        mm_path = self.seed_dir / _MUST_MERGE_SEED
        if mm_path.exists():
            mm_text = mm_path.read_text(encoding="utf-8")
            for alias, cid in _ALIAS_TUPLE_RE.findall(mm_text):
                # 跳过形如 (CANONICAL_ID, CANONICAL_ID) 的行 — 只留人类可读 alias。
                if cid not in self.nodes_by_id:
                    continue
                self.aliases.setdefault(alias.lower(), cid)
        logger.info(
            "CanonicalResolver loaded: %d nodes, %d aliases",
            len(self.nodes_by_id), len(self.aliases),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def resolve(self, text: str | None, *, kind: str | None = None) -> str | None:
        """返回 canonical_id 或 None。

        ``kind`` (可选)：限定 node_type — ``APP / MAT / PROP / PROC / SUB / TEST``
        之一。``None`` 时任意类型都接受。
        """
        if not text:
            return None
        # 1. canonical_id 精确匹配
        node = self.nodes_by_id.get(text)
        if node is not None and (kind is None or node["type"] == kind):
            return text
        # 2. alias 匹配（大小写不敏感）
        cid = self.aliases.get(text.lower())
        if cid is not None:
            node = self.nodes_by_id.get(cid)
            if node is not None and (kind is None or node["type"] == kind):
                return cid
        return None

    def validate_fact(self, fact: dict[str, Any]) -> tuple[bool, list[str]]:
        """跑 §7.5 硬规则 #3：``application`` + ``property`` 必须解析。

        返回 ``(passes, unresolved_required_fields)``。``unresolved`` 为空时通过。
        """
        unresolved: list[str] = []
        if not self.resolve(fact.get("application"), kind="APP"):
            unresolved.append("application")
        if not self.resolve(fact.get("property"), kind="PROP"):
            unresolved.append("property")
        return (not unresolved, unresolved)

    def resolve_optional_slots(
        self, fact: dict[str, Any],
    ) -> dict[str, Any]:
        """对可选字段每槽位的解析报告。每槽位映射到：解析成功的 canonical_id /
        未解析的原始字符串 / ``None`` (槽位本身缺失)。
        测覆盖率用，**不修改** fact。
        """
        out: dict[str, Any] = {}
        # 单值槽
        for slot, kind in (("resin_system", "MAT"), ("test_method", "TEST")):
            v = fact.get(slot)
            out[slot] = self.resolve(v, kind=kind) if v else None
        # substrate.tested
        sub = fact.get("substrate") or {}
        tested = sub.get("tested")
        out["substrate.tested"] = self.resolve(tested, kind="SUB") if tested else None
        # additives list[MAT_*]
        adds = fact.get("additives") or []
        out["additives"] = [self.resolve(a, kind="MAT") for a in adds]
        # process list of dicts，含 "step": PROC_*
        steps = fact.get("process") or []
        out["process.step"] = [self.resolve(s.get("step"), kind="PROC") for s in steps]
        return out


def all_canonical_ids(resolver: CanonicalResolver, kind: str | None = None) -> Iterable[str]:
    """方便函数：迭代所有已加载的 canonical_id，可按类型过滤。"""
    for cid, node in resolver.nodes_by_id.items():
        if kind is None or node["type"] == kind:
            yield cid


def by_subtype(
    resolver: CanonicalResolver, kind: str,
) -> dict[str, list[tuple[str, str]]]:
    """把 ``kind`` 类型的所有 canonical ID 按 sub_type 标签分组。

    没有子分类的类型 (TEST / PROC / EVD / PAT)，所有 ID 进空字符串 ``""`` 桶。
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for cid, node in sorted(resolver.nodes_by_id.items()):
        if node["type"] != kind:
            continue
        sub = SUB_TYPES.get(cid, "")
        out.setdefault(sub, []).append((cid, node.get("name", "")))
    return out
