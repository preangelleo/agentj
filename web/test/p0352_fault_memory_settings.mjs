// A paired phone writes Settings through its normal encrypted protocol; a temporary host
// preference file survives independent Python processes. No installed host or owner state.
import assert from 'node:assert/strict';
import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, evaluate, waitFor } from './browser.mjs';

const host = fileURLToPath(new URL('../../host/', import.meta.url));
const python = process.env.PYTHON || (existsSync(host+'.venv/bin/python') ? host+'.venv/bin/python' : 'python3');
const tmp = mkdtempSync(join(tmpdir(), 'aj-pref-'));
const script = `
from agentj import preferences as p, serve
from agentj.state import State
import json, sys
st = State()
if not st.config_path.exists(): st.init(relay="ws://127.0.0.1:1")
host=serve.Host(st,events="jsonl",read_stdin=False)
if len(sys.argv) > 1:
    import asyncio
    ack=asyncio.run(host.set_pref(sys.argv[1], json.loads(sys.argv[2])))
    assert ack.get('ok'), ack
msg=host.preferences_msg()
print(json.dumps(dict(t=msg['t'], value={k:msg['value'][k] for k in ('heartbeat','support')}, host={'version':msg['host']['version'], 'settable':msg['host']['settable']})))
`;
const config = (...args) => JSON.parse(execFileSync(python, ['-c', script, ...args], {cwd:host,encoding:'utf8',env:{...process.env,HOME:tmp,XDG_CONFIG_HOME:join(tmp,'etc'),AGENTJ_STATE_DIR:join(tmp,'state'),PYTHONPATH:host,AGENTJ_TEST:'1'}}));
test('a paired phone persists and reopens the nightly memory window, time zone and both switches', async () => {
  const web = await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});
  const fake = await startFakeHost(), b = await launch();
  const prefs = config;
  try {
    const p=await newPage(b,390,844,'light',{allow:[web.url,fake.relay+'/'],touch:true});
    await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
    fake.st.onApp=async (_c,m,send) => {
      if(m.t!=='pref_set')return false;
      assert.ok(prefs().host.settable.includes(m.key));config(m.key,JSON.stringify(m.value));
      await send({t:'pref_res',r:m.r,key:m.key,ok:true});await send(prefs());return true;
    };
    await fake.send(prefs());
    await evaluate(p,"document.querySelector('#phone-env-dismiss')?.click();document.querySelector('#setBtn').click()");
    await waitFor(p,"!document.querySelector('#set-tidy-row').hidden");
    await waitFor(p,"!document.querySelector('#set-fault-row').hidden");
    assert.equal(await evaluate(p,"document.querySelector('#set-tidy-timezone').value"),'');
    assert.equal(await evaluate(p,"document.querySelector('#set-tidy-timezone').placeholder"),'跟随主机');
    assert.equal(await evaluate(p,"document.querySelector('#set-fault-label').textContent"),'自动向客服上报 Agent J 故障');
    const initial=prefs().value.heartbeat;
    for(const [key,value] of [['start','02:00'],['end','04:00'],['timezone','Asia/Tokyo']]) {
      await evaluate(p,`(()=>{const input=document.querySelector('#set-tidy-${key}');input.value=${JSON.stringify(value)};input.dispatchEvent(new Event('change'));})()`);
      for(let i=0;i<30 && prefs().value.heartbeat['memory_tidy_'+key]!==value;i++) await new Promise(r=>setTimeout(r,100));
      assert.equal(prefs().value.heartbeat['memory_tidy_'+key],value);
    }
    await evaluate(p,"document.querySelector('#set-tidy').click()");
    await waitFor(p,"document.querySelector('#set-tidy-start').disabled");
    const saved=prefs().value.heartbeat;
    assert.equal(saved.memory_tidy_enabled,false);
    assert.equal(saved.enabled,initial.enabled);
    assert.equal(saved.quiet_start,initial.quiet_start);
    await evaluate(p,"document.querySelector('#set-fault').click()");
    await waitFor(p,"document.querySelector('#set-fault').getAttribute('aria-checked')==='false'");
    assert.equal(prefs().value.support.auto_report,false);
    // The same paired browser reopens the ordinary URL. A new Host instance reads disk.
    await navigate(p,web.url);await waitState(p,'ready');await fake.send(prefs());
    await evaluate(p,"document.querySelector('#setBtn').click()");
    await waitFor(p,"document.querySelector('#set-tidy-timezone').value==='Asia/Tokyo'");
    assert.deepEqual(await evaluate(p,"['start','end'].map(k=>document.querySelector('#set-tidy-'+k).value)"),['02:00','04:00']);
    assert.deepEqual(await evaluate(p,"['set-tidy','set-fault'].map(id=>document.getElementById(id).getAttribute('aria-checked'))"),['false','false']);
    await evaluate(p,"document.querySelector('#set-tidy').click()");
    await waitFor(p,"!document.querySelector('#set-tidy-timezone').disabled");
    await evaluate(p,"const zone=document.querySelector('#set-tidy-timezone');zone.value='';zone.dispatchEvent(new Event('change'))");
    for(let i=0;i<30 && prefs().value.heartbeat.memory_tidy_timezone!=='';i++) await new Promise(r=>setTimeout(r,100));
    assert.equal(prefs().value.heartbeat.memory_tidy_timezone,'');
  } finally {await b.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
});
