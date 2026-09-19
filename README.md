# Coating Mini Source Snapshot

This branch is a source-only snapshot of the current Coating project.

- `extracting/`: local coating KG extraction, direct vision reconstruction, validation, projection, ingestion helpers, and tests.
- `service/`: the service-side retrieval and query-classification source included in this historical snapshot.
- `embedding/`: the A100 hyperedge embedding and collection-scoped incremental ingestion job.

The snapshot deliberately excludes source PDFs, ZIP packages, extracted corpora, JSONL data drops, database contents, vector indexes, runtime state, logs, caches, virtual environments, backups, and all secret-bearing `.env` files.

Provider endpoints, credentials, and deployment paths are intentionally not part of this public snapshot. Only placeholder configuration is included.
