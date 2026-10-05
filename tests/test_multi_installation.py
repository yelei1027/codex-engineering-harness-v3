"""Public update/uninstall across retained and accepted roots; isolated fixtures."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
import test_public_lifecycle as fixtures


class MultiInstallationCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.env['PYTHONDONTWRITEBYTECODE']='1'
        self.old=self.fixture.release('old')
        self.fixture.public(self.old,'install');self.fixture.public(self.old,'prepare',self.fixture.repo,'--apply','--profile','L1')
        self.new_source=self.fixture.release('new-source','3.2.0')
        (self.new_source/'tests').mkdir();(self.new_source/'tests/test_candidate.py').write_text('import unittest\nclass Fixture(unittest.TestCase):\n def test_prepared(self):self.assertTrue(True)\n')

    def promote(self):
        _,report=self.fixture.public(self.old,'update','--to',self.new_source)
        self.assertEqual(report['status'],'CANDIDATE_READY')
        self.new=Path(report['candidate'])
        plan=self.fixture.base/'acceptance.json'
        plan.write_text(json.dumps(self.fixture.candidate_plan(report)))
        _,promoted=self.fixture.public(self.old,'update','--to',self.new,'--promote','--acceptance-plan',plan)
        self.assertEqual(promoted['status'],'PROMOTED')
        return self.new

    def runtime(self,root,marker):
        program=root/'runtimes/test-component/program.py';program.parent.mkdir(parents=True,exist_ok=True);program.write_text('owned '+marker)
        database=program.parent/'memory.sqlite';database.write_bytes(marker.encode())
        private=root/'private/memory/value.txt';private.parent.mkdir(parents=True,exist_ok=True);private.write_text('private '+marker)
        code='from pathlib import Path;from harness_core.software import capture;capture(Path('+repr(str(root))+'),["runtimes/test-component"])'
        subprocess.run([sys.executable,'-c',code],cwd=root,env=self.fixture.env,check=True)
        return program,database,private

    def test_promotion_retains_old_root_and_uninstall_cleans_both_exact_owned_sets(self):
        old_program,old_db,old_private=self.runtime(self.old,'old')
        new=self.promote();new_program,new_db,new_private=self.runtime(new,'new')
        _,preview=self.fixture.public(new,'uninstall')
        self.assertEqual({r['root'] for r in preview['installations']},{str(self.old),str(new)})
        self.assertTrue(old_program.exists());self.assertTrue(new_program.exists())
        _,result=self.fixture.public(new,'uninstall','--apply')
        self.assertEqual(set(result['installations']),{str(self.old),str(new)})
        self.assertFalse(old_program.exists());self.assertFalse(new_program.exists())
        self.assertTrue((self.old/'bin/harness').exists()) # user source checkout
        self.assertFalse((new/'bin/harness').exists()) # copied candidate owned source
        self.assertFalse((self.fixture.repo/'.harness/state/project.json').exists())
        self.assertEqual((self.fixture.repo/'app.py').read_text(),'print("business")\n')
        for selected in result['preserved_data']['installations']:
            saved=Path(selected['local_install_data']);marker='old' if selected['root']==str(self.old) else 'new'
            self.assertEqual((saved/'private/memory/value.txt').read_text(),'private '+marker)
            self.assertEqual((saved/'runtime-data/runtimes/test-component/memory.sqlite').read_bytes(),marker.encode())
        catalogue=json.loads((self.fixture.state/'installations.json').read_text())
        self.assertTrue(all(r['status']=='REMOVED' for r in catalogue['roots'].values()))

    def test_unknown_old_dependency_blocks_every_root_before_detach(self):
        new=self.promote();old_unknown=self.old/'runtimes/legacy/component.txt'
        old_unknown.parent.mkdir(parents=True);old_unknown.write_text('unknown legacy software')
        new_program,_,_=self.runtime(new,'new')
        before=(self.fixture.state/'accepted.json').read_bytes()
        result,_=self.fixture.public(new,'uninstall','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertTrue(new_program.exists())
        self.assertEqual(old_unknown.read_text(),'unknown legacy software')
        self.assertEqual((self.fixture.state/'accepted.json').read_bytes(),before)
        self.assertTrue((self.fixture.repo/'.harness/state/project.json').exists())
        self.assertFalse((self.fixture.state/'uninstalls').exists())

    def test_changed_owned_recipe_in_old_root_blocks_shared_uninstall(self):
        import shutil
        shutil.copytree(fixtures.ROOT/'templates/services',self.old/'templates/services')
        code='from pathlib import Path;from harness_core.sidecars import prepare_runtime;prepare_runtime(Path('+repr(str(self.old))+'),"powercontext")'
        subprocess.run([sys.executable,'-c',code],cwd=self.old,env=self.fixture.env,check=True)
        self.fixture.executable(self.fixture.tools/'docker','#!/bin/sh\necho "error: no such object: fixture" >&2\nexit 1\n')
        # The old root's source hash changed by copied templates; promotion itself
        # remains independent, and uninstall must still protect changed recipes.
        new=self.promote();recipe=self.old/'runtimes/powercontext/compose.yaml'
        recipe.write_text(recipe.read_text()+'\n# user later change\n')
        result,_=self.fixture.public(new,'uninstall','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertTrue((new/'bin/harness').exists())
        self.assertTrue((self.fixture.repo/'.harness/state/project.json').exists())
        self.assertIn('user later change',recipe.read_text())

    def test_later_catalogue_change_is_preserved_before_resource_mutation(self):
        new=self.promote();target=self.fixture.state/'installations.json';doc=json.loads(target.read_text())
        doc['user_note']='keep later custom metadata';target.write_text(json.dumps(doc))
        before=target.read_bytes();result,_=self.fixture.public(new,'uninstall','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(target.read_bytes(),before)
        self.assertTrue((new/'bin/harness').exists());self.assertTrue((self.fixture.repo/'.harness/state/project.json').exists())

    def test_repeated_uninstall_keeps_generations_and_reinstall_finds_latest_data(self):
        self.fixture.public(self.old,'disable',self.fixture.repo)
        _,_,first_private=self.runtime(self.old,'first')
        _,first=self.fixture.public(self.old,'uninstall','--apply')
        first_saved=Path(first['preserved_data']['local_install_data'])
        self.fixture.public(self.old,'install');self.fixture.public(self.old,'recover-data','--apply')
        # Fresh software preparation is represented by the same owned test runtime.
        _,_,second_private=self.runtime(self.old,'second')
        _,second=self.fixture.public(self.old,'uninstall','--apply')
        second_saved=Path(second['preserved_data']['local_install_data'])
        self.assertNotEqual(first_saved,second_saved);self.assertTrue((first_saved/'recovery.json').exists())
        self.fixture.public(self.old,'install')
        _,restored=self.fixture.public(self.old,'recover-data','--apply')
        self.assertEqual(restored['status'],'DATA_RESTORED');self.assertEqual(second_private.read_text(),'private second')

    def test_unaccepted_prepared_candidate_is_also_removed_by_shared_uninstall(self):
        _,report=self.fixture.public(self.old,'update','--to',self.new_source)
        candidate=Path(report['candidate']);self.runtime(candidate,'pending')
        _,preview=self.fixture.public(self.old,'uninstall')
        self.assertEqual({r['root'] for r in preview['installations']},{str(self.old),str(candidate)})
        _,removed=self.fixture.public(self.old,'uninstall','--apply')
        self.assertIn(str(candidate),removed['installations']);self.assertFalse((candidate/'bin/harness').exists())
        self.assertTrue((self.new_source/'bin/harness').exists())

    def test_unregistered_runtime_process_prevents_all_root_cleanup(self):
        program,_,_=self.runtime(self.old,'live');new=self.promote()
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],cwd=program.parent)
        try:
            before=(self.fixture.state/'accepted.json').read_bytes()
            result,_=self.fixture.public(new,'uninstall','--apply',ok=False)
            self.assertNotEqual(result.returncode,0);self.assertTrue(program.exists())
            self.assertEqual((self.fixture.state/'accepted.json').read_bytes(),before)
            self.assertTrue((self.fixture.repo/'.harness/state/project.json').exists())
            self.assertIsNone(child.poll())
        finally:child.terminate();child.wait(timeout=10)

    def test_candidate_service_owner_is_stable_across_isolated_and_shared_state(self):
        _,report=self.fixture.public(self.old,'update','--to',self.new_source);candidate=Path(report['candidate'])
        import shutil
        # Copy recipes solely as a service fixture, not a source-ownership proof.
        shutil.copytree(fixtures.ROOT/'templates/services',candidate/'templates/services')
        code='from pathlib import Path;from harness_core.sidecars import prepare_runtime;prepare_runtime(Path('+repr(str(candidate))+'),"powercontext")'
        env={**self.fixture.env,'HARNESS_STATE_DIR':str(candidate/'candidate-private')}
        subprocess.run([sys.executable,'-c',code],cwd=candidate,env=env,check=True)
        script='from pathlib import Path;from harness_core.sidecars import layout;v=layout(Path('+repr(str(candidate))+'));assert v["state_id"]=='+repr(__import__('hashlib').sha256(str(self.fixture.state).encode()).hexdigest()[:16])
        subprocess.run([sys.executable,'-c',script],cwd=candidate,env=self.fixture.env,check=True)

    def test_failed_complete_install_before_acceptance_can_clean_owned_progress(self):
        root=self.fixture.release('failed-install')
        # Use a distinct shared state: this is a first-install fixture.
        self.fixture.env['HARNESS_STATE_DIR']=str(self.fixture.base/'failed-state')
        self.fixture.env['HARNESS_BIN_DIR']=str(self.fixture.base/'failed-bin')
        self.fixture.env['CODEX_HOME']=str(self.fixture.base/'failed-codex')
        self.fixture.env['FIXTURE_BOOTSTRAP_FAIL']='1'
        result,_=self.fixture.public(root,'install','--prepare',ok=False)
        self.assertNotEqual(result.returncode,0)
        state=Path(self.fixture.env['HARNESS_STATE_DIR']);self.assertFalse((state/'accepted.json').exists())
        _,preview=self.fixture.public(root,'uninstall');self.assertEqual(preview['status'],'UNINSTALL_PLANNED')
        _,removed=self.fixture.public(root,'uninstall','--apply');self.assertEqual(removed['status'],'UNINSTALLED')
        saved=Path(removed['preserved_data']['local_install_data'])
        self.assertEqual((saved/'private/prepared').read_text(),'fixture progress')

    def test_copied_candidate_allows_isolated_preparation_without_changing_shared_acceptance(self):
        _,report=self.fixture.public(self.old,'update','--to',self.new_source);candidate=Path(report['candidate'])
        before=(self.fixture.state/'accepted.json').read_bytes()
        env={**self.fixture.env,'HARNESS_STATE_DIR':str(candidate/'candidate-private'),'HARNESS_BIN_DIR':str(candidate/'candidate-private/bin'),
             'CODEX_HOME':str(candidate/'candidate-private/codex')}
        result=subprocess.run([sys.executable,str(candidate/'bin/harness'),'install','--prepare'],env=env,cwd=candidate,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.fixture.state/'accepted.json').read_bytes(),before)
        self.assertTrue((candidate/'candidate-private/accepted.json').is_file())

    def test_missing_catalogue_with_remaining_ownership_is_not_treated_as_empty(self):
        new=self.promote();target=self.fixture.state/'installations.json';target.unlink()
        owner=self.fixture.state/'installations-ownership.json';before=owner.read_bytes()
        result,_=self.fixture.public(new,'uninstall','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(owner.read_bytes(),before)
        self.assertTrue((new/'bin/harness').exists());self.assertTrue((self.fixture.repo/'.harness/state/project.json').exists())

    def test_archive_runtime_environment_and_derived_cache_are_retained_for_reinstall(self):
        fixture=fixtures.PublicLifecycleCase();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.env['PYTHONDONTWRITEBYTECODE']='1'
        fixture.bundle(fixture.release());installed=fixture.install_release();self.assertEqual(installed.returncode,0,installed.stderr)
        root=fixture.target;program=root/'runtimes/test-component/program.py';program.parent.mkdir(parents=True);program.write_text('owned dependency')
        environment=program.parent/'.env';environment.write_text('PRIVATE_SETTING=retained-value')
        code='from pathlib import Path;from harness_core.software import capture;capture(Path('+repr(str(root))+'),["runtimes/test-component"])'
        subprocess.run([sys.executable,'-c',code],cwd=root,env=fixture.env,check=True)
        import py_compile
        cache=Path(py_compile.compile(str(root/'harness_core/common.py'),doraise=True));before=cache.read_bytes()
        _,removed=fixture.public(root,'uninstall','--apply');saved=Path(removed['preserved_data']['local_install_data'])
        self.assertEqual((saved/'runtime-data/runtimes/test-component/.env').read_text(),'PRIVATE_SETTING=retained-value')
        self.assertEqual((saved/'source-cache'/cache.relative_to(root)).read_bytes(),before)
        self.assertFalse(environment.exists());self.assertFalse(cache.exists())
        retried=fixture.install_release();self.assertEqual(retried.returncode,0,retried.stdout+retried.stderr)
        fixture.public(root,'recover-data','--apply')
        self.assertEqual(environment.read_text(),'PRIVATE_SETTING=retained-value');self.assertFalse(cache.exists())

    def interrupt_after_candidate_entry_move(self):
        import signal
        script="import os,signal,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom harness_core import cli,movement\noriginal=movement.move\nentry=Path(sys.argv[1])/'bin/harness'\ndef interrupt(log,source,target,kind):\n result=original(log,source,target,kind)\n if Path(source)==entry:os.kill(os.getpid(),signal.SIGKILL)\n return result\nmovement.move=interrupt\nsys.argv=['harness','uninstall','--apply'];cli.main()\n"
        result=subprocess.run([sys.executable,'-c',script,str(self.new)],env=self.fixture.env,cwd=self.fixture.repo,capture_output=True,text=True)
        self.assertEqual(result.returncode,-signal.SIGKILL,result.stdout+result.stderr)
        return json.loads(next((self.fixture.state/'uninstalls').glob('*.json')).read_text())

    def rescue(self,record):
        return subprocess.run([sys.executable,str(self.fixture.base/'bin/harness'),'uninstall-recover',record['id'],'--apply'],env=self.fixture.env,cwd=self.fixture.repo,capture_output=True,text=True)

    def test_multiroot_interrupted_uninstall_restores_every_root(self):
        old_program,old_db,old_private=self.runtime(self.old,'old');self.promote();new_program,new_db,new_private=self.runtime(self.new,'new')
        before=(self.fixture.state/'accepted.json').read_bytes();project=(self.fixture.repo/'.harness/state/project.json').read_bytes()
        record=self.interrupt_after_candidate_entry_move()
        self.assertFalse(old_program.exists());self.assertFalse(new_private.exists());self.assertFalse((self.new/'bin/harness').exists())
        result=self.rescue(record);self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(old_program.read_text(),'owned old');self.assertEqual(old_db.read_bytes(),b'old')
        self.assertEqual(new_db.read_bytes(),b'new');self.assertEqual(new_private.read_text(),'private new')
        self.assertEqual((self.fixture.state/'accepted.json').read_bytes(),before)
        self.assertEqual((self.fixture.repo/'.harness/state/project.json').read_bytes(),project)
        self.assertTrue((self.new/'bin/harness').exists());self.assertEqual(self.rescue(record).returncode,0)

    def test_multiroot_restore_conflict_preserves_all_current_and_retained_files(self):
        old_program,_,_=self.runtime(self.old,'old');self.promote();self.runtime(self.new,'new')
        record=self.interrupt_after_candidate_entry_move();old_program.parent.mkdir(parents=True,exist_ok=True);old_program.write_text('user later replacement')
        result=self.rescue(record);self.assertNotEqual(result.returncode,0)
        self.assertEqual(old_program.read_text(),'user later replacement');self.assertFalse((self.new/'bin/harness').exists())
        self.assertFalse((self.fixture.repo/'.harness/state/project.json').exists())
        for root,selected in record['roots'].items():self.assertTrue((Path(selected['retained'])/'private/memory/value.txt').exists())
