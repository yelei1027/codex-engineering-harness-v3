"""Public prerequisite checks must fail before any preparation or registration."""
import json
import unittest
import test_public_lifecycle as fixtures


class PreflightCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.release();self.fixture.env['PYTHONDONTWRITEBYTECODE']='1'

    def test_public_preflight_is_read_only_and_does_not_require_provider_credentials(self):
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        _,result=self.fixture.public(self.root,'preflight')
        self.assertEqual(result['status'],'PREFLIGHT_PASSED');self.assertEqual(result['changes'],'NONE')
        self.assertEqual(result['provider_keys'],'NOT_REQUIRED_FOR_CORE_PREPARATION')
        self.assertFalse(self.fixture.state.exists());self.assertFalse((self.root/'private').exists())
        self.assertEqual(before,{str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_missing_tool_blocks_before_registration_and_bootstrap(self):
        # Keep an executable on PATH whose invocation proves preparation was not reached.
        self.fixture.executable(self.fixture.tools/'uv','#!/bin/sh\nexit 8\n')
        self.fixture.executable(self.fixture.tools/'docker','#!/bin/sh\nexit 8\n')
        result,preflight=self.fixture.public(self.root,'preflight',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(preflight['status'],'PREFLIGHT_BLOCKED')
        result,error=self.fixture.public(self.root,'install','--prepare',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('预检未通过',error['error'])
        self.assertFalse(self.fixture.state.exists());self.assertFalse((self.root/'private').exists())

    def test_non_codex_binary_cannot_be_reported_as_a_usable_runtime(self):
        self.fixture.env['HARNESS_CODEX_BINARY']='/bin/echo'
        result,preflight=self.fixture.public(self.root,'preflight',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('codex_runtime',preflight['missing'])
        self.fixture.public(self.root,'install','--prepare',ok=False)
        self.assertFalse(self.fixture.state.exists());self.assertFalse((self.root/'private').exists())

    def test_inactive_user_session_reports_action_without_modifying_any_state(self):
        self.fixture.executable(self.fixture.tools/'systemctl','#!/bin/sh\nexit 3\n')
        result,preflight=self.fixture.public(self.root,'preflight',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('user_session',preflight['missing'])
        self.assertIn('用户会话',preflight['next_step']);self.assertFalse(self.fixture.state.exists())

    def test_service_access_denied_is_unverified_without_repairing_or_preparing(self):
        self.fixture.executable(self.fixture.tools/'systemctl',
            '#!/bin/sh\necho "Failed to connect to user scope bus: Operation not permitted" >&2\nexit 1\n')
        self.fixture.executable(self.fixture.tools/'docker',
            '#!/bin/sh\nif [ "$1" = info ]; then echo "permission denied while trying to connect to the docker API" >&2; exit 1; fi\nexit 0\n')
        result,preflight=self.fixture.public(self.root,'preflight',ok=False)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(preflight['status'],'PREFLIGHT_BLOCKED')
        self.assertEqual(preflight['missing'],[])
        self.assertEqual(preflight['unverified'],['user_session','docker_daemon'])
        for name in preflight['unverified']:
            check=next(c for c in preflight['checks'] if c['name']==name)
            self.assertEqual(check['status'],'UNVERIFIED')
            self.assertEqual(check['reason'],'ACCESS_DENIED')
            self.assertNotIn('启动',check['next_step'])
            self.assertNotIn('恢复',check['next_step'])
        self.assertIn('未核验',preflight['next_step'])
        _,error=self.fixture.public(self.root,'install','--prepare',ok=False)
        self.assertIn('user_session',error['error'])
        self.assertFalse(self.fixture.state.exists())
        self.assertFalse((self.root/'private').exists())

    def test_tool_permission_denial_is_not_an_installation_diagnosis(self):
        self.fixture.executable(self.fixture.tools/'uv',
            '#!/bin/sh\necho "Permission denied" >&2\nexit 126\n')
        result,preflight=self.fixture.public(self.root,'preflight',ok=False)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(preflight['unverified'],['uv_execution'])
        self.assertNotIn('uv_execution',preflight['missing'])
        self.assertFalse(self.fixture.state.exists())

    def test_node_version_permission_denial_does_not_recommend_installation(self):
        self.fixture.executable(self.fixture.tools/'node',
            '#!/bin/sh\necho "Operation not permitted" >&2\nexit 126\n')
        result,preflight=self.fixture.public(self.root,'preflight',ok=False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('node_compatibility',preflight['unverified'])
        self.assertNotIn('node_compatibility',preflight['missing'])
        check=next(c for c in preflight['checks'] if c['name']=='node_compatibility')
        self.assertEqual(check['reason'],'ACCESS_DENIED')
        self.assertNotIn('准备可用的 Node',check['next_step'])
        self.fixture.public(self.root,'install','--prepare',ok=False)
        self.assertFalse(self.fixture.state.exists())
        self.assertFalse((self.root/'private').exists())

    def test_codex_access_denial_does_not_recommend_installation(self):
        binary=self.fixture.tools/'codex-desktop-fixture'
        for mode in ['version','help','not_executable']:
            with self.subTest(mode=mode):
                self.fixture.executable(binary,
                    '#!/bin/sh\ncase "$1" in --version) '+
                    ('echo "Permission denied" >&2; exit 126' if mode=='version' else 'echo "codex-cli 0.160.0"')+
                    ';; --help) '+('echo "Operation not permitted" >&2; exit 126' if mode=='help' else 'echo "app-server exec"')+';; esac\n')
                if mode=='not_executable':binary.chmod(0o600)
                result,preflight=self.fixture.public(self.root,'preflight',ok=False)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('codex_runtime',preflight['unverified'])
                self.assertNotIn('codex_runtime',preflight['missing'])
                check=next(c for c in preflight['checks'] if c['name']=='codex_runtime')
                self.assertEqual(check['reason'],'ACCESS_DENIED')
                self.assertNotIn('安装 Codex Desktop',check['next_step'])
                self.assertFalse(self.fixture.state.exists())

    def test_explicit_runtime_is_used_instead_of_unrelated_path_binary(self):
        self.fixture.executable(self.fixture.tools/'codex','#!/bin/sh\nexit 9\n')
        _,preflight=self.fixture.public(self.root,'preflight')
        runtime=next(c for c in preflight['checks'] if c['name']=='codex_runtime')
        self.assertEqual(runtime['binary'],self.fixture.env['HARNESS_CODEX_BINARY']);self.assertEqual(runtime['version'],'0.160.0')

    def test_older_codex_is_blocked_by_authoritative_compatibility_floor(self):
        self.fixture.executable(self.fixture.tools/'codex-desktop-fixture','#!/bin/sh\ncase "$1" in --version) echo "codex-cli 0.114.0";; --help) echo "app-server exec";; esac\n')
        result,preflight=self.fixture.public(self.root,'preflight',ok=False)
        self.assertNotEqual(result.returncode,0)
        runtime=next(c for c in preflight['checks'] if c['name']=='codex_runtime')
        self.assertEqual(runtime['minimum_version'],'0.160.0');self.assertEqual(runtime['status'],'MISSING')
        self.assertFalse(self.fixture.state.exists())

    def test_incompatible_node_versions_block_before_any_preparation(self):
        for version in ['v18.20.0','v22.4.0','v25.0.0']:
            with self.subTest(version=version):
                self.fixture.executable(self.fixture.tools/'node','#!/bin/sh\necho "'+version+'"\n')
                result,preflight=self.fixture.public(self.root,'preflight',ok=False)
                self.assertNotEqual(result.returncode,0);self.assertIn('node_compatibility',preflight['missing'])
                self.fixture.public(self.root,'install','--prepare',ok=False)
                self.assertFalse(self.fixture.state.exists());self.assertFalse((self.root/'private').exists())

    def test_unusable_uv_is_not_reported_usable_merely_because_it_exists(self):
        self.fixture.executable(self.fixture.tools/'uv','#!/bin/sh\nexit 8\n')
        result,preflight=self.fixture.public(self.root,'preflight',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('uv_execution',preflight['missing'])
        self.assertFalse(self.fixture.state.exists())
