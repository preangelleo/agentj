#!/usr/bin/env node
// P238 · the phone's 「/」 list always offers /switch-harness (ADR-P155) to a paired owner.
// Real page + local fake host (own headless Chromium, never :9222). The host's answers are made by the REAL Python
// `menu.for_phone` for every combination: session mode (shared / independent) × harness (claude / codex / opencode) ×
// own commands (none / menu.json / `agentj menu add` / both) — plus the buggy 0.20.1a1 answer (only /good-morning, source
// config) and a pre-0.18.6 host that does not know the command.
//   a) a 0.20.3 host, every combination: /switch-harness exactly once, in Agent J's one-tap group (一键执行 / Run now) right
//      after /customize, with the page's sentence, never again under 主 Agent; the owner's own commands still listed
//   b) a 0.20.1a1 host whose answer lost it: the page still offers it (the host knows the command since 0.18.6)
//   c) a 0.18.5 host: not offered (the words would only reach the Agent as text)
//   d) one tap sends 「/switch-harness」 to the Agent J host as typed (serve hands it to the main Agent: list → choose → probe →
//      switch / switch back), with a toast; typed + Send does the same
//   e) typing 「/sw」 finds it in the type-ahead
// Shots (zh/en × 390/1440) → reports/qa/p238/*.png
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p238/';
mkdirSync(OUT, { recursive: true });
const rec = parityRecorder('web/test/p238.mjs');

// the host's own answers (json5 lives in the host venv; a worktree reuses the main checkout's)
const common = dirname(resolve(ROOT, execFileSync('git', ['rev-parse', '--git-common-dir'], { cwd: ROOT }).toString().trim()));
const PY = [process.env.AJ_HOST_PY, ROOT + 'host/.venv/bin/python', common + '/host/.venv/bin/python']
  .find((x) => x && existsSync(x));
assert.ok(PY, 'host venv python');
const MODES = ['shared', 'independent'], KINDS = ['claude', 'codex', 'opencode'], OWN = ['none', 'file', 'config', 'both'];
const answers = JSON.parse(execFileSync(PY, ['-c', `
import json, os, pathlib, sys, tempfile
sys.path.insert(0, ${JSON.stringify(ROOT + 'agentjarvis/host')})
from agentj import menu
out = {}
GM = {"id": "good-morning", "cmd": "/good-morning", "desc": "早安例行 / Morning routine"}
with tempfile.TemporaryDirectory() as d:
    for own in ${JSON.stringify(OWN)}:
        wd = pathlib.Path(d) / own
        (wd / ".agentj").mkdir(parents=True)
        if own in ("file", "both"):
            (wd / ".agentj" / "menu.json").write_text(json.dumps({"version": 1, "items": [{"cmd": "/deploy", "desc": "上线 / Deploy"}]}))
        for kind in ${JSON.stringify(KINDS)}:
            for lang in ("zh", "en"):
                skills = ["review"] if kind == "claude" else []
                out[f"{own}|{kind}|{lang}"] = menu.for_phone(str(wd), skills, lang, [GM] if own in ("config", "both") else [])
print(json.dumps(out, ensure_ascii=False))
`], { env: { ...process.env, AGENTJ_SKILL_LINK: 'off' } }).toString());

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const $ = (id) => `document.getElementById(${JSON.stringify(id)})`;
const SWITCH = { zh: '换 AI 编程工具：列出已装的，试通才切，不通切回',
  en: 'Switch AI coding tool: tested before switching' };
const RUN_NOW = { zh: '一键执行', en: 'Run now' };
const SENT = { zh: '已交给 Agent J：它会先列出能切换的 AI 编程工具，你选了才切', en: 'Sent: Agent J first lists the AI coding tools you can switch to; nothing switches until you choose' };

async function paired({ w = 390, h = 844, lang = 'zh', kind = 'claude', menu }) {
  fake.reset();
  fake.st.menu = menu;
  const p = await newPage(B, w, h, 'light', { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await p.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await ev(p, `(()=>{${$('phone-env-dismiss')}?.click(); const n=${$('confirm-no')}; if(n&&!${$('confirm')}.hidden)n.click(); const a=${$('a2hs-ok')}; if(a&&!${$('a2hs')}.hidden)a.click();})()`);
  await fake.send({ t: 'status', s: 'idle', agent: kind, name: 'Agent J' });
  await sleep(200);
  return p;
}
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };
async function openMenu(p) {
  await ev(p, `${$('slashBtn')}.click()`);
  await waitFor(p, `!${$('menu')}.hidden`);
  await sleep(400);                                    // the host's answer re-renders the list
}
/** [[kind, text, desc]] of the open list: kind = 'g' group header / 'b' entry. */
const listing = (p) => ev(p, `[...${$('menuList')}.children].map((n) => n.classList.contains('menugroup') ? ['g', n.textContent, ''] : ['b', n.querySelector('b')?.textContent || '', n.querySelector('span')?.textContent || ''])`);
function groupOf(rows, cmd) {
  let g = null;
  for (const [k, t] of rows) { if (k === 'g') g = t; else if (t === cmd) return g; }
  return undefined;
}
async function shotEntry(p) {
  await ev(p, `${$('switchEntry')}.scrollIntoView({block: 'center'})`);
  await sleep(150);
  return shoot(p, '');
}
const withVersion = (m, v) => ({ ...m, upgrade: { current: v, latest: v } });

await C('every-combination-lists-switch-harness-in-agent-j-group', async () => {
  let n = 0;
  for (const lang of ['zh', 'en']) for (const kind of KINDS) for (const own of OWN) for (const mode of MODES) {
    const menu = withVersion(answers[`${own}|${kind}|${lang}`], '0.20.3a1');
    const p = await paired({ lang, kind, menu });
    await openMenu(p);
    const rows = await listing(p);
    const sw = rows.filter((r) => r[0] === 'b' && r[1] === '/switch-harness');
    assert.equal(sw.length, 1, `${mode}/${kind}/${own}/${lang}: once — ${JSON.stringify(rows)}`);
    assert.equal(sw[0][2], SWITCH[lang]);
    assert.equal(groupOf(rows, '/switch-harness'), RUN_NOW[lang], 'in Agent J\'s own group');
    const after = rows.findIndex((r) => r[1] === '/customize');
    assert.equal(rows[after + 1][1], '/switch-harness', 'right after /customize');
    const cmds = rows.filter((r) => r[0] === 'b').map((r) => r[1]);
    if (own === 'config' || own === 'both') assert.ok(cmds.includes('/good-morning'), 'own command kept');
    if (own === 'file') assert.ok(cmds.includes('/deploy'));
    assert.ok(cmds.includes('/goal'));
    if (mode === 'shared' && kind === 'claude' && (own === 'config' || own === 'none'))
      writeFileSync(`${OUT}${lang}-390-${kind}-${own}-menu.png`, await shotEntry(p));
    noProblems(p);
    await p.dispose();
    n++;
  }
  assert.equal(n, 2 * KINDS.length * OWN.length * MODES.length);
});

await C('buggy-0.20.1-host-answer-still-offers-it', async () => {
  for (const lang of ['zh', 'en']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    // exactly what 0.20.1a1 sent Leo's phone: the one owner command, source config, no /switch-harness, no /goal
    const menu = { source: 'config', items: [{ cmd: '/good-morning', desc: '早安例行 / Morning routine' }], skills: [{ cmd: '/review', desc: '' }],
      cmds: ['compact', 'clear', 'model', 'context', 'cost', 'usage', 'status', 'help', 'stop', 'update'], upgrade: { current: '0.20.1a1', latest: '0.20.1a1' } };
    const p = await paired({ w, h, lang, menu });
    await openMenu(p);
    const rows = await listing(p);
    assert.equal(rows.filter((r) => r[1] === '/switch-harness').length, 1, JSON.stringify(rows));
    assert.equal(groupOf(rows, '/switch-harness'), RUN_NOW[lang]);
    writeFileSync(`${OUT}${lang}-${w}-host-0.20.1-menu.png`, await shotEntry(p));
    noProblems(p);
    await p.dispose();
  }
});

await C('pre-0.18.6-host-not-offered', async () => {
  const menu = { source: 'default', items: [{ cmd: '/goal', desc: 'x', group: '命令' }], skills: [], cmds: [], upgrade: { current: '0.18.5a1', latest: '0.20.3a1' } };
  const p = await paired({ menu });
  await openMenu(p);
  const rows = await listing(p);
  assert.equal(rows.filter((r) => r[1] === '/switch-harness').length, 0);
  assert.equal(await ev(p, `!!${$('switchEntry')}`), false);
  const p2 = await paired({ menu: { ...menu, upgrade: undefined } });  // no version at all: the same
  await openMenu(p2);
  assert.equal((await listing(p2)).filter((r) => r[1] === '/switch-harness').length, 0);
  noProblems(p); noProblems(p2);
  await p.dispose(); await p2.dispose();
});

await C('tap-sends-and-typed-send-delivers', async () => {
  for (const lang of ['zh', 'en']) {
    const p = await paired({ lang, menu: withVersion(answers[`config|codex|${lang}`], '0.20.3a1') });
    await openMenu(p);
    const n0 = fake.st.says.length;
    await ev(p, `${$('switchEntry')}.click()`);
    await waitFor(p, `${$('menu')}.hidden`);
    await sleep(400);
    assert.deepEqual(fake.st.says.slice(n0).map((m) => (m.text || '').trim()), ['/switch-harness'], 'one tap sends it as typed');
    assert.equal(await ev(p, `${$('input')}.value`), '', 'nothing left in the field');
    assert.equal(await ev(p, `document.body.textContent.includes(${JSON.stringify(SENT[lang])})`), true, 'the toast says what happens next');
    if (lang === 'zh') writeFileSync(`${OUT}zh-390-after-tap.png`, await shoot(p, ''));
    // typed (owner words after it are kept) + Send: the same path
    await ev(p, `(() => { const i = ${$('input')}; i.focus(); i.value = '/switch-harness 切到 codex'; i.dispatchEvent(new Event('input')); })()`);
    await ev(p, `${$('send')}.click()`);
    await sleep(400);
    assert.equal(fake.st.says.at(-1).text.trim(), '/switch-harness 切到 codex');
    noProblems(p);
    await p.dispose();
  }
});

await C('type-ahead-finds-it', async () => {
  for (const lang of ['zh', 'en']) {
    const p = await paired({ lang, menu: withVersion(answers[`config|opencode|${lang}`], '0.20.3a1') });
    await openMenu(p);
    await ev(p, `${$('slashBtn')}.click()`);                     // close; the host menu is loaded
    await ev(p, `(() => { const i = ${$('input')}; i.focus(); i.value = '/sw'; i.dispatchEvent(new Event('input')); })()`);
    await waitFor(p, `!${$('sug')}.hidden && /switch-harness/.test(${$('sug')}.textContent)`);
    const first = await ev(p, `${$('sug')}.children[0].querySelector('b')?.textContent || ${$('sug')}.children[0].textContent`);
    assert.match(first, /switch-harness/);
    if (lang === 'zh') writeFileSync(`${OUT}zh-390-typeahead.png`, await shoot(p, ''));
    noProblems(p);
    await p.dispose();
  }
});

writeFileSync(OUT + 'host-answers.json', JSON.stringify(answers, null, 1));
rec.write();
await B.close(); await fake.stop(); await web.stop();
console.log(failed ? `P238: ${failed} case(s) failed` : 'P238: all cases passed');
process.exit(failed ? 1 : 0);
