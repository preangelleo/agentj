import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot, key, rect } from './browser.mjs';

const before = process.env.P0378_BEFORE === '1';
const out = process.env.P0378_UI_OUT;
const base = process.env.P0378_PUBLIC;
const guide = 'https://agentj.app/docs/install/#upgrade-failed';
const codeBody = 'agentj update apply';
const reply = {
  zh: '无法启动升级。\n\n[查看升级修复步骤](https://example.com/upgrade)\n\n',
  en: 'The update could not start.\n\n[Read the upgrade repair steps](https://example.com/upgrade)\n\n',
};
const fence = '```';
const profiles = [
  { name: 'light', theme: 'light', wire: { factory: 'robots' } },
  { name: 'dark', theme: 'dark', wire: { factory: 'robots' } },
  { name: 'green', theme: 'dark', wire: { id: 'fixture-green', colors: {
    state: { idle: '#394d3f', working: '#436255', waiting: '#5b6653', question: '#465b48', offline: '#4c574d' },
    dialog: { background: '#394d3f' },
  } } },
];
const states = [['idle', 'idle'], ['working', 'working'], ['waiting', 'waiting'], ['question', 'waiting'], ['unknown', 'unknown']];

async function capture(page, label) {
  if (!out) return;
  await evaluate(page, 'document.fonts.ready.then(()=>true)');
  writeFileSync(join(out, label + '.png'), await shoot(page));
}
async function pair(browser, web, host, width, profile, lang) {
  host.reset();
  host.st.prefs = { appearance: { language: lang, theme: profile.theme }, voice: { speak_replies: false, wake_enabled: false } };
  const url = web.url.replace('127.0.0.1', 'localhost');
  const page = await newPage(browser, width, width === 390 ? 844 : 900, profile.theme, { touch: width === 390, allow: [url, host.relay + '/'] });
  await navigate(page, host.newPairing(url + '?lang=' + lang));
  await waitState(page, 'awaiting-approval');
  await host.approve();
  await waitState(page, 'ready');
  await evaluate(page, "for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b?.offsetParent)b.click()}");
  await host.send({ t: 'theme', v: 1, ...profile.wire });
  return page;
}
async function status(host, page, [label, state]) {
  const question = label === 'question';
  await host.send({ t: 'status', s: state, ...(question ? { kind: 'question' } : {}), name: 'Agent J' });
  await waitFor(page, `document.body.dataset.status===${JSON.stringify(state)} && (document.body.dataset.kind==='question')===${question}`);
}
const selector = id => JSON.stringify(id);
function measure(page, targets) {
  return evaluate(page, `(() => {
    const probes = ${JSON.stringify(targets)};
    const css = node => getComputedStyle(node);
    const read = value => {
      const parts = value.replace(/^color\\(srgb /,'').match(/[0-9.]+/g).map(Number);
      return /^color\\(srgb /.test(value) ? parts.slice(0,3).map(x=>x*255).concat(parts[3]??1) : parts.slice(0,3).concat(parts[3]??1);
    };
    const over = ([r,g,b,a], bg) => [r,g,b].map((x,i)=>x*a+bg[i]*(1-a));
    const linear = x => (x/=255) <= .04045 ? x/12.92 : ((x+.055)/1.055)**2.4;
    const luminance = rgb => rgb.map(linear).reduce((v,x,i)=>v+x*[.2126,.7152,.0722][i],0);
    return Object.fromEntries(Object.entries(probes).map(([name,query]) => {
      const node=document.querySelector(query);
      if(!node) throw Error('probe missing: '+query);
      const layers=[];
      for(let parent=node;parent;parent=parent.parentElement) {
        const fill=read(css(parent).backgroundColor);
        if(fill[3]) layers.push(fill);
        if(fill[3]===1) break;
      }
      const bg=layers.reverse().reduce((back,front)=>over(front,back),[255,255,255]);
      const style=css(node), ink=over(read(style.webkitTextFillColor || style.color),bg);
      const a=luminance(bg), b=luminance(ink);
      return [name, {ink:ink.map(Math.round),bg:bg.map(Math.round),ratio:+((Math.max(a,b)+.05)/(Math.min(a,b)+.05)).toFixed(3),line:style.textDecorationLine}];
    }));
  })()`);
}
function readable(probes, context, step) {
  if (before) return;
  for (const [name, value] of Object.entries(probes)) {
    if (value.ratio >= 4.5) continue;
    console.error('P44-JOURNEY-FAIL ' + JSON.stringify({journey:'card-contrast',step,location:name}));
    assert.fail(`${context} ${name} contrast ${value.ratio}`);
  }
}
async function click(page, query) {
  await evaluate(page, `document.querySelector(${selector(query)}).click()`);
}
async function linkStates(page, tag, query, screenshots) {
  await evaluate(page, `document.querySelector(${selector(query)}).scrollIntoView({block:'center'})`);
  const link = await rect(page, query);
  await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(link.x), y: Math.round(link.y) });
  readable(await measure(page, { hover: query }), tag, 'repair-link-hover');
  if (screenshots) await capture(page, tag + '-hover');
  await key(page, 'Tab');
  await evaluate(page, `document.querySelector(${selector(query)}).focus({preventScroll:true})`);
  assert.equal(await evaluate(page, `document.activeElement===document.querySelector(${selector(query)})`), true);
  readable(await measure(page, { focus: query }), tag, 'repair-link-focus');
  if (screenshots) await capture(page, tag + '-focus');
}
async function replyJourney(page, host, width, profile, lang) {
  const text = reply[lang] + fence + 'sh\n' + codeBody.trimEnd() + '\n' + fence;
  host.st.onApp = async (_c, m) => {
    if (m.t !== 'slash' || m.cmd !== 'update') return false;
    await host.addTurn({ k: 'cmd', text: '/update' }, text, 'done', { cmd: 'update', kind: 'error', ok: false, repair: 'reconnect' });
    return true;
  };
  await evaluate(page, "document.getElementById('input').focus()");
  await page.send('Input.insertText', { text: '/update' });
  await key(page, 'Enter');
  await waitFor(page, `!!document.querySelector('#cmdx a[href="${guide}"]') && !!document.querySelector('#words pre[data-lang=sh] code')`);
  assert.ok(host.log.some(m => m.t === 'slash' && m.cmd === 'update'));
  const tag = `${width}-${lang}-${profile.name}`;
  const targets = {body:'#words p',link:'#words p>a',target:'#words .lhost',code:'#words pre code',guide:'#cmdx>a',stamp:'#rmTime',secondary:'#cmdx .small',details:'#cmdx summary'};
  const rows = {};
  for (const state of states) {
    await status(host, page, state);
    rows[state[0]] = await measure(page, targets);
    readable(rows[state[0]], tag + '-' + state[0], 'upgrade-reply');
    if (state[0] === 'idle' || (width === 390 && lang === 'zh')) await capture(page, tag + '-reply-' + state[0]);
  }
  await status(host, page, states[0]);
  const block = await evaluate(page, `(() => {
    const box=document.querySelector('#words .codeblock'), code=box.querySelector('pre>code');
    return {body:code.textContent, heading:box.querySelector('.bar .lang')?.textContent ?? null,
      copy:!box.querySelector('button.copy').disabled,
      url:document.querySelector('#cmdx>a').href, independent:document.querySelector('#cmdx>a').rel};
  })()`);
  assert.equal(block.body, codeBody, 'the command contains the input body only');
  if (!before) assert.equal(block.heading, null, 'the shell language identifier has no separate visible label');
  assert.equal(block.copy, true);
  assert.equal(block.url, guide);
  assert.match(block.independent, /noopener/);
  if (width === 1440) await linkStates(page, tag + '-repair-guide', '#cmdx>a', lang === 'en');
  if (!before && width === 390 && lang === 'zh') {
    await click(page, '#words .codeblock button.copy');
    await waitFor(page, `navigator.clipboard.readText().then(s=>s===${JSON.stringify(codeBody)})`);
    const codeWithShell = ['sh', '  echo fixture'].join('\n');
    const onlyCode = fence + 'sh\n' + codeWithShell + '\n' + fence;
    const nonShell = fence + 'json\n{}\n' + fence;
    const observed = await evaluate(page, `import('./js/md.js').then(({RelayMD}) => {
      const fragment = RelayMD.toDOM(RelayMD.parse(${JSON.stringify([onlyCode,nonShell].join('\n\n'))}));
      return [...fragment.querySelectorAll('.codeblock')].map(n=>[n.querySelector('.lang')?.textContent??null,n.querySelector('pre>code').textContent]);
    })`);
    assert.deepEqual(observed, [[null, codeWithShell], ['json', '{}']],
      'literal shell text and indentation survive while a non-shell language label is preserved');
  }
  await click(page, '#readBtn');
  await waitFor(page, "!document.getElementById('rd').hidden && !!document.querySelector('#rdWords a')");
  const reader = await measure(page, { body:'#rdWords p',link:'#rdWords p>a',target:'#rdWords .lhost',code:'#rdWords pre code' });
  readable(reader, tag + '-reader', 'reader');
  await capture(page, tag + '-reader');
  await key(page, 'Escape');
  await waitFor(page, "document.getElementById('rd').hidden");
  return { rows, block, reader };
}

async function loginJourney(page, host, tag) {
  const notice = { t: 'login_qr_err', op: 'request', id: '', site: 'channels-weixin', why: 'failed', reason: 'capture_blank' };
  await host.send(notice);
  await waitFor(page, "document.getElementById('lqr')?.dataset.popupSystem==='grow' && !!document.getElementById('lqr-note')?.textContent");
  const values = await measure(page, { kicker:'#lqr-kicker', title:'#lqr-title', copy:'#lqr-note', close:'#lqr-close' });
  readable(values, tag + '-qr-failure', 'qr-failure');
  await capture(page, tag + '-qr-failure');
  assert.equal(await evaluate(page, "!document.getElementById('lqr-img').getAttribute('src') && document.getElementById('lqr-tg').hidden"), true);
  return values;
}

const web = await startWebServer(base ? { publicDir: base } : {});
const host = await startFakeHost();
const browser = await launch();
const results = [];
if (out) mkdirSync(out, { recursive: true });
try {
  for (const width of (out ? [390, 768, 1440] : [390, 1440])) for (const lang of ['zh','en']) for (const profile of profiles) {
    const page = await pair(browser, web, host, width, profile, lang);
    const tag = `${width}-${lang}-${profile.name}`;
    try {
      assert.equal(await evaluate(page, "document.documentElement.scrollWidth <= innerWidth"), true);
      const reply = await replyJourney(page, host, width, profile, lang);
      const login = await loginJourney(page, host, tag);
      assert.deepEqual(page.problems, []);
      assert.deepEqual(page.offsite, []);
      results.push({ width, lang, profile: profile.name, reply, login });
    } finally { await page.dispose(); }
  }
  if (out) writeFileSync(join(out, 'contrast.json'), JSON.stringify(results, null, 2) + '\n');
  if (!before) {
    console.log('p0378-upgrade-reply-contrast PASS');
    console.log('p0378-shell-body-copy PASS');
    console.log('p0378-reader-and-login-card-contrast PASS');
  }
} finally {
  await browser.close();
  await host.stop();
  await web.stop();
}
