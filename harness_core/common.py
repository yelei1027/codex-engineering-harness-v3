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

def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()+b'\n'

def write_json(path, value):
    atomic(path,json_bytes(value))

def state_dir():
    return Path(os.environ.get('HARNESS_STATE_DIR', str(Path.home()/'.local/share/codex-engineering-harness'))).absolute()

@contextlib.contextmanager
def lock(path,shared=False):
    path=Path(path)
    if path.name in {'preparation.lock','services.lock'} and path.parent.name=='.harness-install':
        # Locks must survive moving/removing the installation's own metadata.
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise HarnessError('Lifecycle lock path has a symlink')
        from .sidecars import owner_state
        path=owner_state(path.parent.parent)/'root-locks'/sha(str(path.parent.parent).encode())[:16]/path.name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise HarnessError('Lifecycle state lock path has a symlink')
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as stream:
        try:
            fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
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

def empty_tree(repo):
    # Git can address the canonical empty tree without persisting a loose object.
    # Fingerprinting/review discovery must stay observational in committed repos.
    return git(repo, 'hash-object', '-t', 'tree', os.devnull).decode().strip()

def git_head(repo):
    result = run(['git', '-C', repo, 'rev-parse', '--verify', 'HEAD'], ok=False)
    if result.returncode == 0:
        return result.stdout.decode().strip()
    # An unborn branch has no commit; use an empty tree without creating one.
    branch = git(repo, 'symbolic-ref', 'HEAD').decode().strip()
    exists = run(['git', '-C', repo, 'show-ref', '--verify', '--quiet', branch], ok=False)
    if exists.returncode != 1:
        raise HarnessError('Cannot resolve Git HEAD; inspect repository metadata')
    return empty_tree(repo)

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

PWF_CODEX_HOOK_FILES=frozenset(['codex_hook_adapter.py', 'context_frame.py', 'permission_request.py', 'plugin_dispatch.py', 'post-tool-use.sh', 'post_tool_use.py', 'pre-compact.sh', 'pre-tool-use.sh', 'pre_tool_use.py', 'pwf-hook.cmd', 'resolve-plan-dir.sh', 'run_sh.py', 'session-start.sh', 'stop.py', 'stop.sh', 'user-prompt-submit.sh'])
PWF_CODEX_SKILL_FILES=frozenset(['SKILL.md', 'references/examples.md', 'references/reference.md', 'scripts/attest-plan.ps1', 'scripts/attest-plan.sh', 'scripts/check-complete.ps1', 'scripts/check-complete.sh', 'scripts/gate-stop.sh', 'scripts/init-session.ps1', 'scripts/init-session.sh', 'scripts/inject-plan.py', 'scripts/inject-plan.sh', 'scripts/ledger-summary.sh', 'scripts/plan-doctor.sh', 'scripts/resolve-plan-dir.ps1', 'scripts/resolve-plan-dir.sh', 'scripts/session-catchup.py', 'scripts/set-active-plan.ps1', 'scripts/set-active-plan.sh', 'scripts/skill-hook.sh', 'templates/analytics_findings.md', 'templates/analytics_task_plan.md', 'templates/findings.md', 'templates/loop.md', 'templates/progress.md', 'templates/task_plan.md'])
PWF_CODEX_RUNTIME_FILES=frozenset({".codex/hooks/"+n for n in PWF_CODEX_HOOK_FILES} | {".codex/skills/planning-with-files/"+n for n in PWF_CODEX_SKILL_FILES})

def distribution_path(path):
    """Positive release boundary; planning remains available to local review."""
    path=Path(path);parts=path.parts
    if not parts or private_path(path) or any(p in {'.planning','sessions'} for p in parts):return False
    if '.codex' in parts:
        # Only this pinned dependency's exact runtime files and their directory
        # ancestors are distributable; user configuration and unknown code are not.
        if parts[:2]!=('vendor','planning-with-files'):return False
        relative='/'.join(parts[2:])
        return relative in PWF_CODEX_RUNTIME_FILES or any(name.startswith(relative+'/') for name in PWF_CODEX_RUNTIME_FILES)
    directories={'harness_core','bin','skills','plugins','manifests','templates','scripts','tests','docs','third-party-licenses','vendor'}
    files={'VERSION','harness.lock','plugin.json','LICENSE','THIRD_PARTY_NOTICES','SECURITY.md','AGENTS.md','README.md','INSTALLATION.md','.gitignore'}
    if parts[0]=='reports':return str(path) in {'reports/acceptance-summary.json','reports/known-limitations.md'}
    if parts[0]=='contracts':return len(parts)==1 or str(path)=='contracts/workflow.json'
    if parts[0] in directories:return True
    if len(parts)==1 and parts[0] in files:return True
    return str(path) in {'.agents','.agents/plugins','.agents/plugins/marketplace.json',
                         '.codex-plugin','.codex-plugin/plugin.json'}
