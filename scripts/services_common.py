"""Portable sidecar recipes and immutable source validation; no secrets here."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness_core.common import private_path

PINS = {
    'powercontext': '6b2f6e8a4c1aac59fca75662aca26d78748bfa29',
    'screenshot-to-code': 'd026163f586dfa8c5c10d28c36edd59a9d3b0e88',
}

def verify_source(name):
    source = ROOT / 'vendor' / name
    entry = json.loads((ROOT / 'manifests/source-trees.json').read_text())[name]
    if entry['commit'] != PINS[name]:
        raise RuntimeError(f'{name}: accepted source commit changed')
    if (source / '.git').exists():
        actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != PINS[name]:
            raise RuntimeError(f'{name}: Git commit differs from accepted pin')
    verified, omitted = 0, []
    for relative, expected in entry['files'].items():
        path = source / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise RuntimeError('Unsafe source manifest path')
        if not path.exists() and not path.is_symlink() and private_path(Path(relative)):
            omitted.append(relative)
            continue
        if expected['kind'] == 'symlink':
            if not path.is_symlink() or os.readlink(path) != expected['target']:
                raise RuntimeError(f'{name}: source symlink differs: {relative}')
            data = os.readlink(path).encode()
        else:
            if not path.is_file() or path.is_symlink():
                raise RuntimeError(f'{name}: source file missing or invalid: {relative}')
            data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise RuntimeError(f'{name}: source content differs: {relative}')
        verified += 1
    return {'commit': PINS[name], 'verified_files': verified, 'private_files_omitted': omitted,
            'verification': 'ACCEPTED_MANIFEST_FILE_HASHES'}

def prepare_runtime(name):
    from harness_core.sidecars import prepare_runtime as prepare
    return prepare(ROOT,name)


def write_service_binding():
    from harness_core.sidecars import write_binding
    return write_binding(ROOT)
