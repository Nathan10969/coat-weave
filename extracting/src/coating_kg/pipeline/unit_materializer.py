"""把每个 FigureTableUnit 物化到独立目录。

PoC-1 脚手架：``unit_extractor`` 出的每个 unit 在 ``<root>/<unit_id>/`` 下有
自己的目录，含 unit 输入 (meta, caption, 源图 / table HTML)。后续 pipeline
步骤 (paragraph match, VLM describe, fact extract) 把文件**追加**到同目录，
unit 完整 input/output 留痕在一处 — 调 prompt 时方便审。

本模块不调 API、不碰数据库。
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from ..db.models import FigureTableUnit

logger = logging.getLogger(__name__)


def _resolve_image(unit: FigureTableUnit, mineru_root: Path | None) -> Path | None:
    """返回 ``unit.image_path`` 的存在路径，找不到返回 None。

    MinerU 的 ``img_path`` 可能是绝对（已经过 ``image_root`` 替换）或相对于
    doc 的 MinerU 输出目录；都试一下。
    """
    if not unit.image_path:
        return None
    p = Path(unit.image_path)
    if p.is_absolute() and p.exists():
        return p
    if mineru_root is not None:
        candidate = (Path(mineru_root) / unit.doc_id / unit.image_path).resolve()
        if candidate.exists():
            return candidate
    return None


def materialize_unit(
    unit: FigureTableUnit,
    root: Path,
    *,
    mineru_root: Path | None = None,
) -> Path:
    """写到 ``<root>/<unit_id>/`` 并返回目录路径。

    总是覆盖 — 重跑产出干净快照。MinerU 源图是**复制**不是移动。
    """
    folder = Path(root) / unit.unit_id
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)

    # 1. meta.json — 物化时的 unit 元数据
    (folder / "meta.json").write_text(
        json.dumps(
            unit.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 2. caption.txt — caption + footnote 合并文本
    if unit.caption_footnote_text:
        (folder / "caption.txt").write_text(
            unit.caption_footnote_text, encoding="utf-8"
        )

    # 3. image.<ext> (仅 figure) — 从 MinerU 输出复制过来
    if unit.unit_type == "figure":
        src = _resolve_image(unit, mineru_root)
        if src is not None:
            ext = src.suffix or ".png"
            shutil.copy2(src, folder / f"image{ext}")
        elif unit.image_path:
            logger.warning(
                "unit %s: image_path %s could not be resolved (mineru_root=%s)",
                unit.unit_id, unit.image_path, mineru_root,
            )
    elif unit.unit_type == "table":
        src = _resolve_image(unit, mineru_root)
        if src is not None:
            ext = src.suffix or ".png"
            shutil.copy2(src, folder / f"table{ext}")
        elif unit.image_path:
            logger.warning(
                "unit %s: table image_path %s could not be resolved (mineru_root=%s)",
                unit.unit_id, unit.image_path, mineru_root,
            )

    # 4. table.html (仅 table) — 抽出来的原始 HTML
    if unit.unit_type == "table" and unit.extracted_table_html:
        (folder / "table.html").write_text(
            unit.extracted_table_html, encoding="utf-8"
        )

    return folder
