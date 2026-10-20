"""把 table 行标签判为 inventive (positive) 或 comparative (negative)。

V1.2.2 §修订 4 component A 的实现。

优先级:
    1. VLM ``row_polarity`` hint (是 "positive" 或 "negative")
    2. 反义关键词子串匹配
    3. 正义关键词子串匹配
    4. 全 fall-through → "unknown" (进 pending_review 队列)
"""

from __future__ import annotations

from typing import Final, Literal

Polarity = Literal["positive", "negative", "unknown"]

# 顺序：长 / 更具体的短语在前，e.g. "comparative" 在裸 "comp" 前命中。
_NEGATIVE_KEYWORDS: Final[tuple[str, ...]] = (
    "comparative",
    "vergleichsbeispiel",
    "vergleich",
    "reference",
    "对比例",
    "对比",
    "比較例",
    "比較",
    # 单 token "comp" 必须在 "comparative" 之**后** (子串搜索)
    "comp",
)

_POSITIVE_KEYWORDS: Final[tuple[str, ...]] = (
    "embodiment",
    "example",
    "实施例",
    "beispiel",
)


def classify_polarity(row_label: str, vlm_hint: str | None = None) -> Polarity:
    """返回 ``positive`` / ``negative`` / ``unknown`` 之一。

    Args:
        row_label: e.g. "Comp Example C1", "Example E4", "对比例 1"
        vlm_hint:  VLM ``row_polarity`` 字段的可选输出

    刻意用子串匹配（非词边界）—— table 行标签经常拼一起 ("CompExampleC1")。
    """
    if vlm_hint in {"positive", "negative"}:
        return vlm_hint  # type: ignore[return-value]

    if not row_label:
        return "unknown"
    label = row_label.lower()

    for kw in _NEGATIVE_KEYWORDS:
        if kw in label:
            return "negative"
    for kw in _POSITIVE_KEYWORDS:
        if kw in label:
            return "positive"
    return "unknown"
