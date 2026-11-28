// Execute the actual fixed Ego script against real Chromium CDP and a local
// HTTPS fixture. DNS is closed to the loopback-only destinations below.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:https';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch, newPage } from './browser.mjs';

const python = process.env.AGENTJ_TEST_PYTHON || 'python3';
const hostDir = fileURLToPath(new URL('../../host/', import.meta.url));
const script = (site, account, action) => execFileSync(python, ['-B', '-c',
  'import sys; from creator_preview.browser_creator import script; print(script(*sys.argv[1:]))', site, account, action],
  {encoding: 'utf8', env: {...process.env, PYTHONPATH: hostDir}});
const workspace = mkdtempSync(join(tmpdir(), 'aj-creator-fixture-'));
const key = join(workspace, 'fixture.key'), cert = join(workspace, 'fixture.pem');
execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert,
  '-subj', '/CN=fixture.invalid', '-days', '1'], {stdio: 'ignore'});
const trace = [];
const channel = 'UC' + 'x'.repeat(22);
const fixture = {mid: 1234, archMid: 1234, redirect: false, loggedIn: true};
const local = createServer({key: readFileSync(key), cert: readFileSync(cert)}, (req, res) => {
  const site = req.headers.host;
  trace.push({site, path: req.url, method: req.method, hasBrowserCookie: req.headers.cookie === 'fixture=local'});
  if (site === 'api.bilibili.com' && req.url === '/x/web-interface/nav') {
    res.writeHead(200, {'content-type': 'application/json', 'access-control-allow-origin': 'https://member.bilibili.com',
      'access-control-allow-credentials': 'true'});
    return res.end(JSON.stringify({code: 0, data: {isLogin: true, mid: fixture.mid, unused: 'SYNTHETIC_UNUSED'}}));
  }
  if (site === 'member.bilibili.com' && req.url.startsWith('/x/web/archives?')) {
    if (fixture.redirect) return res.writeHead(307, {location: 'https://evil.invalid/redirect'}).end();
    res.writeHead(200, {'content-type': 'application/json'});
    return res.end(JSON.stringify({code: 0, data: {arc_audits: [{Archive: {
      bvid: 'BV1234567890', state: -1, mid: fixture.archMid, title: '<img src="https://evil.invalid/image">', extra: 'SYNTHETIC_UNUSED',
    }}]}}));
  }
  if (site === 'studio.youtube.com' && req.url === '/channel/' + channel) {
    res.writeHead(200, {'content-type': 'text/html'});
    return res.end('<iframe src="https://evil.invalid/frame"></iframe><script src="https://evil.invalid/script"></script>'
      + '<script>ytcfg.set(' + JSON.stringify({CHANNEL_ID: channel, LOGGED_IN: fixture.loggedIn, token: 'SYNTHETIC_UNUSED'}) + ');</script>');
  }
  res.writeHead(404).end();
});
await new Promise((resolve) => local.listen(0, '127.0.0.1', resolve));
const port = local.address().port;
const closedDns = ['member.bilibili.com', 'api.bilibili.com', 'studio.youtube.com', 'evil.invalid']
  .map(host => 'MAP ' + host + ' 127.0.0.1:' + port).join(', ') + ', MAP * ~NOTFOUND';
// Public fixture origins are deliberately mapped to loopback in this browser.
const B = await launch({args: ['--no-proxy-server', '--ignore-certificate-errors',
  '--disable-features=LocalNetworkAccessChecks', '--host-resolver-rules=' + closedDns]});

async function follow(site, account, action) {
  const p = await newPage(B, 390, 844, 'light');
  try {
    const pending = [];
    p.on(m => pending.push(m));
    for (const domain of ['api.bilibili.com', 'member.bilibili.com', 'studio.youtube.com']) {
      assert.equal((await p.send('Network.setCookie', {name: 'fixture', value: 'local', domain, secure: true})).success, true);
    }
    const exec = new Function('page', 'return (async () => {' + script(site, account, action) + '})();');
    return await exec({cdp: (method, args) => p.send(method, args), events: async () => pending.splice(0)});
  } finally { await p.dispose(); }
}

try {
  assert.deepEqual(await follow('bilibili', '1234', 'verify'), {account: '1234'});
  assert.deepEqual(await follow('bilibili', '1234', 'archive_status'), {account: '1234', archives: [{id: 'BV1234567890', state: -1}]});
  assert.deepEqual(await follow('bilibili', '1234', 'archive_status'), {account: '1234', archives: [{id: 'BV1234567890', state: -1}]});
  assert.deepEqual(await follow('youtube-studio', channel, 'channel_status'), {account: channel});
  fixture.loggedIn = false;
  assert.equal(await follow('youtube-studio', channel, 'channel_status'), null);
  fixture.archMid = 9999;
  assert.equal(await follow('bilibili', '1234', 'archive_status'), null);
  fixture.archMid = 1234;
  fixture.redirect = true;
  assert.equal(await follow('bilibili', '1234', 'archive_status'), null);
  fixture.redirect = false;
  fixture.mid = 9999;
  const count = trace.length;
  assert.equal(await follow('bilibili', '1234', 'archive_status'), null);
  assert.deepEqual(trace.slice(count).map(row => row.path), ['/x/web-interface/nav']);
  assert.equal(trace.some(row => row.site === 'evil.invalid' || row.path.startsWith('/.well-known/')), false);
  assert.ok(trace.every(row => row.method === 'GET' && row.hasBrowserCookie), JSON.stringify(trace));
  console.log('PASS Chromium fixed creator GETs, repeat reads, cross-account denial, redirect denial, inert provider HTML and no cross-origin effects.');
} finally {
  await B.close();
  await new Promise(resolve => local.close(resolve));
  rmSync(workspace, {recursive: true, force: true});
}
