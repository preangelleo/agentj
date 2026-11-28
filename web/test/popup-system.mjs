// Inspect actual surfaces in the paired client. Placement is measured from the card, not its data-popup-system marker.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { approvalEnvelopeDigest } from '../../protocol/wire.js';
import { foreground } from './lotion-foreground.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, sleep } from './browser.mjs';

const expected = JSON.parse(readFileSync(new URL('popup-system-baseline.json', import.meta.url)));
const audit = process.argv.includes('--audit');
const publicDir = process.env.AJ_POPUP_PUBLIC;
const web = await startWebServer({ publicDir });
const host = await startFakeHost();
const browser = await launch();
const url = web.url.replace('127.0.0.1', 'localhost');
const rows = [], foregroundRows = [];
let staticRoots;
const dimensions = [[390, 844], [1440, 900]];
const id = '1'.repeat(32), n = '2'.repeat(32), epk = Buffer.alloc(32, 7).toString('base64url');
const image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jco0AAAAASUVORK5CYII=';
const payload = { id, n, epk, ttl: 600 };
const staticSelector = '.gdlg,.ajmodal,dialog,[role="dialog"],[popover],#ptt';
const staticPages = {
  '#badge-panel': 'full-screen information page',
  '#five-detail': 'full-screen navigation detail',
  '#five-menu': 'full-screen navigation menu',
  '#fsEd': 'full-screen composer editor',
  '#hotMenu': 'anchored command menu',
  '#keys': 'full-screen keyboard shortcuts page',
  '#menu': 'anchored command menu',
  '#rd': 'full-screen reply reader',
  '#settings': 'full-screen Settings page',
  '#silent': 'full-screen background activity page',
  '#srch': 'full-screen message search page',
};
// Runtime-only producers are inventoried separately; a new dialog constructor needs a fixture.
const producerDirectory = audit && process.env.AJ_POPUP_PUBLIC
  ? new URL('js/', new URL('file://' + process.env.AJ_POPUP_PUBLIC.replace(/\/?$/, '/')))
  : new URL('../public/js/', import.meta.url);
const producers = Object.fromEntries(readdirSync(fileURLToPath(producerDirectory))
  .filter(name => name.endsWith('.js') && !name.startsWith('locale-')).sort().flatMap(name => {
    const source = readFileSync(new URL(name, producerDirectory), 'utf8');
    const markers = source.match(/\.showModal\(\)|\.showPopover\(\)|\.setAttribute\(['"]aria-modal['"],\s*['"]true['"]\)|\bmk\(['"]div['"],\s*['"](?:ajmodal|gdlg)[^'"]*['"]/g);
    return markers ? [[name, markers.map(marker => marker.replace(/\s+/g, ' ')).sort()]] : [];
  }));
if (!audit) assert.deepEqual(producers, expected.producers, 'new overlay producer needs a popup fixture');
const open = selector => `(() => {
  const root = document.querySelector(${JSON.stringify(selector)});
  if (root.matches('dialog')) root.showModal(); else root.hidden = false;
})()`;
const close = selector => `(() => {
  const root = document.querySelector(${JSON.stringify(selector)});
  if (root.matches('dialog')) root.close(); else root.hidden = true;
})()`;
const inModule = (page, name, script) => evaluate(page, `import('./js/${name}.js').then(m=>{${script}})`);

function measureSurface(selector) {
  const root = document.querySelector(selector);
  const panel = root?.matches('#ptt') ? root.querySelector('.pttc') :
    root?.matches('.gdlg,.ajmodal,#cam') ? root.querySelector(':scope > :is(.box,.sq)') : root;
  if (!panel) return null;
  const r = panel.getBoundingClientRect(), visible = visualViewport;
  const width = visible?.width || innerWidth, height = visible?.height || innerHeight;
  const left = visible?.offsetLeft || 0, top = visible?.offsetTop || 0, bottom = top + height;
  const stage = document.getElementById('stage').getBoundingClientRect();
  const composer = document.querySelector('.composer').getBoundingClientRect();
  const quota = document.getElementById('m5h').getBoundingClientRect();
  const readingCenter = (Math.max(top, stage.top) + Math.min(bottom, stage.bottom, composer.top || bottom)) / 2;
  const near = (a, b, limit = 2) => Math.abs(a - b) <= limit;
  const c = getComputedStyle(panel);
  const wide = near(r.width, width) && near(r.left, left);
  const centeredX = near(r.left + r.width / 2, left + width / 2);
  const group = root.matches('#ptt') && getComputedStyle(root).justifyContent === 'center';
  const centeredY = group || near(r.top + r.height / 2, top + height / 2, 12) ||
    near(r.top + r.height / 2, readingCenter, 12);
  const roundTop = parseFloat(c.borderTopLeftRadius) > 0;
  const flatBottom = parseFloat(c.borderBottomLeftRadius) === 0;
  let system = 'unknown';
  if (wide && r.height >= height * .9 && !roundTop) system = 'full';
  else if (wide && roundTop && flatBottom && r.bottom >= quota.top - 2 && r.bottom <= bottom + 6) system = 'grow';
  else if (centeredX && centeredY) system = 'center';

  const rgb = value => {
    const numbers = value.match(/[\d.]+/g)?.map(Number) || [];
    const scale = value.startsWith('color(') ? 255 : 1;
    return [...numbers.slice(0, 3).map(v => Math.round(v * scale)), numbers[3] ?? 1].join(',');
  };
  return { system, declared: root.dataset.popupSystem || null, panelWidth: r.width, height: r.height,
    shown: r.width > 0 && r.height > 0 && c.visibility !== 'hidden',
    x: r.left, y: r.top, bottom: r.bottom, quota: quota.top, atQuota: near(r.bottom, quota.top),
    fill: rgb(c.backgroundColor), ink: rgb(c.color), water: rgb(getComputedStyle(document.getElementById('water')).backgroundColor),
    parent: rgb(getComputedStyle(root.parentElement).backgroundColor), paint: c.backgroundColor };
}

const noir = '44,43,38,1', lotion = '250,249,245,1';
async function verify(page, scenario, selector, expectedShape, context = 'chat') {
  await waitFor(page, `(() => {const n=document.querySelector(${JSON.stringify(selector)}); return n && !n.hidden && n.getClientRects().length;})()`);
  await sleep(150);
  const sample = await evaluate(page, `(${measureSurface.toString()})(${JSON.stringify(selector)})`);
  const width = (await evaluate(page, 'innerWidth'));
  const theme = await evaluate(page, 'document.documentElement.dataset.theme');
  const issues = [];
  if (!sample?.shown) issues.push('not-visible');
  else {
    if (sample.system !== expectedShape) issues.push('geometry');
    if (sample.system === 'grow' && !sample.atQuota) issues.push('off-quota');
    if (sample.system === 'grow' && sample.fill !== sample.water) issues.push('bottom-fill');
    if (sample.system === 'center' && sample.fill !== noir) issues.push('center-fill');
    if (sample.system === 'center' && sample.ink !== lotion) issues.push('center-ink');
  }
  foregroundRows.push({ scenario, width, theme, samples: await foreground(page, selector) });
  rows.push({ scenario, selector, context, width, theme, expected: expectedShape, issue: issues.join('+'), ...sample });
  if (audit) console.log('MEASURED', scenario, width, theme, issues.join('+'));
}

async function staticAudit(page) {
  const roots = await evaluate(page, `[...document.querySelectorAll(${JSON.stringify(staticSelector)})].map(n=>n.id?'#'+n.id:'')`);
  assert.ok(roots.length >= 20, 'overlay inventory missing');
  assert.ok(roots.every(Boolean), 'overlay without an ID requires a browser fixture');
  staticRoots = [...new Set(roots)].sort();
  if (!audit) assert.deepEqual(staticRoots, expected.staticRoots);
  for (const selector of roots) {
    if (staticPages[selector] || selector === '#confirm') continue;
    await evaluate(page, open(selector));
    await verify(page, selector, selector, 'center');
    await evaluate(page, close(selector));
  }
  // The held recording's cancel gesture uses different CSS from the resting bubble.
  await evaluate(page, `${open('#ptt')};document.getElementById('ptt').classList.add('cancel')`);
  await verify(page, 'recording-cancel', '#ptt', 'center');
  await evaluate(page, `document.getElementById('ptt').classList.remove('cancel');${close('#ptt')}`);
}

async function sendApproval(page, youtube) {
  const tool = youtube ? 'agentj google youtube upload' : 'Bash';
  if (youtube) {
    const envelope = { v: 2, channel: host.channel, session: String(host.st.epoch), request_id: id,
      revision: 1, nonce: 'fixture', source: { channel: 'phone', verified: true, text: '测试样例' },
      title: '确认 YouTube 上传', action: tool, reason: '审阅样例', details: '隔离样例', effects: [],
      expires_at: Math.ceil(Date.now() / 1000) + 120 };
    await host.ask({ t:'ask', id, tool, summary: '隔离样例', approval_v: 2, envelope,
      envelope_sha256: await approvalEnvelopeDigest(envelope), revision: 1, review_state: 'pending', ttl: 120 });
  } else {
    await host.ask({ id, tool, summary: 'Synthetic operation', ttl: 120 });
  }
  await waitFor(page, "document.body.dataset.sheet === '1'");
  await verify(page, youtube ? 'youtube-upload' : 'ordinary-approval', '#sheet', 'grow');
  if (youtube) {
    await evaluate(page, "document.getElementById('details541').open = true");
    await verify(page, 'youtube-upload-details', '#sheet', 'grow');
    await evaluate(page, "document.getElementById('detailsFull541').click()");
    await verify(page, 'youtube-command-full', '#full541', 'center');
    await evaluate(page, "document.getElementById('full541').close()");
    await waitFor(page, "document.body.dataset.sheet === '1'");
  }
  await host.askDone(id, 'gone');
  await waitFor(page, "document.body.dataset.sheet === '0'");
}

async function variants(page, width) {
  await sendApproval(page, false);
  if (!audit) await sendApproval(page, true);
  await host.send({ t: 'elev', ...payload, kind: 'sudo', cmd: 'sudo /usr/bin/true', why: '隔离测试', effect: '无实际操作' });
  await verify(page, 'sudo', '#elev', 'grow');
  await inModule(page, 'elevate', 'm.clear()');
  await host.send({ t: 'upg_card', id, from: '0.30.79', to: '0.30.80', ttl: 90, items: [{ k: 'queue', n: 1 }] });
  await verify(page, 'upgrade', '#confirm[data-kind=upg]', 'center');
  await inModule(page, 'upgcard', 'm.done({id:' + JSON.stringify(id) + '})');
  await waitFor(page, "document.getElementById('confirm').hidden");
  await inModule(page, 'ui', "void m.confirmSheet('Check operation','Fixture','Confirm')");
  await verify(page, 'ordinary-confirm', '#confirm', 'center');
  await inModule(page, 'ui', 'm.closeConfirm()');
  await inModule(page, 'pkassist', "void m.pkSheet('local','Verify here','Fixture','Continue')");
  await verify(page, 'device-passkey', '#confirm', 'grow');
  await inModule(page, 'ui', 'm.closeConfirm()');

  const scene = async (name, selector, system, module, start, end = 'm.clear()', context) => {
    await inModule(page, module, start);
    await verify(page, name, selector, system, context);
    await inModule(page, module, end);
  };
  const local = JSON.stringify(payload);
  await scene('single-secret', '#elev', 'grow', 'elevate', `m.add({...${local},kind:'secret',name:'FIXTURE',purpose:'Fixture:',dest:'fixture'})`);
  await scene('multi-secret', '#elev[data-multi-secret]', 'grow', 'elevate', `m.add({...${local},kind:'secret',name:'FIXTURE, SECOND',names:['FIXTURE','SECOND'],purpose:'Fixture',dest:'fixture'})`);
  await scene('secret-pickup', '#sout', 'grow', 'secretout', `m.add({...${local},kind:'text',name:'FIXTURE',purpose:'Fixture',size:8,pickup_uv_allowed:true})`);
  await scene('login-qr', '#lqr', 'grow', 'logincard', `m.add({...${local},site:'fixture-site',title:'Scan to sign in',img:${JSON.stringify(image)}})`);
  await scene('owner-help', '#hlp', 'center', 'helpcard', `m.add({...${local},kind:'screen',mime:'image/png',img:${JSON.stringify(image)},text:'Fixture',reply:'done',by:'agent',from:'fixture'})`);
  await scene('agent-login', '#lgs', 'center', 'loginsheet', "m.open('opencode')", 'm.close()');
  await scene('goal', '#lt', 'center', 'longtask', `m.add({...${local},card:{card_id:'${id}',nonce:'fixture',title:'Fixture',goal:'Fixture',required:[],optional:[]}})`);
  await scene('capabilities', '#lt-caps', 'center', 'longtask', "m.showCaps({items:[{id:'fixture',title:'Fixture',status:'ready'}]})");
  await scene('mail-servers', '#mail-servers', 'center', 'mail-servers', "void m.mailServers({imap:{host:'imap.example.test'},smtp:{host:'smtp.example.test'}})",
    "document.querySelector('#mail-servers button[type=button]').click()");
  const key = Buffer.alloc(16, 17).toString('base64url'), challenge = Buffer.alloc(32, 19).toString('base64url');
  await scene('peer-passkey', '#pkp', 'grow', 'pkpeer',
    `m.configure({canAssert:()=>true,approve:async()=>({synthetic:true}),send:async()=>{}});m.onAsk({id:'${key}',code:'123456',fa_ch:'${challenge}',fa:'synthetic',name:'Fixture',exp:Math.floor(Date.now()/1000)+300})`,
    'm._reset();m.closeResult()');
  await scene('send-to-contacts', '#ctFwd', 'center', 'contacts', "void m.openForward({kind:'text',text:'Fixture'})",
    "document.getElementById('ctFwd-cancel').click()");
  await evaluate(page, "location.hash='file=abcdefabcdefabcdefabcdefabcdefab'");
  await verify(page, 'private-file-locator', '#private-file-locator', 'center');
  await evaluate(page, "document.querySelector('#private-file-locator button').click();location.hash=''");
  await inModule(page, 'jcards', "void m.directAdd('notes',{nav:()=>{}})");
  await verify(page, 'notebook-editor', 'dialog[data-note-sheet]', width === 390 ? 'grow' : 'center');
  await evaluate(page, "document.querySelector('dialog[data-note-sheet] .jc-note-cancel').click()");
  await inModule(page, 'settings', "m.open('main')");
  await scene('wechat-settings', '#wechat-panel', 'center', 'wechat', 'm.show()', 'm.reset()', 'settings');
  await scene('seat-transfer', '#sxf', 'center', 'seatxfer',
    "m.open({prompt:'Synthetic preview',expires_at:Math.floor(Date.now()/1000)+300})", 'm.close()', 'settings');
  await scene('settings-secret', '#elev', 'grow', 'elevate', `m.add({...${local},kind:'secret',name:'FIXTURE',purpose:'Fixture',dest:'fixture'})`, 'm.clear()', 'settings');
  await scene('settings-device-passkey', '#confirm', 'grow', 'pkassist', "void m.pkSheet('local','Verify here','Fixture','Continue')", '', 'settings');
  await inModule(page, 'ui', 'm.closeConfirm()');
  const theme = await evaluate(page, 'document.documentElement.dataset.theme');
  const scheme = { name: 'prefers-color-scheme', value: theme };
  // Measure the terminal's actual resting layout using its existing reduced-motion recipe.
  await page.send('Emulation.setEmulatedMedia', {
    features: [scheme, { name: 'prefers-reduced-motion', value: 'reduce' }],
  });
  host.st.terminal = { mobile:true, kind:'phone', on:true, os:true, me:true,
    web_ok:true, minutes:15, modes:['line','pty'] };
  await host.send(host.prefsMsg());
  await inModule(page, 'settings', 'm.render()');
  await evaluate(page, "(document.getElementById('set-term-enable')?.getClientRects().length ? document.getElementById('set-term-enable') : document.getElementById('set-term-open')).click()");
  await waitFor(page, "document.getElementById('term') && getComputedStyle(document.getElementById('term')).animationName === 'none'");
  await verify(page, 'terminal-sheet', '#term-sheet', width === 390 ? 'grow' : 'center', 'terminal');
  await evaluate(page, "document.getElementById('term-sh-no').click()");
  await page.send('Emulation.setEmulatedMedia', { features: [scheme] });
  await inModule(page, 'settings', 'm.close()');
  await inModule(page, 'ui', 'm.toastOff()');
  await inModule(page, 'calendar', `const holder=document.createElement('div');holder.id='popup-fixture-calendar';
    holder.style.position='fixed'; holder.style.left='-9999px'; document.body.append(holder);m.drawCalendar(holder,{events:[]},{},()=>{});holder.querySelector('[data-day]').click()`);
  await verify(page, 'calendar-editor', 'dialog.jc-cal-dialog', 'center');
  await evaluate(page, "document.querySelector('dialog.jc-cal-dialog').close();document.getElementById('popup-fixture-calendar').remove()");
}

try {
  for (const [width, height] of dimensions) for (const theme of ['light', 'dark']) {
    host.reset();
    host.st.prefs = { appearance: { language: 'zh', theme } };
    host.st.caps = ['contacts'];
    host.st.extraCaps = ['jcards1'];
    host.st.onApp = async (_connection, message, send) => {
      if (message.t === 'ct_list') {
        await send({ t:'ct_res', r:message.r, ok:true, contacts:[], count:0, me:[], mail:{ready:true} });
        return true;
      }
      if (message.t === 'jcard_get') {
        await send({ t:'jcard_get', r:message.r, id:message.id, ok:true, noteFields:true, template:'notes', entries:[] });
        return true;
      }
      return false;
    };
    const page = await newPage(browser, width, height, theme, { touch: width < 500, allow: [url, host.relay + '/'] });
    try {
      await navigate(page, host.newPairing(url + '?lang=zh'));
      await waitState(page, 'awaiting-approval');
      await host.approve();
      await waitState(page, 'ready');
      await evaluate(page, "for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b?.offsetParent)b.click()}");
      await inModule(page, 'settings', `m.applyThemeLocal('${theme}')`);
      await host.send({ t: 'status', s: 'idle', name: 'Agent J' });
      await staticAudit(page);
      await variants(page, width);
      if (!audit) {
        const receipt = '正在确认任务有没有交到新助手手里，先别重发。';
        await host.addTurn({ k:'phone', dev:host.devs.at(-1).id, name:'你', text:'切换助手' }, receipt);
        await waitFor(page, `document.getElementById('words').textContent.includes(${JSON.stringify(receipt)})`);
        foregroundRows.push({ scenario:'task-resume-receipt', width, theme, samples: await foreground(page, '#words') });
      }
    } finally {
      await page.dispose();
    }
  }
} finally {
  if (process.env.AJ_POPUP_REPORT) writeFileSync(process.env.AJ_POPUP_REPORT, JSON.stringify({ staticRoots, staticPages, producers, rows, foregroundRows }, null, 2) + '\n');
  await browser.close();
  await host.stop();
  await web.stop();
}

const signature = ({ scenario, width, theme, system, issue, fill, ink }) => [scenario, width, theme, system, issue, fill, ink].join('|');
const foregroundIssues = foregroundRows.flatMap(({ scenario, width, theme, samples }) =>
  samples.filter(sample => !sample.pass).map(sample =>
    [scenario, width, theme, sample.node, sample.property, sample.color].join('|')));
const historicalForeground = new Set(expected.historicalForeground);
const unexpectedForeground = [...new Set(foregroundIssues.filter(issue => !historicalForeground.has(issue)))];
if (!audit) {
  for (const issue of unexpectedForeground) console.error('FOREGROUND FAIL', issue);
  assert.deepEqual(unexpectedForeground, [], 'rendered foreground must use Lotion, or Noir on a Lotion button');
  for (const scenario of ['youtube-upload', 'youtube-upload-details', 'youtube-command-full', 'sudo', 'upgrade', 'task-resume-receipt']) {
    const covered = foregroundRows.filter(row => row.scenario === scenario && row.samples.length > 0);
    assert.equal(covered.length, dimensions.length * 2, scenario + ' foreground coverage');
  }
}
console.log(`${audit ? 'AUDIT' : 'PASS'} ${foregroundRows.reduce((count, row) => count + row.samples.length, 0)} rendered foreground samples; ${foregroundIssues.length} deviations`);
const mismatches = rows.filter(row => row.issue);
if (!audit) {
  const known = new Set(expected.historical);
  const unexpected = mismatches.filter(row => !known.has(signature(row)));
  assert.deepEqual(unexpected, [], 'popup geometry and actual backdrop must use the same system');
}
for (const row of mismatches) console.log('HISTORICAL', signature(row));
console.log(`PASS ${rows.length - mismatches.length}/${rows.length} popup classifications; ${mismatches.length} frozen historical rows`);
