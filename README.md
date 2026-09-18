<p align="center"><img src="docs/assets/hero.svg" alt="CoatWeave" width="100%" /></p>

# CoatWeave

**Historical source snapshot 07** — a source-only milestone toward a tool-using coating research agent.

[![License: MIT](https://img.shields.io/badge/License-MIT-7c3aed.svg)](LICENSE)
[![Snapshot](https://img.shields.io/badge/snapshot-07-0f766e.svg)](SNAPSHOT_20260817.md)

CoatWeave is evolving toward a coating-domain agent that can plan, call focused tools, retrieve dispersed evidence, and produce traceable answers. Snapshot 07 is not a complete agent or a production release.

- `extracting/`: patent knowledge-graph extraction, projection, validation, database helpers, prompts, and offline tests.
- `service/`: API orchestration, routing, dialogue-memory support, and read-only knowledge-graph retrieval tools.
- `embedding/`: source-only hyperedge indexing and retrieval-evaluation jobs; corpora, indexes, model services, and outputs are excluded.

The retrieval path goes beyond ordinary chunk matching through hybrid search, graph expansion, SQL aggregation, and document-field scanning hooks. Tool invocation alone does not prove evidential support; automated claim verification and end-to-end scientific validation remain roadmap items.

<p align="center"><img src="docs/assets/architecture.svg" alt="CoatWeave snapshot architecture" width="92%" /></p>

## Quick start

```bash
cd extracting
python -m venv .venv
python -m pip install -e ".[dev]"
python -m coating_kg --help
pytest -q
```

The service and embedding jobs have separate dependency and infrastructure requirements. API keys and tokens in the configuration examples are intentionally empty. Database, parser, model, corpus, index, and production services are not included.

## Snapshot record

| Field | Value |
|---|---|
| Sequence | `07 / 17` |
| Source branch | `snapshot/20260817` |
| Source revision | `440609232c51ea3c0cea21ca46cbca9d2b76f3a5` |
| Source timestamp | `2026-08-17T16:54:31+08:00` |
| Publication cadence | Manual early-release override authorized by the repository owner |
| Publication mode | Sanitized historical source snapshot |

See [the snapshot note](SNAPSHOT_20260817.md) and [public handoff](HANDOFF.md) for scope and validation boundaries.

## Roadmap

- Expose retrieval, read, grep, and document-field scanning through explicit tool contracts.
- Enforce tool policies in runtime code rather than prompts alone.
- Add citation-to-claim verification and evidence-quality gates.
- Evaluate retrieval and answers on frozen coating benchmarks.
- Extend the retrieval foundation into a complete coating research agent.

## License

MIT © 2026 Nathan10969. Historical authorship remains visible in Git history; publication does not claim every component as newly developed at release time.
