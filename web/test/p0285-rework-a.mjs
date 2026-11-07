// A-track presentation QA: isolated real client + Noise host. No physical biometric claims.
// A_PUBLIC_DIR (or PUBLIC_DIR) selects served product; A_PHASE=before|after|regression;
// A_DEVICE=android|iphone|desktop explicitly selects emulated UA/touch; default retains width-based behavior.
// A_OUT output; A_CAPTURE=0 measures only; A_WIDTHS=390,1440; A_INVENTORY=0 skips static modal inventory.
import {mkdirSync,writeFileSync,readFileSync,readdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
const phase=process.env.A_PHASE||'after',out=resolve(process.env.A_OUT||`reports/qa/0285-rework-a/${phase}`),capture=process.env.A_CAPTURE!=='0';mkdirSync(out,{recursive:true});
const publicDir=process.env.A_PUBLIC_DIR||process.env.PUBLIC_DIR;
const manifest=dir=>{const result={};function walk(d,rel=''){for(const e of readdirSync(d,{withFileTypes:true})){const r=rel+e.name;if(e.isDirectory())walk(d+'/'+e.name,r+'/');else if(e.isFile())result[r]=createHash('sha256').update(readFileSync(d+'/'+e.name)).digest('hex');}}walk(dir);return result;};
const sourceDir=publicDir?resolve(publicDir):fileURLToPath(new URL('../public/',import.meta.url)),productManifest=manifest(sourceDir),scriptSha256=createHash('sha256').update(readFileSync(fileURLToPath(import.meta.url))).digest('hex'),cGateModuleSha256=process.env.A_C_GATE_MODULE?createHash('sha256').update(readFileSync(resolve(process.env.A_C_GATE_MODULE))).digest('hex'):null;
const web=await startWebServer(publicDir?{publicDir:resolve(publicDir)}:{}),fake=await startFakeHost(),b=await launch();
const cMeasure=process.env.A_C_GATE_MODULE?(await import(pathToFileURL(resolve(process.env.A_C_GATE_MODULE)).href)).measurePage:null;
const rows=[],checks=[],failures=[],inventory=new Set();const check=(value,name,row)=>{const c={pass:!!value,name,scene:row.scene,width:row.width,lang:row.lang,theme:row.theme};checks.push(c);if(!c.pass)failures.push(c);};
const ua='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1';
const rectJS=`n=>{if(!n)return null;const r=n.getBoundingClientRect(),s=getComputedStyle(n);return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom,bg:s.backgroundColor,color:s.color,backdrop:s.backdropFilter,display:s.display,visibility:s.visibility};}`;
async function shot(p,base,scene,selector,kind){
 if(process.env.A_SCENES&&!process.env.A_SCENES.split(',').includes(scene))return null;
 await sleep(180);await evaluate(p,`document.fonts.ready`);const data=await evaluate(p,`(()=>{const token=k=>{const n=document.createElement('i');n.style.color='var('+k+')';document.body.append(n);const c=getComputedStyle(n).color;n.remove();return c;},rect=${rectJS},root=document.querySelector(${JSON.stringify(selector)}),card=root?.matches('.gdlg,.ajmodal')?(root.querySelector(':scope>.box,:scope>.sq')||root):root,vis=n=>!!n&&n.getClientRects().length>0&&getComputedStyle(n).visibility!=='hidden'&&!n.closest('[hidden]')&&!(n.closest('details:not([open])')&&!n.closest('summary'));const leafText=[];if(card){const walker=document.createTreeWalker(card,NodeFilter.SHOW_TEXT);while(walker.nextNode()){const n=walker.currentNode,t=n.textContent.trim();if(!t||n.parentElement.closest('button,svg,summary,input,textarea'))continue;const range=document.createRange();range.selectNodeContents(n);if([...range.getClientRects()].some(r=>r.width>0&&r.height>0)&&getComputedStyle(n.parentElement).visibility!=='hidden')leafText.push(t);}}return{leafText,tokens:{noir:token('--c-noir'),lotion:token('--c-lotion'),pg:token('--pg')},root:rect(root),card:rect(card),body:rect(document.body),week:rect(document.getElementById('mWeek')),five:rect(document.getElementById('m5h')),composer:rect(document.querySelector('.composer')),status:document.body.dataset.status,system:root?.dataset.popupSystem,text:card?.innerText,details:[...(card?.querySelectorAll('details')||[])].map(n=>({open:n.open,text:n.innerText})),buttons:[...(card?.querySelectorAll('button')||[])].filter(vis).map(n=>({id:n.id,text:n.innerText,...rect(n)})),visibleText:[...(card?.querySelectorAll('p,h2,h3,summary')||[])].filter(vis).map(n=>({tag:n.tagName,text:n.innerText,...rect(n)})),overflow:document.documentElement.scrollWidth>innerWidth,modalIds:[...document.querySelectorAll('.gdlg,.ajmodal,dialog')].map(n=>n.id||n.className)};})()`);
 const cScene=selector==='#sheet'?'approval':selector==='#pkp'?'faceid':selector==='#confirm'&&kind==='center'?'confirm':selector==='#busyDlg'?'busy':scene;
 const semanticGroups=await evaluate(p,`(()=>{const root=document.querySelector(${JSON.stringify(selector)}),v=n=>n&&n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden'&&!n.closest('[hidden]');const groups=[];if(root.matches('.ajmodal,.gdlg')){if(root.querySelector(':scope>.box'))groups.push([${JSON.stringify(selector)}+'>.box',8]);if(root.querySelector(':scope>.sq'))groups.push([${JSON.stringify(selector)}+'>.sq',8]);}if(root.id==='sheet'&&${JSON.stringify(scene)}.startsWith('same-owner'))groups.push(['#frAsk',8]);for(const n of root.querySelectorAll('.pkp-acts,.fr-askbtns,.ajrow,.gdlg-row,.confirm-row,.elev-acts,.sout-acts,.lqr-acts,.hlp-acts,.lt-acts')){if(!v(n))continue;const cls=[...n.classList].filter(c=>['pkp-acts','fr-askbtns','ajrow','gdlg-row','confirm-row','elev-acts','sout-acts','lqr-acts','hlp-acts','lt-acts'].includes(c))[0];const sel=${JSON.stringify(selector)}+' .'+cls;if(!groups.some(x=>x[0]===sel))groups.push([sel,cls==='pkp-acts'||cls==='fr-askbtns'?12:8]);}return groups;})()`);
 const cGate=cMeasure?await evaluate(p,`(${cMeasure.toString()})(${JSON.stringify({scene:cScene,groups:semanticGroups})})`):null;
 const row={...base,scene,kind,selector,...data,cGate,file:capture?`${base.width}-${base.lang}-${base.theme}-${scene}.png`:null};for(const id of data.modalIds)inventory.add(id);rows.push(row);
 if(capture)writeFileSync(`${out}/${row.file}`,await shoot(p));writeFileSync(`${out}/${base.width}-${base.lang}-${base.theme}-${scene}.json`,JSON.stringify(row,null,2));
 if(phase==='after'){
  if(cGate){for(const c of cGate.checks.filter(c=>c.gate===1||c.gate===4))check(c.pass,'C A-surface gate '+c.gate+': '+c.name,row);}
  check(!row.overflow,'no horizontal viewport overflow',row);check(row.card?.w>0&&row.card?.h>0,'card visible',row);
  if(kind==='growth'){
   check(Math.abs(row.card.x)<=1&&Math.abs(row.card.w-base.width)<=1,'growth card viewport width and no side margins',row);
   check(row.five&&Math.abs(row.card.bottom-row.five.y)<=2,'growth bottom joins five-hour quota',row);
   check(row.card.y>=row.week.bottom-1,'growth does not cover weekly quota',row);
   check(row.card.bg===row.body.bg,'growth uses state background',row);
   check(row.root.backdrop==='none','growth has no backdrop blur',row);
  }else{check(row.visibleText.every(n=>{const a=n.color.match(/[\d.]+/g)?.map(Number)||[];return a.length>=3&&Math.min(...a.slice(0,3))>=(n.color.startsWith('color(')?200/255:200); }),'centered visible prose uses white ink',row);check(row.card.bg===row.tokens.noir,'centered card Noir background',row);check(row.card.color===row.tokens.lotion,'centered card white foreground',row);}
  if(scene.startsWith('same-owner')){check(row.details.length>0&&row.details.every(d=>!d.open),'same-owner supporting details default folded',row);check(/\d{6}/.test(row.text),'same-owner six digits visible',row);check(row.leafText.every(t=>/^\d{6}$/.test(t)),'same-owner default has no supporting chrome text',row);}
 }
 return row;
}
async function resetAsk(p,id){await fake.askDone(id,'deny');await sleep(150);await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);}
try{for(const width of (process.env.A_WIDTHS||'390,1440').split(',').map(Number))for(const lang of (process.env.A_LANGS||'zh,en').split(','))for(const theme of (process.env.A_THEMES||'light,dark').split(',')){
 const device=process.env.A_DEVICE||(width===390?'iphone':'desktop');
 if(!['android','iphone','desktop'].includes(device)||(process.env.A_DEVICE&&(device==='desktop'?width!==1440:width!==390)))throw new Error('device/width mismatch');
 const requestedUA=device==='iphone'?ua:device==='android'?'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.36':undefined;
 const base={width,lang,theme,device};fake.reset();fake.st.prefs={appearance:{language:lang,theme}};
 const p=await newPage(b,width,1000,theme,{ua:requestedUA,touch:device!=='desktop',allow:[web.url,fake.relay+'/']});try{
  base.browserIdentity=await evaluate(p,'({ua:navigator.userAgent,touch:navigator.maxTouchPoints})');
  if(requestedUA&&base.browserIdentity.ua!==requestedUA)throw new Error('requested UA not active');
  if(device!=='desktop'&&base.browserIdentity.touch<1)throw new Error('touch emulation not active');
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await fake.addTurn({k:'phone',name:lang==='zh'?'你':'You',text:lang==='zh'?'请检查这次操作。':'Please review this action.'},lang==='zh'?'已检查，等你确认。':'Reviewed and awaiting confirmation.');
  await evaluate(p,`document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);`);await sleep(700);
  const id='a'.repeat(32);await fake.ask({id,tool:'Bash',summary:'rm -- exports/2026-10/old.csv',ttl:600,cat:['delete'],why:lang==='zh'?'删除后无法撤回':'Cannot be undone',passkey_required:true,fa_s:'ready',fa:'c3ludGhldGlj',fa_ch:'c3ludGhldGljLWNoYWxsZW5nZQ',reason:lang==='zh'?'删除旧导出文件':'Delete an old export',pk_kind:'sandbox'});
  await waitFor(p,`document.body.dataset.sheet==='1'`);await shot(p,base,'approval','#sheet','growth');await resetAsk(p,id);
  // Modern ceremony display: fixture supplies display metadata only; no approval is clicked or authority changed.
  const so={id:'b'.repeat(32),tool:'peer_question',summary:'Same owner comparison',state:'open',ttl:600,pq:{pairing:true,same_owner:true,friend:'fixture-peer',name:lang==='zh'?'另一台主机':'Other host',code:'482913',challenge:'0'.repeat(32)}};
  await fake.ask(so);await waitFor(p,`!!document.querySelector('.fr-owner-code')`);
  for(const recovery of [true,false]){
   await evaluate(p,`import('./js/friends.js').then(m=>m.renderAsk(${JSON.stringify({...so,pq:{...so.pq,pairing_scope:'[]'},pairing_code:'482913',pairing_scope_ok:false,pairing_bind_needed:true,pairing_roster:[{here:true,remotes:['Work phone'],pendingRemotes:[]},{here:false,remotes:['Other phone'],pendingRemotes:[]}]})},{open:true,sign:true,repaint:()=>{},dismiss:()=>{}}))`);
   if(!recovery)await evaluate(p,`import('./js/friends.js').then(m=>m.renderAsk(${JSON.stringify({...so,pairing_roster:[{here:true,remotes:['Work phone'],pendingRemotes:[]},{here:false,remotes:['Other phone'],pendingRemotes:[]}]})},{open:true,sign:true,repaint:()=>{},dismiss:()=>{}}))`);
   await shot(p,base,recovery?'same-owner-recovery':'same-owner-comparison','#sheet','growth');
  }await resetAsk(p,so.id);
  const pkid=Buffer.alloc(16,17).toString('base64url'),ch=Buffer.alloc(32,19).toString('base64url');
  await evaluate(p,`import('./js/pkpeer.js').then(m=>{m.configure({canAssert:()=>true,approve:()=>Promise.resolve({synthetic:true}),send:async()=>{}});m.onAsk({id:${JSON.stringify(pkid)},code:'123456',fa_ch:${JSON.stringify(ch)},fa:'synthetic-bound-restored-key',name:${JSON.stringify(lang==='zh'?'新遥控器':'New remote')},at:1762534800,exp:Math.floor(Date.now()/1000)+300});})`);
  await waitFor(p,`!!document.getElementById('pkp-approve')`);await shot(p,base,'faceid-approve','#pkp','growth');
  await evaluate(p,`import('./js/pkpeer.js').then(async m=>{m._reset();m.closeResult();m.configure({supported:async()=>true,ask:async()=>({t:'pk_peer_res',ok:true,mode:'peer',id:${JSON.stringify(pkid)},code:'123456',exp:Math.floor(Date.now()/1000)+300,online:1}),view:null});await m.startPeerSetup();})`);
  await waitFor(p,`document.getElementById('pkp')?.dataset.mode==='wait'`);await shot(p,base,'faceid-wait','#pkp','growth');
  await evaluate(p,`import('./js/pkpeer.js').then(async m=>{m.configure({regOpts:async()=>({syntheticFixture:true})});m.onDone({id:${JSON.stringify(pkid)},ok:true,by:'Other remote'});await m.onOffer({n:${JSON.stringify(ch)},peer:${JSON.stringify(pkid)}});})`);
  await waitFor(p,`document.getElementById('pkp')?.dataset.mode==='approved'`);await shot(p,base,'faceid-save','#pkp','growth');await evaluate(p,`import('./js/pkpeer.js').then(m=>{m._reset();m.closeResult()})`);
  await evaluate(p,`import('./js/ui.js').then(m=>{void m.confirmSheet(${JSON.stringify(lang==='zh'?'确认操作':'Confirm action')},${JSON.stringify(lang==='zh'?'检查后再继续。':'Review before continuing.')},${JSON.stringify(lang==='zh'?'确认':'Confirm')});})`);await waitFor(p,`!document.getElementById('confirm').hidden`);await shot(p,base,'confirm','#confirm','center');await evaluate(p,`document.getElementById('confirm-no').click()`);
  await evaluate(p,`import('./js/pkassist.js').then(m=>{void m.pkSheet('local',${JSON.stringify(lang==='zh'?'用本机验证':'Verify on this device')},${JSON.stringify(lang==='zh'?'用本机通行密钥确认。':'Confirm with this device passkey.')},${JSON.stringify(lang==='zh'?'确认':'Confirm')});})`);await waitFor(p,`!document.getElementById('confirm').hidden`);await shot(p,base,'faceid-confirm','#confirm','growth');await evaluate(p,`document.getElementById('confirm-no').click()`);
  // Additional existing modules opened with synthetic local host payloads; no write action is clicked.
  if(process.env.A_DYNAMIC!=='0'){
   const sid='c'.repeat(32),nonce='d'.repeat(32),img='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jco0AAAAASUVORK5CYII=';
   const extra=[
    ['elevate','sudo-card','#elev','growth',`m.add({id:'${sid}',n:'${nonce}',kind:'sudo',epk:'${Buffer.alloc(32,7).toString('base64url')}',cmd:'ls -ld /tmp',why:'${lang==='zh'?'检查目录':'Inspect directory'}',effect:'${lang==='zh'?'只读检查':'Read-only inspection'}',ttl:600,passkey_required:true,fa:'c3ludGhldGlj',fa_s:'ready',helper_uv:1})`,`m.clear()`],
    ['secretout','pickup-card','#sout','growth',`m.add({id:'${sid}',n:'${nonce}',kind:'text',name:'DEMO_LABEL',purpose:'${lang==='zh'?'领取测试内容':'Collect test content'}',size:8,ttl:600,fa:'c3ludGhldGlj',fa_s:'ready',pickup_uv_allowed:true})`,`m.clear()`],
    ['logincard','login-qr','#lqr','growth',`m.add({id:'${sid}',n:'${nonce}',site:'fixture-site',title:'${lang==='zh'?'扫码登录':'Scan to sign in'}',img:'${img}',ttl:120})`,`m.clear()`],
    ['helpcard','help-card','#hlp','center',`m.add({id:'${sid}',n:'${nonce}',kind:'screen',mime:'image/png',img:'${img}',text:'${lang==='zh'?'请完成屏幕确认':'Please confirm the screen'}',reply:'done',by:'agent',from:'fixture',ttl:600})`,`m.clear()`],
    ['loginsheet','login-sheet','#lgs','center',`m.open('opencode')`,`m.close()`],
    ['longtask','goal-card','#lt','center',`m.add({id:'${sid}',ttl:600,card:{card_id:'${sid}',nonce:'fixture',title:'${lang==='zh'?'整理资料':'Organize notes'}',goal:'${lang==='zh'?'整理资料':'Organize notes'}',required:[],optional:[]}})`,`m.clear()`],
    ['longtask','agent-caps','#lt-caps','center',`m.showCaps({items:[{id:'fixture',title:'${lang==='zh'?'整理资料':'Organize notes'}',status:'ready'}]})`,`m.clear()`],
    ['mail-servers','mail-servers','#mail-servers','center',`void m.mailServers({imap:{host:'imap.example.test'},smtp:{host:'smtp.example.test'}})`,`document.querySelector('#mail-servers button[type=button]').click()`]
   ];
   for(const [module,scene,selector,kind,open,close] of extra){
    if(process.env.A_SCENES&&!process.env.A_SCENES.split(',').includes(scene))continue;
    await evaluate(p,`import('./js/${module}.js').then(m=>{${open}})`);await waitFor(p,`!!document.querySelector('${selector}')&&!document.querySelector('${selector}').hidden`);
    await shot(p,base,scene,selector,kind);
    await evaluate(p,`import('./js/${module}.js').then(m=>{${close}})`);await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);
   }
  }
  if(process.env.A_INVENTORY!=='0'){
   const ids=await evaluate(p,`[...document.querySelectorAll('.gdlg')].filter(n=>n.id).map(n=>n.id)`);
   for(const id of ids){await evaluate(p,`document.getElementById(${JSON.stringify(id)}).hidden=false`);await shot(p,base,'inventory-'+id,'#'+id,'center');await evaluate(p,`document.getElementById(${JSON.stringify(id)}).hidden=true`);}
  }
  console.log('CAPTURE',phase,width,lang,theme);
 }finally{await p.dispose();}
}}finally{const endManifest=manifest(sourceDir),productStable=JSON.stringify(productManifest)===JSON.stringify(endManifest);if(!productStable)failures.push({pass:false,name:'product bytes changed during measurement'});writeFileSync(`${out}/results.json`,JSON.stringify({productStable,scriptSha256,cGateModuleSha256,productManifest,endManifest,phase,publicDir:publicDir||'worktree public',fixture:'Isolated Chromium + Noise host; same-owner render metadata and pkpeer hooks synthetic; no approval click, enrollment or production state',capture,inventory:[...inventory],rows,checks,failures},null,2));await b.close();await fake.stop();await web.stop();}
console.log(JSON.stringify({phase,rows:rows.length,checks:checks.length,failures:failures.length}));if(failures.length)process.exitCode=1;
