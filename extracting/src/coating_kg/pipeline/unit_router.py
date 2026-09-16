"""Stage 4.5 — unit router (three-way 分流).

Routes each unit (figure or table) to one of three downstream paths based on
stage 4 VLM output. Pure-rule routing, no LLM call. Reuses the
``vlm_description.json`` we already paid for to make a free decision and
truncate ~50% of downstream stage 6/7 LLM calls per batch.

Routes:
  - "extract_facts":    proceed to stage 6/7 (L2 Structured Facts)
  - "register_layer1":  run stage 6 for related paragraphs, then skip stage 7/8
                        and register an L1 Retrieval Context figure candidate
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
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

logger = logging.getLogger("coating_kg.unit_router")

Route = Literal["extract_facts", "register_layer1", "delete"]


# ---------------------------------------------------------------------------
# Routing rules — closed enums from stage 4 prompt outputs
# ---------------------------------------------------------------------------

# Figure subtype (vlm_figure.txt closed enum, lowercase English)
FIGURE_REGISTER_LAYER1: set[str] = {
    "structure",   # chemical structural formula
    "scheme",      # reaction / synthesis scheme
    "plot",        # numerical plot — v1 keep as L1 context; v2 may upgrade to extract_facts
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
    "其它",  # ambiguous tables — keep as L1 Retrieval Context visual reference
}

# qwen3.6 table-image recovery emits English labels such as
# "performance comparison" / "formulation" / "test condition" rather than the
# legacy Chinese closed set above. Route those as fact-bearing tables.
TABLE_EXTRACT_FACT_KEYWORDS: set[str] = {
    "performance",
    "property",
    "properties",
    "result",
    "results",
    "evaluation",
    "rating",
    "formulation",
    "composition",
    "recipe",
    "ingredient",
    "condition",
    "conditions",
    "test",
    "mixed",
    "comparison",
    "comparative",
}
TABLE_REGISTER_LAYER1_KEYWORDS: set[str] = {
    "structure/reaction",
    "reaction",
    "structure",
    "scheme",
    "other",
    "unknown",
}

# Description-level low-info threshold (logo / decoration detection)
SKIP_DESC_LEN_THRESHOLD = 30


@dataclass
class RouteDecision:
    route: Route
    reason: str
    confidence: float = 0.0
    normalized_subject: str | None = None


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

    table_subject = (vlm_desc.get("table_subject") or "").strip()
    table_type = (vlm_desc.get("table_type") or "").strip()
    normalized_subject = normalize_table_subject(table_subject, table_type, description)
    table_route = _classify_table_route(normalized_subject, table_subject, table_type, description)
    if table_route == "delete":
        return RouteDecision("delete", f"table_{normalized_subject}", 0.80, normalized_subject)
    if table_route == "extract_facts":
        return RouteDecision(
            "extract_facts",
            f"table_{normalized_subject or table_subject or table_type or 'inferred'}",
            0.85 if normalized_subject not in {"other", ""} else 0.60,
            normalized_subject,
        )
    return RouteDecision(
        "register_layer1",
        f"table_{normalized_subject or table_subject or table_type or 'other'}",
        0.72 if normalized_subject == "structure_or_scheme" else 0.55,
        normalized_subject,
    )
# ---------------------------------------------------------------------------
# Audit log (CSV) — every routing decision is recorded for offline review
# ---------------------------------------------------------------------------
def normalize_table_subject(table_subject: str, table_type: str = "", description: str = "") -> str:
    """Normalize legacy Chinese/free-text labels into the English closed enum."""
    raw = (table_subject or "").strip().casefold()
    compact = raw.replace("-", "_").replace("/", "_").replace(" ", "_")
    aliases = {
        "performance_comparison": "performance_comparison",
        "performance": "performance_comparison",
        "performancecomparison": "performance_comparison",
        "property": "performance_comparison",
        "property_comparison": "performance_comparison",
        "result": "performance_comparison",
        "results": "performance_comparison",
        "evaluation": "performance_comparison",
        "formulation": "formulation",
        "formulation_comparison": "formulation",
        "composition": "formulation",
        "recipe": "formulation",
        "test_condition": "test_condition",
        "condition": "test_condition",
        "conditions": "test_condition",
        "test": "test_condition",
        "mixed": "mixed",
        "structure_or_scheme": "structure_or_scheme",
        "structure_reaction": "structure_or_scheme",
        "structure": "structure_or_scheme",
        "reaction": "structure_or_scheme",
        "scheme": "structure_or_scheme",
        "other": "other",
        "unknown": "other",
        "noise_or_reference": "noise_or_reference",
        "search_report": "noise_or_reference",
        "citation": "noise_or_reference",
        "barcode": "noise_or_reference",
    }
    legacy = {
        "鎬ц兘瀵规瘮": "performance_comparison",
        "閰嶆柟瀵规瘮": "formulation",
        "娴嬭瘯鏉′欢": "test_condition",
        "鍏跺畠": "other",
    }
    if table_subject in legacy:
        return legacy[table_subject]
    if compact in aliases:
        return aliases[compact]

    combined = " ".join([raw, table_type, description]).casefold()
    if any(k in combined for k in ("search report", "citation", "barcode", "bibliographic")):
        return "noise_or_reference"
    if any(k in combined for k in ("surface", "rating", "property", "result", "performance", "gloss", "adhesion")):
        return "performance_comparison"
    if any(k in combined for k in ("formulation", "composition", "ingredient", "wt%", "parts", "recipe")):
        return "formulation"
    if any(k in combined for k in ("condition", "temperature", "time", "method", "test")):
        return "test_condition"
    if any(k in combined for k in ("structure", "reaction", "scheme", "substituent")):
        return "structure_or_scheme"
    return "other"


def _classify_table_route(
    normalized_subject: str,
    table_subject: str,
    table_type: str,
    description: str,
) -> Route:
    """Route table outputs from both legacy prompts and qwen3.6 English labels."""
    if normalized_subject in {"performance_comparison", "formulation", "test_condition", "mixed"}:
        return "extract_facts"
    if normalized_subject == "noise_or_reference":
        return "delete"
    if normalized_subject == "structure_or_scheme":
        return "register_layer1"
    if table_subject in TABLE_EXTRACT_FACTS:
        return "extract_facts"
    if table_subject in TABLE_REGISTER_LAYER1 and not table_type and not description:
        return "register_layer1"

    combined = " ".join([table_subject, table_type, description]).casefold()
    if any(keyword in combined for keyword in TABLE_EXTRACT_FACT_KEYWORDS):
        return "extract_facts"
    if any(keyword in combined for keyword in TABLE_REGISTER_LAYER1_KEYWORDS):
        return "register_layer1"

    # Preserve recall for tables: Stage 7 can still decline non-result cells via
    # semantic coverage, but a router false negative drops facts entirely.
    return "extract_facts"


class RouteAuditLog:
    """Append-only CSV at ``output/unit_routing_audit.csv``.

    Columns: unit_id, doc_id, route, reason, description_excerpt.
    """

    HEADER = [
        "unit_id",
        "doc_id",
        "route",
        "reason",
        "route_confidence",
        "normalized_subject",
        "description_excerpt",
    ]

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

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
        with _file_lock(self.lock_path):
            needs_header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if needs_header:
                    writer.writerow(self.HEADER)
                writer.writerow([
                    unit_id,
                    doc_id,
                    decision.route,
                    decision.reason,
                    f"{decision.confidence:.2f}",
                    decision.normalized_subject or "",
                    excerpt,
                ])


def write_route_decision(
    repo: Path,
    unit_id: str,
    doc_id: str,
    decision: RouteDecision,
    vlm_desc: dict[str, Any] | None,
) -> Path:
    """Persist one route decision next to the unit for QA/debugging."""
    folder = repo / "data" / "units" / unit_id
    folder.mkdir(parents=True, exist_ok=True)
    out_path = folder / "route_decision.json"
    payload = {
        "unit_id": unit_id,
        "doc_id": doc_id,
        "route": decision.route,
        "reason": decision.reason,
        "route_confidence": decision.confidence,
        "normalized_subject": decision.normalized_subject,
        "vlm_table_subject": (vlm_desc or {}).get("table_subject"),
        "vlm_subtype": (vlm_desc or {}).get("subtype"),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


@contextmanager
def _file_lock(lock_path: Path, timeout_s: float = 30.0) -> Iterator[None]:
    """Small cross-process lock based on atomic file creation.

    This keeps audit CSV appends safe on Windows without adding dependencies.
    Stale lock files are removed after ``timeout_s`` so a crashed process does
    not block the whole batch forever.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
        except FileExistsError:
            if time.monotonic() - start > timeout_s:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                start = time.monotonic()
            else:
                time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# pending_figure 落盘 — stage 9.5 会读这个 jsonl 合并进 layer1.json
# ---------------------------------------------------------------------------
def write_pending_figure(
    repo: Path,
    doc_id: str,
    unit: Any,
    vlm_desc: dict[str, Any],
    related_paragraphs: list[dict[str, Any]] | None = None,
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

    # Find the unit's rendered image on disk (best-effort). Table units also
    # have table.html, so only accept image suffixes.
    unit_dir = repo / "data" / "units" / unit.unit_id
    image_suffixes = {".png", ".jpg", ".jpeg", ".webp"}
    image_candidates = [
        path for pattern in ("image.*", "table.*")
        for path in sorted(unit_dir.glob(pattern))
        if path.suffix.casefold() in image_suffixes
    ]
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
        "chemical_candidates": _sanitize_chemical_candidates(
            vlm_desc.get("chemical_candidates") or [],
        ),
        "related_paragraphs": related_paragraphs or [],
        "confidence": route_confidence if route_confidence is not None else 0.9,
    }

    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(figure_record, ensure_ascii=False) + "\n")

    return out_path


def _sanitize_chemical_candidates(candidates: Any) -> list[dict[str, Any]]:
    """Keep structure/scheme hints useful while blocking precise hallucinations."""
    if not isinstance(candidates, list):
        return []

    sanitized: list[dict[str, Any]] = []
    precise_types = {"molecule"}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        candidate_type = str(candidate.get("candidate_type") or "unknown").strip().lower()
        needs_validation = bool(candidate.get("needs_validation", True))
        if candidate_type not in precise_types:
            candidate["smiles"] = None
            if candidate_type in {
                "polymer_repeat_unit",
                "resin_system",
                "reaction_product",
                "mixture",
                "oligomer",
                "unknown",
            }:
                candidate.setdefault("formula", None)
        if needs_validation:
            try:
                confidence = float(candidate.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            candidate["confidence"] = min(confidence, 0.85)
        sanitized.append(candidate)
    return sanitized


def clear_pending_figures(repo: Path, doc_id: str) -> None:
    """Remove stale per-doc pending figures before re-ingesting one PDF."""
    out_path = repo / "data" / "layer1" / doc_id / "pending_figures.jsonl"
    try:
        out_path.unlink()
    except FileNotFoundError:
        return


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
