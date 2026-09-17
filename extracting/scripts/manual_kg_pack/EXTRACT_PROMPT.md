# `manual_kg_pack` — Per-Patent Extraction Prompt (v1.0)

> Agent-native Vision KG note:
> This legacy manual prompt is retained as a schema/reference aid for existing
> manual packs. New batch reconstruction work must follow
> `coating_kg/docs/AGENT_NATIVE_VISION_KG_RECONSTRUCTION.md`.
> In that workflow, scripts must not perform semantic extraction. Codex agents
> reading page images perform page triage, evidence extraction, table reading,
> fact extraction, context binding, hyperedge construction, and audit. Scripts
> may only render, save, validate, project CSV from hyperedges, and package.

You are extracting an **Examples-scope Knowledge Graph pack** for **one coating patent**, mirroring the schema GPT Pro produced for `WO2015132366A1` (`kg_pack_*_v1`). Your output is **10 JSONL files + 1 manifest.json + 1 CSV projection**, written to `$OUT_DIR`.

The user has 347 patents to process. This prompt is reused verbatim across all conversations — keep behavior deterministic.

---

## 0. Inputs (always populated before this prompt is invoked)

You receive **one prep directory** (`$PREP_DIR`) produced by `scripts/manual_kg_pack/prep_patent.py`. It contains:

| File / dir | Purpose |
|---|---|
| `kg_input.json` | **Primary structured input.** One JSON with: `doc_id`, `source_pdf_*`, `page_count`, `examples_scope.detected_pages`, and per-page `paragraphs[]` + `tables[]` (with HTML + cell bboxes) + `figures[]`. **You read this first.** |
| `mineru.md` | Full OCR markdown. Use only for fallback / context, not as primary source. |
| `middle.json` | Raw MinerU layout JSON. Use only if a table looks wrong in `kg_input.json` and you need the original blocks. |
| `pages/page_NNN.png` | 300 dpi rendering of every page. Used to write `asset_path` and **never** decoded by you. |

Two other parameters are passed in:

- `$OUT_DIR` — absolute path where you write outputs.
- `$DOC_ID` — equal to `kg_input.doc_id` (e.g. `WO2015132366A1`), used in every node_id.

**Scope rule:** You extract ONLY content from pages where `kg_input.pages[i].in_examples_scope == true`. Everything else (description, claims, background) is ignored. If `examples_scope.detected_pages` is empty or wrong, **stop and ask the user for `--force-pages`** before continuing — do not guess.

---

## 1. Goal

Produce a single self-contained `kg_pack` directory matching the GPT Pro sample at `<repository-root>\extracting\scripts\manual_kg_pack\reference\WO2015132366A1\` (when present). Specifically:

- Every table row in scope → one `EvidenceRecord` with **N `direct_extracted_units` slot-facts** (one per material/metric/condition).
- Every method/process/test paragraph in scope → one `EvidenceRecord` with slot-facts for substrate / process / test_method / test_standard / test_condition.
- Every distinct sample×example → one `HyperedgeRecord` bundling its formulation, process, substrate, test_method, results, baseline.
- All entities mentioned (materials, properties, applications, substrates, processes, test_methods, test_standards, formulation_metrics, performance_metrics) → one `CanonicalEntityRecord` each.
- All cross-references → typed `EdgeRecord`s.
- One `boss_preview.csv` projected from the hyperedges, schema in §7.

Key targets you must hit (calibrated against `WO2015132366A1`, scale roughly by table count):

| Metric | Target |
|---|---|
| `facts.jsonl` rows | ≥ `15 * (sum of in-scope table rows after header strip) + 50` |
| `evidence_units.jsonl` rows | = (in-scope method paragraphs) + (real data rows across all in-scope tables) |
| `hyperedges.jsonl` rows | = (unique sample×context bundles), typically 60–150 per patent |
| `direct_extracted_units` per formulation row | one per non-empty cell, including totals/PVC/SVR/ratios |
| `confidence.overall` | ≥ 0.85 unless explicit ambiguity |
| `trace_rate.facts_with_evidence_id` | **1.0 — every fact must point to an evidence_id** |

Hallucination rule: **every value you write must be traceable to an HTML cell, a paragraph snippet, or a figure caption that appears in `kg_input.json` or `mineru.md`**. If a value is not in the source, leave it `null`. Do not invent units, ratios, or standard IDs.

---

## 2. Pipeline (do these in order)

You may use `python` (already in `$VENV`) to read JSON, but the writing of all 10 JSONL files must be deterministic — write one file at a time, finalize, then move on.

1. **Read `kg_input.json` fully.** Note `doc_id`, in-scope pages, table count, total table rows.
2. **Build canonical_entities** (write last, but plan now). Walk all in-scope tables + paragraphs and collect every **distinct** material name, application phrase, substrate description, property name, process verb, test method name, test standard ID, formulation metric label, performance metric label. Normalize trivial whitespace; keep parenthetical trademarks as part of the name (e.g. `"Zinc particles, ZMP 4P16 (Umicore Belgium)"`).
3. **For each in-scope method/process paragraph**, produce an `EvidenceRecord` with `evidence_type="method_paragraph"`. Decompose into `direct_extracted_units` (slot vocabulary in §4.3).
4. **For each in-scope table:**
    - Parse the HTML rows in `kg_input.pages[*].tables[*].rows`.
    - Filter header rows (where all cells are empty or are column-labels like "Model paint", "1", "%SV", "Reference paint"). Keep these in your head as **column metadata** for the data rows.
    - For each **data row** (one that has a sample identifier in col 0 like "Epoxy resin (...)", or for a result table a paint id like "Model paint 7"):
        - Produce one `EvidenceRecord` with `evidence_type="formulation_table"` (formulation) or `"result_table"` (rust creep / cracking results).
        - For a **formulation table**: the row represents one ingredient across multiple sample columns. For each non-empty cell across the sample columns, emit a `direct_extracted_units` entry with slot = `resin` if the ingredient's canonical role is `binder|resin`, else `component`. **One row → N facts (one per non-empty sample column).** Plus emit `formulation_metric` units for total/PVC/SVR/ratio rows.
        - For a **result table**: the row represents one performance metric value per sample. Emit `direct_extracted_units` with slot=`result` per cell, and bind `baseline` if the row is a reference paint paired with model paints in the same table.
5. **Build `example_contexts`.** One record per distinct (`example_id` × `sample_id` × `table_role`) tuple. Each context groups its formulation rows + result rows.
6. **Build `hyperedges`.** One per distinct sample×context bundle. Slot fields described in §4.6. Polarity defaults to `tested` for model paints, `claimed` only for application/substrate. Baseline binding: for every model paint in a result table, scan the same table for the most-similar reference paint by formulation; bind it as `baseline.baseline_sample_id` and emit a `HAS_BASELINE_HYPEREDGE` edge.
7. **Build `canonical_relations`** (alias + role disambiguation): for each canonical_entity with multiple aliases, emit `canonical_of` edges; for each `forbidden_merge`/`must_merge`/`chemical_subtype_of` pair you observe in the data, emit one record (rare; usually only when the same chemical appears under two trade names).
8. **Build `edges`.** §4.8 lists the 22 edge types and which (src, dst) types are allowed for each. **Run the type-whitelist check before writing — abort and tell the user if any edge violates it.**
9. **Build `patents.jsonl` + `patent_profiles.jsonl`.** Source metadata from `kg_input` + the first 2 pages of `mineru.md` (title, applicants, dates, jurisdiction).
10. **Build `manifest.json`** (§4.10). Counts must match the actual JSONL line counts.
11. **Build `boss_preview.csv`** by projecting hyperedges (§7). One row per hyperedge, 42 columns.
12. **Self-check** (§6). If any check fails, fix and re-write the affected file.

---

## 3. Output layout

```
$OUT_DIR/
    patents.jsonl              1 line
    patent_profiles.jsonl      1 line
    evidence_units.jsonl       ≈ (method_paragraphs + data_table_rows) lines
    example_contexts.jsonl     ≈ unique (sample × table_role) tuples
    facts.jsonl                = sum of direct_extracted_units across all evidence
    canonical_entities.jsonl   ≈ 80–200 lines depending on patent
    canonical_relations.jsonl  ≈ 0–300 (often small)
    edges.jsonl                ≈ 5000–20000 lines, typed
    hyperedges.jsonl           ≈ 60–150 lines
    manifest.json              1 record
    boss_preview.csv           1 + ≈hyperedges rows (header + data)
```

All JSONL files use **one JSON object per line, no trailing newline after the last record, UTF-8 encoding, no BOM, no pretty-printing**.

---

## 4. Schemas (field-by-field, every JSONL specified)

For every record below, the constant fields are:

- `schema_version`: as specified per record
- `kg_source`: `"<DOC_ID>_manual_strict_example_scope_kg_pack_v1"`
- `patent_id` / `doc_id`: both `$DOC_ID`

### 4.1 `patents.jsonl` — 1 record (`PatentRecord`)

```json
{
  "record_type": "PatentRecord",
  "schema_version": "kg_pack_patent_v1",
  "node_type": "PATENT",
  "node_id": "PAT_<DOC_ID>",
  "patent_id": "<DOC_ID>",
  "doc_id": "<DOC_ID>",
  "publication_number": "<DOC_ID>",
  "title": "<from page 1 OCR>",
  "application_number": "<PCT/EP… or null>",
  "publication_date": "YYYY-MM-DD",
  "filing_date": "YYYY-MM-DD",
  "priority_date": "YYYY-MM-DD",
  "applicants": ["<comma-split list>"],
  "jurisdiction": "WO|US|EP|CN|JP|KR|…",
  "source_pdf": "<source_pdf_name>",
  "page_count": <int>,
  "kg_source": "<kg_source>",
  "review_status": "generated_for_user_review",
  "scope_note": "This pack extracts the Examples section and directly related paragraphs/tables, PDF pages <first>–<last>."
}
```

Any field whose value is not present in the OCR: write `null`, **never** invent.

### 4.2 `patent_profiles.jsonl` — 1 record (`PatentProfileRecord`)

```json
{
  "record_type": "PatentProfileRecord",
  "schema_version": "kg_pack_patent_profile_v1",
  "node_type": "PATENT_PROFILE",
  "node_id": "PROFILE_<DOC_ID>",
  "profile_id": "PROFILE_<DOC_ID>",
  "patent_id": "<DOC_ID>",
  "doc_id": "<DOC_ID>",
  "technology_area": "<2–8 word phrase from title, lowercase>",
  "application": "<core application from claims/abstract, single phrase>",
  "coating_system": "<comma-list of resin systems exemplified, e.g. 'epoxy-based, silicate-based, polysiloxane-based, polyurethane-based'>",
  "core_innovation": "<1 sentence ≤ 200 chars>",
  "systems_covered": ["epoxy-based paints", …],
  "examples_scope": {
    "pages": "PDF pages <first>–<last> / printed pages <first-1>–<last-1>",
    "tables": ["Table 1", "Table 2", …],
    "paragraphs": ["Preparation of test panels", "Cracking test", "Salt Spray Test", "Example 1 process", …]
  },
  "summary": "Strict example-scope KG pack. L1 evidence units are direct source units; L2 hyperedges are answerable coating fact bundles that combine sample, formulation, process, substrate, test method, result, baseline and evidence.",
  "kg_source": "<kg_source>"
}
```

### 4.3 `evidence_units.jsonl` — many records (`EvidenceRecord`)

One record per **source unit** (method paragraph OR data row of a table). Schema:

```json
{
  "record_type": "EvidenceRecord",
  "schema_version": "kg_pack_l1_evidence_unit_v1",
  "node_type": "EVD",
  "node_id": "EVD_<DOC_ID>_p<PAGE>_<descriptor>",
  "evidence_id": "<same as node_id>",
  "patent_id": "<DOC_ID>",
  "doc_id": "<DOC_ID>",
  "layer": "L1_evidence_unit",
  "source": "mineru_layout_plus_llm_decomposition",
  "source_pdf": "<source_pdf_name>",
  "page": <pdf page, 1-indexed>,
  "printed_page": <pdf_page - 1 typically; use null if unknown>,
  "section": "<short hierarchical section name, e.g. 'Example 1 / Table 1 - Basic formulation of epoxy-based paints'>",
  "evidence_type": "method_paragraph | formulation_table | result_table",
  "source_block_type": "method_or_process_paragraph | formulation_table_row | result_table_row | result_paragraph",
  "table": "Table N | null",
  "row": "<row label like 'Model paint 1' or null for paragraphs>",
  "column": "<column header context like '%SV' or null>",
  "caption": "<table caption or section name>",
  "bbox": null,                                   // page-level cell bbox; null OK (we use table-level via image_bbox)
  "asset_path": "pages/page_<NNN>.png",
  "asset_bbox_image": [px0, py0, px1, py1],       // for tables: row_bbox_image; for paragraphs: bbox_image
  "asset_bbox_pdf":   [x0, y0, x1, y1],
  "text_excerpt": "<concise summary string, see below>",
  "quote_style": "structured_row_transcription | structured_paragraph_summary",
  "direct_extracted_units": [ <SlotUnit>, … ],
  "context_bindings": {
    "linked_l2_context_ids": [...],
    "primary_l2_context_ids": [...],
    "inherited_by_l2_context_ids": [...],
    "linked_bundle_types": ["formulation_context", "performance_result", "test_context"],
    "linked_sample_ids": [...]
  },
  "coverage_scope": "examples_section_tables_and_related_method_paragraphs",
  "confidence": <float 0–1>,
  "qa_flags": ["strict_direct_evidence_units", "examples_section_only", ...],
  "unresolved_fields": ["bbox" if cell-level bbox is not available],
  "kg_source": "<kg_source>",
  "trace_role": "L1_retrieval_context",
  "source_scope": "examples_pages_<first>_<last>"
}
```

`text_excerpt` rules:

- **Formulation row**: `"Table N row '<sample_id>'; <ingredient>: <value> <unit> | <ingredient>: <value> <unit> | …"` — pipe-separated cells.
- **Result row**: same pattern, `<metric>: <value> <unit>`.
- **Method paragraph**: `"<section>: <full paragraph text>"`.

Each `<SlotUnit>` in `direct_extracted_units` has the following structure (slot-dependent — only fields relevant to the slot need to be present):

| slot | required fields | optional fields |
|---|---|---|
| `sample_context` | `slot, source_field, name, raw_text, unit_id` | — |
| `example_context` | `slot, source_field, name, raw_text, unit_id` | — |
| `substrate` | `slot, source_field, name, raw_text, unit_id` | `condition` |
| `process` | `slot, source_field, name, metric, raw_text, conditions, evidence_ref, unit_id` | `step_type` |
| `process_condition` | `slot, source_field, name, step_type, raw_text, unit_id` | `value, unit` |
| `test_method` | `slot, source_field, name, raw_text, unit_id` | — |
| `test_standard` | `slot, source_field, name, raw_text, unit_id` | — |
| `test_condition` | `slot, source_field, name, raw_text, unit_id` | `value, unit` |
| `property` | `slot, source_field, name, category, measured_or_claimed, unit_id` | — |
| `resin` | `slot, source_field, name, role, value, unit, raw_text, amount_basis, evidence_ref, unit_id` | `role_detail` |
| `component` | `slot, source_field, name, role, value, unit, raw_text, amount_basis, evidence_ref, unit_id` | `role_detail` |
| `formulation_metric` | `slot, source_field, metric, value, unit, raw_text, scale, unit_id` | — |
| `test_method_hint` | `slot, source_field, name, raw_text, unit_id` | — |
| `result` | `slot, source_field, name, metric, value, unit, raw_text, evidence_ref, unit_id` | — |
| `baseline` | `slot, source_field, baseline_sample_id, baseline_context_id, baseline_result, comparison_text, unit_id` | — |

**Slot → canonical role decision** (for `resin` vs `component`):

- If the ingredient's canonical name contains any of: `epoxy resin`, `silicate binder`, `polyisocyanate`, `polysiloxane`, `acrylic resin`, `polyurethane resin`, `polyaminoamide`, `urea/aldehyde resin`, `binder`, `Cardolite` → slot=`resin`, `role` ∈ {`binder`, `resin`, `curing_agent`, `hardener`}.
- If the ingredient is a pigment/filler/microsphere/conductive pigment/additive/defoamer/dispersant/wax/stabilizer/catalyst/inhibitor → slot=`component`, `role` ∈ {`pigment`, `filler`, `curing_agent`, `dispersant`, `rheology_additive`, `surface_additive`, `other`}.
- `role_detail` is a short freeform descriptor like `"zinc particles / metallic conductive pigment"`, `"reactive epoxy diluent"`, `"polyaminoamide curing agent"`. Optional but encouraged.
- For totals/PVC/SVR/ratios/mixing ratio rows → slot=`formulation_metric`, fill `metric` with the row label.

### 4.4 `example_contexts.jsonl` — many records (`ExampleContextRecord`)

```json
{
  "record_type": "ExampleContextRecord",
  "schema_version": "kg_pack_example_context_v1",
  "node_type": "EXAMPLE_CONTEXT",
  "node_id": "EXCTX_<DOC_ID>__<slug>",
  "context_id": "<same as node_id but without EXCTX_ prefix>",
  "patent_id": "<DOC_ID>",
  "doc_id": "<DOC_ID>",
  "example_id": "Example 1 / Table 1 | Examples / Test Methods | …",
  "sample_id": "<sample identifier like 'Model paint 1' or 'shared test panel preparation'>",
  "bundle_type": "formulation_context | performance_result | test_context",
  "table_role": "Table 1 | Table 2 | … | null",
  "evidence_ids": ["EVD_…", …],
  "linked_hyperedge_ids": ["HEDGE_…"],
  "kg_source": "<kg_source>"
}
```

### 4.5 `facts.jsonl` — many records (`FactRecord`)

One record per `direct_extracted_unit` across all evidence_units. Flat projection — facilitates downstream querying.

```json
{
  "record_type": "FactRecord",
  "schema_version": "kg_pack_fact_v1",
  "node_type": "FACT",
  "node_id": "FACT_<DOC_ID>_<6-digit-seq>",
  "fact_id": "<same as node_id>",
  "patent_id": "<DOC_ID>",
  "doc_id": "<DOC_ID>",
  "evidence_id": "EVD_…",
  "context_id": "<EXCTX_…/CTX_…>",
  "hyperedge_id": "HEDGE_…",
  "slot": "<one of the 15 slot names>",
  "source_field": "<as in SlotUnit>",
  "name": "<as in SlotUnit; may be null for formulation_metric>",
  "metric": "<for result/formulation_metric>",
  "role": "<for resin/component>",
  "role_detail": "<for resin/component>",
  "value": "<string or number, as raw_text>",
  "unit": "<string or null>",
  "raw_text": "<full raw text>",
  "amount_basis": "formulation_amount | result_value | null",
  "scale": "formulation metric | null",
  "step_type": "application | curing | mixing | additive_addition | hardener_addition | testing | null",
  "linked_canonical_entity_id": "ENT_…",
  "kg_source": "<kg_source>"
}
```

Sequence number is global: 6-digit padded, starting at `000001`, monotonically increasing across the whole file.

### 4.6 `canonical_entities.jsonl` — many records (`CanonicalEntityRecord`)

```json
{
  "record_type": "CanonicalEntityRecord",
  "schema_version": "kg_pack_canonical_entity_v1",
  "node_type": "CANONICAL_ENTITY",
  "node_id": "ENT_<TYPE>_<5-digit-seq>",
  "entity_id": "<same as node_id>",
  "entity_type": "application | substrate | property | test_standard | test_method | process | process_condition | material | formulation_metric | performance_metric",
  "canonical_name": "<full normalized name>",
  "aliases": ["<exact strings as they appear in source, deduplicated>"],
  "patent_id": "<DOC_ID>",
  "doc_id": "<DOC_ID>",
  "kg_source": "<kg_source>",
  "category": "<only for property: corrosion_resistance | durability | formulation | other>",
  "role": "<only for material: binder | resin | pigment | filler | curing_agent | dispersant | rheology_additive | surface_additive | other>",
  "role_detail": "<only for material: optional freeform>"
}
```

Sequence number is **continuous across entity_type** (not per-type) — match GPT Pro's convention where ENT_001 is application, ENT_002 application, ENT_003 substrate, etc. Order entities by first appearance in the Examples scope.

### 4.7 `canonical_relations.jsonl` — few records (`CanonicalRelationRecord`)

```json
{
  "record_type": "CanonicalRelationRecord",
  "schema_version": "kg_pack_canonical_relation_v1",
  "relation_id": "REL_<5-digit-seq>",
  "relation_type": "canonical_of | synonym_of | chemical_subtype_of | forbidden_merge | must_merge",
  "src": "ENT_…",
  "dst": "ENT_…",
  "evidence_ids": ["EVD_…"],
  "patent_id": "<DOC_ID>",
  "doc_id": "<DOC_ID>",
  "kg_source": "<kg_source>"
}
```

When to emit:

- `canonical_of`: only when a single canonical entity has > 1 alias and the aliases are not trivially identical (e.g. `"SST"` ↔ `"Salt Spray Test"`).
- `chemical_subtype_of`: when one material is a subtype of another (e.g. `"Bisphenol F-epichlorhydrin epoxy"` chemical_subtype_of `"Epoxy resin"`).
- Others: only if the patent text explicitly distinguishes / merges two entities.

### 4.8 `edges.jsonl` — many records (`EdgeRecord`)

```json
{
  "record_type": "EdgeRecord",
  "schema_version": "kg_pack_edge_v1",
  "edge_id": "EDGE_<12-hex>",
  "edge_type": "<one of 22 types below>",
  "src": "<node_id>",
  "dst": "<node_id>",
  "patent_id": "<DOC_ID>",
  "doc_id": "<DOC_ID>",
  "kg_source": "<kg_source>",
  "amount": "<optional, for HAS_RESIN/HAS_COMPONENT, e.g. '16.6 %SV'>",
  "role": "<optional, for HAS_RESIN/HAS_COMPONENT>",
  "relation_scope": "<optional, 'tested' | 'claimed' for HAS_APPLICATION/HAS_SUBSTRATE>",
  "bundle_type": "<optional, for CONTEXT_HAS_HYPEREDGE>"
}
```

`edge_id` is a 12-char lowercase hex from `md5(edge_type + src + dst)[:12]`.

**The 22 edge types and their strict (src_type, dst_type, dst_role_required) whitelist:**

| edge_type | src_type | dst_type | required role on dst | notes |
|---|---|---|---|---|
| `HAS_PROFILE` | PATENT | PATENT_PROFILE | – | exactly 1 |
| `HAS_EVIDENCE` | PATENT | EVD | – | one per evidence_unit |
| `HAS_EXAMPLE_CONTEXT` | PATENT | EXAMPLE_CONTEXT | – | one per example_context |
| `HAS_HYPEREDGE` | PATENT | HYPEREDGE | – | one per hyperedge |
| `HAS_FACT` | PATENT | FACT | – | one per fact |
| `HAS_RESIN` | HYPEREDGE | CANONICAL_ENTITY | material.role ∈ {binder, resin, curing_agent, hardener} | required `amount`, optional `role` |
| `HAS_COMPONENT` | HYPEREDGE | CANONICAL_ENTITY | material.role ∈ {pigment, filler, curing_agent, dispersant, rheology_additive, surface_additive, other} | required `amount`, optional `role` |
| `HAS_PROPERTY` | HYPEREDGE | CANONICAL_ENTITY | entity_type=property | – |
| `HAS_SUBSTRATE` | HYPEREDGE | CANONICAL_ENTITY | entity_type=substrate | required `relation_scope` |
| `HAS_APPLICATION` | HYPEREDGE | CANONICAL_ENTITY | entity_type=application | required `relation_scope` |
| `HAS_TEST_METHOD` | HYPEREDGE | CANONICAL_ENTITY | entity_type=test_method | – |
| `HAS_TEST_STANDARD` | HYPEREDGE | CANONICAL_ENTITY | entity_type=test_standard | – |
| `HAS_BASELINE_HYPEREDGE` | HYPEREDGE | HYPEREDGE | – | from positive sample → its negative baseline |
| `CONTEXT_HAS_HYPEREDGE` | EXAMPLE_CONTEXT | HYPEREDGE | – | required `bundle_type` |
| `CONTEXT_HAS_FACT` | EXAMPLE_CONTEXT | FACT | – | – |
| `SUPPORTED_BY_EVIDENCE` | HYPEREDGE | EVD | – | – |
| `EVIDENCE_SUPPORTS_HYPEREDGE` | EVD | HYPEREDGE | – | inverse of above (kept for bidirectional traversal) |
| `FACT_SUPPORTED_BY_EVIDENCE` | FACT | EVD | – | – |
| `EVIDENCE_SUPPORTS_FACT` | EVD | FACT | – | inverse |
| `FACT_PART_OF_HYPEREDGE` | FACT | HYPEREDGE | – | – |
| `FACT_INHERITED_BY_HYPEREDGE` | FACT | HYPEREDGE | – | facts from shared method paragraphs inherited by multiple hyperedges |
| `MENTIONS_CANONICAL_ENTITY` | EVD or FACT | CANONICAL_ENTITY | – | – |

**Type-whitelist check:** Before writing `edges.jsonl`, build a dict `{edge_type → (allowed_src_types, allowed_dst_types, role_check)}` from the table above. For every edge you emit, assert the (src, dst) types and role match. If any assertion fails, abort and report the offending edge to the user. **This is the key defense against the resin↔resistance mismatch the user has seen in their pipeline.**

### 4.9 `hyperedges.jsonl` — many records (`HyperedgeRecord`)

```json
{
  "record_type": "HyperedgeRecord",
  "schema_version": "kg_pack_l2_hyperedge_v1",
  "node_type": "HYPEREDGE",
  "node_id": "HEDGE_<DOC_ID>_<4-digit-seq>",
  "hyperedge_id": "<same as node_id>",
  "patent_id": "<DOC_ID>",
  "doc_id": "<DOC_ID>",
  "context_id": "<EXCTX_… / CTX_…>",
  "bundle_type": "test_context | formulation_context | performance_result",
  "sample_id": "<e.g. 'Model paint 1'>",
  "example_id": "Example 1 / Table 1 …",
  "application": {
    "tested": "<phrase as in source>",
    "claimed": "<phrase as in source>"
  },
  "substrate": {
    "tested": "<full substrate description>",
    "claimed": "<generic substrate from claims>"
  },
  "resin": [
    {"name": "<canonical>", "role": "binder|resin|curing_agent|hardener", "amount": "16.6", "unit": "%SV"}
  ],
  "additives": [
    {"name": "<canonical>", "role": "pigment|filler|...", "amount": "...", "unit": "..."}
  ],
  "process": [
    {"step_type": "mixing|application|curing|...",
     "description": "<verbatim from method paragraph>",
     "conditions": {"temperature": "...|null", "time": "...|null", "ratio": "...|null", "film_thickness": "...|null"},
     "evidence_ref": "EVD_…"}
  ],
  "property": {"name": "<canonical_property_name>", "category": "corrosion_resistance|durability|formulation|other", "measured_or_claimed": "measured|claimed"},
  "test_method": {
    "name": "<canonical_test_method>",
    "standard_id": ["ISO 12944", ...],
    "condition": {"exposure": "...", "immersion": "...", "rating_scale": "...", "score_max": null}
  },
  "result": [
    {"metric": "average rust creep, panel 1, 800 h", "value": "2.5", "unit": "mm", "evidence_ref": "EVD_…"}
  ],
  "baseline": {
    "baseline_sample_id": "Reference paint 1",
    "baseline_context_id": "EXCTX_…",
    "baseline_result": "<value with unit>",
    "comparison_text": "<freeform>"
  },
  "evidence": [
    {"evidence_id": "EVD_…", "page": <int>, "section": "...", "table": "Table N|null", "row": "...|null", "column": "...|null", "evidence_type": "...", "printed_page": <int>}
  ],
  "confidence": {"overall": 0.96, "sample_binding": 0.96, "formulation_binding": 0.94, "result_binding": 0.92},
  "qa_flags": ["mineru_layout_based", "role_normalized", "examples_section_only"],
  "unresolved_fields": ["bbox" if applicable],
  "kg_source": "<kg_source>",
  "source_scope": "examples_pages_<first>_<last>",
  "evidence_ids": ["EVD_…", …],
  "answerability": "context_bundle",
  "projection_note": "Use this L2 record as fact source; CSV should be a view only."
}
```

### 4.10 `manifest.json` — 1 record

```json
{
  "record_type": "ManifestRecord",
  "schema_version": "kg_pack_manifest_v1",
  "patent_id": "<DOC_ID>",
  "doc_id": "<DOC_ID>",
  "title": "<patent.title>",
  "source_pdf": "<source_pdf_name>",
  "generated_at_utc": "<ISO-8601 UTC, e.g. 2026-05-19T12:00:00Z>",
  "kg_source": "<kg_source>",
  "scope": "Examples section and directly related paragraphs/tables, PDF pages <first>-<last>",
  "files": {
    "patents": "patents.jsonl",
    "patent_profiles": "patent_profiles.jsonl",
    "evidence_units": "evidence_units.jsonl",
    "example_contexts": "example_contexts.jsonl",
    "facts": "facts.jsonl",
    "canonical_entities": "canonical_entities.jsonl",
    "canonical_relations": "canonical_relations.jsonl",
    "edges": "edges.jsonl",
    "hyperedges": "hyperedges.jsonl",
    "manifest": "manifest.json"
  },
  "counts": {
    "patents": 1, "patent_profiles": 1,
    "evidence_units": <int>, "example_contexts": <int>,
    "facts": <int>, "canonical_entities": <int>,
    "canonical_relations": <int>, "edges": <int>, "hyperedges": <int>
  },
  "coverage": {
    "source_blocks_covered": ["<short block names you actually extracted>"],
    "by_bundle_type": {"test_context": <int>, "formulation_context": <int>, "performance_result": <int>},
    "by_evidence_type": {"method_paragraph": <int>, "formulation_table": <int>, "result_table": <int>},
    "by_evidence_source_block_type": {...},
    "by_fact_slot": {
      "sample_context": ..., "example_context": ..., "substrate": ..., "process": ...,
      "process_condition": ..., "test_method": ..., "test_standard": ..., "test_condition": ...,
      "property": ..., "resin": ..., "component": ..., "formulation_metric": ...,
      "test_method_hint": ..., "result": ..., "baseline": ...
    },
    "trace_rate": {
      "facts_with_evidence_id": <float, must be 1.0>,
      "hyperedges_with_evidence_ids": <float, must be 1.0>,
      "evidence_units_with_asset_path": <float, must be 1.0>
    }
  },
  "qa": {
    "review_status": "generated_for_user_review",
    "missing_obligation_count": <int>,
    "known_limitations": [
      "Cell-level bounding boxes are row-bbox approximations from MinerU table-body bbox.",
      "The pack is scoped to the Examples section and directly related method/process/result blocks.",
      "Confidence is heuristic, not calibrated."
    ]
  }
}
```

---

## 5. ID conventions (always)

| Prefix | Used for |
|---|---|
| `PAT_<DOC_ID>` | patent node |
| `PROFILE_<DOC_ID>` | patent profile |
| `EVD_<DOC_ID>_p<PAGE>_<descriptor>` | evidence_unit |
| `EXCTX_<DOC_ID>__<slug>` | example_context node |
| `FACT_<DOC_ID>_<6-digit>` | fact |
| `ENT_<TYPE>_<5-digit>` | canonical_entity (TYPE in uppercase, e.g. `MATERIAL`, `APPLICATION`) |
| `REL_<5-digit>` | canonical_relation |
| `EDGE_<12-hex>` | edge (md5-based) |
| `HEDGE_<DOC_ID>_<4-digit>` | hyperedge |

`<slug>` is `lower(re.sub(r"\W+", "_", text))` — no whitespace, no punctuation, lowercase.

`<descriptor>` for evidence_units uses short cues:
- `test_panel_preparation`, `cracking_test`, `salt_spray_test`, `example1_epoxy_process`, `table1_model_paint_1`, `table2_reference_paint_3`, etc.

---

## 6. Quality gates (run all of them before declaring done)

Run these as deterministic Python after writing all files:

1. **JSONL parse**: every `.jsonl` parses line-by-line.
2. **ID uniqueness**: `node_id` is unique per file; `fact_id`, `evidence_id`, `hyperedge_id`, `entity_id`, `edge_id` are globally unique.
3. **Edge type-whitelist**: every edge matches §4.8. Print first 3 violations and abort if any.
4. **Trace coverage**:
    - `facts_with_evidence_id == 1.0`
    - `hyperedges_with_evidence_ids == 1.0`
    - `evidence_units_with_asset_path == 1.0`
5. **Slot type matching**: for every `FactRecord` with `slot ∈ {resin, component}`, the `linked_canonical_entity_id` must point to a `CanonicalEntityRecord` with `entity_type == material`. Print first 3 violations and abort.
6. **Resin/property cross-check**: scan every edge `HAS_RESIN` → assert dst entity_type==material AND role∈{binder,resin,curing_agent,hardener}. Same for `HAS_COMPONENT` and `HAS_PROPERTY`. Print first 3 violations and abort.
7. **Manifest count match**: `manifest.counts.<key>` equals `wc -l <key>.jsonl` for every file.
8. **Examples scope check**: every evidence_unit has `page` ∈ `kg_input.examples_scope.detected_pages`. If not, flag as `out_of_scope` and exclude.

Report a one-line summary to the user at the end:

```
[done] <DOC_ID>: <N_patents>/<N_profiles>/<N_evidence>/<N_contexts>/<N_facts>/<N_canonical>/<N_relations>/<N_edges>/<N_hyperedges>  CSV: <N_rows> rows  Gates: <all_pass|first_failure>
```

---

## 7. CSV projection — `boss_preview.csv`

Schema (42 columns). One row per `hyperedge` where `bundle_type ∈ {formulation_context, performance_result}` (omit pure `test_context` rows).

| # | column | source from hyperedge | notes |
|---:|---|---|---|
| 1 | `patent_id` | `patent_id` | – |
| 2 | `doc_id` | `doc_id` | – |
| 3 | `hyperedge_id` | `node_id` | – |
| 4 | `context_id` | `context_id` | – |
| 5 | `example_id` | `example_id` | – |
| 6 | `sample_id` | `sample_id` | – |
| 7 | `bundle_type` | `bundle_type` | – |
| 8 | `polarity` | derived: `"positive"` if model paint, `"negative"` if reference paint, `"context"` if test_context | – |
| 9 | `application_tested` | `application.tested` | – |
| 10 | `application_claimed` | `application.claimed` | – |
| 11 | `substrate_tested` | `substrate.tested` | – |
| 12 | `substrate_claimed` | `substrate.claimed` | – |
| 13 | `resin_binder` | `;`-join of `resin[]` where `role∈{binder}`, each as `"<name>:<amount> <unit>"` | – |
| 14 | `resin_other` | `;`-join of `resin[]` where `role∈{resin, hardener}` | – |
| 15 | `crosslinker_curing_agent` | `;`-join of `resin[] ∪ additives[]` where `role==curing_agent` | – |
| 16 | `pigment_zinc` | `;`-join of `additives[]` where `role==pigment AND name contains "zinc"` | – |
| 17 | `pigment_conductive` | `;`-join of `additives[]` where `role==pigment AND name contains "graphite|carbon|graphene|tin oxide|mica"` | – |
| 18 | `pigment_other` | `;`-join of `additives[]` where `role==pigment` not in 16–17 | – |
| 19 | `filler_microsphere` | `;`-join where `role==filler AND name contains "microsphere|sphere|expancel|cenosphere"` | – |
| 20 | `filler_other` | `;`-join where `role==filler` not in 19 | – |
| 21 | `additive_corrosion_inhibitor` | `;`-join where `role_detail contains "corrosion inhibitor"` | – |
| 22 | `additive_dispersant` | `;`-join where `role==dispersant` | – |
| 23 | `additive_rheology` | `;`-join where `role==rheology_additive` | – |
| 24 | `additive_surface` | `;`-join where `role==surface_additive` | – |
| 25 | `additive_other` | `;`-join where `role==other` | – |
| 26 | `solvent` | `;`-join of `additives[]` where `role_detail contains "solvent\|xylene\|butanol\|naphtha"` OR canonical_entity category=solvent | – |
| 27 | `process_steps` | `;`-join of `process[].step_type` | – |
| 28 | `process_film_thickness` | `process[].conditions.film_thickness` of step=application | – |
| 29 | `process_cure_temp` | `process[].conditions.temperature` of step=curing | – |
| 30 | `process_cure_time` | `process[].conditions.time` of step=curing | – |
| 31 | `test_method` | `test_method.name` | – |
| 32 | `test_standards` | `;`-join of `test_method.standard_id[]` | – |
| 33 | `property_name` | `property.name` | – |
| 34 | `property_category` | `property.category` | – |
| 35 | `result_main` | first `result[].value + " " + result[].unit` | – |
| 36 | `result_all` | `;`-join of all `result[]` as `<metric>=<value> <unit>` | – |
| 37 | `baseline_sample_id` | `baseline.baseline_sample_id` | – |
| 38 | `baseline_result` | `baseline.baseline_result` | – |
| 39 | `evidence_ids` | `;`-join of `evidence_ids[]` | – |
| 40 | `evidence_pages` | `;`-join of `evidence[].page` distinct | – |
| 41 | `extraction_confidence` | `confidence.overall` | – |
| 42 | `qa_flags` | `;`-join of `qa_flags[]` | – |

CSV rules:

- Use `,` as field delimiter, `"` as quote char, `\n` as record sep, `utf-8-sig` encoding (so Excel reads Chinese cleanly).
- Quote every field containing `,`, `"`, `\n`, or `;`.
- Empty cells are `""`, not `null`.

---

## 8. Workflow checklist

Before you start writing JSONL:

- [ ] Read `kg_input.json` fully.
- [ ] Confirm `examples_scope.detected_pages` is non-empty AND matches the printed "Examples" / "Beispiele" / "实施例" markers. If empty, **stop and ask the user for `--force-pages` instead of guessing.**
- [ ] List the in-scope tables (`Table 1, Table 2, …`) and method paragraphs to extract.

While writing:

- [ ] Write each JSONL fully before opening the next.
- [ ] After each file, count lines with `wc -l` and store the count for `manifest.counts`.

After writing:

- [ ] Run §6 gates as a single Python script.
- [ ] Report the one-line summary.

If any gate fails, **fix the affected file and re-run gates** before reporting done. Do not silently continue with violations.

---

## 9. Reference example to mirror

A hand-crafted gold sample for `WO2015132366A1` is in `<repository-root>\extracting\scripts\manual_kg_pack\reference\WO2015132366A1\` (when present). Use it for: ID format, slot field shapes, hyperedge bundle structure. **Do not literally copy values** — extract from this patent's own `kg_input.json`.

---

End of prompt.
