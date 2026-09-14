# Pipeline quality gate audit

## Main flow

1. `pdf_layout.py`
   - Input: PDF.
   - Output: MinerU `content_list/layout/middle` JSON.
   - Current gate: API terminal state only.
   - Missing gate: page/block/table/image count sanity, empty OCR detection, per-doc parse quality score.

2. `patent_metadata_extractor.py`
   - Input: first-page MinerU text.
   - Output: `data/patents/<doc_id>__patent_meta.json`.
   - Current gate: `is_coating_patent` boolean plus reason.
   - Missing gate: metadata extraction confidence and fallback source marker.

3. `unit_extractor.py` + `unit_materializer.py`
   - Input: MinerU blocks.
   - Output: `FigureTableUnit` folders under `data/units/<unit_id>/`.
   - Current gate: skip empty table HTML.
   - Missing gate: unit completeness score, image/table source existence, Examples span confidence.

4. `vlm_describe.py`
   - Input: figure image or table HTML.
   - Output: VLM JSON description.
   - Current gate: retry and JSON parse.
   - Missing gate: schema validation, closed-enum validation, confidence from model or deterministic checks.

5. `unit_router.py`
   - Input: unit + VLM description.
   - Output routes: `extract_facts`, `register_layer1`, `delete`.
   - Current gate: deterministic three-way routing.
   - Added: `RouteDecision.confidence`, audit CSV `route_confidence`, pending figure confidence can inherit router confidence.
   - Missing gate: low-confidence route review queue and table-subject mismatch checks.

6. `paragraph_extractor.py` + `paragraph_matcher.py`
   - Input: Examples paragraphs + unit description.
   - Output: matched paragraphs with `score`.
   - Current gate: matcher score is kept but not enforced.
   - Missing gate: minimum score threshold before using prose context in fact extraction.

7. `fact_extractor.py`
   - Input: unit HTML/description + matched paragraphs.
   - Output: `FactHyperedge` list, proposed canonicals, coverage.
   - Current gate before this audit: coverage warning only, schema validation skip.
   - Added: `ExtractionResult.quality`, `rejected_facts`, low-coverage review/fail state, required `application/property` canonical gate.
   - Missing gate: cell-level alignment proof, unit/value parsing confidence, row polarity confidence.

8. `passage_extractor.py`
   - Input: full MinerU layout plus ontology aliases.
   - Output: Layer 1 passages and figures with confidence.
   - Current gate: skip passages with no entities; heuristic confidence.
   - Added: cheap first-character prefilter in `AliasMatcher`.
   - Missing gate: proper alias matcher such as Aho-Corasick once ontology grows.

## Recommended gate model

Use a shared stage result envelope for every artifact:

```json
{
  "stage": "fact_extraction",
  "status": "pass|review|fail",
  "confidence": 0.0,
  "issues": [],
  "metrics": {},
  "evidence": {}
}
```

Suggested thresholds:

- MinerU parse: `fail` if no layout JSON or first page text `<50 chars`; `review` if table/image count is unexpectedly zero for known patent families.
- Metadata: `fail` if `is_coating_patent=false`; `review` if LLM fallback to regex or title/IPC missing.
- Unit extraction: `review` if Examples section not found and whole-doc fallback is used.
- VLM description: `fail` if JSON invalid after retry; `review` if required closed-enum fields are unknown.
- Router: `review` if route confidence `<0.7`; never hard delete, only quarantine.
- Paragraph match: `review` if max score `<0.5`; omit paragraph context from fact prompt if too weak.
- Fact extraction: `review` if coverage `<60%`; `fail` if extracted facts exist but accepted facts are zero; reject facts whose required `application/property` cannot resolve.
- Layer 1: `review` if confidence `<0.6` or all entities come from doc-level LLM with no alias support.

## Speed priorities

1. Keep `unit_router.py` before fact extraction. It already avoids expensive LLM calls for visual-only units.
2. Batch independent VLM/fact calls by document with bounded concurrency.
3. Cache VLM/fact outputs by hash of image/table HTML + prompt version.
4. Replace `AliasMatcher` substring scan with Aho-Corasick when ontology is large.
5. Avoid calling paragraph matcher for units whose router confidence already sends them to `register_layer1` or `delete`.
6. Add manifest-level incremental rerun: skip stages whose input hash and output schema version are unchanged.
