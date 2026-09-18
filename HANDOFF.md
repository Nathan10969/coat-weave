# Snapshot 07 public handoff

This source-only handoff describes the public tree without exposing workstation paths, private hosts, internal repositories, credentials, corpora, indexes, or runtime state.

## Components

- `extracting/` contains the installable `coating_kg` package, prompts, SQL resources, scripts, documentation, and tests.
- `service/` contains API orchestration, routing, dialogue memory, storage adapters, read-only KG tools, and deployment templates.
- `embedding/` contains a standalone hyperedge indexing and evaluation job. It expects an external embedding endpoint, PostgreSQL with pgvector, and locally supplied evaluation inputs.

## Data and infrastructure contract

The repository intentionally excludes patent PDFs, parsed full text, proprietary corpora, database contents, vector indexes, generated evaluations, model weights, logs, caches, and secrets. Configuration examples contain only local placeholders. Operators must provide their own authorized data and infrastructure.

## Verification boundary

Publication checks cover static syntax, credential scanning, selected offline tests, documentation links, and SVG parsing. They do not exercise paid APIs, parsing services, PostgreSQL, Redis, vector search, GPU inference, production deployment, semantic evidence validation, or retrieval-quality acceptance.

## Historical interpretation

This is sequence 07 in a frozen 17-snapshot timeline. Differences from sequence 06 record the source tree at the source revision; they should not be interpreted as a promise that every capability improved monotonically.
