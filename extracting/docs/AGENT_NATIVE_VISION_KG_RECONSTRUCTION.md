# Agent-native Vision KG Reconstruction

## Summary

This contract preserves the workflow for clean coating patent KG reconstruction
when automatic MinerU-style pipeline output is semantically unreliable.

The workflow is agent-native: Codex agents visually read rendered PDF pages and
table crops, decide scope, extract evidence, bind samples, classify material
roles, build L2 answer hyperedges, and audit drift. Mechanical scripts only
render, save, validate, project, and package.

This is not a production MinerU rerun, not a CSV repair pass, and not a
rule-based parser for coating semantics.

## Core Boundary

scripts must not perform semantic extraction.

Allowed mechanical scripts:

- Render PDF pages to PNG.
- Create per-patent run folders.
- Persist agent-provided JSONL/JSON/CSV files.
- Validate JSON syntax, required fields, enum values, counts, references, and
  trace rates.
- Project CSV rows from `hyperedges.jsonl`.
- Zip final packs.

Forbidden script behavior:

- Deciding resin, crosslinker, additive, pigment, solvent, property, test,
  sample, baseline, result, or hyperedge semantics.
- Matching result rows to sample rows by rules.
- Repairing KG from CSV strings.
- Inferring roles from global material dictionaries unless an agent explicitly
  records patent-local evidence.
- Promoting MinerU/raw OCR cells into facts without agent review.

## Agent Workflow

1. `vision-page-triager` reads page images and identifies Examples scope,
   preparation paragraphs, test methods, formulation tables, result tables,
   captions, footnotes, and out-of-scope claims/description pages.
2. `vision-l1-evidence` creates L1 evidence units only. It does not make cross-
   page conclusions or build hyperedges.
3. `vision-table-reader` visually reads tables and emits sample-scoped facts
   for formulation components, formulation metrics, test conditions, and
   performance results.
4. `vision-context-builder` resolves `example_contexts`: example id, sample id,
   sample kind, baseline sample, source tables, and unresolved reasons.
5. `vision-hyperedge-builder` combines compatible L2 facts into answerable
   sample-scoped hyperedges with evidence traces.
6. `vision-kg-auditor` checks omissions, semantic drift, dangling evidence,
   unresolved samples, CSV provenance, and known role mistakes.

## Output Contract

Each patent pack must contain the 10 KG files plus one CSV projection:

- `patents.jsonl`
- `patent_profiles.jsonl`
- `evidence_units.jsonl`
- `example_contexts.jsonl`
- `facts.jsonl`
- `canonical_entities.jsonl`
- `canonical_relations.jsonl`
- `edges.jsonl`
- `hyperedges.jsonl`
- `manifest.json`
- `projected_same_columns.csv`

CSV is a derived view. It must be projected from `hyperedges.jsonl` and must not
repair or supplement KG semantics.

Every semantic record must carry enough traceability for audit:

- `patent_id`
- `evidence_id` where applicable
- `context_id` where sample/context scoped
- `sample_id` where sample scoped
- explicit `qa_flags` or unresolved reasons when confidence is limited

## Hard Semantic Rules

- Crosslinkers, hardeners, curing agents, initiators, and catalysts do not go
  into resin/binder slots unless the patent evidence explicitly states binder
  function.
- PVC, SVR, Total, Component 1, Component 2, Mixing ratio, Reference paint,
  Model paint, Ingredients, and Amount by weight are not additives.
- Test methods and standards are not properties.
- Performance results are not formulation fields.
- Generic claims and description-only statements are not tested example facts.
- Each model/control/comparative/reference sample must resolve to an
  `example_context` unless genuinely impossible.
- Each hyperedge must reference one or more L1 evidence units.

## Gold Test

Use `WO2015132366A1` as the first reconstruction test before batch expansion.
The known scope includes the Examples section, preparation/test-method
paragraphs, Tables 1-6, salt spray test results, and cracking test results.

Minimum acceptance:

- Manifest counts equal JSONL line counts.
- `facts_with_evidence_id == 1.0`.
- `hyperedges_with_evidence_ids == 1.0`.
- No dangling evidence/context/edge references.
- CSV rows are explainable from `hyperedges.jsonl`.
- Spot checks pass for Tolonate HDT90 as crosslinker/curing agent, PVC/SVR/Total
  as formulation metrics, test methods outside property fields, and results
  outside formulation fields.

## Batch Policy

Scale through small smoke batches first. For each batch, keep per-patent folders
with page images, agent outputs, final KG files, projected CSV, validation
report, and failure JSON. Failed or ambiguous patents continue only when their
failure reason is explicit and the skipped work is recorded.
