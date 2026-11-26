import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { b64u } from '../../protocol/wire.js';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep } from './browser.mjs';

const before=process.env.P0378_BEFORE==='1';
const out=process.env.P0378_UI_OUT;
const base=process.env.P0378_PUBLIC;
const fa=b64u(new Uint8Array(16).fill(5));
const fa_ch=b64u(new Uint8Array(32).fill(2));
const tag='Tn'+'A'.repeat(20);
const modes=[
  ['uv','p0378.pk.testUv','p539.fail.retry'],
  ['origin','p0378.pk.testOrigin',null],
  ['challenge','p0300.pk.testExpired','p539.fail.retry'],
  ['credential','p0300.pk.testRejected','p0300.pk.setup'],
];
const web=await startWebServer(base?{publicDir:base}:{});
const host=await startFakeHost();
const browser=await launch();
const url=web.url.replace('127.0.0.1','localhost');
let step='open-settings';
if(out) mkdirSync(out,{recursive:true});
try {
  for(const width of (out?[390,768,1440]:[390,1440])) for(const theme of ['light','dark']) for(const lang of ['zh','en']) {
    host.reset();
    host.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false}};
    const page=await newPage(browser,width,width===390?844:900,theme,{allow:[url,host.relay+'/']});
    try {
      await navigate(page,host.newPairing(url+'?lang='+lang));
      await waitState(page,'awaiting-approval');
      await host.approve();
      await waitState(page,'ready');
      await host.send({t:'pk_state',s:'local',fa,fa_ch,n:tag});
      // This drives Settings and the ordinary wire handler with a virtual authenticator encounter.
      await evaluate(page,`(() => {
        for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b?.offsetParent)b.click()}
        navigator.credentials.get=options=>Promise.resolve({
          rawId:options.publicKey.allowCredentials[0].id, authenticatorAttachment:'platform',
          response:{clientDataJSON:new ArrayBuffer(40),authenticatorData:new ArrayBuffer(37),signature:new ArrayBuffer(70),userHandle:null}
        });
        document.getElementById('setBtn').click();
      })()`);
      await waitFor(page,"document.getElementById('set-pk-test')?.offsetWidth>0");
      for(const [reason,currentKey,currentAction] of modes) {
        step=reason;
        const key=before?'p0300.pk.testRejected':currentKey;
        const action=before?'p0300.pk.setup':currentAction;
        let submitted=0;
        host.st.onApp=async (_c,message,send)=>{
          if(message.t==='pk_check_ch'){await send({t:'pk_check_ch_res',r:message.r,fa,fa_ch,n:tag});return true;}
          if(message.t==='pk_check'){submitted++;await send({t:'pk_check_res',r:message.r,ok:false,why:reason});return true;}
          return false;
        };
        const words=await evaluate(page,`import('./js/pkassist.js').then(m=>m.withBio(${JSON.stringify(key)}))`);
        assert.notEqual(words,key);
        await evaluate(page,"document.getElementById('set-pk-test').click()");
        for(let i=0;i<80&&!submitted;i++) await sleep(50);
        await waitFor(page,`document.querySelector('#toast.on[data-pk-test="1"] .nt-body')?.textContent===${JSON.stringify(words)}`);
        const observed=await evaluate(page,`(() => {
          const toast=document.getElementById('toast'), text=toast.querySelector('.nt-body'), body=toast.getBoundingClientRect(), inner=text.getBoundingClientRect();
          return {
            action:toast.querySelector('.nt-acts button')?.textContent??null,
            color:getComputedStyle(toast).backgroundColor, fits:inner.top>=body.top&&inner.bottom<=body.bottom&&inner.right<=body.right
          };
        })()`);
        const expected=action===null?null:await evaluate(page,`import('./js/t.js').then(m=>m.t(${JSON.stringify(action)}))`);
        assert.equal(observed.action,expected);
        if(!before) {
          assert.equal(observed.color,'rgb(44, 43, 38)');
          assert.ok(observed.fits,'all diagnostic copy fits in the Noir notice');
        }
        assert.equal(submitted,1);
        if(out) writeFileSync(join(out,`${width}-${lang}-${theme}-passkey-${reason}.png`),await shoot(page));
      }
      assert.deepEqual(page.problems,[]);
      assert.deepEqual(page.offsite,[]);
    } finally {
      host.st.onApp=null;
      await page.dispose();
    }
  }
  if(!before) console.log('p0378-touch-id-settings-journey PASS');
} catch(error) {
  if(!before) console.error('P44-JOURNEY-FAIL '+JSON.stringify({journey:'passkey-diagnostic',step,location:'settings-test-notice'}));
  throw error;
} finally {
  await browser.close();
  await host.stop();
  await web.stop();
}
