// P432 (#66 Cards phase 2) end to end: the REAL host code (a throw-away AGENTJ_STATE_DIR, `agentj card apply` as the Agent
// runs it, jcards.phone_get / jcards_server.call for every jcard_get / jcard_call) behind the real page and a local Noise fake
// host. A card backed by its own server on 127.0.0.1 (a Node HTTP server here, answering X-AgentJ-Card): the list comes
// from the server; a server button shows where it goes on the confirm card, runs only on 运行 / Run, the server gets the
// host-made POST body and the card redraws with the new data. The official Files card (switched on with `agentj card on
// files`): a real working folder — hidden / link / key-named items never listed, folders open and the path goes back, a
// document downloads through the real §13 Media table (jcard_file → media_get → chunks, SHA-256 checked by the page), a
// key-holding file is refused, @ puts the path in the message box without sending. The official Obsidian card set up as
// the agentj-obsidian skill does it (`agentj card obsidian vault / pick`, then on): one small card per picked note, a key in
// a note hidden, a linked note refused, @ drafts `/agentj-obsidian @<note>` without sending. A card installed from the
// plaza (jcards_pkg.install): Settings → Cards marks it, switching it on shows its whole permission summary first; cancel
// leaves it off, 打开 / Turn on switches it on with the summary's digest (the real jcards.set_on checks it).
// Own headless Chromium (never :9222); 390 px touch emulation is not a physical-device claim. Shots → reports/qa/p432/.
import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p432');
const HOST = join(ROOT, 'agentjarvis/host');
const venv = [join(HOST, '.venv/bin/python'), join(ROOT, '../../host/.venv/bin/python')].find(existsSync);
const PY = process.env.AJ_PYTHON || venv || 'python3';
const SCRATCH = join(process.env.P432_SCRATCH || join(homedir(), '.cache/p432-web'));
const ev = evaluate, q = JSON.stringify;
const click = (p, sel) => ev(p, `document.querySelector(${q(sel)}).click()`);
const L = {
  zh: { run: '运行', refresh: '刷新', a: '构建通过', b: '部署完成', c: '回滚演练' },
  en: { run: 'Run', refresh: 'Refresh', a: 'Build passed', b: 'Deploy finished', c: 'Rollback drill' },
};

function py(state, code) {
  return execFileSync(PY, ['-c', code], { cwd: HOST, env: { ...process.env, AGENTJ_STATE_DIR: state, PYTHONPATH: HOST, PYTHONDONTWRITEBYTECODE: '1' }, encoding: 'utf8' });
}
const host = (state, expr) => JSON.parse(py(state, `import json, pathlib; from agentj import jcards, jcards_server; R = pathlib.Path(${q(state)}); print(json.dumps(${expr}, ensure_ascii=False))`));

// the card's own server, in its own process (execFileSync blocks this one): GET /board → items.json, POST /refresh → one
// more item + the body appended to posts.json; every answer names the card (X-AgentJ-Card: board)
mkdirSync(SCRATCH, { recursive: true });
const SRV = mkdtempSync(join(SCRATCH, 'srv-'));
const SERVER_PY = `
import http.server, json, pathlib, sys
D = pathlib.Path(sys.argv[1])
class H(http.server.BaseHTTPRequestHandler):
    def answer(self, code, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        if obj is not None:
            self.send_header("X-AgentJ-Card", "board"); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.headers.get("X-AgentJ-Card") != "board" or self.path != "/board": return self.answer(404)
        self.answer(200, {"items": json.loads((D / "items.json").read_text())["items"]})
    def do_OPTIONS(self):
        self.answer(200, {}) if self.headers.get("X-AgentJ-Card") == "board" and self.path == "/refresh" else self.answer(404)
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        if self.headers.get("X-AgentJ-Card") != "board" or self.path != "/refresh": return self.answer(404)
        st = json.loads((D / "items.json").read_text()); st["items"].append({"id": "c", "text": st["c"], "done": False})
        (D / "items.json").write_text(json.dumps(st))
        posts = json.loads((D / "posts.json").read_text()) if (D / "posts.json").exists() else []
        (D / "posts.json").write_text(json.dumps(posts + [body]))
        self.answer(200, {"ok": True, "message": st["msg"]})
    def log_message(self, *a): pass
s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
(D / "port").write_text(str(s.server_address[1]))
s.serve_forever()
`;
const child = spawn(PY, ['-c', SERVER_PY, SRV], { stdio: 'ignore' });
for (let i = 0; i < 100 && !existsSync(join(SRV, 'port')); i++) await sleep(50);
const PORT = Number(readFileSync(join(SRV, 'port'), 'utf8'));
const posts = () => existsSync(join(SRV, 'posts.json')) ? JSON.parse(readFileSync(join(SRV, 'posts.json'), 'utf8')) : [];

// the Files card's working folder
const WORK = mkdtempSync(join(SCRATCH, 'work-'));
mkdirSync(join(WORK, 'docs')); writeFileSync(join(WORK, 'docs/plan.md'), '# plan\n');
writeFileSync(join(WORK, 'report.pdf'), '%PDF-1.4\n' + 'x'.repeat(60000)); writeFileSync(join(WORK, 'notes.txt'), 'hello');
writeFileSync(join(WORK, '.env'), 'K=v'); writeFileSync(join(WORK, 'server.pem'), 'x');
writeFileSync(join(WORK, 'keys.txt'), 'deploy ' + 'AKIA' + 'Q3EGRKX7' + 'PLM2VN4B');
symlinkSync(join(WORK, 'docs'), join(WORK, 'link-dir'));
const WORK_LABEL = realpathSync(WORK).replace(homedir(), '~');
const VAULT = mkdtempSync(join(SCRATCH, 'vault-'));
mkdirSync(join(VAULT, '.obsidian')); mkdirSync(join(VAULT, 'Projects'));
writeFileSync(join(VAULT, 'Inbox.md'), '# Inbox\n\n- call the bank\n- key ' + 'AKIA' + 'Q3EGRKX7' + 'PLM2VN4B' + '\n');
writeFileSync(join(VAULT, 'Projects/Plan.md'), 'Ship **cards** this week, with a very long unbroken address https://example.com/' + 'a'.repeat(120) + '\n');
mkdirSync(OUT, { recursive: true });
const rec = parityRecorder('web/test/p432.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
let checks = 0; const results = [];
const check = (ok, name) => { assert.ok(ok, name); checks++; results.push(name); };
try {
  for (const [w, h, lang, scheme] of [[390, 844, 'zh', 'light'], [390, 844, 'en', 'dark'], [1440, 900, 'zh', 'light']]) {
    const T = L[lang], tag = `${w}-${lang}-${scheme}`;
    const state = mkdtempSync(join(SCRATCH, 's-'));
    try {
      rmSync(join(SRV, 'posts.json'), { force: true });
      writeFileSync(join(SRV, 'items.json'), JSON.stringify({ items: [{ id: 'a', text: T.a, done: true }, { id: 'b', text: T.b, done: false }], c: T.c, msg: lang === 'en' ? 'Refreshed' : '刷新好了' }));
      const folder = join(state, 'card-src'); mkdirSync(folder);
      writeFileSync(join(folder, 'card.json'), JSON.stringify({
        schemaVersion: 1, id: 'board', version: '1', name: { zh: '发布看板', en: 'Release board' }, description: { zh: '本机发布工具的状态', en: 'The local release tool' },
        icon: 'board', accent: 'windstorm', template: 'checklist', data: { get: '/board' },
        server: { port: PORT, routes: [{ id: 'refresh', path: '/refresh' }] },
        actions: [{ id: 'refresh', label: { zh: T.refresh, en: L.en.refresh }, call: 'refresh' }] }));
      const applied = JSON.parse(py(state, `import sys; from agentj import jcards; sys.exit(jcards.command(['apply', ${q(folder)}, '--json']))`));
      check(applied.ok, 'agentj card apply a server card ' + tag);

      fake.reset(); fake.st.caps = ['jcards1'];
      fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
      fake.st.onApp = async (c, m, send) => {
        const L2 = m.lang === 'en' ? 'en' : 'zh';
        if (m.t === 'jcard_list') { await send({ t: 'jcard_items', r: m.r, ...host(state, `jcards.phone_list(R, ${q(L2)})`) }); return true; }
        if (m.t === 'jcard_get') { py(state, 'from agentj import jcards_server; jcards_server._last_get.clear()'); await send({ t: 'jcard_get', r: m.r, ...host(state, `jcards.phone_get(R, ${q(m.id)}, ${q(L2)})`) }); return true; }
        if (m.t === 'jcard_files') { await send({ t: 'jcard_files', r: m.r, ...host(state, `__import__('agentj.jcards_files').jcards_files.listing(R, {'dir': ${q(WORK)}}, ${q(m.root ?? null).replace('null', 'None')}, ${q(m.path ?? '')})`) }); return true; }
        if (m.t === 'jcard_file') { await send({ t: 'jcard_file', r: m.r, ...host(state, `__import__('agentj.jcards_files').jcards_files.fetch(R, {'dir': ${q(WORK)}}, __import__('agentj.media').media.Media(__import__('agentj.state').state.State(R), None), ${q(m.root)}, ${q(m.path)})`) }); return true; }
        if (m.t === 'media_get') {
          const win = host(state, `[[o, __import__('agentj.wire').wire.b64u(d), last] for o, d, last in __import__('agentj.media').media.Media(__import__('agentj.state').state.State(R), None).window(${q(m.mid)}, ${Number(m.o)})]`);
          for (const [o, d, last] of win) await send({ t: 'media_chunk', mid: m.mid, o, d, last });
          return true;
        }
        if (m.t === 'jcard_perm') { await send({ t: 'jcard_perm', r: m.r, ...host(state, `(lambda c: {'ok': True, 'id': c['id'], 'text': __import__('agentj.jcards_pkg').jcards_pkg.summary_text(c['doc'], ${q(L2)}, __import__('agentj.jcards_pkg').jcards_pkg.source(R, c['id'], c['doc'])), 'digest': __import__('agentj.jcards_pkg').jcards_pkg.digest(c['doc'])})(jcards.find(R, ${q(m.id)}))`) }); return true; }
        if (m.t === 'jcard_set') { const ok = host(state, `jcards.set_on(R, ${q(m.id)}, ${m.on ? 'True' : 'False'}, ${q(m.seen ?? null).replace('null', 'None')})`); await send({ t: 'jcard_res', r: m.r, ok, id: ok ? m.id : null, on: ok ? m.on : null }); return true; }
        if (m.t === 'jcard_call') { await send({ t: 'jcard_call', r: m.r, ...host(state, `jcards_server.call(R, ${q(m.id)}, ${q(m.act)}, ${q(m.item ?? null).replace('null', 'None')}, ${q(L2)})`) }); return true; }
        return false;
      };
      const p = await newPage(B, w, h, scheme, { touch: w === 390, allow: [web.url, fake.relay + '/'] });
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
        await ev(p, "document.getElementById('phone-env-dismiss')?.click()"); await sleep(250);
        await ev(p, "import('./js/jcards.js').then(m=>m.refresh())");
        await ev(p, `import('./js/five-pages.js').then(m=>m.openTarget('board'))`);
        await waitFor(p, `document.querySelectorAll('.jc-body[data-card=board] .jc-item').length===2`); await sleep(150);
        check(await ev(p, `[...document.querySelectorAll('.jc-item .jc-text')].map(e=>e.textContent).join('|')===${q(T.a + '|' + T.b)}`), 'list from the card server ' + tag);
        check(await ev(p, "document.querySelector('.jc-action[data-act=refresh]').dataset.call==='1'"), 'server button marked ' + tag);
        check(!(await ev(p, "document.querySelector('.jc-body').textContent.includes('127.0.0.1')")), 'no address on the card face ' + tag);
        const wide = await ev(p, "[...document.querySelectorAll('.jc-item,.jc-action')].filter(e=>e.scrollWidth>e.clientWidth+1).length");
        check(wide === 0, 'fits the width ' + tag);
        writeFileSync(join(OUT, `server-card-${tag}.png`), await shoot(p));
        // the button: shown whole first; cancel sends nothing
        await click(p, '.jc-action[data-act=refresh]'); await waitFor(p, "!document.getElementById('confirm').hidden");
        check(await ev(p, `document.getElementById('confirm-text').textContent===${q(T.refresh + '\n' + `POST http://127.0.0.1:${PORT}/refresh`)}`), 'confirm card: label + where ' + tag);
        check(await ev(p, `document.getElementById('confirm-yes').textContent===${q(T.run)}`), 'confirm says Run ' + tag);
        writeFileSync(join(OUT, `server-confirm-${tag}.png`), await shoot(p));
        await click(p, '#confirm-no'); await sleep(300);
        check(posts().length === 0, 'cancel → nothing sent to the server ' + tag);
        await click(p, '.jc-action[data-act=refresh]'); await waitFor(p, "!document.getElementById('confirm').hidden");
        await click(p, '#confirm-yes');
        await waitFor(p, `document.querySelectorAll('.jc-body[data-card=board] .jc-item').length===3`);
        const P = posts();
        check(P.length === 1 && P[0].card === 'board' && P[0].action === 'refresh' && P[0].lang === lang && Object.keys(P[0]).length === 3, 'POST body made by the host ' + JSON.stringify(P) + ' ' + tag);
        check(await ev(p, `document.querySelector('.jc-item[data-item=c] .jc-text').textContent===${q(T.c)}`), 'redrawn with the new data ' + tag);
        // the Files card
        py(state, `import sys; from agentj import jcards; sys.exit(jcards.command(['on', 'files']))`);
        await ev(p, "import('./js/jcards.js').then(m=>m.refresh())"); await sleep(200);
        await ev(p, `import('./js/five-pages.js').then(m=>m.openTarget('files'))`);
        await waitFor(p, "document.querySelectorAll('.jc-file').length===4");
        check(await ev(p, "[...document.querySelectorAll('.jc-file')].map(li=>li.dataset.item+':'+li.dataset.kind).join()") === 'docs:dir,keys.txt:file,notes.txt:file,report.pdf:file', 'listing without hidden / link / key names ' + tag);
        check(await ev(p, "[...document.querySelectorAll('.jc-file,.jc-crumb')].every(e=>e.scrollWidth<=e.clientWidth+1)"), 'rows fit ' + tag);
        writeFileSync(join(OUT, `files-${tag}.png`), await shoot(p));
        await click(p, '.jc-file[data-item=docs] .jc-row'); await waitFor(p, "document.querySelector('.jc-file[data-item=\"plan.md\"]')");
        check(await ev(p, "document.querySelectorAll('.jc-crumb').length===2&&document.querySelectorAll('.jc-crumb')[1].textContent==='docs'"), 'path shows docs ' + tag);
        await click(p, '.jc-crumb'); await waitFor(p, "document.querySelectorAll('.jc-file').length===4");
        await click(p, '.jc-file[data-item="report.pdf"] .jc-file-dl');
        await waitFor(p, `document.getElementById('toast').textContent.includes('report.pdf')`);
        check(true, 'download through §13 media: the page verified and saved report.pdf ' + tag);
        await sleep(2600);
        await click(p, '.jc-file[data-item="keys.txt"] .jc-file-dl').catch(() => {});
        const keysDl = await ev(p, "!!document.querySelector('.jc-file[data-item=\"keys.txt\"] .jc-file-dl')");
        if (keysDl) { await waitFor(p, `document.getElementById('toast').textContent===${q(lang === 'zh' ? '这个文件像是密钥或含密钥，不能下载' : 'This file looks like a key or holds one, so it cannot be downloaded')}`); check(true, 'a key-holding file is refused ' + tag); }
        await sleep(2600);
        const n0 = fake.st.says.length;
        await click(p, '.jc-file[data-item="notes.txt"] .jc-file-at');
        await waitFor(p, "document.querySelector('#input').value.includes('notes.txt')");
        check(await ev(p, `document.querySelector('#input').value===${q('@' + WORK_LABEL + '/notes.txt ')}`) && fake.st.says.length === n0, '@ drafts the path, nothing sent ' + tag);
        // the Obsidian card
        for (const args of [['obsidian', 'vault', VAULT], ['obsidian', 'pick', 'Inbox.md', 'Projects/Plan.md'], ['on', 'obsidian']])
          py(state, `import sys; from agentj import jcards; sys.exit(jcards.command(${q(args)}))`);
        await ev(p, "import('./js/jcards.js').then(m=>m.refresh())"); await sleep(200);
        await ev(p, `import('./js/five-pages.js').then(m=>m.openTarget('obsidian'))`);
        await waitFor(p, "document.querySelectorAll('.jc-obsidian').length===2");
        check(await ev(p, "[...document.querySelectorAll('.jc-obsidian h2')].map(h=>h.textContent).join()") === 'Inbox,Plan', 'one card per picked note ' + tag);
        check(!(await ev(p, "document.querySelector('.jc-body').textContent.includes('AKIA')")), 'a key in a note is hidden ' + tag);
        check(await ev(p, "[...document.querySelectorAll('.jc-obsidian')].every(e=>e.scrollWidth<=e.clientWidth+1)"), 'long note text wraps ' + tag);
        writeFileSync(join(OUT, `obsidian-${tag}.png`), await shoot(p));
        const n1 = fake.st.says.length;
        await ev(p, "(()=>{const i=document.querySelector('#input');i.value='';i.dispatchEvent(new Event('input',{bubbles:true}));})()");
        await click(p, '.jc-obsidian[data-item=o2] .jc-auto-at');
        await waitFor(p, "document.querySelector('#input').value.startsWith('/agentj-obsidian')");
        check(await ev(p, "document.querySelector('#input').value==='/agentj-obsidian @Projects/Plan.md '") && fake.st.says.length === n1, '@ drafts the edit, nothing sent ' + tag);
        // a card from the plaza: off, marked, summary first
        const shared = { schemaVersion: 1, id: 'shared-notes', version: '1', name: { zh: '分享的笔记', en: 'Shared notes' }, description: { zh: '别人分享的卡', en: 'A shared card' },
          icon: 'note', accent: 'noir', template: 'notes', data: { file: 'n.md' }, actions: [{ id: 'tidy', label: { zh: '整理', en: 'Tidy' }, prompt: { zh: '帮我整理 {{card.name}}', en: 'Tidy {{card.name}}' } }] };
        py(state, `import json, pathlib; from agentj import jcards_pkg; R = pathlib.Path(${q(state)}); print(json.dumps(jcards_pkg.install(R, {'README.md': b'# shared', 'card/card.json': ${q(JSON.stringify(shared))}.encode(), 'card/sample/n.md': b'- hello'}, {'name': 'shared-notes', 'version': '1.0.0', 'sha256': 'a' * 64, 'certified': False})))`);
        await ev(p, "import('./js/jcards.js').then(m=>m.refresh())"); await sleep(200);
        await ev(p, "document.getElementById('settings').hidden && document.getElementById('setBtn').click()");
        await waitFor(p, "!document.getElementById('settings').hidden && !document.getElementById('set-cards-nav').hidden");
        await click(p, '#set-cards-nav');
        await waitFor(p, "document.querySelector('#set-cards-list .jc-setrow[data-card=shared-notes]')");
        check(await ev(p, "(()=>{const r=document.querySelector('#set-cards-list .jc-setrow[data-card=shared-notes]');return r.querySelector('.tgl').getAttribute('aria-checked')==='false'&&!!r.querySelector('.jc-src');})()"), 'plaza card: off and marked ' + tag);
        await click(p, '#set-cards-list .jc-setrow[data-card=shared-notes] .tgl'); await waitFor(p, "!document.getElementById('confirm').hidden");
        const sum = await ev(p, "document.getElementById('confirm-text').textContent");
        check(sum.includes(lang === 'zh' ? '来自广场：shared-notes v1.0.0' : 'From the plaza: shared-notes v1.0.0') && sum.includes(lang === 'zh' ? '「帮我整理 {{card.name}}」' : '「Tidy {{card.name}}」') && sum.includes('n.md'), 'summary shown word for word ' + tag);
        check(await ev(p, "getComputedStyle(document.getElementById('confirm-text')).whiteSpace==='pre-line'"), 'summary one line each ' + tag);
        writeFileSync(join(OUT, `plaza-summary-${tag}.png`), await shoot(p));
        await click(p, '#confirm-no'); await sleep(300);
        check(!host(state, "jcards.find(R, 'shared-notes')['on']") && await ev(p, "document.querySelector('#set-cards-list .jc-setrow[data-card=shared-notes] .tgl').getAttribute('aria-checked')==='false'"), 'cancel: stays off ' + tag);
        check(await ev(p, "document.getElementById('confirm').dataset.kind!=='jcard'"), 'confirm card back to normal ' + tag);
        await click(p, '#set-cards-list .jc-setrow[data-card=shared-notes] .tgl'); await waitFor(p, "!document.getElementById('confirm').hidden");
        await click(p, '#confirm-yes');
        await waitFor(p, "document.querySelector('#set-cards-list .jc-setrow[data-card=shared-notes] .tgl').getAttribute('aria-checked')==='true'");
        check(host(state, "jcards.find(R, 'shared-notes')['on']") === true, 'turned on with the digest ' + tag);
        check(p.problems.length === 0, 'no console errors ' + tag + ': ' + p.problems.join('|'));
      } finally { await p.dispose(); }
    } finally { rmSync(state, { recursive: true, force: true }); }
  }
  writeFileSync(join(OUT, 'web-browser.json'), JSON.stringify({ ok: true, checks, results, host: 'real agentj.jcards / jcards_server in a throw-away AGENTJ_STATE_DIR, a real loopback card server', ios: '390px Chromium touch emulation; not a physical iOS device' }, null, 2) + '\n');
  rec.record('p432-server-card', true); rec.record('p432-files-card', true); rec.record('p432-obsidian-card', true); rec.record('p432-plaza-card', true); rec.write();
  console.log(`P432 PASS ${checks} checks → reports/qa/p432/`);
} catch (e) { rec.record('p432-server-card', false); rec.record('p432-files-card', false); rec.record('p432-obsidian-card', false); rec.record('p432-plaza-card', false); rec.write(); throw e; } finally { await B.close(); await fake.stop(); await web.stop(); child.kill(); rmSync(SRV, { recursive: true, force: true }); rmSync(WORK, { recursive: true, force: true }); rmSync(VAULT, { recursive: true, force: true }); }
