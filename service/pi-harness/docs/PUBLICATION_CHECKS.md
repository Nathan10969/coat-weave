# Public Pi Migration Checks

Local verification on 2026-09-30, based on public repository commit `81665eb`.
These checks describe the local migration patch, not a pushed GitHub release or
a production deployment. The source snapshot remained unchanged.

## Delivered Scope

- Authorized service-layer migration: native Pi session HTTP/SSE, a limited
  completion-compatible endpoint, patent tool and FastAPI/Milvus/Markdown backend.
- Optional `edit`/`write`, restricted to workspace outputs. Read-only by default;
  shell is separately opt-in and is not a file-policy sandbox.
- Shared 11-field metadata logger, explicit configuration, private state,
  generic deployment example and upstream license attribution.
- Existing Python retrieval CLI retained independently. No hypergraph integration,
  trained weights, private corpus, production prompt or unused legacy agent CLI
  is included. Official Pi is a dependency, not a copied upstream monorepo.

## Verification

Environment: macOS, Node.js 25.8.0, Python 3.12.7. The added Linux CI targets Node
22 and Python 3.12; that new remote workflow has not been run for this patch.

| Check | Result |
|---|---|
| Original Python retrieval suite | 16/16 passed, unchanged |
| Pi SDK / HTTP / policy / logging / tool-client suite | 31/31 passed |
| Patent backend suite | 10/10 passed |
| TypeScript check and compiled build | Passed |
| Fresh npm installation with third-party scripts disabled | Passed; requires the documented security backport |
| Actual installed dependency versions after preparation | brace-expansion 5.0.12; undici 8.10.2 |
| npm audit after dependency preparation | 0 reported vulnerabilities at verification time |
| Compiled process smoke | Health, unauthorized rejection and offline greeting passed; process shut down |
| Backend editable installation without GPU dependencies | Passed; packaged schema accessible |
| Gitleaks 8.30.1, prospective public tree | No findings |
| Gitleaks 8.30.1, all available Git refs in public clone | No findings |
| Private deployment identity/path checks | No known production hosts, accounts, buckets, audit IDs or workstation paths found |

The backend test client emits an upstream httpx deprecation warning; no test was
skipped or hidden. GPU extras, real Milvus/TOS, local models and paid providers
were not installed or contacted for acceptance. Tests use synthetic text, fake
clients and deterministic SDK streams; they do not measure coating answer quality.

## Dependency Backport

The snapshot used Pi 0.85.1. The migration pins the adjacent 0.86.0 release to
obtain undici 8.10.2, and updates tests to its normalized system-message context.
Pi's published shrinkwrap still installs brace-expansion 5.0.9, ignoring the root
override. A lockfile-only audit can therefore disagree with the installed code.

`npm run prepare-deps` replaces only that nested dependency with the compatible
5.0.12 package already pinned at the root, retaining its license and verifying
the version actually resolved by minimatch. Check/build/test invoke this step.
A fresh `npm ci --ignore-scripts` must always be followed by preparation. Initial
installation may report the vulnerable shrinkwrapped package before this step.
Future SDK upgrades require review of the version checks and backport.

## Privacy and Remaining Boundaries

Secret scanning used an official Gitleaks release with its published SHA256
verified locally. Reports stay under ignored `.local/audit/`; no source or
credentials were uploaded to a scan service. Tree scanning uses only tracked
and nonignored prospective files, not local runtime state or dependency caches.
History scanning covers available public-clone refs, not unavailable private
repository history. No scan guarantees absence of all secrets or confidential data.

Production names, storage defaults and historical collection audit evidence were
removed, rather than generalized into unverified claims about new collections.
Credentials, account configurations, documents, traces, weights and backups are
excluded by both migration scope and ignore rules. Before publishing subsequent
changes, inspect the actual staged diff and rerun scans; force-add can defeat
ignore rules. Rotate any credentials known to have been exposed elsewhere.

Native SDK messages/events and persisted sessions may contain sensitive text and
reasoning. They are private API data, not customer-safe progress or public logs.
Metadata logs still reveal document identities, timestamps and activity patterns.
Shared-token authentication is not tenant isolation. OS isolation, retention,
customer-safe event filtering and real provider/datastore acceptance remain
deployment responsibilities. No production-security certification is claimed.
