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
    recipes = ROOT / 'templates/services' / name
    runtime = ROOT / 'runtimes' / name
    runtime.mkdir(parents=True, exist_ok=True)
    for recipe in recipes.iterdir():
        if recipe.is_file() and recipe.name != 'env.example':
            shutil.copy2(recipe, runtime / recipe.name)
    environment = runtime / '.env'
    if not environment.exists():
        descriptor = os.open(environment, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            handle.write((recipes / 'env.example').read_text())
    return runtime

def write_service_binding():
    """Generate this machine's binding from its resolved Compose publish ports."""
    binding = {'private_machine_binding': True, 'mcp_servers': {},
               'component_commands': {}, 'service_urls': {}}
    for name in PINS:
        compose = ROOT/'runtimes'/name/'compose.yaml'
        if not compose.is_file():
            continue
        # Resolve Compose substitutions without logging env values or credentials.
        resolved = json.loads(subprocess.check_output([
            'docker', 'compose', '-p', 'codex-harness', '-f', str(compose),
            'config', '--format', 'json'], text=True))
        urls = {}
        for component, configuration in resolved['services'].items():
            for port in configuration.get('ports', []):
                if port.get('host_ip') not in ['127.0.0.1', '::1']:
                    raise RuntimeError(f'{component}: refusing a non-loopback machine binding')
                host = '[::1]' if port['host_ip'] == '::1' else '127.0.0.1'
                urls[component] = f'http://{host}:{port["published"]}'
        if name == 'powercontext':
            url = urls['powercontext']
            binding['service_urls']['powercontext'] = url
            binding['mcp_servers']['powercontext'] = {'url': url+'/mcp/'}
            binding['component_commands']['powercontext'] = [
                str(ROOT/'runtimes/powercontext/client-venv/bin/powercontext'),
                'doctor', '--server-url', url, '--json']
        else:
            binding['service_urls'].update({'screenshot_backend': urls['screenshot-backend'],
                                           'screenshot_frontend': urls['screenshot-frontend']})
            binding['component_commands']['screenshot-to-code'] = [
                sys.executable, str(ROOT/'scripts/services.py'), 'status', '--service', name]
    path = ROOT/'services-binding.json'
    # Atomic private replacement; preserve no previous machine paths.
    temporary = path.with_suffix('.json.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(json.dumps(binding, ensure_ascii=False, indent=2)+'\n')
    temporary.chmod(0o600)
    temporary.replace(path)
    return binding
