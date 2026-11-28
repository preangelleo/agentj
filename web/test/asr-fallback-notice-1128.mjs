#!/usr/bin/env node
// A real touch-held recording in isolated Chromium and the real encrypted blob/say transport.
// The paired local fixture supplies ASR route outcomes and a synthetic long recording.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { launch, newPage, navigate, waitFor, waitState, evaluate, rect, tap, shoot, sleep } from './browser.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

const message = '远端转写不可用，已改用本机模型转写。';
const out = resolve(process.env.ASR_1128_OUT || 'reports/qa/asr-fallback-notice-1128');
const publicDir = process.env.ASR_1128_PUBLIC;
const web = await startWebServer(publicDir ? { publicDir: resolve(publicDir) } : {});
const host = await startFakeHost();
const browser = await launch();
const rows = [];
const errors = [];
mkdirSync(out, { recursive: true });

const fallbackVisible = `[...document.querySelectorAll('#asrNotice,.toast.on')].some(n=>!n.hidden&&n.getClientRects().length&&n.textContent.includes(${JSON.stringify(message)}))`;
const measure = p => evaluate(p, `(() => {
  const box = document.querySelector('.composer').getBoundingClientRect();
  const input = document.getElementById('input').getBoundingClientRect();
  const tools = document.getElementById('tools').getBoundingClientRect();
  const notice = [...document.querySelectorAll('#asrNotice,.toast.on')].find(n => !n.hidden && n.getClientRects().length && n.textContent.includes(${JSON.stringify(message)}));
  return {width: innerWidth, composer: box.height, input: input.height, inputTop: input.top, toolsGap: input.top-tools.bottom,
    notice: notice ? {height:notice.getBoundingClientRect().height, position:getComputedStyle(notice).position,
      color:getComputedStyle(notice.querySelector('.nt-text') || notice).color,
      lotion:getComputedStyle(document.documentElement).getPropertyValue('--c-lotion').trim()} : null};
})()`);
const layout = ({width, composer, input, inputTop, toolsGap}) => ({width, composer, input, inputTop, toolsGap});
const effect = p => evaluate(p, 'window.__asrNotices');
const nativeRecording = seconds => `(() => {
  const n = ${seconds} * 16000, bytes = new Uint8Array(44+n*2), v = new DataView(bytes.buffer);
  for(const [offset, tag] of [[0,'RIFF'],[8,'WAVE'],[12,'fmt '],[36,'data']]) {
    for(let i=0;i<tag.length;i++) bytes[offset+i]=tag.charCodeAt(i);
  }
  v.setUint32(4,36+n*2,true); v.setUint32(16,16,true); v.setUint16(20,1,true); v.setUint16(22,1,true);
  v.setUint32(24,16000,true); v.setUint32(28,32000,true); v.setUint16(32,2,true); v.setUint16(34,16,true); v.setUint32(40,n*2,true);
  let bin=''; for(let i=0;i<bytes.length;i+=8192) bin+=String.fromCharCode(...bytes.subarray(i,i+8192));
  return window.relayNative.take(btoa(bin),${seconds});
})()`;

async function record(p, text, { fallback = false, remote = false, hold = false } = {}) {
  host.st.asrText = text;
  host.st.asrNotice = fallback ? 'local_fallback' : undefined;
  host.st.asrRemote = remote;
  if (hold) {
    const { x, y } = await rect(p, '#mic');
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    try {
      await waitFor(p, "!document.getElementById('ptt').hidden && /^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)", 4000);
      await sleep(1070);
    } finally {
      await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    }
  } else {
    assert.equal(await evaluate(p, nativeRecording(1)), true);
    await waitFor(p, `document.getElementById('input').value===${JSON.stringify(text)}`);
    const send = await rect(p, '#send');
    await tap(p, send.x, send.y);
  }
  for (let i = 0; i < 120 && !host.st.says.some(m => m.text === text); i++) await sleep(50);
  assert.equal(host.st.says.filter(m => m.text === text).length, 1, 'recording sends exactly once');
  await waitFor(p, "document.getElementById('asrTag').hidden && document.getElementById('input').value===''");
}

function capture(p, theme, name) {
  return shoot(p).then(buffer => writeFileSync(join(out, `${theme}-${name}.png`), buffer));
}

try {
  for (const theme of ['light', 'dark']) {
    host.reset();
    host.st.prefs = { appearance: { language: 'zh', theme } };
    const p = await newPage(browser, 390, 844, theme, { touch: true, allow: [web.url, host.relay + '/'] });
    const row = { theme };
    rows.push(row);
    try {
      await navigate(p, host.newPairing(web.url));
      await waitState(p, 'awaiting-approval');
      await host.approve();
      await waitState(p, 'ready');
      await host.send({ t: 'status', s: 'idle', agent: 'claude', name: 'TensorBook（验收）' });
      await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();
        document.documentElement.dataset.theme=${JSON.stringify(theme)};
        window.__asrNotices=0;
        window.__asrNoticeEdges=[];
        let visible=false;
        new MutationObserver(() => {
          const next=${fallbackVisible};
          if(next!==visible) window.__asrNoticeEdges.push({visible:next, at:performance.now()});
          if(next&&!visible) window.__asrNotices++;
          visible=next;
        }).observe(document.body,{subtree:true,attributes:true,childList:true,characterData:true});`);
      await waitFor(p, "document.body.dataset.status==='idle' && document.getElementById('mic').getBoundingClientRect().height>0");
      row.before = await measure(p);
      assert.equal(row.before.width, 390);
      assert.equal(row.before.notice, null);

      await record(p, '隔离验收语音一', { fallback: true, hold: true });
      await waitFor(p, fallbackVisible);
      row.shown = await measure(p);
      await capture(p, theme, 'shown');
      const shownAt = Date.now();
      for (let i = 0; i < 80 && await evaluate(p, fallbackVisible); i++) await sleep(50);
      row.observedMs = Date.now() - shownAt;
      row.after = await measure(p);
      const [shown, hidden] = await evaluate(p, 'window.__asrNoticeEdges');
      row.visibleMs = hidden && Math.round(hidden.at - shown.at);
      await capture(p, theme, 'after');

      await record(p, '隔离验收语音二', { fallback: true });
      row.repeat = await measure(p);
      row.noticeCount = await effect(p);
      assert.equal(row.after.notice, null, 'fallback clears without recording again');
      assert.equal(row.shown.notice.position, 'fixed', 'reuse a floating notice');
      assert.deepEqual(layout(row.shown), layout(row.before), 'even an active notice consumes no composer height');
      assert.deepEqual(layout(row.after), layout(row.before), 'composer returns to the same pixel layout');
      assert.equal(row.repeat.notice, null, 'same outage does not show another notice');
      assert.equal(row.noticeCount, 1);
      assert.ok(row.visibleMs >= 2800 && row.visibleMs <= 3300, 'notice is visible for about three seconds');
      assert.equal(row.shown.notice.color, 'rgb(250, 249, 245)', 'notice text uses Lotion');

      await record(p, '隔离验收本机成功');
      assert.equal(await effect(p), 1, 'a local success has no recovery semantics');
      await record(p, '隔离验收仍在回退', { fallback: true });
      assert.equal(await effect(p), 1, 'a local success does not rearm the notice');
      await record(p, '隔离验收远端恢复', { remote: true });
      assert.equal(await effect(p), 1, 'remote recovery is silent');
      await record(p, '隔离验收新故障', { fallback: true });
      await waitFor(p, fallbackVisible);
      assert.equal(await effect(p), 2, 'a distinct outage may display once');
      // Recover via an actual attached recording send; only the live route receipt is consumed.
      host.st.onApp = async (_, m) => {
        if (m.t === 'say' && m.att?.length) {
          const route = host.st.longRoute;
          setTimeout(() => host.send({ t: 'say_state', sid: m.sid, s: 'asr_route', ...route }), 250);
        }
        return false;
      };
      async function sendRecording(route) {
        host.st.longRoute = route;
        assert.equal(await evaluate(p, nativeRecording(95)), true);
        await waitFor(p, "document.querySelector('#tray .chip[data-st=ready]')");
        const sent = host.st.says.length;
        const send = await rect(p, '#send');
        await tap(p, send.x, send.y);
        for (let i=0; i<100 && host.st.says.length===sent; i++) await sleep(50);
        assert.equal(host.st.says[sent]?.att?.length, 1, 'recording attachment uses real blob/say transport');
      }
      await sendRecording({ remote: true });
      await waitFor(p, `!(${fallbackVisible})`);
      assert.equal(await effect(p), 2);
      const attachment = { name: '录音.webm', mime: 'audio/webm', bytes: 32000, kind: 'audio',
        asr: { ok: true, text: '合成的附件转写', notice: 'local_fallback' } };
      await host.addTurn({ k: 'phone', text: '旧录音', att: [attachment] }, '验收');
      await sleep(250);
      await evaluate(p, "if(document.getElementById('omMore').getAttribute('aria-expanded')==='false') document.getElementById('omMore').click()");
      await waitFor(p, "document.querySelector('.omasr-text')?.textContent==='合成的附件转写'");
      assert.equal(await evaluate(p, 'document.querySelector(".omasr summary").textContent.includes(' + JSON.stringify(message) + ')'), false);
      assert.equal(await effect(p), 2, 'history and its transcript do not replay a notice');
      await sendRecording({ notice: 'local_fallback' });
      await waitFor(p, fallbackVisible);
      row.finalNoticeCount = await effect(p);
      assert.equal(row.finalNoticeCount, 3);
      const reloaded = p.waitFor('Page.loadEventFired');
      await p.send('Page.reload');
      await reloaded;
      await waitState(p, 'ready');
      await host.send({ t: 'status', s: 'idle', agent: 'claude', name: 'TensorBook（验收）' });
      await evaluate(p, "document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()");
      await waitFor(p, "document.body.dataset.status==='idle'");
      await record(p, '同次故障网页重开', { fallback: true });
      row.reloadSilent = !(await evaluate(p, fallbackVisible));
      assert.equal(row.reloadSilent, true, 'same outage is silent after reloading the paired tab');

      assert.deepEqual(p.offsite, []);
      assert.deepEqual(p.problems, []);
    } catch (error) {
      row.error = String(error);
      errors.push(error);
    } finally {
      await p.dispose();
    }
  }
} finally {
  writeFileSync(join(out, 'results.json'), JSON.stringify(rows, null, 2) + '\n');
  await browser.close();
  await host.stop();
  await web.stop();
}
console.log(JSON.stringify(rows));
if (errors.length) throw new AggregateError(errors, 'ASR fallback notice journey failed');
