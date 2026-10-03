import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_core import cli,context,memory,review,workflow
from harness_core.common import HarnessError, ROOT, read_json, sha, write_json
from harness_core.transaction import Transaction, image, rollback

CONTRACT=ROOT/'contracts/workflow.json'
if not CONTRACT.exists():CONTRACT=ROOT.parent/'codex-engineering-harness-v3-document-delivery/contracts/harness_v3_workflow_contract.json'

class HarnessCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.repo=self.base/'project';self.repo.mkdir()
        self.machine=self.base/'state'
        self.environment=patch.dict(os.environ,{'HARNESS_STATE_DIR':str(self.machine),'CODEX_HOME':str(self.base/'codex'),'HARNESS_BIN_DIR':str(self.base/'bin')})
        self.environment.start();self.addCleanup(self.environment.stop)
        self.git('init','-q');self.git('config','user.email','test@example.invalid');self.git('config','user.name','Harness Test')
        (self.repo/'source.py').write_text('print(1)\n')
        self.git('add','source.py');self.git('commit','-qm','initial')
        self.original=self.git('rev-parse','HEAD').strip()

    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.repo),*args]).decode()

    def fake_release(self):
        release=self.base/'release';release.mkdir()
        (release/'VERSION').write_text('3.0.0\n')
        (release/'skills/harness-dev').mkdir(parents=True)
        (release/'skills/harness-dev/SKILL.md').write_text('---\nname: harness-dev\ndescription: test\n---\nRouter\n')
        (release/'templates/project').mkdir(parents=True)
        (release/'templates/project/AGENTS.harness.md').write_text('Read local router.\n')
        (release/'vendor/planning-with-files/hooks').mkdir(parents=True)
        shutil.copyfile(ROOT/'vendor/planning-with-files/hooks/codex-hooks.json',release/'vendor/planning-with-files/hooks/codex-hooks.json')
        (release/'contracts').mkdir()
        shutil.copyfile(CONTRACT,release/'contracts/workflow.json')
        write_json(release/'machine-binding.json',{'codex_binary':'/bin/echo','mcp_servers':{'test':{'command':'/bin/echo','args':['hello']}}})
        (release/'manifests').mkdir()
        for name in ['components','compatibility','licenses','ownership']:(release/'manifests'/f'{name}.yaml').write_text('schema_version: 1\n')
        shutil.copytree(ROOT/'harness_core',release/'harness_core',ignore=shutil.ignore_patterns('__pycache__'))
        (release/'bin').mkdir();shutil.copyfile(ROOT/'bin/harness',release/'bin/harness');(release/'bin/harness').chmod(0o755)
        (release/'tests').mkdir();(release/'tests/test_candidate.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_isolated(self):\n  import os\n  self.assertIn("candidate-private",os.environ["HARNESS_STATE_DIR"])\n')
        return release

    def test_transaction_restores_bytes_and_mode(self):
        p=self.repo/'owned';p.write_bytes(b'original\xff');p.chmod(0o640)
        with Transaction('test') as tx:tx.change(p,b'new',0o600)
        self.assertEqual(image(p)['mode'],0o600)
        rollback(tx.id)
        self.assertEqual(p.read_bytes(),b'original\xff');self.assertEqual(image(p)['mode'],0o640)
        self.assertEqual(rollback(tx.id)['status'],'ALREADY_ROLLED_BACK')

    def test_conflict_validates_all_before_restore(self):
        a=self.repo/'a';b=self.repo/'b';a.write_text('A');b.write_text('B')
        with Transaction('test') as tx:tx.change(a,b'A2');tx.change(b,b'B2')
        a.write_text('USER CHANGE')
        with self.assertRaises(HarnessError):rollback(tx.id)
        self.assertEqual(b.read_text(),'B2')

    def test_failed_transaction_restores_prior_touch(self):
        p=self.repo/'a';p.write_text('before')
        with self.assertRaisesRegex(ValueError,'fault'):
            with Transaction('fault') as tx:
                tx.change(p,b'after');raise ValueError('fault')
        self.assertEqual(p.read_text(),'before')

    def test_symlink_parent_rejected(self):
        (self.repo/'linked').symlink_to(self.base)
        with self.assertRaises(HarnessError):
            with Transaction('test') as tx:tx.change(self.repo/'linked/outside',b'unsafe')
        self.assertFalse((self.base/'outside').exists())

    def test_enable_idempotent_disable_preserves_user_bytes(self):
        release=self.fake_release();agents=self.repo/'AGENTS.md';agents.write_bytes(b'user rules without newline')
        with patch.object(cli,'ROOT',release):
            first=cli.enable(self.repo,'L1');second=cli.enable(self.repo,'L1')
            self.assertEqual(second['status'],'ALREADY_ENABLED')
            config=(self.repo/'.codex/config.toml').read_text()
            self.assertEqual(config.count('[mcp_servers."test"]'),1)
            cli.disable(self.repo)
        self.assertEqual(agents.read_bytes(),b'user rules without newline')
        self.assertFalse((self.repo/'.codex/config.toml').exists())
        self.assertFalse((self.repo/'.agents/skills/harness-dev').exists())

    def test_disable_preserves_later_unmanaged_append(self):
        release=self.fake_release()
        with patch.object(cli,'ROOT',release):
            cli.enable(self.repo,'L1')
            p=self.repo/'AGENTS.md';p.write_text(p.read_text()+'Additional user rules\n')
            cli.disable(self.repo)
        self.assertEqual(p.read_text(),'Additional user rules\n')

    def test_unknown_overlay_preflight_no_mutation(self):
        release=self.fake_release();(self.repo/'harness.yaml').write_text('user-owned')
        with patch.object(cli,'ROOT',release),self.assertRaises(HarnessError):cli.enable(self.repo,'L1')
        self.assertFalse((self.repo/'AGENTS.md').exists())
        self.assertEqual((self.repo/'harness.yaml').read_text(),'user-owned')

    def test_mcp_conflict_preflight(self):
        release=self.fake_release();(self.repo/'.codex').mkdir()
        p=self.repo/'.codex/config.toml';p.write_text('[mcp_servers.test]\ncommand="other"\n')
        with patch.object(cli,'ROOT',release),self.assertRaises(HarnessError):cli.enable(self.repo,'L1')
        self.assertEqual(p.read_text(),'[mcp_servers.test]\ncommand="other"\n')

    def test_same_mcp_not_registered_twice(self):
        release=self.fake_release();(self.repo/'.codex').mkdir()
        p=self.repo/'.codex/config.toml';p.write_text('[mcp_servers.test]\ncommand="/bin/echo"\nargs=["hello"]\n')
        with patch.object(cli,'ROOT',release):cli.enable(self.repo,'L1')
        self.assertEqual(p.read_text().count('mcp_servers.test'),1)
        self.assertNotIn('[mcp_servers."test"]',p.read_text())

    def test_modified_managed_skill_disable_refused(self):
        release=self.fake_release()
        with patch.object(cli,'ROOT',release):
            cli.enable(self.repo,'L1');p=self.repo/'.agents/skills/harness-dev';p.unlink();p.mkdir()
            with self.assertRaises(HarnessError):cli.disable(self.repo)

    def test_mixed_review_complete_snapshot(self):
        (self.repo/'source.py').write_text('print(2)\n');self.git('commit','-qam','task committed')
        (self.repo/'source.py').write_text('print(3)\n');self.git('add','source.py')
        (self.repo/'source.py').write_text('print(4)\n');(self.repo/'new.py').write_text('new=1\n')
        result=review.freeze(self.repo,'mixed',self.original);path=Path(result['path'])
        self.assertEqual((path/'source/source.py').read_text(),'print(4)\n')
        self.assertTrue((path/'source/new.py').exists())
        for name in ['committed.patch','tracked.patch','index.patch']:self.assertTrue((path/name).stat().st_size)
        target=read_json(path/'target.json');self.assertEqual(set(target['reviewable_paths']),{'source.py','new.py'})
        before=review.review_status(self.repo,'mixed',result['snapshot_id'])
        write_json(path/'review-results/main-review.json',{'files':[{'path':p,'status':'reviewed'} for p in target['reviewable_paths']],'findings':[]})
        after=review.review_status(self.repo,'mixed',result['snapshot_id'])
        self.assertEqual(before['input_hash'],after['input_hash']);self.assertEqual(after['status'],'COMPLETE_RECORDED')
        (self.repo/'source.py').write_text('changed after review\n')
        self.assertEqual(review.review_status(self.repo,'mixed',result['snapshot_id'])['status'],'STALE')

    def test_snapshot_symlink_binary_secret_and_fifo(self):
        outside=self.base/'private';outside.write_text('secret external')
        (self.repo/'link.py').symlink_to(outside)
        (self.repo/'asset.bin').write_bytes(b'\x00\x01binary')
        (self.repo/'.env').write_text('API_KEY=secret')
        os.mkfifo(self.repo/'pipe')
        result=review.freeze(self.repo,'special',self.original);p=Path(result['path']);target=read_json(p/'target.json')
        self.assertFalse((p/'source/link.py').exists());self.assertFalse((p/'source/.env').exists())
        entries={e['path']:e for e in target['files']}
        self.assertEqual(entries['link.py']['skip_reason'],'SYMLINK_NOT_DEREFERENCED')
        self.assertEqual(review.file_image(self.repo,'pipe')['kind'],'special')
        self.assertNotIn('pipe',entries) # Git does not enumerate untracked FIFOs
        self.assertIn('BINARY',entries['asset.bin']['skip_reason'])
        self.assertNotIn('secret external',''.join(x.read_text(errors='ignore') for x in p.rglob('*') if x.is_file()))

    def test_input_tamper_detected(self):
        result=review.freeze(self.repo,'tamper',self.original);p=Path(result['path'])/'source/source.py';p.chmod(0o600);p.write_text('tampered')
        self.assertEqual(review.review_status(self.repo,'tamper',result['snapshot_id'])['status'],'TAMPERED')

    def test_snapshot_race_rejected(self):
        actual=review.fingerprint;calls=[0]
        def racing(*args):
            calls[0]+=1
            if calls[0]>1:(self.repo/'source.py').write_text('race')
            return actual(*args)
        with patch.object(review,'fingerprint',racing),self.assertRaises(HarnessError):review.freeze(self.repo,'race',self.original)
        self.assertEqual(list((self.repo/'.harness-artifacts/review/race').iterdir()),[])

    def test_contract_transition_risk_and_recovery(self):
        release=self.fake_release()
        with patch.object(workflow,'ROOT',release):
            workflow.create(self.repo,'task','L1','development')
            self.assertEqual(workflow.transition(self.repo,'task','D00','classified')['node'],'D00')
            with self.assertRaises(HarnessError):workflow.transition(self.repo,'task','ZDONE','skip gates')
            with self.assertRaises(HarnessError):workflow.transition(self.repo,'task','A01','downgrade',risk='L0')
            workflow.pause(self.repo,'task','A01','pause')
            (self.repo/'source.py').write_text('changed')
            self.assertEqual(workflow.resume(self.repo,'task')['node'],'V00')

    def test_event_log_recovers_missing_checkpoint(self):
        release=self.fake_release()
        with patch.object(workflow,'ROOT',release):
            workflow.create(self.repo,'task','L0','development')
            workflow.transition(self.repo,'task','D00','route')
            (self.repo/'.harness/task/task/state.json').unlink()
            self.assertEqual(workflow.load(workflow.task_path(self.repo,'task'))['node'],'D00')

    def test_publication_requires_exact_head_action(self):
        release=self.fake_release()
        with patch.object(workflow,'ROOT',release):
            state=workflow.create(self.repo,'task','L1','development');state['node']='U02'
            workflow.save(workflow.task_path(self.repo,'task'),state,{'action':'fixture'})
            with self.assertRaises(HarnessError):workflow.transition(self.repo,'task','P01','unauthorized')
            proof=self.base/'proof.json';write_json(proof,{'authorization_id':'user-request','head':self.original,'approved_action':'commit_push'})
            self.assertEqual(workflow.transition(self.repo,'task','P01','approved exact candidate',str(proof))['node'],'P01')

    def test_review_only_rejects_publish(self):
        release=self.fake_release()
        with patch.object(workflow,'ROOT',release):
            state=workflow.create(self.repo,'review','L1','review_only');state['node']='D23'
            workflow.save(workflow.task_path(self.repo,'review'),state,{'action':'fixture'})
            with self.assertRaises(HarnessError):workflow.transition(self.repo,'review','P00','publish')

    def test_package_excludes_machine_and_private_state(self):
        release=self.fake_release();(release/'runtimes').mkdir();(release/'runtimes/secret').write_text('runtime')
        (release/'data').mkdir();(release/'data/sessions.json').write_text('sessions')
        (release/'.env').write_text('secret')
        (release/'vendor/example/.git').mkdir(parents=True);(release/'vendor/example/.git/config').write_text('credential')
        (release/'vendor/example/LICENSE').write_text('license')
        write_json(release/'manifests/source-trees.json',{'example':{'files':{'LICENSE':{'kind':'file','sha256':sha(b'license')}}}})
        destination=self.base/'delivery.tar.gz'
        with patch.object(cli,'ROOT',release):cli.package(destination)
        import tarfile
        with tarfile.open(destination) as tar:files=tar.getnames()
        self.assertTrue(any(x.endswith('/vendor/example/LICENSE') for x in files))
        self.assertFalse(any('machine-binding' in x or '/.env' in x or '/data/' in x or '/runtimes/' in x or '/.git/' in x for x in files))

    def test_transaction_repeated_target_and_failure_restore(self):
        p=self.repo/'owned';p.write_text('initial')
        with self.assertRaisesRegex(ValueError,'fault'):
            with Transaction('repeat') as tx:
                tx.change(p,b'one');tx.change(p,b'two');raise ValueError('fault')
        self.assertEqual(p.read_text(),'initial')

    def test_all_private_paths_excluded_from_all_patch_kinds(self):
        secret_paths=['id_rsa','id_ed25519','credentials.json','services-binding.json','machine-binding.json','.env',
                      '.env.production','memory.db-wal','memory.sqlite-shm','private/session.txt','.test-projects/source.py']
        for n in secret_paths:
            p=self.repo/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('INITIAL_SENTINEL_PRIVATE')
        self.git('add','.');self.git('commit','-qm','private fixtures')
        for n in secret_paths:(self.repo/n).write_text('STAGED_SENTINEL_PRIVATE')
        self.git('add','.')
        for n in secret_paths:(self.repo/n).write_text('UNSTAGED_SENTINEL_PRIVATE')
        result=review.freeze(self.repo,'private',self.original);path=Path(result['path'])
        for p in path.rglob('*'):
            if p.is_file():self.assertNotIn(b'SENTINEL_PRIVATE',p.read_bytes(),str(p))
        self.assertFalse(any(p['path'] in secret_paths for p in read_json(path/'target.json')['files']))

    def test_freeze_excludes_preexisting_unrelated_owned_baseline(self):
        (self.repo/'source.py').write_text('user work')
        (self.repo/'unrelated.py').write_text('user untracked')
        with patch.object(workflow,'ROOT',self.fake_release()):workflow.create(self.repo,'task','L1','development')
        (self.repo/'ours.py').write_text('task work')
        result=review.freeze(self.repo,'task',self.original);path=Path(result['path']);target=read_json(path/'target.json')
        self.assertEqual(target['reviewable_paths'],['ours.py'])
        self.assertFalse((path/'source/source.py').exists());self.assertFalse((path/'source/unrelated.py').exists())
        (self.repo/'unrelated.py').write_text('later user work')
        self.assertFalse(review.review_status(self.repo,'task',result['snapshot_id'])['stale'])
        explicit=review.freeze(self.repo,'task',self.original,allowlist=['source.py'])
        self.assertEqual(read_json(Path(explicit['path'])/'target.json')['reviewable_paths'],['source.py'])
        with self.assertRaises(HarnessError):review.freeze(self.repo,'task',self.original,allowlist=['../outside'])
        with self.assertRaises(HarnessError):review.freeze(self.repo,'task',self.original,allowlist=['id_rsa'])

    def test_review_delete_recreate_rename_and_binary(self):
        self.git('rm','source.py');(self.repo/'source.py').write_text('replacement after staged deletion')
        (self.repo/'old.py').write_text('old');self.git('add','old.py');self.git('commit','-qm','delete then unrelated add')
        # Re-establish staged deletion/recreated untracked separately from rename.
        self.git('add','source.py');self.git('commit','-qm','replacement base')
        base=self.git('rev-parse','HEAD').strip();self.git('rm','source.py');(self.repo/'source.py').write_text('rebuilt')
        self.git('mv','old.py','renamed.py');(self.repo/'asset.bin').write_bytes(b'\0binary')
        result=review.freeze(self.repo,'odd',base);p=Path(result['path']);target=read_json(p/'target.json')
        self.assertEqual(set(target['reviewable_paths']),{'source.py','old.py','renamed.py','asset.bin'})
        self.assertEqual((p/'source/source.py').read_text(),'rebuilt')
        self.assertEqual(next(x for x in target['files'] if x['path']=='old.py')['kind'],'deleted')
        self.assertIn(b'deleted file mode',(p/'index.patch').read_bytes())
        self.assertIn('BINARY',next(x for x in target['files'] if x['path']=='asset.bin')['skip_reason'])

    def test_equivalent_sha_change_distinct_from_source_stale(self):
        (self.repo/'source.py').write_text('task source')
        result=review.freeze(self.repo,'equivalent',self.original);path=Path(result['path'])
        write_json(path/'review-results/main-review.json',{'files':[{'path':'source.py','status':'reviewed'}],'findings':[]})
        self.git('commit','-qam','same content new SHA')
        state=review.review_status(self.repo,'equivalent',result['snapshot_id'])
        self.assertEqual(state['status'],'CONTENT_EQUIVALENT_REVALIDATION_REQUIRED');self.assertFalse(state['stale'])
        (self.repo/'source.py').write_text('different source')
        self.assertEqual(review.review_status(self.repo,'equivalent',result['snapshot_id'])['status'],'STALE')

    def test_l2_requires_complete_actual_independent_process_receipt(self):
        release=self.fake_release()
        with patch.object(workflow,'ROOT',release):workflow.create(self.repo,'risk','L2','development')
        (self.repo/'source.py').write_text('risk change');(self.repo/'second.py').write_text('second')
        result=review.freeze(self.repo,'risk',self.original);path=Path(result['path'])
        files=[{'path':p,'status':'reviewed'} for p in read_json(path/'target.json')['reviewable_paths']]
        write_json(path/'review-results/main-review.json',{'files':files,'run_id':'main','findings':[]})
        write_json(path/'review-results/risk-review.json',{'files':files[:1],'run_id':'fake','independent_context':True})
        self.assertEqual(review.review_status(self.repo,'risk',result['snapshot_id'])['status'],'INCOMPLETE')
        import sys
        def command(records):return [sys.executable,'-c','import json;print(json.dumps('+repr({'files':records,'findings':[]})+'))']
        recorded=review.run_review(self.repo,'risk',result['snapshot_id'],'risk',command(files[:1]))
        self.assertEqual(recorded['review']['status'],'INCOMPLETE')
        recorded=review.run_review(self.repo,'risk',result['snapshot_id'],'risk',command(files))
        self.assertEqual(recorded['review']['status'],'COMPLETE_RECORDED')
        self.assertEqual(recorded['review']['independent_review'],'ACTUAL_PROCESS_RECORDED')
        report=path/'review-results/risk-review.json';doc=read_json(report);doc['run_id']='main';write_json(report,doc)
        self.assertEqual(review.review_status(self.repo,'risk',result['snapshot_id'])['status'],'INCOMPLETE')

    def test_doctor_detects_pwf_duplicate_context_hooks_and_mcp_urls(self):
        home=self.base/'codex';home.mkdir()
        (home/'config.toml').write_text('[plugins."planning-with-files@test"]\nenabled=true\n[mcp_servers.a]\nurl="http://localhost:8000/mcp"\n[mcp_servers.b]\nurl="http://localhost:8000/mcp/"\n[mcp_servers.c]\nurl="http://localhost:9000/mcp"\n')
        write_json(home/'hooks.json',{'SessionStart':[{'hooks':[{'command':'planning-with-files start'},{'command':'context-mode hook'}]}]})
        from harness_core.inventory import inspect_registrations
        groups=inspect_registrations()
        self.assertEqual(groups['HOOKS']['status'],'FAIL');self.assertEqual(groups['MCP']['status'],'FAIL')
        self.assertEqual(len(groups['MCP']['duplicates']),1)
        self.assertIn('PWF_PLUGIN_AND_STANDALONE_DUPLICATE',groups['HOOKS']['errors'])
        self.assertIn('CONTEXT_MODE_HOOKS_VIOLATE_MCP_ONLY_PROFILE',groups['HOOKS']['errors'])

    def test_url_identity_does_not_compare_missing_commands(self):
        release=self.fake_release();write_json(release/'machine-binding.json',{'codex_binary':'/bin/echo','mcp_servers':{'wanted':{'url':'http://localhost:9000/mcp'}}})
        with patch.object(cli,'ROOT',release):
            text=cli.mcp_fragment('[mcp_servers.other]\nurl="http://localhost:8000/mcp"\n')
            self.assertIn('wanted',text)
            with self.assertRaises(HarnessError):cli.mcp_fragment('[mcp_servers.other]\nurl="http://localhost:9000/mcp/"\n')

    def test_l2_hook_registered_pending_trust(self):
        release=self.fake_release();hook=release/'vendor/planning-with-files/hooks/codex-hooks.json';hook.parent.mkdir(parents=True,exist_ok=True)
        write_json(hook,{'hooks':{'SessionStart':[{'hooks':[{'command':'${PLUGIN_ROOT}/scripts/start'}]}]}})
        with patch.object(cli,'ROOT',release):
            result=cli.enable(self.repo,'L2');self.assertEqual(result['hooks'],'PENDING_TRUST')
            self.assertEqual(cli.doctor(self.repo)['categories']['HOOKS']['status'],'PENDING_TRUST')

    def test_install_launcher_idempotent_dispatch_uninstall_ownership(self):
        release=self.fake_release()
        with patch.object(cli,'ROOT',release):
            first=cli.install();self.assertEqual(cli.install()['status'],'ALREADY_INSTALLED')
            result=json.loads(subprocess.check_output([str(cli.launcher_path()),'status'],env=os.environ))
            self.assertEqual(result['accepted']['root'],str(release))
            cli.uninstall();self.assertFalse(cli.launcher_path().exists());self.assertFalse((self.machine/'accepted.json').exists())
            cli.install();cli.launcher_path().write_text('user launcher')
            with self.assertRaises(HarnessError):cli.uninstall()
            self.assertTrue((self.machine/'accepted.json').exists())

    def test_unowned_launcher_refused(self):
        release=self.fake_release();cli.launcher_path().parent.mkdir();cli.launcher_path().write_text('user')
        with patch.object(cli,'ROOT',release),self.assertRaises(HarnessError):cli.install()
        self.assertEqual(cli.launcher_path().read_text(),'user');self.assertFalse((self.machine/'accepted.json').exists())

    def acceptance_plan(self,report,failure=None):
        from harness_core.acceptance import REQUIRED
        import sys
        plan={'release_hash':report['release_hash'],'machine_binding_hash':report['machine_binding_hash'],
              'checks':[{'category':c,'command':[sys.executable,'-c','import sys;sys.exit('+('1' if c==failure else '0')+')']} for c in sorted(REQUIRED)]}
        path=self.base/'plan.json';write_json(path,plan);return path

    def test_update_candidate_isolated_require_hash_bound_actual_acceptance_and_promote(self):
        release=self.fake_release()
        with patch.object(cli,'ROOT',release):
            cli.install();before=(self.machine/'accepted.json').read_bytes();(release/'VERSION').write_text('3.1.0\n')
            report=cli.update(release);candidate=Path(report['candidate'])
            self.assertEqual((self.machine/'accepted.json').read_bytes(),before)
            with self.assertRaises(HarnessError):cli.update(candidate,promote=True)
            plan=self.acceptance_plan(report)
            accepted=cli.update(candidate,promote=True,acceptance_plan=plan)
            self.assertEqual(accepted['status'],'PROMOTED')
            self.assertEqual(read_json(self.machine/'accepted.json')['root'],str(candidate))
            runtime=json.loads(subprocess.check_output([str(cli.launcher_path()),'status'],env=os.environ))
            self.assertEqual(runtime['root'],str(candidate))
            cli.rollback(accepted['transaction_id']);self.assertEqual((self.machine/'accepted.json').read_bytes(),before)

    def test_update_acceptance_failures_do_not_touch_accepted(self):
        release=self.fake_release()
        with patch.object(cli,'ROOT',release):
            cli.install();before=(self.machine/'accepted.json').read_bytes()
            report=cli.update(release);candidate=Path(report['candidate'])
            for category in ['STATIC','MCP','RUNTIME','TRIGGER']:
                with self.subTest(category=category):
                    with self.assertRaises(HarnessError):cli.update(candidate,promote=True,acceptance_plan=self.acceptance_plan(report,category))
                    self.assertEqual((self.machine/'accepted.json').read_bytes(),before)
            plan=self.acceptance_plan(report);doc=read_json(plan);doc['release_hash']='wrong';write_json(plan,doc)
            with self.assertRaises(HarnessError):cli.update(candidate,acceptance_plan=plan)
            self.assertEqual((self.machine/'accepted.json').read_bytes(),before)

    def test_update_post_activation_failure_restores_accepted(self):
        from harness_core import acceptance
        release=self.fake_release()
        with patch.object(cli,'ROOT',release):
            cli.install();before=(self.machine/'accepted.json').read_bytes();report=cli.update(release)
            candidate=Path(report['candidate']);plan=self.acceptance_plan(report)
            real=acceptance.execute
            def failing(*args,**kwargs):
                if args[3]=='ACTIVATION':raise HarnessError('injected promote doctor failure')
                return real(*args,**kwargs)
            with patch.object(acceptance,'execute',failing),self.assertRaises(HarnessError):cli.update(candidate,True,plan)
            self.assertEqual((self.machine/'accepted.json').read_bytes(),before)
            self.assertEqual(read_json(candidate/'candidate-report.json')['promotion'],'ROLLED_BACK')

    def test_update_source_stale_acceptance_rejected(self):
        release=self.fake_release()
        with patch.object(cli,'ROOT',release):
            cli.install();report=cli.update(release);candidate=Path(report['candidate'])
            accepted=cli.update(candidate,acceptance_plan=self.acceptance_plan(report))
            (candidate/'skills/harness-dev/SKILL.md').write_text('new source after acceptance')
            with self.assertRaises(HarnessError):cli.update(candidate,True,acceptance=accepted['acceptance']['path'])
            self.assertEqual(read_json(self.machine/'accepted.json')['root'],str(release))

    def test_candidate_zero_tests_cannot_pass_acceptance(self):
        release=self.fake_release();shutil.rmtree(release/'tests');(release/'tests').mkdir()
        with patch.object(cli,'ROOT',release):
            report=cli.update(release);self.assertEqual(report['status'],'CANDIDATE_FAILED')

    def test_package_vendor_allowlist_private_candidate_and_symlink(self):
        release=self.fake_release();vendor=release/'vendor/example';vendor.mkdir(parents=True)
        (vendor/'file').write_text('allowed');(vendor/'untracked-session').write_text('private')
        (vendor/'link').symlink_to('file')
        write_json(release/'manifests/source-trees.json',{'example':{'files':{'file':{'kind':'file','sha256':sha(b'allowed')},'link':{'kind':'symlink','target':'file'}}}})
        for name in ['private','candidate-private','.test-projects']:
            p=release/name;p.mkdir();(p/'session.txt').write_text('PRIVATE_SENTINEL')
        destination=self.base/'package.tar.gz'
        with patch.object(cli,'ROOT',release):cli.package(destination)
        import tarfile
        with tarfile.open(destination) as tar:
            names=tar.getnames();self.assertFalse(any('untracked-session' in n or '/candidate-private/' in n or '/private/' in n or '/.test-projects/' in n for n in names))
            self.assertEqual(tar.getmember('codex-engineering-harness/vendor/example/link').linkname,'file')
        (vendor/'link').unlink();(vendor/'link').symlink_to('/etc/passwd')
        with patch.object(cli,'ROOT',release),self.assertRaises(HarnessError):cli.package(destination)

    def test_policy_only_skill_behavior_diff_and_mcp_migration(self):
        release=self.fake_release();p=release/'skills/harness-dev/agents/openai.yaml';p.parent.mkdir();p.write_text('allow_implicit_invocation: false')
        with patch.object(cli,'ROOT',release):
            cli.install();report=cli.update(release);candidate=Path(report['candidate'])
            (candidate/'skills/harness-dev/agents/openai.yaml').write_text('allow_implicit_invocation: true')
            binding=read_json(candidate/'machine-binding.json');binding['mcp_servers']['test']={'url':'http://localhost:9200/mcp'};write_json(candidate/'machine-binding.json',binding)
            changed=cli.update(candidate)
            self.assertIn('skills/harness-dev/agents/openai.yaml',[x['path'] for x in changed['skill_behavior_changes']])
            self.assertEqual(changed['permission_migration']['binding_changes'][0]['after']['url'],'http://localhost:9200/mcp')
            self.assertNotEqual(report['release_hash'],changed['release_hash'])

    def test_d00_only_routes_matching_intent_and_fresh_resume(self):
        release=self.fake_release()
        with patch.object(workflow,'ROOT',release):
            workflow.create(self.repo,'explain','L0','explanation');workflow.transition(self.repo,'explain','D00','classification')
            with self.assertRaises(HarnessError):workflow.transition(self.repo,'explain','A01','unrelated development')
            self.assertEqual(workflow.transition(self.repo,'explain','ZINFO','pure explanation')['node'],'ZINFO')
            workflow.create(self.repo,'resume','L1','development');workflow.pause(self.repo,'resume','U02','user paused')
            # Read journal with no previous in-memory task object; publication permission is not restored.
            resumed=workflow.resume(self.repo,'resume');self.assertEqual(resumed['node'],'U02')
            with self.assertRaises(HarnessError):workflow.transition(self.repo,'resume','P01','reuse old permission')

    def test_review_run_cli_parses_flags_and_actual_child_command(self):
        import sys
        (self.repo/'source.py').write_text('review cli change')
        result=review.freeze(self.repo,'cli-review',self.original)
        argv=[sys.executable,str(ROOT/'bin/harness'),'review-run',str(self.repo),'--task','cli-review','--snapshot',result['snapshot_id'],'--kind','risk','--',sys.executable,'-c','import json;print(json.dumps({"files":[{"path":"source.py","status":"reviewed"}],"findings":[]}))']
        called=subprocess.run(argv,capture_output=True,env=os.environ)
        self.assertEqual(called.returncode,0,called.stderr.decode()+called.stdout.decode())
        self.assertEqual(json.loads(called.stdout)['status'],'RECORDED_PROCESS_EVIDENCE')

    def test_package_standard_sha256sum_and_explicit_symlink_identity(self):
        release=self.fake_release();vendor=release/'vendor/example';vendor.mkdir(parents=True)
        (vendor/'README.md').write_text('actual target file bytes')
        (vendor/'AGENTS.md').symlink_to('README.md')
        write_json(release/'manifests/source-trees.json',{'example':{'files':{
            'README.md':{'kind':'file','sha256':sha(b'actual target file bytes')},
            'AGENTS.md':{'kind':'symlink','target':'README.md'}}}})
        (release/'reports').mkdir()
        write_json(release/'reports/acceptance-summary.json',{'status':'PASS_WITH_OPTIONAL_BLOCK','categories':{'runtime':'PASS','provider':'UNVERIFIED'}})
        (release/'reports/known-limitations.md').write_text('Provider generation remains UNVERIFIED.\n')
        (release/'reports/raw-runtime.json').write_text('PRIVATE_RUNTIME_SENTINEL')
        destination=self.base/'links.tar.gz'
        with patch.object(cli,'ROOT',release):cli.package(destination)
        import tarfile
        extracted=self.base/'extracted';extracted.mkdir()
        with tarfile.open(destination) as tar:tar.extractall(extracted,filter='data')
        root=extracted/'codex-engineering-harness'
        result=subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=root,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr.decode()+result.stdout.decode())
        self.assertNotIn('vendor/example/AGENTS.md',(root/'SHA256SUMS').read_text())
        self.assertIn('reports/acceptance-summary.json',(root/'SHA256SUMS').read_text())
        self.assertIn('reports/known-limitations.md',(root/'SHA256SUMS').read_text())
        self.assertFalse((root/'reports/raw-runtime.json').exists())
        self.assertEqual(read_json(root/'package-manifest.json')['acceptance_summary']['status'],'PASS_WITH_OPTIONAL_BLOCK')
        link=read_json(root/'package-manifest.json')['files']['vendor/example/AGENTS.md']
        self.assertEqual(link,{'kind':'symlink','target':'README.md','sha256_target':sha(b'README.md')})
        self.assertEqual(os.readlink(root/'vendor/example/AGENTS.md'),link['target'])

    def test_start_resolves_locked_ocr_in_new_process_after_user_path(self):
        import sys
        release=self.fake_release();tool_dir=release/'runtimes/node/node_modules/.bin';tool_dir.mkdir(parents=True)
        ocr=tool_dir/'ocr';ocr.write_text('#!/bin/sh\necho locked-ocr\n');ocr.chmod(0o755)
        codex=release/'fake-codex';codex.write_text('#!/usr/bin/env python3\nimport json,os,shutil\nprint(json.dumps({"ocr":shutil.which("ocr"),"cwd":os.getcwd(),"PATH":os.environ["PATH"]}))\n');codex.chmod(0o755)
        binding=read_json(release/'machine-binding.json');binding['codex_binary']=str(codex);write_json(release/'machine-binding.json',binding)
        parent_path=os.environ.get('PATH')
        with patch.object(cli,'ROOT',release):cli.enable(self.repo,'L1')
        env=dict(os.environ);env['PATH']='/usr/bin:/bin'
        process=subprocess.run([sys.executable,str(release/'bin/harness'),'start',str(self.repo)],capture_output=True,env=env)
        self.assertEqual(process.returncode,0,process.stderr.decode()+process.stdout.decode())
        result=json.loads(process.stdout)
        self.assertEqual(result['ocr'],str(ocr));self.assertEqual(result['cwd'],str(self.repo))
        self.assertTrue(result['PATH'].startswith('/usr/bin:/bin:'))
        self.assertEqual(os.environ.get('PATH'),parent_path)

    def test_start_desktop_mode_reports_project_ready_without_exec(self):
        import sys
        release=self.fake_release()
        binding=read_json(release/'machine-binding.json');binding['codex_launch_mode']='desktop';write_json(release/'machine-binding.json',binding)
        with patch.object(cli,'ROOT',release):cli.enable(self.repo,'L1')
        process=subprocess.run([sys.executable,str(release/'bin/harness'),'start',str(self.repo)],capture_output=True)
        self.assertEqual(process.returncode,0,process.stderr.decode()+process.stdout.decode())
        result=json.loads(process.stdout)
        self.assertEqual(result['status'],'DESKTOP_PROJECT_READY')
        self.assertEqual(result['repo'],str(self.repo))
        self.assertEqual(result['profile'],'L1')
        self.assertIn('primary folder',result['action'])
        self.assertEqual(result['automatic_gui_launch'],'NOT_ATTEMPTED_NO_STABLE_PUBLIC_DESKTOP_CLI')

    def test_init_new_project_preserves_files_and_creates_no_commit(self):
        release=self.fake_release();project=self.base/'new-project';project.mkdir()
        (project/'README.md').write_text('user content\n')
        with patch.object(cli,'ROOT',release):
            result=cli.initialize_project(project,'L1')
            self.assertEqual(result['git'],'INITIALIZED')
            self.assertEqual(cli.initialize_project(project,'L1')['status'],'ALREADY_ENABLED')
        self.assertEqual((project/'README.md').read_text(),'user content\n')
        self.assertTrue((project/'.git').is_dir())
        self.assertEqual(subprocess.run(['git','-C',str(project),'rev-parse','--verify','HEAD'],capture_output=True).returncode,128)
        self.assertEqual(subprocess.check_output(['git','-C',str(project),'remote']).strip(),b'')

    def test_init_subdirectory_uses_existing_root(self):
        release=self.fake_release();child=self.repo/'src';child.mkdir()
        with patch.object(cli,'ROOT',release):result=cli.initialize_project(child,'L1')
        self.assertEqual(result['repo'],str(self.repo))
        self.assertEqual(result['git'],'EXISTING')
        self.assertFalse((child/'.git').exists())
        self.assertEqual(self.git('rev-parse','HEAD').strip(),self.original)

    def test_init_refuses_home_and_nested_repositories(self):
        parent=self.base/'collection';parent.mkdir();child=parent/'existing';child.mkdir()
        subprocess.check_call(['git','-C',str(child),'init','-q'])
        with self.assertRaisesRegex(HarnessError,'contains a Git repository'):cli.initialize_project(parent,'L1')
        self.assertFalse((parent/'.git').exists())
        with patch('pathlib.Path.home',return_value=self.base),self.assertRaisesRegex(HarnessError,'home directory'):
            cli.initialize_project(self.base,'L1')
        self.assertFalse((self.base/'.git').exists())

    def test_init_preserves_bare_repository_and_failed_enablement(self):
        bare=self.base/'bare';subprocess.check_call(['git','init','--bare','-q',str(bare)])
        with self.assertRaisesRegex(HarnessError,'bare repository'):cli.initialize_project(bare,'L1')
        self.assertFalse((bare/'.git').exists())
        release=self.fake_release();project=self.base/'conflict';project.mkdir()
        (project/'harness.yaml').write_text('user configuration')
        with patch.object(cli,'ROOT',release),self.assertRaisesRegex(HarnessError,'Git was initialized and preserved'):
            cli.initialize_project(project,'L1')
        self.assertTrue((project/'.git').is_dir())
        self.assertEqual((project/'harness.yaml').read_text(),'user configuration')
        self.assertFalse((project/'AGENTS.md').exists())

    def test_cli_init_enable_and_start_default_to_current_project(self):
        import sys
        release=self.fake_release();project=self.base/'current-project';project.mkdir()
        process=subprocess.run([sys.executable,str(release/'bin/harness'),'init'],cwd=project,capture_output=True)
        self.assertEqual(process.returncode,0,process.stdout.decode()+process.stderr.decode())
        self.assertEqual(json.loads(process.stdout)['repo'],str(project))
        child=project/'src';child.mkdir()
        process=subprocess.run([sys.executable,str(release/'bin/harness'),'enable'],cwd=child,capture_output=True)
        self.assertEqual(json.loads(process.stdout)['status'],'ALREADY_ENABLED')
        process=subprocess.run([sys.executable,str(release/'bin/harness'),'start'],cwd=child,capture_output=True)
        self.assertEqual(process.returncode,0,process.stdout.decode()+process.stderr.decode())

    def test_start_missing_binding_explains_enable_command(self):
        with self.assertRaisesRegex(HarnessError,'harness init'):
            cli.project_binding(self.repo)

    def test_new_git_repository_supports_task_and_review_without_commit(self):
        from harness_core.common import repo_root
        project=self.base/'unborn';project.mkdir();subprocess.check_call(['git','-C',str(project),'init','-q'])
        self.assertEqual(repo_root(project),project)
        (project/'README.md').write_text('preexisting\n')
        state=workflow.create(project,'first-task','L1','development')
        (project/'new.py').write_text('answer=42\n')
        subprocess.check_call(['git','-C',str(project),'add','new.py'])
        result=review.freeze(project,'first-task',state['baseline_head'])
        target=read_json(Path(result['path'])/'target.json')
        self.assertEqual(target['reviewable_paths'],['new.py'])
        self.assertFalse(review.review_status(project,'first-task',result['snapshot_id'])['stale'])
        subprocess.check_call(['git','-C',str(project),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','first commit'])
        self.assertEqual(review.review_status(project,'first-task',result['snapshot_id'])['revision_evidence'],'SHA_OR_INDEX_CHANGED_CONTENT_EQUIVALENT')
        next_snapshot=review.freeze(project,'first-task',state['baseline_head'])
        self.assertEqual(read_json(Path(next_snapshot['path'])/'target.json')['reviewable_paths'],['new.py'])
        (project/'new.py').write_text('answer=43\n')
        self.assertTrue(review.review_status(project,'first-task',result['snapshot_id'])['stale'])

    def task_facts(self):
        return {'objective':'Implement a CSV validation rule','completed':['Acceptance identified'],
                'remaining':['Implement validation'],'blockers':[],'decisions':['Preserve input rows'],
                'next_action':'Write one failing integration test','spec_refs':['spec.md'],
                'tests':[{'command':['python3','-m','unittest'],'exit_code':0}],
                'review':{'status':'UNVERIFIED'},'workers':[]}

    def context_task(self):
        (self.repo/'spec.md').write_text('Approved CSV validation spec\n')
        workflow.create(self.repo,'ctx','L1','development')
        workflow.transition(self.repo,'ctx','D00','classify')
        workflow.transition(self.repo,'ctx','A01','development')

    def test_context_checkpoint_detects_code_spec_branch_and_workflow_drift(self):
        self.context_task();facts=self.task_facts()
        context.checkpoint(self.repo,'ctx',facts)
        self.assertEqual(context.verify(self.repo,'ctx')['status'],'RESUME_OK')
        (self.repo/'spec.md').write_text('Changed spec\n')
        self.assertIn('SPEC_REFS_CHANGED',context.verify(self.repo,'ctx')['conflicts'])
        context.checkpoint(self.repo,'ctx',facts)
        self.git('checkout','-qb','different-branch')
        self.assertIn('BRANCH_CHANGED',context.verify(self.repo,'ctx')['conflicts'])
        context.checkpoint(self.repo,'ctx',facts)
        workflow.transition(self.repo,'ctx','D01','verify repository')
        self.assertIn('WORKFLOW_CHANGED',context.verify(self.repo,'ctx')['conflicts'])

    def test_context_precompact_never_reuses_stale_facts_or_creates_remote_handoff(self):
        self.context_task();facts=self.task_facts()
        context.checkpoint(self.repo,'ctx',facts)
        self.assertEqual(context.checkpoint(self.repo,'ctx',reason='precompact')['status'],'CHECKPOINT_READY')
        (self.repo/'source.py').write_text('print(2)\n')
        self.assertEqual(context.checkpoint(self.repo,'ctx',reason='precompact')['status'],'CHECKPOINT_PARTIAL')
        self.assertIn('AGENT_SUMMARY_REQUIRED',context.verify(self.repo,'ctx')['conflicts'])
        self.assertFalse((self.repo/'.harness/task/ctx/powercontext-handoff.json').exists())

    def test_precompact_does_not_resign_tampered_checkpoint_facts(self):
        self.context_task();context.checkpoint(self.repo,'ctx',self.task_facts())
        path=self.repo/'.harness/task/ctx/context-checkpoint.json'
        doc=read_json(path);doc['facts']['next_action']='tampered stale instruction';write_json(path,doc)
        result=context.checkpoint(self.repo,'ctx',reason='precompact')
        self.assertEqual(result['status'],'CHECKPOINT_PARTIAL')
        clean=context.load_checkpoint(self.repo,'ctx')
        self.assertIsNone(clean['facts'])
        self.assertEqual(clean['facts_status'],'AGENT_SUMMARY_REQUIRED')

    def test_checkpoint_rejects_secret_invalid_result_and_symlink(self):
        self.context_task();facts=self.task_facts();facts['next_action']='OPENAI_API_KEY=TEST_SECRET_DO_NOT_USE_1234567890'
        with self.assertRaisesRegex(HarnessError,'credentials'):context.checkpoint(self.repo,'ctx',facts)
        facts=self.task_facts();facts['tests'][0]['exit_code']='passed'
        with self.assertRaises(HarnessError):context.checkpoint(self.repo,'ctx',facts)
        outside=self.base/'outside';outside.write_text('preserve')
        (self.repo/'.harness/task/ctx/context-checkpoint.json').symlink_to(outside)
        with self.assertRaisesRegex(HarnessError,'symlink'):context.checkpoint(self.repo,'ctx',self.task_facts())
        self.assertEqual(outside.read_text(),'preserve')

    def test_checkpoint_tampering_and_ambiguous_task_are_not_restored(self):
        self.context_task();context.checkpoint(self.repo,'ctx',self.task_facts(),session='session-1')
        workflow.create(self.repo,'other','L1','development')
        with self.assertRaises(HarnessError):context.choose_task(self.repo)
        self.assertEqual(context.choose_task(self.repo,session='session-1'),'ctx')
        p=self.repo/'.harness/task/ctx/context-checkpoint.json';doc=read_json(p);doc['resume_node']='T03';write_json(p,doc)
        with self.assertRaisesRegex(HarnessError,'modified'):context.verify(self.repo,'ctx')

    def test_guard_reports_only_supplied_usage_and_configured_thresholds(self):
        self.assertEqual(context.guard()['status'],'USAGE_UNKNOWN')
        for percent,status in [(69,'NORMAL'),(70,'WARNING_CONTEXT_HIGH'),(80,'CHECKPOINT_REQUIRED'),(90,'STOP_NEW_WORK')]:
            self.assertEqual(context.guard(percent)['status'],status)
        self.assertTrue(context.guard(75,2)['handoff_recommended'])
        for value in [-1,101,float('nan')]:
            with self.assertRaises(HarnessError):context.guard(value)

    def test_powercontext_binding_is_project_scoped_and_handoff_is_exact_and_idempotent(self):
        release=self.fake_release();write_json(release/'services-binding.json',{'service_urls':{'powercontext':'http://127.0.0.1:18080'}})
        self.context_task();context.checkpoint(self.repo,'ctx',self.task_facts())
        calls=[]
        def remote(url,path,payload=None):
            calls.append((path,payload))
            if path=='/health/ready':return {'status':'ready'}
            if path=='/v1/scopes':return {'scope_id':'scope-1'}
            if path.endswith('prepare-current'):return {'handoff':payload['handoff']}
            if path.endswith('/commit'):return {'reference':{'artifact_id':'real-artifact','revision':'real-revision'}}
            return {'status':'resolved','scope_id':'scope-1','selected_revision':{'artifact_id':'real-artifact','revision':'real-revision'}}
        with patch.object(cli,'ROOT',release),patch.object(memory,'request',side_effect=remote):
            first=context.handoff(self.repo,'ctx')
            context.checkpoint(self.repo,'ctx',self.task_facts())
            second=context.handoff(self.repo,'ctx')
            self.assertEqual(first['status'],'SYNCED');self.assertEqual(second['status'],'ALREADY_SYNCED')
            self.assertEqual(memory.recall(self.repo,'ctx')['status'],'RECALLED')
        self.assertEqual(sum(p=='/v1/handoff/commit' for p,_ in calls),1)
        self.assertEqual(calls[-1][1]['selection'],'exact')
        self.assertNotIn('source.py',json.dumps(calls))

    def test_memory_rejects_wrong_revision_and_changed_server_binding(self):
        release=self.fake_release();write_json(release/'services-binding.json',{'service_urls':{'powercontext':'http://127.0.0.1:18080'}})
        p=self.repo/'.harness/state/memory.json';write_json(p,{'repo':str(self.repo),'scope_id':'scope-1','url':'https://external.invalid'})
        with patch.object(cli,'ROOT',release),self.assertRaisesRegex(HarnessError,'loopback'):memory.bind(self.repo)
        write_json(p,{'repo':str(self.repo),'scope_id':'scope-1','url':'http://127.0.0.1:18080'})
        write_json(self.repo/'.harness/task/ctx/powercontext-handoff.json',{'repo':str(self.repo),'scope_id':'scope-1','url':'http://127.0.0.1:18080','revision':{'artifact_id':'handoff','revision':1}})
        with patch.object(cli,'ROOT',release),patch.object(memory,'request',return_value={'status':'resolved','scope_id':'scope-1','selected_revision':{'artifact_id':'handoff','revision':2}}),self.assertRaisesRegex(HarnessError,'conflicting'):
            memory.recall(self.repo,'ctx')

    def test_powercontext_failure_preserves_local_checkpoint_and_never_reads_login(self):
        self.context_task();context.checkpoint(self.repo,'ctx',self.task_facts())
        login=self.base/'codex/auth.json';login.parent.mkdir(exist_ok=True);login.write_text('invalid credential sentinel')
        with patch.object(memory,'bind',side_effect=HarnessError('service unavailable')):
            self.assertEqual(context.handoff(self.repo,'ctx')['status'],'UNSYNCED')
        self.assertEqual(context.verify(self.repo,'ctx')['status'],'RESUME_OK')
        facts=self.task_facts();facts['workers']=[{'id':'child-real','status':'running'}]
        context.checkpoint(self.repo,'ctx',facts)
        with self.assertRaisesRegex(HarnessError,'workers'):memory.handoff(self.repo,context.load_checkpoint(self.repo,'ctx'))

    def test_start_automatically_initializes_new_project_with_l2(self):
        import sys
        release=self.fake_release();project=self.base/'auto-start';project.mkdir()
        (project/'user.txt').write_text('original')
        process=subprocess.run([sys.executable,str(release/'bin/harness'),'start'],cwd=project,capture_output=True)
        self.assertEqual(process.returncode,0,process.stderr.decode())
        binding=read_json(project/'.harness/state/project.json')
        self.assertEqual(binding['profile'],'L2');self.assertEqual((project/'user.txt').read_text(),'original')
        events=read_json(project/'.codex/hooks.json')['hooks']
        self.assertIn('PostCompact',events)
        self.assertEqual(sum('context_hook.py' in h['command'] for g in events['PreCompact'] for h in g['hooks']),1)
        self.assertFalse((project/'.harness/state/memory.json').exists())

    def test_refresh_preserves_unmanaged_config_and_rolls_back_failed_enablement(self):
        release=self.fake_release()
        with patch.object(cli,'ROOT',release):
            cli.enable(self.repo,'L1')
            agents=self.repo/'AGENTS.md';agents.write_text(agents.read_text()+'User added rule\n')
            result=cli.refresh(self.repo)
            self.assertEqual(result['profile'],'L1');self.assertIn('User added rule',agents.read_text())
            before=agents.read_bytes()
            with patch.object(cli,'enable',side_effect=HarnessError('candidate fails')),self.assertRaises(HarnessError):cli.refresh(self.repo)
            self.assertEqual(agents.read_bytes(),before)
            self.assertEqual(cli.project_binding(self.repo)['profile'],'L1')

    def test_refresh_returned_transaction_restores_previous_binding(self):
        release=self.fake_release()
        with patch.object(cli,'ROOT',release):
            first=cli.enable(self.repo,'L1')
            paths=[self.repo/'AGENTS.md',self.repo/'.codex/config.toml',self.repo/'harness.lock',self.repo/'.harness/state/project.json']
            before={str(path):path.read_bytes() for path in paths}
            old_registry=json.loads(json.dumps(cli.registry()))
            result=cli.refresh(self.repo)
            self.assertTrue(result['transaction_id'].startswith('refresh-'))
            rolled=cli.rollback_operation(result['transaction_id'])
            self.assertEqual(rolled['status'],'ROLLED_BACK')
            self.assertEqual({str(path):path.read_bytes() for path in paths},before)
            self.assertEqual(cli.registry(),old_registry)
            self.assertEqual(cli.rollback_operation(result['transaction_id'])['status'],'ALREADY_ROLLED_BACK')

    def test_native_hook_status_fails_on_runtime_load_errors(self):
        release=self.fake_release();scripts=release/'scripts';scripts.mkdir(exist_ok=True)
        (scripts/'codex_inventory.py').write_text('fixture')
        def fake_run(argv,**kwargs):
            out=Path(argv[argv.index('--output')+1])
            write_json(out,{'hooks':{'data':[{'cwd':str(self.repo),'hooks':[{
                'source':'project','key':'project-hook','trustStatus':'trusted','enabled':True
            }],'warnings':[],'errors':['invalid hook definition']}]}})
            return subprocess.CompletedProcess(argv,0,b'',b'')
        with patch.object(cli,'ROOT',release),patch.object(cli,'run',side_effect=fake_run):
            result=cli.native_hook_status(self.repo,'/bin/echo')
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['native_discovery'],'FAIL')
        self.assertIn('invalid hook definition',result['errors'])

    def test_postcompact_without_task_continues_but_ambiguous_task_stops(self):
        import importlib.util
        path=ROOT/'scripts/context_hook.py'
        spec=importlib.util.spec_from_file_location('harness_context_hook_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        payload={'cwd':str(self.repo),'hook_event_name':'PostCompact','session_id':'session-test'}
        with patch.object(module.cli,'select_project',return_value=self.repo),patch.object(module.cli,'project_binding',return_value={}):
            with patch.object(module.context,'choose_task',side_effect=HarnessError('No active Harness task for this session')):
                self.assertTrue(module.handle(payload)['continue'])
            with patch.object(module.context,'choose_task',side_effect=HarnessError('Choose --task explicitly; multiple active Harness tasks exist for this session')):
                self.assertFalse(module.handle(payload)['continue'])

    def test_empty_tree_and_fingerprint_do_not_write_git_objects(self):
        from harness_core.common import empty_tree
        def objects():
            root=self.repo/'.git/objects'
            return {str(p.relative_to(root)):p.stat().st_size for p in root.rglob('*') if p.is_file()}
        before=objects()
        self.assertTrue(empty_tree(self.repo))
        review.fingerprint(self.repo,self.original)
        self.assertEqual(objects(),before)

if __name__=='__main__':unittest.main()
