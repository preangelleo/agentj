import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep } from './browser.mjs';

const before = process.env.P0378_BEFORE === '1';
const out = process.env.P0378_UI_OUT;
const base = process.env.P0378_PUBLIC;
const site = 'channels-weixin';
const note = "document.getElementById('lqr-note')";
const frame = "document.getElementById('lqr-frame')";
const picture = "document.getElementById('lqr-img')";
const shown = id => host.log.filter(m => m.t === 'login_qr_shown' && m.id === id);
const card = (letter, png, extra = {}) => ({
  t: 'login_qr', site, id: letter.repeat(32), n: 'b'.repeat(32), ttl: 120,
  title: 'Video Channel', img: png, shown_ack: true, tg: 'text', channel: 'Telegram', ...extra,
});

async function capture(page, label) {
  if (!out) return;
  await evaluate(page, 'document.fonts.ready.then(()=>true)');
  writeFileSync(join(out, label + '.png'), await shoot(page));
}
async function pair(width, theme, lang, green) {
  host.reset();
  host.st.prefs = { appearance: { language: lang, theme }, voice: { speak_replies: false, wake_enabled: false } };
  const url = web.url.replace('127.0.0.1', 'localhost');
  const page = await newPage(browser, width, width === 390 ? 844 : 900, theme, { touch: width === 390, allow: [url, host.relay + '/'] });
  await navigate(page, host.newPairing(url + '?lang=' + lang));
  await waitState(page, 'awaiting-approval');
  await host.approve();
  await waitState(page, 'ready');
  await evaluate(page, "for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b?.offsetParent)b.click()}");
  await host.send({ t: 'status', s: 'idle', name: 'Agent J' });
  if (green) await host.send({ t:'theme', v:1, id:'fixture-green', colors:{ state:{idle:'#394d3f'}, dialog:{background:'#394d3f'} } });
  return page;
}
function pattern(page) {
  // A visible bitmap with no data encoding. The browser produces the PNG received on the normal Noise channel.
  return evaluate(page, `(() => {
    const canvas=document.createElement('canvas');
    canvas.width=canvas.height=100;
    const ctx=canvas.getContext('2d');
    ctx.fillStyle='#faf9f5';ctx.fillRect(0,0,100,100);
    ctx.fillStyle='#141414';
    for(let y=1;y<19;y++) for(let x=1;x<19;x++)
      if ((x*7+y*3)%5<2) ctx.fillRect(x*5,y*5,5,5);
    return canvas.toDataURL('image/png').split(',')[1];
  })()`);
}
async function awaitAck(id) {
  for (let i=0;i<70 && !shown(id).length;i++) await sleep(50);
  assert.equal(shown(id).length,1, 'one authenticated display receipt after the image is presented');
}
async function view(page) {
  await waitFor(page, `${picture}?.complete && ${picture}?.naturalWidth===100 && !${frame}.hidden`);
}
async function firstCard(page, label, lang, png) {
  const active = card('a', png, {title:lang==='zh'?'视频号':'Video Channel'});
  await host.send(active);
  await view(page);
  if (!before) {
    await awaitAck(active.id);
    assert.deepEqual(shown(active.id)[0], { t:'login_qr_shown', id:active.id, n:active.n });
    assert.equal(await evaluate(page, `!${picture}.draggable && document.getElementById('lqr-tg').hidden && document.getElementById('lqr-tgnote').hidden`),true);
  }
  await capture(page, label+'-qr-open');
  await host.send({ t:'login_qr_state', id:active.id, state:'checking' });
  if (!before) await waitFor(page, `${note}.textContent.includes(${JSON.stringify(lang==='zh'?'正在检查登录状态':'Checking sign-in status')})`);
  await capture(page, label+'-qr-checking');
  await host.send({ t:'login_qr_state', id:active.id, state:'waiting' });
  if (!before) await waitFor(page, `${note}.textContent.includes(${JSON.stringify(lang==='zh'?'还没看到登录成功':"doesn't see a sign-in yet")})`);
  await capture(page, label+'-qr-waiting');
  await host.send({ t:'login_qr_done', id:active.id, site, result:'expired' });
  await waitFor(page, `${frame}.hidden && !${picture}.getAttribute('src')`);
  if (!before) assert.equal(await evaluate(page,"document.getElementById('lqr-refresh').hidden"),true);
  await capture(page,label+'-qr-expired');
}
async function controls(page, label, png) {
  const hidden = card('c', png);
  await evaluate(page, "Object.defineProperty(document,'hidden',{configurable:true,get:()=>true})");
  await host.send(hidden);
  await view(page);
  await sleep(1100);
  assert.equal(shown(hidden.id).length,0,'hidden document receives no visual acknowledgement');
  await evaluate(page, `(() => {
    const veil = document.createElement('div');
    veil.id = 'fixture-veil';
    veil.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#2c2b26';
    document.body.append(veil);
    delete document.hidden;
    document.dispatchEvent(new Event('visibilitychange'));
  })()`);
  await waitFor(page, "document.elementFromPoint(innerWidth/2,innerHeight/2)?.id==='fixture-veil'");
  await sleep(1100);
  assert.equal(shown(hidden.id).length,0,'covered image does not count as displayed after returning to the tab');
  await evaluate(page, "document.getElementById('fixture-veil').remove()");
  await awaitAck(hidden.id);
  await host.send(hidden);
  await host.send({ ...hidden, n:'c'.repeat(32) });
  await sleep(1100);
  assert.equal(shown(hidden.id).length,1,'ordinary repeat and nonce mismatch cannot send additional receipts');

  const invalid = card('d','AA==');
  await host.send(invalid);
  await waitFor(page, `${frame}.hidden && /couldn’t display|无法显示二维码/.test(${note}.textContent)`);
  await capture(page,label+'-qr-decode-error');
  assert.equal(shown(invalid.id).length,0,'invalid image bytes cannot acknowledge');
  assert.equal(await evaluate(page,"document.getElementById('lqr-check').hidden"),true);
  await evaluate(page, `document.getElementById('lqr-check').click()`);
  await sleep(75);
  assert.ok(!host.log.some(m=>m.t==='login_qr_check' && m.id===invalid.id));

  // Same-site replacement removes the earlier image. Losing both readiness and generation clears a restricted image.
  const current=card('e', png);
  await host.send(current);
  await view(page);
  await awaitAck(current.id);
  await host.send({ t:'login_qr_done', id:hidden.id, site, result:'done' });
  assert.equal(await evaluate(page,`${picture}.naturalWidth`),100);
  host.kick();
  await waitFor(page, `${picture}.getAttribute('src')===null`);
  await waitState(page,'ready');
  assert.equal(shown(current.id).length,1,'reconnect did not replay the old display receipt');

  const legacy=card('f', png, { shown_ack:undefined, ttl:2 });
  await host.send(legacy);
  await view(page);
  await sleep(1050);
  assert.equal(shown(legacy.id).length,0,'pre-ack hosts continue to use the legacy card contract');
  await waitFor(page, `${frame}.hidden && !${picture}.getAttribute('src')`);
  assert.equal(await evaluate(page,"document.getElementById('lqr-refresh').hidden"),false,'legacy explicit refresh remains available');
}

const web = await startWebServer(base ? { publicDir: base } : {});
const host = await startFakeHost();
const browser = await launch();
let step='fixture';
if (out) mkdirSync(out, {recursive:true});
try {
  for (const width of (out ? [390,768,1440] : [390,1440])) for (const lang of ['zh','en']) {
    for (const choice of (out ? ['light','dark','green'] : ['light','green'])) {
      const page=await pair(width,choice==='light'?'light':'dark',lang,choice==='green');
      const label=`${width}-${lang}-${choice}`;
      try {
        const png=await pattern(page);
        step='show-check-expire';
        await firstCard(page,label,lang,png);
        if (!before && width===390 && choice==='light') {
          step='hidden-occluded-decode-reconnect';
          await controls(page,label,png);
        }
        assert.deepEqual(page.problems,[]);
        assert.deepEqual(page.offsite,[]);
      } finally {
        await page.dispose();
      }
    }
  }
  if (!before) {
    console.log('p0378-login-visual-ack PASS');
    console.log('p0378-login-overdraw-expiry-reconnect PASS');
    console.log('p0378-login-scan-progress PASS');
  }
} catch (error) {
  if (!before) console.error('P44-JOURNEY-FAIL '+JSON.stringify({journey:'login-card-receipt',step,location:'paired-remote'}));
  throw error;
} finally {
  await browser.close();
  await host.stop();
  await web.stop();
}
