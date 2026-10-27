# Pi KG Tools Implementation Plan

**Goal:** Let Pi call the existing read-only KG HTTP tools without changing retrieval, expansion, aggregation, source data or production services.

**Architecture:** Add one opt-in `kg` retrieval backend and keep the existing `milvus` backend as the default. Export tool JSON schemas and retrieval limits using the existing Python `kg_contract` module at build/test time. A small TypeScript adapter forwards tool arguments to the existing `/tools/kg.*` endpoints and returns their evidence and receipts without summarizing or truncating them.

**Scope:** Local implementation and offline verification only. No GitHub push, production deployment, database change, dependency upgrade, new router model or new semantic rules.

- [x] Read the existing Pi factory, KG contract and HTTP service.
- [x] Add failing transport/schema/configuration tests.
- [x] Export shared schemas; add the KG client and opt-in factory registration.
- [x] Test the actual SDK tool loop with a local synthetic HTTP service and fake model stream.
- [x] Run TypeScript checks/build/smoke, Pi/backend tests and existing Python KG regressions. Record failures and boundaries.
- [x] Inspect the final diff and secret scan; document local configuration and results.

Completed local verification: 35 Pi tests, 661 Python service tests and 10 Milvus
backend tests passed. Both compiled smoke modes passed. The original Python KG
implementation, dependency lock, prompts and thinking setting remain unchanged.
The staged diff passed Gitleaks. No push or production action was performed.

## Files

- `service/pi-harness/scripts/export-kg-tools.py`: reuse `openai_tool_parameters`, `retrieval_policy` and registry metadata; no independent vocabulary.
- `service/pi-harness/src/kg-tools.ts`: HTTP transport and five tool definitions, no retrieval implementation.
- `service/pi-harness/src/pi.ts`, `src/config.ts`, `src/main.ts`, `src/tool-policy.ts`: explicit backend selection and registered tools.
- `service/pi-harness/tests/kg-tools.test.ts`, `tests/kg-sdk.test.ts`: synthetic HTTP and actual SDK integration.
- Package lifecycle scripts, asset copying, ignore rules, setup docs and layout test: support the new adapter without altering the existing Python KG implementation.

## Acceptance

Use the shared 100/50 search budget and full expansion. Preserve raw question, filters, pagination, source-qualified IDs, exact/adjacent separation, unsupported receipts, partial expansion and composite statistics. Errors, timeout and cancellation must be explicit, not empty search results or silent Milvus fallback. Keep full evidence over 2 MB. The model chooses calls and object IDs; this adapter does not reproduce the old Python router's automatic selection/planning engine.
