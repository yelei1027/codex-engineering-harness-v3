"""Lifecycle concurrency tests; public gateway integration is tested separately."""
import threading
import unittest
from harness_core.common import HarnessError
from harness_core.resource_runtime import ServiceRuntime


class ResourceRuntimeCase(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.calls = []
        self.runtime = ServiceRuntime(lambda timeout: self.calls.append(('start', timeout)),
                                      lambda: self.calls.append(('stop',)), clock=lambda: self.now)

    def test_idle_deadline_starts_after_last_call_and_exception_releases(self):
        with self.runtime.use():
            self.now = 200
            self.assertFalse(self.runtime.stop(idle_only=True))
            with self.assertRaises(HarnessError): self.runtime.stop()
            with self.assertRaises(ValueError):
                with self.runtime.use(): raise ValueError('client disconnected')
            self.assertEqual(self.runtime.status()['active_calls'], 1)
        self.now = 319
        self.assertFalse(self.runtime.stop(idle_only=True))
        self.now = 320
        self.assertTrue(self.runtime.stop(idle_only=True))
        self.assertEqual(self.calls, [('start', 60), ('stop',)])
        with self.runtime.use(): pass
        self.assertEqual(len(self.calls), 3)

    def test_manual_pin_survives_idle_then_explicit_stop(self):
        self.runtime.start_manual()
        self.now = 1000
        self.assertFalse(self.runtime.stop(idle_only=True))
        with self.runtime.use(): pass
        self.assertEqual(self.runtime.status()['mode'], 'MANUAL')
        self.assertTrue(self.runtime.stop())
        self.assertEqual(self.runtime.status()['state'], 'DORMANT')

    def test_two_clients_share_start_and_no_premature_stop(self):
        entered = threading.Event()
        release = threading.Event()
        errors = []
        def client():
            try:
                with self.runtime.use():
                    entered.set()
                    self.assertTrue(release.wait(3))
            except BaseException as error: errors.append(error)
        thread = threading.Thread(target=client)
        thread.start()
        self.assertTrue(entered.wait(3))
        try:
            with self.runtime.use():
                self.assertEqual(self.runtime.status()['active_calls'], 2)
            self.now = 1000
            self.assertFalse(self.runtime.stop(idle_only=True))
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(self.calls, [('start', 60)])

    def test_failed_start_is_visible_and_next_explicit_call_can_retry(self):
        attempts = []
        def start(timeout):
            attempts.append(timeout)
            if len(attempts) == 1: raise HarnessError('unavailable')
        runtime = ServiceRuntime(start, lambda: None)
        with self.assertRaises(HarnessError):
            with runtime.use(): pass
        self.assertEqual(runtime.status()['state'], 'START_FAILED')
        self.assertEqual(runtime.status()['active_calls'], 0)
        with runtime.use(): pass
        self.assertEqual(attempts, [60, 60])


    def test_quiesce_refuses_active_then_blocks_new_calls_until_resume(self):
        with self.runtime.use():
            with self.assertRaises(HarnessError):self.runtime.quiesce()
            self.assertTrue(self.runtime.status()['accepting_calls'])
        self.runtime.quiesce()
        with self.assertRaises(HarnessError):
            with self.runtime.use():pass
        with self.assertRaises(HarnessError):self.runtime.start_manual()
        self.runtime.resume()
        with self.runtime.use():pass
        self.assertEqual(self.calls,[('start',60)])


    def test_failed_start_cleanup_runs_once_and_preserves_failure_status(self):
        calls=[]
        def start(timeout):calls.append('partial start');raise OSError('not ready')
        runtime=ServiceRuntime(start,lambda:calls.append('stop'))
        with self.assertRaises(OSError):
            with runtime.use():pass
        self.assertTrue(runtime.stop(idle_only=True))
        self.assertFalse(runtime.stop(idle_only=True))
        self.assertEqual(calls,['partial start','stop'])
        self.assertEqual(runtime.status()['state'],'START_FAILED')
