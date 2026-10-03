import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class HarnessError(Exception):
    pass

def now():
    return datetime.now(timezone.utc).isoformat()

def new_id(prefix):
    return f'{prefix}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:12]}'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()

def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default

def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.harness-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def write_json(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b'\n')

def state_dir():
    return Path(os.environ.get('HARNESS_STATE_DIR', str(Path.home()/'.local/share/codex-engineering-harness'))).absolute()

@contextlib.contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HarnessError(f'Another Harness operation holds {path}')
        yield

def run(argv, cwd=None, timeout=60, ok=True):
    p = subprocess.run([str(a) for a in argv], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if ok and p.returncode:
        raise HarnessError(f'{argv[0]} exited {p.returncode}: {p.stderr.decode(errors="replace")[:1500]}')
    return p

def git(repo, *args):
    return run(['git', '-C', repo, *args]).stdout

def repo_root(value):
    path = Path(value).expanduser().resolve()
    root = Path(git(path, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if root != path:
        raise HarnessError(f'Use the verified Git root: {root}')
    return root

def safe_id(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,120}', value):
        raise HarnessError('Invalid ID; use letters, digits, dot, hyphen, underscore')
    return value

def private_path(path):
    parts = Path(path).parts
    name = Path(path).name.lower()
    return (any(p in {'.git', 'node_modules', '__pycache__', '.venv', '.harness', '.harness-artifacts', '.codegraph', '.harness-install', 'dist', 'cache', 'runtimes', 'private', '.test-projects', 'candidate-private'} for p in parts)
        or name in {'machine-binding.json', 'services-binding.json', 'auth.json', 'credentials.json', 'id_rsa', 'id_ed25519'}
        or name == '.env' or name.startswith('.env.') or name.endswith(('.db', '.sqlite', '.sqlite3', '.pem', '.key', '.pyc', '.db-wal', '.db-shm', '.sqlite-wal', '.sqlite-shm', '.sqlite3-wal', '.sqlite3-shm')))
