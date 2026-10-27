# Third-Party Notices

CoatWeave uses the official Pi SDK packages `@earendil-works/pi-coding-agent`
and `@earendil-works/pi-ai`. Pi is authored by Mario Zechner and contributors.
The inspected source snapshot includes MIT copyright (c) 2025 Mario Zechner;
that notice is reproduced in [docs/licenses/pi-MIT.txt](docs/licenses/pi-MIT.txt).
The upstream monorepo is not copied into this repository. npm dependencies retain
their own licenses and notices. See the package lock for the pinned versions.
The local dependency preparation step replaces Pi's shrinkwrapped
brace-expansion 5.0.9 with compatible 5.0.12 from the locked root package;
the replacement retains that package's license and notices.

The HTTP adapter and patent tool service are migrated, sanitized and adapted
from an authorized service-layer snapshot, not claimed to be a from-scratch Pi
implementation. The project owner confirmed publication authorization for that
snapshot. The public prompt is a neutral replacement; private deployment
configuration, credentials, corpus and execution records are excluded.

Patent documents, model weights, Milvus/TOS deployments and third-party API
services are not bundled or licensed by this repository's MIT license. Their
usage rights and terms must be checked separately.
