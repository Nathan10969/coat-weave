# Local Pi KG Adapter Verification

## Scope

Base revision: `18cbfef438fb38af4bcd99ed2e6457aaba26b2ca`.
Local branch: `feat/20261027-pi-kg-tools`.

The change adds a thin HTTP adapter, not another retrieval system. KG mode calls
the existing five `/tools/kg.*` endpoints. Schema export invokes the existing
`kg_contract.openai_tool_parameters` and classification registry at build time.
The original Python KG service, retrieval logic, normalization, source data,
embedding, database and production configuration are unchanged.

Milvus remains the default. Opt in with `PI_RETRIEVAL_BACKEND=kg`, configure a
local `PI_KG_TOOLS_URL`, and provide `PI_KG_API_TOKEN` if the KG server requires
one. KG errors do not fall back to Milvus. No new intent model, keyword routing,
summarization, JSON answer gate or paid model call was introduced.

The adapter injects the shared 100-candidate/50-result budget and requests full
expansion, with at most 30 objects per expansion call. It does not implement the
old Python router's automatic object selection, session scope or answer planning.
Pi makes the tool calls; the original KG server evaluates retrieval constraints.

## Verification

Local environment: macOS, Node.js 25.8.0 and Python 3.12.7. Tests use local
ephemeral ports, synthetic documents and fake model streams. No former production
endpoint, production data store or paid model provider was contacted.

| Check | Result |
| --- | --- |
| New adapter test before implementation | Failed because the adapter module was absent |
| Pi/SDK/transport/policy tests after implementation | 35 passed; no skips |
| Full Python service suite, including the new contract and real-handler checks | 661 passed |
| Independent Milvus backend offline suite | 10 passed |
| TypeScript checking | Passed |
| Compiled build | Passed |
| Compiled process smoke in KG mode | Passed; process stopped |
| Compiled process smoke in legacy Milvus mode | Passed; process stopped |

The actual SDK loop registers five KG tools instead of `patent_search`, calls
search, full expansion and aggregate endpoints, and receives more than 2 MB of
sample-bound evidence in its model context without truncation. Request filters,
source-qualified IDs, units and all requested group dimensions are retained.
The existing 11-field logger records KG tool names and call IDs, not the question
or evidence text.

A separate integration test runs the original KG HTTP handler and real
`run_expand` implementation against an in-memory KG fixture. The TypeScript client
receives the raw hyperedge, all 25 facts, all 7 evidence records and unchanged long
quotes. Database access alone is replaced with the existing synthetic fixture;
the expansion implementation is not mocked or copied.

Transport tests cover all five endpoints, authentication, service-managed search
budgets, full expansion, partial failures, unsupported receipts, separate adjacent
results, pagination, composite group dimensions, timeout, cancellation, malformed
JSON and HTTP failure. No hidden retry or cross-backend fallback occurs.

Initial Python test attempts failed because the existing environments lacked
pytest or psycopg. A separate ignored `.local/kg-test-venv` was prepared with
system packages plus psycopg/Redis; the completed suite above then passed. No
global Python environment or service requirements file was changed.

## Reproduce

From `service/pi-harness`, after installing the documented dependencies:

```bash
npm run check
npm run build
npm test
node scripts/smoke.mjs kg
node scripts/smoke.mjs milvus
PYTHONPATH=backend/src python -B -m unittest discover -s backend/tests -v
```

From the repository root with the Python service test dependencies installed:

```bash
python -m pytest service/tests -q
```

Run the Node preparation/build before the full Python suite: the cross-language
integration test needs Node/tsx and the generated shared-schema asset.

## Not claimed

- No real-model routing or coating answer-quality acceptance was performed.
- No real corpus retrieval-recall or 370k-patent coverage claim was tested.
- Existing SDK/dependency versions, prompts and thinking settings were not changed.
- The previously documented undici dependency audit issue remains unresolved;
  offline functional tests are not production security approval.
- No production service, systemd unit or reverse proxy was changed or restarted.
- This change was tested locally, not published or deployed.
