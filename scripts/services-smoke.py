#!/usr/bin/env python3
"""Exercise actual local Basic Handoff storage and an independent receiver."""
import json
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:18080'
def request(path, payload=None, base=BASE):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = Request(base + path, data=data, headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=30) as response:
        return json.load(response)

ready = request('/health/ready')
assert ready['status'] == 'ready'
capabilities = request('/v1/capabilities')
marker = 'harness-handoff-' + uuid.uuid4().hex
scope = request('/v1/scopes', {'title': 'Harness service validation', 'summary': 'Isolated installation smoke fixtures', 'idempotency_key': marker})
sid = scope['scope_id']
claim = lambda text: {'text': text, 'basis': 'declared', 'evidence': []}
prepared = request('/v1/work/handoffs/prepare-current', {
    'scope_id': sid, 'source_id': marker,
    'handoff': {
        'schema': 'powercontext.current-work-handoff.v1', 'trust': 'untrusted_input',
        'objective': 'Validate Basic explicit Handoff persistence: ' + marker,
        'state': [claim('This is an installation test fixture; service responds ready.')],
        'disposition': 'continuable',
        'next_action': claim('Independent receiver must resolve the exact committed Handoff and verify marker.'),
        'omissions': ['No generation or embedding provider configured.']}})
committed = request('/v1/handoff/commit', {'scope_id': sid, 'handoff': prepared['handoff']})
fixture = {'scope_id': sid, 'revision': committed['reference'], 'marker': marker, 'url': BASE}
fixture_path = ROOT/'runtimes/powercontext/recall-fixture.json'
fixture_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + '\n')
receiver = subprocess.run([sys.executable, str(ROOT/'scripts/services-recall.py'), str(fixture_path)], text=True, capture_output=True, check=True)
recall = json.loads(receiver.stdout)
result = {'status': 'PASS', 'scope_id': sid, 'ready': ready, 'capabilities': capabilities, 'handoff': {'prepared': prepared, 'committed': committed, 'independent_recall': recall}, 'full_automatic_memory': 'NOT_TESTED_PROVIDER_UNCONFIGURED'}
(ROOT/'reports/powercontext-smoke.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'status': 'PASS', 'scope_id': sid, 'revision': committed['reference'], 'independent_receiver': recall['status']}))
