import tempfile
import unittest
from pathlib import Path
from harness_core.common import HarnessError
from harness_core.resource_preferences import ResourcePreferences
from harness_core.resource_runtime import ServiceRuntime


class ResourcePreferencesCase(unittest.TestCase):
    def test_manual_intent_survives_new_controller_and_explicit_stop_clears(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'.harness-install').mkdir()
            store=ResourcePreferences(root,'image1')
            runtime=ServiceRuntime(lambda timeout:None,lambda:None,persist_manual=lambda v:store.set_manual('powercontext',v))
            runtime.start_manual()
            restored=ResourcePreferences(root,'image1').read()
            self.assertTrue(restored['powercontext']);self.assertFalse(restored['screenshot-to-code'])
            runtime.stop();self.assertFalse(store.read()['powercontext'])
            with self.assertRaises(HarnessError):ResourcePreferences(root,'image2').read()

    def test_cannot_stop_live_call_or_lose_pin_on_failed_stop(self):
        saved=[]
        def fail():raise OSError('docker unavailable')
        runtime=ServiceRuntime(lambda timeout:None,fail,persist_manual=saved.append)
        runtime.start_manual()
        with runtime.use():
            with self.assertRaises(HarnessError):runtime.stop()
        self.assertEqual(saved,[True])
        with self.assertRaises(OSError):runtime.stop()
        self.assertEqual(saved,[True,False,True])
        self.assertEqual(runtime.status()['mode'],'MANUAL')
