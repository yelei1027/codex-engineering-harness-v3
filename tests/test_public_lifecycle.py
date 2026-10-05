"""Public install/migration regressions; all homes, services and releases are fixtures."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = Path(os.environ.get('HARNESS_INSTALLER_UNDER_TEST', ROOT/'scripts/install-release.sh'))


class PublicLifecycleCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base/'project'
        self.repo.mkdir()
        self.state = self.base/'state'
        self.target = self.base/'installed'
        self.tools = self.base/'tools'
        self.tools.mkdir()
        self.assets = self.base/'assets'
        self.assets.mkdir()
        self.env = {**os.environ, 'HOME': str(self.base/'home'), 'CODEX_HOME': str(self.base/'codex'),
                    'HARNESS_STATE_DIR': str(self.state), 'HARNESS_BIN_DIR': str(self.base/'bin'),
                    'HARNESS_INSTALL_DIR': str(self.target), 'FIXTURE_ASSETS': str(self.assets),
                    'PATH': str(self.tools)+os.pathsep+os.environ['PATH'], 'PLANNING_DISABLED': '1'}
        for tool in ('npm', 'uv', 'docker', 'systemctl', 'systemd-run'):
            self.executable(self.tools/tool, '#!/bin/sh\nexit 0\n')
        codex=self.tools/'codex-desktop-fixture'
        self.executable(codex,'#!/bin/sh\ncase "$1" in --version) echo "codex-cli 0.160.0";; --help) echo "app-server exec";; esac\n')
        self.env['HARNESS_CODEX_BINARY']=str(codex)
        self.executable(self.tools/'node','#!/bin/sh\necho "v24.0.0"\n')
        self.executable(self.tools/'gh', '''#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
if sys.argv[1:3] == ['auth','status']: sys.exit(0)
out = Path(sys.argv[sys.argv.index('--dir')+1])
for p in Path(os.environ['FIXTURE_ASSETS']).iterdir(): shutil.copyfile(p,out/p.name)
''')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Lifecycle Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo/'app.py').write_text('print("business")\n')
        self.git('add', 'app.py')
        self.git('commit', '-qm', 'baseline')

    def git(self, *argv):
        return subprocess.check_output(['git', '-C', str(self.repo), *argv]).decode()

    def executable(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)

    def release(self, name='release', version='3.1.1'):
        path = self.base/name
        path.mkdir()
        shutil.copytree(ROOT/'harness_core', path/'harness_core', ignore=shutil.ignore_patterns('__pycache__'))
        self.executable(path/'bin/harness', (ROOT/'bin/harness').read_text())
        (path/'VERSION').write_text(version+'\n')
        (path/'contracts').mkdir()
        shutil.copyfile(ROOT/'contracts/workflow.json', path/'contracts/workflow.json')
        (path/'skills/harness-dev').mkdir(parents=True)
        (path/'skills/harness-dev/SKILL.md').write_text('Fixture router\n')
        shutil.copytree(ROOT/'skills/harness-entry',path/'skills/harness-entry')
        (path/'templates/project').mkdir(parents=True)
        (path/'templates/project/AGENTS.harness.md').write_text('Fixture project routing\n')
        (path/'vendor/planning-with-files/hooks').mkdir(parents=True)
        (path/'vendor/planning-with-files/hooks/codex-hooks.json').write_text('{"hooks":{}}\n')
        (path/'manifests').mkdir()
        for name in ('components', 'compatibility', 'licenses', 'ownership'):
            (path/'manifests'/f'{name}.yaml').write_text('schema_version: 1\n')
        shutil.copyfile(ROOT/'manifests/compatibility.yaml',path/'manifests/compatibility.yaml')
        hook_bytes=(path/'vendor/planning-with-files/hooks/codex-hooks.json').read_bytes()
        source_trees={'planning-with-files':{'files':{'hooks/codex-hooks.json':{'kind':'file','sha256':hashlib.sha256(hook_bytes).hexdigest()}}}}
        for name in ['context-mode','codegraph']:
            package=path/'vendor'/name/'package.json';package.parent.mkdir(parents=True)
            shutil.copyfile(ROOT/'vendor'/name/'package.json',package)
            source_trees[name]={'files':{'package.json':{'kind':'file','sha256':hashlib.sha256(package.read_bytes()).hexdigest()}}}
        (path/'manifests/source-trees.json').write_text(json.dumps(source_trees))
        (path/'scripts').mkdir()
        (path/'scripts/bootstrap.py').write_text('''import os, sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
(root/'private').mkdir(exist_ok=True)
(root/'private/prepared').write_text('fixture progress')
assert sys.argv[1:]==['--services']
if os.environ.get('FIXTURE_BOOTSTRAP_FAIL'): sys.exit(9)
''')
        (path/'machine-binding.json').write_text(json.dumps({'codex_binary': '/bin/echo', 'mcp_servers': {}}))
        return path

    def public(self, release, *argv, ok=True):
        result = subprocess.run([sys.executable, str(release/'bin/harness'), *map(str, argv)],
                                cwd=self.repo, env=self.env, capture_output=True, text=True)
        if ok: self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return result, json.loads(result.stdout)

    def candidate_plan(self,report):
        """Owned toy runtime for lifecycle mechanics, never native acceptance."""
        candidate=Path(report['candidate'])
        runtime=candidate/'runtimes/node/fixture-command'
        self.executable(runtime,'#!/bin/sh\nexit 0\n')
        binding=json.loads((candidate/'machine-binding.json').read_text())
        binding.update(mcp_servers={'test':{'command':str(runtime),'args':[]}},
                       component_commands={'test':[str(runtime),'--version']})
        (candidate/'machine-binding.json').write_text(json.dumps(binding))
        script='from pathlib import Path;from harness_core.software import capture;capture(Path('+repr(str(candidate))+'),["runtimes/node"])'
        subprocess.run([sys.executable,'-c',script],cwd=candidate,env={**self.env,'PYTHONDONTWRITEBYTECODE':'1'},check=True,capture_output=True)
        report['machine_binding_hash']=hashlib.sha256(json.dumps(binding,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        from harness_core.acceptance import REQUIRED
        return {'release_hash':report['release_hash'],'machine_binding_hash':report['machine_binding_hash'],
                'checks':[{'category':category,'command':['${PYTHON}','-c','print("{\\"status\\":\\"PASS\\"}")']}
                          for category in sorted(REQUIRED)]}

    def bundle(self, release, special=None):
        asset = self.assets/'codex-engineering-harness-3.1.1-linux-x86_64.tar.gz'
        with tarfile.open(asset, 'w:gz') as tar:
            # No machine binding is distributed; the fixture binary falls back to the native runtime.
            tar.add(release, arcname='codex-engineering-harness', filter=lambda m: None if m.name.endswith('machine-binding.json') else m)
            if special:
                member, content = special
                tar.addfile(member, io.BytesIO(content) if content is not None else None)
        checksum = hashlib.sha256(asset.read_bytes()).hexdigest()
        Path(str(asset)+'.sha256').write_text(checksum+'  '+asset.name+'\n')
        return asset

    def install_release(self, **changes):
        return subprocess.run(['bash', str(INSTALLER), 'v3.1.1'], env={**self.env, **changes},
                              capture_output=True, text=True, timeout=20)

    def test_installer_preserves_unmanaged_nonempty_target_before_any_preparation(self):
        self.bundle(self.release())
        self.target.mkdir()
        sentinel = self.target/'user.txt'
        sentinel.write_bytes(b'user original\x00')
        result = self.install_release(FIXTURE_BOOTSTRAP_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(sentinel.exists(), result.stdout+result.stderr)
        self.assertEqual(sentinel.read_bytes(), b'user original\x00')
        self.assertFalse((self.target/'bin').exists())
        self.assertFalse((self.state/'accepted.json').exists())

    def test_installer_failure_is_resumable_without_losing_progress_or_new_user_files(self):
        self.bundle(self.release())
        failed = self.install_release(FIXTURE_BOOTSTRAP_FAIL='1')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('完整组件准备', failed.stderr)
        progress = self.target/'private/prepared'
        self.assertEqual(progress.read_text(), 'fixture progress')
        (self.target/'user-note.txt').write_text('retain after failure')
        before = (self.target/'bin/harness').read_bytes()
        completed = self.install_release()
        self.assertEqual(completed.returncode, 0, completed.stdout+completed.stderr)
        self.assertEqual((self.target/'user-note.txt').read_text(), 'retain after failure')
        self.assertEqual((self.target/'bin/harness').read_bytes(), before)
        self.assertEqual(json.loads((self.state/'accepted.json').read_text())['root'], str(self.target))
        self.assertTrue((self.base/'bin/harness').is_file())
        # Running it twice cannot replace the accepted release.
        self.assertNotEqual(self.install_release().returncode, 0)
        self.assertEqual((self.target/'user-note.txt').read_text(), 'retain after failure')

    def test_installer_refuses_modified_owned_source_on_retry(self):
        self.bundle(self.release())
        self.assertNotEqual(self.install_release(FIXTURE_BOOTSTRAP_FAIL='1').returncode, 0)
        source = self.target/'bin/harness'
        source.write_text('user change')
        result = self.install_release()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(source.read_text(), 'user change')
        self.assertFalse((self.state/'accepted.json').exists())

    def test_installer_checksum_mismatch_never_creates_target(self):
        asset = self.bundle(self.release())
        Path(str(asset)+'.sha256').write_text('0'*64+'  '+asset.name+'\n')
        self.assertNotEqual(self.install_release().returncode, 0)
        self.assertFalse(self.target.exists())

    def test_installer_archive_traversal_never_extracts(self):
        member = tarfile.TarInfo('../escaped.txt'); member.size = 4
        self.bundle(self.release(), (member, b'evil'))
        self.assertNotEqual(self.install_release().returncode, 0)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.base/'escaped.txt').exists())

    def test_installer_unsafe_links_special_files_and_duplicate_paths_are_rejected(self):
        release = self.release()
        cases = [('symlink', 'codex-engineering-harness/vendor/test/link'),
                 ('hardlink', 'codex-engineering-harness/link'),
                 ('fifo', 'codex-engineering-harness/pipe'),
                 ('duplicate', 'codex-engineering-harness/VERSION')]
        for kind, name in cases:
            with self.subTest(kind=kind):
                member = tarfile.TarInfo(name)
                content = None
                if kind=='symlink': member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'
                elif kind=='hardlink': member.type = tarfile.LNKTYPE; member.linkname = 'codex-engineering-harness/VERSION'
                elif kind=='fifo': member.type = tarfile.FIFOTYPE
                else: content=b'3.1.1\n';member.size=len(content)
                self.bundle(release, (member, content))
                self.assertNotEqual(self.install_release().returncode, 0)
                self.assertFalse(self.target.exists())

    def test_installer_preserves_symlink_target_and_target_file(self):
        self.bundle(self.release())
        outside=self.base/'outside';outside.mkdir();(outside/'user').write_text('safe')
        self.target.symlink_to(outside)
        self.assertNotEqual(self.install_release().returncode, 0)
        self.assertEqual((outside/'user').read_text(), 'safe')
        self.assertTrue(self.target.is_symlink())
        self.target.unlink();self.target.write_text('user file')
        self.assertNotEqual(self.install_release().returncode, 0)
        self.assertEqual(self.target.read_text(), 'user file')

    def bound_releases(self, profile='L1'):
        old=self.release('old','3.1.0');new=self.release('new','3.1.1')
        self.public(old,'install');self.public(old,'enable',self.repo,'--profile',profile)
        # Fixture promotion changes only the accepted registration. Its source
        # hash comes from the actual public check. Full candidate acceptance is
        # independently covered by the preexisting candidate integration tests.
        _,check=self.public(new,'check')
        (self.state/'accepted.json').write_text(json.dumps({'root':str(new),'version':'3.1.1','release_hash':check['release_hash']}))
        return old,new

    def test_migration_preview_is_read_only_and_apply_preserves_user_state(self):
        (self.repo/'AGENTS.md').write_text('user instructions\n')
        old,new=self.bound_releases('L2')
        binding=self.repo/'.harness/state/project.json'
        before=binding.read_bytes()
        (self.repo/'AGENTS.md').write_text((self.repo/'AGENTS.md').read_text()+'later user instructions\n')
        preserved=self.repo/'.harness/task/retained-note';preserved.parent.mkdir(parents=True);preserved.write_text('task data')
        _,plan=self.public(new,'migrate',self.repo,'--from',old)
        self.assertEqual(plan['status'],'MIGRATION_PLANNED');self.assertEqual(binding.read_bytes(),before)
        self.assertEqual((self.repo/'.agents/skills/harness-dev').resolve(),old/'skills/harness-dev')
        _,result=self.public(new,'migrate',self.repo,'--from',old,'--apply')
        self.assertEqual(result['status'],'MIGRATED')
        self.assertEqual(json.loads(binding.read_text())['harness_root'],str(new))
        self.assertEqual((self.repo/'.agents/skills/harness-dev').resolve(),new/'skills/harness-dev')
        self.assertIn('later user instructions',(self.repo/'AGENTS.md').read_text())
        self.assertEqual(preserved.read_text(),'task data')
        self.assertEqual((self.repo/'app.py').read_text(),'print("business")\n')
        self.assertTrue(old.is_dir())
        self.public(new,'rollback',result['transaction_id'])
        self.assertEqual(binding.read_bytes(),before)
        self.assertEqual(preserved.read_text(),'task data')
        self.assertIn('later user instructions',(self.repo/'AGENTS.md').read_text())

    def test_migration_refuses_wrong_source_modified_overlay_or_unaccepted_target(self):
        old,new=self.bound_releases()
        binding=self.repo/'.harness/state/project.json';before=binding.read_bytes()
        result,_=self.public(new,'migrate',self.repo,'--from',new,'--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(binding.read_bytes(),before)
        accepted=self.state/'accepted.json';accepted.write_text('{}')
        result,_=self.public(new,'migrate',self.repo,'--from',old,'--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(binding.read_bytes(),before)
        _,check=self.public(new,'check');accepted.write_text(json.dumps({'root':str(new),'version':'3.1.1','release_hash':check['release_hash']}))
        (self.repo/'harness.yaml').write_text('user changed')
        result,_=self.public(new,'migrate',self.repo,'--from',old,'--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(binding.read_bytes(),before)
        self.assertEqual((self.repo/'harness.yaml').read_text(),'user changed')

    def test_migration_defers_active_and_paused_tasks_without_changing_old_binding(self):
        old,new=self.bound_releases()
        self.public(old,'task','create',self.repo,'--id','active','--risk','L2')
        binding=self.repo/'.harness/state/project.json';before=binding.read_bytes()
        _,plan=self.public(new,'migrate',self.repo,'--from',old)
        self.assertEqual(plan['status'],'MIGRATION_DEFERRED');self.assertEqual(plan['active_tasks'],['active'])
        result,_=self.public(new,'migrate',self.repo,'--from',old,'--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(binding.read_bytes(),before)
        self.public(old,'task','pause',self.repo,'--id','active','--resume','A01','--reason','fixture pause')
        result,_=self.public(new,'migrate',self.repo,'--from',old,'--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(binding.read_bytes(),before)
        self.assertEqual((self.repo/'.agents/skills/harness-dev').resolve(),old/'skills/harness-dev')

    def test_migration_enable_failure_restores_old_binding_and_unknown_config(self):
        old,new=self.bound_releases()
        (new/'machine-binding.json').write_text(json.dumps({'codex_binary':'/missing-native-runtime','mcp_servers':{}}))
        binding=self.repo/'.harness/state/project.json';before=binding.read_bytes()
        (self.repo/'AGENTS.md').write_text((self.repo/'AGENTS.md').read_text()+'user append\n')
        result,_=self.public(new,'migrate',self.repo,'--from',old,'--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(binding.read_bytes(),before)
        self.assertEqual((self.repo/'.agents/skills/harness-dev').resolve(),old/'skills/harness-dev')
        self.assertIn('user append',(self.repo/'AGENTS.md').read_text())

    def test_migration_rollback_conflict_preserves_later_user_edit_and_new_binding(self):
        old,new=self.bound_releases()
        _,result=self.public(new,'migrate',self.repo,'--from',old,'--apply')
        binding=self.repo/'.harness/state/project.json';before=binding.read_bytes()
        (self.repo/'harness.yaml').write_text('later user edit')
        outcome,_=self.public(new,'rollback',result['transaction_id'],ok=False)
        self.assertNotEqual(outcome.returncode,0);self.assertEqual(binding.read_bytes(),before)
        self.assertEqual((self.repo/'harness.yaml').read_text(),'later user edit')

    def test_public_candidate_promotion_then_migration_and_rollback(self):
        old=self.release('old','3.1.0');new=self.release('new')
        (new/'tests').mkdir()
        (new/'tests/test_candidate.py').write_text('import unittest,os\nclass Candidate(unittest.TestCase):\n def test_isolation(self):\n  self.assertIn("candidate-private",os.environ["HARNESS_STATE_DIR"])\n')
        self.public(old,'install');self.public(old,'enable',self.repo,'--profile','L2')
        self.public(old,'task','create',self.repo,'--id','finished','--intent','explanation')
        self.public(old,'task','transition',self.repo,'--id','finished','--to','D00','--reason','explanation')
        self.public(old,'task','transition',self.repo,'--id','finished','--to','ZINFO','--reason','complete')
        event=self.repo/'.harness/task/finished/events.jsonl';before=event.read_bytes()
        _,report=self.public(old,'update','--to',new)
        candidate=Path(report['candidate'])
        plan=self.base/'acceptance.json'
        plan.write_text(json.dumps(self.candidate_plan(report)))
        _,promoted=self.public(old,'update','--to',candidate,'--promote','--acceptance-plan',plan)
        self.assertEqual(promoted['status'],'PROMOTED')
        # Promotion must not itself rebind a project or its completed task.
        self.assertEqual(json.loads((self.repo/'.harness/state/project.json').read_text())['harness_root'],str(old))
        self.assertEqual(event.read_bytes(),before)
        _,migrated=self.public(candidate,'migrate',self.repo,'--from',old,'--apply')
        self.assertEqual(migrated['status'],'MIGRATED');self.assertEqual(event.read_bytes(),before)
        _,dispatched=self.public(self.base,'status')
        self.assertEqual(dispatched['root'],str(candidate))
        self.public(candidate,'rollback',migrated['transaction_id'])
        self.assertEqual(json.loads((self.repo/'.harness/state/project.json').read_text())['harness_root'],str(old))
        self.assertEqual(event.read_bytes(),before)

    def test_candidate_copy_does_not_import_source_runtime_or_service_bindings(self):
        old=self.release('old');source=self.release('prepared-source')
        (source/'tests').mkdir();(source/'tests/test_smoke.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        foreign=source/'runtimes/node/foreign';self.executable(foreign,'#!/bin/sh\nexit 0\n')
        (source/'machine-binding.json').write_text(json.dumps({'codex_binary':'/bin/echo','mcp_servers':{'borrowed':{'command':str(foreign)}},'component_commands':{'borrowed':[str(foreign)]}}))
        self.public(old,'install');_,report=self.public(old,'update','--to',source)
        binding=json.loads((Path(report['candidate'])/'machine-binding.json').read_text())
        self.assertEqual(binding,{'codex_binary':'/bin/echo'})

    def test_third_source_runtime_binding_rejected_before_acceptance_commands(self):
        old=self.release('old');source=self.release('prepared-source')
        (source/'tests').mkdir();(source/'tests/test_smoke.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.public(old,'install');before=(self.state/'accepted.json').read_bytes()
        _,report=self.public(old,'update','--to',source);candidate=Path(report['candidate']);plan=self.candidate_plan(report)
        foreign=source/'runtimes/node/foreign';self.executable(foreign,'#!/bin/sh\nexit 0\n')
        binding=json.loads((candidate/'machine-binding.json').read_text());binding['mcp_servers']['test']['command']=str(foreign)
        (candidate/'machine-binding.json').write_text(json.dumps(binding))
        plan['machine_binding_hash']=hashlib.sha256(json.dumps(binding,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        marker=candidate/'private/acceptance-ran'
        for check in plan['checks']:check['command']=['${PYTHON}','-c','from pathlib import Path;Path('+repr(str(marker))+').parent.mkdir(exist_ok=True);Path('+repr(str(marker))+').write_text("ran")']
        path=self.base/'third-source-plan.json';path.write_text(json.dumps(plan))
        result,reply=self.public(old,'update','--to',candidate,'--promote','--acceptance-plan',path,ok=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('another runtime path',reply['error'])
        self.assertFalse(marker.exists());self.assertEqual((self.state/'accepted.json').read_bytes(),before)

    def test_missing_hook_category_is_rejected_even_without_binding_hook_flag(self):
        old=self.release('old');source=self.release('new')
        (source/'tests').mkdir();(source/'tests/test_smoke.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.public(old,'install');before=(self.state/'accepted.json').read_bytes()
        _,report=self.public(old,'update','--to',source);candidate=Path(report['candidate']);plan=self.candidate_plan(report)
        self.assertNotIn('hooks',json.loads((candidate/'machine-binding.json').read_text()))
        plan['checks']=[c for c in plan['checks']if c['category']!='HOOKS'];path=self.base/'no-hooks-plan.json';path.write_text(json.dumps(plan))
        result,reply=self.public(old,'update','--to',candidate,'--promote','--acceptance-plan',path,ok=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('HOOKS',reply['error'])
        self.assertEqual((self.state/'accepted.json').read_bytes(),before)

    def test_terminal_review_cancel_recovery_tasks_do_not_block_migration_or_uninstall(self):
        from collections import deque
        contract=json.loads((ROOT/'contracts/workflow.json').read_text());edges={n['id']:[e['target']for e in n['edges']]for n in contract['nodes']}
        for terminal in ['ZREPORT','ZCANCEL','ZRECOVERED']:
            with self.subTest(terminal=terminal):
                case=PublicLifecycleCase();case.setUp();self.addCleanup(case.doCleanups)
                old,new=case.bound_releases();intent='review_only'if terminal=='ZREPORT'else'development'
                queue=deque([['A00']]);seen=set();route=None
                while queue:
                    path=queue.popleft();node=path[-1]
                    if node==terminal:route=path;break
                    if node in seen:continue
                    seen.add(node)
                    for next_node in edges[node]:
                        if next_node in {'RESUME','P01','P03','P06'}:continue
                        if node=='D00'and next_node!='A01':continue
                        if intent=='review_only'and next_node in {'P00','T03','T05','X00'}:continue
                        queue.append(path+[next_node])
                case.assertIsNotNone(route)
                case.public(old,'task','create',case.repo,'--id','terminal','--risk','L0','--intent',intent)
                for node in route[1:]:case.public(old,'task','transition',case.repo,'--id','terminal','--to',node,'--reason','Synthetic terminal-state classification fixture')
                _,moved=case.public(new,'migrate',case.repo,'--from',old,'--apply');case.assertEqual(moved['status'],'MIGRATED')
                _,preview=case.public(new,'uninstall');case.assertEqual(preview['active_tasks'],[])
                _,removed=case.public(new,'uninstall','--apply');case.assertIn(removed['status'],['UNINSTALLED','UNINSTALLED_WITH_PRESERVED_CHANGES'])
                case.assertTrue((case.repo/'.harness/task/terminal/events.jsonl').is_file())

    def test_promotion_conflicts_with_same_candidate_root_locks_from_private_state(self):
        from unittest.mock import patch
        from harness_core.common import lock
        old=self.release('old');source=self.release('new')
        (source/'tests').mkdir();(source/'tests/test_smoke.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.public(old,'install');before=(self.state/'accepted.json').read_bytes()
        _,report=self.public(old,'update','--to',source);candidate=Path(report['candidate']);plan=self.base/'prepared-plan.json';plan.write_text(json.dumps(self.candidate_plan(report)))
        self.public(old,'update','--to',candidate,'--acceptance-plan',plan)
        for name in ['preparation.lock','services.lock']:
            with self.subTest(lock=name),patch.dict(os.environ,{**self.env,'HARNESS_STATE_DIR':str(candidate/'candidate-private/state')}):
                with lock(candidate/'.harness-install'/name):
                    result,reply=self.public(old,'update','--to',candidate,'--promote',ok=False)
                    self.assertNotEqual(result.returncode,0);self.assertIn('Another Harness operation',reply['error'])
                    self.assertEqual((self.state/'accepted.json').read_bytes(),before)

    def test_live_service_binding_rejects_missing_resources_ports_and_mounts(self):
        import copy
        from unittest.mock import patch
        from harness_core import sidecars
        from harness_core.common import HarnessError
        layout={'recipes':{'powercontext':{}},'namespace':'owned','data_namespace':'owned-data','ports':{'powercontext':28080}}
        resolved={'services':{'powercontext':{'ports':[{'host_ip':'127.0.0.1','published':28080,'target':8000}],
                                              'volumes':[{'type':'volume','source':'data','target':'/data'}]}},
                  'volumes':{'data':{'name':'owned-data-powercontext-data'}}}
        ports={'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':'28080'}]}
        valid={'State':{'Running':True},'HostConfig':{'PortBindings':ports},'NetworkSettings':{'Ports':ports},
               'Mounts':[{'Type':'volume','Name':'owned-data-powercontext-data','Destination':'/data'}]}
        for problem in ['missing_container','missing_volume','wrong_port','wrong_mount','extra_port','extra_mount','readonly_mount']:
            with self.subTest(problem=problem):
                info=copy.deepcopy(valid)
                if problem=='wrong_port':info['HostConfig']['PortBindings']['8000/tcp'][0]['HostPort']='29090'
                if problem=='wrong_mount':info['Mounts'][0]['Name']='another-root-volume'
                if problem=='extra_port':
                    for section,key in [('HostConfig','PortBindings'),('NetworkSettings','Ports')]:
                        info[section][key]['9000/tcp']=[{'HostIp':'0.0.0.0','HostPort':'29090'}]
                if problem=='extra_mount':info['Mounts'].append({'Type':'bind','Source':'/external-user-data','Destination':'/extra'})
                if problem=='readonly_mount':info['Mounts'][0]['RW']=False
                def docker(argv,**kwargs):
                    if argv[0]=='volume':return None if problem=='missing_volume'else'[{}]'
                    return json.dumps([info])
                with patch.object(sidecars,'layout',return_value=layout),patch.object(sidecars,'inspect_owned',return_value=[]if problem=='missing_container'else[{'state':'running'}]),patch.object(sidecars,'inspect_volumes'),patch.object(sidecars,'compose',return_value=json.dumps(resolved)),patch.object(sidecars,'docker',side_effect=docker):
                    with self.assertRaises(HarnessError):sidecars.require_live_binding(self.base)

    def test_promotion_rejects_orphan_pending_and_unrecovered_package_journals(self):
        old=self.release('old');source=self.release('new')
        (source/'tests').mkdir();(source/'tests/test_smoke.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.public(old,'install');before=(self.state/'accepted.json').read_bytes()
        _,report=self.public(old,'update','--to',source);candidate=Path(report['candidate'])
        plan=self.base/'prepared-plan.json';plan.write_text(json.dumps(self.candidate_plan(report)))
        self.public(old,'update','--to',candidate,'--acceptance-plan',plan)
        journal=candidate/'.harness-install/preparation-data/orphan.json';journal.parent.mkdir(parents=True)
        worker=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
        try:
            for name,fields in [('orphan',{'writers':[{'group':worker.pid}]}),('pending',{'writer_pending':True}),('unrecovered',{})]:
                with self.subTest(state=name):
                    doc={'root':str(candidate),'status':'STARTED',**fields};journal.write_text(json.dumps(doc));saved=journal.read_bytes()
                    result,reply=self.public(old,'update','--to',candidate,'--promote',ok=False)
                    self.assertNotEqual(result.returncode,0);self.assertIn('Package',reply['error'])
                    self.assertEqual(journal.read_bytes(),saved);self.assertEqual((self.state/'accepted.json').read_bytes(),before)
        finally:
            worker.terminate();worker.wait(timeout=5)
        journal.write_text(json.dumps({'root':str(candidate),'status':'RESTORED'}))
        _,promoted=self.public(old,'update','--to',candidate,'--promote');self.assertEqual(promoted['status'],'PROMOTED')

    def test_public_promotion_rejects_changed_candidate_service_ownership_before_accepting(self):
        old=self.release('old');new=self.release('new')
        shutil.copytree(ROOT/'templates/services',new/'templates/services')
        (new/'tests').mkdir()
        (new/'tests/test_candidate.py').write_text('import unittest\nclass Candidate(unittest.TestCase):\n def test_isolated(self): self.assertTrue(True)\n')
        self.public(old,'install')
        before=(self.state/'accepted.json').read_bytes()
        _,report=self.public(old,'update','--to',new)
        candidate=Path(report['candidate'])
        # Preparation in the acceptance control state still owns resources in
        # the shared state through the candidate's source receipt.
        script='from pathlib import Path;from harness_core.sidecars import prepare_runtime;prepare_runtime(Path('+repr(str(candidate))+'),"powercontext")'
        env={**self.env,'HARNESS_STATE_DIR':str(candidate/'candidate-private/state'),'PYTHONDONTWRITEBYTECODE':'1'}
        subprocess.run([sys.executable,'-c',script],cwd=candidate,env=env,check=True,capture_output=True)
        layout=candidate/'.harness-install/services.json';data=json.loads(layout.read_text())
        self.assertEqual(data['state_id'],hashlib.sha256(str(self.state).encode()).hexdigest()[:16])
        data['state_id']='0'*16;layout.write_text(json.dumps(data));changed=layout.read_bytes()
        plan=self.base/'acceptance.json';plan.write_text(json.dumps({'release_hash':report['release_hash'],'machine_binding_hash':report['machine_binding_hash'],
            'checks':[{'category':c,'command':['${PYTHON}','-c','print("{\\"status\\":\\"PASS\\"}")']}
                      for c in ('STATIC','RUNTIME','TRIGGER','MCP','HOOKS','REVIEW','RECOVERY','ISOLATION')]}))
        result,reply=self.public(old,'update','--to',candidate,'--promote','--acceptance-plan',plan,ok=False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Service ownership does not match',reply['error'])
        self.assertEqual((self.state/'accepted.json').read_bytes(),before)
        self.assertEqual(layout.read_bytes(),changed)

    def test_public_promotion_rejects_unowned_runtime_areas_with_or_without_snapshot(self):
        for snapshot in [False,True]:
            with self.subTest(snapshot=snapshot):
                case=PublicLifecycleCase();case.setUp();self.addCleanup(case.doCleanups)
                old=case.release('old-'+str(snapshot));new=case.release('new-'+str(snapshot))
                (new/'tests').mkdir();(new/'tests/test_candidate.py').write_text('import unittest\nclass Candidate(unittest.TestCase):\n def test_isolated(self): self.assertTrue(True)\n')
                case.public(old,'install');before=(case.state/'accepted.json').read_bytes()
                _,report=case.public(old,'update','--to',new);candidate=Path(report['candidate'])
                if snapshot:
                    known=candidate/'runtimes/node/known';known.parent.mkdir(parents=True);known.write_text('managed dependency')
                    script='from pathlib import Path;from harness_core.software import capture;capture(Path('+repr(str(candidate))+'),["runtimes/node"])'
                    subprocess.run([sys.executable,'-c',script],cwd=candidate,env={**case.env,'PYTHONDONTWRITEBYTECODE':'1'},check=True,capture_output=True)
                unknown=candidate/'runtimes/unknown-area/unknown.txt';unknown.parent.mkdir(parents=True);unknown.write_text('USER ORIGINAL')
                plan=case.base/('acceptance-'+str(snapshot)+'.json');plan.write_text(json.dumps({'release_hash':report['release_hash'],'machine_binding_hash':report['machine_binding_hash'],
                    'checks':[{'category':c,'command':['${PYTHON}','-c','print("{\\"status\\":\\"PASS\\"}")']}
                              for c in ('STATIC','RUNTIME','TRIGGER','MCP','HOOKS','REVIEW','RECOVERY','ISOLATION')]}))
                result,reply=case.public(old,'update','--to',candidate,'--promote','--acceptance-plan',plan,ok=False)
                case.assertNotEqual(result.returncode,0);case.assertIn('Unowned runtime files',reply['error'])
                case.assertEqual((case.state/'accepted.json').read_bytes(),before);case.assertEqual(unknown.read_text(),'USER ORIGINAL')

    def test_public_package_and_candidate_exclude_plans_sessions_and_unknown_business_source(self):
        release=self.release()
        (release/'tests').mkdir()
        (release/'tests/test_candidate.py').write_text('import unittest\nclass Candidate(unittest.TestCase):\n def test_runs(self): self.assertTrue(True)\n')
        for name in ('.planning/my-task/task_plan.md','sessions/rollout.jsonl','business-app.py','scripts/.planning/private/progress.md'):
            p=release/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('PRIVATE_PAYLOAD_MUST_NOT_DISTRIBUTE')
        (release/'README.md').write_text('public installation guide')
        package=self.base/'public.tar.gz'
        self.public(release,'package','--output',package)
        with tarfile.open(package) as tar:
            names=tar.getnames()
            self.assertTrue(any(n.endswith('/README.md') for n in names))
            for entry in tar.getmembers():
                if entry.isfile():self.assertNotIn(b'PRIVATE_PAYLOAD_MUST_NOT_DISTRIBUTE',tar.extractfile(entry).read())
        self.public(release,'install')
        _,candidate=self.public(release,'update','--to',release)
        directory=Path(candidate['candidate'])
        self.assertEqual((directory/'README.md').read_text(),'public installation guide')
        self.assertFalse((directory/'.planning').exists());self.assertFalse((directory/'business-app.py').exists())
        self.assertFalse((directory/'sessions').exists());self.assertFalse((directory/'scripts/.planning').exists())

    def test_migration_journal_write_failure_restores_old_overlay(self):
        old,new=self.bound_releases()
        binding=self.repo/'.harness/state/project.json';before=binding.read_bytes()
        (self.state/'refreshes').write_text('unknown user file blocks journal directory')
        result,_=self.public(new,'migrate',self.repo,'--from',old,'--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(binding.read_bytes(),before)
        self.assertEqual((self.repo/'.agents/skills/harness-dev').resolve(),old/'skills/harness-dev')
        self.assertEqual((self.state/'refreshes').read_text(),'unknown user file blocks journal directory')

    def test_migration_refuses_changed_original_release(self):
        old,new=self.bound_releases()
        binding=self.repo/'.harness/state/project.json';before=binding.read_bytes()
        (old/'skills/harness-dev/SKILL.md').write_text('changed original release')
        result,_=self.public(new,'migrate',self.repo,'--from',old,'--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(binding.read_bytes(),before)

    def test_prepare_non_git_preview_then_one_explicit_apply(self):
        release=self.release();project=self.base/'new-app';project.mkdir()
        (project/'business.txt').write_bytes(b'unchanged\x00')
        _,preview=self.public(release,'prepare',project)
        self.assertEqual(preview['status'],'PREPARATION_REQUIRED')
        self.assertEqual(preview['git'],'WILL_INITIALIZE')
        self.assertFalse((project/'.git').exists());self.assertFalse((project/'AGENTS.md').exists())
        self.assertFalse(self.state.exists())
        _,ready=self.public(release,'prepare',project,'--apply')
        self.assertEqual(ready['status'],'ENABLED');self.assertEqual(ready['hooks'],'PENDING_TRUST')
        self.assertEqual((project/'business.txt').read_bytes(),b'unchanged\x00')
        self.assertNotEqual(subprocess.run(['git','-C',str(project),'rev-parse','--verify','HEAD'],capture_output=True).returncode,0)
        _,repeat=self.public(release,'prepare',project)
        self.assertEqual(repeat['status'],'PROJECT_READY')
        self.assertEqual(repeat['confirmation'],'NOT_REQUIRED_FOR_VALID_EXISTING_SETUP')

    def test_start_unbound_project_only_previews_and_preserves_git(self):
        release=self.release();project=self.base/'unbound';project.mkdir()
        _,preview=self.public(release,'start',project)
        self.assertEqual(preview['status'],'PREPARATION_REQUIRED')
        self.assertFalse((project/'.git').exists());self.assertFalse((project/'.codex').exists())

    def test_native_defaults_are_root_toml_and_user_settings_restore_exactly(self):
        import tomllib
        release=self.release()
        config=self.repo/'.codex/config.toml';config.parent.mkdir();original='[mcp_servers.user]\ncommand="/bin/echo"\nargs=["user"]'
        config.write_text(original)
        self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        effective=tomllib.loads(config.read_text())
        self.assertEqual(effective['sandbox_mode'],'read-only')
        self.assertEqual(effective['approval_policy'],'on-request')
        self.assertEqual(effective['approvals_reviewer'],'user')
        self.assertEqual(effective['apps']['_default']['default_tools_approval_mode'],'prompt')
        self.assertEqual(effective['mcp_servers']['user']['args'],['user'])
        self.assertEqual(effective['permissions']['harness-inspect']['extends'],':read-only')
        self.public(release,'disable',self.repo)
        self.assertEqual(config.read_text(),original)

    def test_prepare_preserves_existing_safe_named_inspection_profile(self):
        import tomllib
        release=self.release()
        config=self.repo/'.codex/config.toml';config.parent.mkdir()
        original='[permissions.harness-inspect]\nextends=":read-only"\ndescription="User inspection profile"\n'
        config.write_text(original)
        self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        parsed=tomllib.loads(config.read_text())
        self.assertEqual(parsed['permissions']['harness-inspect']['description'],'User inspection profile')
        self.assertEqual(config.read_text().count('[permissions.harness-inspect]'),1)
        self.public(release,'disable',self.repo)
        self.assertEqual(config.read_text(),original)

    def test_conflicting_project_or_global_inspection_profile_blocks_preparation(self):
        unsafe='[permissions.harness-inspect]\nextends=":read-only"\n[permissions.harness-inspect.filesystem.":workspace_roots"]\n"**"="write"\n'
        for location in ['project','global']:
            with self.subTest(location=location):
                release=self.release('inspection-release-'+location)
                project=self.base/('inspection-project-'+location);project.mkdir()
                config=(project/'.codex/config.toml') if location=='project' else Path(self.env['CODEX_HOME'])/'config.toml'
                config.parent.mkdir(parents=True,exist_ok=True);config.write_text(unsafe)
                result,report=self.public(release,'prepare',project,'--apply','--profile','L1',ok=False)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('harness-inspect',str(report))
                self.assertEqual(config.read_text(),unsafe)
                self.assertFalse((project/'.git').exists())
                self.assertFalse((project/'AGENTS.md').exists())

    def test_conflicting_native_defaults_stop_before_git_or_overlay_writes(self):
        release=self.release();project=self.base/'conflicting';project.mkdir();(project/'.codex').mkdir()
        config=project/'.codex/config.toml';original='sandbox_mode="danger-full-access"\n';config.write_text(original)
        result,_=self.public(release,'prepare',project,ok=False)
        self.assertNotEqual(result.returncode,0);self.assertFalse((project/'.git').exists())
        self.assertEqual(config.read_text(),original);self.assertFalse((project/'AGENTS.md').exists())



    def test_public_binding_records_nondefault_state_for_bound_cli_lookup(self):
        release=self.release();self.public(release,'install')
        self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        binding=json.loads((self.repo/'.harness/state/project.json').read_text())
        self.assertEqual(binding['state_root'],str(self.state))
        self.assertEqual(binding['harness_root'],str(release))
        # It is routing metadata, not a grant of implementation permission.
        self.assertNotIn('approved',binding)

    def exercise_registered_context_hook(self, host_state=None):
        self.state = self.base/"state with space and 'quote'"
        self.env['HARNESS_STATE_DIR'] = str(self.state)
        release = self.release()
        shutil.copyfile(ROOT/'scripts/context_hook.py', release/'scripts/context_hook.py')
        self.public(release, 'install')
        self.public(release, 'prepare', self.repo, '--apply', '--profile', 'L2')
        self.public(release, 'task', 'create', self.repo, '--id', 'hook-state',
                    '--risk', 'L2', '--intent', 'review_only')
        facts = self.base/'hook-facts.json'
        facts.write_text(json.dumps({'objective': 'Read the fixture without changing business code',
            'completed': [], 'remaining': ['Read the fixture'], 'blockers': [],
            'decisions': [], 'next_action': 'Read app.py', 'spec_refs': [], 'tests': [],
            'review': {'status': 'FIXTURE_ONLY'}, 'workers': []}))
        self.public(release, 'context', 'checkpoint', self.repo, '--task', 'hook-state',
                    '--facts', facts, '--resume-node', 'A01')
        command = next(h['command'] for group in json.loads((self.repo/'.codex/hooks.json').read_text())['hooks']['SessionStart']
                       for h in group['hooks'] if 'context_hook.py' in h['command'])
        env = {**self.env, 'PLANNING_DISABLED': '0', 'PYTHONDONTWRITEBYTECODE': '1'}
        env.pop('HARNESS_STATE_DIR', None)
        if host_state is not None:
            env['HARNESS_STATE_DIR'] = str(host_state)
        before = (self.repo/'app.py').read_bytes()
        result = subprocess.run(command, shell=True, cwd=self.repo, env=env, text=True,
                                input=json.dumps({'cwd':str(self.repo), 'hook_event_name':'SessionStart',
                                                  'session_id':'fixture-hook-state'}),
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        output = json.loads(result.stdout)
        self.assertIn('RESUME_OK', output.get('hookSpecificOutput', {}).get('additionalContext', ''), output)
        self.assertEqual((self.repo/'app.py').read_bytes(), before)

    def test_registered_context_hook_recovers_nondefault_state_without_launcher_environment(self):
        self.exercise_registered_context_hook()

    def test_registered_context_hook_ignores_unrelated_host_installation_state(self):
        unrelated = self.base/'unrelated-state'
        unrelated.mkdir()
        sentinel = unrelated/'user-note.txt'
        sentinel.write_text('preserve unrelated state')
        self.exercise_registered_context_hook(unrelated)
        self.assertEqual(sentinel.read_text(), 'preserve unrelated state')
        self.assertEqual(sorted(p.name for p in unrelated.iterdir()), ['user-note.txt'])

    def test_wrong_state_disable_refuses_without_claiming_already_disabled(self):
        release=self.release();self.public(release,'install');self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        original=(self.repo/'.harness/state/project.json').read_bytes();env={**self.env,'HARNESS_STATE_DIR':str(self.base/'wrong-state')}
        result=subprocess.run([sys.executable,str(release/'bin/harness'),'disable',str(self.repo)],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertIn('another installation state',result.stdout)
        self.assertEqual((self.repo/'.harness/state/project.json').read_bytes(),original)
        self.assertTrue((self.state/'projects.json').exists())

    def test_installed_entry_is_owned_and_removed_without_touching_other_skills(self):
        release=self.release();home=Path(self.env['CODEX_HOME'])
        unrelated=home/'skills/user-skill/SKILL.md';unrelated.parent.mkdir(parents=True);unrelated.write_text('user original')
        self.public(release,'install')
        entry=home/'skills/harness-entry'
        self.assertTrue(entry.is_symlink());self.assertEqual(entry.resolve(),release/'skills/harness-entry')
        self.public(release,'install')
        self.public(release,'uninstall','--apply')
        self.assertFalse(entry.exists());self.assertEqual(unrelated.read_text(),'user original')

    def test_install_refuses_unowned_entry_before_registration(self):
        release=self.release();entry=Path(self.env['CODEX_HOME'])/'skills/harness-entry'
        entry.mkdir(parents=True);(entry/'SKILL.md').write_text('user entry')
        result,_=self.public(release,'install',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertFalse((self.state/'accepted.json').exists())
        self.assertEqual((entry/'SKILL.md').read_text(),'user entry');self.assertFalse((self.base/'bin/harness').exists())

    def test_named_native_policy_blocks_preserve_user_append_and_detect_policy_tamper(self):
        release=self.release();self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        config=self.repo/'.codex/config.toml';prepared=config.read_text()
        config.write_text(prepared+'\n[mcp_servers.user_added]\ncommand="/bin/echo"\n')
        self.public(release,'disable',self.repo)
        self.assertIn('user_added',config.read_text());self.assertNotIn('read-only',config.read_text())
        self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        config.write_text(config.read_text().replace('sandbox_mode = "read-only"','sandbox_mode = "danger-full-access"'))
        result,_=self.public(release,'disable',self.repo,ok=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('danger-full-access',config.read_text())


    def test_full_uninstall_preview_then_detach_and_preserve_task_private_and_user_config(self):
        release=self.release();self.public(release,'install')
        (self.repo/'AGENTS.md').write_text('user original instructions')
        self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        task=self.repo/'.harness/task/retained-record.json';task.parent.mkdir(parents=True);task.write_text('task original')
        private=release/'private/context-mode';private.mkdir(parents=True);(private/'memory.db').write_bytes(b'memory original')
        _,plan=self.public(release,'uninstall')
        self.assertEqual(plan['status'],'UNINSTALL_PLANNED');self.assertTrue((self.state/'accepted.json').exists())
        _,result=self.public(release,'uninstall','--apply')
        self.assertEqual(result['status'],'UNINSTALLED');self.assertFalse((self.state/'accepted.json').exists())
        self.assertEqual((self.repo/'AGENTS.md').read_text(),'user original instructions')
        self.assertFalse((self.repo/'.agents/skills/harness-dev').exists());self.assertEqual(task.read_text(),'task original')
        retained=Path(result['preserved_data']['local_install_data'])
        self.assertEqual((retained/'private/context-mode/memory.db').read_bytes(),b'memory original')
        self.assertTrue((retained/'recovery.json').is_file());self.assertTrue((release/'bin/harness').exists())

    def test_full_uninstall_defers_active_task_before_any_registration_write(self):
        release=self.release();self.public(release,'install');self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        self.public(release,'task','create',self.repo,'--id','busy','--risk','L2')
        accepted=(self.state/'accepted.json').read_bytes();binding=(self.repo/'.harness/state/project.json').read_bytes()
        _,preview=self.public(release,'uninstall');self.assertEqual(preview['status'],'UNINSTALL_DEFERRED')
        failed,_=self.public(release,'uninstall','--apply',ok=False);self.assertNotEqual(failed.returncode,0)
        self.assertEqual((self.state/'accepted.json').read_bytes(),accepted)
        self.assertEqual((self.repo/'.harness/state/project.json').read_bytes(),binding)

    def test_full_uninstall_removes_owned_dependencies_and_preserves_runtime_databases(self):
        release=self.release();runtime=release/'runtimes/node';runtime.mkdir(parents=True)
        (runtime/'tool.py').write_text('owned program');(runtime/'cache.db').write_bytes(b'keep database')
        # The production capture seam is used, with only fixture software/data.
        script='from pathlib import Path;from harness_core.software import capture;capture(Path('+repr(str(release))+'),["runtimes/node"])'
        subprocess.run([sys.executable,'-c',script],cwd=release,env=self.env,check=True)
        self.public(release,'install');_,result=self.public(release,'uninstall','--apply')
        self.assertFalse((runtime/'tool.py').exists())
        retained=Path(result['preserved_data']['local_install_data'])
        self.assertEqual((retained/'runtime-data/runtimes/node/cache.db').read_bytes(),b'keep database')

    def test_full_uninstall_refuses_later_runtime_changes_without_detaching_project(self):
        release=self.release();runtime=release/'runtimes/node';runtime.mkdir(parents=True)
        tool=runtime/'tool.py';tool.write_text('initial')
        script='from pathlib import Path;from harness_core.software import capture;capture(Path('+repr(str(release))+'),["runtimes/node"])'
        subprocess.run([sys.executable,'-c',script],cwd=release,env=self.env,check=True)
        self.public(release,'install');self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        tool.write_text('later user change');before=(self.repo/'.harness/state/project.json').read_bytes()
        failed,_=self.public(release,'uninstall','--apply',ok=False);self.assertNotEqual(failed.returncode,0)
        self.assertEqual(tool.read_text(),'later user change');self.assertEqual((self.repo/'.harness/state/project.json').read_bytes(),before)


    def test_reinstall_restores_private_memory_and_task_bindings_without_old_software_or_trust(self):
        release=self.release();self.public(release,'install');self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        task=self.repo/'.harness/task/record';task.parent.mkdir(parents=True);task.write_text('retained task')
        private=release/'private/context-mode';private.mkdir(parents=True);(private/'memory.db').write_bytes(b'retained memory')
        self.public(release,'uninstall','--apply')
        self.public(release,'install')
        _,preview=self.public(release,'recover-data');self.assertEqual(preview['status'],'RECOVERY_PLANNED')
        self.assertFalse(private.exists())
        _,restored=self.public(release,'recover-data','--apply');self.assertEqual(restored['status'],'DATA_RESTORED')
        self.assertEqual((private/'memory.db').read_bytes(),b'retained memory');self.assertEqual(task.read_text(),'retained task')
        self.assertFalse((self.repo/'.codex/hooks.json').exists())
        _,second=self.public(release,'recover-data','--apply');self.assertEqual(second['status'],'ALREADY_RESTORED')
        self.public(release,'prepare',self.repo,'--apply','--profile','L1')
        self.assertEqual(task.read_text(),'retained task')
        self.public(release,'uninstall','--apply')

    def test_recovery_refuses_newer_private_data_without_overwriting_either_copy(self):
        release=self.release();self.public(release,'install')
        private=release/'private';private.mkdir();(private/'memory.db').write_bytes(b'old')
        _,removed=self.public(release,'uninstall','--apply')
        private.mkdir();(private/'memory.db').write_bytes(b'newer')
        failed,_=self.public(release,'recover-data','--apply',ok=False);self.assertNotEqual(failed.returncode,0)
        self.assertEqual((private/'memory.db').read_bytes(),b'newer')
        retained=Path(removed['preserved_data']['local_install_data']);self.assertEqual((retained/'private/memory.db').read_bytes(),b'old')


    def test_public_services_not_prepared_has_no_docker_or_runtime_side_effect(self):
        release=self.release();_,result=self.public(release,'services','status')
        self.assertEqual(result['status'],'NOT_PREPARED');self.assertFalse((release/'runtimes').exists())
        failed,_=self.public(release,'services','start',ok=False)
        self.assertNotEqual(failed.returncode,0);self.assertFalse((release/'runtimes').exists())

    def test_public_services_refuse_changed_recipe_before_any_docker_mutation(self):
        release=self.release();shutil.copytree(ROOT/'templates/services',release/'templates/services')
        script='from pathlib import Path;from harness_core.sidecars import prepare_runtime;prepare_runtime(Path('+repr(str(release))+'),"powercontext");prepare_runtime(Path('+repr(str(release))+'),"screenshot-to-code")'
        subprocess.run([sys.executable,'-c',script],cwd=release,env=self.env,check=True)
        recipe=release/'runtimes/powercontext/compose.yaml';recipe.write_text(recipe.read_text()+'# user changed\n')
        failed,result=self.public(release,'services','start',ok=False)
        self.assertNotEqual(failed.returncode,0);self.assertIn('recipe changed',result['error'])
        self.assertTrue(recipe.read_text().endswith('# user changed\n'))

    def test_service_preparation_preserves_legacy_unowned_runtime(self):
        release=self.release();shutil.copytree(ROOT/'templates/services',release/'templates/services')
        recipe=release/'runtimes/powercontext/compose.yaml';recipe.parent.mkdir(parents=True);recipe.write_text('user legacy configuration')
        script='from pathlib import Path;from harness_core.sidecars import prepare_runtime;prepare_runtime(Path('+repr(str(release))+'),"powercontext")'
        result=subprocess.run([sys.executable,'-c',script],cwd=release,env=self.env,capture_output=True)
        self.assertNotEqual(result.returncode,0);self.assertEqual(recipe.read_text(),'user legacy configuration')
        self.assertFalse((release/'.harness-install/services.json').exists())


    def test_managed_archive_full_uninstall_removes_source_then_reinstalls_safely(self):
        self.bundle(self.release());installed=self.install_release();self.assertEqual(installed.returncode,0,installed.stderr)
        private=self.target/'private/context-mode';private.mkdir(parents=True);(private/'memory.db').write_bytes(b'archive memory')
        _,result=self.public(self.target,'uninstall','--apply')
        self.assertEqual(result['source_cleanup'],'MANAGED_ARCHIVE_FILES')
        self.assertFalse((self.target/'bin/harness').exists());self.assertFalse((self.target/'.harness-install/receipt.json').exists())
        retained=Path(result['preserved_data']['local_install_data']);self.assertEqual((retained/'private/context-mode/memory.db').read_bytes(),b'archive memory')
        reinstalled=self.install_release();self.assertEqual(reinstalled.returncode,0,reinstalled.stdout+reinstalled.stderr)

    def test_single_preparation_install_reuses_retained_data_before_bootstrap(self):
        release=self.release();self.public(release,'install')
        private=release/'private/context-mode';private.mkdir(parents=True);(private/'memory.db').write_bytes(b'prepare recovery')
        self.public(release,'uninstall','--apply')
        _,result=self.public(release,'install','--prepare')
        self.assertEqual(result['retained_data'],'DATA_RESTORED');self.assertEqual((private/'memory.db').read_bytes(),b'prepare recovery')
        self.assertTrue((self.state/'accepted.json').exists())


if __name__=='__main__': unittest.main()
