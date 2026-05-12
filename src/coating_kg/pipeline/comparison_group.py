"""把 positive fact 自动绑到对应的 baseline (negative)。

V1.2.2 §修订 4 component B 的实现。

规则:
    - 只有 positive fact 才设 comparison_group。
    - 在同表里找 negative fact 中 ``property`` 一致的；正好一条 → bind，
      否则 → 返回 None (进人审队列)。
"""

from __future__ import annotations

from collections.abc import Iterable

from ..db.models import FactHyperedge


def resolve_comparison_group(
    fact: FactHyperedge,
    table_facts: Iterable[FactHyperedge],
) -> str | None:
    """返回唯一 negative baseline 的 fact_id，或 None。"""
    pol = fact.polarity_hint
    pol_val = pol.value if hasattr(pol, "value") else pol
    if pol_val != "positive":
        return None

    negatives: list[FactHyperedge] = []
    for f in table_facts:
        f_pol = f.polarity_hint
        f_pol_val = f_pol.value if hasattr(f_pol, "value") else f_pol
        if f_pol_val != "negative":
            continue
        if f.property != fact.property:
            continue
        if f.fact_id == fact.fact_id:
            continue
        negatives.append(f)

    if len(negatives) == 1:
        return negatives[0].fact_id
    # 0 个或 2+ 个 → 歧义，进 pending_review
    return None
