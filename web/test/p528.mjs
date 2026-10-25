// P528 bilingual real Chromium interactions, fake encrypted host, synthetic data only.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot } from './browser.mjs';
import { b64u } from '../../protocol/wire.js';
const out = process.env.P528_OUT || '/tmp/p528-screens';
mkdirSync(out, {recursive:true});
const web = await startWebServer({port:0, relayCsp:'ws://127.0.0.1:*'});
const fake = await startFakeHost();
let browser;
try {
  browser = await launch();
  for (const lang of ['zh','en']) {
    fake.reset();
    const p = await newPage(browser, 390, 844, 'light', {touch:true, allow:[web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url + (lang==='en'?'?lang=en':'')));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
    const kp = await webcrypto.subtle.generateKey({name:'X25519'},true,['deriveBits']);
    const epk = b64u(new Uint8Array(await webcrypto.subtle.exportKey('raw',kp.publicKey)));
    const card = {t:'elev',id:'5'.repeat(32),kind:'sudo',n:'6'.repeat(32),epk,ttl:120,tries:2,bad:1,
      cmd:'synthetic-check',why:lang==='zh'?'检查合成数据':'Check synthetic data',effect:''};
    await fake.send(card);
    await waitFor(p, `document.querySelector('#elev-bad')?.textContent.includes(${JSON.stringify(lang==='zh'?'手动输入':'autofill')})`);
    writeFileSync(`${out}/sudo-auth-${lang}.png`, await shoot(p, 'unused'));
    // Actual click: decline closes the card through a signed answer; never types a real password.
    await evaluate(p, `document.querySelector('#elev-deny').click()`);
    await fake.send({t:'elev_done',id:card.id,result:'denied'});
    await waitFor(p, `document.querySelector('#elev')?.hidden`);
    await fake.send({...card,id:'7'.repeat(32),bad:0});
    await waitFor(p, `!document.querySelector('#elev')?.hidden`);
    await fake.send({t:'elev_done',id:'7'.repeat(32),result:'done',code:1,summary:'synthetic-check: permission denied <email>'});
    await waitFor(p, `document.body.innerText.includes('synthetic-check: permission denied')`);
    const text=await evaluate(p, 'document.body.innerText');
    assert.ok(text.includes(lang==='zh'?'退出码 1':'exit code 1'));
    writeFileSync(`${out}/sudo-command-${lang}.png`, await shoot(p, 'unused'));
    const title = lang==='zh' ? 'Synthetic TB 派来任务' : 'Synthetic TB sent a task';
    const task=await fake.addTurn({k:'task',via:'same-owner',name:title,text:lang==='zh'?'检查合成数据服务':'Check the synthetic data service'}, lang==='zh'?'正在办理，敏感步骤会请你批准。':'Working on it. Sensitive steps require your approval.', 'open');
    await waitFor(p, `document.body.innerText.includes(${JSON.stringify('Check the synthetic data service')}) || document.body.innerText.includes('检查合成数据服务')`);
    await waitFor(p, `document.querySelector('#omLabel')?.textContent === ${JSON.stringify(title)}`);
    await waitFor(p, `!document.querySelector('#toast')?.classList.contains('on')`);
    writeFileSync(`${out}/task-${lang}.png`, await shoot(p,'unused'));
    await fake.updateTurn(task.id,{end:'failed',reply:{text:lang==='zh'?'任务未完成，请在接收设备查看状态。':'The task did not complete. Check the receiving device.'}});
    assert.equal(p.problems.length,0,p.problems.join('\n'));
    await p.dispose();
  }
  console.log('PASS P528: ZH/EN authentication retry, signed decline, command failure summary; 6 screenshots');
} finally {
  if(browser) await browser.close();
  await fake.stop(); await web.stop();
}
