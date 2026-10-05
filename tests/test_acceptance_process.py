"""Acceptance budgets and actual descendant cleanup."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from harness_core import acceptance
from harness_core.common import HarnessError


class AcceptanceProcessTests(unittest.TestCase):
    def test_internal_suite_budget_is_separate_from_external_static(self):
        command=[sys.executable,'-m','unittest','discover','-s','tests']
        for internal,expected in ((True,600),(False,120)):
            process=Mock(pid=123,returncode=0)
            process.communicate.return_value=(b'',b'Ran 1 test')
            with patch.object(acceptance.subprocess,'Popen',return_value=process):
                acceptance.execute(command,Path.cwd(),os.environ.copy(),'STATIC',600,internal_static=internal)
            process.communicate.assert_called_once_with(timeout=expected)
        with self.assertRaises(HarnessError):
            acceptance.execute([sys.executable,'-c','pass'],Path.cwd(),os.environ.copy(),'STATIC',600,internal_static=True)

    def test_timeout_kills_actual_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file=Path(directory)/'child.pid'
            script="import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
            started=time.monotonic()
            with self.assertRaisesRegex(HarnessError,'timed out: STATIC'):
                acceptance.execute([sys.executable,'-c',script,str(pid_file)],directory,os.environ.copy(),'STATIC',1)
            self.assertLess(time.monotonic()-started,5)
            pid=int(pid_file.read_text())
            def alive():
                try:return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[0]!='Z'
                except (FileNotFoundError, ProcessLookupError):return False
            deadline=time.monotonic()+2
            while alive() and time.monotonic()<deadline:time.sleep(.02)
            self.assertFalse(alive())

    def test_invalid_timeout_rejected_before_launch(self):
        for timeout in (0,-1,None,'bad',float('inf')):
            with self.subTest(timeout=timeout),self.assertRaises(HarnessError):
                acceptance.execute([sys.executable,'-c','pass'],Path.cwd(),os.environ.copy(),'STATIC',timeout)
