// P281 (owner, 2026-10-14, Mac web page during /switch-harness): 「◆?」 + three dots on the reply card. Real page + local fake
// host, independent headless Chromium (never :9222), zh, 390×844 (touch) and 1440×900, light and dark:
//   wait        open page, Agent working, no words → the dancing robot + 「董事长，您的最新指示已经收到…」 (no dots anywhere)
//   progress    open page with the host's /switch-harness line (card.progress), no words → robot + 「正在探测 Codex（2/3）…」
//   under       words streaming + progress → the words, then the robot line with the progress under them
//   lone        words streaming, no progress → the words, then the robot alone
//   switching   the host's own page (card.switch) while Agent J restarts → robot + 「正在切换到 OpenCode…」
//   fffd        the owner's stored page 「�\n\n董事长，…」 → no U+FFFD, no lone surrogate, no robot (a finished reply)
// Every robot: the factory sheet, dancing (wb-dance), as tall as 1.3× the text beside it. Shots → reports/qa/p281/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p281');
const NAME = 'Jarvis Mac';
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };
const RUNS = [['light', 390, 844, true], ['dark', 390, 844, true], ['light', 1440, 900, false], ['dark', 1440, 900, false]];
const PROGRESS = '正在探测 Codex（2/3），每个最多 1 分钟…';
const ANSWER = '董事长，先用大白话说一下：harness 就是这台Agent J 主机上真正在思考、在干活的那个 AI 程序，可以是 Claude Code、Codex 或 OpenCode。';

const look = (p) => evaluate(p, `(()=>{const w=document.getElementById('words'),th=document.getElementById('thinking');
  const vis=(e)=>!!e&&getComputedStyle(e).display!=='none'&&e.getBoundingClientRect().height>0;
  const bot=(e)=>{if(!e)return null;const cs=getComputedStyle(e),r=e.getBoundingClientRect(),host=e.parentElement;
    return {anim:cs.animationName,img:cs.backgroundImage.includes('robots-12.png'),h:Math.round(r.height),fs:parseFloat(getComputedStyle(host).fontSize),
      first:host.firstChild===e,top:Math.round(r.top)}};
  return {text:w.textContent,html:w.innerHTML.length,fffd:/\\ufffd|[\\ud800-\\udbff](?![\\udc00-\\udfff])/.test(w.textContent),
    wordsBot:bot(w.querySelector('.wbot')),wordsBots:w.querySelectorAll('.wbot').length,
    thinkVis:vis(th),thinkBot:vis(th)?bot(th.querySelector('.wbot')):null,thinkText:th.textContent,
    waitLine:document.body.dataset.waitLine,dots:document.querySelectorAll('.thinking i:not(.wbot),.wbot.wdots').length}})()`);

export async function runP281() {
  mkdirSync(OUT, { recursive: true });
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const results = [];
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const robotOk = (b, where, tag) => {
    check(b && b.anim === 'wb-dance' && b.img, `${where}: a dancing factory robot ${tag} ${JSON.stringify(b)}`);
    check(Math.abs(b.h - 1.3 * b.fs) <= 1.5, `${where}: robot ${b.h}px ≈ 1.3 × text ${b.fs}px ${tag}`);
  };
  try {
    for (const [theme, w, h, touch] of RUNS) {
      fake.reset();
      fake.st.prefs = structuredClone({ ...PREFS, appearance: { language: 'zh', theme } });
      const p = await newPage(B, w, h, theme, { touch, allow: [web.url, fake.relay + '/'] });
      const tag = `${theme}-${w}`;
      const snap = async (name, extra = {}) => { const l = await look(p); results.push({ tag, name, ...l, ...extra }); writeFileSync(join(OUT, `${name}-zh-${tag}.png`), await shoot(p, '')); return l; };
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
        await fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 30, max: 100 }, h5: { pct: 19, reset: 0 }, week: { pct: 90, reset: 0 }, at: 0 });

        // fffd: the owner's stored page, as the Mac host kept it
        await fake.addTurn({ k: 'phone', dev: 'other', text: '/switch-harness' }, '�\n\n' + ANSWER, 'done');
        await waitFor(p, `document.getElementById('words').textContent.includes('先用大白话')`);
        let l = await snap('fffd');
        check(!l.fffd && l.text.startsWith('董事长'), `fffd: the page starts with its words ${tag}: ${JSON.stringify(l.text.slice(0, 12))}`);
        check(l.wordsBots === 0 && l.waitLine === '0', `fffd: a finished reply has no robot ${tag}`);

        // wait: a new open page, working, no words
        const turn = await fake.addTurn({ k: 'phone', dev: 'other', text: '/switch-harness' }, '', 'open');
        await fake.send({ t: 'status', s: 'working', agent: 'claude', name: NAME });
        await evaluate(p, `document.getElementById('pgNext').click()`);      // another remote's page: go to the newest
        await waitFor(p, `document.body.dataset.status==='working' && document.getElementById('words').textContent.includes('已经收到')`);
        await sleep(300);
        l = await snap('wait');
        check(l.wordsBots === 1 && l.wordsBot.first && l.waitLine === '1', `wait: the robot leads the wait line ${tag}`);
        robotOk(l.wordsBot, 'wait', tag);
        check(!l.thinkVis && l.dots === 0, `wait: no second robot, no dots ${tag}`);

        // progress: the host's /switch-harness line, still no words
        await fake.updateTurn(turn.id, { card: { progress: PROGRESS } });
        await waitFor(p, `document.getElementById('words').textContent===${JSON.stringify(PROGRESS)}`);
        await sleep(200);
        l = await snap('progress');
        check(l.wordsBots === 1 && l.wordsBot.first && !l.thinkVis, `progress: robot + the progress line ${tag}`);
        robotOk(l.wordsBot, 'progress', tag);

        // under: words streamed, the progress goes under them with the robot
        await fake.updateTurn(turn.id, { reply: { text: ANSWER } });
        await waitFor(p, `document.getElementById('words').textContent.includes('先用大白话') && document.getElementById('thinking').textContent.includes('正在探测')`);
        await sleep(200);
        l = await snap('under');
        check(l.wordsBots === 0 && l.thinkVis && l.thinkText === PROGRESS && l.waitLine === '0', `under: words, then robot + progress ${tag}`);
        robotOk(l.thinkBot, 'under', tag);

        // lone: no progress any more → the robot alone under the words
        await fake.updateTurn(turn.id, { card: {} });
        await waitFor(p, `document.getElementById('thinking').textContent===''`);
        await sleep(200);
        l = await snap('lone');
        check(l.thinkVis && l.thinkText === '' && l.wordsBots === 0, `lone: a single robot under the words ${tag}`);
        robotOk(l.thinkBot, 'lone', tag);

        // switching: the turn ended, Agent J restarts — the host's own page
        await fake.updateTurn(turn.id, { end: 'done' });
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
        await fake.addTurn({ k: 'sys' }, '正在切换到 OpenCode：Agent J 重启中，新的 Agent 接上后会先跟您打招呼…', 'done', { switch: 'switching' });
        await evaluate(p, `document.getElementById('pgNext').click()`);
        await waitFor(p, `document.getElementById('words').textContent.includes('正在切换到 OpenCode')`);
        await sleep(200);
        l = await snap('switching');
        check(l.wordsBots === 1 && l.wordsBot.first && l.waitLine === '1', `switching: robot + 正在切换 ${tag}`);
        robotOk(l.wordsBot, 'switching', tag);
        check(!p.problems.length && !p.offsite.length, 'no CSP/network errors ' + JSON.stringify(p.problems.slice(0, 2)));
      } finally { await p.dispose(); }
    }
    writeFileSync(join(OUT, 'results.json'), JSON.stringify({ scope: 'real Chromium + local fake host; not a physical phone', checks, results, complete: true }, null, 2) + '\n');
    console.log(`p281: ${checks} checks passed → ${OUT}`);
  } finally { await B.close(); await fake.stop(); await web.stop(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP281();
