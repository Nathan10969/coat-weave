# coating_mini 26_6_2 Snapshot

This branch contains a clean code snapshot prepared on 2026-06-02 from the
private development workspace. Machine-local source paths have been replaced
with repository-relative references for public release.

## Layout

- `extracting/` contains the KG extraction and reconstruction code path:
  pipeline package code, selected batch scripts, Vision KG helpers, prompts,
  database schema/seed SQL, dependency files, and contract documentation.
- `service/` contains the current online Q&A service code aligned with the A100
  runtime path: FastAPI API layer, orchestration, core routing/answering,
  KG tools, storage schema/repository, systemd units, smoke scripts, and
  stream diagnostics.
- `MANIFEST.json` records copied files, source paths, SHA256 hashes, byte sizes,
  exclusion rules, and the key service file hashes.

## Exclusions

The snapshot intentionally excludes data and machine-local artifacts:

- `.env` and real secrets
- virtual environments
- runtime conversation data
- `__pycache__` / `*.pyc`
- pipeline `data/`, `output/`, `runs/`, and `audit/`
- logs, zip archives, and temporary scripts
- tests, for now

## Verification

Before this branch was prepared:

- The local service hot-path files matched the A100 runtime hashes for
  `api/app.py`, `api/orchestrator.py`, `api/main.py`, `core/answering.py`,
  and `core/routing.py`.
- The private source tree was reported to have passed `compileall` when the
  snapshot was prepared; the public release records its own checks separately.
- Forbidden-path scanning found no `.env`, virtualenv, runtime data, caches,
  `runs`, `audit`, logs, or zip files in the snapshot.
- Source-tree smoke tests passed:
  - `memory.dialogue_memory_demo.tests.test_kg_expand_routing`
  - `memory.dialogue_memory_demo.tests.test_coating_api_service_contract`
  - `memory.dialogue_memory_demo.tests.test_kg_sql_aggregate_service`

## Notes

This branch is a code snapshot only. It does not deploy to A100 and does not
include KG data. To run against production-like data, configure the service
environment separately and point the KG tools to the desired KG artifact set.
