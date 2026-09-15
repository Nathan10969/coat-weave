"""Pydantic 模型，跟 ``db/schema.sql`` 一一对应。

KG 里每行的 in-process 表示。``db/insert.py`` / ``db/query.py`` 用这些对象进出。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------
class NodeType(str, Enum):
    MAT = "MAT"
    APP = "APP"
    SUB = "SUB"
    PROP = "PROP"
    PROC = "PROC"
    TEST = "TEST"
    EVD = "EVD"
    PAT = "PAT"


class EdgeType(str, Enum):
    canonical_of = "canonical_of"
    synonym_of = "synonym_of"
    chemical_subtype_of = "chemical_subtype_of"
    forbidden_merge = "forbidden_merge"
    must_merge = "must_merge"


class SourceSectionType(str, Enum):
    TABLE = "TABLE"
    FIGURE = "FIGURE"
    TABLE_CAPTION_FOOTNOTE = "TABLE_CAPTION_FOOTNOTE"
    FIGURE_CAPTION_FOOTNOTE = "FIGURE_CAPTION_FOOTNOTE"


# V1.2.2 §修订 1 — 评分函数的质量权重
SOURCE_SECTION_WEIGHT: dict[SourceSectionType, float] = {
    SourceSectionType.TABLE: 1.00,
    SourceSectionType.FIGURE: 0.85,
    SourceSectionType.TABLE_CAPTION_FOOTNOTE: 0.85,
    SourceSectionType.FIGURE_CAPTION_FOOTNOTE: 0.75,
}


class FigureSubtype(str, Enum):
    structure = "structure"
    scheme = "scheme"
    plot = "plot"
    sem = "sem"
    apparatus = "apparatus"
    other = "other"


class Polarity(str, Enum):
    positive = "positive"
    negative = "negative"
    unknown = "unknown"


class Directionality(str, Enum):
    higher_is_better = "higher_is_better"
    lower_is_better = "lower_is_better"
    depends = "depends"


class ForbiddenMergeReason(str, Enum):
    chemistry_diff = "chemistry_diff"
    measurement_diff = "measurement_diff"
    scope_diff = "scope_diff"
    structure_role_diff = "structure_role_diff"
    standard_diff = "standard_diff"
    polarity_hint = "polarity_hint"


class MustMergeType(str, Enum):
    chemical_subtype_of = "chemical_subtype_of"
    trade_name_of = "trade_name_of"
    abbreviation_of = "abbreviation_of"
    synonym_of = "synonym_of"
    chinese_translation_of = "chinese_translation_of"
    formula_to_name = "formula_to_name"


# ----------------------------------------------------------------------
# Row models
# ----------------------------------------------------------------------
class Patent(BaseModel):
    doc_id: str
    title: str | None = None
    applicant: str | None = None
    inventor: list[str] | None = None
    filing_date: date | None = None
    priority_date: list[dict[str, Any]] | None = None
    publication_date: date | None = None
    ipc_codes: list[str] | None = None
    abstract: str | None = None
    language: Literal["en", "zh", "de"] | None = None
    corrected_version: str | None = None


class Node(BaseModel):
    canonical_id: str
    node_type: NodeType
    canonical_name: str
    chinese_name: str | None = None
    description: str | None = None
    name_embedding: list[float] | None = None


class Alias(BaseModel):
    alias_text: str
    canonical_id: str
    edge_type: EdgeType
    subtype: str | None = None
    confidence: float | None = None


class ForbiddenMerge(BaseModel):
    entity_a: str
    entity_b: str
    reason_type: ForbiddenMergeReason
    explanation: str | None = None
    risk_severity: Literal["low", "med", "high"] | None = None


class MustMerge(BaseModel):
    alias_text: str
    canonical_id: str
    merge_type: MustMergeType
    confidence: Literal["low", "med", "high"] | None = None
    source_evidence: str | None = None


class EvidencePointer(BaseModel):
    """单条 fact 的 evidence cell 定位器。

    V1.2.5 新增 (spec §7.2):
      - unit_id: 让下游 fact ↔ figure_table_units 直接 join，不用 parse fact_id 字符串。
        从 fact_id 模式 F_<unit_id>_<seq:03d> 在 fact_extractor 后处理回填。
      - bbox: unit 级 bounding box [x1, y1, x2, y2]（来自 MinerU layout），
        V1 demo 用于高亮原文证据。V2 可升级到 fact 级 cell bbox。
    """

    doc_id: str
    page: int
    region_type: str
    region_id: str
    unit_id: str | None = None             # ★ V1.2.5: 从 fact_id 回填
    row: str | None = None
    column: str | None = None
    cell: str | None = None
    bbox: list[float] | None = None        # ★ V1.2.5: [x1, y1, x2, y2] from MinerU


class FactHyperedge(BaseModel):
    """V1.2.2 fact spec 的 9 必填 + 10 可选 + 1 标记字段。"""

    # 9 必填
    fact_id: str
    application: str
    property: str = Field(..., alias="property")
    source_section_type: SourceSectionType
    polarity_hint: Polarity
    evidence_pointer: EvidencePointer
    ontology_version: str
    extraction_confidence: float = Field(..., ge=0.0, le=1.0)
    human_validated: bool = False
    doc_id: str

    # 10 可选
    substrate: dict[str, Any] | None = None  # {tested: id, claimed: [id]}
    resin_system: str | None = None
    additives: list[str] | None = None
    process: list[dict[str, Any]] | None = None
    test_method: str | None = None
    test_condition: dict[str, Any] | None = None
    result_value: float | None = None
    result_value_text: str | None = None
    baseline_value: float | None = None
    baseline_value_text: str | None = None
    comparison_group: str | None = None

    # 1 标记
    claimed_in_claims: bool = False

    # V1.2.4: LLM 规一化的 example identifier (E_<id> / C_<id> / null)。
    # Stage 9 优先用这个，再 fallback 到 evidence_pointer.row/column 的 regex。
    example_id: str | None = None

    # evidence 渲染
    evidence_text: str | None = None
    evidence_embedding: list[float] | None = None
    context_provenance: dict[str, Any] | None = None

    model_config = {"populate_by_name": True, "use_enum_values": True}


class FigureTableUnit(BaseModel):
    unit_id: str
    unit_type: Literal["figure", "table"]
    doc_id: str
    page: int | None = None
    region_id: str | None = None

    image_path: str | None = None
    caption_footnote_text: str | None = None
    vlm_description: str | None = None
    description_embedding: list[float] | None = None
    extracted_table_html: str | None = None

    tagged_entities: list[str] | None = None
    figure_subtype: FigureSubtype | None = None

    # V1.2.5: 来自 MinerU layout 的 bbox（用于证据渲染 / demo）。
    # 格式 [x1, y1, x2, y2]，PDF 页面绝对坐标。
    bbox: list[float] | None = None

    vlm_model_version: str | None = None
    description_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class PropertyRegistryEntry(BaseModel):
    property_id: str
    english_name: str
    chinese_name: str | None = None
    typical_unit: str | None = None
    directionality: Directionality
    requires_baseline: bool = False
    comparable_test_methods: list[str] | None = None
    related_but_not_equivalent: list[str] | None = None
    is_surface_property: bool = False
    is_aging_property: bool = False
    notes: str | None = None
