#!/usr/bin/env python3
"""Internal systemd gateway process; no command or upstream supplied over HTTP."""
import signal
import sys
import threading
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness_core.resource_manager import serve
stop = threading.Event()
for event in (signal.SIGTERM, signal.SIGINT):
    signal.signal(event, lambda *_: stop.set())
serve(ROOT, stop)
