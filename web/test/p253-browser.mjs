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
const OUT = ROOT + 'reports/qa/p253/browser/';
mkdirSync(OUT, { recursive: true });
const rec = parityRecorder('web/test/p253-browser.mjs');

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

const CMD = '/feedback-to-agentj-father';
try {
await C('father-menu-group-fill-and-send-all-harnesses', async () => {
  for (const lang of ['zh','en']) for (const kind of KINDS) {
    const hostMenu=withVersion(answers[`config|${kind}|${lang}`],'0.20.3a1');
    if (kind==='opencode') hostMenu.items=Array.from({length:60},(_,i)=>({cmd:`/own-${i}`,desc:'Own command'})).concat(hostMenu.items.filter(x=>['/switch-harness',CMD].includes(x.cmd)));
    const p = await paired({lang,kind,menu:hostMenu});
    try {
      await openMenu(p);
      const rows=await listing(p);
      assert.equal(rows.filter(r=>r[1]===CMD).length,1);
      assert.equal(groupOf(rows,CMD), lang==='zh' ? '工具' : 'Tools');
      const entry=`[...document.querySelectorAll('#menuList button')].find(b=>b.dataset.slash===${JSON.stringify(CMD)})`;
      await ev(p,`${entry}.scrollIntoView({block:'center'})`);
      writeFileSync(`${OUT}${lang}-${kind}-menu.png`,await shoot(p,''));
      const before=fake.st.says.length;
      await ev(p,`${entry}.click()`);
      assert.equal(fake.st.says.length,before,'tap fills, owner still chooses content before sending');
      assert.equal((await ev(p,`${$('input')}.value`)).trim(),CMD);
      await ev(p,`${$('input')}.value += 'Please add a shortcut'; ${$('input')}.dispatchEvent(new Event('input')); ${$('send')}.click()`);
      await waitFor(p,`${$('input')}.value === ''`);
      await sleep(200);
      assert.equal(fake.st.says.at(-1).text.trim(),CMD+' Please add a shortcut');
      noProblems(p);
    } finally {await p.dispose();}
  }
});
} finally {
 rec.write();await B.close();await fake.stop();await web.stop();
}
console.log(failed ? `P253: ${failed} failed` : 'P253: all browser cases passed');
process.exit(failed?1:0);
