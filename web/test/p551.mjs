#!/usr/bin/env node
// P551 — two owner decisions of 2026-10-26, the real web client against fakehost.mjs, own headless Chromium (never :9222):
//  A (#316 #318) every confirmation card is on the TOP layer and takes the focus: an approval card arriving while the reply
//    card's 「转发到邮箱」 dialog is open (and while a modal <dialog> — the browser's top layer — is open), the confirm sheet
//    over both, the tier in CSS above every other layer. Topmost = document.elementFromPoint at the card's centre (and at its
//    main button) lands inside it; focus inside it; Esc folds / cancels it and the focus goes back into the dialog, whose
//    typed words are still there; a tap on 拒绝 / 确定 reaches the card.
//  B (#320) the 4th approval level 「跟随 AI 编程工具（Harness）」 in Settings → 安全: one picker, 4 buttons that fit at 390 px; the red confirm;
//    no passkey on this remote → the host's `no_passkey` in plain words; with one → passkey_required → Face ID → the SAME
//    signed approval_set again with `fa`.
//  C (#318 ③) unbinding ANOTHER remote (Settings → 遥控器管理) and unbinding WeChat ask this remote's Face ID: the first signed
//    request → passkey_required → the Face ID sheet 「用 Face ID 确认解绑」 → the SAME signed object (same n) again with `fa`;
//    no passkey → `no_passkey` in plain words and nothing unbound; unbinding THIS remote itself stays without Face ID.
// 390 × 844 and 1440 × 900, light / dark (+ en). `P551_BEFORE=<public/ of the base tree> node test/p551.mjs` = shots only, into
// reports/qa/p551/before/ (the old page: the card under the dialog); no env = shots + checks → reports/qa/p551/after/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, key } from './browser.mjs';

const BEFORE = process.env.P551_BEFORE || '', CHECK = !BEFORE;
const OUT = fileURLToPath(new URL(`../../../reports/qa/p551/${BEFORE ? 'before' : 'after'}/`, import.meta.url));
const WX_QR = readFileSync(fileURLToPath(new URL('./fixtures/p501/qr.png', import.meta.url))).toString('base64');   // P550: the setup QR that opens the WeChat panel
mkdirSync(OUT, { recursive: true });
const web = await startWebServer(BEFORE ? { publicDir: BEFORE, relayCsp: 'ws://127.0.0.1:*' } : { port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
const J = JSON.stringify;
let checks = 0;
const check = (v, label) => { if (!CHECK) return; assert.ok(v, label); checks++; };
const hex = (n) => [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, '0')).join('');
const OWNER = 'owner@corp.example';
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };

// topmost: the element at the centre of `sel` (and of `inner`, when given) is inside `sel`
const TOP = (sel, inner) => `(()=>{const ok=(s)=>{const n=document.querySelector(s);if(!n)return false;const r=n.getBoundingClientRect();if(!r.width||!r.height)return false;
  const h=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return !!h&&document.querySelector(${J(sel)}).contains(h);};return ok(${J(sel)})&&${inner ? `ok(${J(inner)})` : 'true'};})()`;
const FOCUS_IN = (sel) => `!!document.querySelector(${J(sel)})?.contains(document.activeElement)`;
async function press(p, sel, touch) {
  const r = await ev(p, `(()=>{const r=document.querySelector(${J(sel)}).getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
  if (touch) {
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [r] });
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  } else {
    await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  }
}
async function paired(w, h, scheme, lang, before) {
  fake.reset();
  fake.st.prefs = structuredClone(PREFS);
  fake.st.prefs.appearance.language = lang;
  fake.st.caps = ['contacts'];
  fake.st.onApp = async (c, m, send) => {
    if (!/^(ct_|mail_fwd)/.test(m.t)) return false;
    if (m.t === 'ct_list') await send({ t: 'ct_res', r: m.r, ok: true, contacts: [], count: 0, me: [OWNER], mail: { ready: true } });
    return true;
  };
  if (before) before(fake.st);
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
// the 「转发到邮箱」 dialog, with words typed into its address field (the owner's in-progress input)
async function openForward(p, lang) {
  await fake.addTurn({ k: 'phone', dev: 'other', name: 'iPhone', text: lang === 'zh' ? '把这周的进展整理一下' : 'Sum up this week' },
    lang === 'zh' ? '## 本周进展\n- 发布了 0.25\n- 修好了登录' : '## This week\n- shipped 0.25\n- fixed sign-in');
  await waitFor(p, `!document.getElementById('mailBtn').hidden && !document.getElementById('mailBtn').disabled`);
  await ev(p, `document.getElementById('mailBtn').click()`);
  await waitFor(p, `!document.getElementById('ctFwd').hidden && !!document.querySelector('#ctFwd .ct-mode[data-mode=other]')`);
  await ev(p, `document.querySelector('#ctFwd .ct-mode[data-mode=other]').click()`);
  await waitFor(p, `!!document.getElementById('ctFwd-addr')`);
  await ev(p, `(()=>{const d=document.getElementById('ctFwd-add');if(d)d.open=true;const a=document.getElementById('ctFwd-addr');a.focus();a.value='half-typed@exa';a.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await sleep(200);
}
const CARD_UP = `document.body.dataset.sheet==='1' && !document.getElementById('apprBtns').hidden`;
const ask = (summary) => { const id = hex(16); return fake.ask({ id, tool: 'Bash', summary, ttl: 120 }).then(() => id); };
// every positioned layer of the page right now (z-index > 0), for the "one highest tier" check
const LAYERS = `(()=>{const out=[];for(const n of document.querySelectorAll('body *')){const cs=getComputedStyle(n);if(cs.position==='static')continue;const z=parseInt(cs.zIndex);if(!(z>0))continue;
  out.push({id:n.id||'',cls:String(n.className&&n.className.baseVal!==undefined?n.className.baseVal:n.className).slice(0,60),z,pos:cs.position});}return out;})()`;

const VIEWS = [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'zh'], [1440, 900, 'light', 'zh'], [1440, 900, 'dark', 'zh'], [390, 844, 'dark', 'en']];
try {
  // ======================================================================== A: cards on top, with focus
  for (const [w, h, scheme, lang] of VIEWS) {
    const name = `${w}-${scheme}-${lang}`, touch = w < 500;
    const p = await paired(w, h, scheme, lang);
    await openForward(p, lang);
    writeFileSync(OUT + `a1-dialog-${name}.png`, await shoot(p, ''));

    // 1) an approval card arrives while the e-mail dialog is open
    const id1 = await ask('rm -rf build/');
    await waitFor(p, `document.body.dataset.sheet==='1'`);
    await sleep(800);                                        // the sheet's slide-in
    writeFileSync(OUT + `a2-card-over-dialog-${name}.png`, await shoot(p, ''));
    if (CHECK) {
      check(await ev(p, TOP('#sheet', '#apprAllow')), 'approval card topmost over the e-mail dialog ' + name);
      check(await ev(p, `document.activeElement && document.activeElement.id==='apprAllow'`), 'focus on 长按批准 ' + name + ' :: ' + await ev(p, `document.activeElement?.id`));
      check(!(await ev(p, `document.getElementById('ctFwd').hidden`)), 'the dialog stays open under the card ' + name);
      check(await ev(p, `document.getElementById('apprAllowLbl').textContent`) === (lang === 'zh' ? '长按批准' : 'Hold to approve'), 'a normal card says 长按批准 ' + name);
      // Esc → the card folds; the dialog, its words and the focus come back
      await key(p, 'Escape'); await sleep(300);
      check(await ev(p, `document.body.dataset.sheet!=='1'`), 'Esc folds the card first ' + name);
      check(!(await ev(p, `document.getElementById('ctFwd').hidden`)), 'Esc did not close the dialog under it ' + name);
      check(await ev(p, `document.getElementById('ctFwd-addr').value`) === 'half-typed@exa', 'typed words kept ' + name);
      check(await ev(p, `document.activeElement && document.activeElement.id==='ctFwd-addr'`), 'focus back in the dialog field ' + name + ' :: ' + await ev(p, `document.activeElement?.id`));
      check(await ev(p, `!document.getElementById('sheet').classList.contains('ontop-float') && document.getElementById('sheet').parentElement.id==='stage'`), 'the sheet went home into the stage ' + name);
      // 2) a second card over the dialog: a real tap on 拒绝 reaches it
      fake.st.answers.length = 0;
      await fake.askDone(id1, 'ended');
      const id2 = await ask('git push --force');
      await waitFor(p, CARD_UP); await sleep(800);
      check(await ev(p, TOP('#sheet', '#apprDeny')), 'second card topmost ' + name);
      await press(p, '#apprDeny', touch);
      await waitFor(p, `document.body.dataset.sheet!=='1'`).catch(() => {});
      await sleep(300);
      check(fake.st.answers.some((m) => m.id === id2 && m.ok === false), 'deny reached the host from the card above the dialog ' + name);
      check(!(await ev(p, `document.getElementById('ctFwd').hidden`)) && await ev(p, `document.getElementById('ctFwd-addr').value`) === 'half-typed@exa', 'the dialog and its words survive the answer ' + name);

      // 3) the confirm sheet over the dialog: top, focused, Esc = cancel, focus back
      await ev(p, `document.getElementById('ctFwd-addr').focus()`);
      await ev(p, `import(new URL('js/ui.js', document.baseURI).href).then(m=>{window.__c=m.confirmSheet('P551','over the dialog','OK');})`);
      await waitFor(p, `!document.getElementById('confirm').hidden`); await sleep(150);
      check(await ev(p, TOP('#confirm .box', '#confirm-yes')), 'confirm sheet topmost over the dialog ' + name);
      check(await ev(p, `document.activeElement && document.activeElement.id==='confirm-yes'`), 'confirm sheet focused ' + name);
      if (w === 390 && scheme === 'light') writeFileSync(OUT + `a3-confirm-over-dialog-${name}.png`, await shoot(p, ''));
      await key(p, 'Escape'); await sleep(200);
      check(await ev(p, `document.getElementById('confirm').hidden && window.__c.then?true:false`) && await ev(p, `window.__c`) === false, 'Esc cancels the confirm ' + name);
      check(await ev(p, `document.activeElement && document.activeElement.id==='ctFwd-addr'`), 'focus back after the confirm ' + name);

      // 4) a confirm sheet while the approval card is up over the dialog: the newest card wins (z, Esc, focus)
      const id3 = await ask('npm publish');
      await waitFor(p, CARD_UP); await sleep(800);
      await ev(p, `import(new URL('js/ui.js', document.baseURI).href).then(m=>{window.__c=m.confirmSheet('P551','newest','OK');})`);
      await waitFor(p, `!document.getElementById('confirm').hidden`); await sleep(150);
      check(await ev(p, TOP('#confirm .box', '#confirm-yes')) && await ev(p, FOCUS_IN('#confirm')), 'the newer confirm is above the approval card ' + name);
      await key(p, 'Escape'); await sleep(250);
      check(await ev(p, `document.getElementById('confirm').hidden && document.body.dataset.sheet==='1'`), 'Esc closes the newer one first ' + name);
      check(await ev(p, TOP('#sheet', '#apprAllow')), 'approval card still on top after it ' + name);

      // the tier: every confirmation layer is above every other layer on the page
      const layers = await ev(p, LAYERS);
      const isCard = (l) => ['sheet', 'confirm'].includes(l.id) || /\b(ajconfirm|ontop-float|auth-approval)\b/.test(l.cls) || /ajmodal.*\b(elev|lqr|sout)\b/.test(l.cls);
      const isNotice = (l) => l.id === 'toast' || /\b(satband|sabband)\b/.test(l.cls) || l.id === 'iosdiag';
      const cardZ = Math.min(...layers.filter((l) => isCard(l) && l.pos === 'fixed').map((l) => l.z));
      const other = layers.filter((l) => !isCard(l) && !isNotice(l));
      check(other.every((l) => l.z < cardZ), 'no other layer at or above the confirmation tier ' + name + ' :: ' + J(other.filter((l) => l.z >= cardZ)));
      await key(p, 'Escape'); await sleep(250);
      await fake.askDone(id3, 'ended'); await sleep(200);

      // 5) a modal <dialog> (the browser's top layer: J Cards editors, the calendar) — the card still comes on top, focused
      await ev(p, `document.getElementById('ctFwd-cancel').click()`); await sleep(200);
      await ev(p, `(()=>{const d=document.createElement('dialog');d.id='p551dlg';d.className='jc-dialog';const i=document.createElement('input');i.id='p551in';i.value='draft words';d.append(i);document.body.append(d);d.showModal();i.focus();})()`);
      await sleep(150);
      await ask('rm -rf ~/old');
      await waitFor(p, CARD_UP); await sleep(800);
      if (w === 390 && scheme === 'light') writeFileSync(OUT + `a4-card-over-modal-dialog-${name}.png`, await shoot(p, ''));
      check(await ev(p, TOP('#sheet', '#apprAllow')), 'approval card topmost over a modal <dialog> ' + name);
      check(await ev(p, `document.activeElement && document.activeElement.id==='apprAllow'`), 'focus on the card over the modal dialog ' + name + ' :: ' + await ev(p, `(document.activeElement?.id||document.activeElement?.tagName)+' in '+document.getElementById('sheet').parentElement.id+'/'+document.getElementById('sheet').parentElement.tagName`));
      await key(p, 'Escape'); await sleep(300);
      check(await ev(p, `document.body.dataset.sheet!=='1' && document.getElementById('p551dlg').open && document.getElementById('p551in').value==='draft words'`), 'Esc folds the card; the dialog and its words stay ' + name);
      check(await ev(p, `document.activeElement && document.activeElement.id==='p551in'`), 'focus back in the modal dialog ' + name);
      check(await ev(p, `document.getElementById('sheet').parentElement.id==='stage'`), 'the card went home out of the dialog ' + name);
      await ev(p, `import(new URL('js/ui.js', document.baseURI).href).then(m=>{window.__c=m.confirmSheet('P551','over a modal dialog','OK');})`);
      await waitFor(p, `!document.getElementById('confirm').hidden`); await sleep(150);
      check(await ev(p, TOP('#confirm .box', '#confirm-yes')) && await ev(p, `document.activeElement.id==='confirm-yes'`), 'confirm sheet topmost + focused over a modal dialog ' + name);
      await press(p, '#confirm-yes', touch); await sleep(200);
      check(await ev(p, `window.__c`) === true && await ev(p, `document.getElementById('confirm').parentElement===document.body`), 'a tap on the confirm reaches it; it went home ' + name);
      // the dialog closes while a card sits inside it: the card comes home first (never removed with the dialog)
      await ev(p, `import(new URL('js/ui.js', document.baseURI).href).then(m=>{window.__c=m.confirmSheet('P551','dialog closes','OK');})`);
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      await ev(p, `(()=>{const d=document.getElementById('p551dlg');d.close();d.remove();})()`); await sleep(200);
      check(await ev(p, `document.getElementById('confirm')?.parentElement===document.body && document.getElementById('confirm').isConnected`), 'confirm survives its dialog closing ' + name);
      check(await ev(p, TOP('#confirm .box', '#confirm-yes')), 'and is still on top ' + name);
      await key(p, 'Escape'); await sleep(200);
      check(!p.problems.length, 'no console errors ' + name + ' ' + p.problems.join(' | '));
    }
    await p.dispose();
  }

  // ======================================================================== B: 「跟随 AI 编程工具（Harness）」
  const L4 = `(()=>{const bad=[];const row=document.getElementById('set-level-row');const R=row.getBoundingClientRect();const g=row.querySelector('.segrow').getBoundingClientRect();
    for(const b of row.querySelectorAll('[data-set-level]')){const r=b.getBoundingClientRect();if(r.left<R.left-1||r.right>R.right+1||r.left<g.left-1||r.right>g.right+1)bad.push('overflow '+b.dataset.setLevel);if(r.height<44||r.width<44)bad.push('tap '+b.dataset.setLevel);
      if(b.scrollWidth>b.clientWidth+1)bad.push('clipped '+b.dataset.setLevel);}
    if(document.documentElement.scrollWidth>innerWidth)bad.push('page scroll');return bad;})()`;
  const pressed = (p) => ev(p, `[...document.querySelectorAll('[data-set-level]')].filter(b=>b.getAttribute('aria-pressed')==='true').map(b=>b.dataset.setLevel).join()`);
  async function openSafety(p) {
    await ev(p, `document.getElementById('setBtn').click()`);
    await waitFor(p, `!document.getElementById('settings').hidden`);
    await ev(p, `document.getElementById('set-h-safety').scrollIntoView({block:'start'})`);
    await sleep(300);
  }
  for (const [w, h, scheme, lang] of [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'zh'], [390, 844, 'light', 'en'], [390, 844, 'dark', 'en'], [1440, 900, 'light', 'zh'], [1440, 900, 'dark', 'en'], [360, 780, 'light', 'en']]) {
    const name = `${w}-${scheme}-${lang}`, L = (zh, en) => (lang === 'en' ? en : zh), touch = w < 500;
    // no passkey on this remote
    let p = await paired(w, h, scheme, lang, (st) => { st.prefs.approval = { level: 'standard' }; });
    await openSafety(p);
    writeFileSync(OUT + `b1-levels-${name}.png`, await shoot(p, ''));
    if (CHECK) {
      check(await ev(p, `document.querySelectorAll('#set-level-row [data-set-level]').length`) === 4, '4 buttons in ONE picker ' + name);
      check(await ev(p, `document.querySelector('[data-set-level="harness"]').textContent`) === L('跟随 AI 编程工具', 'Follow AI tool'), 'harness label ' + name);
      const bad = await ev(p, L4);
      check(!bad.length, 'the 4 buttons fit ' + name + ' :: ' + bad.join(' · '));
      await press(p, '[data-set-level="harness"]', touch);
      await waitFor(p, `!document.getElementById('confirm').hidden`); await sleep(200);
      writeFileSync(OUT + `b2-harness-confirm-${name}.png`, await shoot(p, ''));
      check(await ev(p, `document.getElementById('confirm-title').textContent`) === L('改成「跟随 AI 编程工具（Harness）」档？', 'Switch to Follow your AI coding tool (harness)?'), 'confirm title ' + name);
      const txt = await ev(p, `document.getElementById('confirm-text').textContent`);
      check(txt.includes(L('这四类仍要 Face ID', 'Only four kinds still need Face ID')) && txt.includes(L('设置了 Face ID 的设备上切换', 'Face ID set up can switch')), 'confirm text = note + Face ID sign note ' + name);
      check(await ev(p, `document.getElementById('confirm-yes').textContent`) === L('改成跟随 AI 编程工具', 'Switch to Follow AI tool'), 'yes label ' + name);
      check(await ev(p, `document.getElementById('confirm-yes').classList.contains('aj-btn--danger')`), 'the switch reads as dangerous ' + name);
      check(await ev(p, TOP('#confirm .box', '#confirm-yes')) && await ev(p, `document.activeElement.id==='confirm-yes'`), 'confirm over Settings, focused ' + name);
      await press(p, '#confirm-yes', touch);
      await waitFor(p, `document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent.includes(${J(L('还没有设置 Face ID', 'no Face ID'))})`);
      await sleep(250);
      writeFileSync(OUT + `b3-no-passkey-${name}.png`, await shoot(p, ''));
      check(await pressed(p) === 'standard', 'no_passkey → the level stays ' + name);
      check([...await ev(p, `[...document.querySelectorAll('#toast button')].map((b) => b.textContent)`)].some((x) => x.includes(L('在本机设置', 'Set up here'))), 'P550: no_passkey offers 「在本机设置（需另一台批准）」 ' + name);
      check(fake.st.approvalSets.length === 1 && fake.st.approvalSets[0].value === 'harness' && fake.st.approvalSets[0].sigOk, 'one signed approval_set harness ' + name);
      check(!fake.st.prefSets.some((m) => String(m.key).startsWith('approval.')), 'never pref_set ' + name);
      check(!p.problems.length, 'no console errors (no passkey) ' + name + ' ' + p.problems.join(' | '));
    }
    await p.dispose();
    if (!CHECK) continue;
    // with a passkey: passkey_required → Face ID sheet → the same signed object + fa → harness pressed, its note shown
    p = await paired(w, h, scheme, lang, (st) => { st.approvalPasskey = true; st.prefs.approval = { level: 'standard' }; });
    await ev(p, `navigator.credentials.get = (o) => { window.__fa = (window.__fa || 0) + 1; const b = (s) => new TextEncoder().encode(s).buffer;
      return Promise.resolve({ rawId: b('cred-1'), response: { clientDataJSON: b('{}'), authenticatorData: b('ad'), signature: b('sig') } }); }`);
    await openSafety(p);
    await press(p, '[data-set-level="harness"]', touch);
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    await press(p, '#confirm-yes', touch);
    await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-title').textContent===${J(L('用 Face ID 确认', 'Confirm with Face ID'))}`);
    await sleep(200);
    writeFileSync(OUT + `b4-faceid-${name}.png`, await shoot(p, ''));
    check(await ev(p, `document.activeElement.id==='confirm-yes'`), 'Face ID sheet focused ' + name);
    await press(p, '#confirm-yes', touch);
    await waitFor(p, `document.querySelector('[data-set-level="harness"]').getAttribute('aria-pressed')==='true'`);
    const [s1, s2] = fake.st.approvalSets;
    check(s1 && !s1.fa && s2 && s2.n === s1.n && s2.fa && s2.sigOk && s2.value === 'harness', 'same signed object resent with fa ' + name);
    check(await ev(p, `window.__fa`) === 1, 'Face ID asked once ' + name);
    check((await ev(p, `document.getElementById('set-level-note').textContent`)).startsWith(L('审批跟随底层 AI 编程工具（技术上叫 Harness）', 'Approvals follow your underlying AI coding tool (technically, a harness)')), 'harness note ' + name);
    await sleep(2500);
    writeFileSync(OUT + `b5-harness-on-${name}.png`, await shoot(p, ''));
    // back to standard: narrowing, no Face ID
    await press(p, '[data-set-level="standard"]', touch);
    await waitFor(p, `!document.getElementById('confirm').hidden`); await press(p, '#confirm-yes', touch);
    await waitFor(p, `document.querySelector('[data-set-level="standard"]').getAttribute('aria-pressed')==='true'`);
    check(fake.st.approvalSets.length === 3 && !fake.st.approvalSets[2].fa, 'leaving harness needs no Face ID ' + name);
    check(!p.problems.length, 'no console errors (Face ID) ' + name + ' ' + p.problems.join(' | '));
    await p.dispose();
  }
  // ======================================================================== C: ③ unbinds behind Face ID
  if (CHECK) {
    const NOW = Math.floor(Date.now() / 1000);
    const DEVS = () => [
      { id: 'ME', name: 'iPhone Air', paired_at: NOW - 86400 * 12, seen: NOW, online: true, source: 'local', me: true },
      { id: 'Ab4Tq7Wz9Lm3Xc8P', name: '华为 MatePad', paired_at: NOW - 86400, seen: NOW - 7200, online: false, source: 'phone' },
    ];
    const FAKE_FA = `navigator.credentials.get = (o) => { window.__fa = (window.__fa || 0) + 1; const b = (s) => new TextEncoder().encode(s).buffer;
      return Promise.resolve({ rawId: b('cred-1'), response: { clientDataJSON: b('{}'), authenticatorData: b('ad'), signature: b('sig') } }); }`;
    const OTHER = '#rm-list .rmitem[data-id="Ab4Tq7Wz9Lm3Xc8P"] [data-act="unbind"]';
    const toastHas = (s) => `document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent.includes(${J(s)})`;
    async function openRemotes(p) {
      await ev(p, `document.getElementById('setBtn').click()`);
      await waitFor(p, `!document.getElementById('settings').hidden`);
      await ev(p, `document.getElementById('set-remotes-open').click()`);
      await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === 2`);
    }
    async function openWechat(p) {
      // P550: since P536 Settings →「开通或管理」 hands over to the main Agent; the panel opens with a fresh setup QR and
      // stays open for the handshake — that is where the phone's own 「解除绑定」 lives
      await fake.send({ t: 'wechat_state', bound: false, pending: 0, qr: { id: 'c'.repeat(32), status: 'wait', img: WX_QR } });
      await waitFor(p, `document.getElementById('wechat-panel')?.hidden===false`);
      await fake.send({ t: 'wechat_state', bound: true, pending: 0, greeting: 'needs_message' });
      await waitFor(p, `document.getElementById('wechat-panel')?.hidden===false && !document.getElementById('wc-unbind').hidden`);
    }
    for (const [w, h, scheme, lang] of [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'en'], [1440, 900, 'dark', 'zh'], [1440, 900, 'light', 'en']]) {
      const name = `${w}-${scheme}-${lang}`, L = (zh, en) => (lang === 'en' ? en : zh), touch = w < 500;
      // C1 another remote, this remote has a passkey: confirm 「用 Face ID 解绑」 → passkey_required → Face ID → same n + fa
      let p = await paired(w, h, scheme, lang, (st) => { st.remotes = { limit: 10, items: DEVS(), passkey: true }; });
      await ev(p, FAKE_FA);
      await openRemotes(p);
      await press(p, OTHER, touch);
      await waitFor(p, `!document.getElementById('confirm').hidden`); await sleep(150);
      check(await ev(p, `document.getElementById('confirm-yes').textContent`) === L('用 Face ID 解绑', 'Unbind with Face ID'), 'the unbind button says Face ID ' + name);
      writeFileSync(OUT + `c1-unbind-confirm-${name}.png`, await shoot(p, ''));
      await press(p, '#confirm-yes', touch);
      await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-title').textContent===${J(L('用 Face ID 确认解绑', 'Confirm unbinding with Face ID'))}`);
      await sleep(150);
      writeFileSync(OUT + `c2-unbind-faceid-${name}.png`, await shoot(p, ''));
      check(await ev(p, `document.activeElement.id==='confirm-yes'`) && await ev(p, TOP('#confirm .box', '#confirm-yes')), 'Face ID sheet on top, focused ' + name);
      check(fake.st.remotes.items.length === 2, 'nothing unbound before Face ID ' + name);
      await press(p, '#confirm-yes', touch);
      await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === 1`);
      const [u1, u2] = fake.st.remotes.writes.filter((x) => x.t === 'dev_unbind');
      check(u1 && !u1.fa && u1.sigOk && u2 && u2.fa && u2.sigOk && u2.n === u1.n && u2.ts === u1.ts && u2.sig === u1.sig && u2.device === 'Ab4Tq7Wz9Lm3Xc8P', 'same signed dev_unbind resent with fa ' + name);
      check(await ev(p, `window.__fa`) === 1, 'Face ID asked once ' + name);
      // C2 THIS remote unbinding itself: no Face ID (the host answers ok at once)
      const before = fake.st.remotes.writes.length;
      await press(p, '#rm-list .rmitem[data-me="1"] [data-act="unbind"]', touch);
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      check(await ev(p, `document.getElementById('confirm-yes').textContent`) === L('解绑', 'Unbind'), 'self unbind button unchanged ' + name + ' :: ' + await ev(p, `document.getElementById('confirm-yes').textContent`));
      await press(p, '#confirm-yes', touch);
      for (let i = 0; i < 50 && fake.st.remotes.writes.length === before; i++) await sleep(20);
      const self = fake.st.remotes.writes.at(-1);
      check(self.t === 'dev_unbind' && self.device === 'ME' && !self.fa && await ev(p, `window.__fa`) === 1, 'self unbind: one write, no Face ID ' + name);
      check(!p.problems.length, 'no console errors (remote Face ID) ' + name + ' ' + p.problems.join(' | '));
      await p.dispose();

      // C3 another remote, NO passkey here: no_passkey in plain words, nothing unbound, no Face ID
      p = await paired(w, h, scheme, lang, (st) => { st.remotes = { limit: 10, items: DEVS(), passkey: false }; });
      await ev(p, FAKE_FA);
      await openRemotes(p);
      await press(p, OTHER, touch);
      await waitFor(p, `!document.getElementById('confirm').hidden`); await press(p, '#confirm-yes', touch);
      await waitFor(p, toastHas(L('解绑属于不可逆操作，需要本人验证', "Unbinding can't be undone and needs you to verify")));
      await sleep(250);
      writeFileSync(OUT + `c3-unbind-no-passkey-${name}.png`, await shoot(p, ''));
      check(fake.st.remotes.items.length === 2 && await ev(p, `document.querySelectorAll('#rm-list .rmitem').length`) === 2, 'no_passkey → nothing unbound ' + name);
      check(fake.st.remotes.writes.filter((x) => x.t === 'dev_unbind').length === 1 && !(await ev(p, `window.__fa`)), 'no_passkey → no Face ID, no resend ' + name);
      check(!p.problems.length, 'no console errors (remote no passkey) ' + name + ' ' + p.problems.join(' | '));
      await p.dispose();

      // C4 WeChat unbind with a passkey: Face ID sheet (the panel stays under it) → same signed object + fa → unbound
      p = await paired(w, h, scheme, lang, (st) => { st.wechat = { bound: true, passkey: true }; });
      await ev(p, FAKE_FA);
      await fake.send({ t: 'wechat_state', bound: true, pending: 0 });
      await openWechat(p);
      await press(p, '#wc-unbind', touch);
      await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-title').textContent===${J(L('用 Face ID 确认解绑', 'Confirm unbinding with Face ID'))}`);
      await sleep(150);
      writeFileSync(OUT + `c4-wechat-faceid-${name}.png`, await shoot(p, ''));
      check(await ev(p, `document.getElementById('confirm-yes').textContent`) === L('用 Face ID 解绑', 'Unbind with Face ID'), 'WeChat Face ID button ' + name);
      check(await ev(p, TOP('#confirm .box', '#confirm-yes')), 'Face ID sheet above the WeChat panel ' + name);
      check(fake.st.wechat.bound === true, 'WeChat still bound before Face ID ' + name);
      await press(p, '#confirm-yes', touch);
      await waitFor(p, `document.getElementById('wc-unbind').hidden`);
      const [w1, w2] = fake.st.wechat.writes.filter((x) => x.op === 'unbind');
      check(w1 && !w1.fa && w1.sigOk && w2 && w2.fa && w2.sigOk && w2.n === w1.n && w2.sig === w1.sig, 'same signed wechat unbind resent with fa ' + name);
      check(fake.st.wechat.bound === false && await ev(p, `window.__fa`) === 1, 'WeChat unbound after one Face ID ' + name);
      // setup never asks Face ID (P550: since P536 it hands over to the main Agent's composer, no signed write of its own)
      await ev(p, `document.getElementById('wc-setup').click()`);
      await sleep(300);
      check(await ev(p, `window.__fa`) === 1 && await ev(p, `document.getElementById('confirm').hidden`) && await ev(p, `document.getElementById('wechat-panel').hidden`), 'setup: no Face ID ' + name);
      check(!p.problems.length, 'no console errors (WeChat Face ID) ' + name + ' ' + p.problems.join(' | '));
      await p.dispose();

      // C5 WeChat unbind, NO passkey: no_passkey in plain words, still bound
      p = await paired(w, h, scheme, lang, (st) => { st.wechat = { bound: true, passkey: false }; });
      await ev(p, FAKE_FA);
      await fake.send({ t: 'wechat_state', bound: true, pending: 0 });
      await openWechat(p);
      await press(p, '#wc-unbind', touch);
      await waitFor(p, toastHas(L('解绑属于不可逆操作，需要本人验证', "Unbinding can't be undone and needs you to verify")));
      await sleep(250);
      writeFileSync(OUT + `c5-wechat-no-passkey-${name}.png`, await shoot(p, ''));
      check(fake.st.wechat.bound === true && !(await ev(p, `document.getElementById('wc-unbind').hidden`)), 'no_passkey → WeChat still bound ' + name);
      check(fake.st.wechat.writes.filter((x) => x.op === 'unbind').length === 1 && !(await ev(p, `window.__fa`)), 'no_passkey → no Face ID, no resend ' + name);
      check(!p.problems.length, 'no console errors (WeChat no passkey) ' + name + ' ' + p.problems.join(' | '));
      await p.dispose();
    }
  }
  console.log(CHECK ? `p551: ${checks} checks passed — shots in ${OUT}` : `p551 before: shots in ${OUT}`);
} catch (e) {
  console.error('p551 FAILED:', e && e.stack || e);
  process.exitCode = 1;
} finally {
  await B.close();
  await fake.stop();
  await web.stop();
}
