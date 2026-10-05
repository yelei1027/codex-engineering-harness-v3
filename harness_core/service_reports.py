"""Validate all local smoke outputs before any service call or copy."""
from pathlib import Path
import stat

from .common import HarnessError


REPORT_FILES = (
    'powercontext-smoke.json', 'powercontext-mcp.json',
    'powercontext-host-doctor.json', 'powercontext-container-doctor.json',
    'screenshot-preview.png', 'screenshot-smoke.json', 'services.json',
)


def validate_output(path, directory=False):
    path = Path(path).absolute()
    for parent in path.parents:
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise HarnessError(f'Refuse unsafe service output parent: {parent}')
    try:
        metadata = path.lstat()
        mode = metadata.st_mode
    except FileNotFoundError:
        return
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise HarnessError(f'Refuse unsafe service output: {path}')
    if not directory and metadata.st_nlink != 1:
        raise HarnessError(f'Refuse shared service output inode: {path}')
    if directory:
        for child in path.iterdir():
            validate_output(child, directory=child.is_dir() and not child.is_symlink())


def prepare_outputs(root):
    reports = Path(root)/'private/service-reports'
    fixture = Path(root)/'private/service-smoke/recall-fixture.json'
    # Validate the entire set first, including files copied from containers.
    validate_output(reports, directory=True)
    for name in REPORT_FILES:
        validate_output(reports/name)
    validate_output(reports/'document-previews', directory=True)
    validate_output(fixture)
    reports.mkdir(parents=True, exist_ok=True)
    fixture.parent.mkdir(parents=True, exist_ok=True)
    return reports, fixture
