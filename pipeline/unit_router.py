"""Stage 4.5 — unit router (three-way 分流).

Routes each unit (figure or table) to one of three downstream paths based on
stage 4 VLM output. Pure-rule routing, no LLM call. Reuses the
``vlm_description.json`` we already paid for to make a free decision and
truncate ~50% of downstream stage 6/7 LLM calls per batch.

Routes:
  - "extract_facts":    proceed to stage 6/7 (Layer 2 fact extraction)
  - "register_layer1":  skip stage 6/7, register a FigureHyperedge candidate
                        into ``data/layer1/<doc_id>/pending_figures.jsonl``
                        for stage 9.5 to collect.
  - "delete":           **soft-delete** — move unit folder to
                        ``data/units/_routed_out/<unit_id>/`` (reversible).
                        Used for low-info noise (logo / decoration).
                        V1.2.7: changed from rmtree to soft-delete so router
                        rule errors are recoverable.

See PIPELINE_STAGES_v5.md §2 Stage 4.5 for the detailed design rationale.
"""
from __future__ import annotations

import csv
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("coating_kg.unit_router")

Route = Literal["extract_facts", "register_layer1", "delete"]


# ---------------------------------------------------------------------------
# Routing rules — closed enums from stage 4 prompt outputs
# ---------------------------------------------------------------------------

# Figure subtype (vlm_figure.txt closed enum, lowercase English)
FIGURE_REGISTER_LAYER1: set[str] = {
    "structure",   # chemical structural formula
    "scheme",      # reaction / synthesis scheme
    "plot",        # numerical plot — v1 keep as Layer 1; v2 may upgrade to extract_facts
    "sem",         # SEM / TEM / optical micrograph
    "apparatus",   # process / test equipment diagram
    "other",       # default catch-all
}

# Table subject (vlm_table.txt closed enum, Chinese)
TABLE_EXTRACT_FACTS: set[str] = {
    "性能对比",
    "配方对比",
    "测试条件",
}
TABLE_REGISTER_LAYER1: set[str] = {
    "其它",  # ambiguous tables — keep as Layer 1 visual reference
}

# Description-level low-info threshold (logo / decoration detection)
SKIP_DESC_LEN_THRESHOLD = 30


@dataclass
class RouteDecision:
    route: Route
    reason: str
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Core routing logic
# ---------------------------------------------------------------------------
def classify_unit_route(
    unit: Any,
    vlm_desc: dict[str, Any] | None,
) -> RouteDecision:
    """Decide which downstream path a unit takes.

    Decision tree (V1.2.7, first match wins):
      1. VLM call failed entirely           -> extract_facts (preserve recall;
                                               stage 7 has table_html + paragraphs as backup)
      2. low-info: entities=[] AND len<30   -> delete (logo / decoration)
      3. figure with closed-enum subtype    -> register_layer1
      4. figure with unknown subtype        -> register_layer1 (defensive)
      5. table_subject in 性能/配方/测试   -> extract_facts
      6. table_subject in 其它 / unknown    -> register_layer1 (defensive)

    NOTE: cross-page table dedup (region_id_dedup_skip) is NOT yet implemented
    upstream in stage 2. When stage 2 adds dedup, gate by
    ``unit.meta.get("region_id_dedup_skip")`` here. For now removed (YAGNI).
    """
    # Rule 1: VLM failed — preserve recall, let stage 7 try with table_html
    # (V1.2.7: previously routed to delete, but stage 7 has graceful degradation
    # without VLM via matched_paragraphs + table_html).
    if not vlm_desc:
        return RouteDecision("extract_facts", "vlm_failed_fallback_to_extract", 0.35)

    description = (vlm_desc.get("description") or "").strip()
    entities = vlm_desc.get("identified_entities") or []

    # Rule 2: low-info detection (BASF logo / version banner / decoration)
    if not entities and len(description) < SKIP_DESC_LEN_THRESHOLD:
        return RouteDecision("delete", "low_info_decoration", 0.75)

    unit_type = getattr(unit, "unit_type", "table")  # default conservatively to table

    # Rule 4 & 5: figure routing
    if unit_type == "figure":
        subtype = (vlm_desc.get("subtype") or "other").strip().lower()
        if subtype in FIGURE_REGISTER_LAYER1:
            return RouteDecision("register_layer1", f"figure_{subtype}", 0.85)
        # Unknown subtype — defensive: don't lose the figure, send to layer 1
        return RouteDecision("register_layer1", f"figure_unknown_subtype:{subtype}", 0.55)

    # Rule 6 & 7: table routing
    table_subject = (vlm_desc.get("table_subject") or "其它").strip()
    if table_subject in TABLE_EXTRACT_FACTS:
        return RouteDecision("extract_facts", f"table_{table_subject}", 0.85)
    if table_subject in TABLE_REGISTER_LAYER1:
        return RouteDecision("register_layer1", f"table_{table_subject}", 0.75)
    # Unknown table_subject — defensive: keep as visual reference
    return RouteDecision("register_layer1", f"table_unknown_subject:{table_subject}", 0.55)


# ---------------------------------------------------------------------------
# Audit log (CSV) — every routing decision is recorded for offline review
# ---------------------------------------------------------------------------
class RouteAuditLog:
    """Append-only CSV at ``output/unit_routing_audit.csv``.

    Columns: unit_id, doc_id, route, reason, description_excerpt.
    """

    HEADER = ["unit_id", "doc_id", "route", "reason", "route_confidence", "description_excerpt"]

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._needs_header = not self.path.exists() or self.path.stat().st_size == 0

    def write(
        self,
        unit_id: str,
        doc_id: str,
        decision: RouteDecision,
        vlm_desc: dict[str, Any] | None,
    ) -> None:
        excerpt = ""
        if vlm_desc:
            excerpt = (vlm_desc.get("description") or "")[:200].replace("\n", " ").replace("\r", " ")
        with self.path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if self._needs_header:
                writer.writerow(self.HEADER)
                self._needs_header = False
            writer.writerow([
                unit_id,
                doc_id,
                decision.route,
                decision.reason,
                f"{decision.confidence:.2f}",
                excerpt,
            ])


# ---------------------------------------------------------------------------
# pending_figure 落盘 — stage 9.5 会读这个 jsonl 合并进 layer1.json
# ---------------------------------------------------------------------------
def write_pending_figure(
    repo: Path,
    doc_id: str,
    unit: Any,
    vlm_desc: dict[str, Any],
    *,
    route_confidence: float | None = None,
) -> Path:
    """Append a FigureHyperedge candidate record to
    ``data/layer1/<doc_id>/pending_figures.jsonl``.

    Stage 9.5 (passage_extractor) will pick these up and merge them into the
    final ``data/layer1/<doc_id>/layer1.json`` alongside passages.

    Returns the jsonl file path.
    """
    out_dir = repo / "data" / "layer1" / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "pending_figures.jsonl"

    # Find the unit's image_path on disk (best-effort)
    unit_dir = repo / "data" / "units" / unit.unit_id
    image_candidates = sorted(unit_dir.glob("image.*"))
    if image_candidates:
        image_path = str(image_candidates[0].relative_to(repo)).replace("\\", "/")
    else:
        # Tables won't have image.png; fall back to None
        image_path = None

    # Combine subtype source: figure uses 'subtype', table uses 'table_subject'
    subtype = vlm_desc.get("subtype") or vlm_desc.get("table_subject") or "other"

    figure_record = {
        "figure_id": f"FIG_{unit.unit_id}",
        "unit_id": unit.unit_id,
        "doc_id": doc_id,
        "page": getattr(unit, "page", 0),
        "subtype": subtype,
        "image_path": image_path,
        "vlm_description": vlm_desc.get("description") or "",
        "caption": getattr(unit, "caption_footnote_text", "") or "",
        "tagged_entities": vlm_desc.get("identified_entities") or [],
        "reference_numerals": vlm_desc.get("reference_numerals") or [],
        "confidence": route_confidence if route_confidence is not None else 0.9,
    }

    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(figure_record, ensure_ascii=False) + "\n")

    return out_path


def delete_unit_folder(repo: Path, unit_id: str) -> bool:
    """Soft-delete: 把 ``data/units/<unit_id>/`` 移到
    ``data/units/_routed_out/<unit_id>/`` (V1.2.7 改动)。

    可逆：router 规则错时能 30 秒移回来。原 rmtree 不可逆是 design smell。

    幂等：源不存在 → True；目标已存在（重跑） → 删除新源（重复数据），保持
    第一次 quarantine 的内容。

    Returns True if quarantined successfully (or src didn't exist), False on error.
    """
    src = repo / "data" / "units" / unit_id
    if not src.exists():
        return True

    dst_root = repo / "data" / "units" / "_routed_out"
    dst_root.mkdir(parents=True, exist_ok=True)
    dst = dst_root / unit_id

    # 重跑 idempotent: dst 已存在就删新源（保留首次 quarantine）
    if dst.exists():
        try:
            shutil.rmtree(src)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "delete_unit_folder: dst exists but failed to remove duplicate src %s: %s",
                src, exc,
            )
            return False

    try:
        shutil.move(str(src), str(dst))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_unit_folder (soft) failed for %s: %s", unit_id, exc)
        return False
