# CoatWeave

## Historical source snapshot 10

This repository is a public, source-only archive of the tenth frozen CoatWeave
snapshot. It is not a claim that this snapshot is a complete deployed agent or
a scientific/production acceptance result.

- Source branch: `fix/20260907-retrieval-full-evidence`
- Source tip: `83b790ed3d6204ea37a5aef5b8bb2be66a04bb07`
- Snapshot time: `2026-09-08 01:09:01 -07:00`
- Publication: `snapshot-10` on `main` (manual early-cadence override)

The tree contains extraction (`extracting/`), service (`service/`), embedding
(`embedding/`), retrieval/evidence implementation, prompts, schemas, seed SQL,
tests, and operational documentation. The public archive excludes source PDFs,
corpora, database dumps, vector indexes, runtime logs, private incident
material, production backups, and credentials. Example credential fields are
empty and must be configured locally.

## Scope and validation

The code and tests are preserved as historical material. Offline syntax, unit
tests, and CLI checks are release-time checks only; they do not establish model
quality, live database compatibility, production deployment, or end-to-end
scientific validity. No paid model/API, GPU, or production service was used for
this publication.

## License

MIT © 2026 Nathan10969.
