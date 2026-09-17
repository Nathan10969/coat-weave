<p align="center">
  <img src="docs/assets/hero.svg" alt="CoatWeave" width="100%" />
</p>

# CoatWeave

**Historical source snapshot 05** — a source-only milestone toward a tool-using coating research agent.

[![License: MIT](https://img.shields.io/badge/License-MIT-7c3aed.svg)](LICENSE)
[![Snapshot](https://img.shields.io/badge/snapshot-05-0f766e.svg)](SNAPSHOT_20260812_123244.md)

CoatWeave is evolving toward a coating-domain agent in the spirit of modern coding agents: it should plan, call focused tools, retrieve dispersed evidence, and produce traceable answers. This snapshot is not a complete agent or a production release. It preserves two code areas that existed at this point in the project timeline:

- `extracting/`: patent knowledge-graph extraction, projection, validation, database helpers, prompts, and offline tests.
- `service/`: API orchestration, routing, dialogue-memory support, and read-only knowledge-graph retrieval tools.

The retrieval path is broader than ordinary chunk matching. The service contains hybrid search and graph expansion hooks, plus document-field scanning for cases where coating evidence is spread across a document. Tool invocation alone does not prove that evidence supports an answer; automatic citation entailment and end-to-end scientific validation remain future work.

<p align="center">
  <img src="docs/assets/architecture.svg" alt="CoatWeave snapshot architecture" width="92%" />
</p>

## Quick start

The extraction package requires Python 3.10 or newer:

```bash
cd extracting
python -m venv .venv
python -m pip install -e ".[dev]"
python -m coating_kg --help
pytest -q
```

The service is a separate application with its own dependencies and configuration template:

```bash
cd service
python -m venv .venv
python -m pip install -r requirements.txt
cp config/.env.example .env
```

API keys and tokens are intentionally empty. Database, model, parsing-service, and corpus-dependent flows require infrastructure that is not included here. Do not treat local import or offline-test success as evidence of deployment readiness, retrieval quality, or scientific correctness.

## Snapshot record

| Field | Value |
|---|---|
| Sequence | `05 / 17` |
| Source branch | `snapshot/20260812-123244` |
| Source revision | `1ee51a4a395ebc5ce5d60d44a66bcd3c29775abe` |
| Source timestamp | `2026-08-12T12:41:28+08:00` |
| Publication mode | Sanitized historical source snapshot |

See [the snapshot note](SNAPSHOT_20260812_123244.md) for scope and validation boundaries.

## Roadmap

- Package retrieval, read, grep, and document-field scanning behind explicit tool contracts.
- Enforce tool policies in runtime code rather than prompts alone.
- Add citation-to-claim verification and evidence-quality gates.
- Evaluate retrieval and answer quality on frozen coating benchmarks.
- Extend the research workflow from retrieval foundation to a complete coating agent.

## License

MIT © 2026 Nathan10969. Historical authorship remains visible in the Git history; this public snapshot does not claim that every component was newly developed at publication time.
