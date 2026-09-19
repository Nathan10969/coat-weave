-- ============================================================
--  coating_kg — V1.2.2 schema
--  Target: PostgreSQL 16 + pgvector
--  Source of truth:
--    G:\coating_1\v1.2_design\06_entity_relation_hyperedge.md
--    G:\coating_1\v1.2_design\08_v122_amendments.md
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ------------------------------------------------------------
-- 1. patents
--    One row per source patent (identified by doc_id, e.g. WO2026077939A1).
--    Per V1.2.2: company / inventor / lawyer / dates kept here only,
--    NOT lifted into the KG nodes (red-line — see 06 §A "明确不抽取").
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS patents (
    doc_id              TEXT PRIMARY KEY,
    title               TEXT,
    applicant           TEXT,
    inventor            JSONB,                     -- list of strings
    filing_date         DATE,
    priority_date       JSONB,                     -- list of {country, number, date}
    publication_date    DATE,
    ipc_codes           JSONB,                     -- list of strings
    abstract            TEXT,
    language            TEXT,                      -- 'en' / 'zh' / 'de'
    corrected_version   TEXT,                      -- e.g. 'A1', 'B1'
    ingested_at         TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_patents_ipc ON patents USING gin (ipc_codes);


-- ------------------------------------------------------------
-- 2. nodes — the canonical KG nodes (all 8 types).
--    NodeType ∈ MAT / APP / SUB / PROP / PROC / TEST / EVD / PAT
--    Embedding is BGE-M3 (1024-dim) over canonical_name + chinese_name.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nodes (
    canonical_id    TEXT PRIMARY KEY,              -- e.g. MAT_HDI_trimer
    node_type       TEXT NOT NULL CHECK (node_type IN
                    ('MAT','APP','SUB','PROP','PROC','TEST','EVD','PAT')),
    canonical_name  TEXT NOT NULL,
    chinese_name    TEXT,
    description     TEXT,
    name_embedding  vector(1024),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes (node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_name_emb ON nodes
    USING hnsw (name_embedding vector_cosine_ops);


-- ------------------------------------------------------------
-- 3. aliases — alias subgraph (V1.2.2 keeps 5 active edge types).
--    edge_type ∈ canonical_of / synonym_of / chemical_subtype_of
--                forbidden_merge / must_merge
--    `subtype` further refines synonym_of / chemical_subtype_of
--    (e.g. trade_name_of / abbreviation_of / chinese_translation_of /
--          formula_to_name) — we collapsed the V1.1 8-edge taxonomy
--    into 5 + a subtype field for V1.2.2.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aliases (
    alias_text      TEXT NOT NULL,
    canonical_id    TEXT NOT NULL REFERENCES nodes(canonical_id) ON DELETE CASCADE,
    edge_type       TEXT NOT NULL CHECK (edge_type IN
                    ('canonical_of','synonym_of','chemical_subtype_of',
                     'forbidden_merge','must_merge')),
    subtype         TEXT,                          -- trade_name_of / abbreviation_of / ...
    confidence      NUMERIC,                       -- 0..1, source-quoted confidence
    created_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (alias_text, canonical_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_aliases_canonical ON aliases (canonical_id);
CREATE INDEX IF NOT EXISTS idx_aliases_edge_type ON aliases (edge_type);


-- ------------------------------------------------------------
-- 4. forbidden_merge — pairs that must NEVER be auto-merged.
--    Symmetric: enforce (entity_a < entity_b) on insert via app code.
--    reason_type ∈ chemistry_diff / measurement_diff / scope_diff /
--                  structure_role_diff / standard_diff / polarity_hint
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forbidden_merge (
    entity_a       TEXT NOT NULL,
    entity_b       TEXT NOT NULL,
    reason_type    TEXT NOT NULL CHECK (reason_type IN
                   ('chemistry_diff','measurement_diff','scope_diff',
                    'structure_role_diff','standard_diff','polarity_hint')),
    explanation    TEXT,
    risk_severity  TEXT CHECK (risk_severity IN ('low','med','high')),
    created_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (entity_a, entity_b)
);

CREATE INDEX IF NOT EXISTS idx_forbidden_a ON forbidden_merge (entity_a);
CREATE INDEX IF NOT EXISTS idx_forbidden_b ON forbidden_merge (entity_b);


-- ------------------------------------------------------------
-- 5. must_merge — alias-text → canonical_id forced normalisations.
--    Looser PK than aliases: alias_text alone is unique here because
--    a string only legitimately resolves to one canonical entity.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS must_merge (
    alias_text     TEXT PRIMARY KEY,
    canonical_id   TEXT NOT NULL REFERENCES nodes(canonical_id) ON DELETE CASCADE,
    merge_type     TEXT NOT NULL CHECK (merge_type IN
                   ('chemical_subtype_of','trade_name_of','abbreviation_of',
                    'synonym_of','chinese_translation_of','formula_to_name')),
    confidence     TEXT CHECK (confidence IN ('low','med','high')),
    source_evidence TEXT,
    created_at     TIMESTAMPTZ DEFAULT now()
);


-- ------------------------------------------------------------
-- 6. facts — THE FactHyperedge table (9 required + 10 optional + 1 marker).
--    Every row = one fact pulled from one cell of one figure/table inside
--    the Examples section of one patent.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS facts (
    -- 9 required ----------------------------------------------------------
    fact_id                TEXT PRIMARY KEY,
    application            TEXT NOT NULL,                 -- canonical_id (APP_*)
    property               TEXT NOT NULL,                 -- canonical_id (PROP_*)
    source_section_type    TEXT NOT NULL CHECK (source_section_type IN
                           ('TABLE','FIGURE','TABLE_CAPTION_FOOTNOTE','FIGURE_CAPTION_FOOTNOTE')),
    polarity_hint          TEXT NOT NULL CHECK (polarity_hint IN
                           ('positive','negative','unknown')),
    evidence_pointer       JSONB NOT NULL,                -- {doc_id, page, region_type, region_id, row, column, cell?}
    ontology_version       TEXT NOT NULL,
    extraction_confidence  NUMERIC NOT NULL CHECK (extraction_confidence BETWEEN 0 AND 1),
    human_validated        BOOLEAN NOT NULL DEFAULT FALSE,
    doc_id                 TEXT NOT NULL REFERENCES patents(doc_id) ON DELETE CASCADE,

    -- 10 optional ---------------------------------------------------------
    substrate              JSONB,                          -- {tested: id, claimed: [id]}
    resin_system           TEXT,                           -- canonical_id (MAT_*)
    additives              JSONB,                          -- list of canonical_id
    process                JSONB,                          -- ordered list of {step, condition}
    test_method            TEXT,                           -- canonical_id (TEST_*)
    test_condition         JSONB,                          -- {film_thickness, curing_condition, dosage, ...}
    result_value           NUMERIC,                        -- numeric magnitude (unit lives on the property)
    result_value_text      TEXT,                           -- raw "78.4 GU" — kept for provenance
    baseline_value         NUMERIC,
    baseline_value_text    TEXT,
    comparison_group       TEXT,                           -- a fact_id of the negative baseline (auto-bound)

    -- 1 marker ------------------------------------------------------------
    claimed_in_claims      BOOLEAN DEFAULT FALSE,

    -- evidence text + embedding (per V1 scoring §E)
    evidence_text          TEXT,                           -- TABLE row+col+footnote concat
    evidence_embedding     vector(1024),

    created_at             TIMESTAMPTZ DEFAULT now()
);

-- B-tree filters on the most-queried slots
CREATE INDEX IF NOT EXISTS idx_facts_application ON facts (application);
CREATE INDEX IF NOT EXISTS idx_facts_property    ON facts (property);
CREATE INDEX IF NOT EXISTS idx_facts_test_method ON facts (test_method);
CREATE INDEX IF NOT EXISTS idx_facts_polarity    ON facts (polarity_hint);
CREATE INDEX IF NOT EXISTS idx_facts_doc_id      ON facts (doc_id);
CREATE INDEX IF NOT EXISTS idx_facts_result_val  ON facts (result_value);

-- ANN over the evidence text embedding
CREATE INDEX IF NOT EXISTS idx_facts_evidence_emb ON facts
    USING hnsw (evidence_embedding vector_cosine_ops);

-- JSONB search on additives + test_condition
CREATE INDEX IF NOT EXISTS idx_facts_additives  ON facts USING gin (additives);
CREATE INDEX IF NOT EXISTS idx_facts_test_cond  ON facts USING gin (test_condition);


-- ------------------------------------------------------------
-- 7. passages — V2 RESERVE.
--    Per V1.2.2 §修订 1 rollback we DO NOT extract paragraph evidence in V1.
--    Table is kept as a forward-compatible placeholder for V2 paragraph mode.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS passages (
    passage_id      TEXT PRIMARY KEY,
    doc_id          TEXT REFERENCES patents(doc_id) ON DELETE CASCADE,
    page            INT,
    paragraph_id    TEXT,
    text            TEXT,
    tagged_entities JSONB,
    text_embedding  vector(1024),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_passages_doc ON passages (doc_id);


-- ------------------------------------------------------------
-- 8. figure_table_units — V1.2.2 spine for figure/table evidence.
--    See V1.2.2 §修订 2 — adds tagged_entities + figure_subtype.
--    caption_footnote_text concatenates caption + footnote in one column
--    (per V1.2.2 §修订 1 — no separate caption / footnote rows).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS figure_table_units (
    unit_id                 TEXT PRIMARY KEY,
    unit_type               TEXT NOT NULL CHECK (unit_type IN ('figure','table')),
    doc_id                  TEXT NOT NULL REFERENCES patents(doc_id) ON DELETE CASCADE,
    page                    INT,
    region_id               TEXT,                          -- 'Table 5' / 'Fig. 7'

    image_path              TEXT,
    caption_footnote_text   TEXT,
    vlm_description         TEXT,
    description_embedding   vector(1024),
    extracted_table_html    TEXT,                          -- only for tables

    tagged_entities         JSONB,                         -- list of canonical_id (V1.2.2 new)
    figure_subtype          TEXT CHECK (figure_subtype IS NULL OR figure_subtype IN
                            ('structure','scheme','plot','sem','apparatus','other')),

    vlm_model_version       TEXT,
    description_confidence  NUMERIC CHECK (description_confidence IS NULL OR
                                            description_confidence BETWEEN 0 AND 1),
    created_at              TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ftu_doc ON figure_table_units (doc_id);
CREATE INDEX IF NOT EXISTS idx_ftu_subtype ON figure_table_units (figure_subtype);
CREATE INDEX IF NOT EXISTS idx_ftu_desc_emb ON figure_table_units
    USING hnsw (description_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_ftu_tagged ON figure_table_units USING gin (tagged_entities);


-- ------------------------------------------------------------
-- 9. property_registry — directionality + comparable-method bookkeeping.
--    property_id is a PROP_* canonical_id from `nodes`.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS property_registry (
    property_id                  TEXT PRIMARY KEY REFERENCES nodes(canonical_id) ON DELETE CASCADE,
    english_name                 TEXT NOT NULL,
    chinese_name                 TEXT,
    typical_unit                 TEXT,
    directionality               TEXT NOT NULL CHECK (directionality IN
                                 ('higher_is_better','lower_is_better','depends')),
    requires_baseline            BOOLEAN NOT NULL DEFAULT FALSE,
    comparable_test_methods      JSONB,        -- list of canonical TEST_* ids
    related_but_not_equivalent   JSONB,        -- list of canonical_id
    is_surface_property          BOOLEAN DEFAULT FALSE,
    is_aging_property            BOOLEAN DEFAULT FALSE,
    notes                        TEXT,
    created_at                   TIMESTAMPTZ DEFAULT now()
);


-- ------------------------------------------------------------
-- End of schema.
--    Tables: patents / nodes / aliases / forbidden_merge / must_merge /
--            facts / passages / figure_table_units / property_registry
-- ============================================================
