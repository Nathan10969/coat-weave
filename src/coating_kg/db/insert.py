"""薄薄的 INSERT helper — 一表一函数。

刻意保持精简：参数化 SQL + ``ON CONFLICT DO NOTHING`` 让 ingest 流程重跑幂等。
"""

from __future__ import annotations

import json
from typing import Iterable

from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import Json

from .models import (
    Alias,
    FactHyperedge,
    FigureTableUnit,
    ForbiddenMerge,
    MustMerge,
    Node,
    Patent,
    PropertyRegistryEntry,
)


# ---- helpers ---------------------------------------------------------
def _json_or_none(v: object) -> object:
    return Json(v) if v is not None else None


# ---- patents ---------------------------------------------------------
def insert_patent(conn: PgConnection, patent: Patent) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO patents (doc_id, title, applicant, inventor, filing_date,
                priority_date, publication_date, ipc_codes, abstract, language,
                corrected_version)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (doc_id) DO UPDATE SET title = EXCLUDED.title
            """,
            (
                patent.doc_id, patent.title, patent.applicant,
                _json_or_none(patent.inventor), patent.filing_date,
                _json_or_none(patent.priority_date), patent.publication_date,
                _json_or_none(patent.ipc_codes), patent.abstract, patent.language,
                patent.corrected_version,
            ),
        )


# ---- nodes -----------------------------------------------------------
def insert_node(conn: PgConnection, node: Node) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO nodes (canonical_id, node_type, canonical_name,
                chinese_name, description, name_embedding)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (canonical_id) DO NOTHING
            """,
            (
                node.canonical_id,
                node.node_type.value if hasattr(node.node_type, "value") else node.node_type,
                node.canonical_name, node.chinese_name, node.description,
                node.name_embedding,
            ),
        )


def insert_alias(conn: PgConnection, alias: Alias) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO aliases (alias_text, canonical_id, edge_type, subtype, confidence)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (alias_text, canonical_id, edge_type) DO NOTHING
            """,
            (alias.alias_text, alias.canonical_id,
             alias.edge_type.value if hasattr(alias.edge_type, "value") else alias.edge_type,
             alias.subtype, alias.confidence),
        )


def insert_forbidden_merge(conn: PgConnection, fm: ForbiddenMerge) -> None:
    a, b = sorted([fm.entity_a, fm.entity_b])
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO forbidden_merge (entity_a, entity_b, reason_type, explanation, risk_severity)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (entity_a, entity_b) DO NOTHING
            """,
            (a, b,
             fm.reason_type.value if hasattr(fm.reason_type, "value") else fm.reason_type,
             fm.explanation, fm.risk_severity),
        )


def insert_must_merge(conn: PgConnection, mm: MustMerge) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO must_merge (alias_text, canonical_id, merge_type, confidence, source_evidence)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (alias_text) DO NOTHING
            """,
            (mm.alias_text, mm.canonical_id,
             mm.merge_type.value if hasattr(mm.merge_type, "value") else mm.merge_type,
             mm.confidence, mm.source_evidence),
        )


# ---- facts -----------------------------------------------------------
def insert_fact(conn: PgConnection, fact: FactHyperedge) -> None:
    """插入单条 FactHyperedge。

    数值 vs 文本表示：``result_value`` / ``baseline_value`` 进 NUMERIC 列；
    ``result_value_text`` / ``baseline_value_text`` 保留原始字面 (e.g. "78.4 GU")。
    """
    sst = fact.source_section_type
    sst_val = sst.value if hasattr(sst, "value") else sst
    pol_val = fact.polarity_hint.value if hasattr(fact.polarity_hint, "value") else fact.polarity_hint

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO facts (
                fact_id, application, property, source_section_type, polarity_hint,
                evidence_pointer, ontology_version, extraction_confidence, human_validated,
                doc_id,
                substrate, resin_system, additives, process, test_method, test_condition,
                result_value, result_value_text, baseline_value, baseline_value_text,
                comparison_group, claimed_in_claims, evidence_text, evidence_embedding
            )
            VALUES (
                %s,%s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,
                %s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s
            )
            ON CONFLICT (fact_id) DO NOTHING
            """,
            (
                fact.fact_id, fact.application, fact.property, sst_val, pol_val,
                Json(fact.evidence_pointer.model_dump(mode="json")),
                fact.ontology_version, fact.extraction_confidence, fact.human_validated,
                fact.doc_id,
                _json_or_none(fact.substrate), fact.resin_system,
                _json_or_none(fact.additives), _json_or_none(fact.process),
                fact.test_method, _json_or_none(fact.test_condition),
                fact.result_value, fact.result_value_text,
                fact.baseline_value, fact.baseline_value_text,
                fact.comparison_group, fact.claimed_in_claims,
                fact.evidence_text, fact.evidence_embedding,
            ),
        )


# ---- figure_table_units ----------------------------------------------
def insert_figure_table_unit(conn: PgConnection, unit: FigureTableUnit) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO figure_table_units (
                unit_id, unit_type, doc_id, page, region_id,
                image_path, caption_footnote_text, vlm_description, description_embedding,
                extracted_table_html, tagged_entities, figure_subtype,
                vlm_model_version, description_confidence
            )
            VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s, %s,%s)
            ON CONFLICT (unit_id) DO NOTHING
            """,
            (
                unit.unit_id, unit.unit_type, unit.doc_id, unit.page, unit.region_id,
                unit.image_path, unit.caption_footnote_text, unit.vlm_description,
                unit.description_embedding, unit.extracted_table_html,
                _json_or_none(unit.tagged_entities),
                unit.figure_subtype.value if unit.figure_subtype else None,
                unit.vlm_model_version, unit.description_confidence,
            ),
        )


# ---- property_registry -----------------------------------------------
def insert_property_registry(conn: PgConnection, entry: PropertyRegistryEntry) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO property_registry (
                property_id, english_name, chinese_name, typical_unit, directionality,
                requires_baseline, comparable_test_methods, related_but_not_equivalent,
                is_surface_property, is_aging_property, notes
            )
            VALUES (%s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s)
            ON CONFLICT (property_id) DO NOTHING
            """,
            (
                entry.property_id, entry.english_name, entry.chinese_name,
                entry.typical_unit,
                entry.directionality.value if hasattr(entry.directionality, "value") else entry.directionality,
                entry.requires_baseline,
                _json_or_none(entry.comparable_test_methods),
                _json_or_none(entry.related_but_not_equivalent),
                entry.is_surface_property, entry.is_aging_property, entry.notes,
            ),
        )


def bulk_insert_facts(conn: PgConnection, facts: Iterable[FactHyperedge]) -> int:
    """方便函数：一个事务里插多条 fact。返回行数。"""
    n = 0
    for fact in facts:
        insert_fact(conn, fact)
        n += 1
    conn.commit()
    return n
