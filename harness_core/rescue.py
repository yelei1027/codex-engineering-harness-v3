"""Minimal owned recovery runtime survives partial source removal."""
import os
from pathlib import Path
from .common import HarnessError, atomic, canonical, sha, state_dir, write_json
from .transaction import safe_parent


def prepare(root,identifier):
    destination=state_dir()/'recovery-runtime'/identifier
    safe_parent(destination/'guard')
    if destination.exists():raise HarnessError('Recovery runtime already exists; preserve')
    destination.mkdir(parents=True,mode=0o700)
    files={}
    for source in sorted((Path(root)/'harness_core').glob('*.py')):
        if source.is_symlink():raise HarnessError('Recovery source is a symlink')
        name='harness_core/'+source.name;data=source.read_bytes()
        atomic(destination/name,data,0o600);files[name]=sha(data)
    entry=("import os,sys,json\n"
           "from pathlib import Path\n"
           "os.environ['HARNESS_STATE_DIR']="+repr(str(state_dir()))+"\n"
           "sys.dont_write_bytecode=True\n"
           "from harness_core.common import lock,state_dir,HarnessError\n"
           "from harness_core.lifecycle import recover_uninstall\n"
           "if any(a!='--apply' for a in sys.argv[1:]):sys.exit('Only --apply is accepted')\n"
           "try:\n"
           " with lock(state_dir()/'operation.lock'):\n"
           "  result=recover_uninstall("+repr(identifier)+",'--apply' in sys.argv[1:])\n"
           " print(json.dumps(result,ensure_ascii=False))\n"
           "except (HarnessError,OSError,ValueError) as error:\n"
           " print(json.dumps({'status':'ERROR','error':str(error)},ensure_ascii=False));sys.exit(2)\n").encode()
    atomic(destination/'entry.py',entry,0o600);files['entry.py']=sha(entry)
    write_json(destination/'receipt.json',{'operation_id':identifier,'files':files})
    return {'operation_id':identifier,'receipt_sha256':sha((destination/'receipt.json').read_bytes()),'directory':str(destination),'command':['python3',str(destination/'entry.py')],
            'scope':'File/config recovery only; no prior Hook trust or stopped services automatically restored'}


def launcher_fragment():
    # Verify this owned runtime before loading it, even when bin/harness vanished.
    return ("if len(sys.argv)>2 and sys.argv[1]=='uninstall-recover':\n"
        " import re,hashlib\n"
        " identifier=sys.argv[2]\n"
        " if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',identifier):sys.exit('Unsafe recovery ID')\n"
        " recovery=state/'recovery-runtime'/identifier\n"
        " if recovery.exists():\n"
        "  if recovery.is_symlink() or any(p.is_symlink() for p in recovery.parents):sys.exit('Recovery path symlink; preserve')\n"
        "  receipt=recovery/'receipt.json'\n"
        "  if receipt.is_symlink():sys.exit('Recovery receipt symlink; preserve')\n"
        "  record_path=state/'uninstalls'/(identifier+'.json')\n"
        "  if record_path.is_symlink():sys.exit('Recovery operation symlink; preserve')\n"
        "  record=json.loads(record_path.read_text());registration=record.get('recovery_runtime',{})\n"
        "  receipt_bytes=receipt.read_bytes();receipt_doc=json.loads(receipt_bytes)\n"
        "  if record.get('id')!=identifier or receipt_doc.get('operation_id')!=identifier or registration.get('operation_id')!=identifier or registration.get('directory')!=str(recovery) or registration.get('receipt_sha256')!=hashlib.sha256(receipt_bytes).hexdigest():sys.exit('Recovery operation identity differs; preserve')\n"
        "  files=receipt_doc['files']\n"
        "  if not isinstance(files,dict) or 'entry.py' not in files:sys.exit('Incomplete recovery receipt')\n"
        "  actual=set()\n"
        "  for p in recovery.rglob('*'):\n"
        "   if p.is_symlink():sys.exit('Unknown recovery link; preserve')\n"
        "   if p.is_file():actual.add(str(p.relative_to(recovery)))\n"
        "   elif not p.is_dir():sys.exit('Unknown recovery special file; preserve')\n"
        "  if actual!=set(files)|{'receipt.json'}:sys.exit('Unknown recovery files; preserve')\n"
        "  for name,digest in files.items():\n"
        "   relative=Path(name);p=recovery/relative\n"
        "   if relative.is_absolute() or '..' in relative.parts or p.is_symlink() or any(a.is_symlink() for a in p.parents) or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:sys.exit('Recovery runtime changed; preserve')\n"
        "  os.execv(sys.executable,[sys.executable,str(recovery/'entry.py'),*sys.argv[3:]])\n")


def cleanup(record):
    from .common import read_json
    import shutil
    directory=Path(record['directory']);safe_parent(directory/'guard')
    if not directory.exists():return 'ALREADY_REMOVED'
    if directory.is_symlink():return 'PRESERVED_CHANGED_RECOVERY_RUNTIME'
    receipt=directory/'receipt.json'
    if receipt.is_symlink():return 'PRESERVED_CHANGED_RECOVERY_RUNTIME'
    raw=receipt.read_bytes();doc=read_json(receipt,{})
    if doc.get('operation_id')!=record.get('operation_id') or sha(raw)!=record.get('receipt_sha256'):return 'PRESERVED_CHANGED_RECOVERY_RUNTIME'
    expected=doc.get('files',{})
    expected_directories={str(parent) for name in expected for parent in Path(name).parents if str(parent)!='.'}
    actual={}
    for path in directory.rglob('*'):
        if path.is_symlink():return 'PRESERVED_CHANGED_RECOVERY_RUNTIME'
        if path.is_dir():
            if str(path.relative_to(directory)) not in expected_directories:return 'PRESERVED_CHANGED_RECOVERY_RUNTIME'
            continue
        if not path.is_file():return 'PRESERVED_CHANGED_RECOVERY_RUNTIME'
        if path.is_file() and path!=receipt:actual[str(path.relative_to(directory))]=sha(path.read_bytes())
    if not expected or actual!=expected:return 'PRESERVED_CHANGED_RECOVERY_RUNTIME'
    shutil.rmtree(directory)
    return 'REMOVED_OWNED_RECOVERY_RUNTIME'
