"""按子串匹配把 free-text 片段打上 canonical_id。

VLM 出描述后用作第二道兜底（catch VLM 漏的 entity），同时用于填
``figure_table_units.tagged_entities`` (V1.2.2 §修订 2)。
"""

from __future__ import annotations

from typing import Iterable

from psycopg2.extensions import connection as PgConnection


def tag_entities(
    text: str,
    candidate_ids: Iterable[str] | None = None,
    *,
    conn: PgConnection | None = None,
) -> list[str]:
    """返回 ``text`` 里出现的 canonical_id 子集（按 alias / canonical_name 匹配）。

    两种模式:
      - 给了 ``candidate_ids`` 就只查这些（快路径，VLM 已先筛过时用）。
      - 给了 ``conn`` 就从 DB 拉 alias + canonical_name。

    匹配大小写不敏感，子串匹配 — V1 够用，V2 再升 span 感知。
    """
    if not text:
        return []
    haystack = text.lower()

    aliases: list[tuple[str, str]] = []  # (alias_text, canonical_id)

    if candidate_ids is not None:
        for cid in candidate_ids:
            aliases.append((cid, cid))
            # canonical id 有可识别后缀时（如 MAT_HDI_trimer），也试它的尾段
            tail = cid.split("_", 1)[1] if "_" in cid else cid
            if tail and tail != cid:
                aliases.append((tail, cid))

    if conn is not None:
        with conn.cursor() as cur:
            cur.execute("SELECT canonical_name, canonical_id FROM nodes")
            aliases.extend((r[0], r[1]) for r in cur.fetchall())
            cur.execute(
                "SELECT alias_text, canonical_id FROM aliases "
                "WHERE edge_type IN ('canonical_of','synonym_of','chemical_subtype_of')"
            )
            aliases.extend((r[0], r[1]) for r in cur.fetchall())
            cur.execute("SELECT alias_text, canonical_id FROM must_merge")
            aliases.extend((r[0], r[1]) for r in cur.fetchall())

    found: dict[str, None] = {}  # 有序 set
    for alias_text, canonical_id in aliases:
        if not alias_text:
            continue
        if alias_text.lower() in haystack:
            found.setdefault(canonical_id, None)
    return list(found.keys())
