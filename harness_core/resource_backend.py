"""Bounded lifecycle adapter for a verified installation's service group.

The gateway constructs adapters from its owned registry, never request parameters.
"""
import json
import os
import subprocess
import signal
import tempfile
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from .common import HarnessError
from . import sidecars
from .resource_runtime import ServiceRuntime


def run_owned_command(argv, env, timeout, *, input_data=None, max_output=65536, cancel_event=None):
    """Bound output memory and terminate our process group on interrupted startup."""
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as request:
        if input_data is not None:
            request.write(input_data)
            request.seek(0)
        process = subprocess.Popen(argv, env=env, stdin=request, stdout=output, stderr=errors,
                                   start_new_session=True)
        try:
            deadline=time.monotonic()+timeout
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    process.send_signal(signal.SIGTERM)
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:pass
                    raise HarnessError('Resource request cancelled')
                remaining=deadline-time.monotonic()
                if remaining<=0:raise subprocess.TimeoutExpired(argv,timeout)
                try:
                    process.wait(timeout=min(.1,remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            # Popen remains unreaped on timeout; its session ID cannot be reused.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        output.seek(0)
        data = output.read(max_output+1)
        if len(data) > max_output:
            raise HarnessError('Resource operation returned oversized metadata')
        return subprocess.CompletedProcess(argv, process.returncode, data, b'')


class OwnedBackend:
    def __init__(self, root, group, *, clock=time.monotonic, sleep=time.sleep):
        self.root = Path(root).resolve()
        if group not in sidecars.GROUPS:
            raise HarnessError('Unknown resource group')
        self.group = group
        self.clock = clock
        self.sleep = sleep
        self._opener = build_opener(ProxyHandler({}))

    def _run(self, action, timeout):
        if timeout <= 0:
            raise HarnessError('Resource startup deadline exceeded')
        env = {**os.environ, 'HARNESS_STATE_DIR': str(sidecars.owner_state(self.root)),
               'PYTHONDONTWRITEBYTECODE': '1'}
        try:
            result = run_owned_command(
                [sys.executable, str(self.root/'scripts/services.py'), action,
                 '--service', self.group], env=env, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise HarnessError('Resource operation unavailable or timed out') from None
        if result.returncode:
            # Child diagnostics may contain environment values; do not forward them.
            raise HarnessError('Resource operation refused; inspect ownership and service status')
        try:
            return json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise HarnessError('Invalid resource operation result') from None

    def endpoints(self):
        layout = sidecars.layout(self.root)
        if not layout or self.group not in layout['recipes']:
            raise HarnessError('Resource is not prepared')
        return {name: 'http://127.0.0.1:'+str(layout['ports'][name])
                for name in sidecars.GROUPS[self.group]}

    def start(self, timeout=60):
        deadline = self.clock() + timeout
        self._run('start', deadline-self.clock())
        endpoints = self.endpoints()
        while True:
            ready = True
            for name, url in endpoints.items():
                remaining = deadline-self.clock()
                if remaining <= 0:
                    raise HarnessError('Resource did not become ready within startup deadline')
                path = '/health/ready' if name == 'powercontext' else '/'
                try:
                    with self._opener.open(url+path, timeout=min(2, remaining)) as response:
                        if response.status != 200:
                            ready = False
                        if name == 'powercontext':
                            # Bounded readiness response; no project data is queried.
                            payload = response.read(4097)
                            if len(payload) > 4096 or json.loads(payload).get('status') != 'ready':
                                ready = False
                except (OSError, URLError, ValueError):
                    ready = False
            if ready:
                return
            remaining = deadline-self.clock()
            if remaining <= 0:
                raise HarnessError('Resource did not become ready within startup deadline')
            self.sleep(min(.2, remaining))

    def stop(self):
        self._run('stop', 30)

    def runtime(self):
        return ServiceRuntime(self.start, self.stop)


class RetainedBackend(OwnedBackend):
    def __init__(self, root, origin, **kwargs):
        super().__init__(root, 'powercontext', **kwargs)
        self.origin = str(Path(origin).resolve())

    def endpoints(self):
        from . import memory_services
        doc = memory_services.load(self.origin)
        if not doc or doc['status'] != 'PREPARED' or doc.get('owner_root') != str(self.root):
            raise HarnessError('Retained memory belongs to another owner')
        memory_services.verify_origin(doc, memory_services.source(self.origin))
        return {'powercontext':'http://127.0.0.1:'+str(memory_services.backend_port(doc))}

    def _run(self, action, timeout):
        self.endpoints()  # Provenance must pass before any worker runs.
        env = {**os.environ, 'HARNESS_STATE_DIR':str(sidecars.owner_state(self.root)),
               'PYTHONDONTWRITEBYTECODE':'1'}
        try:
            result = run_owned_command([sys.executable,str(self.root/'scripts/resource-retained.py'),
                                        action,'--origin',self.origin],env,timeout)
        except (OSError,subprocess.TimeoutExpired):
            raise HarnessError('Retained memory operation unavailable or timed out') from None
        if result.returncode:
            raise HarnessError('Retained memory operation refused; data preserved')
        try:
            return json.loads(result.stdout)
        except (ValueError,UnicodeError):
            raise HarnessError('Invalid retained memory worker response') from None
