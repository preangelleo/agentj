// P263 actual phone: five state colours, chat still / page dance, settings, hold, reduced motion, custom / legacy.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate as ev,shoot,sleep,rect,tap,key} from './browser.mjs';
import {hostMessages} from './p169.mjs';
const rec=parityRecorder('web/test/p263.mjs');
const out='reports/qa/p263/screens/';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
let checks=0;const check=(ok,label)=>{assert.ok(ok,label);checks++;};
// P360 Wallet stack: only the 44 px title strip of a covered card is tappable; its centre hits a later card.
const click=async(p,s)=>{const r=await rect(p,s);const card=s.startsWith('.fp-menu-card');const y=card?r.top+Math.min(22,r.h/2):r.y;if(card)assert.ok(await ev(p,`!!document.elementFromPoint(${r.x},${y})?.closest(${JSON.stringify(s)})`),'the visible Wallet title strip receives the native tap');await tap(p,r.x,y);};
const states=['idle','working','waiting','question','offline'];
const M=hostMessages();
const group={...M.custom,logo:{disc:true,states:Object.fromEntries(states.map(s=>[s,readFileSync(`agentjarvis/brand/img/robot-${s}-128.png`).toString('base64')]))}};
try{
 for(const width of [393,1440])for(const scheme of ['light','dark']){
  fake.reset();const p=await newPage(B,width,852,scheme,{touch:true,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await ev(p,`document.getElementById('phone-env-dismiss')?.click()`);
   for(const factory of ['robots','dots']){
    await fake.send(M[factory]);
    for(const state of states){
     await fake.send({t:'status',s:state==='question'?'waiting':state==='offline'?'unknown':state,kind:state==='question'?'question':undefined,agent:'claude',name:'Agent J'});
     await waitFor(p,`document.getElementById('orb').dataset.v===${JSON.stringify(state)}`);
     await waitFor(p,`document.getElementById('orb').complete && document.getElementById('orb').naturalWidth===256`);
     check(await ev(p,`!document.getElementById('orb').classList.contains('father-dance')`),'chat still '+state);
     check(await ev(p,`getComputedStyle(document.getElementById('orb')).transform==='none'`),'chat pixels never fractionally scale '+state);
     const shot=async(tag)=>writeFileSync(out+`${width}-${scheme}-${factory}-${state}-${tag}.png`,await shoot(p,''));
     if(factory==='robots')await shot('chat');
     await click(p,'#orb');await waitFor(p,`document.getElementById('orb').classList.contains('father-dance')`);
     check(await ev(p,`getComputedStyle(document.getElementById('orb')).animationName==='father-dance' && getComputedStyle(document.getElementById('orb')).animationDuration==='2.4s'`),'dance 12 steps '+state);
     check(await ev(p,`getComputedStyle(document.getElementById('orb')).backgroundImage.includes('dance-${state}.png')`),'dance state '+state);
     if(factory==='robots')await shot('menu');
     const r=await rect(p,'#orb');await p.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:r.x,y:r.y}]});await sleep(800);await p.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
     await waitFor(p,`document.getElementById('five-menu').hidden && !document.getElementById('orb').classList.contains('father-dance')`);check(true,'hold chat');
    }
   }
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   for(const page of ['workflows','agentj','skills','memory']){
    await click(p,'#orb');await click(p,`.fp-menu-card[data-page="${page}"]`);await waitFor(p,`document.body.dataset.fivePage===${JSON.stringify(page)}`);
    check(await ev(p,`document.getElementById('orb').classList.contains('father-dance')`),'page '+page);
    writeFileSync(out+`${width}-${scheme}-${page}.png`,await shoot(p,''));
    await click(p,'#orb');await click(p,'.fp-menu-card[data-page="chat"]');
   }
   await click(p,'#setBtn');await waitFor(p,`document.getElementById('orb').classList.contains('father-dance')`);check(true,'settings dance');await key(p,'Escape');
   await fake.send(group);await waitFor(p,`document.getElementById('orb').src.startsWith('blob:')`);
   for(const state of states){await fake.send({t:'status',s:state==='question'?'waiting':state==='offline'?'unknown':state,kind:state==='question'?'question':undefined});await waitFor(p,`document.getElementById('orb').dataset.v===${JSON.stringify(state)} && document.getElementById('orb').src.startsWith('blob:') && document.getElementById('orb').complete`);check(true,'custom '+state);}
   const urls=await ev(p,`import('./js/theme.js').then(m=>${JSON.stringify(states)}.map(s=>m.themeLogo(s)))`);
   check(new Set(urls).size===5 && urls.every(u=>u.startsWith('blob:')),'five distinct preloaded URLs');
   for(const malformed of [{...group.logo,states:{idle:group.logo.states.idle}},{...group.logo,states:{...group.logo.states,working:'bad'}},{...group.logo,states:{...group.logo.states,working:readFileSync('agentjarvis/brand/img/logo-mark.png').toString('base64')}}]){
    await fake.send({...group,id:'invalid-logo',logo:malformed});await waitFor(p,`document.documentElement.dataset.look==='invalid-logo' && !document.getElementById('orb').src.startsWith('blob:')`);check(true,'invalid group falls back');
   }
   await fake.send(M.custom);await waitFor(p,`document.documentElement.dataset.look==='night-owl' && document.getElementById('orb').src.startsWith('blob:')`);
   const singles=await ev(p,`import('./js/theme.js').then(m=>${JSON.stringify(states)}.map(s=>m.themeLogo(s)))`);check(new Set(singles).size===1,'legacy single across five states');
   await fake.send(M.robots);await fake.send({t:'status',s:'idle'});
   await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:scheme},{name:'prefers-reduced-motion',value:'reduce'}]});
   await click(p,'#orb');await waitFor(p,`getComputedStyle(document.getElementById('orb')).animationName==='father-hint'`);
   check(await ev(p,`getComputedStyle(document.getElementById('orb')).backgroundImage==='none' && getComputedStyle(document.getElementById('orb')).objectPosition==='50% 50%'`),'reduced still outline');
   writeFileSync(out+`${width}-${scheme}-reduced.png`,await shoot(p,''));
   check(await ev(p,`new Promise(resolve=>{const orb=document.getElementById('orb');let writes=0;const obs=new MutationObserver(r=>writes+=r.length);obs.observe(orb,{attributes:true,attributeFilter:['style']});const node=document.createElement('div');node.hidden=true;document.body.append(node);setTimeout(()=>{node.remove();obs.disconnect();resolve(writes===0)},150)})`),'unrelated reply/media content never rewrites header style');
   check(!p.problems.length,'console '+p.problems.join(';'));
  }finally{await p.dispose();}
 }
 writeFileSync('reports/qa/p263/browser.json',JSON.stringify({checks,ok:true},null,2));console.log('P263 PASS',checks);rec.record('p263-logo-regression',true);
}finally{await B.close();await fake.stop();await web.stop();rec.write();}
