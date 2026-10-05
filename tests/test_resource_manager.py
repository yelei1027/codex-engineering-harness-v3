import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from harness_core.resource_manager import serve


class ResourceManagerCase(unittest.TestCase):
    def test_partial_listener_failure_closes_prior_socket_without_shutdown_deadlock(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'.harness-install').mkdir()
            value={'identity':'fixture','control_token':'fixture-token','ports':{'screenshot-backend':12340,'screenshot-frontend':12341}}
            backend=Mock();backend.endpoints.return_value={'screenshot-backend':'http://127.0.0.1:12342','screenshot-frontend':'http://127.0.0.1:12343'}
            listener=Mock()
            with patch('harness_core.resource_manager.load',return_value=value), patch('harness_core.resource_manager.OwnedBackend',return_value=backend), patch('harness_core.resource_manager.processes.require_idle'), patch('harness_core.resource_manager.sidecars.inspect_owned',return_value=[]), patch('harness_core.resource_manager.ResourceHTTPServer',side_effect=[listener,OSError('port occupied')]):
                with self.assertRaises(OSError):serve(root,threading.Event())
            listener.shutdown.assert_not_called()
            listener.server_close.assert_called_once()

    def test_second_busy_group_reopens_first_reserved_group(self):
        from harness_core import resource_manager
        from harness_core.common import HarnessError
        actions=[]
        def control(root,action,group):
            actions.append((action,group))
            if action=='quiesce' and group=='screenshot-to-code':raise HarnessError('active request')
        with patch.object(resource_manager,'control',side_effect=control):
            with self.assertRaises(HarnessError):resource_manager.quiesce(Path('/owned'))
        self.assertEqual(actions,[('quiesce','powercontext'),('quiesce','screenshot-to-code'),('resume','powercontext')])


    def test_memory_selection_includes_retained_but_screenshot_selection_does_not(self):
        from harness_core import resource_manager
        with patch.object(resource_manager,'retained_targets',return_value={'retained-one':{}}):
            self.assertEqual(resource_manager.target_groups('/owned','powercontext'),['powercontext','retained-one'])
            self.assertEqual(resource_manager.target_groups('/owned','screenshot-to-code'),['screenshot-to-code'])
