"""Yield one ``FigureTableUnit`` per MinerU figure/table block."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterator

from ..db.models import FigureTableUnit
from .section_split import locate_examples_blocks


_LABEL_RE = re.compile(
    r"\b(Tabelle|Table|Tab|Figure|Fig|Scheme)\.?\s*(\d+)",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)


def iter_figure_table_units(
    layout: dict[str, Any],
    *,
    examples_only: bool = True,
    image_root: Path | None = None,
) -> Iterator[FigureTableUnit]:
    """Yield figure/table units from a parsed MinerU layout.

    With ``examples_only=True``, the filter uses the block-level Examples span
    from ``section_split``. If the splitter cannot find a credible span, the
    historical fail-open behaviour is preserved and the whole document is used.
    """

    doc_id: str = layout.get("doc_id", "UNKNOWN")
    data: list[dict[str, Any]] | dict[str, Any] | None = layout.get("data")
    if data is None:
        logger.warning("layout has no 'data' key; was parse_pdf a stub? doc=%s", doc_id)
        return

    blocks = data if isinstance(data, list) else data.get("para_blocks", [])

    examples_block_range: tuple[int, int] | None = None
    if examples_only:
        examples_span = locate_examples_blocks(blocks)
        if examples_span is None or examples_span.start_block is None or examples_span.end_block is None:
            logger.warning(
                "No Examples section found in doc=%s; falling back to whole-doc.",
                doc_id,
            )
        else:
            examples_block_range = (examples_span.start_block, examples_span.end_block)
            logger.info(
                "Examples section doc=%s blocks=%s-%s pages=%s-%s mode=%s confidence=%s",
                doc_id,
                examples_span.start_block,
                examples_span.end_block,
                examples_span.start_page,
                examples_span.end_page,
                examples_span.mode,
                examples_span.confidence,
            )

    table_seq = 0
    fig_seq = 0
    for block_idx, block in enumerate(blocks):
        if examples_block_range is not None and not (
            examples_block_range[0] <= block_idx < examples_block_range[1]
        ):
            continue

        b_type = block.get("type")
        if b_type == "image":
            fig_seq += 1
            yield _build_figure_unit(block, doc_id, block_idx, fig_seq, image_root)
        elif b_type == "equation" and block.get("img_path"):
            fig_seq += 1
            yield _build_figure_unit(
                block,
                doc_id,
                block_idx,
                fig_seq,
                image_root,
                subtype="structure",
            )
        elif b_type == "table":
            table_seq += 1
            table_unit = _build_table_unit(block, doc_id, block_idx, table_seq, image_root)
            if table_unit is not None:
                yield table_unit


def _parse_label(text: str | None, kind: str, seq_fallback: int) -> str:
    """Return a normalized label such as ``Table 1`` / ``Figure 7``."""

    if text:
        m = _LABEL_RE.search(text)
        if m:
            head = m.group(1).lower()
            if head.startswith("fig"):
                prefix = "Figure"
            elif head.startswith("tab"):
                prefix = "Table"
            else:
                prefix = m.group(1).capitalize()
            return f"{prefix} {m.group(2)}"
    return f"{kind} {seq_fallback}"


def _block_text(block: dict[str, Any]) -> str:
    """Best-effort MinerU block text extraction."""

    if "text" in block:
        return str(block["text"])
    if "content" in block and isinstance(block["content"], str):
        return block["content"]
    return ""


def _as_text(value: Any) -> str | None:
    """Normalize MinerU caption/footnote values into one trimmed string."""

    if value is None:
        return None
    if isinstance(value, list):
        joined = " ".join(s for s in value if s).strip()
        return joined or None
    s = str(value).strip()
    return s or None


def _build_figure_unit(
    block: dict[str, Any],
    doc_id: str,
    block_idx: int,
    fig_seq: int,
    image_root: Path | None,
    *,
    subtype: str | None = None,
) -> FigureTableUnit:
    page = int(block.get("page_idx", 0)) + 1
    caption_text = _as_text(block.get("img_caption") or block.get("image_caption"))
    footnote_text = _as_text(block.get("img_footnote") or block.get("image_footnote"))
    region_id = _parse_label(caption_text, kind="Figure", seq_fallback=fig_seq)
    img_path = block.get("img_path")
    if image_root and img_path:
        img_path = str((image_root / img_path).resolve())
    caption = " ".join(filter(None, [caption_text, footnote_text])) or None

    return FigureTableUnit(
        unit_id=f"U_{doc_id}_p{page}_b{block_idx}",
        unit_type="figure",
        doc_id=doc_id,
        page=page,
        region_id=region_id,
        image_path=img_path,
        caption_footnote_text=caption,
        figure_subtype=subtype,  # type: ignore[arg-type]
        bbox=_extract_bbox(block),
    )


def _build_table_unit(
    block: dict[str, Any],
    doc_id: str,
    block_idx: int,
    table_seq: int,
    image_root: Path | None,
) -> FigureTableUnit | None:
    page = int(block.get("page_idx", 0)) + 1
    caption_text = _as_text(block.get("table_caption"))
    footnote_text = _as_text(block.get("table_footnote"))
    region_id = _parse_label(caption_text, kind="Table", seq_fallback=table_seq)
    caption = " ".join(filter(None, [caption_text, footnote_text])) or None
    table_html = block.get("table_body") or block.get("html") or block.get("text")
    table_html_text = _as_text(table_html)
    img_path = block.get("img_path")
    if image_root and img_path:
        img_path = str((image_root / img_path).resolve())

    if not table_html_text and not img_path:
        logger.warning(
            "doc=%s table block %s has no table_body/html/text/img_path; skipping.",
            doc_id,
            block_idx,
        )
        return None
    if not table_html_text:
        logger.warning(
            "doc=%s table block %s has no table_body/html/text; keeping image-only table.",
            doc_id,
            block_idx,
        )

    return FigureTableUnit(
        unit_id=f"U_{doc_id}_p{page}_b{block_idx}",
        unit_type="table",
        doc_id=doc_id,
        page=page,
        region_id=region_id,
        image_path=img_path,
        caption_footnote_text=caption,
        extracted_table_html=table_html_text,
        bbox=_extract_bbox(block),
    )


def _extract_bbox(block: dict[str, Any]) -> list[float] | None:
    """Extract MinerU ``bbox = [x1, y1, x2, y2]`` when available."""

    bbox = block.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        return [float(x) for x in bbox]
    except (TypeError, ValueError):
        return None
