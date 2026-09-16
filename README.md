<p align="center"><img src="docs/assets/hero.svg" alt="CoatWeave: coating patent evidence and knowledge graphs" width="100%" /></p>

# CoatWeave

Evidence-grounded coating patent extraction and tool-oriented research, created and maintained by [Nathan10969](https://github.com/Nathan10969).

中文简介：面向涂料专利的证据抽取、知识图谱与检索工具研究。本仓库按时间线公开历史源码；长期方向是构建类似 Codex / Claude Code 的 coating research agent，但当前快照不是完整 agent或生产部署声明。

## Snapshot 04 — extraction and service split

This release publishes timeline snapshot **04**, dated **2026-06-02 13:33:02 +08:00**, from development branch `26_6_2` (source tip `1590daba854ad532452e76ba6ae3219a32a13db8`). The publication commit uses its actual publication date. The source date above is preserved provenance, not a newly claimed development date.

The frozen ZIP contains 131 files in two source areas. `extracting/` holds the patent-to-KG pipeline; `service/` adds a historical API, dialogue-memory, routing and KG-tool service snapshot. Public branding, this README, the MIT license and original illustrations accompany the source. Earlier versions remain available through immutable tags [`snapshot-01`](https://github.com/Nathan10969/coat-weave/tree/snapshot-01), [`snapshot-02`](https://github.com/Nathan10969/coat-weave/tree/snapshot-02) and [`snapshot-03`](https://github.com/Nathan10969/coat-weave/tree/snapshot-03).

## Repository map

- [`extracting/`](extracting/) — `coating_kg` package, CLI, prompts, database schema/seeds, Vision KG helpers and design notes.
- [`service/`](service/) — historical FastAPI layer, orchestration, routing/answering, memory, KG tools, storage and deployment examples.
- [`SNAPSHOT_26_6_2.md`](SNAPSHOT_26_6_2.md) — source-snapshot scope and exclusions.
- [`MANIFEST.json`](MANIFEST.json) — public inventory derived from the frozen archive; paths and hashes reflect the sanitized public tree where applicable.

<img src="docs/assets/architecture.svg" alt="Conceptual coating patent evidence pipeline" width="100%" />

The illustration is a conceptual overview, not a claim that every external integration is operational.

## Local inspection

Python 3.10 or newer is declared by the extraction package. To inspect its CLI without contacting external services:

```bash
cd extracting
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
coating_kg --help
```

The service is a separate historical component with its own [`requirements.txt`](service/requirements.txt) and empty credential placeholders in [`service/config/.env.example`](service/config/.env.example). Configure your own local paths and credentials before attempting an integration run. Do not commit the resulting `.env` file.

## Validation boundaries

For this publication, the ZIP size and SHA-256 are checked against the timeline, and archive paths are checked for traversal. Python files are syntax-checked and import/CLI checks are run only where the available offline environment supports them. This snapshot intentionally excludes its original tests, data and runtime artifacts, so it does **not** carry a fresh model-quality, database, API, GPU, production-readiness or end-to-end acceptance claim.

Raw PDFs, private corpora, credentials, pretrained model weights, generated indexes, logs and runtime state are not included. Public-facing paths and credential placeholders are sanitized without redesigning the historical business logic. Historical documents can describe intended or previously observed work; treat those statements as source context rather than independently reproduced results.

## License

[MIT](LICENSE) · Copyright © 2026 Nathan10969. Covers repository code and original illustrations, not third-party patents, datasets, models or service terms.
