# Coating KG Field Contract

Status: draft v0.1

This document is the source-of-truth contract for coating patent extraction
semantics. It exists to prevent the pipeline from accumulating one-off regex
patches for every new table shape. Regex may normalize syntax; it must not own
open-ended coating semantics.

Compatibility note: the current `FactHyperedge` model is the executable v1
schema. This contract defines the target v2 semantics. Any new standalone field
in this document, such as `test_standard` or `cell_annotations`, requires an
explicit schema/model/export/test migration before code may depend on it. Until
then, standards may be stored in `test_condition.standard_id` or represented as
canonical `TEST_*` aliases/proposals.

## 1. Source Of Truth

The source of truth is the KG projection plus the per-unit audit artifacts:

- `data/kg/patents.jsonl`
- `data/kg/patent_profiles.jsonl`
- `data/kg/example_contexts.jsonl`
- `data/kg/evidence_units.jsonl`
- `data/kg/facts.jsonl`
- `data/kg/canonical_entities.jsonl`
- `data/kg/canonical_relations.jsonl`
- `data/kg/edges.jsonl`
- `data/units/U_*/normalized_facts.json`
- `data/units/U_*/facts.json` (raw Stage 7 audit only)
- `data/units/U_*/coverage.json`
- `data/units/U_*/matched_paragraphs.json`
- `data/units/U_*/table_ocr.json`
- `data/units/U_*/vlm_description.json`

`data/kg/*.jsonl` is the graph-shaped projection used by views, retrieval, QA,
and downstream loaders. The per-unit folders are audit artifacts and extraction
inputs; they are not view-layer semantic fallbacks.

The wide CSV is a boss-facing view and QA entry point. It is not a KG source of
truth and must not contain independent semantic inference that is absent from
the normalized facts / contexts.

## 2. Layer Definitions

Use these names consistently:

- **L1 Retrieval Context**: passage and figure context records for recall and
  display fallback.
- **L2 Structured Facts**: grounded `FactHyperedge` records extracted from
  evidence units.
- **Answer Hyperedges**: denser sample-scoped hyperedges produced by
  deterministic slot propagation across compatible L2 records.
- **KG Projection**: JSONL records materializing source artifacts, L1/L2
  records, contexts, canonicals, and edges.
- **WideCSVView**: boss-facing and QA projection from KG JSONL.

### L1 Retrieval Context

L1 stores broad retrieval/display context:

- patent passages
- structure/scheme figures
- VLM descriptions
- matched paragraphs linked to figures/tables
- chemical candidates from visual structure analysis

L1 answers: what surrounding text or visual object should retrieval show when a
structured fact is missing or when additional context is useful?

L1 does not decide final material/property/process facts by itself and does not
support quantitative claims unless a linked L2 fact exists.

### L2 Structured Facts

L2 stores atomic structured facts extracted from evidence units:

- material/formulation facts
- performance/result facts
- process facts
- test method/condition facts
- evidence pointers back to page/region/row/column/cell/bbox
- explicit vs inherited context provenance

L2 answers: what exact fact was stated, for which example, supported by
which evidence?

### Answer Hyperedges

Answer hyperedges are not raw model guesses and not CSV patches. They are
deterministic, sample-scoped records built after normalized L2 facts exist.
Their job is to join compatible formulation, process, substrate, test, and
performance facts into a denser answerable shape:

```json
{
  "hyperedge_id": "H_...",
  "patent_id": "WO...",
  "context_id": "CTX_...",
  "application": "APP_*",
  "substrate": {"tested": "SUB_*", "claimed": []},
  "resin": "MAT_*",
  "additives": ["MAT_*"],
  "process": [{"canonical_id": "PROC_*", "condition": "..."}],
  "property": "PROP_*",
  "test_method": "TEST_*",
  "test_condition": {"standard_id": "ASTM D3359", "film_thickness": "..."},
  "result": {"value_text": "5B", "unit": ""},
  "baseline": null,
  "evidence": [{"evidence_id": "EVD_...", "role": "result"}],
  "slot_provenance": {
    "substrate": {"source": "context_fact", "fact_id": "F_..."},
    "process": {"source": "same_context", "fact_id": "F_..."}
  },
  "unresolved_fields": [],
  "qa_flags": []
}
```

Allowed propagation scope:

1. same `patent_id`
2. same `context_id` / sample / example / comparative / control
3. same table or linked evidence block when table-local context is required
4. same test family when propagating method, standard, or condition

Allowed propagation targets:

- application
- tested substrate and claimed substrate
- process and cure/application conditions
- resin/binder, crosslinker, additives, pigments, fillers, solvents, monomers
- test method, test standard, film thickness, exposure, rating scale, and other
  test conditions
- baseline/comparative relation when sample binding is explicit

Forbidden propagation:

- no document-level profile default as a tested sample value
- no CSV-to-KG repair
- no global `unknown` merge
- no cross-example inheritance unless evidence states a shared preparation,
  common test method, or common formulation table scope
- no value invention for sparse columns

Every propagated slot must carry slot-level provenance and confidence. If the
slot is absent, the answer hyperedge must leave it empty or mark it unresolved /
excluded with a reason. A sparse CSV is acceptable only when the matching answer
hyperedge also lacks that slot. If the answer hyperedge has the slot and the CSV
does not show it, that is a projection bug.

### KG Projection

The KG projection materializes implicit pipeline links as explicit records:

- `PatentRecord`
- `PatentProfileRecord`
- `ExampleContextRecord`
- `EvidenceRecord`
- `FactRecord`
- `CanonicalEntityRecord`
- `CanonicalRelationRecord`
- `EdgeRecord`

`EvidenceRecord` is a source/evidence locator record. It is not a replacement
name for L1 Retrieval Context. `ExampleContextRecord` is a derived aggregation
from L2 facts, not a Stage 7 input and not an L1 passage/figure context.

The projection emits graph edges such as `SUPPORTED_BY`, `HAS_CONTEXT`,
`HAS_PROPERTY`,
  `HAS_RESIN_SYSTEM`, `HAS_PROCESS`, `COMPARES_TO_BASELINE`

KG export must read `normalized_facts.json` for `FactRecord` input.
`facts.json` is the raw Stage 7 audit artifact and must not be used as a KG
semantic fallback. If normalized facts are missing, the export should fail
clearly rather than recreate example/context inference.

### WideCSVView

The CSV view may pivot, format, and summarize fields from `data/kg/*.jsonl`.
It must not read `data/units/U_*` as a semantic fallback, re-resolve
`example_id`, create canonical entities, or infer application/substrate/process
from raw strings. If a value is absent from KG records, the CSV should show
`unknown` or a QA flag rather than inventing it.

## 3. Canonical Entity Types

Canonical IDs use these prefixes:

- `MAT_*`: material
- `PROP_*`: property/result
- `APP_*`: application
- `SUB_*`: substrate
- `PROC_*`: process
- `TEST_*`: test method

Allowed material sub-types:

- `Resin / binder`
- `Crosslinker / Curing agent`
- `Pigment`
- `Filler`
- `Additive`
- `Solvent`
- `Monomer`

Allowed application sub-types:

- `Automotive`
- `Industrial protective`
- `Specialty`
- `Architectural`

Allowed substrate sub-types:

- `Metal`
- `Plastic`
- `Mineral`

Canonical lifecycle:

1. `raw_mention`: surface text in evidence.
2. `proposed`: LLM-created candidate when no approved canonical fits.
3. `candidate_alias`: possible alias for an existing canonical.
4. `approved`: curated canonical entity.
5. `must_merge` / `forbidden_merge`: curation seed.
6. `deprecated` / `superseded`: entity kept for traceability but no longer
   preferred.

Proposed entities may appear in facts, but downstream views must expose their
status and must not treat them as equally stable with approved seed entities.

## 4. Field Definitions

### `resin_system` / Binder

The film-forming main resin or binder phase. Examples:

- acrylic resin
- polyurethane dispersion
- epoxy resin
- polyester resin
- polyol/prepolymer/oligomer that forms the coating network
- FEVE/PVDF/silicone-modified binder

Do not fill `resin_system` from a sample ID, product-line code, generic phrase
such as "coating composition", or commercial name unless the evidence states it
is the binder/resin.

### `crosslinker`

A material that cures or crosslinks the binder. Examples:

- HDI trimer
- blocked isocyanate
- melamine resin
- amine epoxy hardener
- aziridine crosslinker

Crosslinkers are not resin systems unless the patent explicitly treats them as
the main binder phase.

### `additives`, `pigments`, `fillers`, `solvents`, `monomers`

Use functional material role, not string similarity:

- defoamer, wetting agent, catalyst, UV absorber, HALS, wax, matting agent,
  adhesion promoter -> `Additive`
- TiO2, dye/colorant/colorant solids -> `Pigment`
- talc, CaCO3, SiO2 filler -> `Filler`
- water, xylene, butyl acetate, reactive diluent used as diluent -> `Solvent`
- HEMA, BA, MMA, other reactive monomers -> `Monomer`

Do not dump every unknown material into `Additive` if evidence supports a more
specific allowed material sub-type.

### `formulation_quantity`

Material amount or ratio used in a formulation:

- wt%
- pbw / parts by weight
- phr
- grams
- solids basis
- NCO/OH ratio
- equivalent ratio

Formulation quantities are formulation context facts, not performance facts.
They may become context for performance rows in the same example.

### `performance_property`

Experimental or evaluation outcome:

- adhesion / cross-cut / cross-hatch rating
- gloss
- MEK rubs
- corrosion / scribe creep / blistering
- fouling coverage
- cracking rating
- hardness
- flame test result
- pass/fail / OK / 5B / rating 1-6

Rows such as `Surface (film)`, `Surface (metal)`, `Small flame test`,
`visual assessment`, and ordinal ratings are performance facts when they are
evaluation outputs.

### `test_method`

The measurement method or protocol family, when stated by evidence:

- cross-cut adhesion
- MEK double rub
- salt spray
- rain erosion
- seawater immersion
- small flame test

Do not infer a method only from a property unless the evidence names the method
or a local table/caption convention clearly defines it.

### `test_standard`

Formal standard identifiers:

- ISO 2409
- ASTM D3359
- DIN 55654
- GB / JIS / EN standard numbers

`test_standard` is not the same field as `test_method`. The method may be
"cross-cut adhesion" while the standard is "ISO 2409".

Current v1 compatibility: `test_standard` is not yet a standalone
`FactHyperedge` field. If the schema is not migrated, store it inside
`test_condition.standard_id` and/or preserve it as the source text for a
canonical `TEST_*` proposal. Do not add a prompt-only field that downstream
models silently drop.

### `test_condition`

Measurement-specific parameters:

- film thickness
- dry/wet state
- exposure duration
- temperature / humidity
- immersion medium
- angle such as 20/60/85 degrees
- rating scale definition
- replicate position
- load, speed, geometry

Do not place conditions in `test_method`.

### `process`

Preparation, application, and curing process:

- mixing
- grinding / dispersion
- spray application
- drawdown
- dip coating
- electrocoating
- thermal cure
- UV cure
- ambient cure
- aging before test

`process` is not the test method. "Salt spray 500 h" is a test method/condition,
not a coating process.

Process step representation must be one canonical shape:

```json
{"canonical_id": "PROC_*", "condition": "...", "source": "..."}
```

Existing code may still contain legacy `{"step": "PROC_*"}` entries. Readers
must accept both during migration, but writers should emit `canonical_id`.

## 5. Evidence Strength

Strong evidence:

- table cell value
- row/column/header labels
- table caption and footnote
- figure/table VLM OCR with visible support
- matched paragraph that directly references the same table/figure/example

Weak evidence:

- patent title
- abstract
- broad doc profile
- unrelated claims/background text
- generic product names without definition

Weak evidence may fill context only through Stage 7.5 inheritance with a
confidence cap and provenance. It must not create a primary result fact.

## 6. Table Cell Roles

Stage 7 must classify meaningful table cells/rows/columns into roles before
emitting facts:

- `RESULT_VALUE`: measured outcome, rating, score, pass/fail, qualitative
  evaluation, yield, adhesion, gloss, corrosion, fouling, cracking, flame result.
- `PROPERTY_LABEL`: row/column/header that names the measured property.
- `EXAMPLE_ID`: sample/run/example/formulation identifier.
- `FORMULATION_CONTEXT`: material amount, ratio, component dosage, formulation
  feed.
- `PROCESS_CONDITION`: preparation/application/curing setting.
- `TEST_METHOD`: method/protocol family.
- `TEST_STANDARD`: ISO/ASTM/DIN/GB/JIS standard identifier.
- `TEST_CONDITION`: exposure, substrate/panel, film thickness, scale,
  temperature, humidity, geometry, replicate position.
- `NON_DATA`: blank, separator, pure header, footnote marker, page/footer noise.

Coverage denominator counts only `RESULT_VALUE` cells that should produce
facts. Formulation/context cells must not inflate result coverage.

## 7. Normalized Fact Contract

Stage 7 should emit raw extraction plus cell annotations. Stage 7.5 should
produce normalized facts that downstream stages consume.

`cell_annotations` are required in the target v2 Stage 7 output. They are not
part of the current v1 `FactHyperedge` model and should be stored as a sidecar
or added through a model/schema migration before being treated as mandatory.

Required normalized fact fields:

```json
{
  "schema_version": "fact_v2",
  "fact_id": "F_<unit_id>_<seq>",
  "doc_id": "AU...",
  "example_id": "E1",
  "property": "PROP_*",
  "result_value": 1.23,
  "result_value_text": "as printed",
  "polarity_hint": "positive",
  "evidence_pointer": {
    "doc_id": "AU...",
    "unit_id": "U_...",
    "page": 12,
    "region_id": "Table 3",
    "row": "as printed",
    "column": "as printed",
    "cell": "as printed",
    "bbox": []
  },
  "application": "APP_*",
  "substrate": {"tested": "SUB_*", "claimed": []},
  "resin_system": "MAT_*",
  "additives": ["MAT_*"],
  "process": [{"canonical_id": "PROC_*", "condition": "..."}],
  "test_method": "TEST_*",
  "test_condition": {"film_thickness": "...", "standard_id": "ISO 2409"},
  "context_provenance": {
    "resin_system": {
      "source_type": "example_context",
      "source_id": "CTX_...",
      "source_field": "resin_systems",
      "evidence_id": "EVD_...",
      "confidence_cap": 0.86,
      "evidence_excerpt": "...",
      "inherited_stage": "stage7_5",
      "rule_version": "context_inheritance_v2"
    }
  },
  "extraction_confidence": 0.84
}
```

Primary result fields (`property`, `result_value`, `result_value_text`,
`evidence_pointer`) must be explicit. They cannot be inherited from profile or
matched paragraphs.

## 8. Inheritance Matrix

Only missing or unknown fields may be filled. Never overwrite an explicit fact.

Priority:

`fact explicit > example_context > unit_context > matched_paragraphs > patent_profile > unknown`

Field-specific rules:

| Field | Allowed inheritance | Cap |
| --- | --- | --- |
| `property` | explicit fact only | none |
| `result_value` / `result_value_text` | explicit fact only | none |
| `evidence_pointer` | explicit fact only | none |
| `resin_system` | example_context > unit_context > matched_paragraphs > patent_profile | profile <= 0.72 |
| `additives` | example_context > unit_context > matched_paragraphs | no profile default |
| `process` | example_context > unit_context > matched_paragraphs > patent_profile.cure_mechanism | profile <= 0.76 |
| `test_method` | example_context > unit_context > matched_paragraphs | no patent_profile fallback |
| `test_standard` | unit/evidence text > matched_paragraphs | exact standard only |
| `test_condition` | unit/evidence text > matched_paragraphs > example_context | no patent_profile fallback |
| `application` | example_context > unit_context > matched_paragraphs > patent_profile | profile <= 0.80 |
| `substrate` | example_context > unit_context > matched_paragraphs > patent_profile | profile <= 0.72 |

Every inherited value must include provenance:

- `source_type`
- `source_id`
- `source_field`
- `evidence_id` / `context_id` / `profile_id`
- `confidence_cap`
- `evidence_excerpt`
- `inherited_stage`
- `rule_version`

## 9. Confidence Rubric

Confidence is QA metadata, not model self-approval.

- `>= 0.95`: value, property, row/column mapping, scale, and context are clear.
- `0.85-0.94`: value/property clear; minor missing context.
- `0.70-0.84`: OCR/header noise, inferred property label, incomplete row-specific
  context, or inherited context.
- `0.50-0.69`: significant inference, weak matched paragraph support, uncertain
  method/scale.
- `< 0.50`: review candidate.

Mandatory caps:

- no matched paragraph support when context is needed: `<= 0.85`
- OCR/header damage: `<= 0.80`
- row-specific condition omitted: `<= 0.80`
- partial result-row extraction: `<= 0.75`
- proposed canonical: `<= 0.85` unless source mapping is obvious
- patent_profile fallback for resin/application/substrate/process: use the caps
  in the inheritance matrix

## 10. Responsibility Split

### LLM / VLM responsibilities

- table structure understanding
- cell role classification
- mapping rows/columns/cells to facts
- deciding whether a row is result vs formulation vs condition
- extracting unit context from caption/footnote/matched paragraphs
- proposing missing canonicals with source text
- reporting semantic coverage and confidence rationale

### Deterministic code responsibilities

- schema validation
- fact ID assignment
- evidence pointer completion from pipeline metadata
- canonical exact/alias validation
- proposed canonical aggregation
- inheritance priority and provenance
- confidence caps
- semantic coverage aggregation
- KG export
- CSV projection from normalized facts/context only

### Regex responsibilities

Allowed:

- numeric/unit parsing
- standard ID extraction (`ISO`, `ASTM`, `DIN`, `GB`, `JIS`)
- empty/dash/ND cleanup
- exact alias lookup
- deterministic heading/state-machine support

Not allowed:

- open-ended material role classification
- deciding whether a row is a result or formulation context
- inventing test methods from property labels
- broad coating/non-coating semantic decisions
- ad hoc fixes for each new table layout

## 11. Stage Contracts

### Stage 4 / Table VLM

For tables with images, Stage 4 may produce corrected table text/HTML and OCR
notes. It should not emit final facts. Its output is evidence assistance for
Stage 7.

### Stage 7 / Fact Extraction

Stage 7 must emit:

- `unit_context`
- `cell_annotations`
- `facts`
- `proposed_canonicals`
- semantic `coverage`

It should separate `test_method`, `test_standard`, and `test_condition`.
During v1 compatibility, this means preserving standards under
`test_condition.standard_id` unless the `FactHyperedge` schema has been migrated
to include a standalone `test_standard` field.

### Stage 7.5 / Normalization And Context Inheritance

Stage 7.5 converts raw Stage 7 output into the normalized fact contract:

- fill only missing/unknown context fields
- apply the inheritance matrix
- record context provenance
- cap confidence
- preserve explicit fact evidence

### Stage 8 / Answer Hyperedge Build

Stage 8 consumes normalized facts, example contexts, evidence units, and
canonical records. It builds denser answer hyperedges by joining compatible L2
records under the propagation rules in the Answer Hyperedges section.

Stage 8 may propagate context slots only with slot-level provenance. It must not
invent a value, read CSV as input, use patent profile as tested sample evidence,
or merge unresolved values into a global `unknown`. It should expose
`explicit_slots`, `propagated_slots`, `unresolved_fields`, `excluded_fields`,
and `qa_flags` so sparse downstream views can be audited.

### Stage 9 / CSV Projection

Stage 9 reads normalized facts, example contexts, and answer hyperedges. It must
not independently infer semantic fields. It may format, pivot, sort, and
aggregate only. For boss-facing CSV, answer hyperedges are the preferred source
because they already contain deterministic context-slot propagation.

### KG Export

KG export reads normalized facts/context/evidence and materializes nodes/edges.
It should expose proposed vs approved entity status and avoid dangling canonical
endpoints by exporting every referenced canonical/proposed entity.

## 12. Implementation Checklist

P0:

- Keep this document updated when semantics change.
- Add tests that fail when CSV or KG semantics diverge from normalized facts.

P1:

- Update `prompts/fact_extract.txt` to emit `cell_annotations` and split
  `test_method` / `test_standard` / `test_condition`.
- Update `src/coating_kg/db/models.py` if any field becomes part of the fact
  model rather than a sidecar or nested `test_condition` value.
- Update Stage 7.5 normalizer to implement this inheritance matrix and richer
  provenance.
- Update CSV projection to consume normalized facts / ExampleContext rather
  than doing independent semantic inference.
- Add deterministic answer-hyperedge build and tests for slot propagation,
  especially substrate/process/test condition propagation within the same
  `context_id` and evidence scope.

P2:

- Update KG export schema/tests so every fact/context/evidence edge follows this
  contract.
- If DB mode needs the new fields, update `db/schema.sql` and
  `src/coating_kg/db/insert.py` in the same change.
- Add audit checks for dangling canonical endpoints, partial coverage, inherited
  profile fallbacks, and proposed canonical rates.

## 13. Query-Facing Dimension Contract: application_family / applications / substrates

> Added 2026-06-12 after the fiber-count incident (same question answered
> 0/31/554). Root cause: these three dimensions were never defined in this
> contract, so build-side LLMs free-texted 454 distinct application_family
> labels over 554 patents and the serving side guessed at matching semantics.

### 13.1 Definitions

- **application_family** — the patent's end-use BUSINESS DOMAIN, from the
  CLOSED 25-key taxonomy in `coating_api_service/config/
  application_family_taxonomy.json` (marine, automotive, optical_fiber,
  protective_anticorrosive, ...). Multi-valued. Query semantics: exact match
  on family keys; OR within the list.
- **applications** — free-text end-use phrases as stated in the document
  (weak evidence, doc-profile tier). Not used for hard filtering.
- **substrates** — the MATERIAL the coating is applied to (steel, optical
  fiber, carbon fiber, concrete...). Query semantics: normalized fuzzy match.

### 13.2 Axis adjudication rules (what goes where)

1. **Function/performance words are NOT application families**: anti_icing,
   self_cleaning, antimicrobial, thermal_insulation, vibration_damping →
   property dimension. (33 such values were polluting application_family.)
2. **Coating form/technology words are NOT application families**:
   waterborne, powder_coating, clearcoat, high_temperature, multilayer →
   coating-type facet. (19 values.)
3. **Dual-nature concepts (光纤 case)**: when a material is both the substrate
   and the de-facto market (optical fiber, coil steel), tag BOTH
   substrates=<material> AND application_family=<family key>. Never only one.
4. **Extraction output must be a taxonomy key**, not a free phrase. Sentence-
   like labels ("Water-based multilayer automotive coating system; ...") are
   extraction bugs; fix the prompt, do not extend the taxonomy.
5. **Hypernyms resolve to the family**: automotive_oem/refinish/trim are all
   `automotive`; sub-scenario detail belongs in `applications` free text.

### 13.3 Data layout after the 2026-06-12 remap

`patent_profiles.jsonl`: `application_family` holds canonical family keys
(query axis); `application_family_raw` preserves the original free-text
labels (drill-down + re-taxonomy). Remap script:
`coating_api_service/scripts/remap_application_family.py` (idempotent,
re-derives from raw). After any KG rebuild: re-run remap → restart
coating-kg-tools → re-run `scripts/export_kg_vocab.py`.

### 13.4 Pipeline obligation

Stage emitting doc profiles must constrain `application_family` to taxonomy
keys (closed enum in the extraction prompt mirroring
`application_family_enum.json`), and route rule-13.1/13.2 words to their own
dimensions. Until that lands, the serve-side remap script is the enforcement
point.
