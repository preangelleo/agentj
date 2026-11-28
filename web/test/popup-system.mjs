// Inspect actual surfaces in the paired client. Placement is measured from the card, not its data-popup-system marker.
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { approvalEnvelopeDigest } from '../../protocol/wire.js';
import { foreground } from './lotion-foreground.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, sleep, shoot, rect } from './browser.mjs';

const expected = JSON.parse(readFileSync(new URL('popup-system-baseline.json', import.meta.url)));
const audit = process.argv.includes('--audit');
const publicDir = process.env.AJ_POPUP_PUBLIC;
const screenshotDir = process.env.AJ_POPUP_SCREENSHOTS;
const reviewScenarios = new Map([
  ['#cam', '01-camera'], ['recording-cancel', '02-recording-cancel'], ['multi-secret', '03-multi-secret'],
  ['terminal-sheet', '04-terminal'], ['notebook-editor', '05-notebook'], ['login-qr', '06-login-qr'],
  ['single-secret', '07-single-secret'], ['settings-secret', '08-settings-secret'],
  ['settings-device-passkey', '09-settings-face-id'],
]);
if (screenshotDir) mkdirSync(screenshotDir, { recursive: true });
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
const peachy = '217,119,87,1';
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

function measureSurface(selector, context) {
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
  const visiblePage = context === 'settings' ? document.getElementById('settings')
    : context === 'terminal' ? document.getElementById('term') : null;
  const growFloor = visiblePage ? Math.min(bottom, visiblePage.getBoundingClientRect().bottom) : quota.top;
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
  else if (wide && roundTop && flatBottom && r.bottom >= Math.min(growFloor, bottom) - 2 && r.bottom <= bottom + 6) system = 'grow';
  else if (centeredX && centeredY) system = 'center';

  const rgb = value => {
    const numbers = value.match(/[\d.]+/g)?.map(Number) || [];
    const scale = value.startsWith('color(') ? 255 : 1;
    return [...numbers.slice(0, 3).map(v => Math.round(v * scale)), numbers[3] ?? 1].join(',');
  };
  const visibleControl = button => button?.getBoundingClientRect().width > 0
    && button.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  const buttonPaint = button => {
    const style = getComputedStyle(button);
    return { id: button.id || button.className, fill: rgb(style.backgroundColor),
      ink: rgb(style.color), image: style.backgroundImage,
      borderless: ['Top', 'Right', 'Bottom', 'Left'].every(side => parseFloat(style['border' + side + 'Width']) === 0) };
  };
  const buttons = Array.from(root.querySelectorAll('button,[role="button"],input:is([type="button"],[type="submit"],[type="reset"])'))
    .filter(visibleControl).map(buttonPaint);
  const surroundingIds = context === 'terminal' ? ['term-end']
    : context === 'settings' && root.id === 'confirm' ? ['scan-repair', 'unpair'] : [];
  const surroundingButtons = surroundingIds.map(id => {
    const button = document.getElementById(id);
    return visibleControl(button) ? buttonPaint(button) : { id, hidden: true };
  });
  const ring = root.querySelector('.pttring');
  const cross = root.querySelector('.pttx');
  const recordingCancel = ring && cross ? {
    fill: rgb(getComputedStyle(ring).backgroundColor),
    opacity: Number(getComputedStyle(ring).opacity),
    ink: rgb(getComputedStyle(cross).stroke),
    shown: visibleControl(cross),
  } : null;
  const authTag = root.querySelector('.atag.danger.arconfirm');
  const authWarning = authTag ? rgb(getComputedStyle(authTag).borderInlineStartColor) : null;
  const nativeBackdrop = root.matches('dialog:modal') ? getComputedStyle(root,'::backdrop') : null;
  return { buttons, surroundingButtons, recordingCancel, authWarning,
    clearBackdrop: nativeBackdrop && rgb(nativeBackdrop.backgroundColor) === '0,0,0,0' && nativeBackdrop.backdropFilter === 'none',
    system, declared: root.dataset.popupSystem || null, panelWidth: r.width, height: r.height,
    shown: r.width > 0 && r.height > 0 && c.visibility !== 'hidden',
    x: r.left, y: r.top, bottom: r.bottom, quota: quota.top, growFloor, atGrowFloor: near(r.bottom, growFloor),
    expectedGrowFill: rgb(getComputedStyle(visiblePage || document.getElementById('water')).backgroundColor),
    fill: rgb(c.backgroundColor), ink: rgb(c.color), water: rgb(getComputedStyle(document.getElementById('water')).backgroundColor),
    parent: rgb(getComputedStyle(root.parentElement).backgroundColor), paint: c.backgroundColor };
}

const noir = '44,43,38,1', lotion = '250,249,245,1';
const strictScenarios = new Set([...reviewScenarios.keys(), 'settings-auth-approval', 'settings-peer-passkey', 'device-passkey', 'peer-passkey']);
const expectedActions = new Map([
  ['#cam', ['camSnap', 'camCancel']],
  ['terminal-sheet', ['term-sh-no', 'term-sh-yes']],
  ['settings-secret', ['elev-allow', 'elev-deny']],
  ['settings-device-passkey', ['confirm-no', 'confirm-yes']],
]);
const solidAction = ({ fill, ink, image, borderless }, panelFill) =>
  image === 'none' && (fill === lotion && ink === noir && (panelFill !== noir || borderless)
    || fill === noir && ink === lotion && panelFill !== noir);
const pageAction = button => button.id === 'scan-repair'
  ? solidAction(button, noir)
  : button.fill === peachy && button.ink === noir && button.image === 'none' && button.borderless;
async function verify(page, scenario, selector, expectedShape, context = 'chat') {
  await waitFor(page, `(() => {const n=document.querySelector(${JSON.stringify(selector)}); return n && !n.hidden && n.getClientRects().length;})()`);
  await sleep(150);
  // Theme and focus transitions may still be in flight on a busy browser.
  await evaluate(page, `(async () => {
    const root = document.querySelector(${JSON.stringify(selector)});
    const finite = root.getAnimations({subtree:true}).filter(animation => Number.isFinite(animation.effect.getComputedTiming().endTime));
    await Promise.allSettled(finite.map(animation => animation.finished));
  })()`);
  const sample = await evaluate(page, `(${measureSurface.toString()})(${JSON.stringify(selector)},${JSON.stringify(context)})`);
  const width = (await evaluate(page, 'innerWidth'));
  const theme = await evaluate(page, 'document.documentElement.dataset.theme');
  const issues = [];
  if (!sample?.shown) issues.push('not-visible');
  else {
    if (sample.system !== expectedShape) issues.push('geometry');
    if (sample.system === 'grow' && !sample.atGrowFloor) issues.push('off-waterline');
    if (sample.system === 'grow' && sample.fill !== sample.expectedGrowFill) issues.push('bottom-fill');
    if (sample.system === 'center' && sample.fill !== noir) issues.push('center-fill');
    if (sample.system === 'center' && sample.ink !== lotion) issues.push('center-ink');
    if (scenario === 'terminal-sheet' && expectedShape === 'grow' && sample.parent !== '0,0,0,0') issues.push('terminal-veil');
    if (scenario === 'notebook-editor' && expectedShape === 'grow' && !sample.clearBackdrop) issues.push('notebook-veil');
    if (strictScenarios.has(scenario) && sample.ink !== lotion) issues.push('panel-ink');
    if (expectedActions.get(scenario)?.some(id => !sample.buttons.some(button => button.id === id))) issues.push('missing-action');
    if (strictScenarios.has(scenario)) {
      if (sample.buttons.some(button => button.fill === sample.fill)) issues.push('button-matches-surface');
      if (sample.buttons.some(button => !solidAction(button, sample.fill))) issues.push('button-fill');
    }
    if (sample.surroundingButtons.some(button => !pageAction(button))) issues.push('page-button-fill');
    if (scenario === 'recording-cancel') {
      const { fill, opacity, ink, shown } = sample.recordingCancel;
      if (fill !== peachy || opacity !== 1 || ink !== noir || !shown) issues.push('cancel-accent');
    }
    if (scenario === 'settings-auth-approval' && sample.authWarning !== peachy) issues.push('auth-accent');
  }
  foregroundRows.push({ scenario, width, theme, samples: await foreground(page, selector) });
  rows.push({ scenario, selector, context, width, theme, expected: expectedShape, issue: issues.join('+'), ...sample });
  if (screenshotDir && reviewScenarios.has(scenario)) {
    const file = join(screenshotDir, `${reviewScenarios.get(scenario)}-${width}-${theme}-${audit ? 'before' : 'after'}.png`);
    writeFileSync(file, await shoot(page, file));
  }
  if (audit) console.log('MEASURED', scenario, width, theme, issues.join('+'));
}

async function staticAudit(page) {
  const roots = await evaluate(page, `[...document.querySelectorAll(${JSON.stringify(staticSelector)})].map(n=>n.id?'#'+n.id:'')`);
  assert.ok(roots.length >= 20, 'overlay inventory missing');
  assert.ok(roots.every(Boolean), 'overlay without an ID requires a browser fixture');
  staticRoots = [...new Set(roots)].sort();
  if (!audit) assert.deepEqual(staticRoots, expected.staticRoots);
  for (const selector of roots) {
    if (staticPages[selector] || ['#confirm', '#cam', '#ptt'].includes(selector)) continue;
    await evaluate(page, open(selector));
    await verify(page, selector, selector, 'center');
    await evaluate(page, close(selector));
  }
  // Phones use the OS camera picker. Render the same desktop preview at 390px
  // for visual review, with a synthetic stream and the existing capture controls.
  const touch = await evaluate(page, 'matchMedia("(pointer:coarse)").matches');
  if (touch) {
    await evaluate(page, `(async () => {
      const video = document.getElementById('camVideo');
      video.srcObject = await navigator.mediaDevices.getUserMedia({ video: true });
      video.hidden = false;
      document.getElementById('camSnap').hidden = false;
      document.getElementById('cam').hidden = false;
    })()`);
  } else {
    await evaluate(page, "document.getElementById('tCamera').click()");
    await waitFor(page, "document.getElementById('cam').dataset.mode === 'live'");
  }
  await verify(page, '#cam', '#cam', 'center');
  if (touch) await evaluate(page, "document.getElementById('camVideo').srcObject.getTracks().forEach(track=>track.stop())");
  await evaluate(page, "document.getElementById('camCancel').click()");

  // Hold and drag the real microphone control to cancel; never send audio.
  const { x, y } = await rect(page, '#mic');
  if (touch) {
    await page.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    await page.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y - 130 }] });
  } else {
    await page.send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', buttons: 1, clickCount: 1, x, y });
    await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', button: 'left', buttons: 1, x, y: y - 130 });
  }
  await waitFor(page, "document.getElementById('ptt').matches('.cancel:not([hidden])')");
  await verify(page, 'recording-cancel', '#ptt', 'center');
  if (touch) await page.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  else await page.send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', buttons: 0, clickCount: 1, x, y: y-130 });
  await waitFor(page, "document.getElementById('ptt').hidden");
}

async function sendApproval(page, youtube, settings = false) {
  const tool = settings ? 'AuthRescue' : youtube ? 'agentj google youtube upload' : 'Bash';
  if (youtube) {
    const envelope = { v: 2, channel: host.channel, session: String(host.st.epoch), request_id: id,
      revision: 1, nonce: 'fixture', source: { channel: 'phone', verified: true, text: '测试样例' },
      title: '确认 YouTube 上传', action: tool, reason: '审阅样例', details: '隔离样例', effects: [],
      expires_at: Math.ceil(Date.now() / 1000) + 120 };
    await host.ask({ t:'ask', id, tool, summary: '隔离样例', approval_v: 2, envelope,
      envelope_sha256: await approvalEnvelopeDigest(envelope), revision: 1, review_state: 'pending', ttl: 120 });
  } else {
    await host.ask({ id, tool, summary: settings ? '确认重新登录示例账号' : 'Synthetic operation',
      ttl: 120, ...(settings ? { host_tool:true } : {}) });
  }
  await waitFor(page, "document.body.dataset.sheet === '1'");
  if (settings) await waitFor(page, "!document.getElementById('settings').hidden && document.getElementById('sheet').matches('.auth-approval')");
  await verify(page, settings ? 'settings-auth-approval' : youtube ? 'youtube-upload' : 'ordinary-approval',
    settings ? '#sheet.auth-approval' : '#sheet', 'grow', settings ? 'settings' : 'chat');
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
  await inModule(page, 'pkassist', "void m.pkSheet('local','在这台遥控器验证','通过本机通行密钥继续。','继续验证')");
  await verify(page, 'device-passkey', '#confirm', 'grow');
  await inModule(page, 'ui', 'm.closeConfirm()');

  const scene = async (name, selector, system, module, start, end = 'm.clear()', context) => {
    await inModule(page, module, start);
    await verify(page, name, selector, system, context);
    if (['single-secret', 'multi-secret'].includes(name) && width === 390) {
      const actions = await evaluate(page, `(() => {
        const buttons = ['elev-allow','elev-deny'].map(id=>document.getElementById(id));
        const card = document.querySelector('#elev > .box'), frame = card.getBoundingClientRect();
        return buttons.map(button => {
          const r = button.getBoundingClientRect();
          const x = r.left + r.width / 2, y = r.top + r.height / 2;
          const points = [[x, y], [x, r.top + 2], [x, r.bottom - 2], [r.left + 2, y], [r.right - 2, y]];
          return { id: button.id, scroll: card.scrollTop, enabled: !button.disabled,
            visible: r.width >= 44 && r.height >= 44 && r.left >= frame.left && r.right <= frame.right
              && r.top >= frame.top && r.bottom <= frame.bottom && r.bottom <= visualViewport.offsetTop + visualViewport.height,
            hit: points.every(([x, y]) => button.contains(document.elementFromPoint(x, y))) };
        });
      })()`);
      const issue = actions.some(action => action.scroll || !action.visible || !action.hit || !action.enabled);
      if (issue) rows.at(-1).issue += (rows.at(-1).issue ? '+' : '') + 'actions-off-first-screen';
      rows.at(-1).actions = actions;
      console.log('MEASURED', name, 'actions', width, JSON.stringify(actions));
    }
    await inModule(page, module, end);
  };
  const local = JSON.stringify(payload);
  await scene('single-secret', '#elev', 'grow', 'elevate', `m.add({...${local},kind:'secret',name:'FIXTURE',purpose:'Fixture:',dest:'fixture'})`);
  await scene('multi-secret', '#elev[data-multi-secret]', 'grow', 'elevate', `m.add({...${local},kind:'secret',name:'FIXTURE, SECOND',names:['FIXTURE','SECOND'],purpose:'Fixture',dest:'fixture'})`);
  await scene('secret-pickup', '#sout', 'grow', 'secretout', `m.add({...${local},kind:'text',name:'FIXTURE',purpose:'Fixture',size:8,pickup_uv_allowed:true})`);
  const qr = await inModule(page, 'qr', `
    const {m: cells,n} = m.qrMatrix('Agent J popup visual fixture');
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = n;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0,0,n,n);
    ctx.fillStyle = '#000';
    cells.forEach((row,y)=>row.forEach((dark,x)=>{if(dark)ctx.fillRect(x,y,1,1)}));
    return canvas.toDataURL('image/png').split(',')[1];
  `);
  await scene('login-qr', '#lqr', 'grow', 'logincard', `m.add({...${local},site:'fixture-site',title:'扫码登录',img:${JSON.stringify(qr)}})`);
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
  await scene('settings-device-passkey', '#confirm', 'grow', 'pkassist', "void m.pkSheet('local','在这台遥控器验证','通过本机通行密钥继续。','继续验证')", '', 'settings');
  await inModule(page, 'ui', 'm.closeConfirm()');
  await scene('settings-peer-passkey', '#pkp', 'grow', 'pkpeer',
    `m.onAsk({id:'${key}',code:'123456',fa_ch:'${challenge}',fa:'synthetic',name:'Fixture',exp:Math.floor(Date.now()/1000)+300})`,
    'm._reset();m.closeResult()', 'settings');
  await sendApproval(page, false, true);
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
  await waitFor(page, "document.getElementById('term-sheetwrap')?.dataset.kind === 'intro' && !document.getElementById('term-sh-yes').disabled");
  await waitFor(page, "getComputedStyle(document.getElementById('term')).animationName === 'none'");
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
      if (message.t === 'term_open' && !message.sig) {
        await send({ t:'ctl_res', r:message.r, action:'term_open', ok:false, why:'passkey_required',
          n, epk, fa:'Zml4dHVyZQ', fa_s:'local', fa_ch:epk, salt:epk });
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
const unexpectedForeground = [...new Set(foregroundIssues.filter(issue => strictScenarios.has(issue.split('|')[0]) || !historicalForeground.has(issue)))];
if (!audit) {
  for (const issue of unexpectedForeground) console.error('FOREGROUND FAIL', issue);
  assert.deepEqual(unexpectedForeground, [], 'rendered foreground must use Lotion, or Noir on a Lotion button');
  for (const scenario of [...strictScenarios, 'youtube-upload', 'youtube-upload-details', 'youtube-command-full', 'sudo', 'upgrade', 'task-resume-receipt']) {
    const covered = foregroundRows.filter(row => row.scenario === scenario && row.samples.length > 0);
    assert.equal(covered.length, dimensions.length * 2, scenario + ' foreground coverage');
  }
}
console.log(`${audit ? 'AUDIT' : 'PASS'} ${foregroundRows.reduce((count, row) => count + row.samples.length, 0)} rendered foreground samples; ${foregroundIssues.length} deviations`);
const mismatches = rows.filter(row => row.issue);
if (!audit) {
  for (const row of mismatches) {
    console.error('POPUP FAIL', signature(row), JSON.stringify([...row.buttons.filter(button => !solidAction(button, row.fill)), ...row.surroundingButtons.filter(button => !pageAction(button))]));
  }
  assert.deepEqual(mismatches.map(signature), [], 'popup geometry, colors and visible buttons must use the same system');
  console.log('PASS popup actions contrast with their surfaces and fit on entry');
}
for (const row of mismatches) console.log('HISTORICAL', signature(row));
console.log(`${audit ? 'AUDIT' : 'PASS'} ${rows.length - mismatches.length}/${rows.length} popup classifications`);
if (!audit) console.log('PASS 0.30.82 popup contract');
