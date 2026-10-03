import { Client } from '../runtimes/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../runtimes/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readFileSync, writeFileSync } from 'node:fs';
const config = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const client = new Client({name:'harness-acceptance',version:'3.1.1'}, {capabilities:{roots:{listChanged:true}}});
const transport = new StdioClientTransport({command:config.command,args:config.args||[],cwd:config.cwd,env:{...process.env,...config.env},stderr:'pipe'});
const output = {server:config.name,started_at:new Date().toISOString(),status:'FAIL',calls:[]};
const timer=setTimeout(()=>{transport.close();}, 90000);
try {
  await client.connect(transport);
  const tools=await client.listTools();
  output.tools=tools.tools;
  for(const call of config.calls||[]) {
    const result=await client.callTool(call);
    output.calls.push({...call,result});
    if(result.isError) throw new Error('tool returned isError: '+call.name);
    if(call.expect && !JSON.stringify(result).includes(call.expect)) throw new Error('missing expected result: '+call.expect);
  }
  output.status='PASS';
} catch(e) {output.error=String(e);}
finally {clearTimeout(timer);await client.close();}
if(config.output)writeFileSync(config.output,JSON.stringify(output,null,2)+'\n');
console.log(JSON.stringify({server:output.server,status:output.status,tools:output.tools?.map(t=>t.name),error:output.error,calls:output.calls.length}));
if(output.status!=='PASS')process.exitCode=1;
