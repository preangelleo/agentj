// #500 rollback (chairman 11-05 22:5x: 「这个不是重新设计，这个是roll back，因为以前的就很好」): the approval card is back to
// the 0.26.8a2 bottom sheet — same place, same width — and the dark notice strip keeps its 0.26.9 width. The same scenario
// (one reply, an approval card; then a dark 「已自动放行」 notice) runs on three web trees served side by side:
//   a2   = web/public at ec27aa6ce6 (private 0.26.8a2 qualification, the published 0.26.8a2 web page)
//   head = web/public at the branch HEAD (0.26.9 line, before this rollback) — the notice baseline
//   now  = this worktree (the rollback)
// Real phone page against the local fake host, own headless Chromium (never :9222). 360 and 412 px wide, dark.
// Shots of the rolled-back tree go to reports/hotfix-500/rollback/ (P500_OUT overrides the folder).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { startWebServer, PUBLIC_DIR } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate as ev, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const A2 = process.env.P500_A2_REV || 'ec27aa6ce6';
const OUT = process.env.P500_OUT ? pathToFileURL(process.env.P500_OUT.replace(/\/?$/, '/')) : new URL('../../../reports/hotfix-500/rollback/', import.meta.url);
mkdirSync(OUT, { recursive: true });
const REPO = fileURLToPath(new URL('../../../', import.meta.url));
const TREES = join(process.env.TMPDIR || join(homedir(), '.cache'), `p500-trees-${process.pid}`);
const tree = (rev, name) => {
  const dir = join(TREES, name); mkdirSync(dir, { recursive: true });
  const tar = execFileSync('git', ['-C', REPO, 'archive', rev, 'web/public'], { maxBuffer: 1 << 30 });
  execFileSync('tar', ['-x', '-C', dir, '--strip-components=3'], { input: tar });
  return dir + '/';
};
const servers = {
  a2: await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', publicDir: tree(A2, 'a2') }),
  head: await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', publicDir: tree('HEAD', 'head') }),
  now: await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', publicDir: PUBLIC_DIR }),
};
const fake = await startFakeHost(), B = await launch();
const hex = (n) => randomBytes(n).toString('hex');
const results = [];
const check = (ok, what) => { results.push({ ok: !!ok, what }); console.log((ok ? 'ok   ' : 'FAIL ') + what); };
const lines = (sel) => `(()=>{const n=document.querySelector(${JSON.stringify(sel)});if(!n||!n.getClientRects().length)return -1;
  const cs=getComputedStyle(n),lh=parseFloat(cs.lineHeight)||parseFloat(cs.fontSize)*1.4;return Math.round(n.getBoundingClientRect().height/lh)})()`;
const rect = (sel) => `(()=>{const n=document.querySelector(${JSON.stringify(sel)});if(!n||!n.getClientRects().length)return null;const r=n.getBoundingClientRect();
  return {left:Math.round(r.left),right:Math.round(r.right),top:Math.round(r.top),bottom:Math.round(r.bottom),w:Math.round(r.width),parent:n.parentElement.id||n.parentElement.className}})()`;

async function run(which, w, h, shots) {
  fake.reset();
  const web = servers[which];
  const p = await newPage(B, w, h, 'dark', { touch: true, allow: [web.url, fake.relay + '/'] });
  try {
    await navigate(p, fake.newPairing(web.url));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await ev(p, `(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();return true})()`);
    const dev = await ev(p, `import('./js/api.js').then(m=>m.myDeviceId())`);
    await fake.addTurn({ k: 'phone', dev, text: '把这个密钥换掉' }, '好的，我来换这个密钥。这一步要你点头，卡片在下面。');
    await waitFor(p, `document.getElementById('pg').textContent==='1 / 1'`); await sleep(400);
    // the dark notice first, alone (its width is the thing to keep)
    await fake.send({ t: 'auto', by: 'full', tool: 'Bash', summary: 'git status' });
    await waitFor(p, `!!document.querySelector('#toast.on, .toast.on')`); await sleep(500);
    const toast = await ev(p, rect('#toast'));
    if (shots) writeFileSync(new URL(`notice-${w}-dark.png`, OUT), await shoot(p, ''));
    await ev(p, `(()=>{document.querySelectorAll('.toast').forEach(t=>t.classList.remove('on'));return true})()`);
    await fake.ask({ id: hex(16), tool: 'Bash', summary: 'agentj secret rotate EXAMPLE_API_KEY --verify', ttl: 120,
      reason: '改密码或密钥。具体内容只在 Agent J 主机上看，凭据不过隧道。' });
    await waitFor(p, `document.body.dataset.sheet==='1'`); await sleep(800);
    const sheet = await ev(p, rect('#sheet'));
    const askL = await ev(p, lines('#askText')), hostL = await ev(p, lines('#sheet>.cp-host>span'));
    const overlap = await ev(p, `(()=>{const a=document.querySelector('#sheet .sheetacts')?.getBoundingClientRect();if(!a)return false;
      return [...document.querySelectorAll('#sheet>.cp-host span,#askText,#sheetTitle')].some(n=>{const t=document.createRange();t.selectNodeContents(n);
      const q=t.getBoundingClientRect();return q.width>0&&q.right>a.left+1&&q.left<a.right&&q.bottom>a.top+1&&q.top<a.bottom-1})})()`);
    const g = { vw: await ev(p, 'innerWidth'), toast, sheet, askL, hostL, overlap };
    console.log(which, w, JSON.stringify(g));
    if (shots) writeFileSync(new URL(`approval-${w}-dark.png`, OUT), await shoot(p, ''));
    return g;
  } finally { await p.close?.(); }
}

try {
  for (const [w, h] of [[360, 780], [412, 915]]) {
    const a2 = await run('a2', w, h, false), head = await run('head', w, h, false), now = await run('now', w, h, w === 360);
    const id = `${w}-dark`;
    check(now.sheet && a2.sheet && now.sheet.parent === a2.sheet.parent && now.sheet.parent === 'stage', `${id}: the approval card is in the chat stage, as in 0.26.8a2 (${now.sheet?.parent} / ${a2.sheet?.parent})`);
    for (const k of ['left', 'right', 'w', 'bottom'])
      check(now.sheet && a2.sheet && Math.abs(now.sheet[k] - a2.sheet[k]) <= 1, `${id}: card ${k} = 0.26.8a2 (${now.sheet?.[k]} vs ${a2.sheet?.[k]})`);
    check(now.sheet && now.sheet.left <= 0 && now.sheet.right >= now.vw, `${id}: the card spans the screen (${now.sheet?.left}..${now.sheet?.right} of ${now.vw})`);
    check(now.askL === 1, `${id}: 「请你点头」 on one line (${now.askL})`);
    check(now.hostL === 1, `${id}: 「来自 Agent J 主机」 badge kept, one line (${now.hostL})`);
    check(!now.overlap, `${id}: the 收起 button covers no text`);
    check(now.toast && head.toast && ['left', 'right', 'w'].every((k) => Math.abs(now.toast[k] - head.toast[k]) <= 1) && now.toast.parent === head.toast.parent,
      `${id}: the dark notice keeps its 0.26.9 width and place (${JSON.stringify(now.toast)} vs ${JSON.stringify(head.toast)})`);
  }
} finally {
  await B.close?.(); await fake.close?.(); for (const s of Object.values(servers)) await s.close?.();
  rmSync(TREES, { recursive: true, force: true });
}
const bad = results.filter((r) => !r.ok);
writeFileSync(new URL('results.json', OUT), JSON.stringify({ ok: !bad.length, a2: A2, results }, null, 1));
assert.equal(bad.length, 0, `#500 rollback: ${bad.length} check(s) failed`);
console.log(`p500-wide: ${results.length} checks PASS`);
process.exit(0);
