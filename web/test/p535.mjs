// P535: rendered state colours, live transitions, custom themes and review screenshots.
import assert from 'node:assert/strict';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {parityRecorder} from '../../parity/lib.mjs';
const rec=parityRecorder('web/test/p535.mjs');
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,shoot,sleep} from './browser.mjs';
const phase=process.env.P535_BEFORE?'before':'after',out=pathToFileURL(resolve(process.env.P535_OUT || 'reports/qa/p535',phase)+'/');
mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0}),fake=await startFakeHost(),B=await launch(),evidence=[];
const states={idle:['idle',''],working:['working',''],waiting:['waiting','permission'],question:['waiting','question'],offline:['unknown','']};
try{for(const scheme of ['light','dark']){
 fake.reset();fake.st.prefs={appearance:{language:'zh',theme:scheme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 fake.remotes({limit:10,items:[{id:'ME',me:true},{id:'other'}]});
 const p=await newPage(B,390,844,scheme,{touch:true,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await waitFor(p,`document.querySelector('#setBtn [data-remote-count]').textContent==='2'`);
 await evaluate(p,`(async()=>{window.p535ui=await import('./js/ui.js')})()`);
 for(const [state,[s,kind]] of Object.entries(states)){
 await fake.send({t:'status',s,kind,agent:'claude',name:'Agent J'});
 await waitFor(p,`document.body.dataset.status==='${s}'`);
 // Same public component and call as relay.say's attachment-wait branch.
 await evaluate(p,`p535ui.toast('等附件传完…',60000)`);
 await sleep(350);
 const meta=await evaluate(p,`(()=>{const css=getComputedStyle(document.body),probe=document.createElement('i');probe.style.color='var(--state)';document.body.append(probe);const state=getComputedStyle(probe).color;probe.remove();return {state,badge:getComputedStyle(document.querySelector('#setBtn [data-remote-count]')).color,icon:getComputedStyle(document.querySelector('#toast .nt-ico')).stroke,ring:getComputedStyle(document.querySelector('#toast')).boxShadow,border:getComputedStyle(document.querySelector('#toast')).borderTopWidth,background:getComputedStyle(document.querySelector('#toast')).backgroundColor,status:document.body.dataset.status,kind:document.body.dataset.kind}})()`);
 writeFileSync(new URL(`${scheme}-${state}.png`,out),await shoot(p,'unused'));
 evidence.push({scheme,stateName:state,...meta});
 // P550 3c (Jarvis 23:4x 「按门禁改」): away from the approved idle green the digit is Noir, ≥ 4.5:1 on its Lotion circle
 if(phase==='after'){assert.equal(meta.badge,state==='idle'?meta.state:'rgb(44, 43, 38)');assert.equal(meta.icon,meta.state);assert.equal(meta.ring,'none');assert.equal(meta.border,'0px');assert.equal(meta.background,'rgb(44, 43, 38)');}
 }
 if(phase==='after'){
 // All centred formats, including short circles, long squares and the saved alias, have no ring.
 for(const kind of ['info','progress','confirm','success','warn','error'])for(const shape of ['circle','square','mail-saved']){
 const style=await evaluate(p,`(()=>{p535ui.notify({kind:'${kind}',text:'完成',shape:'${shape==='mail-saved'?'circle':shape}',ms:60000});const n=document.querySelector('#toast');if('${shape}'==='mail-saved'){n.classList.remove('circle');n.classList.add('mail-saved')}const c=getComputedStyle(n);return {ring:c.boxShadow,border:c.borderTopWidth,outline:c.outlineWidth,background:c.backgroundColor,icon:getComputedStyle(n.querySelector('.nt-ico')).stroke}})()`);
 assert.equal(style.ring,'none',kind+'/'+shape);assert.equal(style.border,'0px');assert.equal(style.outline,'0px');assert.equal(style.background,'rgb(44, 43, 38)');
 const semantic={success:'rgb(98, 152, 135)',warn:'rgb(217, 119, 87)',error:'rgb(217, 119, 87)'};
 assert.equal(style.icon,semantic[kind]||evidence.at(-1).state);
 }
 // An already visible notice follows custom colours without being reopened.
 await evaluate(p,`(async()=>{document.body.dataset.status='idle';document.body.dataset.conn='on';(await import('./js/theme.js')).applyTheme({t:'theme',v:1,id:'custom',colors:{state:{idle:'#ffe292',working:'#71bbde',waiting:'#ebc9b7',question:'#cacadb',offline:'#858585'}}})})()`);
 for(const kind of ['info','progress','confirm']){
 const c=await evaluate(p,`(()=>{p535ui.notify({kind:'${kind}',text:'正在发送消息',shape:'rect',ms:60000});return {icon:getComputedStyle(document.querySelector('#toast .nt-ico')).stroke,stripe:getComputedStyle(document.querySelector('#toast')).backgroundImage,badge:getComputedStyle(document.querySelector('#setBtn [data-remote-count]')).color}})()`);
 assert.equal(c.icon,'rgb(255, 226, 146)');assert.equal(c.badge,c.icon);assert.ok(c.stripe.includes(c.icon));
 }
 // Every custom state updates already visible notices and other neutral overlays.
 const custom={idle:'rgb(255, 226, 146)',working:'rgb(113, 187, 222)',waiting:'rgb(235, 201, 183)',question:'rgb(202, 202, 219)',offline:'rgb(133, 133, 133)'};
 for(const [name,[s,kind]] of Object.entries(states)){
 await evaluate(p,`document.body.dataset.status='${s}';document.body.dataset.kind='${kind}'`);
 const colors=await evaluate(p,`(()=>{const probe=document.createElement('div');probe.className='ajmodal dlgcard';const inner=document.createElement('i');inner.style.color='var(--accent)';probe.append(inner);document.body.append(probe);const accent=getComputedStyle(inner).color;probe.remove();return {accent,icon:getComputedStyle(document.querySelector('#toast .nt-ico')).stroke,badge:getComputedStyle(document.querySelector('#setBtn [data-remote-count]')).color,share:getComputedStyle(document.getElementById('share-paste')).borderTopColor}})()`);
 for(const [part,value] of Object.entries(colors))assert.equal(value,part==='badge'&&name!=='idle'?'rgb(44, 43, 38)':custom[name],name+': '+part);
 }
 await evaluate(p,`document.body.dataset.status='idle';delete document.body.dataset.kind`);
 for(const [kind,want] of [['success','rgb(98, 152, 135)'],['error','rgb(217, 119, 87)'],['warn','rgb(217, 119, 87)']]){
 assert.equal(await evaluate(p,`(()=>{p535ui.notify({kind:'${kind}',text:'完成',ms:60000});return getComputedStyle(document.querySelector('#toast .nt-ico')).stroke})()`),want);
 }
 await evaluate(p,`document.getElementById('setBtn').click()`);await waitFor(p,`!document.getElementById('settings').hidden`);
 assert.equal(await evaluate(p,`getComputedStyle(document.querySelector('#set-remotes-open [data-remote-count]')).color`),'rgb(98, 152, 135)');
 // The real upgrade command card opens the existing signed reset path, in both languages.
 for(const language of ['zh','en']){
 await evaluate(p,`(async()=>{p535ui.toastOff();(await import('./js/settings.js')).close();window.AJLang.set('${language}')})()`);
 let signedReset=false;
 fake.st.onApp=async(c,m,send)=>{if(m.t!=='reset')return false;signedReset=await fake.verifyControl(c,'reset',m,{what:'connection'});await send({t:'reset_done',r:m.r,flow:'reset',ok:true,step:1,text:'ok'});return true;};
 await fake.addTurn({k:'cmd',text:'/update'},language==='zh'?'后台连接有问题；点一键修复。':'Host connection failed. Tap Repair.','done',{cmd:'update',kind:'error',ok:false,open:'upgrade_repair'});
 await waitFor(p,`!!document.querySelector('#cmdx [data-open="upgrade_repair"]')`);
 assert.equal(await evaluate(p,`document.querySelector('#cmdx [data-open="upgrade_repair"]').textContent`),language==='zh'?'一键修复':'Repair');
 await evaluate(p,`document.querySelector('#cmdx [data-open="upgrade_repair"]').click()`);
 for(let i=0;i<100&&!signedReset;i++)await sleep(20);
 assert.equal(signedReset,true,'paired signed reset request');
 }

 }
 assert.deepEqual(p.problems,[]);
 }finally{await p.dispose();}
}
writeFileSync(new URL('evidence.json',out),JSON.stringify(evidence,null,2));rec.record('states-and-repair',true);console.log('PASS P535',phase,evidence.length,'state/theme screenshots');
}finally{rec.write();await B.close();await fake.stop();await web.stop();}
