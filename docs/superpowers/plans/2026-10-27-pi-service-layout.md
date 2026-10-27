# Pi Service Layout Implementation Plan

**Goal:** Publish the existing authorized Harness repository and include its Pi service under `service/pi-harness` without changing retrieval behavior.

**Architecture:** Keep the Python KG service and the Pi/Milvus service independent. Copy only tracked files from Harness commit `fe0950db7ef8704248cab7e4866b69fe72661a01`. Preserve SDK versions, prompts, tool schemas, model settings and runtime logic. Production services and proprietary data are out of scope.

**Tech Stack:** Python/FastAPI, TypeScript, Node.js, official Pi SDK.

- [x] Check all available Harness Git history with redacted Gitleaks and make the repository public.
- [x] Add repository-layout checks and observe missing Pi service failures.
- [x] Import the tracked Pi service, license notices and historical verification notes; update documentation paths, deployment example and ignore patterns.
- [x] Provide the existing offline CI workflow as `service/pi-harness/docs/ci-example.yml` with relocated paths; do not enable new repository automation in this source-only move.
- [x] Run layout checks, TypeScript checking, build, SDK tests, backend tests and compiled-process smoke. Compare runtime source bytes against the pinned upstream commit. Record the newly reported dependency audit failure without changing the SDK or lockfile.
- [x] Scan the staged release, commit, push and verify the GitHub revision and public visibility.

Published source commit: `2285e3fea3a22b8d648bb28bcc600e963bed3964` (Jony).
GitHub `main` and `feat/20261027-pi-service-layout` were verified at that revision;
`backup/20261027-before-pi-service` preserves baseline `2660ffe`. The Harness
repository was independently verified public. No production service was restarted.

## Acceptance

The new directory runs from its own documented working directory. No existing Python service implementation changes. The Pi service still registers `patent_search` against its configured Milvus backend, not the KG tools. KG tool integration is a separate change requiring explicit approval.
