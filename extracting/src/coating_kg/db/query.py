"""轻量读端 helper，pipeline + 未来 query 层用。"""

from __future__ import annotations

from psycopg2.extensions import connection as PgConnection


def list_canonical_ids(conn: PgConnection, node_type: str | None = None) -> list[str]:
    """返回 canonical ID，可按 node_type 过滤。

    VLM 客户端用它构 ``candidate_canonical_ids`` 短列表，约束
    identified_entities 输出 (V1.2.2 §修订 3)。
    """
    with conn.cursor() as cur:
        if node_type is None:
            cur.execute("SELECT canonical_id FROM nodes ORDER BY canonical_id")
        else:
            cur.execute(
                "SELECT canonical_id FROM nodes WHERE node_type = %s ORDER BY canonical_id",
                (node_type,),
            )
        return [row[0] for row in cur.fetchall()]


def get_canonical_for_alias(conn: PgConnection, alias_text: str) -> str | None:
    """alias 解析到 canonical_id；先查 must_merge 再查 aliases。"""
    with conn.cursor() as cur:
        cur.execute("SELECT canonical_id FROM must_merge WHERE alias_text = %s", (alias_text,))
        row = cur.fetchone()
        if row is not None:
            return row[0]

        cur.execute(
            """
            SELECT canonical_id FROM aliases
            WHERE alias_text = %s
              AND edge_type IN ('canonical_of','synonym_of','chemical_subtype_of')
            ORDER BY confidence DESC NULLS LAST
            LIMIT 1
            """,
            (alias_text,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def fetch_facts_for_unit(conn: PgConnection, unit_id: str) -> list[dict]:
    """返回 evidence_pointer.region_id 匹配某 unit 的所有 facts。

    给 comparison-group resolver 用，找同表内的兄弟行。
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT fact_id, application, property, polarity_hint,
                   evidence_pointer->>'region_id' AS region_id
            FROM facts
            WHERE evidence_pointer->>'region_id' = %s
            """,
            (unit_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
