# Pi Harness Service

Imported from `Nathan10969/coat_weave_harness` commit
`fe0950db7ef8704248cab7e4866b69fe72661a01` into `service/pi-harness`.
The initial relocation preserved runtime source and retrieval behavior; the
subsequent local KG adapter is opt-in and documented below. SDK pins and the
existing Milvus backend remain unchanged. Start commands below run from `service/pi-harness`, not the repository
root. This is a separate process from the Python KG API in the parent directory.

A publicly sanitized migration of the service layer: official Pi SDK sessions,
an HTTP/SSE adapter, an OpenAI-compatible completion subset, an explicit
`patent_search` tool, and a read-only FastAPI/Milvus/Markdown backend.
The upstream Pi monorepo is not vendored. The service consumes a pinned npm SDK.

```text
Client -> Pi HTTP adapter :8003 -> configured tool-calling model
                              -> milvus: patent_search -> FastAPI :8002
                                                   -> Milvus
                                                   -> optional embedding/reranker
                                                   -> local Markdown or TOS
                              -> kg: existing /tools/kg.* HTTP service
                                     -> original KG retrieval/expansion/aggregation
```

The existing [Python KG service](../README.md) is independent of this Pi/Milvus
service. By default Pi registers the existing `patent_search` tool against its own
configured backend. Setting `PI_RETRIEVAL_BACKEND=kg` instead registers the five
existing KG tools through the thin HTTP adapter described below. The separate
[`patent_coating/` Python CLI](https://github.com/Nathan10969/coat_weave_harness)
also remains an independent implementation. Training weights, employer datasets
and model quality measurements are not included or claimed.

## Local Setup

Dependency status on 2026-10-27: this unchanged upstream lockfile still resolves
SDK-local `undici` 8.10.2. The current npm audit reports one high and one moderate
vulnerability after the existing brace-expansion preparation. The high-severity
[undici advisory](https://github.com/advisories/GHSA-xjj6-f33q-929c) lists 8.12.2
as a patched version. This source relocation does not upgrade dependencies or
certify the snapshot for production use. Review and test that dependency update
separately before deploying. Do not use `npm audit fix --force`: its proposed
SDK replacement changes the supported API.

Node.js 22.19+; Python 3.10+ for shared KG schema generation and the backend.
Start from this directory:

```bash
npm ci --ignore-scripts
npm run prepare-deps
cp .env.example .env
npm run check
npm test
npm run build
npm start
```

`--ignore-scripts` prevents dependency install hooks from running. The tested
HTTP/tool paths do not need the optional terminal UI's native clipboard setup.
The SDK's published shrinkwrap pins a vulnerable brace-expansion despite root
overrides. `prepare-deps` backports the locked compatible 5.0.12 patch and checks
the dependency actually resolved by minimatch; check/build/test run it as well.
Do not treat a lockfile-only audit as verification of the installed package.
Without a model configuration, `/health` and the initial greeting work; that is
not evidence of model or database connectivity. Configure `PI_LLM_BASE_URL`,
`PI_LLM_MODEL` and `PI_LLM_API_KEY` locally for real requests. Remote model URLs
must use HTTPS. Context/output limits are user-supplied settings, not measured
provider capabilities. This adapter configures text input and thinking off.

```bash
curl http://127.0.0.1:8003/health
curl http://127.0.0.1:8003/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"coat-weave-pi","stream":false,"messages":[{"role":"user","content":"Hello"}]}'
```

The initial whole-message greeting is a fixed response that makes no model call.
Other questions send their history and authorized tool results to your model
provider and can incur charges. The public model alias does not select or switch
the server's configured provider model.

## Backend

### Existing KG tools (opt-in)

Configure the already available KG HTTP service; this adapter does not start,
deploy, rebuild or write to it. For local verification use a local test service
and an isolated Pi state directory, not a former production endpoint.

```dotenv
PI_RETRIEVAL_BACKEND=kg
PI_KG_TOOLS_URL=http://127.0.0.1:18021
PI_KG_API_TOKEN=
PI_KG_TIMEOUT_MS=120000
```

Set `PI_KG_API_TOKEN` to the token configured on that KG service when required.
`PI_KG_TOOLS_URL` is the service base URL, not the Python API's tool-proxy URL.
Remote services require HTTPS. KG failures never switch silently to Milvus.

| Pi tool | Existing endpoint |
|---|---|
| `kg_hybrid_search` | `/tools/kg.hybrid_search` |
| `kg_expand_hyperedge_multihop` | `/tools/kg.expand_hyperedge_multihop` |
| `kg_sql_aggregate` | `/tools/kg.sql_aggregate` |
| `kg_lookup_vocabulary` | `/tools/kg.lookup_vocabulary` |
| `kg_doc_field_scan` | `/tools/kg.doc_field_scan` |

Schema generation calls the parent service's `kg_contract.openai_tool_parameters`
and reads its classification registry. It runs locally during check/build/test,
not during each model call. Generated schemas are ignored source assets and are
copied into the compiled build. No second material/category vocabulary is added.

Search uses the shared 100-candidate/50-result policy; quantity parameters remain
hidden from the model. Expansion requests use `evidence_mode=full` and accept up
to the shared 30-object limit per call. No evidence byte cap, summarizer or hidden
retry is added. Query, filters, pagination, source IDs, full evidence, partial
results, unsupported receipts and exact/adjacent envelopes are forwarded intact.
Existing normalization and retrieval execute on the original KG server.

Pi chooses calls and object IDs. This adapter does not duplicate the Python
router, its session scope management, automatic expansion selection or answer
planning. A tool connection is not proof that a real model will choose the right
filters or return a scientifically correct answer. Native events and the existing
11-field metadata logs retain the KG tool names and call IDs.

### Independent Milvus backend (default)

```bash
python -m venv backend/.venv
backend/.venv/bin/python -m pip install -e './backend[test]'
backend/.venv/bin/python -m uvicorn patent_milvus_tool.server:app \
  --host 127.0.0.1 --port 8002 --env-file .env
```

Install `python-dotenv` to use uvicorn's `--env-file`. Optional extras are `tos`
(`tos==2.9.2`) and `models` (local Sentence Transformers). GPU/model compatibility
and a real Milvus deployment require separate acceptance. Supply your own
`MILVUS_COLLECTION`, field mappings and indexes. No collection is created or
changed. BM25/hybrid requires a compatible sparse field, BM25 function and index.
Dense search needs a compatible embedding model or embedding HTTP service.
`EMBEDDING_URL` uses the migrated custom `{"inputs": "...", "dimensions": ...}` protocol, not a
generic OpenAI embeddings contract; inspect `_embed` before connecting a provider.

Set `PATENT_MD_STORAGE=local` with an authorized `PATENT_MD_ROOT`, or explicitly
configure TOS with your own bucket and credentials. Local `read_document` uses
the `{doc_id}.md` template; query references can additionally locate stored paths.
TOS retains the migrated WO/year/5000-number-range locator, which must match your
own object layout. Neither mode publishes or includes documents. Read pages use
line/character cursors and content versions; continue with `next_statement` until
`has_more=false`. A snippet or derived object URI is not a full-text inspection.

Backend health/schema do not load models or open Milvus. Health is liveness only.
Always set `PATENT_SEARCH_API_TOKEN` before network exposure; the included example
binds loopback. Database/TOS credentials are separate from the service token.

## HTTP Contracts

| Endpoint | Behavior |
|---|---|
| `GET /health` | Adapter liveness; requires Bearer auth if configured |
| `POST /sessions` with `{}` | Create a server-configured native SDK session |
| `GET /sessions/{id}/events` | Native SDK SSE, replay with `Last-Event-ID` |
| `GET /sessions/{id}/messages` | Native SDK messages, including tool results |
| `POST /sessions/{id}/prompt` | `{"text":"..."}`; await completion, observe SSE separately |
| `POST /sessions/{id}/abort` with `{}` | Abort the active SDK run |
| `DELETE /sessions/{id}` | Release active session; does not erase persisted files |
| `POST /chat/completions` or `/v1/chat/completions` | Stateless supplied history, streaming/nonstreaming |

Completion compatibility is intentionally limited: text messages, a configured
alias, optional temperature and optional usage. Client tools and arbitrary model
selection are rejected. Completion SSE emits text chunks and heartbeats, **not
native tool-progress frames**. Use the native session event stream for progress.
Raw SDK events can include reasoning, tool arguments and document text: do not
render hidden reasoning or expose the raw stream to customers without filtering.
Map tool starts/ends to observable status labels instead.

## Tools and State

| `PI_TOOL_MODE` | Registered tools | Boundary |
|---|---|---|
| `read-only` (default) | `read` plus selected retrieval tools | Workspace files and configured backend |
| `workspace-write` | Above plus `edit`, `write` | Writes restricted to workspace `outputs/` |
| `shell` | Above plus `bash` | Privileged; shell is NOT confined by path guards |

Selected retrieval tools are `patent_search` in Milvus mode or the five `kg_*`
tools in KG mode, never an automatic mixture of both backends.

Privileged modes and non-loopback binding require a 24-256 character API token.
File tools reject hidden paths, credential files, traversal and symlink escapes.
These checks are not an OS sandbox and cannot prevent concurrent filesystem
races. Use a dedicated account/container and no unrelated secrets. Shell can
read its process environment, including provider keys, and must not be enabled
for untrusted users. Arbitrary project extensions, skills and account prompts are
not auto-loaded. The neutral prompt treats documents as data, not instructions.

Workspace, agent state and metadata log are isolated under ignored `.local/` by
default. Native sessions persist SDK messages under agent state; completion
contexts use in-memory sessions. DELETE removes the active session, not the
on-disk history. Set an explicit retention/deletion policy. A shared Bearer token
is not multi-tenant user authorization.

Both API paths write the same 11-field JSONL metadata:

```text
session_id, event_type, tool_name, tool_call_id, op, doc_id, start_line,
has_more, next_start_line, tool_result_success, timestamp
```

Missing values are `null`. Start/end metadata is paired by call ID, and nested
`args.statement` is supported. Logs omit prompts, response text, thinking,
filters, raw arguments/results, URLs and credentials; file permissions are 0600.
Document IDs, timestamps and activity patterns can still be sensitive. Native
SDK persistence and the existing Python CLI's traces have richer content and
must stay private. Rotate logs externally; retention is not automatic.

## Offline Verification

```bash
npm run check
npm run build
npm test
node scripts/smoke.mjs kg
PYTHONPATH=backend/src backend/.venv/bin/python -B -m unittest discover -s backend/tests -v
```

Tests use synthetic documents, fake Milvus/TOS and deterministic SDK streams;
they do not call paid models or former production infrastructure. They validate
transport, policy and pagination, not scientific answer quality. See
[historical upstream publication verification](docs/PUBLICATION_CHECKS.md),
[relocation verification](docs/RELOCATION_20261027.md) and
[upstream attribution](THIRD_PARTY_NOTICES.md).
Local KG adapter checks and their boundaries are recorded in
[KG_ADAPTER_20261027.md](docs/KG_ADAPTER_20261027.md).
