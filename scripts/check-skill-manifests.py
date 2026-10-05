#!/usr/bin/env python3
"""Check source/mirror/policy identity; --refresh updates only owned metadata copies."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def policy(path):
    if not path.exists():return True
    match=re.search(r'(?m)^\s*allow_implicit_invocation:\s*(true|false)\s*$',path.read_text())
    if not match and 'allow_implicit_invocation' in path.read_text():raise ValueError('Unsupported invocation policy: '+str(path))
    return match.group(1)=='true' if match else True

def check(root,refresh=False):
    manifest=root/'manifests/skill-behavior.json';doc=json.loads(manifest.read_text())
    actual={p.parent.name:p.parent for p in (root/'skills').glob('*/SKILL.md')}
    recorded={entry['name']:entry for entry in doc['skills']}
    if len(recorded)!=len(doc['skills']):raise ValueError('Duplicate skill manifest names')
    if refresh:
        for name,path in actual.items():
            entry=recorded.get(name)
            if not entry:
                entry={'name':name,'component':'harness','source':'local Main Router entry','runtime_selection':'UNVERIFIED'}
                doc['skills'].append(entry);recorded[name]=entry
            new=digest(path/'SKILL.md')
            if entry.get('installed_sha256')!=new and entry.get('component')=='harness':entry['runtime_selection']='SOURCE_CHANGED; actual native model selection UNVERIFIED'
            entry['installed_sha256']=new;entry['allow_implicit_invocation']=policy(path/'agents/openai.yaml')
            entry['invocation_policy_sha256']=digest(path/'agents/openai.yaml') if (path/'agents/openai.yaml').is_file() else None
        doc['harness_version']=(root/'VERSION').read_text().strip()
        doc['registration_owner']='One global explicit-request entry; one project skills source and Main Router; no duplicate plugin/standalone hooks'
        manifest.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
        for name in ('harness-dev','harness-maintain','harness-doctor'):
            shutil.copyfile(manifest,root/'skills'/name/'references/skill-behavior.json')
        for name,path in actual.items():
            shutil.copytree(path,root/'plugins/harness/skills'/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    errors=[]
    if set(actual)!=set(recorded):errors.append('Manifest/source skill set differs')
    if doc.get('harness_version')!=(root/'VERSION').read_text().strip():errors.append('Manifest version differs')
    for name,path in actual.items():
        entry=recorded.get(name,{})
        if entry.get('installed_sha256')!=digest(path/'SKILL.md'):errors.append(name+': source hash differs')
        if entry.get('allow_implicit_invocation')!=policy(path/'agents/openai.yaml'):errors.append(name+': policy differs')
        if 'invocation_policy_sha256' in entry and entry['invocation_policy_sha256']!=(digest(path/'agents/openai.yaml') if (path/'agents/openai.yaml').is_file() else None):errors.append(name+': policy hash differs')
        mirror=root/'plugins/harness/skills'/name
        sources={p.relative_to(path) for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
        mirrored={p.relative_to(mirror) for p in mirror.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
        if sources!=mirrored:errors.append(name+': mirror file set differs')
        for relative in sources&mirrored:
            if (path/relative).read_bytes()!=(mirror/relative).read_bytes():errors.append(name+': mirror differs: '+str(relative))
    for name in ('harness-dev','harness-maintain','harness-doctor'):
        if (root/'skills'/name/'references/skill-behavior.json').read_bytes()!=manifest.read_bytes():errors.append(name+': stale manifest reference')
    return {'status':'FAIL' if errors else 'PASS','skills':len(actual),'errors':errors,
            'scope':'SOURCE_HASH_POLICY_AND_MIRROR_IDENTITY; no native model routing assertion'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--refresh',action='store_true');parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);args=parser.parse_args()
    result=check(args.root,args.refresh);print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(1 if result['status']=='FAIL' else 0)
