// #514b: host link ready, Claude session absent; repair stays on signed cmd_run.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {generateKeyPairSync,randomBytes} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot} from './browser.mjs';
const out='reports/qa/avon-514b-web';mkdirSync(out,{recursive:true});
const before=!!process.env.P514B_BEFORE;
const web=await startWebServer(before?{publicDir:process.env.P514B_BASE_DIR}:{}),fake=await startFakeHost(),browser=await launch();
try {
 const combos=process.env.P514B_QUICK?[['zh','dark',390]]:['zh','en'].flatMap(l=>['dark','light'].flatMap(t=>[390,1440].map(w=>[l,t,w])));
 for(const [lang,theme,width] of combos){
  fake.reset();fake.setUp(true);fake.st.runCmds=['doctor','reconnect'];fake.st.prefs={};
  const calls=[];let success=false;
  const credId=randomBytes(16),fa=credId.toString('base64url');
  const priv=generateKeyPairSync('ec',{namedCurve:'P-256'}).privateKey.export({type:'pkcs8',format:'der'}).toString('base64');
  fake.st.onApp=async(c,m,send)=>{
   if(m.t!=='cmd_run')return false;
   const sigOk=await fake.verifyControl(c,'cmd_run',m,{cmd:m.cmd});calls.push({cmd:m.cmd,sigOk,fa:!!m.fa});
   const res=x=>send({t:'ctl_res',r:m.r,action:'cmd_run',cmd:m.cmd,...x});
   if(!sigOk){await res({ok:false,why:'bad_signature'});return true;}
   if(!success){await res({ok:false,why:'no_passkey'});return true;}
   const ch=Buffer.from('p514b-'+m.n).toString('base64url');
   if(!m.fa){await res({ok:false,why:'passkey_required',fa,fa_ch:ch});return true;}
   const cd=JSON.parse(Buffer.from(m.fa.cd,'base64url').toString());
   assert.equal(cd.challenge,ch);assert.equal(cd.type,'webauthn.get');assert.equal(m.fa.id,fa);
   await res({ok:true,code:0,out:'Session connection repaired.'});return true;
  };
  const pageUrl=web.url.replace('127.0.0.1','localhost');
  const p=await newPage(browser,width,844,theme,{allow:[web.url,pageUrl,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(pageUrl+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`import('./js/settings.js').then(m=>m.applyThemeLocal('${theme}'))`);
   await fake.send({t:'status',s:'idle',agent:'claude'});
   assert.equal(await evaluate(p,"(document.getElementById('session-help')?.hidden ?? true)"),true,'positive: idle hides session repair');
   await fake.send({t:'status',s:'down',agent:'claude'});await waitFor(p,"document.body.dataset.agent==='down'");
   if(before){writeFileSync(`${out}/${lang}-${theme}-${width}-before.png`,await shoot(p));continue;}
   assert.equal(await evaluate(p,"document.getElementById('session-help')?.hidden"),false,'ready host but disconnected Claude has persistent help');
   assert.ok(await evaluate(p,"document.getElementById('session-help').classList.contains('gdlg') && !document.getElementById('chat-view').contains(document.getElementById('session-help'))"),'0.30.6: a centred dialog, not a row under the header');
   assert.equal(await evaluate(p,"document.body.dataset.conn"),'on','transport stays online');
   assert.ok(await evaluate(p,`document.getElementById('session-help').textContent.includes(${JSON.stringify(lang==='zh'?'Claude 会话没接上':'Claude session is disconnected')})`));
   writeFileSync(`${out}/${lang}-${theme}-${width}-session.png`,await shoot(p));
   await evaluate(p,"document.getElementById('session-repair').click()");
   await waitFor(p,"!document.getElementById('rs-cmd-out').hidden");
   assert.deepEqual(calls,[{cmd:'reconnect',sigOk:true,fa:false}],'repair uses existing signed command; no passkey cannot run');
   assert.ok(await evaluate(p,"document.getElementById('rs-cmd-out').textContent.includes('ID')"),'existing biometric setup guidance remains');
   await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
   if(lang==='zh' && theme==='dark' && width===390){
    success=true;
    await p.send('WebAuthn.enable',{enableUI:false});
    const {authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
    await p.send('WebAuthn.addCredential',{authenticatorId,credential:{credentialId:credId.toString('base64'),isResidentCredential:true,rpId:'localhost',privateKey:priv,userHandle:Buffer.from('p514b-fixture').toString('base64'),signCount:0}});
    await evaluate(p,"document.getElementById('session-repair').click()");
    await waitFor(p,"!document.getElementById('confirm').hidden");
    await evaluate(p,"document.getElementById('confirm-no').click()");
    assert.equal(calls.filter(x=>x.fa).length,0,'cancel does not submit passkey/run');
    await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
    await evaluate(p,"document.getElementById('session-repair').click()");
    await waitFor(p,"!document.getElementById('confirm').hidden");
    await evaluate(p,"document.getElementById('confirm-yes').click()");
    await waitFor(p,"document.getElementById('rs-cmd-out').textContent.includes('Session connection repaired.')");
    assert.equal(calls.filter(x=>x.fa&&x.sigOk).length,1,'one authenticated run after explicit WebAuthn');
    assert.ok(calls.every(x=>x.cmd==='reconnect'&&x.sigOk));
    await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
   }
   await fake.send({t:'status',s:'working',agent:'claude'});await waitFor(p,"document.body.dataset.agent==='working'");
   assert.equal(await evaluate(p,"document.getElementById('session-help').hidden"),true,'working hides help');
   await fake.send({t:'status',s:'none',agent:'claude'});await waitFor(p,"document.body.dataset.agent==='none'");
   assert.equal(await evaluate(p,"document.getElementById('session-help').hidden"),false,'explicit none also needs reconnect');
   fake.setUp(false);await waitState(p,'waiting-host');await waitFor(p,"document.body.dataset.conn==='off'");
   assert.equal(await evaluate(p,"document.getElementById('session-help').hidden"),true,'true relay offline uses separate connection guide');
   await waitFor(p,"!document.getElementById('connDlg').hidden && document.getElementById('connDlg').dataset.state==='down'",15000);   // 0.30.6: centred, after 10 s
   assert.equal(p.problems.length,0);
   assert.ok(await evaluate(p,"!document.getElementById('session-help').textContent.includes('agentj doctor')"),'remote customer guidance never requires host terminal');
  }finally{await p.dispose();}
 }
 console.log(`PASS #514b: ${combos.length} viewport/theme/language cases; idle/working controls, down/none help, signed reconnect/no-passkey, true offline`);
}finally{await browser.close();await fake.stop();await web.stop();}
