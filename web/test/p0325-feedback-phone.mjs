import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdirSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate, shoot} from './browser.mjs';

const host = fileURLToPath(new URL('../../host/', import.meta.url));
const fixture = fileURLToPath(new URL('./p0325-feedback-record.py', import.meta.url));
const run = spawnSync(process.env.AJ_PYTHON ? resolve(process.env.AJ_PYTHON) : resolve(host,'.venv/bin/python'), [fixture], {
  cwd: host, encoding:'utf8', env:{...process.env, PYTHONPATH:[host, resolve(host,'tests')].join(':')}
});
if (run.error) throw run.error;
assert.equal(run.status,0,run.stderr);
const record = JSON.parse(run.stdout);
const out = process.env.P0325_PHONE_OUT;
if (out) mkdirSync(out, {recursive:true});
const beforeDir = process.env.P0325_BEFORE;
const web = await startWebServer(beforeDir ? {publicDir:resolve(beforeDir)} : {});
const fake = await startFakeHost();
const browser = await launch();
const words = "document.getElementById('words').innerText.trim()";
const copy = {zh:'反馈尚未发送。请保留草稿，等隐私检查恢复后再提交。',
  en:'Your feedback hasn’t been sent. Keep your draft and submit it again when the privacy check is available.'};
try {
  for (const width of [390,1440]) for (const lang of ['zh','en']) for (const theme of ['light','dark']) {
    fake.reset();
    const p = await newPage(browser, width, width===390 ? 844 : 900, theme, {allow:[web.url, fake.relay+'/']});
    try {
      await navigate(p, fake.newPairing(web.url+'?lang='+lang));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, "document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()");
      await fake.send({t:'preferences',value:{appearance:{language:lang,theme}},problem:null});
      await waitFor(p, 'document.documentElement.dataset.theme==='+JSON.stringify(theme));
      const before = fake.log.length;
      await fake.addTurn(record.src,record.reply.text,record.end,record.card);
      await waitFor(p, words+'==='+JSON.stringify(beforeDir ? record.reply.text : copy[lang]));
      assert.equal(await evaluate(p,"document.querySelectorAll('#cmdx button,#cmdx a,#words button,#words a').length"),0);
      if (out) writeFileSync(resolve(out, [lang,width,theme].join('-')+'.png'), await shoot(p));
      for (const [src,card] of [[{k:'phone'},record.card],[{k:'friend_report'},record.card],[{k:'unknown'},record.card],
                                 [{k:'sys',via:'hook'},record.card],[record.src,{...record.card,draft:'example'}]]) {
        const raw = record.reply.text.replace('peer_unverified', 'control_'+fake.st.turns.length);
        await fake.addTurn(src,raw,record.end,card);
        await waitFor(p,words+'==='+JSON.stringify(raw));
      }
      assert.ok(!fake.log.slice(before).some(m=>/^(?:say|feedback|support|fr_report_reply|wf_resume)/.test(m.t)));
      assert.deepEqual(p.problems,[]);
      console.log([lang,width,theme].join('/')+' host feedback with five controls PASS');
    } finally { await p.close(); }
  }
} finally { await browser.close(); await fake.stop(); await web.stop(); }
