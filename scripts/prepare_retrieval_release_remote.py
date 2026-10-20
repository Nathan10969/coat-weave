"""Pin code parity and completed sidecar gates; never mutate production."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile

parser = argparse.ArgumentParser()
parser.add_argument('--candidate', type=Path, required=True)
parser.add_argument('--candidate-sha', required=True)
parser.add_argument('--release-script', type=Path, required=True)
parser.add_argument('--sidecar', type=Path, required=True)
parser.add_argument('--display-gate', type=Path, required=True)
parser.add_argument('--pagination-gate', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.release_script.parent))
import importlib.util
spec = importlib.util.spec_from_file_location('release', args.release_script)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
os.umask(0o077)
payload = release.read_candidate(args.candidate, args.candidate_sha)
files = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
live = release.snapshot(args.sidecar, runtime_only=True)
if files != live:
    print(json.dumps({'parity_differences': [
        {'file': name, 'candidate_present': name in files, 'sidecar_present': name in live,
         'lf_equal': (payload[name].replace(b'\r\n', b'\n') == (args.sidecar / name).read_bytes().replace(b'\r\n', b'\n'))
          if name in files and name in live else False}
        for name in sorted(set(files) | set(live)) if files.get(name) != live.get(name)]}))
assert set(files) == set(live), 'runtime inventory differs'
for name, data in payload.items():
    assert data.replace(b'\r\n', b'\n') == (args.sidecar / name).read_bytes().replace(b'\r\n', b'\n'), name
# Retain tested Linux bytes when the local Git checkout differs only by CRLF.
payload = {name: (args.sidecar / name).read_bytes() for name in files}
files = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
release.verify_parity(files, live, 'complete tested sidecar inventory')
display = json.loads(args.display_gate.read_bytes())
assert len(display) == 3 and all(row['display_gate'] for row in display)
pages = json.loads(args.pagination_gate.read_bytes())
assert len(pages) == 2
searches = [row['searches'][0] for row in pages]
assert all(row['mode'] == 'candidates' and len(set(row['ids'])) == 50 for row in searches)
assert not set(searches[0]['ids']) & set(searches[1]['ids'])
assert [row['pagination']['offset'] for row in searches] == [0, 50]
assert all(row['budget']['effective'] == {'candidate_k': 100, 'top_k': 50, 'expand_top_k': 0}
           for row in searches)
assert all(row['filters']['assignees'] == ['Jotun'] for row in searches)
# Use the exact deployed deterministic publication normalization, not a suffix guess.
sys.path.insert(0, str(args.sidecar / 'core'))
from demo_text import doc_id_lookup_key
scopes = [{doc_id_lookup_key(value) for value in row['filters']['doc_ids']} for row in searches]
assert scopes[0] == scopes[1]
gate = json.loads((args.sidecar / 'gates/tool_gate.json').read_bytes())
assert gate['budget_gate'] and gate['returned'] == 50 and gate['selected'] == 30
assert gate['budget']['effective'] == {'candidate_k': 100, 'top_k': 50, 'expand_top_k': 30}
args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
with tarfile.open(args.output / 'tested_runtime.tar', 'w') as tar:
    for name, data in sorted(payload.items()):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(data))
(args.output / 'candidate_manifest.json').write_text(json.dumps(files, indent=2))
attestation = {'status': 'passed', 'tested_at_utc': datetime.now(timezone.utc).isoformat(),
               'files': files, 'gates': {}, 'production_modified': False,
               'local_candidate_sha256': args.candidate_sha, 'local_parity': 'identical_after_crlf_normalization'}
for path in (args.display_gate, args.pagination_gate, args.sidecar / 'gates/tool_gate.json'):
    attestation['gates'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
(args.output / 'tested_sidecar.json').write_text(json.dumps(attestation, indent=2))
print(json.dumps({'output': str(args.output), 'files': len(files),
                  'tested_sha': hashlib.sha256((args.output / 'tested_sidecar.json').read_bytes()).hexdigest(),
                  'candidate_sha': hashlib.sha256((args.output / 'tested_runtime.tar').read_bytes()).hexdigest(),
                  'production_modified': False}))
