"""Stage 9.5 — passage_extractor (Layer 1: PassageHyperedge + FigureHyperedge).

Layer 1 has TWO types of hyperedges (V2.0.5 design):

  - PassageHyperedge: entity co-occurrence at paragraph level.
    Source: integrated NER over the full doc text (per-doc single-pass LLM,
    V1.2.6 redesign — not the older Tier A/B per-passage variant).

  - FigureHyperedge: visual references (reaction schemes, process flows,
    SEM micrographs, apparatus diagrams, etc.) that came from stage 4.5
    routing decisions. We DON'T re-run VLM here; we just collect the
    pending_figures.jsonl that stage 4.5 already wrote.

Output schema (data/layer1/<doc_id>/layer1.json):

  {
    "passages": [PassageHyperedge, ...],
    "figures":  [FigureHyperedge,  ...]
  }

PassageHyperedge fields (this file's main job):

  {
    "passage_id":  "<doc_id>_p<page>_b<block_idx>",
    "doc_id":      "...",
    "page":        18,
    "section_type": "DESCRIPTION" | "CLAIMS" | "EXAMPLES" | "ABSTRACT",
    "text_excerpt": "...",
    "entities":     ["MAT_*", "PROP_*", "APP_*", ...],
    "entities_provenance": {"MAT_X": "A", "MAT_Y": "B", ...},
    "source_tier":  ["A"] or ["A", "B"] or ["A", "B", "C"],
    "confidence":   0.0-1.0,
    "embedding_id": null  (Tier C 暂未启用)
  }

FigureHyperedge fields (collected from stage 4.5):

  {
    "figure_id":   "FIG_<unit_id>",
    "unit_id":     "U_...",
    "doc_id":      "...",
    "page":        15,
    "subtype":     "scheme" | "structure" | "plot" | "sem" | "apparatus" | "other",
    "image_path":  "data/units/U_.../image.png",
    "vlm_description": "...",
    "caption":     "...",
    "tagged_entities": [...],
    "reference_numerals": [{"numeral": "101", "label": "..."}, ...],
    "chemical_candidates": [
      {"label": "PAC1", "candidate_type": "resin_system", "needs_validation": true}
    ],
    "related_paragraphs": [
      {"para_id": 12, "page": 15, "score": 0.88, "reason": "...", "text": "..."}
    ],
    "confidence":  0.0-1.0
  }

Note on Tier A/B/C terminology: the field names ``entities_provenance`` and
``source_tier`` are kept for backward compatibility with V2.0.5 demos. The
"A" provenance still means alias-dictionary string match; the "B"
provenance is now from the per-doc LLM pass (V1.2.6 redesign), not the
deprecated per-passage Tier B.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("coating_kg.passage_extractor")


# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------
MIN_PASSAGE_CHARS = 50          # 太短的段落（< 50 字符）不抽，多半是噪声
TIER_B_TRIGGER_CHARS = 500      # 超过 500 字符 + 关键词命中才触发 Tier B（暂未启用）
TIER_B_TRIGGER_KEYWORDS = (
    "resin", "binder", "polymer", "coating", "composition",
    "additive", "cure", "crosslink", "polyurethane", "acrylate",
)


# Section type 推断规则（按 page index + 关键词组合）
SECTION_HINTS = {
    "ABSTRACT": (re.compile(r"\babstract\b", re.I), 0, 3),       # 多数在 page 0-3
    "CLAIMS": (re.compile(r"\bclaims?\b|\bclaim\s+\d", re.I), None, None),
    "EXAMPLES": (re.compile(r"\bexamples?\b|\bembodiments?\b|实施例", re.I), None, None),
}
# ---------------------------------------------------------------------------
# Tier B prompt — 轻量 NER (仅抽实体名 + 类型，不抽数值/极性/process)
# ---------------------------------------------------------------------------
# V1.2.6 redesign: per-DOC LLM call (1 call instead of 119 per-passage).
# The configured long-context text LLM fits an entire patent doc, and seeing
# full-doc context is more accurate than isolated passages.
# 单段更准（区分 polyurethane 是 resin / binder / coating 三种角色）。
_TIER_B_DOC_PROMPT = """You are a coating-chemistry domain expert performing entity extraction.

Given the FULL TEXT of a coating patent below, identify all named entities NOT yet
in the existing list. For each NEW entity, output:
- phrase: the exact noun phrase as it appears in the text
- type: one of Material / Property / Application / Substrate / Process / TestMethod
- suggested_canonical_id: <TYPE_PREFIX>_<snake_case_name>
    Material  -> MAT_polyurethane_dispersion, MAT_BASF_X-Tend_2400
    Property  -> PROP_scratch_resistance, PROP_water_contact_angle
    Application -> APP_automotive_clearcoat, APP_marine_coating
    Substrate -> SUB_aluminum, SUB_concrete
    Process   -> PROC_spray_apply, PROC_uv_cure
    TestMethod-> TEST_ISO_2409, TEST_ASTM_D3359

Existing entities ALREADY captured by Tier A (DO NOT repeat these):
{existing_canonicals}

Rules:
- Skip pronouns ("the resin", "this composition") and abstract terms ("good performance")
- Only specific named entities (chemical names, brand names, standards, properties)
- For unknown trade names, propose new canonical_id (e.g., MAT_BASF_X-Tend_2400)
- Output STRICT JSON only - no markdown fences, no explanation text
- Each entity should appear ONCE in output, even if mentioned multiple times in doc

Output schema (JSON):
{{
  "entities": [
    {{"phrase": "...", "type": "Material", "suggested_canonical_id": "MAT_..."}},
    ...
  ]
}}

If no new entities, output: {{"entities": []}}

Patent full text:
{full_doc_text}
"""



# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class PassageHyperedge:
    """Layer 1 段落超边。

    Schema notes (V2.0.5 conscious deviation from spec §修订 6):
      - spec wanted only 5 fields (text + page + para_index + tagged_entities
        + text_embedding) and "no LLM";
      - we keep ``confidence`` / ``entity_phrases`` (via entities_provenance)
        / ``section_type`` because retrieval-time debugging and explainability
        need them, and we use an LLM because A100 local inference is free.
      - See ``feedback_stage9_5_llm_and_schema`` memory for full rationale.
    """
    passage_id: str
    doc_id: str
    page: int
    section_type: str
    text_excerpt: str
    entities: list[str] = field(default_factory=list)
    entities_provenance: dict[str, str] = field(default_factory=dict)
    source_tier: list[str] = field(default_factory=list)
    confidence: float = 0.0
    embedding_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "passage_id": self.passage_id,
            "doc_id": self.doc_id,
            "page": self.page,
            "section_type": self.section_type,
            "text_excerpt": self.text_excerpt,
            "entities": self.entities,
            "entities_provenance": self.entities_provenance,
            "source_tier": self.source_tier,
            "confidence": round(self.confidence, 3),
            "embedding_id": self.embedding_id,
        }


@dataclass
class FigureHyperedge:
    """Layer 1 图表超边 — collected from stage 4.5 register_layer1 routes.

    These are figures that don't carry quantitative data (reaction schemes,
    process flows, SEM, apparatus diagrams) but still have retrieval value
    via VLM description + tagged entities. Retrieval returns the original
    image_path so the frontend can render the actual visual.
    """
    figure_id: str
    unit_id: str           # ★ cross-layer JOIN key with Layer 2 fact.evidence_pointer.unit_id
    doc_id: str
    page: int              # ★ for PDF jump links
    subtype: str
    image_path: str | None  # ★ for direct frontend rendering (None for tables)
    vlm_description: str
    caption: str
    tagged_entities: list[str] = field(default_factory=list)
    reference_numerals: list[dict[str, str]] = field(default_factory=list)
    chemical_candidates: list[dict[str, Any]] = field(default_factory=list)
    related_paragraphs: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.9

    def to_json(self) -> dict[str, Any]:
        return {
            "figure_id": self.figure_id,
            "unit_id": self.unit_id,
            "doc_id": self.doc_id,
            "page": self.page,
            "subtype": self.subtype,
            "image_path": self.image_path,
            "vlm_description": self.vlm_description,
            "caption": self.caption,
            "tagged_entities": self.tagged_entities,
            "reference_numerals": self.reference_numerals,
            "chemical_candidates": self.chemical_candidates,
            "related_paragraphs": self.related_paragraphs,
            "confidence": round(self.confidence, 3),
        }


# ---------------------------------------------------------------------------
# Pending-figures loader — reads what stage 4.5 wrote
# ---------------------------------------------------------------------------
def load_pending_figures(repo: Path, doc_id: str) -> list[FigureHyperedge]:
    """Read ``data/layer1/<doc_id>/pending_figures.jsonl`` written by stage 4.5.

    Returns empty list if the file doesn't exist (e.g. all units were
    extract_facts or delete, no figures registered).
    """
    jsonl_path = repo / "data" / "layer1" / doc_id / "pending_figures.jsonl"
    if not jsonl_path.exists():
        return []

    figures: list[FigureHyperedge] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "load_pending_figures: skipped malformed line %d in %s: %s",
                    line_num, jsonl_path, exc,
                )
                continue
            try:
                figures.append(FigureHyperedge(
                    figure_id=rec["figure_id"],
                    unit_id=rec["unit_id"],
                    doc_id=rec["doc_id"],
                    page=int(rec.get("page", 0)),
                    subtype=rec.get("subtype", "other"),
                    image_path=rec.get("image_path"),
                    vlm_description=rec.get("vlm_description", ""),
                    caption=rec.get("caption", ""),
                    tagged_entities=rec.get("tagged_entities", []) or [],
                    reference_numerals=rec.get("reference_numerals", []) or [],
                    chemical_candidates=rec.get("chemical_candidates", []) or [],
                    related_paragraphs=rec.get("related_paragraphs", []) or [],
                    confidence=float(rec.get("confidence", 0.9)),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "load_pending_figures: skipped invalid record on line %d: %s",
                    line_num, exc,
                )

    # De-dup by figure_id (in case stage 4.5 was re-run and appended twice)
    seen: set[str] = set()
    unique: list[FigureHyperedge] = []
    for fig in figures:
        if fig.figure_id in seen:
            continue
        seen.add(fig.figure_id)
        unique.append(fig)
    return unique


# ---------------------------------------------------------------------------
# Tier A — 字符串/aho-corasick 匹配
# ---------------------------------------------------------------------------
class AliasMatcher:
    """A simple alias matcher. For production use, replace with aho-corasick
    (e.g., the `pyahocorasick` package) for O(N + M) total scan time.

    Current implementation: linear pass per alias, fine for a few hundred
    canonical_ids and tens of paragraphs per PDF. Upgrade to aho-corasick
    when ontology grows past ~5000 canonicals.
    """

    def __init__(self, alias_to_canonical: dict[str, str]):
        """alias_to_canonical: dict from lowercased alias string to canonical_id."""
        # Sort aliases by length DESC so longer matches win first
        # ("polyurethane resin" matches before "polyurethane")
        self._items = sorted(
            alias_to_canonical.items(),
            key=lambda kv: -len(kv[0]),
        )

    def match(self, text: str) -> list[str]:
        """Return list of canonical_ids found in text (case-insensitive, deduped)."""
        text_lower = text.lower()
        hits: list[str] = []
        seen: set[str] = set()
        for alias, canonical in self._items:
            if alias in text_lower and canonical not in seen:
                hits.append(canonical)
                seen.add(canonical)
        return hits


def load_ontology_aliases(ontology_path: Path) -> dict[str, str]:
    """Load the alias dictionary from ontology JSON / CSV / DB seed.

    Expected format (per ontology):
      {
        "MAT_polyurethane": ["polyurethane", "PU", "聚氨酯", "polyurethane resin"],
        "PROP_adhesion_cross_cut": ["adhesion (cross-cut)", "cross-cut adhesion", "划格附着力"],
        ...
      }

    Returns flat alias→canonical dict (lowercased aliases).
    """
    if not ontology_path.exists():
        logger.warning("ontology aliases file not found: %s — Tier A will be inert", ontology_path)
        return {}

    raw = json.loads(ontology_path.read_text(encoding="utf-8"))
    flat: dict[str, str] = {}
    for canonical, aliases in raw.items():
        if isinstance(aliases, list):
            for a in aliases:
                if a and isinstance(a, str):
                    flat[a.lower()] = canonical
        # also include the canonical id stem itself as alias
        # (e.g. "polyurethane" from MAT_polyurethane)
        stem = canonical.split("_", 1)[-1].replace("_", " ").lower()
        if stem and stem not in flat:
            flat[stem] = canonical
    return flat


# ---------------------------------------------------------------------------
# Tier B / C — STUB（暂未启用，等 vLLM + BGE-M3 部署）
# ---------------------------------------------------------------------------
def _tier_b_doc_level_ner(
    full_doc_text: str,
    existing_canonicals: list[str],
) -> list[tuple[str, str]]:
    """V1.2.6 Tier B: 一次 LLM 调用扫整篇 doc，找 Tier A 漏的 entity。

    设计权衡：原 per-passage 设计每篇 doc 调 LLM 100+ 次（10 min, ¥2.4/篇）。
    新设计每篇 1 次（10s, ¥0.05/篇，48x 便宜，60x 快），且 LLM 看全文上下文
    比看孤立段落更准（区分 polyurethane 在不同段落里是 resin / binder / 涂层）。

    Returns list of (phrase, canonical_id) tuples. Empty list on failure.
    """
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed; Tier B disabled")
        return []

    try:
        from coating_kg.config import SETTINGS
    except Exception:
        logger.warning("SETTINGS not loadable; Tier B disabled")
        return []

    api_key = SETTINGS.openai.api_key
    base_url = SETTINGS.openai.base_url
    model = SETTINGS.models.qwen_text
    if not api_key:
        return []

    client = OpenAI(api_key=api_key, base_url=base_url)
    existing_str = ", ".join(existing_canonicals) if existing_canonicals else "(none)"
    # Long-context text model. 100k chars is roughly 25k tokens.
    prompt = _TIER_B_DOC_PROMPT.format(
        existing_canonicals=existing_str,
        full_doc_text=full_doc_text[:100000],
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4000,   # 整篇 doc 的 entity list 可能多，留足
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
    except Exception as exc:
        logger.warning("Tier B (doc-level) LLM call failed: %s", exc)
        return []

    try:
        payload = json.loads(raw)
    except Exception as exc:
        logger.warning("Tier B JSON parse failed: %s; raw=%r", exc, raw[:200])
        return []

    items = payload.get("entities") or []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        canonical = item.get("suggested_canonical_id")
        phrase = item.get("phrase")
        if not canonical or not phrase:
            continue
        # 过滤明显不规范的 canonical_id 前缀
        if not any(canonical.startswith(p) for p in ("MAT_", "PROP_", "APP_", "SUB_", "PROC_", "TEST_")):
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append((phrase, canonical))
    return out


def _tier_c_embedding(text: str) -> str | None:
    """TODO V2.0.5: call local BGE-M3 to embed paragraph.

    Stores vector in pgvector and returns its ID.
    Currently returns None — embedding deferred.
    """
    return None


# ---------------------------------------------------------------------------
# 核心抽取逻辑
# ---------------------------------------------------------------------------
def _infer_section_type(block_text: str, page_idx: int, total_pages: int) -> str:
    """Heuristic: classify which section a paragraph belongs to."""
    for section, (pattern, min_pg, max_pg) in SECTION_HINTS.items():
        if pattern.search(block_text):
            if min_pg is None or page_idx >= min_pg:
                if max_pg is None or page_idx <= max_pg:
                    return section
    # Default: by relative position
    if page_idx < total_pages * 0.15:
        return "ABSTRACT"
    elif page_idx > total_pages * 0.85:
        return "CLAIMS"
    else:
        return "DESCRIPTION"




def extract_passages(
    layout: dict[str, Any],
    doc_id: str,
    *,
    alias_matcher: AliasMatcher,
    enable_tier_b: bool = False,
    enable_tier_c: bool = False,
) -> list[PassageHyperedge]:
    """Main entry: extract Layer 1 passages from a single PDF's MinerU layout.

    Args:
      layout: MinerU output dict with "data" key (list of blocks).
      doc_id: patent ID (e.g., "WO2026057741A1").
      alias_matcher: pre-built Tier A matcher.
      enable_tier_b / enable_tier_c: feature flags for V2.0.5 deployment.

    Returns:
      list of PassageHyperedge objects.
    """
    data = layout.get("data") or []
    blocks = (
        data if isinstance(data, list)
        else (data.get("para_blocks", []) if isinstance(data, dict) else [])
    )

    # Get total pages for section_type inference
    total_pages = max(
        (b.get("page_idx", 0) for b in blocks if isinstance(b, dict)),
        default=1,
    ) + 1

    # ★ V1.2.6: Tier B 改成 per-doc 一次调用
    # 1) 先全篇 Tier A 跑一遍收 existing canonicals
    # 2) 整篇喂 LLM 一次拿 (phrase, canonical_id) list
    # 3) per-passage 时用 phrase 子串扫描，命中标 provenance="B"
    tier_b_phrase_to_cid: dict[str, str] = {}
    if enable_tier_b:
        # 收集整篇 text + 收集 Tier A 已 captured 的 canonicals
        text_blocks = [
            (b.get("text") or "").strip()
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip()
        ]
        full_doc_text = "\n\n".join(text_blocks)
        tier_a_existing: set[str] = set()
        for t in text_blocks:
            for hit in alias_matcher.match(t):
                tier_a_existing.add(hit)
        logger.info(
            "Tier B (doc-level): doc=%s, %d text blocks (%d chars), Tier A captured %d entities",
            doc_id, len(text_blocks), len(full_doc_text), len(tier_a_existing),
        )
        tier_b_pairs = _tier_b_doc_level_ner(full_doc_text, sorted(tier_a_existing))
        for phrase, cid in tier_b_pairs:
            tier_b_phrase_to_cid[phrase.lower()] = cid
        logger.info(
            "Tier B (doc-level): doc=%s, LLM returned %d new entities",
            doc_id, len(tier_b_phrase_to_cid),
        )

    passages: list[PassageHyperedge] = []
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = (block.get("text") or "").strip()
        if len(text) < MIN_PASSAGE_CHARS:
            continue

        page_idx = block.get("page_idx", 0)
        passage_id = f"{doc_id}_p{page_idx}_b{i}"
        section_type = _infer_section_type(text, page_idx, total_pages)

        # Tier A
        tier_a_hits = alias_matcher.match(text)
        provenance = {ent: "A" for ent in tier_a_hits}
        tiers_used = ["A"] if tier_a_hits else []

        # Tier B (V1.2.6 per-doc): 在已有 phrase→canonical 字典里扫这个 passage
        if tier_b_phrase_to_cid:
            text_lower = text.lower()
            tier_b_added = False
            for phrase_lower, cid in tier_b_phrase_to_cid.items():
                if phrase_lower in text_lower and cid not in provenance:
                    provenance[cid] = "B"
                    tier_b_added = True
            if tier_b_added:
                tiers_used.append("B")

        # Tier C (TODO — currently no-op)
        embedding_id = _tier_c_embedding(text) if enable_tier_c else None
        if embedding_id:
            tiers_used.append("C")

        # Confidence: simple heuristic — more entities = higher confidence
        # (will be replaced with proper rerank score in V2.0.5)
        n_entities = len(provenance)
        if n_entities == 0:
            # Skip empty passages — no value for KG retrieval
            continue
        confidence = min(0.5 + 0.1 * n_entities, 0.95)

        passage = PassageHyperedge(
            passage_id=passage_id,
            doc_id=doc_id,
            page=page_idx,
            section_type=section_type,
            text_excerpt=text[:500],   # 截断，原文磁盘上有
            entities=sorted(provenance.keys()),
            entities_provenance=provenance,
            source_tier=tiers_used,
            confidence=confidence,
            embedding_id=embedding_id,
        )
        passages.append(passage)

    return passages


def save_layer1(
    repo: Path,
    doc_id: str,
    passages: list[PassageHyperedge],
    figures: list[FigureHyperedge],
) -> Path:
    """Persist Layer 1 (passages + figures) to ``data/layer1/<doc_id>/layer1.json``.

    Output schema:
        {
          "doc_id": "...",
          "passages": [PassageHyperedge.to_json(), ...],
          "figures":  [FigureHyperedge.to_json(),  ...],
        }
    """
    out_dir = repo / "data" / "layer1" / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "layer1.json"
    out_path.write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "passages": [p.to_json() for p in passages],
                "figures": [f.to_json() for f in figures],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out_path


# Deprecated alias — kept temporarily so older callers keep working.
def save_passages(
    repo: Path,
    doc_id: str,
    passages: list[PassageHyperedge],
) -> Path:
    """Deprecated. Use save_layer1(repo, doc_id, passages, figures=[])."""
    logger.warning(
        "save_passages is deprecated; use save_layer1 with explicit figures list",
    )
    return save_layer1(repo, doc_id, passages, figures=[])


# ---------------------------------------------------------------------------
# CLI / batch entry point (for V2.0.5 standalone runs)
# ---------------------------------------------------------------------------
def run_for_doc(
    repo: Path,
    mineru_dir: Path,
    doc_id: str,
    *,
    alias_matcher: AliasMatcher,
    enable_tier_b: bool = False,
    enable_tier_c: bool = False,
) -> tuple[int, int, int]:
    """Run Stage 9.5 (Layer 1 builder) for a single doc_id.

    Steps:
      1. Run paragraph NER (PassageHyperedge) over MinerU layout.
      2. Collect FigureHyperedge candidates that stage 4.5 wrote into
         pending_figures.jsonl.
      3. Persist both to data/layer1/<doc_id>/layer1.json.

    Returns (n_passages, n_figures, n_total_entities).
    """
    # Load layout from disk (uses pdf_layout._load_layout_json convention)
    from .pdf_layout import _load_layout_json

    try:
        layout = _load_layout_json(mineru_dir, doc_id)
    except Exception as exc:
        logger.warning("layout load failed for %s: %s", doc_id, exc)
        return (0, 0, 0)

    passages = extract_passages(
        layout=layout,
        doc_id=doc_id,
        alias_matcher=alias_matcher,
        enable_tier_b=enable_tier_b,
        enable_tier_c=enable_tier_c,
    )

    # Collect figures already registered by stage 4.5 (cheap — disk read only)
    figures = load_pending_figures(repo, doc_id)
    if figures:
        logger.info(
            "stage 9.5: doc=%s collected %d figures from stage 4.5", doc_id, len(figures),
        )

    save_layer1(repo, doc_id, passages, figures)
    n_entities = sum(len(p.entities) for p in passages)
    return (len(passages), len(figures), n_entities)
