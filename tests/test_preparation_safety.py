"""Public regressions for independently reproduced installer/lifecycle defects."""
import os
import shutil
import subprocess
import sys
import unittest
import time
import json
from pathlib import Path
import test_public_lifecycle as fixtures
ROOT=fixtures.ROOT

class PreparationSafetyCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.release=self.fixture.release()
        shutil.copyfile(ROOT/'scripts/bootstrap.py',self.release/'scripts/bootstrap.py')
        (self.release/'manifests/node').mkdir()
        (self.release/'manifests/node/package.json').write_text('{"name":"preparation-fixture","version":"1.0.0"}')
        (self.release/'manifests/node/package-lock.json').write_text('{"name":"preparation-fixture","version":"1.0.0","lockfileVersion":3,"packages":{"":{"name":"preparation-fixture","version":"1.0.0"}}}')
        self.fixture.executable(self.fixture.tools/'uv','#!/bin/sh\n[ \"$1\" = --version ] && exit 0\nexit 9\n')

    def npm_script(self,text):
        first,body=text.split('\n',1)
        if 'python' in first:
            probe='import sys\nif sys.argv[1:]==["--version"]: print("fixture npm"); sys.exit(0)\n'
        else:
            probe='[ "$1" = --version ] && { echo "fixture npm"; exit 0; }\n'
        self.fixture.executable(self.fixture.tools/'npm',first+'\n'+probe+body)

    def test_public_prepare_retry_preserves_owned_npm_failure_progress(self):
        counter=self.fixture.base/'npm-count'
        self.npm_script(f'''#!/usr/bin/env python3
from pathlib import Path
import sys
count=Path({str(counter)!r});n=int(count.read_text())+1 if count.exists() else 1;count.write_text(str(n))
Path('node_modules').mkdir(exist_ok=True);Path('node_modules/progress').write_text('owned progress')
sys.exit(9 if n==1 else 0)
''')
        first,first_reply=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(first.returncode,0)
        self.assertIn('Node 组件依赖（npm ci）',first_reply['error'])
        self.assertIn('下一步：运行 harness preflight',first_reply['error'])
        second,second_reply=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(second.returncode,0) # uv is intentionally unavailable.
        self.assertIn('MarkItDown Python 环境（uv venv）',second_reply['error'])
        self.assertIn('Python 下载源',second_reply['error'])
        self.assertEqual(counter.read_text(),'2')
        self.assertEqual((self.release/'runtimes/node/node_modules/progress').read_text(),'owned progress')

    def test_public_failure_guidance_does_not_echo_dependency_stderr(self):
        self.npm_script('#!/bin/sh\necho "SENSITIVE_FIXTURE_VALUE" >&2\nexit 9\n')
        result,reply=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Node 组件依赖（npm ci）',reply['error'])
        self.assertIn('harness install --prepare',reply['error'])
        self.assertNotIn('SENSITIVE_FIXTURE_VALUE',result.stdout+result.stderr)
        logs=list((self.release/'private/install-logs').glob('*.log'));self.assertEqual(len(logs),1)
        self.assertIn('SENSITIVE_FIXTURE_VALUE',logs[0].read_text())
        self.assertEqual(logs[0].stat().st_mode & 0o777,0o600)

    def test_public_failure_refuses_symlink_install_logs_before_dependency_execution(self):
        outside=self.fixture.base/'outside';outside.mkdir();victim=outside/'user-data';victim.write_text('USER ORIGINAL')
        private=self.release/'private';private.mkdir();(private/'install-logs').symlink_to(outside)
        self.npm_script('#!/bin/sh\nexit 99\n')
        result,reply=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('unsafe service output parent',reply['error'])
        self.assertEqual(victim.read_text(),'USER ORIGINAL');self.assertEqual(list(outside.iterdir()),[victim])

    def test_public_failure_unknown_diagnostics_are_preserved_not_trusted_or_echoed(self):
        self.npm_script('''#!/usr/bin/env python3
import json,os
from pathlib import Path
path=Path('..').resolve().parent/'private/install-logs'/(os.environ['HARNESS_PREPARATION_ID']+'.status.json')
data=json.loads(path.read_text());data['stage']=['SENSITIVE_FIXTURE_VALUE'];path.write_text(json.dumps(data))
''')
        result,reply=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('准备阶段无法核验',reply['error'])
        self.assertNotIn('SENSITIVE_FIXTURE_VALUE',result.stdout+result.stderr)
        statuses=list((self.release/'private/install-logs').glob('*.status.json'))
        self.assertEqual(len(statuses),1)
        self.assertEqual(json.loads(statuses[0].read_text())['stage'],['SENSITIVE_FIXTURE_VALUE'])
        self.assertFalse((self.fixture.state/'accepted.json').exists())

    def test_real_offline_npm_rebuild_keeps_retained_sqlite_bytes(self):
        self.fixture.executable(self.fixture.tools/'node','#!/bin/sh\nexec '+shutil.which('node')+' \"$@\"\n')
        npm=shutil.which('npm');self.npm_script('#!/bin/sh\nexec '+npm+' "$@" --offline\n')
        database=self.release/'runtimes/node/node_modules/local-tool/memory.sqlite'
        database.parent.mkdir(parents=True);database.write_bytes(b'retained sqlite bytes')
        script='from pathlib import Path;from harness_core.software import capture;capture(Path('+repr(str(self.release))+'),["runtimes/node"])'
        subprocess.run([sys.executable,'-c',script],cwd=self.release,env=self.fixture.env,check=True)
        result,_=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(result.returncode,0) # npm succeeded; uv fails after it.
        self.assertEqual(database.read_bytes(),b'retained sqlite bytes')

    def test_real_offline_uv_existing_owned_environment_retries_and_keeps_data(self):
        real_uv=shutil.which('uv')
        if not real_uv:self.skipTest('Real uv is unavailable')
        # Use the installed interpreter offline: this tests actual uv directory
        # preservation, not the separate pinned3.12 download/version contract.
        self.fixture.executable(self.fixture.tools/'uv',f'''#!/usr/bin/env python3
import subprocess,sys
args=sys.argv[1:]
if '--python' in args and args[args.index('--python')+1]=='3.12':args[args.index('--python')+1]=sys.executable
raise SystemExit(subprocess.call([{real_uv!r},'--offline',*args]))
''')
        self.npm_script('#!/bin/sh\nmkdir -p node_modules\necho owned > node_modules/fixture\n')
        first,reply=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(first.returncode,0);self.assertIn('Python 依赖',reply['error']) # Deliberately absent requirements lock.
        database=self.release/'runtimes/markitdown/memory.sqlite';database.write_bytes(b'owned environment retained data')
        second,reply=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(second.returncode,0);self.assertIn('Python 依赖',reply['error'])
        self.assertEqual(database.read_bytes(),b'owned environment retained data')
        self.assertTrue((self.release/'runtimes/markitdown/bin/python').is_file())

    def test_service_recovery_does_not_write_through_unknown_parent_symlink(self):
        shutil.copytree(ROOT/'templates/services',self.release/'templates/services')
        script='from pathlib import Path;from harness_core.sidecars import prepare_runtime;prepare_runtime(Path('+repr(str(self.release))+'),"powercontext");prepare_runtime(Path('+repr(str(self.release))+'),"screenshot-to-code")'
        subprocess.run([sys.executable,'-c',script],cwd=self.release,env=self.fixture.env,check=True)
        # No image/container exists. Fake Docker returns explicit missing objects.
        self.fixture.executable(self.fixture.tools/'docker','#!/bin/sh\nif [ "$1" = inspect ] || [ "$2" = inspect ]; then echo "error: no such object: fixture" >&2; exit 1; fi\nexit 0\n')
        self.fixture.public(self.release,'install');self.fixture.public(self.release,'uninstall','--apply')
        outside=self.fixture.base/'outside';outside.mkdir()
        (self.release/'.harness-install').symlink_to(outside)
        result,_=self.fixture.public(self.release,'recover-data','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertFalse((outside/'services.json').exists())
        self.assertTrue((self.release/'.harness-install').is_symlink())

    def test_killed_package_preparation_restores_staged_data_on_retry(self):
        database=self.release/'runtimes/node/node_modules/local-tool/memory.sqlite'
        database.parent.mkdir(parents=True);database.write_bytes(b'kill-safe memory')
        script='from pathlib import Path;from harness_core.software import capture;capture(Path('+repr(str(self.release))+'),["runtimes/node"])'
        subprocess.run([sys.executable,'-c',script],cwd=self.release,env=self.fixture.env,check=True)
        counter=self.fixture.base/'kill-once'
        self.npm_script(f"""#!/usr/bin/env python3
from pathlib import Path
import os,signal
once=Path({str(counter)!r})
if not once.exists():
 once.write_text('killed');os.kill(os.getppid(),signal.SIGKILL)
""")
        first,_=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(first.returncode,0);self.assertFalse(database.exists())
        second,_=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(second.returncode,0) # later uv fails deliberately
        self.assertEqual(database.read_bytes(),b'kill-safe memory')

    def test_enable_refuses_service_build_lock_before_overlay_mutation(self):
        self.fixture.public(self.release,'install')
        script='from pathlib import Path;import subprocess,sys;from harness_core.common import lock;root=Path('+repr(str(self.release))+');repo=Path('+repr(str(self.fixture.repo))+');\nwith lock(root/".harness-install/services.lock"):\n r=subprocess.run([sys.executable,str(root/"bin/harness"),"prepare",str(repo),"--apply","--profile","L1"],capture_output=True)\n assert r.returncode==2 and b"holds" in r.stdout\n assert not (repo/".harness/state/project.json").exists()'
        subprocess.run([sys.executable,'-c',script],cwd=self.release,env=self.fixture.env,check=True)

    def owned_database(self):
        database=self.release/'runtimes/node/node_modules/local-tool/memory.sqlite'
        database.parent.mkdir(parents=True);database.write_bytes(b'private database')
        subprocess.run([sys.executable,'-c','from pathlib import Path;from harness_core.software import capture;capture(Path('+repr(str(self.release))+'),["runtimes/node"])'],cwd=self.release,env=self.fixture.env,check=True)
        return database

    def test_crash_retry_preserves_later_unknown_software_without_rebuild(self):
        database=self.owned_database()
        self.npm_script("#!/usr/bin/env python3\nimport os,signal\nos.kill(os.getppid(),signal.SIGKILL)\n")
        self.fixture.public(self.release,'install','--prepare',ok=False)
        unknown=database.parent/'user-new.txt';unknown.write_text('user added after crash')
        marker=self.fixture.base/'second-npm'
        real=shutil.which('npm')
        self.npm_script('#!/bin/sh\ntouch '+str(marker)+'\nexec '+real+' "$@" --offline\n')
        result,_=self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertFalse(marker.exists())
        self.assertEqual(unknown.read_text(),'user added after crash')
        self.assertEqual(database.read_bytes(),b'private database')
        self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertFalse(marker.exists());self.assertEqual(unknown.read_text(),'user added after crash')

    def test_orphan_package_writer_blocks_recovery_until_group_exits(self):
        database=self.owned_database();gate=self.fixture.base/'exit-gate';pidfile=self.fixture.base/'writer-pid'
        self.npm_script(f"""#!/usr/bin/env python3
import os,signal,time
from pathlib import Path
Path({str(pidfile)!r}).write_text(str(os.getpid()))
os.kill(os.getppid(),signal.SIGKILL)
while not Path({str(gate)!r}).exists():time.sleep(.02)
""")
        try:
            self.fixture.public(self.release,'install','--prepare',ok=False)
            self.assertTrue(pidfile.exists());self.assertFalse(database.exists())
            marker=self.fixture.base/'second-writer'
            self.npm_script('#!/bin/sh\ntouch '+str(marker)+'\nexit 0\n')
            result,_=self.fixture.public(self.release,'install','--prepare',ok=False)
            self.assertNotEqual(result.returncode,0);self.assertFalse(marker.exists());self.assertFalse(database.exists())
        finally:
            gate.touch()
            if pidfile.exists():
                pid=int(pidfile.read_text())
                for _ in range(200):
                    try:
                        raw=Path('/proc',str(pid),'stat').read_text()
                        if raw[raw.rfind(')')+2:].split()[0]=='Z':break
                    except FileNotFoundError:break
                    time.sleep(.01)
                else:self.fail('Isolated package writer did not stop')
        self.fixture.public(self.release,'install','--prepare',ok=False)
        self.assertTrue(marker.exists());self.assertEqual(database.read_bytes(),b'private database')
