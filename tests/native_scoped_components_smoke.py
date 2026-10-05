"""Installed component tool calls through current scope adapter in disposable project.
The dependency root is an earlier owned full preparation snapshot, not final package proof.
"""
import argparse,json,os,select,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'tests'),str(ROOT)]
from test_public_lifecycle import PublicLifecycleCase
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runtime-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
dependency=args.runtime_root.resolve();case=PublicLifecycleCase();case.setUp();case.env['PATH']=os.environ['PATH'];release=case.release();record={'dependency_root':str(dependency),'scope':__doc__,'checks':[]}
try:
    original=json.loads((dependency/'machine-binding.json').read_text())
    names=['context-mode','codegraph','markitdown'];servers={name:original['mcp_servers'][name] for name in names}
    servers['context-mode']['env']['CONTEXT_MODE_DIR']=str(case.base/'context-state')
    (release/'machine-binding.json').write_text(json.dumps({'codex_binary':'/bin/echo','mcp_servers':servers}))
    case.public(release,'install');case.public(release,'prepare',case.repo,'--apply','--profile','L1')
    import tomllib
    registered=tomllib.loads((case.repo/'.codex/config.toml').read_text())['mcp_servers']
    (case.repo/'sample.py').write_text('def scoped_component_symbol(value):\n    return value.strip().upper()\n')
    (case.repo/'input.html').write_text('<h1>scoped-markitdown-marker</h1>')
    index=subprocess.run([servers['codegraph']['command'],'init','-y',str(case.repo)],env=case.env,capture_output=True,text=True,timeout=60)
    if index.returncode:raise RuntimeError(index.stdout+index.stderr)
    calls={'context-mode':[{'name':'ctx_execute','arguments':{'language':'javascript','code':'console.log("scoped-context-execution")'},'expect':'scoped-context-execution'},
                           {'name':'ctx_index','arguments':{'content':'# Scoped document\nscoped persistent search marker','source':'scoped-test'}},
                           {'name':'ctx_search','arguments':{'queries':['persistent search marker'],'source':'scoped-test'},'expect':'scoped persistent'}],
           'codegraph':[{'name':'codegraph_explore','arguments':{'query':'scoped_component_symbol','projectPath':str(case.repo)},'expect':'return value.strip().upper()'}],
           'markitdown':[{'name':'convert_to_markdown','arguments':{'uri':(case.repo/'input.html').as_uri()},'expect':'scoped-markitdown-marker'}]}
    for name in names:
        config=case.base/(name+'.json');result_path=case.base/(name+'-result.json')
        config.write_text(json.dumps({**registered[name],'name':name,'cwd':str(case.repo),'calls':calls[name],'output':str(result_path)}))
        client=subprocess.run(['node',str(dependency/'scripts/mcp_probe.mjs'),str(config)],env=case.env,capture_output=True,text=True,timeout=110)
        if client.returncode:raise RuntimeError(name+' '+client.stdout+client.stderr)
        result=json.loads(result_path.read_text());assert result['status']=='PASS'
        _,scopes=case.public(release,'processes','status')
        assert not scopes['active_scopes'],name+' scope outlived its MCP connection'
        record['checks'].append({'server':name,'status':result['status'],'calls':len(result['calls']),'scope_closed':True})
    record['status']='PASS'
except Exception as error:record.update(status='FAIL',error=str(error))
finally:
    try:case.public(release,'processes','stop')
    except Exception as error:record['cleanup_error']=str(error)
    case.doCleanups();args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');print(json.dumps(record,ensure_ascii=False,indent=2))
    if record['status']!='PASS':sys.exit(1)
