"""遍历 MinerU layout JSON，每个 figure / table 出一个 ``FigureTableUnit``。

骨架 — 依赖 MinerU 输出格式（W2.1 锁定）。下面 TODO 标的是格式相关代码位置。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterator

from ..db.models import FigureTableUnit
from .section_split import split_examples_section


# 匹配 "Table 5" / "Tab. 5" / "Tabelle 5" / "Fig. 7" / "Figure 7" / "Scheme 2"。
# 大小写不敏感，允许可选点/冒号，捕获后面的整数。
_LABEL_RE = re.compile(r"\b(Tabelle|Table|Tab|Figure|Fig|Scheme)\.?\s*(\d+)", re.IGNORECASE)


def _parse_label(text: str | None, kind: str, seq_fallback: int) -> str:
    """返回干净的引用标签，如 ``"Table 1"`` / ``"Figure 7"``。

    匹配不到 pattern 时回退到 ``f"{kind} {seq_fallback}"`` —
    假设 MinerU 按文档顺序检测 figure/table 不漏；否则 caption 缺失的 unit 标签会差 1。
    """
    if text:
        m = _LABEL_RE.search(text)
        if m:
            head = m.group(1).lower()
            if head.startswith("fig"):
                prefix = "Figure"
            elif head.startswith("tab"):  # tabelle / table / tab
                prefix = "Table"
            else:
                prefix = m.group(1).capitalize()
            return f"{prefix} {m.group(2)}"
    return f"{kind} {seq_fallback}"

logger = logging.getLogger(__name__)


def iter_figure_table_units(
    layout: dict[str, Any],
    *,
    examples_only: bool = True,
    image_root: Path | None = None,
) -> Iterator[FigureTableUnit]:
    """从已解析的 MinerU layout 流出 ``FigureTableUnit``。

    Args:
        layout: ``pdf_layout.parse_pdf`` 的结果
        examples_only: True (默认) 时按 V1.2.2 §修订 1 限定 Examples 章节内的元素。
        image_root: MinerU 切图存放的根目录。
    """
    doc_id: str = layout.get("doc_id", "UNKNOWN")
    data: list[dict[str, Any]] | dict[str, Any] | None = layout.get("data")
    if data is None:
        logger.warning("layout has no 'data' key — was parse_pdf a stub? doc=%s", doc_id)
        return

    # TODO(W2.1): MinerU content_list.json 是 flat block 列表，每个含
    #             {type: 'text'|'image'|'table', page_idx, bbox, ...}。
    #             跟实际安装的 magic-pdf 字段名对齐。
    blocks = data if isinstance(data, list) else data.get("para_blocks", [])

    examples_span: tuple[int, int] | None = None
    if examples_only:
        # 拼成纯文本喂给 section-splitter
        full_text = "\n".join(_block_text(b) for b in blocks if _block_text(b))
        examples_span = split_examples_section(full_text)
        if examples_span is None:
            logger.warning(
                "No Examples section found in doc=%s; falling back to whole-doc.", doc_id,
            )

    cursor = 0  # 在 full_text 视图内的位置游标
    table_seq = 0
    fig_seq = 0
    for block_idx, block in enumerate(blocks):
        b_type = block.get("type")
        b_text = _block_text(block) or ""
        # 算这个 block 在 full_text 视图内的 [start, end) span
        start = cursor
        end = cursor + len(b_text)
        cursor = end + 1  # 加 1 是给 join 用的 "\n"

        if examples_span is not None and not (examples_span[0] <= start <= examples_span[1]):
            continue

        if b_type == "image":
            fig_seq += 1
            yield _build_figure_unit(block, doc_id, block_idx, fig_seq, image_root)
        elif b_type == "equation" and block.get("img_path"):
            # MinerU 的 "equation" block 在涂料专利里承载化学结构 / scheme 图 —
            # 按设计 §7.3 当作 figure subtype='structure' 输出
            fig_seq += 1
            yield _build_figure_unit(
                block, doc_id, block_idx, fig_seq, image_root, subtype="structure",
            )
        elif b_type == "table":
            table_seq += 1
            table_unit = _build_table_unit(block, doc_id, block_idx, table_seq)
            if table_unit is not None:
                yield table_unit


def _block_text(block: dict[str, Any]) -> str:
    """从 MinerU block 抽文本（结构不固定，best-effort）。"""
    if "text" in block:
        return str(block["text"])
    if "content" in block and isinstance(block["content"], str):
        return block["content"]
    return ""


def _as_text(value: Any) -> str | None:
    """规一化 MinerU caption/footnote — 观察到是 ``list[str]`` per block —
    合成单个 trimmed 字符串，空则 None。
    """
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
    page = int(block.get("page_idx", 0)) + 1  # MinerU 0-indexed
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
        bbox=_extract_bbox(block),  # ★ V1.2.5
    )


def _build_table_unit(
    block: dict[str, Any], doc_id: str, block_idx: int, table_seq: int,
) -> FigureTableUnit | None:
    page = int(block.get("page_idx", 0)) + 1
    caption_text = _as_text(block.get("table_caption"))
    footnote_text = _as_text(block.get("table_footnote"))
    region_id = _parse_label(caption_text, kind="Table", seq_fallback=table_seq)
    caption = " ".join(filter(None, [caption_text, footnote_text])) or None
    table_html = block.get("table_body") or block.get("html") or block.get("text")
    if not _as_text(table_html):
        logger.warning(
            "doc=%s table block %s has no table_body/html/text; skipping.",
            doc_id, block_idx,
        )
        return None

    return FigureTableUnit(
        unit_id=f"U_{doc_id}_p{page}_b{block_idx}",
        unit_type="table",
        doc_id=doc_id,
        page=page,
        region_id=region_id,
        caption_footnote_text=caption,
        extracted_table_html=table_html,
        bbox=_extract_bbox(block),  # ★ V1.2.5
    )


def _extract_bbox(block: dict[str, Any]) -> list[float] | None:
    """从 content_list block 抽 MinerU bbox = [x1, y1, x2, y2]。

    V1.2.5 spec §7.2：evidence_pointer.bbox 让 demo UI 在原 PDF 上高亮区域。
    MinerU 输出绝对页面坐标，这里保持绝对坐标。渲染器需要时再除以页宽/页高。

    Block 没可用 bbox 时返回 None。
    """
    bbox = block.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        return [float(x) for x in bbox]
    except (TypeError, ValueError):
        return None
