"""Consistency rules enforced before writing alias / fact rows.

Per V1.2.2: ``forbidden_merge > must_merge > synonym_of`` — i.e. if A and B
are forbidden to merge, no alias / synonym / merge edge between them is
allowed.
"""

from __future__ import annotations

from psycopg2.extensions import connection as PgConnection


def check_no_forbidden_merge(
    canonical_a: str,
    canonical_b: str,
    conn: PgConnection,
) -> bool:
    """Return True iff merging A and B is allowed.

    The forbidden_merge table is symmetric — we check both orderings even
    though insert_forbidden_merge stores (entity_a, entity_b) sorted.
    """
    if canonical_a == canonical_b:
        return True
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM forbidden_merge
            WHERE (entity_a = %s AND entity_b = %s)
               OR (entity_a = %s AND entity_b = %s)
            LIMIT 1
            """,
            (canonical_a, canonical_b, canonical_b, canonical_a),
        )
        return cur.fetchone() is None


def explain_forbidden_merge(
    canonical_a: str,
    canonical_b: str,
    conn: PgConnection,
) -> tuple[str, str] | None:
    """Return (reason_type, explanation) if A,B are forbidden to merge."""
    if canonical_a == canonical_b:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT reason_type, explanation FROM forbidden_merge
            WHERE (entity_a = %s AND entity_b = %s)
               OR (entity_a = %s AND entity_b = %s)
            LIMIT 1
            """,
            (canonical_a, canonical_b, canonical_b, canonical_a),
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else None
