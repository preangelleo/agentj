#!/usr/bin/env node
// P341 (backlog #19): the goal-result system card from 10-16 (commands with $(…) / $HOME) and an approval card whose
// Agent sent no description. The real web client against fakehost.mjs, own headless Chromium (never :9222); 390 × 844,
// zh / en, light / dark. `node test/p341.mjs before` = shots only (run from a tree without P341); no tag = shots + checks.
//   → reports/qa/p341/<tag>/<shot>-<w>-<scheme>-<lang>.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const tag = process.argv[2] || 'after', CHECK = tag === 'after';
const OUT = process.env.P341_OUT || fileURLToPath(new URL(`../../reports/qa/p341/${tag}/`, import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
let checks = 0;
const check = (v, label) => { if (!CHECK) return; assert.ok(v, label); checks++; };
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };
const VIEWS = [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'zh'], [390, 844, 'light', 'en'], [390, 844, 'dark', 'en']];
// the 10-16 card (FEEDBACK-BACKLOG #19 / #43), as goal.result_text writes it — before P341 without the code spans
const CMD1 = 'env -i HOME=$HOME PATH=$PATH sh -c "cd $(ls -d ~/work/app/.worktrees/p3*) && make release"';
const CMD2 = "df -h /dev/shm /tmp | tail -2; cat > ~/bench-jarvis/pwone.py <<'EOF'";
const quote = (s) => (tag === 'after' ? '`' + s + '`' : s);
const RESULT = {
  zh: `🎯 目标结束 · 未达成：今晚发版\n用到的授权：无\n自动批准 6 次（其中 10 秒倒计时 1 次）\n卡在这里，等你决定：\n· ${quote(CMD1)} — 不在授权范围内，且无法撤回\n· ${quote(CMD2)} — 不在授权范围内，且无法撤回`,
  en: `🎯 Goal ended — not achieved: ship tonight\nGrants used: none\nApproved automatically: 6 (10-second countdown 1)\nWaiting for your decision:\n· ${quote(CMD1)} — outside the grants and cannot be undone\n· ${quote(CMD2)} — outside the grants and cannot be undone`,
};
const MATH = { zh: '面积公式是 $A=\\pi r^2$；复制命令：cp $(ls -d build/*) $HOME/out', en: 'The area is $A=\\pi r^2$; to copy: cp $(ls -d build/*) $HOME/out' };
const SCP = 'scp -q ~/bench-jarvis/pwone.py olares:bench-jarvis/pwone.py && echo copied';

async function paired(w, h, scheme, lang) {
  fake.reset();
  fake.st.prefs = structuredClone(PREFS);
  fake.st.prefs.appearance.language = lang;
  const p = await newPage(B, w, h, scheme, { touch: w < 500, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
  await sleep(250);
  return p;
}
const name = (shot, w, scheme, lang) => `${OUT}/${shot}-${w}-${scheme}-${lang}.png`;
const snap = async (p, file) => writeFileSync(file, await shoot(p, file));
const words = (p) => ev(p, `(() => { const w = document.getElementById('words'); return { text: w.textContent, math: w.querySelectorAll('.math').length, code: [...w.querySelectorAll('code')].map(c => c.textContent) }; })()`);

try {
  for (const [w, h, scheme, lang] of VIEWS) {
    // 1) the system card: a host-written goal result
    let p = await paired(w, h, scheme, lang);
    await fake.addTurn({ k: 'sys', text: '' }, RESULT[lang], 'done');
    await waitFor(p, `document.getElementById('words').textContent.includes('pwone.py')`, 8000);
    await sleep(1500);                                   // render.js upgrades marks after the page settles
    let r = await words(p);
    check(r.math === 0, `${lang} ${scheme}: no TeX on the system card`);
    check(r.text.includes('$HOME') && r.text.includes('$(ls -d'), `${lang}: $HOME / $(…) shown as written`);
    check(r.code.some((c) => c.includes('$(ls -d ~/work')), `${lang}: the blocked command is a code span`);
    await snap(p, name('sys-goal-result', w, scheme, lang));
    // 2) an Agent reply: real maths still renders, a shell line beside it does not
    await fake.addTurn({ k: 'agent' }, MATH[lang], 'done');
    await waitFor(p, `document.getElementById('words').textContent.includes('build/*')`, 8000);
    await sleep(1500);
    r = await words(p);
    check(r.math === 1, `${lang}: one formula in the Agent's reply (the shell line is text)`);
    check(r.text.includes('$(ls -d build/*) $HOME/out'), `${lang}: the shell line stays as written`);
    await snap(p, name('agent-math', w, scheme, lang));
    await p.close?.();
    // 3) an approval card without the Agent's description: one plain line above the folded command
    p = await paired(w, h, scheme, lang);
    await fake.send({ t: 'status', s: 'waiting', agent: 'codex', name: 'Agent J' });
    await fake.ask({ id: randomBytes(16).toString('hex'), tool: 'Bash', summary: SCP, ttl: 120 });
    await waitFor(p, `document.getElementById('cmd').textContent.includes('olares')`, 8000);
    await sleep(500);
    const d = await ev(p, `(() => { const e = document.getElementById('cmdDesc'); return { hidden: e.hidden, text: e.textContent, gist: e.classList.contains('gist'), cmd: document.getElementById('cmd').textContent }; })()`);
    check(!d.hidden && d.gist, `${lang}: the gist line shows`);
    check(d.text === (lang === 'zh' ? '把文件复制到另一台电脑，后面还有 1 步' : 'Copy files to another computer, then one more step'), `${lang}: gist text ${d.text}`);
    check(d.cmd === SCP, `${lang}: the command itself is unchanged`);
    await snap(p, name('approval-gist', w, scheme, lang));
    await p.close?.();
    // 4) #50: /new in the 「/」 list, and sending it is /clear (same confirm sheet, the host gets "clear")
    if (tag !== 'after') continue;
    p = await paired(w, h, scheme, lang);
    await ev(p, `(() => { const i = document.getElementById('input'); i.focus(); i.value = '/ne'; i.dispatchEvent(new Event('input')); })()`);
    await waitFor(p, `!document.getElementById('sug').hidden && [...document.querySelectorAll('#sug b')].some(b => b.textContent === '/new')`, 5000);
    const sug = await ev(p, `document.getElementById('sug').textContent`);
    check(sug.includes(lang === 'zh' ? '同 /clear：开新对话' : 'Same as /clear: start a new conversation'), `${lang}: /new row says what it is`);
    await snap(p, name('slash-new', w, scheme, lang));
    await ev(p, `(() => { const i = document.getElementById('input'); i.value = '/new'; i.dispatchEvent(new Event('input')); document.getElementById('send').click(); })()`);
    await waitFor(p, `!document.getElementById('confirm').hidden`, 5000);
    await snap(p, name('slash-new-confirm', w, scheme, lang));
    const n0 = (fake.st.slashes || []).length;
    await ev(p, `document.getElementById('confirm-yes').click()`);
    for (let i = 0; i < 50 && (fake.st.slashes || []).length === n0; i++) await sleep(100);
    const sl = (fake.st.slashes || []).at(-1) || {};
    check(sl.cmd === 'clear' && sl.confirm === true, `${lang}: /new reached the host as a confirmed /clear (${JSON.stringify(sl)})`);
    check(!(fake.st.says || []).some((x) => /^\/new/.test(x.text || '')), `${lang}: /new never went to the Agent as text`);
    await p.close?.();
  }
  if (CHECK) console.log(`p341: ${checks} checks pass, shots in ${OUT}`);
  else console.log(`p341 ${tag}: shots in ${OUT}`);
} finally {
  await B.close?.();
  await fake.close?.();
  await web.close?.();
}
process.exit(0);
