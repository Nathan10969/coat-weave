"""Resolve a free-text alias to a canonical_id.

Resolution order (V1.2.2 conflict priority):
    1. ``forbidden_merge`` short-circuit (caller decides; the resolver only
       reports conflicts via ``check_no_forbidden_merge`` in
       ``consistency_check.py``)
    2. ``must_merge`` (alias_text → canonical_id, force-mapped on ingest)
    3. ``aliases`` (canonical_of / synonym_of / chemical_subtype_of, highest
       confidence wins)
    4. exact ``nodes.canonical_name`` match
    5. None
"""

from __future__ import annotations

from psycopg2.extensions import connection as PgConnection


def resolve(alias_text: str, conn: PgConnection) -> str | None:
    """Return canonical_id for ``alias_text`` or None if unknown."""
    if not alias_text:
        return None

    with conn.cursor() as cur:
        # 1. must_merge — overrides everything else
        cur.execute(
            "SELECT canonical_id FROM must_merge WHERE alias_text = %s",
            (alias_text,),
        )
        row = cur.fetchone()
        if row:
            return row[0]

        # 2. aliases (best confidence first)
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
        if row:
            return row[0]

        # 3. fall back to direct canonical_name / canonical_id match
        cur.execute(
            """
            SELECT canonical_id FROM nodes
            WHERE canonical_id = %s OR canonical_name = %s OR chinese_name = %s
            LIMIT 1
            """,
            (alias_text, alias_text, alias_text),
        )
        row = cur.fetchone()
        return row[0] if row else None


def resolve_many(aliases: list[str], conn: PgConnection) -> dict[str, str | None]:
    """Convenience batch resolver — returns a dict alias → canonical_id."""
    return {a: resolve(a, conn) for a in aliases}
