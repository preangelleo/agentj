// P491: real phone UI over local Noise; native startup failure has an owner-signed recovery action.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitFor, waitState, evaluate, sleep, shoot} from './browser.mjs';
const out = new URL('../../../reports/qa/p491/', import.meta.url); mkdirSync(out, {recursive: true});
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const checks = [];
try {
  for (const language of ['zh', 'en']) for (const [width, theme] of [[390,'light'], [1440,'dark']]) {
    fake.reset();
    const controls = [];
    fake.st.onApp = async (c, m, send) => {
      if (m.t !== 'rescue_do') return false;
      const ok = m.action === 'restart_session' && await fake.verifyControl(c, m.t, m, {action:'restart_session'});
      controls.push({ok, action:m.action});
      await send({t:'reset_done', r:m.r, flow:'restart_session', ok, text:'Session ready'});
      return true;
    };
    const p = await newPage(B, width, 844, theme, {touch:true, allow:[web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url + '?lang=' + language));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    const note = language === 'zh' ? 'OpenCode 还没准备好，这条消息没有交给它。' : 'OpenCode is not ready. Your message was not delivered.';
    await fake.addTurn({k:'sys', text:''}, note, 'done', {recovery:'restart_session'});
    await waitFor(p, `!!document.querySelector('#cmdx [data-recovery="restart_session"]')`, 5000);
    const label = await evaluate(p, `document.querySelector('#cmdx button').textContent`);
    assert.equal(label, language === 'zh' ? '重开 AI 会话' : 'Reopen AI session');
    assert.equal(await evaluate(p, `document.getElementById('words').textContent.includes('AttributeError')`), false);
    writeFileSync(new URL(`recovery-${language}-${width}-${theme}.png`,out), await shoot(p,''));
    await evaluate(p, `document.querySelector('#cmdx button').click()`);
    await waitFor(p, `!document.getElementById('confirm').hidden`,3000);
    await evaluate(p, `document.getElementById('confirm-no').click()`); await sleep(100);
    assert.equal(controls.length,0,'cancel sends no restart');
    await evaluate(p, `document.querySelector('#cmdx button').click(); document.getElementById('confirm-yes').click()`);
    await waitFor(p, `document.getElementById('confirm').hidden`,3000); await sleep(300);
    assert.deepEqual(controls,[{ok:true,action:'restart_session'}]);
    // Durable history after reload still carries the action, without sending it automatically.
    await navigate(p, web.url + '?lang=' + language); await waitState(p,'ready');
    await waitFor(p, `!!document.querySelector('#cmdx [data-recovery="restart_session"]')`,5000);
    assert.equal(controls.length,1);
    checks.push(`${language}/${width}/${theme}: visible, cancel safe, one signed restart, history replay safe`);
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(new URL('phone.json',out), JSON.stringify({ok:true,checks},null,2)+'\n');
console.log('P491 phone PASS',checks);

if (process.env.AJ_PARITY_OUT) writeFileSync(process.env.AJ_PARITY_OUT, JSON.stringify({suite:'web/test/p491.mjs',results:{'p491-opencode-startup':'pass'}}));
