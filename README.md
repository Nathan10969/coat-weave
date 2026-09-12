<p align="center"><img src="docs/assets/hero.svg" alt="CoatWeave: weaving coating patent evidence into knowledge graphs" width="100%" /></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-14b8a6" alt="MIT license" /></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-3b82f6" alt="Python 3.10 or newer" />
  <img src="https://img.shields.io/badge/PostgreSQL-pgvector-334155" alt="PostgreSQL with pgvector" />
  <img src="https://img.shields.io/badge/status-research%20prototype-64748b" alt="Research prototype" />
</p>

# CoatWeave

**Evidence-grounded knowledge graphs for coating patents.**

CoatWeave turns patent figures, tables and surrounding context into structured, traceable coating facts. Instead of reducing every result to a loose subject–relation–object triple, it represents a result as a multi-slot **fact hyperedge**: what was tested, under which conditions, with which comparison, and where the evidence can be found.

中文简介：面向涂料专利的证据型知识图谱原型。将图表、实施例上下文、材料与性能结果组织为可追溯的事实超边，保留页码、区域和对照信息，支持后续人工核验。

Created and maintained by [Nathan10969](https://github.com/Nathan10969). This initial release publishes an early development snapshot, not the later production system.

## From documents to evidence

<img src="docs/assets/architecture.svg" alt="Pipeline: patent PDF, MinerU layout, evidence units, Qwen descriptions and facts, canonical resolution, JSON artifacts and PostgreSQL" width="100%" />

The illustration describes the code flow, not a measured deployment or acceptance result.

- **Evidence first:** document, page, region and optional row/column pointers accompany facts.
- **Structured results:** Pydantic models and SQL represent materials, applications, substrates, properties, processes, test methods, patents and evidence.
- **Visual and contextual extraction:** MinerU REST, Qwen-compatible description, paragraph matching and fact parsing integrations are implemented.
- **Canonicalization with guardrails:** alias lookup and constraints separate synonyms from chemical subtypes and forbidden merges.
- **Explicit ambiguity:** polarity and comparison-group rules can leave unclear cases unresolved instead of inventing a baseline.
- **Reviewable artifacts:** per-unit descriptions, matches, proposed canonicals, coverage and facts accompany routing audit records.

## What is—and is not—ready

| Area | In this snapshot | Verification boundary |
|---|---|---|
| Section splitting, polarity, comparison groups | Implemented; offline tests included | Rule coverage, not extraction-accuracy validation |
| Schema, seeds and typed models | Implemented | SQL syntax is checkable offline; DB migration not revalidated here |
| MinerU and Qwen clients | Implemented integrations | Credentials/network required; no fresh end-to-end acceptance claim |
| Evidence-unit parsing and routing | Implemented; remaining format-related TODO comments | Validate with your actual MinerU output |
| Passage Tier-C embeddings | Deferred; currently a no-op | Not a complete embedding/retrieval layer |
| Production serving, accuracy and corpus coverage | Not established by this release | No production-readiness or performance claim |

Raw PDFs, private corpora, API keys, generated results and pretrained embedding models are **not included**. Historic technical notes in `docs/` describe development context; they are not independent validation of this public release.

## Start locally

Python 3.10+. Run from the cloned repository so prompts, schema and data paths remain available. Editable installation is recommended; this snapshot is not a self-contained deployment wheel.

```bash
git clone git@github.com:Nathan10969/coat-weave.git
cd coat-weave
python -m venv .venv
source .venv/bin/activate   # PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e . pytest
python -m coating_kg --help
```

The public project name is **CoatWeave**; `coating_kg` imports and CLI remain compatible.

### Offline checks: no database, GPU or API calls

```bash
python -m pytest tests/ -v
```

Tests cover English/Chinese/German section boundaries, polarity labels and comparison ambiguity. See [the offline walkthrough](SAMPLE_RUN.md) for interactive examples.

### Optional ingestion: external services required

```bash
cp .env.example .env
# Edit OPENAI_API_KEY, MINERU_TOKEN and input/output paths.
# Set DB credentials if using database writes.
docker compose up -d
bash scripts/setup_db.sh
python -m coating_kg ingest ./data/pdf/example.pdf --skip-db
```

`--skip-db` skips database writes, **not model calls**. `--dry-run` stops after evidence-unit materialization, but PDF parsing can still contact MinerU. Services may incur charges. Docker/`psql`/Bash are needed for DB setup; on Windows use a suitable Bash environment. The database password is a local-development placeholder—change it before exposing any service.

## A fact is more than a triple

Illustrative record only: identifiers and values below are invented, not extracted patent data or a scientific result.

```json
{
  "fact_id": "F_DEMO_001",
  "doc_id": "DEMO_PATENT",
  "application": "APP_DEMO_COATING",
  "property": "PROP_DEMO_GLOSS",
  "result_value": 80,
  "result_unit": "GU",
  "comparison_group": "F_DEMO_BASELINE",
  "evidence_pointer": {
    "doc_id": "DEMO_PATENT", "page": 12,
    "region_type": "TABLE", "region_id": "Table 2", "row": "Example 1"
  },
  "human_validated": false
}
```

This is a shortened conceptual view; consult [typed models](src/coating_kg/db/models.py) for required fields and validation.

## Explore the code

```text
src/coating_kg/
├── cli.py          Ingest, setup-db and entity-tag commands
├── db/             Models, connections, inserts and queries
├── pipeline/       Layout, units, routing, matching and facts
└── ontology/       Alias resolution and consistency checks
db/                 PostgreSQL schema and starter seeds
prompts/            Figure, table, matching and fact prompts
tests/              Offline deterministic-rule tests
scripts/            Development and batch utilities
```

Suggested reading: [fact models](src/coating_kg/db/models.py) → [comparison rules](src/coating_kg/pipeline/comparison_group.py) → [ingest CLI](src/coating_kg/cli.py) → [SQL schema](db/schema.sql).

## License

[MIT](LICENSE) · Copyright © 2026 Nathan10969. Covers repository code and original illustrations, not third-party patents, datasets or service terms.
