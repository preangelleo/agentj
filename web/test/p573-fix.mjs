#!/usr/bin/env node
// P573 (#386, chairman 10-29: 「全屏阅读版式可以」; item 3: 「点一下」 tier): one full-screen reader. A Markdown report the reply
// sent as a file opens in the reply's own reader (#rd, file mode): the same Noir page, md.js rendering, type size, footer icons
// and select-to-quote; the header is the file name with 下载 as a thin icon (no × — the book icon, Back and Esc leave).
// 4b: the file reader inherits every reply-card action the file card has (下载 · 分享 · Telegram · 邮件 · 复制 · 回复 ·
// select-to-quote) and each works. ⑫: a new message while any reader is open (reply reader, file reader, picture viewer)
// neither closes nor repaints it. The key pickup card's full screen has two thin icons (复制 · 返回) instead of text buttons.
// Approval card: 批准 / 批量批准 answer on one tap (no hold); 拒绝 one tap; a finger that slides off answers nothing.
// The real web client against fakehost.mjs, own headless Chromium (never :9222). 390 × 844 light / dark × zh / en, + 1440 light zh.
//   P573_OUT=<dir> P573_TAG=approved|after node p573.mjs
// The same file runs against the step-1 tree for the 「approved」 shots; the checks run on `after` only.
import assert from 'node:assert/strict';
import { generateKeyPairSync } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { createHash, randomBytes } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, rect } from './browser.mjs';

const OUT = process.env.P573_OUT || new URL('../../../reports/qa/p573/', import.meta.url).pathname;
const TAG = process.env.P573_TAG || 'after';
mkdirSync(OUT, { recursive: true });
const UA = 'Mozilla/5.0 (Linux; Android 14; PJZ110) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36';
// Synthetic report (no real names, keys, paths): a heading, a paragraph, a bold label, a numbered and a bulleted list, a
// link, inline code, a code block and a table — every Markdown piece the owner compared.
const MD = {
  zh: `# 本地小模型替代调研（合成样例）

董事长原话：用户没有官方 API key 时，用本地几百 MB 的小模型兜底；文本不离开本机。

**结论**

1. 通用小模型不行：0.6B 误报 13/20，1.7B 误报 9/20，调阈值几乎无效。
2. 查密钥用 \`credsweeper\`：召回 16/20、误报 0，43 条 0.42 秒。
3. 隐私判断仍走云端审阅，本地只做第一道筛。

- 安装包增加约 12 MB
- 详见 [调研方法](https://example.com/method)

\`\`\`bash
agentj check --local --threshold 0.95
\`\`\`

| 方案 | 召回 | 误报 |
|---|---|---|
| 0.6B | 7/20 | 13/20 |
| CredSweeper | 16/20 | 0 |
`,
  en: `# Local small-model survey (synthetic sample)

The owner's words: when a user has no official API key, a local model of a few hundred MB fills in; text never leaves the machine.

**Conclusion**

1. General small models fail: 0.6B 13/20 false alarms, 1.7B 9/20; thresholds barely help.
2. Secrets: \`credsweeper\` finds 16/20 with 0 false alarms, 43 lines in 0.42 s.
3. Privacy calls stay with the cloud reviewer; the local model is the first sieve.

- The install grows by about 12 MB
- See [the method](https://example.com/method)

\`\`\`bash
agentj check --local --threshold 0.95
\`\`\`

| Option | Found | False |
|---|---|---|
| 0.6B | 7/20 | 13/20 |
| CredSweeper | 16/20 | 0 |
`,
};
const NOTE = { zh: '报告写好了，见下面的文件。', en: 'The report is ready — the file is below.' };
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(process.env.P573_PUBLIC ? { publicDir: process.env.P573_PUBLIC } : {}) });
web.url = web.url.replace('127.0.0.1', 'localhost');
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
let checks = 0;
const check = (v, label) => { if (TAG !== 'after') return; assert.ok(v, label); checks++; };
const results = {};
const bytes = new Map();

async function paired(w, h, scheme, lang, { caps = [], tgfwd = false, init = '' } = {}) {
  fake.reset(); bytes.clear();
  fake.st.extraCaps = caps; fake.st.tgfwd = tgfwd;
  fake.st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    const b = bytes.get(m.mid);
    for (let i = 0; i < 8; i++) { const o = m.o + i * 45056; if (o >= b.length) break; const d = b.subarray(o, o + 45056); await send({ t: 'media_chunk', mid: m.mid, o, d: d.toString('base64url'), last: o + d.length >= b.length }); }
    return true;
  };
  const p = await newPage(B, w, h, scheme, { ua: w < 500 ? UA : undefined, touch: w < 500, allow: [web.url, fake.relay + '/'] });
  if (init) await p.send('Page.addScriptToEvaluateOnNewDocument', { source: init });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '?lang=zh')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `for (const id of ['phone-env-dismiss','a2hs-ok','confirm-no']) { const b = document.getElementById(id); if (b && b.offsetParent) b.click(); }`);
  await sleep(300);
  return p;
}
async function fileTurn(p, lang) {
  const data = Buffer.from(MD[lang]), mid = randomBytes(11).toString('hex').slice(0, 22), name = lang === 'en' ? 'local-model-survey.md' : '本地小模型调研.md';
  bytes.set(mid, data);
  const turn = await fake.addTurn({ k: 'phone', dev: null, text: lang === 'en' ? 'Write up the survey' : '把调研写成报告' }, NOTE[lang], 'done');
  await fake.updateTurn(turn.id, { media: [{ mid, name, mime: 'text/markdown', kind: 'file', bytes: data.length, sha256: createHash('sha256').update(data).digest('hex'), ref: 'out/' + name }] });
  const card = `#mstrip .mcard[data-mid="${mid}"]`;
  await waitFor(p, `!!document.querySelector(${JSON.stringify(card)})`).catch(async (e) => {
    console.error('debug', await ev(p, `JSON.stringify({pg: document.getElementById('pg').textContent, words: document.getElementById('words').textContent.slice(0, 80), strip: document.getElementById('mstrip')?.outerHTML.slice(0, 300), rd: document.getElementById('rd').hidden})`), p.problems.slice(0, 5));
    throw e;
  });
  await ev(p, `document.querySelector(${JSON.stringify(card)}).scrollIntoView({ block: 'center' })`);
  await waitFor(p, `document.querySelector(${JSON.stringify(card)})?.dataset.state === 'ready'`);
  return { card, name };
}
const openFile = async (p, card) => {
  await ev(p, `document.querySelector(${JSON.stringify(card + ' .pv')}).click()`);
  await waitFor(p, `(!document.getElementById('rd').hidden && document.getElementById('rd').dataset.file === '1' && !!document.querySelector('#rdWords ol'))
    || (!!document.querySelector('.mview:not([hidden]) .mdoc .words ol'))`);
  await sleep(350);
};
const readerShown = (p) => ev(p, `(() => { const rd = document.getElementById('rd'), mv = document.querySelector('.mview');
  return { rd: !rd.hidden, file: rd.dataset.file === '1', mview: !!mv && !mv.hidden }; })()`);

try {
 for (const lang of ['zh','en']) {
  const p=await paired(390,844,'light',lang);
  const scope=JSON.stringify({task:'synthetic-task',actions:[{tool:'Bash',input_sha256:'a'.repeat(64),class:'write'},{tool:'Bash',input_sha256:'b'.repeat(64),class:'write'}]});
  const summary=lang==='zh' ? '本次批准的全部动作：\n1. 创建合成空目录 A\n2. 创建合成空目录 B' : 'All actions in this approval:\n1. Create synthetic empty folder A\n2. Create synthetic empty folder B';
  const id=randomBytes(16).toString('hex'); fake.st.holdAnswers=true;
  await fake.ask({id,tool:'Bash',summary,ttl:120,task_scope:scope,task_grant:lang==='zh'?'仅授权以上两项操作。':'Only these two actions are authorized.'});
  await waitFor(p,`document.body.dataset.sheet === '1'`);await sleep(500);
  await ev(p,`if(!document.getElementById('cmdMore').hidden)document.getElementById('cmdMore').click()`);
  writeFileSync(`${OUT}/task-plan-${lang}.png`,await shoot(p,''));
  const a=await rect(p,'#apprAllow');await p.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:a.x,y:a.y}]});await p.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await sleep(500);
  check(fake.st.sigOk.at(-1)===true,`${lang}: concrete task plan is signed`);
  await fake.askDone(id,'allow');await sleep(500);
  fake.st.onApp=async(c,m,send)=>{
   if(m.t==='keys_get'){await send({t:'keys',r:m.r,id:'1'.repeat(32),n:'2'.repeat(32),button_ops:true,base:[],third:[{name:'TEST_REPORT_KEY',set:false,found:[{src:'s1234567890123456',fp:'a'.repeat(16)}]}],src:[{id:'s1234567890123456',kind:'file',label:'Synthetic source'}]});return true;}
   if(m.t==='keys_set'){await send({t:'keys_res',r:m.r,ok:true,card:{t:'elev',kind:'secret',name:'TEST_REPORT_KEY',purpose:lang==='zh'?'保存合成样例':'Save synthetic sample',dest:'Synthetic key file',verify:'',id:randomBytes(16).toString('hex'),n:randomBytes(16).toString('hex'),epk:generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x,ttl:120,passkey_required:false,button_only:true}});return true;}
   return false;
  };
  await ev(p,`document.getElementById('models-view').hidden=false;import('./js/keys.js').then(m=>m.load(document.getElementById('keys-box')))`);
  await waitFor(p,`!!document.querySelector('#keys-box .kimp')`);
  await ev(p,`document.querySelector('#keys-box .kimp').click()`);
  await waitFor(p,`!document.getElementById('confirm').hidden`);
  writeFileSync(`${OUT}/keys-import-${lang}.png`,await shoot(p,''));
  check(await ev(p,`document.getElementById('confirm-pk').hidden`),`${lang}: import is a button without Face ID`);
  await ev(p,`document.getElementById('confirm-no').click();document.querySelector('#keys-box [data-op="set"]').click()`);
  await waitFor(p,`!!document.querySelector('.elev:not([hidden])')`);
  writeFileSync(`${OUT}/keys-edit-${lang}.png`,await shoot(p,''));
  check(await ev(p,`!document.querySelector('.elev:not([hidden])').textContent.includes('Face ID')`),`${lang}: edit is a button`);
  await p.dispose();
 }
 console.log(`p573 fix supplementary: ${checks} checks passed`);
} finally {await B.close();await web.stop();await fake.stop?.();}
process.exit(0);
