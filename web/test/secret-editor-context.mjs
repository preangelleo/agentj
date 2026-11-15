// Secret metadata stays attached to the originating card while the IME viewport shrinks.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitFor,waitState,sleep,shoot} from './browser.mjs';
import {b64u} from '../../protocol/wire.js';
const out=process.env.OLARES_RUN_OUT||'reports/qa/secret-editor-context';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0}),fake=await startFakeHost(),B=await launch();let checks=0;
const ok=(v,msg)=>{assert.ok(v,msg);checks++;};
async function card(name,purpose){const kp=await webcrypto.subtle.generateKey({name:'X25519'},true,['deriveBits']);return {t:'elev',kind:'secret',id:webcrypto.randomUUID().replaceAll('-',''),n:webcrypto.randomUUID().replaceAll('-',''),epk:b64u(new Uint8Array(await webcrypto.subtle.exportKey('raw',kp.publicKey))),ttl:120,tries:3,name,purpose,dest:'env:/tmp/agentj-context-fixture#TEST_KEY',verify:'',button_only:true,passkey_required:false};}
try{for(const theme of ['light','dark'])for(const width of [390,1440])for(const lang of ['zh','en']){
 fake.reset();fake.st.prefs={appearance:{language:lang,theme}};
 const p=await newPage(B,width,844,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.documentElement.dataset.ios='1'`);
 const first=await card('FIRST_KEY','First card purpose'),second=await card('SECOND_KEY_'+ 'LONG_'.repeat(21),'Second card purpose '+ 'long context '.repeat(18));
 await fake.send(first);await waitFor(p,`document.getElementById('elev-input')&&!document.getElementById('elev').hidden`);
 await fake.send(second);await sleep(100);
 for(const c of [first,second]){
 await ev(p,`document.getElementById('elev-input').click()`);await waitFor(p,`!document.getElementById('fsEd').hidden`);
 const title=(lang==='zh'?'粘贴 ':'Paste ')+c.name;
 ok(await ev(p,`document.querySelector('#fsEd .fs-secret-title')?.textContent===${JSON.stringify(title)}`),'visible title identifies originating key in UI language');
 ok(await ev(p,`document.querySelector('#fsEd .fs-secret-purpose')?.textContent===${JSON.stringify(c.purpose)}`),'purpose matches exact card text');
 await ev(p,`Object.defineProperty(window,'visualViewport',{configurable:true,value:Object.assign(new EventTarget(),{height:385,width:${width},offsetTop:27,offsetLeft:0,scale:1})});dispatchEvent(new Event('resize'))`);await sleep(480);
 const geo=await ev(p,`(()=>{const root=document.getElementById('fsEd'),head=root.querySelector('.fs-secret-context'),text=document.getElementById('fsText'),send=document.getElementById('fsSend'),r=head.getBoundingClientRect(),a=text.getBoundingClientRect(),b=send.getBoundingClientRect(),names=[...head.children].map(n=>{const s=getComputedStyle(n);return {whiteSpace:s.whiteSpace,overflow:s.overflow,textOverflow:s.textOverflow,width:n.clientWidth,scroll:n.scrollWidth}});return {top:r.top,bottom:r.bottom,textTop:a.top,sendBottom:b.bottom,rootWidth:root.clientWidth,scrollWidth:root.scrollWidth,names,hit:document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)?.closest('.fs-secret-context')===head}})()`);
 writeFileSync(`${out}/${theme}-${width}-${lang}-geometry.json`,JSON.stringify(geo));writeFileSync(`${out}/${theme}-${width}-${lang}-probe.png`,await shoot(p,''));
 ok(geo.top>=27&&geo.bottom<=geo.textTop&&geo.bottom<412&&geo.sendBottom<=412&&geo.hit,'fixed header and save stay above keyboard');
 ok(geo.rootWidth===geo.scrollWidth&&geo.names.every(n=>n.whiteSpace==='nowrap'&&n.overflow==='hidden'&&n.textOverflow==='ellipsis'),'metadata is one-line ellipsis without horizontal page overflow');
 if(c===second&&width===390)ok(geo.names.every(n=>n.scroll>n.width),'long key and purpose actually truncate at phone width');
 writeFileSync(`${out}/${theme}-${width}-${lang}-${c===first?'first':'second'}-keyboard.png`,await shoot(p,''));
 await ev(p,`import('./js/fsedit.js').then(m=>m.close())`);
 if(c===first){await fake.send({t:'elev_done',id:first.id,result:'saved'});await waitFor(p,`document.getElementById('elev-title').textContent.includes('SECOND_KEY_')`);}
 }
 const mail=await card('AGENTJ_MAIL_PASSWORD','Mail provider application password\nFixture mailbox\nUse the app password from the provider');
 await fake.send({t:'elev_done',id:second.id,result:'saved'});await fake.send(mail);await waitFor(p,`document.getElementById('elev-title').textContent==='Mail provider application password'`);
 await ev(p,`document.getElementById('elev-input').click()`);await waitFor(p,`!document.getElementById('fsEd').hidden`);
 ok(await ev(p,`document.querySelector('#fsEd .fs-secret-title').textContent===${JSON.stringify((lang==='zh'?'粘贴 ':'Paste ')+mail.name)}&&document.querySelector('#fsEd .fs-secret-purpose').textContent===document.getElementById('elev-title').textContent`),'mail context preserves variable name and rendered card purpose title');
 await ev(p,`import('./js/fsedit.js').then(m=>m.close())`);
 await ev(p,`import('./js/fsedit.js').then(m=>m.open())`);
 ok(await ev(p,`!document.querySelector('#fsEd .fs-secret-context')&&!document.getElementById('fsEd').hasAttribute('data-secret')`),'ordinary composer has no leftover secret metadata');
 }finally{await p.dispose();}
}writeFileSync(out+'/result.json',JSON.stringify({checks,kind:'injected visualViewport, not real IME'},null,2));console.log('secret-editor-context '+checks+' PASS');
}finally{await B.close();await fake.stop();await web.stop();}
