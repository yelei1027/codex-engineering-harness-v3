"""Public freeze evidence privacy is relative to the application, not its ancestors."""
import json
import subprocess
import unittest
from pathlib import Path
import test_public_lifecycle as fixtures


class ReviewEvidencePathCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        old=self.fixture.repo;self.fixture.repo=self.fixture.base/'.test-projects/project'
        self.fixture.repo.parent.mkdir();old.rename(self.fixture.repo);self.repo=self.fixture.repo
        self.root=self.fixture.release();self.fixture.env['PYTHONDONTWRITEBYTECODE']='1'
        (self.repo/'SPEC.md').write_text('Approved app-only change.\n');(self.repo/'checks.json').write_text('{"checks":[]}\n')
        self.fixture.git('add','SPEC.md','checks.json');self.fixture.git('commit','-qm','Fixture evidence baseline')
        self.fixture.public(self.root,'install');self.fixture.public(self.root,'prepare',self.repo,'--apply','--profile','L0')
        self.fixture.public(self.root,'task','create',self.repo,'--id','evidence-path','--risk','L0')
        (self.repo/'app.py').write_text('print("changed")\n')

    def freeze(self,spec,ok=True):
        return self.fixture.public(self.root,'freeze',self.repo,'--task','evidence-path','--base','HEAD','--spec',spec,'--checks',self.repo/'checks.json',ok=ok)

    def test_normal_spec_and_checks_are_frozen_under_private_named_ancestor(self):
        _,result=self.freeze(self.repo/'SPEC.md');snapshot=Path(result['path'])
        self.assertEqual((snapshot/'spec-source').read_bytes(),(self.repo/'SPEC.md').read_bytes())
        self.assertEqual((snapshot/'checks-source.json').read_bytes(),(self.repo/'checks.json').read_bytes())
        self.assertEqual(json.loads((snapshot/'spec-ref.json').read_text())['status'],'PROVIDED_NOT_INDEPENDENTLY_VERIFIED')

    def test_private_evidence_inside_project_is_still_rejected(self):
        path=self.repo/'.harness/private/spec.md';path.parent.mkdir(parents=True);path.write_text('private data')
        result,error=self.freeze(path,ok=False);self.assertNotEqual(result.returncode,0)
        self.assertIn('Private file',error['error'])

    def test_symlink_evidence_is_rejected_without_reading_its_target(self):
        path=self.repo/'spec-link.md';path.symlink_to(self.repo/'SPEC.md')
        result,error=self.freeze(path,ok=False);self.assertNotEqual(result.returncode,0)
        self.assertIn('Symlink',error['error'])
