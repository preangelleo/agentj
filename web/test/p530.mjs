// Real Settings click -> synthetic Noise -> real Host admission/background mail/Elevator -> real input DOM.
import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const py=process.env.AJ_PYTHON||execFileSync('git',['rev-parse','--path-format=absolute','--git-common-dir'],{encoding:'utf8'}).trim()+'/../host/.venv/bin/python';
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const checks=[];
try {
 for(const width of [390,1440]) {
  fake.reset();
  const child=spawn(py,['web/test/p530_host.py'],{stdio:['pipe','pipe','pipe']});
  let send,error='';child.stderr.on('data',b=>error+=b);
  const lines=createInterface({input:child.stdout});
  const handled=[];
  lines.on('line',line=>{const m=JSON.parse(line);handled.push(m);if(send)void send(m);});
  fake.st.onApp=async(c,m,reply)=>{
   if(!['mail_get','mail_key'].includes(m.t))return false;
   send=reply;child.stdin.write(JSON.stringify(m)+'\n');return true;
  };
  const p=await newPage(B,width,900,'dark',{allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,"import('./js/settings.js').then(m=>m.openMail())");
   await waitFor(p,"document.getElementById('mail-addr').value==='demo@example.com'");
   await evaluate(p,"document.getElementById('mail-key').click()");
   await waitFor(p,"document.getElementById('elev-input')&&!document.getElementById('elev').hidden");
   assert.ok(handled.some(m=>m.t==='elev'&&m.name==='AGENTJ_MAIL_PASSWORD'));
   assert.equal(await evaluate(p,"(()=>{const e=document.getElementById('elev-input'),r=e.getBoundingClientRect();return e.type==='password'&&r.width>0&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===e})()"),true);
   if(process.env.P530_SHOTS){mkdirSync(process.env.P530_SHOTS,{recursive:true});writeFileSync(`${process.env.P530_SHOTS}/mail-card-${width}.png`,await shoot(p,''));}
   assert.deepEqual(p.problems,[]);assert.equal(error,'');checks.push(`${width}: Settings click -> actual Host background -> actual Elevator input visible`);
  } finally {await p.dispose();lines.close();const closed=new Promise(r=>child.once('close',r));child.stdin.end();const timer=setTimeout(()=>child.kill('SIGKILL'),3000);await closed;clearTimeout(timer);}
 }
}finally{await B.close();await fake.stop();await web.stop();}
mkdirSync('reports/qa/p530',{recursive:true});writeFileSync('reports/qa/p530/browser.json',JSON.stringify({ok:true,checks,scope:'Synthetic Noise transport and paired fixture, actual Host and Elevator. No physical macOS sandbox claim.'},null,2)+'\n');
console.log('P530 browser PASS',checks);
