#!/usr/bin/env python3
"""Independent HTTP receiver for an exact committed Handoff fixture."""
import json
import sys
from urllib.request import Request, urlopen

fixture = json.load(open(sys.argv[1]))
payload = {'scope_id': fixture['scope_id'], 'selection': 'exact', 'revision': fixture['revision']}
request = Request(fixture['url'] + '/v1/handoff/continue', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
with urlopen(request, timeout=20) as response:
    resolved = json.load(response)
assert fixture['marker'] in json.dumps(resolved), 'Exact Handoff marker missing from independent receiver'
print(json.dumps({'status': 'PASS', 'receiver_process': True, 'resolved': resolved}, ensure_ascii=False))
