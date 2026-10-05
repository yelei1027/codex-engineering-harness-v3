import json
import unittest
from pathlib import Path
from unittest.mock import patch
import test_public_lifecycle as fixtures
from harness_core import resource_manager
from harness_core.common import HarnessError


class ResourceCLICase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.release()

    def test_unprepared_status_is_read_only_and_does_not_start_services(self):
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        _,result=self.fixture.public(self.root,'resources','status')
        self.assertEqual(result['status'],'NOT_PREPARED')
        after={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_unknown_descriptor_fails_instead_of_falling_back_to_docker(self):
        control=self.root/'.harness-install';control.mkdir(exist_ok=True)
        (control/'resources.json').write_text(json.dumps({'schema_version':99}))
        result,_=self.fixture.public(self.root,'services','start',ok=False)
        self.assertNotEqual(result.returncode,0)

    def test_all_stop_checks_both_services_before_mutation(self):
        calls=[]
        def control(root,action,group):
            calls.append((action,group))
            return {'active_calls':1 if group=='screenshot-to-code' else 0,'state':'RUNNING'}
        with patch.object(resource_manager,'control',side_effect=control):
            with self.assertRaises(HarnessError):resource_manager.manage(Path('/fixture'),'stop')
        self.assertTrue(all(action=='status' for action,group in calls))
