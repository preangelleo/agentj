import{phaseAuxiliaryPage}from'./owned-browser-session.mjs';
import {createHash} from 'node:crypto';
import {readFileSync,writeFileSync,mkdirSync,readdirSync,lstatSync} from 'node:fs';
import {resolve} from 'node:path';
import {startCriticalDashboard,prepareDashboardPage} from '../p0033-dashboard-adapter.mjs';
export const criticalConfigs=()=>[[390,844],[834,1112],[1440,900]].flatMap(([width,height])=>['light','dark'].flatMap(theme=>['zh','en'].map(lang=>({width,height,theme,lang}))));
export function criticalSceneSelection(file) {
 const manifest=JSON.parse(readFileSync(new URL('./scene-manifest.json',import.meta.url),'utf8')).publishCritical;
 if(!file)return {scenes:[...manifest.scenes],sha256:null};
 const bytes=readFileSync(file),input=JSON.parse(bytes);
 if(input.schemaVersion!==1||!Array.isArray(input.scenes)||!input.scenes.length||new Set(input.scenes).size!==input.scenes.length||input.scenes.some(name=>typeof name!=='string'||!manifest.scenes.includes(name)))throw Error('Invalid explicit critical scene subset');
 return {scenes:[...input.scenes],sha256:createHash('sha256').update(bytes).digest('hex'),...(input.legacyOwnerSources?{legacyOwnerSources:input.legacyOwnerSources}:{})};
}
export function criticalOwnershipScope(selection, sourceCombined) {
 if(!selection?.legacyOwnerSources)return '577-component-ownership';
 const allowed=['562d9ec40730b5d44f4739da76c81f2b5afedbeea6b2164eaabf4495a981a037','707727abf3f16a1b84adee7da8113154d795871397477deddaea8d9fbc6c8061'];
 const sources=selection.legacyOwnerSources;
 if(!selection.sha256||!Array.isArray(sources)||sources.length!==2||new Set(sources).size!==2||sources.some(x=>!allowed.includes(x))||!sources.includes(sourceCombined))throw Error('Unreviewed formal pre577 ownership source');
 return 'formal-pre577-source';
}
export function comparisonFixture(file){
 const bytes=readFileSync(file),input=JSON.parse(bytes);
 if(Object.keys(input).sort().join(',')!=='displayVersion,now,policy,schemaVersion'||input.schemaVersion!==1||input.policy!=='fixed-interface-data-only'||typeof input.displayVersion!=='string'||!/^\d+\.\d+\.\d+(?:[-+.][\w.-]+)?$/.test(input.displayVersion)||input.now!==1793980000000)throw Error('Invalid fixed interface comparison fixture');
 return {...input,sha256:createHash('sha256').update(bytes).digest('hex')};
}
export function criticalReply(m){
 if(m.t==='owner_hosts_get')return ['owner_hosts_state',{scope:'workspace',rev:'1',valid:true,rows:[{alias:'sample',hostname:'sample.example.invalid',user:'qa',port:22,public_key:'ssh-ed25519 SYNTHETIC-PUBLIC-SAMPLE',fingerprint:'SHA256:SyntheticPublicSample',peer:'sample',expires:1796572000,status:'active',ssh:'ssh sample',scp:'scp sample.txt sample:/'}],candidates:[]}];
 if(m.t==='jcard_get'&&m.id==='automation'){
  const task={id:'agentj-sample',name:'Synthetic task',source:'agentj',scope:'workspace',owner:'QA',schedule:'0 9 * * *',mode:'research',execution:'research',last_result:'done',control:{supported:true,target:{kind:'agentj-task',id:'sample',tsha:'a'.repeat(64),enabled:true}}};
  const native={id:'native-sample',name:'Synthetic native timer',source:'systemd',scope:'user',owner:'QA',schedule:'0 12 * * *',last_result:'done',control:{supported:true,target:{platform:'linux',source:'systemd',scope:'user',id:'sample.timer',snapshot:'b'.repeat(64),op:'disable'}}};
  const page=m.automation_page||0;if(page>1)return ['jcard_get',{ok:false,why:'bad_automation_page'}];
  return ['jcard_get',{ok:true,id:'automation',template:'automation',items:page===0?[task,native]:[{...task,id:'tail-sample',name:'Synthetic last-page task',control:{supported:true,target:{...task.control.target,id:'tail-sample',enabled:false}}}],pagination:{page,total:3,page_size:2,pages:2,scan_limited:true},actions:[],links:[],anchors:[]}];
 }
 return null;
}
export function criticalFixtureFingerprint(root){
 const files=['web/test/p0033-dashboard-adapter.mjs','dashboard/worker.ts'];
 for(const dir of ['dashboard/src','dashboard/test','dashboard/migrations','worker/src'])for(const f of readdirSync(resolve(root,dir),{recursive:true})){const path=resolve(root,dir,f),st=lstatSync(path);if(st.isSymbolicLink())throw Error('Critical fixture symlink');if(st.isFile())files.push(dir+'/'+f);}
 return Object.fromEntries(files.sort().map(f=>['critical/'+f,createHash('sha256').update(readFileSync(resolve(root,f))).digest('hex')]));
}
export async function runCriticalFlow({p,ctx,cfg,fake,capture,captureStable,cssSession,rows,out,tag,sourceCombined,input,versionRoute,dashboardSource,mediaItems,sheetURLs,rawAboutPage,scenes}){
 const selected=new Set(scenes||criticalSceneSelection().scenes);
 const cap=async(name,selector,meta)=>{if(selected.has(name))return capture(name,selector,meta);};
 await fake.addTurn({k:'phone',dev:'synthetic-owner',name:'QA',text:cfg.lang==='zh'?'检查合成引用':'Inspect synthetic quotes',quote:{id:1,who:'QA',ts:1793980000000,text:'First synthetic source quote.',more:[{id:2,who:'QA',ts:1793980000000,text:'Second synthetic source quote.'}]}},'A fixed synthetic reply.\n\n[Continue](reply:Continue) [Cancel](reply:Cancel)\n\n```js\nconst value = 42;\n```');
 await p.waitForFunction(()=>document.querySelector('.rbtn'));
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 await p.evaluate(()=>{for(const sel of ['.top','#input','.tools']){const e=document.querySelector(sel),r=e?.getBoundingClientRect();if(!r||r.width<=0||r.height<=0||r.bottom<=0||r.top>=innerHeight)throw Error('Missing visible critical header/composer '+sel);}});
 await cap('chat-idle','#chat-view');
 await cap('source-quotes-folded','#omQuoteFold');await p.locator('#omQuoteFold').click();await cap('source-quotes-expanded','#omQuoteMore');await p.locator('#omQuoteFold').click();
 await p.locator('#words .codeblock').scrollIntoViewIfNeeded();await cap('publish-chat-code','#words .codeblock');
 await fake.send({t:'status',s:'working',agent:'claude',name:'Agent J'});await cap('chat-working','#chat-view');
 for(const state of ['hover','focus-visible','active']){
  const r=p.locator('.rbtn').first();await r.scrollIntoViewIfNeeded();await p.evaluate(()=>document.activeElement?.blur());await p.mouse.move(0,0);let nodeId;
  try{
   if(state==='focus-visible'){await p.keyboard.press('Tab');await r.focus();if(!await r.evaluate(e=>e.matches(':focus-visible')))throw Error('Keyboard focus-visible did not activate');}
   else await r.hover();
   if(state==='active'){const {root}=await cssSession.send('DOM.getDocument');({nodeId}=await cssSession.send('DOM.querySelector',{nodeId:root.nodeId,selector:'.rbtn'}));await cssSession.send('CSS.forcePseudoState',{nodeId,forcedPseudoClasses:['active']});}
   await p.evaluate(state=>{if(state==='focus-visible')document.documentElement.dataset.qaPreserveFocus='1';else delete document.documentElement.dataset.qaPreserveFocus;},state);const forcedRules=nodeId?(await cssSession.send('CSS.getMatchedStylesForNode',{nodeId})).matchedCSSRules.filter(x=>x.rule.origin==='regular').flatMap(x=>x.matchingSelectors.map(i=>x.rule.selectorList.selectors[i].text).filter(t=>t.includes(':active')).map(selector=>({selector,file:sheetURLs.get(x.rule.styleSheetId)||'inline',properties:x.rule.style.cssProperties.filter(p=>!p.disabled).map(p=>p.name),conditions:(x.rule.media||[]).map(m=>m.text),line:(x.rule.style.range?.startLine||0)+1}))):[];await cap('reply-'+state,'.rbtn[data-say="Continue"]',{forcedSelector:'.rbtn',forcedRules,state,activeMechanism:nodeId?'CDP visual pseudo-state, no gesture/longpress':null});
  }finally{await p.evaluate(()=>delete document.documentElement.dataset.qaPreserveFocus);if(nodeId)await cssSession.send('CSS.forcePseudoState',{nodeId,forcedPseudoClasses:[]});}
 }
 await p.locator('#replyBtn').click();await fake.addTurn({k:'phone',dev:'synthetic-owner',text:'Second synthetic message'},'Second fixed reply');await p.locator('#replyBtn').click();await cap('composer-multi-quotes','#qbar');await p.locator('#qbX').click();
 await fake.ask({id:'a'.repeat(32),tool:'Bash',summary:'Read synthetic sample.txt',ttl:120,cat:['read']});await cap('approval','#sheet');await fake.askDone('a'.repeat(32),'gone');await p.waitForFunction(async()=>(await import('./js/snap.js')).S.lastAsk?.state==='ended');await p.locator('#sheetClose').click();
 await fake.send({t:'status',s:'waiting',kind:'question',agent:'claude',name:'Agent J'});await fake.send({t:'question',id:'b'.repeat(32),ttl:180,qs:[{q:'Which sample?',h:'Sample',m:true,o:[{l:'A',d:'First'},{l:'B',d:'Second'}]}]});await cap('question','#sheet');await fake.send({t:'question_done',id:'b'.repeat(32),result:'gone'});await p.waitForFunction(async()=>(await import('./js/snap.js')).S.lastQ?.state==='ended');await p.locator('#sheetClose').click();
 await p.setInputFiles('#fDoc',{name:'sample.txt',mimeType:'text/plain',buffer:Buffer.from('Synthetic attachment')});await cap('file-tray','#tray');await p.evaluate(()=>[...document.querySelectorAll('#tray .chip .x')].forEach(b=>b.click()));
 const turn=await fake.addTurn({k:'phone',dev:'synthetic-owner',text:'Show sample media',att:[{kind:'file',name:'sample.txt'}]},'![Sample](out/sample.png)\n\n[Sample text](out/sample.txt)');await fake.updateTurn(turn.id,{media:mediaItems.slice(0,2)});await p.waitForFunction(()=>document.querySelector('#words .mslot .mcard[data-state=ready] img'));await cap('media-message','#words');
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 await fake.send({t:'preferences',value:fake.st.prefs,host:{version:input.displayVersion,owner_hosts:1,platform:'macos',settable:[],run_cmds:['doctor','status']}});
 await p.evaluate(()=>import('./js/settings.js').then(m=>m.open()));await cap('settings-main','#settings');await p.evaluate(()=>document.getElementById('set-main').scrollTop=100000);await cap('settings-main-end','#settings');
 for(const id of ['agent','rescue','tools','look','read','notify','channels','safety','app','help']){await p.locator('#set-s-'+id).scrollIntoViewIfNeeded();await cap('settings-section-set-s-'+id,'#set-s-'+id);}
 if(selected.has('publish-owner-hosts-list')||selected.has('publish-owner-hosts-edit')){
 await p.locator('#owner-hosts').scrollIntoViewIfNeeded();await p.waitForFunction(()=>document.querySelector('#owner-hosts-list button'));await cap('publish-owner-hosts-list','#owner-hosts');await p.locator('#owner-hosts-list .owner-hosts__item > button').first().click();await p.locator('#owner-hosts-form').scrollIntoViewIfNeeded();await cap('publish-owner-hosts-edit','#owner-hosts-form');
 }
 const subs=[['ios','set-a2hs'],['remotes','set-remotes-open'],['mail','set-mail-nav'],['contacts','set-contacts-nav'],['auth','set-auth-nav'],['imessage','set-imsg-nav'],['cards','set-cards-nav'],['notify','set-notify-nav'],['hooks','set-hooks-nav'],['files','set-local-files-nav'],['accts','set-accts-nav']];
 for(const[name,id]of subs){
  if(name==='ios'&&cfg.width>390){if(selected.has('settings-ios'))rows.push({...cfg,name:'settings-ios',ok:true,notApplicable:'Desktop UA uses browser install menu'});continue;}
  await p.evaluate(()=>import('./js/settings.js').then(m=>{m.close();m.open();}));await p.evaluate(id=>document.getElementById(id)?.click(),id);await p.waitForTimeout(200);await cap('settings-'+name,name==='files'?'#set-local-files':'#set-'+name);
  if(name==='contacts'){await p.evaluate(()=>document.getElementById('ctm-add').click());await cap('contact-editor','#ctmEdit');await p.evaluate(()=>document.getElementById('ctm-e-cancel').click());}
 }
 await p.evaluate(()=>import('./js/settings.js').then(m=>m.close()));await p.evaluate(()=>document.getElementById('open-models').click());await cap('models','#models-view');await p.evaluate(()=>document.getElementById('models-back').click());
 if(selected.has('wallet-automation')){
 await p.evaluate(()=>import('./js/five-pages.js').then(m=>m.openTarget('automation')));await p.waitForFunction(()=>document.querySelector('.jc-body[data-card=automation] .jc-automation'));await p.waitForFunction(()=>document.querySelector('[data-item="agentj-sample"]')&&document.querySelector('[data-item="native-sample"]'));
 const shown=await p.locator('.jc-body').innerText();if(!shown.includes('agentj')||!shown.includes('systemd'))throw Error('Both automation source families must actually render');
 for(const id of ['agentj-sample','native-sample'])if(await p.locator('[data-item="'+id+'"] button').count()<2)throw Error('Typed control missing from automation row '+id);
 await p.locator('.jc-body > .fp-actions button:last-child').click();await p.waitForFunction(()=>document.querySelector('[data-item="tail-sample"]'));
 await p.locator('.jc-body > .fp-actions button:first-child').click();await p.waitForFunction(()=>document.querySelector('[data-item="agentj-sample"]'));await p.locator('.jc-body > .fp-actions').scrollIntoViewIfNeeded();
 await cap('wallet-automation','#five-page');await p.evaluate(()=>import('./js/five-pages.js').then(m=>m.go('chat')));
 }
 // Actual build identity uses an independent fresh page with no VERSION transform or cached module.
 const raw=await rawAboutPage();let about;
 try{
  await raw.evaluate(()=>document.getElementById('menu-about').click());await raw.waitForFunction(()=>!document.getElementById('badge-panel').hidden);mkdirSync(resolve(out,'raw-about'),{recursive:true});
  const actual=await raw.evaluate(async()=>({version:await(await fetch('/version.json',{cache:'no-store'})).json(),text:document.getElementById('badge-panel').innerText,renderedHash:document.getElementById('version-hash').textContent}));
  if(actual.version.combined!==sourceCombined||actual.renderedHash!==sourceCombined)throw Error('Raw About actual source hash mismatch');
  const aboutFile='raw-about/'+tag+'.png',textFile='raw-about/'+tag+'.txt';await captureStable(resolve(out,aboutFile),false,raw);writeFileSync(resolve(out,textFile),actual.text);about={...cfg,sourceCombined,screenshot:aboutFile,sha256:createHash('sha256').update(readFileSync(resolve(out,aboutFile))).digest('hex'),textFile,textSha256:createHash('sha256').update(actual.text).digest('hex'),actualVersion:actual.version.version};
 }finally{await raw.close();}
 if(selected.has('publish-dashboard-account')){
 const dash=await startCriticalDashboard({publicRoot:dashboardSource,now:input.now}),dp=await phaseAuxiliaryPage(ctx);
 try{const meta=await prepareDashboardPage({page:dp,context:ctx,dashboard:dash,config:cfg});await dp.waitForFunction(()=>document.getElementById('loading')?.hidden===true&&document.getElementById('signed-in')?.hidden===false&&document.getElementById('loading')?.getAttribute('aria-busy')==='false');if(!await dp.locator(meta.target).isVisible())throw Error('Dashboard account content is not visible');const path=resolve(out,tag+'-publish-dashboard-account.png'),stable=await captureStable(path,false,dp);rows.push({...cfg,name:meta.name,ok:true,target:meta.target,selfStability:stable,sourceScope:meta.sourceScope,preservedAssetsSha256:meta.preservedAssetsSha256,accountBootComplete:true});}finally{await dp.close();await dash.stop();}
 }
 return about;
}
