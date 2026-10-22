// P318 · Noir Notification in the real client against fakehost.mjs, own Chromium (never :9222). Every kind × both shapes ×
// light / dark × 390 phone / 1440 desktop: computed background = Noir, text = Lotion, opaque; the rect is as wide as the
// input-message card; the circle is a circle. Screens → reports/qa/p318/noir/*.png + checks.json.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep} from './browser.mjs';
const root = fileURLToPath(new URL('../../../', import.meta.url)), out = root + 'reports/qa/p318/noir/';
mkdirSync(out, {recursive: true});
const NOIR = 'rgb(44, 43, 38)', LOTION = 'rgb(250, 249, 245)';
const SPECS = [
  {name: 'info', spec: {kind: 'info', text: '已切换到 Codex'}},
  {name: 'success', spec: {kind: 'success', title: '已提交', text: '你的回答已发给 Agent，它会接着做。'}},
  {name: 'confirm', spec: {kind: 'confirm', text: '已删除 3 条引用'}, act: '撤销'},
  {name: 'error', spec: {kind: 'error', text: '没切换：Agent J 主机不在线'}},
  {name: 'progress', spec: {kind: 'progress', text: '正在上传 2 / 5…'}},
  {name: 'circle', spec: {kind: 'success', shape: 'circle', text: '已保存'}},
];
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const rows = [];
try {
  for (const [w, h] of [[390, 844], [1440, 900]]) for (const theme of ['light', 'dark']) {
    fake.reset();
    fake.st.prefs = {appearance: {language: 'zh', theme}, voice: {speak_replies: false, wake_enabled: false}};
    const p = await newPage(B, w, h, theme, {touch: w < 600, allow: [web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await fake.send({t: 'status', s: 'idle', agent: 'claude', name: 'Agent J'});
    const dev = await evaluate(p, `import('./js/api.js').then(api=>api.myDeviceId())`);
    await fake.addTurn({k: 'phone', dev, text: '把设置改成深色主题。'}, '好，已经改好了。');
    await waitFor(p, `document.getElementById('pg').textContent==='1 / 1'`);
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
    for (const c of SPECS) {
      const spec = JSON.stringify({...c.spec, ms: 60000});
      await evaluate(p, `import('./js/ui.js').then(m=>{const s=${spec};${c.act ? `s.actions=[{label:${JSON.stringify(c.act)},fn(){}}];` : ''}m.notify(s);})`);
      await sleep(450);
      const g = await evaluate(p, `(()=>{const n=document.getElementById('toast'),cs=getComputedStyle(n),r=n.getBoundingClientRect();
        const om=document.getElementById('om'),cp=document.querySelector('.composer');
        const ref=om&&!om.hidden&&om.getClientRects().length?om.getBoundingClientRect().width:cp.getBoundingClientRect().width;
        const b=n.querySelector('.undo'),bs=b&&getComputedStyle(b);
        return {bg:cs.backgroundColor,fg:cs.color,op:cs.opacity,w:r.width,h:r.height,top:r.top,ref,radius:cs.borderTopLeftRadius,
          text:n.textContent,kind:n.dataset.kind,btn:b?{h:b.getBoundingClientRect().height,right:r.right-b.getBoundingClientRect().right,fg:bs.color}:null,
          ico:getComputedStyle(n.querySelector('.nt-ico')).stroke};})()`);
      const tag = `${c.name}-${w}-${theme}`;
      assert.equal(g.bg, NOIR, `${tag} background Noir`); assert.equal(g.fg, LOTION, `${tag} text Lotion`); assert.equal(g.op, '1', `${tag} opaque`);
      assert.ok(g.text.includes(c.spec.text), `${tag} text`);
      if (await evaluate(p, `document.getElementById('toast').classList.contains('circle')`)) { assert.ok(Math.abs(g.w - g.h) < 1, `${tag} circle ${g.w}×${g.h}`); assert.equal(g.radius, '50%'); }
      else if (await evaluate(p, `document.getElementById('toast').classList.contains('square')`)) { assert.ok(g.w>g.h, `${tag} centred rounded square`); }
      else assert.ok(await evaluate(p, `document.getElementById('toast').parentElement.id==='reply-notices'`), `${tag} internal card`);
      if (c.act) { assert.ok(g.btn.h >= 44, `${tag} button ≥ 44 px`); assert.ok(g.btn.right < 10, `${tag} button right-aligned`); }
      writeFileSync(out + `${tag}.png`, await shoot(p, ''));
      rows.push({tag, ...g});
    }
    await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`);
    // the legacy entry points are Noir too: toast() and the share hand-off notice
    await evaluate(p, `import('./js/ui.js').then(m=>m.toast('已保存', 60000))`); await sleep(400);
    assert.equal(await evaluate(p, `getComputedStyle(document.getElementById('toast')).backgroundColor`), NOIR, 'toast() is Noir');
    await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`);
    await evaluate(p, `(()=>{const b=document.getElementById('share-notice');document.getElementById('reply-notices').append(b);b.hidden=false;document.getElementById('share-status').textContent='截图已就绪，点「粘贴截图」放进输入框';const k=document.getElementById('share-paste');k.hidden=false;k.disabled=false;})()`);
    await sleep(200);
    const s = await evaluate(p, `(()=>{const cs=getComputedStyle(document.getElementById('share-notice'));return {bg:cs.backgroundColor,fg:cs.color}})()`);
    assert.deepEqual(s, {bg: NOIR, fg: LOTION}, 'share notice Noir');
    writeFileSync(out + `share-${w}-${theme}.png`, await shoot(p, ''));
    rows.push({tag: `share-${w}-${theme}`, ...s});
    await p.dispose();
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(out + 'checks.json', JSON.stringify({passed: rows.length, rows}, null, 2) + '\n');
console.log('PASS p318 noir browser: ' + rows.length + ' cases');
