// Dependency-free serving of the immutable Vite build with same-origin backend routes.
import http from 'node:http';
import {createReadStream} from 'node:fs';
import {realpath, stat} from 'node:fs/promises';
import path from 'node:path';

const root = await realpath(process.env.STATIC_ROOT || '/app/dist');
const upstream = new URL(process.env.PROXY_CODEGEN_BACKEND || 'http://screenshot-backend:7001');
if (upstream.protocol !== 'http:' || upstream.username || upstream.password) throw Error('Invalid backend');
const routed = p => ['/api', '/local-assets', '/generate-code'].some(prefix => p === prefix || p.startsWith(prefix + '/'));
const types = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.ico':'image/x-icon','.woff2':'font/woff2'};
const server = http.createServer(async (req, res) => {
  let pathname;
  try { pathname = decodeURIComponent(new URL(req.url, 'http://local').pathname); }
  catch { res.writeHead(400).end(); return; }
  if (routed(pathname)) {
    const proxy = http.request({hostname:upstream.hostname, port:upstream.port, path:req.url,
      method:req.method, headers:{...req.headers, host:upstream.host}}, response => {
        res.writeHead(response.statusCode, response.headers);
        response.pipe(res);
        response.on('error', () => res.destroy());
    });
    proxy.on('error', () => { if (!res.headersSent) res.writeHead(502).end(); else res.destroy(); });
    req.on('aborted', () => proxy.destroy());
    res.on('close', () => proxy.destroy());
    req.pipe(proxy);
    return;
  }
  if (!['GET','HEAD'].includes(req.method)) { res.writeHead(405).end(); return; }
  try {
    let target = path.resolve(root, '.' + pathname);
    if (target !== root && !target.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    let info;
    try { info = await stat(target); }
    catch { if (path.extname(target)) { res.writeHead(404).end(); return; } }
    if (!info || info.isDirectory()) target = path.join(root, 'index.html');
    target = await realpath(target);
    if (!target.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    info = await stat(target);
    if (!info.isFile()) { res.writeHead(404).end(); return; }
    res.writeHead(200, {'Content-Type':types[path.extname(target)] || 'application/octet-stream',
      'Content-Length':info.size, 'X-Content-Type-Options':'nosniff',
      'Cache-Control':path.basename(target)==='index.html'?'no-cache':'public, max-age=3600'});
    if (req.method === 'HEAD') res.end();
    else createReadStream(target).on('error', () => res.destroy()).pipe(res);
  } catch { if (!res.headersSent) res.writeHead(404).end(); else res.destroy(); }
});
server.on('upgrade', (req, client, head) => {
  if (new URL(req.url, 'http://local').pathname !== '/generate-code') { client.destroy(); return; }
  const proxy = http.request({hostname:upstream.hostname,port:upstream.port,path:req.url,
    headers:{...req.headers,host:upstream.host}});
  proxy.on('upgrade', (response, remote, extra) => {
    client.write(`HTTP/1.1 ${response.statusCode} ${response.statusMessage}\r\n` +
      response.rawHeaders.reduce((s,v,i,a) => i%2?s:s+a[i]+': '+a[i+1]+'\r\n','')+'\r\n');
    if (extra.length) client.write(extra);
    if (head.length) remote.write(head);
    client.pipe(remote).pipe(client);
    client.on('error', () => remote.destroy()); remote.on('error', () => client.destroy());
    client.on('close', () => remote.destroy()); remote.on('close', () => client.destroy());
  });
  proxy.on('response', response => { response.resume(); client.destroy(); });
  proxy.on('error', () => client.destroy());
  client.on('close', () => proxy.destroy());
  proxy.end();
});
server.listen(Number(process.env.PORT || 5173), process.env.HOST || '0.0.0.0');
