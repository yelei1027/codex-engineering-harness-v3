// Short-lived SDK client used by the owned resource gateway, never an arbitrary shell.
import { Client } from '../runtimes/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StreamableHTTPClientTransport } from '../runtimes/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/streamableHttp.js';
import { ResultSchema } from '../runtimes/node/node_modules/@modelcontextprotocol/sdk/dist/esm/types.js';
let chunks=[],size=0;
for await (const chunk of process.stdin) {
  size+=chunk.length;
  if(size>8*1024*1024) throw Error('Input exceeds resource request budget');
  chunks.push(chunk);
}
const input=JSON.parse(Buffer.concat(chunks).toString('utf8'));
const url=new URL(input.url);
if(url.protocol!=='http:' || url.hostname!=='127.0.0.1' || url.username || url.password || url.search || url.hash || url.pathname!=='/mcp/') throw Error('Owned loopback MCP endpoint required');
const cancellation=new AbortController();
process.once('SIGTERM',()=>cancellation.abort());
const client=new Client({name:'harness-resource-gateway',version:'3.3.0'}, {capabilities:{}});
const transport=new StreamableHTTPClientTransport(url,{reconnectionOptions:{maxRetries:0}});
// SDK abort rejects the request before its cancellation POST has completed.
// Keep that POST alive until acknowledged before DELETE/close tears down the session.
const send=transport.send.bind(transport);
let cancellationDelivery=Promise.resolve();
transport.send=(message,options)=>{
  const delivered=send(message,options);
  if(message.method==='notifications/cancelled') cancellationDelivery=delivered.catch(()=>{});
  return delivered;
};
try {
  await client.connect(transport);
  let output;
  if(input.operation==='discovery') {
    const capabilities=client.getServerCapabilities() || {};
    output={initialize:{protocolVersion:transport.protocolVersion,capabilities,
      serverInfo:client.getServerVersion(),instructions:client.getInstructions()}};
    for(const [cap,method,key] of [['tools','tools/list','tools'],['prompts','prompts/list','prompts'],['resources','resources/list','resources'],['resources','resources/templates/list','resourceTemplates']]) {
      if(!capabilities[cap]) continue;
      let cursor,records=[],seen=new Set();
      do {
        const result=await client.request({method,params:cursor?{cursor}:{}},ResultSchema);
        if(!Array.isArray(result[key])) throw Error('Invalid discovery page');
        records.push(...result[key]);cursor=result.nextCursor;
        if(cursor && seen.has(cursor)) throw Error('Repeated discovery cursor');
        seen.add(cursor);
        if(records.length>10000 || seen.size>100) throw Error('Discovery exceeds budget');
      } while(cursor);
      output[method]={[key]:records};
    }
  } else if(input.operation==='call') {
    const message=input.message;
    if(!message || !['tools/call','resources/read','prompts/get','completion/complete'].includes(message.method)) throw Error('Unsupported resource operation');
    try {
      const result=await client.request({method:message.method,params:message.params || {}},ResultSchema,{timeout:600000,signal:cancellation.signal});
      output={jsonrpc:'2.0',id:message.id,result};
    } catch(error) {
      if(typeof error.code!=='number') throw error;
      output={jsonrpc:'2.0',id:message.id,error:{code:error.code,message:error.message,...(error.data===undefined?{}:{data:error.data})}};
    }
  } else throw Error('Unknown resource operation');
  const text=JSON.stringify(output);
  if(Buffer.byteLength(text)>8*1024*1024) throw Error('Response exceeds resource budget');
  process.stdout.write(text+'\n');
} catch {
  // No upstream error bodies or provider secrets in process diagnostics.
  process.stderr.write('MCP resource operation failed\n');
  process.exitCode=1;
} finally {
  await cancellationDelivery;
  // Closing the client ends transport; backend may be stateless or reject DELETE.
  try { await transport.terminateSession(); } catch {}
  await client.close();
}
