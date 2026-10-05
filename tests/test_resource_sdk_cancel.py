"""Cancellation delivery must complete before SDK session teardown."""
import json
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node is required for the gateway client')
class CancellationDeliveryTests(unittest.TestCase):
    def test_delayed_cancellation_post_precedes_session_teardown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'scripts/resource-mcp-client.mjs'
            script.parent.mkdir()
            shutil.copy2(ROOT / 'scripts/resource-mcp-client.mjs', script)
            sdk = root / 'runtimes/node/node_modules/@modelcontextprotocol/sdk'
            client = sdk / 'dist/esm/client'
            client.mkdir(parents=True)
            (sdk / 'package.json').write_text('{"type":"module"}')
            (sdk / 'dist/esm/types.js').write_text('export const ResultSchema={};')
            (client / 'index.js').write_text('''
import fs from 'node:fs';
export class Client {
 async connect(transport){this.transport=transport;}
 request(message,schema,options){
  fs.writeFileSync('ready','1');
  return new Promise((resolve,reject)=>{const keepalive=setInterval(()=>{},1000); options.signal.addEventListener('abort',()=>{clearInterval(keepalive);
   this.transport.send({method:'notifications/cancelled'}).catch(()=>{});
   reject(Object.assign(new Error('cancelled'),{code:-32800}));
  });});
 }
 async close(){fs.appendFileSync('events','close\\n');}
}
''')
            (client / 'streamableHttp.js').write_text('''
import fs from 'node:fs';
export class StreamableHTTPClientTransport {
 async send(){await new Promise(resolve=>setTimeout(resolve,150)); fs.appendFileSync('events','delivered\\n');}
 async terminateSession(){fs.appendFileSync('events','terminate\\n');}
}
''')
            process = subprocess.Popen(['node', str(script)], cwd=root,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                process.stdin.write(json.dumps({'url':'http://127.0.0.1:1/mcp/',
                    'operation':'call','message':{'method':'tools/call','id':7}}))
                process.stdin.close()
                deadline = time.monotonic() + 5
                while not (root / 'ready').exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(process.stderr.read())
                    time.sleep(.01)
                self.assertTrue((root / 'ready').exists())
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertEqual((root / 'events').read_text().splitlines(),
                                 ['delivered', 'terminate', 'close'])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                process.stderr.close()
