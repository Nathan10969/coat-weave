<p align="center">
  <img src="docs/assets/hero.svg" alt="CoatWeave" width="100%" />
</p>

# CoatWeave

**Historical source snapshot 06** — a source-only milestone toward a tool-using coating research agent.

[![License: MIT](https://img.shields.io/badge/License-MIT-7c3aed.svg)](LICENSE)
[![Snapshot](https://img.shields.io/badge/snapshot-06-0f766e.svg)](SNAPSHOT_20260816.md)

CoatWeave is evolving toward a coating-domain agent in the spirit of modern coding agents: it should plan, call focused tools, retrieve dispersed evidence, and produce traceable answers. Snapshot 06 is not a complete agent or a production release. It preserves three code areas present at this point in the frozen project timeline:

- `extracting/`: patent knowledge-graph extraction, projection, validation, database helpers, prompts, and offline tests.
- `service/`: API orchestration, routing, dialogue-memory support, and read-only knowledge-graph retrieval tools.
- `embedding/`: a source-only job for hyperedge indexing and retrieval evaluation; its corpora, indexes, model service, and outputs are deliberately excluded.

The retrieval path is broader than ordinary chunk matching. The service contains hybrid search, graph expansion, SQL aggregation, and document-field scanning hooks for evidence dispersed across documents. Tool invocation alone does not prove that cited evidence supports an answer; automated claim verification and end-to-end scientific validation remain roadmap items.

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

The service and embedding job have separate dependency and infrastructure requirements. Copy the public-safe configuration template before local setup:

```bash
cd service
python -m venv .venv
python -m pip install -r requirements.txt
cp config/.env.example .env
```

API keys and tokens are intentionally empty. Database, model, parsing-service, corpus, and index-dependent flows require infrastructure not included here. Local imports or offline tests do not establish deployment readiness, retrieval quality, or scientific correctness.

## Snapshot record

| Field | Value |
|---|---|
| Sequence | `06 / 17` |
| Source branch | `snapshot/20260816` |
| Source revision | `cafda373d6451fd492f37916eea932654e5e3d5a` |
| Source timestamp | `2026-08-16T19:55:56+08:00` |
| Publication cadence | Manual early-release override authorized by the repository owner |
| Publication mode | Sanitized historical source snapshot |

See [the snapshot note](SNAPSHOT_20260816.md) and [public handoff](HANDOFF.md) for scope and validation boundaries.

## Roadmap

- Package retrieval, read, grep, and document-field scanning behind explicit tool contracts.
- Enforce tool policies in runtime code rather than prompts alone.
- Add citation-to-claim verification and evidence-quality gates.
- Evaluate retrieval and answer quality on frozen coating benchmarks.
- Extend the retrieval foundation into a complete coating research agent.

## License

MIT © 2026 Nathan10969. Historical authorship remains visible in Git history; publication does not claim that every component was newly developed at release time.
