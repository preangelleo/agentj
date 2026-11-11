#!/usr/bin/env node
// 0.30.7 candidate (chairman 11-11): the reload-stop and preview-failure notices, on the REAL notices (not a stand-in text).
//  Reload stop (reload-guard.js createReloadGuard + ui.js reloadStoppedNotice — the function app.js wires, seeded with three
//  recent attempts, as p590):
//   rs1 shown with its close button  ·  rs2 Esc → gone  ·  rs3 a real tap / click on the close button → gone
//   (check only, zh-light-390) gone by itself after 15 s
//  Preview failure (preview-failure.js, real sessionStorage marker on a paired seat page, as p591):
//   pf1 shown: its own strip between the header and the reply card, the card's top not covered
//   pf2 「知道了」 → gone  ·  pf3 shown again, Esc → gone  ·  (check only, zh-light-390) gone after 10 s
//   pf4 a confirmation card (ui.js confirmSheet) opens while the strip shows: the card is above the strip (S3)
//   pf5 an approval card (host ask) arrives while the strip shows: its title is not covered
// zh + en × light + dark × 390 × 844 and 1440 × 900 → $AJ_P0307_TOAST_SHOTS/{preview-failure,reload-stop}/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { seatId } from '../public/js/seats.js';
import { launch, newPage, navigate, evaluate, waitFor, waitState, shoot, key, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.AJ_P0307_TOAST_SHOTS || join(ROOT, 'reports/qa/0307-toast-dismiss-shots/');
const PF = join(OUT, 'preview-failure'), RS = join(OUT, 'reload-stop');
mkdirSync(PF, { recursive: true }); mkdirSync(RS, { recursive: true });
const web = await startWebServer({ port: 0 }), fake = await startFakeHost(), B = await launch();
let checks = 0;
const results = [];
const check = (v, m) => { results.push([!!v, m]); assert.ok(v, m); checks++; };
const J = JSON.stringify;
const index = [];
const cap = async (p, dir, state, label, note) => { const f = `${state}-${label}.png`; writeFileSync(join(dir, f), await shoot(p, '')); index.push([dir === PF ? 'preview-failure' : 'reload-stop', state, f, note]); };
const on = `document.getElementById('toast').classList.contains('on')`;
const TXT = { zh: { pf: '预览没能打开，已回到正式版', rs: '页面刷新没有生效', x: '关闭这条提示' }, en: { pf: "Preview couldn't open — you're on the regular version.", rs: 'Page refresh did not take effect', x: 'Close this notice' } };
let marks = 0;
const mark = (p) => evaluate(p, `(async()=>{sessionStorage.setItem('aj.preview.failed.'+globalThis.AJSeat.id,'1');if(${marks++ > 0})(await import('./js/preview-failure.js')).watchPreviewFailure();return true})()`);
const STOP = `(async()=>{const {createReloadGuard}=await import('./js/reload-guard.js');const {reloadStoppedNotice}=await import('./js/ui.js');const stamp=Date.now(),key='aj.reloadGuard.v1',data={'version.follow':{times:[stamp-3000,stamp-2000,stamp-1000]}};sessionStorage.setItem(key,JSON.stringify(data));localStorage.setItem(key,JSON.stringify(data));let navigation=0;const guard=createReloadGuard({navigate:()=>navigation++,report:()=>{},notice:()=>reloadStoppedNotice()});const result=guard('version.follow');await Promise.resolve();return {result,navigation}})()`;
const LAYOUT = `(()=>{const n=document.getElementById('toast'),r=n.getBoundingClientRect(),b=n.querySelector('.undo');return {inView:r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1,clipped:[...n.querySelectorAll('*')].some(e=>e.scrollWidth>e.clientWidth+1&&getComputedStyle(e).overflow!=='visible'),btn:b?b.getBoundingClientRect().height:null}})()`;
// the close button: size, label, colours, and whether a tap at its centre lands on it (nothing above it)
const XBTN = `(()=>{const n=document.getElementById('toast'),x=n.querySelector('.nt-close');if(!x)return {present:false};const r=x.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2,h=document.elementFromPoint(cx,cy),t=n.querySelector('.nt-body').getBoundingClientRect(),cs=getComputedStyle(x),bs=getComputedStyle(n);
 return {present:true,w:r.width,h:r.height,label:x.getAttribute('aria-label'),hit:!!h&&(h===x||x.contains(h)),cx,cy,inBox:r.left>=n.getBoundingClientRect().left-1&&r.right<=n.getBoundingClientRect().right+1,gap:Math.max(t.top-r.bottom,r.left-t.right,0),overlap:!(r.bottom<=t.top||r.top>=t.bottom||r.right<=t.left||r.left>=t.right),fg:cs.color,bg:bs.backgroundColor}})()`;
// the strip vs the reply card: the strip ends above the stage and the card's top-left text area is the card, not the strip
const STRIP = `(()=>{const n=document.getElementById('toast'),r=n.getBoundingClientRect(),s=document.getElementById('stage').getBoundingClientRect(),hd=document.querySelector('header.top').getBoundingClientRect();
 const h=document.elementFromPoint(s.left+Math.min(60,s.width/4),s.top+24);
 const d=document.getElementById('deck'),dr=d.getBoundingClientRect(),dc=getComputedStyle(d),cl=dr.left+parseFloat(dc.paddingLeft),cr=dr.right-parseFloat(dc.paddingRight);
 return {aligned:Math.abs(r.left-cl)<2&&Math.abs(r.right-cr)<2,left:r.left,right:r.right,cardLeft:cl,cardRight:cr,parent:n.parentElement?.id||n.parentElement?.tagName,position:getComputedStyle(n).position,above:r.bottom<=s.top+1,belowHeader:r.top>=hd.bottom-1,cardTopFree:!!h&&!n.contains(h)&&h!==n,top:r.top,bottom:r.bottom,stageTop:s.top}})()`;
// a card (selector) over the strip: the card's own title point hits the card, and the strip is under the card's z
const OVER = (card, title) => `(()=>{const n=document.getElementById('toast'),c=document.querySelector(${J(card)}),t=document.querySelector(${J(title)})||c;if(!c||!t)return {ok:false,why:'no card'};
 const tr=t.getBoundingClientRect(),h=document.elementFromPoint(tr.left+Math.min(20,tr.width/2),tr.top+tr.height/2);
 const nr=n.getBoundingClientRect(),cr=c.getBoundingClientRect(),overlap=n.classList.contains('on')&&!(nr.bottom<=cr.top||nr.top>=cr.bottom||nr.right<=cr.left||nr.left>=cr.right);
 let stripOnTop=false;if(overlap){const pe=n.style.pointerEvents;n.style.pointerEvents='auto';const x=Math.max(nr.left,cr.left)+4,y=Math.max(nr.top,cr.top)+4,q=document.elementFromPoint(x,y);stripOnTop=!!q&&(q===n||n.contains(q));n.style.pointerEvents=pe}
 return {ok:true,titleHit:!!h&&(h===t||t.contains(h)||c.contains(h)),overlap,stripOnTop,ontop:document.body.dataset.ontop||'',z:getComputedStyle(n).zIndex,stripOn:n.classList.contains('on')}})()`;
const tapAt = async (p, w, x, y) => {
  if (w === 390) { await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] }); await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); }
  else { await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }); await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }); await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }); }
};
try {
  for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const label = `${lang}-${theme}-${w}`, s = TXT[lang];
    if (process.env.AJ_P0307_ONLY && process.env.AJ_P0307_ONLY !== label) continue;
    fake.reset(); fake.st.prefSet = true; fake.st.prefs = { appearance: { language: lang, theme }, voice: { speak_replies: false } };
    const p = await newPage(B, w, h, theme, { touch: w === 390, allow: [web.url, fake.relay + '/'] });
    try {
      const seatPath = await seatId(fake.channel);
      await navigate(p, fake.newPairing(web.url.replace(/\/$/, '') + '/' + seatPath + '/?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();true`);
      await sleep(300);
      // ---- preview failure
      marks = 0;
      await mark(p);
      await waitFor(p, `${on} && document.getElementById('toast').textContent.includes(${J(s.pf)})`);
      const l = await evaluate(p, LAYOUT);
      check(l.inView && !l.clipped && l.btn >= 44, 'pf layout ' + label + ' ' + J(l));
      const st = await evaluate(p, STRIP);
      check(st.parent === 'chat-view' && st.position === 'relative', 'pf is a strip in the chat flow ' + label + ' ' + J(st));
      check(st.aligned, 'pf strip lines up with the reply card edges ' + label + ' ' + J(st));
      check(st.above && st.belowHeader && st.cardTopFree, 'pf strip between header and card, card top not covered ' + label + ' ' + J(st));
      await cap(p, PF, 'pf1-shown', label, 'real marker → 「预览没能打开，已回到正式版」 + 「知道了」 as its own strip; the reply card moved down, its top uncovered');
      await evaluate(p, `document.querySelector('#toast .undo').click();true`);
      await waitFor(p, `!${on}`, 3000);
      check(!(await evaluate(p, on)), 'pf button dismisses ' + label);
      await sleep(300);
      check(await evaluate(p, `document.getElementById('toast').getBoundingClientRect().height===0||getComputedStyle(document.getElementById('toast')).display==='none'`), 'pf strip collapses after 知道了 ' + label);
      await cap(p, PF, 'pf2-dismissed-button', label, '「知道了」 tapped → gone, the card back in place');
      await mark(p);
      await waitFor(p, `${on} && document.getElementById('toast').textContent.includes(${J(s.pf)})`);
      await key(p, 'Escape');
      await waitFor(p, `!${on}`, 3000);
      check(!(await evaluate(p, on)), 'pf Esc dismisses ' + label);
      await sleep(300);
      await cap(p, PF, 'pf3-dismissed-esc', label, 'shown again, Esc → gone');
      // pf4: a confirmation card opens while the strip shows (S3)
      await mark(p);
      await waitFor(p, `${on} && document.getElementById('toast').textContent.includes(${J(s.pf)})`);
      await evaluate(p, `import('./js/ui.js').then(m=>{window.__c=m.confirmSheet(${J(lang === 'zh' ? '确认删除这个文件夹？' : 'Delete this folder?')},${J(lang === 'zh' ? '删除后不能恢复。' : 'This cannot be undone.')},${J(lang === 'zh' ? '删除' : 'Delete')},true)});true`);
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      await sleep(500);
      const o4 = await evaluate(p, OVER('#confirm .ajconfirm, #confirm', '#confirm-title'));
      check(o4.ok && o4.titleHit && !o4.stripOnTop, 'pf4 confirm card above the strip, title readable ' + label + ' ' + J(o4));
      await cap(p, PF, 'pf4-with-confirm-card', label, `confirm card opened while the strip shows: card above it (body[data-ontop]=${o4.ontop || '—'}, strip z ${o4.z}; overlap ${o4.overlap})`);
      await evaluate(p, `document.getElementById('confirm-no').click();true`);
      await sleep(300);
      // pf5: an approval card from the host while the strip shows
      if (!(await evaluate(p, on))) { await mark(p); await waitFor(p, `${on}`); }
      const askId = randomBytes(16).toString('hex');
      await fake.ask({ id: askId, tool: 'Bash', summary: lang === 'zh' ? '删除 build 目录' : 'Delete the build folder', ttl: 120 });
      await waitFor(p, `document.body.dataset.sheet==='1'`);
      await sleep(900);
      const o5 = await evaluate(p, OVER('#sheet', '#sheet h2, #sheet .shhd, #sheet [id$="Title"], #sheet'));
      const sheetTop = await evaluate(p, `(()=>{const r=document.getElementById('sheet').getBoundingClientRect(),n=document.getElementById('toast').getBoundingClientRect(),on=document.getElementById('toast').classList.contains('on');return {sheetTop:r.top,stripBottom:n.bottom,free:!on||n.bottom<=r.top+1||document.body.dataset.ontop==='1'}})()`);
      check(o5.ok && o5.titleHit && !o5.stripOnTop && sheetTop.free, 'pf5 approval card title not covered ' + label + ' ' + J(o5) + J(sheetTop));
      await cap(p, PF, 'pf5-with-approval-card', label, `approval card arrived while the strip shows: its title uncovered (strip ${o5.stripOn ? 'on' : 'gone'}, overlap ${o5.overlap})`);
      await fake.askDone(askId, 'ended');
      await waitFor(p, `document.body.dataset.sheet!=='1'`).catch(() => {});
      await evaluate(p, `document.getElementById('toast').classList.contains('on')&&document.getElementById('toast').dataset.previewFailure&&document.querySelector('#toast .undo')?.click();true`);
      await sleep(300);
      if (lang === 'zh' && theme === 'light' && w === 390) {
        await mark(p);
        await waitFor(p, on);
        await sleep(10600);
        check(!(await evaluate(p, on)), 'pf auto-dismiss after 10 s');
      }
      // ---- reload stop
      const r = await evaluate(p, STOP);
      check(r.result === false && r.navigation === 0, 'reload guard stops ' + label);
      await waitFor(p, `${on} && document.getElementById('toast').textContent.includes(${J(s.rs)})`);
      const l2 = await evaluate(p, LAYOUT);
      check(l2.inView && !l2.clipped, 'rs layout ' + label + ' ' + J(l2));
      const x = await evaluate(p, XBTN);
      check(x.present && x.w >= 44 && x.h >= 44, 'rs close button ≥ 44 px ' + label + ' ' + J(x));
      check(x.label === s.x, 'rs close aria-label in the page language ' + label + ' ' + J(x.label));
      check(x.hit && x.inBox && !x.overlap, 'rs close button on top, inside the notice, clear of the text ' + label + ' ' + J(x));
      check(x.fg === 'rgb(250, 249, 245)' && x.bg === 'rgb(44, 43, 38)', 'rs Lotion on Noir ' + label + ' ' + J(x));
      await cap(p, RS, 'rs1-shown', label, `fourth automatic reload → 「页面刷新没有生效，已停止自动刷新」 with its close button (aria-label 「${s.x}」, ${x.w}×${x.h})`);
      await key(p, 'Escape');
      await waitFor(p, `!${on}`, 3000).catch(() => {});
      check(!(await evaluate(p, on)), 'rs Esc dismisses ' + label);
      await sleep(300);
      await cap(p, RS, 'rs2-dismissed-esc', label, 'Esc → gone');
      await evaluate(p, STOP);
      await waitFor(p, on);
      const x2 = await evaluate(p, XBTN);
      await tapAt(p, w, x2.cx, x2.cy);
      await sleep(600);
      const tapGone = !(await evaluate(p, on));
      check(tapGone, `rs ${w === 390 ? 'tap' : 'click'} on the close button dismisses ` + label);
      await cap(p, RS, 'rs3-closed-by-button', label, `a real ${w === 390 ? 'touch tap' : 'mouse click'} on the close button → gone`);
      if (lang === 'zh' && theme === 'light' && w === 390) {
        await evaluate(p, STOP); await waitFor(p, on);
        await sleep(14000); check(await evaluate(p, on), 'rs still shown at 14 s');
        await sleep(1400); check(!(await evaluate(p, on)), 'rs auto-dismiss after 15 s');
      }
      check(p.offsite.length === 0, 'local only ' + label);
    } finally { await p.dispose(); }
    console.log('ok', label);
  }
  writeFileSync(join(OUT, 'browser.json'), JSON.stringify({ pass: true, checks, results, index }, null, 2));
  console.log(`p0307-toast-dismiss-shots: ${checks} checks PASS, ${index.filter((x) => x[2]).length} shots → ${OUT}`);
} catch (e) {
  writeFileSync(join(OUT, 'browser.json'), JSON.stringify({ pass: false, checks, results, index, error: String(e && e.message || e) }, null, 2));
  console.error('p0307-toast-dismiss-shots FAILED:', e && e.stack || e); process.exitCode = 1;
}
finally { await B.close(); await fake.stop(); await web.stop(); }
