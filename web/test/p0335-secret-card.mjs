// Product UI regression on a local paired Noise connection, with generated credentials and synthetic turns.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {randomBytes} from 'node:crypto';
import {resolve} from 'node:path';
import {startFakeHost} from './fakehost.mjs';
import {startWebServer} from './serve.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep} from './browser.mjs';
import {approvalEnvelopeDigest, b64u} from '../../protocol/wire.js';

const out = resolve(process.env.AJ_SECRET_SHOTS || 'reports/qa/0335-secret');
mkdirSync(out, {recursive: true});
const web = await startWebServer({publicDir: process.env.AJ_PREVIEW_PUBLIC});
const host = await startFakeHost(), browser = await launch();
const sample = 'example value for a local test';
try {
  for (const width of [390, 1440]) for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
    host.reset();
    host.st.prefs = {appearance: {language: lang, theme}, voice: {speak_replies: false, wake_enabled: false}};
    const page = await newPage(browser, width, width === 390 ? 844 : 900, theme, {touch: width === 390, allow: [web.url, host.relay + '/']});
    const zh = lang === 'zh';
    const explanation = zh ? '请先打开服务商控制台，复制 API 密钥，再点下面的密钥卡。' : 'Copy the API key from your provider, then open the key card below.';
    const prompt = zh ? '帮我配置日历服务。' : 'Set up the calendar service for me.';
    const id = randomBytes(16).toString('hex');
    try {
      await navigate(page, host.newPairing(web.url + '?lang=' + lang));
      await waitState(page, 'awaiting-approval'); await host.approve(); await waitState(page, 'ready');
      await host.send({t:'status', s:'idle', name:'Agent J'});
      await evaluate(page, "for (const id of ['phone-env-dismiss','a2hs-ok','confirm-no']) {const b=document.getElementById(id); if (b?.offsetParent) b.click()}");
      const turn = await host.addTurn({k:'phone', name:zh?'你':'You', text:prompt}, '');
      const pair = await crypto.subtle.generateKey({name:'X25519'}, true, ['deriveBits']);
      const card = {t:'elev', id, kind:'secret', n:randomBytes(16).toString('hex'), epk:b64u(new Uint8Array(await crypto.subtle.exportKey('raw',pair.publicKey))),
        ttl:600, tries:3, name:'CALENDAR_API_KEY', purpose:zh?'连接日历服务':'Connect the calendar service', dest:zh?'本机密钥库':'Local key store', verify:'', button_only:true, passkey_required:false, approval_v:2, revision:1, review_state:'pending'};
      card.envelope = {v:2, channel:host.channel, session:String(host.st.epoch), request_id:id, revision:1, special:{tool:'SecretInput'},
        title:zh?'保存日历密钥':'Save the calendar key', action:card.purpose, details:card.dest, expires_at:Math.floor(Date.now()/1000)+600,
        source:{message_id:turn.id, root_message_id:turn.id, session:String(host.st.epoch), channel:'phone', sender:zh?'你':'You', text:prompt, verified:true, suspicious:false}};
      card.envelope_sha256 = await approvalEnvelopeDigest(card.envelope);
      // A host card can beat the reply to the phone.
      await host.send(card);
      await waitFor(page, "document.getElementById('elev-run')?.hidden===false && document.getElementById('elev')?.hidden");
      await host.updateTurn(turn.id, {reply:{text:explanation}});
      await waitFor(page, 'document.getElementById("words").innerText.includes('+JSON.stringify(explanation)+')');
      await sleep(250);
      assert.ok(await evaluate(page, "(()=>{const body=document.querySelector('#words p')||document.querySelector('#words'),r=body.getBoundingClientRect(),strip=document.querySelector('#elev-run-main').getBoundingClientRect(); const hit=document.elementFromPoint(r.x+r.width/2,r.y+Math.min(12,r.height/2)); return body.contains(hit)&&strip.y>=r.y+r.height-1&&strip.bottom<=innerHeight})()"),'reply is visible with the key card underneath');
      const snap = async scene => writeFileSync(resolve(out, [width,lang,theme,scene].join('-')+'.png'), await shoot(page));
      await snap('reply');
      await evaluate(page, "document.getElementById('elev-run-main').click()");
      await waitFor(page, "document.querySelector('#elev .auth-warn541')?.hidden && document.querySelector('#elev-input')?.offsetWidth>0");
      assert.equal(await evaluate(page, "document.querySelector('#elev .auth-text541').innerText"),prompt);
      await evaluate(page, "document.activeElement?.blur()");
      await snap('form');
      if(width===390 && lang==='zh' && theme==='light'){
        const away=await newPage(browser,390,844,theme,{touch:true});
        try {
          await away.send('Page.bringToFront');
          await host.addTurn({k:'webhook',text:'Calendar tick'}, 'OK');
          const started = Date.now();
          await sleep(120000);
          assert.ok(Date.now()-started >= 120000);
          await page.send('Page.bringToFront');
        } finally {await away.dispose();}
      }
      await evaluate(page, "document.getElementById('elev-input').focus()");
      await page.send('Input.insertText',{text:sample});
      await waitFor(page,"!document.getElementById('elev-allow').disabled");
      await evaluate(page, "document.getElementById('elev-allow').click()");
      let answer;
      for (let i=0;i<40&&!answer;i++){answer=host.log.find(a=>a.t==='elev_answer'&&a.id===id); if(!answer)await sleep(100);}
      assert.ok(answer?.ok && answer.ct && answer.sig, 'paired phone sent its sealed signed answer');
      assert.ok(!JSON.stringify(answer).includes(sample));
      await host.send({t:'elev_done', id, result:'saved'});
      await waitFor(page, "document.getElementById('elev').hidden && !document.querySelector('#elev-input').value");
      const external = structuredClone(card);
      external.id = external.envelope.request_id = randomBytes(16).toString('hex');
      external.envelope.source = {channel:'webhook', verified:false, suspicious:true, text:''};
      external.envelope_sha256 = await approvalEnvelopeDigest(external.envelope);
      await host.send(external);
      await waitFor(page, "document.querySelector('#elev-run-main')?.offsetWidth>0");
      await evaluate(page, "document.getElementById('elev-run-main').click()");
      await waitFor(page, "document.querySelector('#elev .auth-warn541')?.offsetWidth>0");
      console.log('PASS', width, lang, theme);
    } finally {await page.dispose();}
  }
} finally {await browser.close();await host.stop();await web.stop();}
