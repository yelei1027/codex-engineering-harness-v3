import os
import tempfile
import subprocess
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from harness_core.common import HarnessError
from harness_core.resource_systemd import unit_text, unit_name, verify, install


class ResourceSystemdCase(unittest.TestCase):
    def test_unit_has_no_shell_and_escapes_expansion(self):
        root=Path('/tmp/space name/$HOME/%h')
        with patch('harness_core.resource_systemd.sidecars.owner_state',return_value=Path('/tmp/state')):
            text=unit_text(root)
        self.assertIn('$$HOME/%%h',text)
        self.assertIn('KillMode=control-group',text)
        self.assertNotIn('/bin/sh',text)
        self.assertNotIn('docker.sock',text)
        self.assertNotEqual(unit_name(root),unit_name(Path('/tmp/other')))

    def test_existing_unowned_unit_prevents_any_systemd_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'root';root.mkdir();(root/'.harness-install').mkdir()
            config=Path(folder)/'config';units=config/'systemd/user';units.mkdir(parents=True)
            unit=units/unit_name(root);unit.write_text('unrelated user service')
            with patch.dict(os.environ,{'XDG_CONFIG_HOME':str(config)}), patch('harness_core.resource_systemd.systemctl') as call:
                with self.assertRaises(HarnessError):install(root)
                call.assert_not_called()
            self.assertEqual(unit.read_text(),'unrelated user service')


    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd validator required')
    def test_actual_systemd_accepts_generated_unit(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/unit_name(root);path.write_text(unit_text(root))
            result=subprocess.run(['systemd-analyze','--user','verify',str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_registered_unit_removal_deletes_only_owned_files(self):
        from harness_core.resource_systemd import remove,unit_path,receipt_path
        from harness_core.common import write_json
        from harness_core.transaction import image
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'root';root.mkdir();(root/'.harness-install').mkdir()
            state=Path(folder)/'state';config=Path(folder)/'config'
            with patch.dict(os.environ,{'HARNESS_STATE_DIR':str(state),'XDG_CONFIG_HOME':str(config)}):
                path=unit_path(root);path.parent.mkdir(parents=True);path.write_text('owned unit')
                unrelated=path.parent/'other.service';unrelated.write_text('preserve')
                write_json(receipt_path(root),{'root':str(root),'path':str(path),'image':image(path)})
                with patch('harness_core.resource_systemd.systemctl'):
                    self.assertEqual(remove(root)['status'],'REMOVED')
                self.assertFalse(path.exists());self.assertFalse(receipt_path(root).exists())
                self.assertEqual(unrelated.read_text(),'preserve')
