#!/usr/bin/env python3
"""Codex lifecycle adapter. Reads event identity, never transcript or credentials."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness_core import cli, context
from harness_core.common import HarnessError, lock, read_json, write_json


def handle(payload):
    if os.environ.get('PLANNING_DISABLED')=='1':return {}
    repo=cli.select_project(payload['cwd']);cli.project_binding(repo)
    event=payload.get('hook_event_name');session=payload.get('session_id')
    try:task=context.choose_task(repo,session=session)
    except HarnessError as error:
        message=str(error)
        # A trusted L2 project may also host explanation/non-task chats. With no
        # active task there is nothing to restore, so PostCompact must not stop the
        # whole session. Ambiguous multiple-task recovery remains fail-closed.
        no_task=message.startswith('No active Harness task')
        return {'continue':no_task or event!='PostCompact','systemMessage':'Harness context: '+message+'. Main must select the task before implementing.'}
    if event=='PreCompact':
        result=context.checkpoint(repo,task,session=session,reason='precompact')
        return {'systemMessage':result['status']+': '+result['path']+'; local only. No formal PowerContext Handoff for compaction.'}
    if event=='PostCompact':
        marker=context.safe_file(repo/'.harness/state/compactions.json')
        with lock(repo/'.harness/state/context-hook.lock'):
            counts=read_json(marker,{})
            identity=session or 'unknown-session';counts[identity]=counts.get(identity,0)+1
            write_json(marker,counts)
    result=context.verify(repo,task)
    text=result['status']+'; task '+task+'; resume '+result['resume_node']+'. Read the checkpoint/PWF, approved Spec, tests/review and actual workers before modifying code. Current Git overrides Handoff/chat. '+', '.join(result['conflicts'])
    counts=read_json(context.safe_file(repo/'.harness/state/compactions.json'),{})
    if counts.get(session or 'unknown-session',0)>=cli.defaults()['context']['handoff_after_compactions']:
        text+=' HANDOFF_RECOMMENDED: this session has compacted repeatedly; checkpoint and plan a formal cross-session handoff.'
    if event=='SessionStart':
        return {'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':text}}
    return {'continue':result['status']=='RESUME_OK','systemMessage':text,'stopReason':text if result['status']!='RESUME_OK' else ''}


if __name__=='__main__':
    payload={}
    try:
        payload=json.load(sys.stdin);result=handle(payload)
    except (HarnessError,KeyError,OSError,ValueError) as error:
        result={'continue':payload.get('hook_event_name')!='PostCompact','systemMessage':'Harness recovery needs verification: '+str(error)}
    print(json.dumps(result,ensure_ascii=False))
