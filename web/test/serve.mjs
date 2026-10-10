// Local static server for the web client: serves public/ on 127.0.0.1 with the SAME headers worker.ts sends
// (webHeaders() is imported from worker.ts, so the two cannot drift), except connect-src = relayCsp for local relays.
//   import { startWebServer } from 'web/test/serve.mjs';
//   const { url, stop } = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
// CLI: node web/test/serve.mjs [port]
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join, normalize, extname } from 'node:path';
import { webHeaders, SPA_PATHS } from '../worker.ts';

export const PUBLIC_DIR = fileURLToPath(new URL('../public/', import.meta.url));
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8', '.json': 'application/json', '.png': 'image/png', '.webmanifest': 'application/manifest+json',
  '.ico': 'image/x-icon', '.webp': 'image/webp', '.woff2': 'font/woff2', '.md': 'text/markdown; charset=utf-8' };

export function startWebServer({ port = 0, relayCsp = 'ws://127.0.0.1:*', publicDir = PUBLIC_DIR } = {}) {

  const server = createServer(async (req, res) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(404).end(); return; }
    const url = new URL(req.url, 'http://x');
    if (SPA_PATHS.includes(url.pathname)) url.pathname = '/';          // same page routes as worker.ts (/friends)
    const p = normalize(decodeURIComponent(url.pathname)).replace(/^(\.\.[/\\])+/, '');
    let file = join(publicDir, p);
    if (!file.startsWith(publicDir.replace(/\/$/, ''))) { res.writeHead(404).end(); return; }
    try {
      let st = await stat(file);
      if (st.isDirectory()) { file = join(file, 'index.html'); st = await stat(file); }
      const body = await readFile(file);
      res.writeHead(200, { ...webHeaders(relayCsp, `http://127.0.0.1:${server.address().port}/version.json`), 'content-type': TYPES[extname(file)] || 'application/octet-stream' });
      res.end(req.method === 'HEAD' ? undefined : body);
    } catch { res.writeHead(404, { 'content-type': 'text/plain' }).end('not found'); }
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => {
    const { port: actual } = server.address();
    resolve({ url: `http://127.0.0.1:${actual}/`, port: actual, server,
      stop: () => new Promise((r) => { server.closeAllConnections?.(); server.close(() => r()); }) });
  }));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { url } = await startWebServer({ port: Number(process.argv[2] || 0) });
  console.log(`serving ${PUBLIC_DIR} at ${url}`);
}
