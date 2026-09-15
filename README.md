<p align="center"><img src="docs/assets/hero.svg" alt="CoatWeave: coating patent evidence and knowledge graphs" width="100%" /></p>

# CoatWeave

Evidence-grounded coating patent knowledge-graph research, created and maintained by [Nathan10969](https://github.com/Nathan10969).

中文简介：面向涂料专利的证据型知识图谱研究。本版公开历史源码，供阅读与追溯；它不是生产部署或科学验收声明。

## Snapshot 03 — historical source archive

This release publishes timeline snapshot **03**, dated **2026-05-14 10:19:32 +08:00**, from development branch `master` (source tip `1a52120cd3706b448f4274eac585c8ffcbfc43d6`). The publication commit uses its actual publication date; the source timestamp above is not a newly claimed development date.

The frozen ZIP contains 90 files, including 62 Python files. Public branding, this README, the MIT license and original illustrations accompany the source. Earlier releases remain available through [snapshot-01](https://github.com/Nathan10969/coat-weave/tree/snapshot-01) and [snapshot-02](https://github.com/Nathan10969/coat-weave/tree/snapshot-02); this version will be fixed by the immutable `snapshot-03` tag.

## What the source contains

- A `src/coating_kg` package and command-line entry point.
- PostgreSQL/pgvector schema plus four seed SQL files.
- Prompts for figure, table, paragraph and fact extraction.
- Patent layout, evidence-unit, fact and canonical-resolution pipeline code.
- Five offline test modules and historical pipeline scripts and design notes.

Read [the sample walk-through](SAMPLE_RUN.md), [pipeline stages](docs/PIPELINE_STAGES.md), [CLI source](src/coating_kg/cli.py), [fact models](src/coating_kg/db/models.py) and [schema](db/schema.sql). Historical reports describe intended or observed development work; they are not independently revalidated production claims.

<img src="docs/assets/architecture.svg" alt="Conceptual coating patent evidence pipeline" width="100%" />

The illustration is a conceptual project overview, not a claim that every external integration is operational.

## Local setup

Python 3.10 or newer is declared by the package metadata. A lightweight offline setup is:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pytest
coating_kg --help
```

Copy `.env.example` to `.env` only when configuring optional API, database or corpus integrations. Its API-key and API-token fields are empty and its data paths are repository-relative. The database values are local-development defaults; replace them before connecting to any exposed or remote database.

## Validation boundaries

For this publication candidate, the frozen ZIP size and SHA-256 were checked against the timeline; archive paths were also checked for traversal and symlinks. The package, tests, CLI and SVG assets are validated offline before publication. The source includes dependencies and integration paths that require services or credentials; no paid API, database, GPU, corpus ingestion or end-to-end extraction acceptance is claimed.

Raw PDFs, private corpora, credentials, pretrained models and generated runtime results are not included. Public-facing paths and credential placeholders are sanitized without refactoring historical business logic.

## License

[MIT](LICENSE) · Copyright © 2026 Nathan10969. Covers repository code and original illustrations, not third-party patents, datasets or service terms.
