# SAMPLE_RUN — offline smoke test

This document walks through the parts of `coating_kg` that need **no
PostgreSQL, no DashScope key, no MinerU GPU** — i.e. the deterministic
logic of V1.2.2 you can run on a fresh laptop the moment you `git clone`.

## Prereqs

Python 3.10+, then:

```bash
cd coat-weave
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install pydantic pytest python-dotenv
```

(That's all you need for the offline tests — the full
`requirements.txt` adds database and API-client dependencies which the offline
flow doesn't touch.)

## 1. Run the unit tests

```bash
pytest tests/ -v
```

The suite covers:

- `test_section_split.py` — English, Chinese, German Examples-section
  detection + the no-Examples and `EMBODIMENTS` variants.
- `test_polarity.py` — 13 row labels across 3 languages, plus
  VLM-hint override behaviour.
- `test_comparison_group.py` — single negative auto-binds, 0 or
  multiple negatives → manual review, different-property negatives ignored,
  self-referential pool excluded.

## 2. Try the section splitter on your own text

```python
from coating_kg.pipeline.section_split import split_examples_section

text = open("some_patent.txt", encoding="utf-8").read()
span = split_examples_section(text)
print("Examples section spans:", span)
print(text[span[0]:span[1]][:500] if span else "(none found)")
```

## 3. Try the polarity classifier interactively

```python
from coating_kg.pipeline.polarity import classify_polarity

print(classify_polarity("Comp Example C1"))   # negative
print(classify_polarity("Example E4"))        # positive
print(classify_polarity("实施例 7"))           # positive
print(classify_polarity("对比例 2"))           # negative
print(classify_polarity("Sample 99"))         # unknown
print(classify_polarity("Sample 99", vlm_hint="positive"))  # positive
```

## 4. Try the comparison-group resolver

```python
from coating_kg.db.models import (
    EvidencePointer, FactHyperedge, Polarity, SourceSectionType,
)
from coating_kg.pipeline.comparison_group import resolve_comparison_group

def f(fid, pol, row):
    return FactHyperedge(
        fact_id=fid, application="APP_automotive_oem_clearcoat",
        property="PROP_gloss_20deg",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity(pol),
        evidence_pointer=EvidencePointer(
            doc_id="WO_X", page=22, region_type="TABLE",
            region_id="Table 5", row=row,
        ),
        ontology_version="v0.1", extraction_confidence=0.9,
        human_validated=False, doc_id="WO_X",
    )

pos = f("F1", "positive", "Example E4")
neg = f("F2", "negative", "Comp Example C1")
print(resolve_comparison_group(pos, [pos, neg]))   # → "F2"
```

## 5. Try the SQL parser (no DB needed)

```bash
pip install pglast
python -c "
import pglast
for path in [
    'db/schema.sql',
    'db/seed_property_directionality.sql',
    'db/seed_canonical_starter.sql',
    'db/seed_forbidden_merge_starter.sql',
    'db/seed_must_merge_starter.sql',
]:
    pglast.parse_sql(open(path, encoding='utf-8').read())
    print('OK', path)
"
```

If all five print `OK`, the SQL syntax was accepted by the parser. This is
not a database execution or migration check.

## What you still need a real environment for

| Module | Needs |
|---|---|
| `pipeline.pdf_layout.parse_pdf` | MinerU API token + network |
| `pipeline.vlm_describe.QwenVLClient` | `DASHSCOPE_API_KEY` + network |
| `pipeline.fact_extractor.FactExtractor` | same DashScope key |
| `db/insert.py`, `db/query.py`, the CLI ingest path | running PostgreSQL with pgvector |

Validate integrations separately. Offline tests do not establish extraction
quality, corpus coverage or production readiness.
