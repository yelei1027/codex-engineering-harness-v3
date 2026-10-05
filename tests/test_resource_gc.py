import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from harness_core import resource_gc
from harness_core.common import HarnessError
import test_public_lifecycle as fixtures


class ResourceGCCase(unittest.TestCase):
    def test_public_empty_preview_does_not_create_registry(self):
        fixture=fixtures.PublicLifecycleCase();fixture.setUp();self.addCleanup(fixture.doCleanups)
        root=fixture.release();_,result=fixture.public(root,'resources','gc')
        self.assertEqual(result['resources'],[])
        self.assertFalse((fixture.state/'resource-tests').exists())

    def test_only_finished_registered_root_is_collected_and_tamper_preserved(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root=Path(folder)/'installation';root.mkdir()
            state=Path(folder)/'state'
            stack.enter_context(patch.dict(os.environ,{'HARNESS_STATE_DIR':str(state),'XDG_CONFIG_HOME':str(Path(folder)/'config')}))
            layout={'recipes':{'powercontext':{}},'namespace':'owned'}
            for target,value in [('sidecars.layout',layout),('sidecars.manage',{}),('sidecars.inspect_volumes',None),('sidecars.unowned_resource_consumers',[]),('processes.plan',{'active_scopes':[]}),('processes.unregistered_consumers',[]),('software.require_preparation_idle',None),('resource_systemd.verify',None)]:
                stack.enter_context(patch('harness_core.resource_gc.'+target,return_value=value))
            remove=stack.enter_context(patch('harness_core.resource_gc.sidecars._manage'))
            resource_gc.register(root,'fixture-run')
            self.assertEqual(resource_gc.collect()['resources'][0]['state'],'OWNER_RUNNING')
            resource_gc.collect(True);remove.assert_not_called()
            resource_gc.finish('fixture-run')
            self.assertEqual(resource_gc.collect()['resources'][0]['state'],'COLLECTIBLE')
            result=resource_gc.collect(True);self.assertEqual(result['collected'],['fixture-run'])
            remove.assert_called_once_with(root,'remove')
            path,_=resource_gc.paths('fixture-run');path.write_text('{}')
            with self.assertRaises(HarnessError):resource_gc.collect(True)
            self.assertEqual(remove.call_count,1)
