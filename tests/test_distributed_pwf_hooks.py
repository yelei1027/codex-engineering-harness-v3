"""Exercise the shipped PWF prompt entry after public packaging and copying."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path

import test_public_lifecycle as lifecycle

ROOT = lifecycle.ROOT


class DistributedHooksCase(unittest.TestCase):
    def setUp(self):
        self.fixture = lifecycle.PublicLifecycleCase()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.release = self.fixture.release()
        trees_path = self.release/'manifests/source-trees.json'
        trees = json.loads(trees_path.read_text())
        locked = json.loads((ROOT/'manifests/source-trees.json').read_text())['planning-with-files']['files']
        self.hooks = {name: item for name, item in locked.items()
                      if name.startswith(('.codex/hooks/', '.codex/skills/planning-with-files/'))
                      or name == 'hooks/codex-hooks.json'}
        for name, item in self.hooks.items():
            source = ROOT/'vendor/planning-with-files'/name
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), item['sha256'])
            target = self.release/'vendor/planning-with-files'/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        trees['planning-with-files']['files'].update(self.hooks)
        trees_path.write_text(json.dumps(trees))
        # Same-named user config, secrets, and unrecorded vendor code stay private.
        self.excluded = ['.codex/config.toml', '.codex/auth.json',
                         'vendor/planning-with-files/.codex/config.toml',
                         'vendor/planning-with-files/.codex/hooks/auth.json',
                         'vendor/planning-with-files/.codex/hooks/unknown.py',
                         'vendor/planning-with-files/.codex/skills/planning-with-files/scripts/unknown.py']
        for name in self.excluded:
            path = self.release/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('unowned fixture data')

    def verify_hooks(self, root):
        plan = self.fixture.repo/'.planning/distributed-hook-proof'
        plan.mkdir(parents=True, exist_ok=True)
        for name in ('task_plan', 'findings', 'progress'):
            (plan/(name+'.md')).write_text('# DISTRIBUTED_PLAN_MARKER\nStatus: in_progress\n')
        wrapper = root/'vendor/planning-with-files/.codex/hooks/run_sh.py'
        payload = json.dumps({'cwd': str(self.fixture.repo), 'session_id': 'distributed-fixture',
                              'hook_event_name': 'UserPromptSubmit', 'prompt': 'explain only'})
        for event in ('user-prompt-submit.sh', 'session-start.sh'):
            result = subprocess.run([sys.executable, str(wrapper), event],
                                    cwd=self.fixture.repo, input=payload, capture_output=True,
                                    text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                                                   'PLANNING_DISABLED': '0',
                                                   'PLAN_ID': 'distributed-hook-proof',
                                                   'PWF_PLAN_ROOT': str(self.fixture.repo)}, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertTrue(result.stdout, event+': no planning context was produced')
            context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
            self.assertIn('DISTRIBUTED_PLAN_MARKER', context)
            self.assertIn('BEGIN-PWF-DATA', context)
            self.assertNotIn('nothing injected', context)
        for name, item in self.hooks.items():
            target = root/'vendor/planning-with-files'/name
            self.assertTrue(target.is_file(), name)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), item['sha256'])
        for name in self.excluded:
            self.assertFalse((root/name).exists(), name)

    def test_public_package_contains_executable_locked_hook_dependencies(self):
        archive = self.fixture.base/'portable.tar.gz'
        self.fixture.public(self.release, 'package', '--output', archive)
        unpacked = self.fixture.base/'unpacked'
        with tarfile.open(archive) as tar:
            tar.extractall(unpacked, filter='data')
        self.verify_hooks(unpacked/'codex-engineering-harness')

    def test_public_update_candidate_preserves_hooks_without_user_configuration(self):
        tests = self.release/'tests'
        tests.mkdir()
        (tests/'test_smoke.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_exists(self): self.assertTrue(True)\n')
        _, report = self.fixture.public(self.release, 'update', '--to', self.release)
        self.verify_hooks(Path(report['candidate']))

    def test_missing_locked_hook_fails_check_and_enable_before_project_writes(self):
        missing = self.release/'vendor/planning-with-files/.codex/hooks/run_sh.py'
        missing.unlink()
        _, report = self.fixture.public(self.release, 'check', ok=False)
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn('vendor/planning-with-files/.codex/hooks/run_sh.py', report['missing'])
        result, report = self.fixture.public(self.release, 'enable', self.fixture.repo, '--profile', 'L2', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PWF hook files', str(report))
        self.assertFalse((self.fixture.repo/'.codex/hooks.json').exists())
        self.assertFalse((self.fixture.repo/'AGENTS.md').exists())

    def test_missing_canonical_resolver_fails_before_project_writes(self):
        name = 'vendor/planning-with-files/.codex/skills/planning-with-files/scripts/resolve-plan-dir.sh'
        (self.release/name).unlink()
        _, report = self.fixture.public(self.release, 'check', ok=False)
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn(name, report['missing'])
        result, _ = self.fixture.public(self.release, 'enable', self.fixture.repo, '--profile', 'L2', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.fixture.repo/'.codex/hooks.json').exists())
        self.assertFalse((self.fixture.repo/'AGENTS.md').exists())

    def test_changed_canonical_resolver_fails_before_project_writes(self):
        name = 'vendor/planning-with-files/.codex/skills/planning-with-files/scripts/resolve-plan-dir.sh'
        with (self.release/name).open('a') as stream:
            stream.write('\n# altered locked helper\n')
        _, report = self.fixture.public(self.release, 'check', ok=False)
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue(any(name in error for error in report['errors']))
        result, _ = self.fixture.public(self.release, 'enable', self.fixture.repo, '--profile', 'L2', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.fixture.repo/'.codex/hooks.json').exists())

    def test_missing_manifest_entry_blocks_check_enable_and_package(self):
        name = '.codex/skills/planning-with-files/scripts/resolve-plan-dir.sh'
        manifest = self.release/'manifests/source-trees.json'
        trees = json.loads(manifest.read_text())
        del trees['planning-with-files']['files'][name]
        manifest.write_text(json.dumps(trees))
        self.assertTrue((self.release/'vendor/planning-with-files'/name).is_file())
        _, report = self.fixture.public(self.release, 'check', ok=False)
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue(any(name in error for error in report['errors']))
        result, _ = self.fixture.public(self.release, 'enable', self.fixture.repo, '--profile', 'L2', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.fixture.repo/'.codex/hooks.json').exists())
        archive = self.fixture.base/'existing.tar.gz'
        archive.write_bytes(b'KEEP EXISTING ARCHIVE')
        result, _ = self.fixture.public(self.release, 'package', '--output', archive, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(archive.read_bytes(), b'KEEP EXISTING ARCHIVE')

    def test_absent_runtime_declarations_block_source_copy_before_candidate_creation(self):
        manifest = self.release/'manifests/source-trees.json'
        trees = json.loads(manifest.read_text())
        files = trees['planning-with-files']['files']
        trees['planning-with-files']['files'] = {'hooks/codex-hooks.json': files['hooks/codex-hooks.json']}
        manifest.write_text(json.dumps(trees))
        _, report = self.fixture.public(self.release, 'check', ok=False)
        self.assertEqual(report['status'], 'FAIL')
        result, _ = self.fixture.public(self.release, 'update', '--to', self.release, ok=False)
        self.assertNotEqual(result.returncode, 0)
        candidates = self.fixture.state/'candidates'
        self.assertFalse(candidates.exists() and any(candidates.iterdir()))

    def test_changed_locked_hook_definition_blocks_before_project_writes(self):
        origin = self.release/'vendor/planning-with-files/hooks/codex-hooks.json'
        value = json.loads(origin.read_text())
        value['description'] = 'Changed locked Hook definition'
        origin.write_text(json.dumps(value))
        _, report = self.fixture.public(self.release, 'check', ok=False)
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue(any('hooks/codex-hooks.json' in error for error in report['errors']))
        result, _ = self.fixture.public(self.release, 'enable', self.fixture.repo, '--profile', 'L2', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.fixture.repo/'.codex/hooks.json').exists())

    def test_real_lock_prevents_manifest_and_definition_claiming_minimal_release(self):
        shutil.copy2(ROOT/'harness.lock', self.release/'harness.lock')
        origin = self.release/'vendor/planning-with-files/hooks/codex-hooks.json'
        origin.write_text('{"hooks":{}}\n')
        manifest = self.release/'manifests/source-trees.json'
        trees = json.loads(manifest.read_text())
        trees['planning-with-files'] = {'files': {'hooks/codex-hooks.json': {
            'kind': 'file', 'sha256': hashlib.sha256(origin.read_bytes()).hexdigest()}}}
        manifest.write_text(json.dumps(trees))
        _, report = self.fixture.public(self.release, 'check', ok=False)
        self.assertEqual(report['status'], 'FAIL')
        result, _ = self.fixture.public(self.release, 'package', '--output', self.fixture.base/'bad.tar.gz', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.fixture.base/'bad.tar.gz').exists())
        result, _ = self.fixture.public(self.release, 'enable', self.fixture.repo, '--profile', 'L2', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.fixture.repo/'.codex/hooks.json').exists())
