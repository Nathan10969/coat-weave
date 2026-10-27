# Pi Service Relocation Verification

## Source and scope

- Source: `https://github.com/Nathan10969/coat_weave_harness`
- Source commit: `fe0950db7ef8704248cab7e4866b69fe72661a01`
- Destination baseline: `2660ffe79c06b136b017e8326da6f71a15e7d421`
- Destination: `service/pi-harness`
- Approved scope: source relocation, not KG integration or production deployment.

Only tracked files were imported. Runtime source, backend implementation, prompts,
tool schemas, tests, dependency versions and the package lock are unchanged.
Documentation links, the example systemd installation path, repository ignore
rules and CI working directories were adapted. The upstream MIT license,
third-party notices and Pi license notice are included locally.

`ci-example.yml` is an optional workflow template, not an enabled GitHub Action.
No new repository automation was activated. The verification below was local.

The Python KG API and this Pi service remain independent processes. Pi continues
to register `patent_search` against its configured Milvus/Markdown backend. No KG
adapter, automatic extraction, new retrieval algorithm or extra model call was
introduced. Existing Python API/core/KG/storage/config/systemd/scripts files have
no changes relative to the destination baseline.

## Local verification

Environment: macOS, Node.js 25.8.0, Python 3.12.7. Tests use synthetic fixtures and
deterministic SDK events, not paid model requests or former production systems.

| Check | Result |
| --- | --- |
| New layout tests before import | Failed for absent files and absent ignore rules |
| New layout tests after import | 3/3 passed |
| TypeScript type check | Passed |
| Compiled build | Passed |
| Pi SDK/transport/tool/privacy tests | 31/31 passed |
| Milvus backend offline tests | 10/10 passed |
| Compiled process smoke | Passed: health, authentication and offline greeting; process stopped |
| Imported TypeScript runtime directory vs. pinned source | Byte-identical |
| Existing Python service implementation | Unchanged |
| npm dependency audit after existing preparation | Failed: one high and one moderate vulnerability |

The backend suite emits an upstream Starlette/httpx deprecation warning; no test
was skipped. A backend invocation without `PYTHONPATH` initially failed to import
the package; rerunning the documented command below passed all ten tests.

## Reproduce

From `service/pi-harness`:

```bash
npm ci --ignore-scripts
npm run check
npm run build
npm test
npm run smoke
npm audit --audit-level=high
PYTHONPATH=backend/src python -B -m unittest discover -s backend/tests -v
```

The final audit command is expected to fail for this unchanged dependency
snapshot. From the repository root, run the three layout checks with:

```bash
python -B -m unittest discover -s service/tests -p test_pi_harness_layout.py -v
```

## Dependency limitation

The existing preparation script fixes the SDK-local brace-expansion version, but
the SDK also pins `undici` 8.10.2, now covered by additional advisories. The
[high-severity advisory](https://github.com/advisories/GHSA-xjj6-f33q-929c) and
[EventSource advisory](https://github.com/advisories/GHSA-4qv8-2p9j-2685) list
8.12.2 as a patched version. This relocation deliberately does not change the SDK,
dependency lock or runtime behavior. Review a separately tested dependency fix
before production use; do not interpret the offline tests as security clearance.

## Publication boundaries

Before the Harness repository was made public, Gitleaks scanned all five locally
available commits after refreshing GitHub refs, with no findings. Secret scanning
is not a guarantee that every confidential value is absent. No real documents,
database content, production configuration, private SDK state or credentials are
part of this import. The 370k-patent collection is not included.

Historical upstream results remain in `PUBLICATION_CHECKS.md`; in particular,
its earlier zero-vulnerability audit result does not describe today's audit.
