"""给 matcher 构建候选段落列表。

输入是已解析的 MinerU layout，对外两个 helper:

- :func:`examples_page_range` — 把 ``section_split`` 的字符 offset span
  转成 ``(start_page, end_page)`` 元组，按 ``page_idx`` 过滤 block
  (避免 stage-2 cursor 错位 — 见 task #7)。
- :func:`gather_examples_paragraphs` — 返回该 page 范围内有实质内容的
  ``text`` block，去掉 running header / page number / 编号小节标题。

之前内联在 ``scripts/run_poc2.py`` 里；提到 pipeline 让 CLI ingest 路径和
PoC 脚本共用一份源。
"""

from __future__ import annotations

import re
from typing import Any

from .section_split import split_examples_section
from .unit_extractor import _block_text


# "WO 2026/077939" 页眉、纯数字 / 纯大写、裸章节标签
_NOISE_RE = re.compile(
    r"^(?:WO\s*\d{4}/\d+|[\d\s.]+|[A-Z]+|EXAMPLES|CLAIMS)$",
    re.IGNORECASE,
)
# 编号小节标题: "1." / "1.3" / "3.5.1" 后跟 Title-Case 短语。
# block 短的时候丢 — 纯导航无可抽内容。
_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+[A-Z][^.]{0,80}$")


def examples_page_range(layout: dict[str, Any]) -> tuple[int, int] | None:
    """返回 Examples 章节的 ``(start_page, end_page)`` (1-indexed)，
    没找到 Examples 标题则 ``None``。
    """
    blocks = layout.get("data") or []
    full_text = "\n".join(_block_text(b) for b in blocks if _block_text(b))
    span = split_examples_section(full_text)
    if span is None:
        return None
    cursor = 0
    start_page = end_page = None
    for b in blocks:
        t = _block_text(b)
        if not t:
            continue
        bstart = cursor
        page = int(b.get("page_idx", 0)) + 1
        if start_page is None and bstart >= span[0]:
            start_page = page
        if bstart < span[1]:
            end_page = page
        cursor = bstart + len(t) + 1
    if start_page is None or end_page is None:
        return None
    return (start_page, end_page)


def gather_examples_paragraphs(
    layout: dict[str, Any],
    page_range: tuple[int, int],
    *,
    min_chars: int = 30,
    heading_max_chars: int = 100,
) -> list[dict[str, Any]]:
    """返回 ``[{para_id, page, text}, ...]`` —— 有实质内容的 text block。

    丢掉:
      - < ``min_chars`` 字符的 block
      - running header / page number / "EXAMPLES" / "CLAIMS" 标签
      - < ``heading_max_chars`` 字符的编号小节标题
        (``"1.3 Preparation of clearcoat compositions"``)
    """
    out: list[dict[str, Any]] = []
    p_lo, p_hi = page_range
    for i, b in enumerate(layout.get("data") or []):
        if b.get("type") != "text":
            continue
        page = int(b.get("page_idx", 0)) + 1
        if not (p_lo <= page <= p_hi):
            continue
        text = (b.get("text") or "").strip()
        if not text or len(text) < min_chars:
            continue
        if _NOISE_RE.match(text):
            continue
        if len(text) < heading_max_chars and _HEADING_RE.match(text):
            continue
        out.append({"para_id": i, "page": page, "text": text})
    return out
