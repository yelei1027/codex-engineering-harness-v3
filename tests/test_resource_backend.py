import json
import os
import sys
import tempfile
import time
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from harness_core.common import HarnessError
from harness_core.resource_backend import OwnedBackend, run_owned_command


class ResourceBackendCase(unittest.TestCase):
    def test_child_uses_exact_owned_group_and_bounded_timeout(self):
        backend = OwnedBackend('/tmp/owned-resource-fixture', 'powercontext')
        result = subprocess.CompletedProcess([], 0, b'{"status":"SERVICES_START"}', b'')
        with patch('harness_core.resource_backend.sidecars.owner_state', return_value=Path('/tmp/owned-state')), patch('harness_core.resource_backend.run_owned_command', return_value=result) as run:
            backend._run('start', 60)
        argv = run.call_args.args[0]
        self.assertEqual(argv[-3:], ['start', '--service', 'powercontext'])
        self.assertEqual(run.call_args.kwargs['timeout'], 60)
        self.assertEqual(run.call_args.kwargs['env']['HARNESS_STATE_DIR'], '/tmp/owned-state')

    def test_failure_does_not_expose_child_diagnostics(self):
        backend = OwnedBackend('/tmp/owned-resource-fixture', 'powercontext')
        with patch('harness_core.resource_backend.sidecars.owner_state', return_value=Path('/tmp/owned-state')), patch('harness_core.resource_backend.run_owned_command', return_value=subprocess.CompletedProcess([], 1, b'', b'provider-secret')):
            with self.assertRaises(HarnessError) as caught:
                backend._run('start', 60)
        self.assertNotIn('provider-secret', str(caught.exception))

    def test_readiness_has_one_shared_deadline(self):
        now = [0]
        backend = OwnedBackend('/tmp/owned-resource-fixture', 'powercontext',
                               clock=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0]+delay))
        backend._run = Mock(side_effect=lambda *args: now.__setitem__(0, 59))
        backend.endpoints = Mock(return_value={'powercontext': 'http://127.0.0.1:12345'})
        backend._opener = Mock()
        backend._opener.open.side_effect = OSError('not ready')
        with self.assertRaises(HarnessError): backend.start(60)
        self.assertLessEqual(now[0], 60)
        self.assertTrue(all(c.kwargs['timeout'] <= 1 for c in backend._opener.open.call_args_list))

    def test_no_arbitrary_service_names(self):
        with self.assertRaises(HarnessError): OwnedBackend('/tmp/fixture', 'unregistered')


class OwnedCommandCase(unittest.TestCase):
    def test_timeout_stops_spawned_child(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory)/'child.pid'
            code = "import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
            with self.assertRaises(subprocess.TimeoutExpired):
                run_owned_command([sys.executable, '-c', code, str(pidfile)], os.environ.copy(), 1)
            self.assertTrue(pidfile.exists())
            pid = int(pidfile.read_text())
            for _ in range(50):
                path = Path('/proc')/str(pid)/'stat'
                try:
                    if path.read_text().split(') ', 1)[1].split()[0] == 'Z':
                        break
                except (FileNotFoundError, ProcessLookupError):
                    break
                time.sleep(.02)
            else:
                self.fail('Owned descendant still running after startup timeout')

    def test_output_budget_does_not_return_large_response(self):
        with self.assertRaises(HarnessError):
            run_owned_command([sys.executable, '-c', "print('x'*70000)"], os.environ.copy(), 3)
