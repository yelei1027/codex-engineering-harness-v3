"""Durable manual service intent, scoped to the exact prepared deployment."""
import threading
from pathlib import Path
from .common import HarnessError, read_json, write_json
from .transaction import safe_parent
from .sidecars import GROUPS


class ResourcePreferences:
    def __init__(self, root, identity):
        self.path = Path(root)/'.harness-install/resource-preferences.json'
        self.identity = identity
        self.guard = threading.RLock()

    def read(self):
        safe_parent(self.path)
        if self.path.is_symlink():
            raise HarnessError('Resource preferences are a symlink; preserve')
        value = read_json(self.path, {})
        if not value:
            return {group:False for group in GROUPS}
        if (value.get('schema_version') != 1 or value.get('identity') != self.identity or
                set(value.get('manual', {})) != set(GROUPS) or
                any(type(v) is not bool for v in value['manual'].values())):
            raise HarnessError('Resource preference ownership changed; preserve')
        return value['manual']

    def set_manual(self, group, enabled):
        if group not in GROUPS or type(enabled) is not bool:
            raise HarnessError('Invalid manual resource intent')
        with self.guard:
            manual = self.read()
            manual[group] = enabled
            write_json(self.path, {'schema_version':1, 'identity':self.identity, 'manual':manual})
