"""在专利正文里定位 Examples 章节。

V1.2.2 红线：只抽 Examples 章节**内**的 figure / table (V1.2.2 §修订 1)。

支持英文 / 中文 / 德文章节标记。返回 (start, end) 字符 offset，找不到 None。
"""

from __future__ import annotations

import re
from typing import Final

# 类标题 Examples 标记（英文不区分大小写）。
# 同语言内多匹配时取最早一个；跨语言也总取**最早**。
_EXAMPLES_PATTERNS: Final[list[re.Pattern[str]]] = [
    # 英文 — 必须独占一行或后跟标点/换行
    re.compile(r"(?im)^[ \t]*(?:examples|embodiments)\b[ \t]*[:.]?[ \t]*$"),
    # 英文带编号变体: "1. EXAMPLES"
    re.compile(r"(?im)^[ \t]*\d+[.)][ \t]*(?:examples|embodiments)\b[ \t]*$"),
    # 中文 — 实施例 / 具体实施例 / 实施例：
    re.compile(r"(?m)^[ \t]*(?:具体)?实施例[\s::]?[ \t]*$"),
    # 德文 — BEISPIELE / Beispiele
    re.compile(r"(?im)^[ \t]*beispiele\b[ \t]*[:.]?[ \t]*$"),
]

# 关闭 Examples 章节的标记（下一个 H1 级标题）
_END_PATTERNS: Final[list[re.Pattern[str]]] = [
    re.compile(r"(?im)^[ \t]*claims\b[ \t]*[:.]?[ \t]*$"),
    re.compile(r"(?im)^[ \t]*what is claimed\b"),
    re.compile(r"(?im)^[ \t]*industrial applicability\b"),
    re.compile(r"(?im)^[ \t]*abstract\b[ \t]*[:.]?[ \t]*$"),
    re.compile(r"(?m)^[ \t]*权利要求[\s::]?[ \t]*$"),
    re.compile(r"(?m)^[ \t]*工业实用性[\s::]?[ \t]*$"),
    re.compile(r"(?im)^[ \t]*patentansprüche\b"),
    # Comparative-examples 标题在 Examples **内**，不算关闭。
    # 全大写下一个 H1 (上面都不匹配的) 也算关闭：
    re.compile(r"(?m)^[ \t]*[A-Z][A-Z \t]{4,}$"),
]


def split_examples_section(text: str) -> tuple[int, int] | None:
    """返回 Examples 章节的 (start_idx, end_idx)，没有则 None。

    ``end_idx`` 为开区间 — ``text[start:end]`` 切出章节内容。
    切片**包含**章节标题；下游觉得不需要可以自己剥。
    """
    if not text:
        return None

    # 1. 找最早的 start
    starts = [m.start() for p in _EXAMPLES_PATTERNS for m in p.finditer(text)]
    if not starts:
        return None
    start_idx = min(starts)

    # 2. 在 start 之后找第一个 end-pattern；忽略 start 自己
    body = text[start_idx:]
    end_offsets: list[int] = []
    # 跳过第一行 (Examples 标题本身)，避免全大写 heading regex 命中它
    first_newline = body.find("\n")
    search_from = first_newline + 1 if first_newline != -1 else len(body)

    for p in _END_PATTERNS:
        for m in p.finditer(body, search_from):
            end_offsets.append(m.start())
            break  # 每 pattern 取最早

    end_idx = start_idx + min(end_offsets) if end_offsets else len(text)
    return (start_idx, end_idx)


def extract_examples_text(text: str) -> str | None:
    """方便函数 — 返回 Examples 切片或 None。"""
    span = split_examples_section(text)
    if span is None:
        return None
    return text[span[0] : span[1]]
