#!/usr/bin/env node
// Snapshot fixtures seed the same two history records as signed-off 541c. Interaction replay separately keeps new history.
import {writeFileSync,readFileSync,mkdirSync,readdirSync} from 'node:fs';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
process.env.P541_LIBRARY='1';process.env.P541_CAPTURE_CLOCK='1';
const installReceiptPath=process.env.P541_INSTALL_RECEIPT;let installReceipt=null;
if(process.env.P541_INSTALLED==='1'){if(!installReceiptPath)throw new Error('POSTINSTALL requires guard installation receipt');const bytes=readFileSync(installReceiptPath);if(!bytes.length)throw new Error('empty installation receipt');installReceipt={path:installReceiptPath,sha256:createHash('sha256').update(bytes).digest('hex')};}
const {setup,fake,B,ev,web,currentQuestion,currentCard}=await import('./p541.mjs');
const {shoot,sleep,waitFor,newPage}=await import('./browser.mjs');
const root='/home/laogege/coding/vibe-remote',out=process.env.P541_INSTALLED==='1'?homedir()+'/coding/.agentj/outbox':process.cwd()+'/reports/qa/541-implement/web/preinstall',evidence=process.cwd()+'/reports/qa/541-implement/web';
mkdirSync(evidence,{recursive:true});mkdirSync(out,{recursive:true});
const startedAt=new Date().toISOString(),sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const publicRoot=new URL('../public/',import.meta.url),sourceManifest=JSON.parse(readFileSync(new URL('version.json',publicRoot),'utf8'));
const actualSource=()=>Object.fromEntries(readdirSync(publicRoot,{recursive:true,withFileTypes:true}).filter(f=>f.isFile()).map(f=>{const rel=join(f.parentPath,f.name).slice(new URL(publicRoot).pathname.length);return[rel,sha(readFileSync(new URL(rel,publicRoot)))];}).sort((a,b)=>a[0].localeCompare(b[0])));const actualSourceStart=actualSource();
const shots=[],diffs=[];let H,cmp;
const startCwd=process.cwd();process.chdir(root);const {createHarness}=await import('file://'+root+'/.jarvis/541c-design/harness.mjs');H=await createHarness();process.chdir(startCwd);
// Both surfaces use the exact same Chromium process, with isolated browser contexts and separate Noise fixtures.
// Reuse the public browser handle; the original design setup/fixture/CSS stay byte-exact.
await H.B.close();Object.assign(H.B,{browser:B.browser,port:B.port});H.close=async()=>{await H.fake.stop();await H.web.stop();};
const referenceFixture=readFileSync(root+'/.jarvis/541c-design/fixture.js','utf8'),referenceCss=readFileSync(root+'/.jarvis/541c-design/fixture.css','utf8');
const frozenDir=join(evidence,'design-frozen');mkdirSync(frozenDir,{recursive:true});
const scenes=[['normal','普通危险卡三按钮'],['suspect','来源可疑卡'],['collapsed','收起心跳正常背景'],['question-waiting','疑问等待回复蓝色'],['explain','疑问原话解释与重弹'],['reject-normal','拒绝正常任务回复'],['reject-suspect','拒绝可疑来源回复'],['history','查看历史正常背景']];
const reply=(suspect,reject)=>reject?(suspect?'**你已拒绝这条可疑请求。**\n\n要求读取并外发密钥的指令来自外部邮件，不是你的授权；这次请求已作废，不会执行。\n\n我会核对来信身份和邮件通道记录。若确认有异常，建议暂停该通道、关闭相关白名单或重设通道凭据；这些处置会先请你确认。':'**你已拒绝这次删除。**\n\n这次请求已作废，文件未删除。清理任务暂时做不了；我可以先整理文件清单，供你核对。'):(suspect?'**先不要确认这张卡。**\n\n读密钥并外发的指令来自外部邮件，不是你的授权。我会核对来信与通道记录；如有异常，建议先暂停邮件通道，相关处置需你确认。\n\n来源核清前请勿批准。':'**这张卡对应你 14:03 的清理任务。**\n\n只会删除上个月的 3 个旧导出文件，本月数据保留。来源消息、文件清单和派单记录一致。\n\n如果这个范围符合你的意思，可以重新打开原卡，用设备确认。');
const stableScript=`(async()=>{
 await document.fonts.ready;
 // Rebuild cached paint on both surfaces after all interaction layout has settled. Restore inline display exactly.
 const n=document.getElementById('deck'),display=n.style.display,before=n.getBoundingClientRect().toJSON();n.style.display='none';void n.offsetHeight;n.style.display=display;void n.offsetHeight;
 const after=n.getBoundingClientRect().toJSON();if(JSON.stringify(before)!==JSON.stringify(after))throw new Error('raster settle changed geometry');
 const rows=[];
 for(let pass=0;pass<3;pass++){
  for(const a of document.getAnimations()){
   const timing=a.effect?.getTiming(),target=a.effect?.target;
   if(timing?.iterations===Infinity){a.pause();a.currentTime=0;}else{try{a.finish();}catch{}}
   rows.push({pass,target:target?.id||target?.className||target?.tagName,currentTime:a.currentTime,playState:a.playState,iterations:timing?.iterations===Infinity?'Infinity':timing?.iterations,duration:timing?.duration});
  }
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 }
 return {raster_settle:{before,after,inline_display:display},animations:rows,body:{...document.body.dataset},selected:Object.fromEntries(['html541','body541','chat','app','deck','om','omText','omQuote','omqText','omMore','rm','words','reopen541','pending541','rmbar','rmIcons','orb','thinking'].map(id=>{const n=id==='html541'?document.documentElement:id==='body541'?document.body:document.getElementById(id);if(!n)return[id,null];const s=getComputedStyle(n),r=n.getBoundingClientRect();return[id,{rect:{x:r.x,y:r.y,w:r.width,h:r.height},style:Object.fromEntries([...s].map(k=>[k,s.getPropertyValue(k)])),scrollTop:n.scrollTop,data:{...n.dataset},src:n.getAttribute('src')}];}))};
})()`;
const freeze=p=>ev(p,stableScript);
async function pixels(p,A,Z){cmp||=await newPage(B,64,64,'light');return ev(cmp,`(async()=>{const load=async b=>{const i=await createImageBitmap(await(await fetch('data:image/png;base64,'+b)).blob()),c=new OffscreenCanvas(i.width,i.height),x=c.getContext('2d');x.drawImage(i,0,0);return x.getImageData(0,0,i.width,i.height);};const a=await load(${JSON.stringify(A.toString('base64'))}),b=await load(${JSON.stringify(Z.toString('base64'))});let n=0,x0=a.width,y0=a.height,x1=0,y1=0;for(let k=0;k<a.data.length;k+=4)if(a.data.slice(k,k+4).some((v,j)=>v!==b.data[k+j])){n++;const x=k/4%a.width,y=Math.floor(k/4/a.width);x0=Math.min(x0,x);x1=Math.max(x1,x);y0=Math.min(y0,y);y1=Math.max(y1,y);}return {raw_pixels:n,total:a.width*a.height,bbox:n?[x0,y0,x1+1,y1+1]:null};})()`);}
try{
 for(const [device,w,h] of [['手机',390,844],['电脑',1440,900]])for(const [theme,tc] of [['light','浅色'],['dark','深色']])for(const [scene,label] of (process.env.ONLY_SCENE?scenes.filter(s=>s[0]===process.env.ONLY_SCENE):process.env.ONLY_FIRST?scenes.slice(0,1):scenes)){
  if((process.env.ONLY_FIRST||process.env.ONLY_SCENE)&&(device!=='手机'||theme!=='light'))continue;
  const suspect=['suspect','reject-suspect'].includes(scene),p=await setup(w,h,theme,suspect,true),second=p.fixture.second;
  const staticTs=new Date(new Date().setHours(14,11,0,0)).getTime();await fake.updateTurn(second.id,{ts:staticTs});
  if(scene==='collapsed')await ev(p,`document.getElementById('sheetClose').click()`);
  if(scene==='history'){await ev(p,`document.getElementById('source541').click()`);await sleep(200);}
  if(['question-waiting','explain'].includes(scene)){
   await ev(p,`document.getElementById('doubt541c').click()`);await waitFor(p,`document.body.dataset.sheet==='0'&&document.body.dataset.status==='working'`);await sleep(250);
   if(scene==='explain'){
    await fake.updateTurn(second.id,{reply:{text:reply(suspect,false)},end:'done'});await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await waitFor(p,`!!document.getElementById('reopen541')`);
   }
  }
  if(scene.startsWith('reject')){
   await ev(p,`document.getElementById('apprDeny').click()`);await sleep(200);
   await fake.updateTurn(second.id,{src:{k:'phone',dev:fake.devs[0].id,name:'你',text:'主人拒绝了这张卡。',quote:{id:1,who:'需要 '+(w<500?'Face ID':'Touch ID')+' 确认的操作',text:currentCard().envelope.action+' 来源：'+(suspect?'邮件 · 外部消息':'手机 · 你')+' · 14:03'}},reply:{text:reply(suspect,true)},end:'done',card:{approval_review:'denied',approval_request_id:currentCard().id,approval_label:'541c-review-request'}});await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
  }
  await sleep(750);await ev(p,'document.fonts.ready.then(()=>true)');const productStable=await freeze(p);
  writeFileSync(join(evidence,'dom-'+device+'-'+theme+'-'+scene+'.json'),JSON.stringify(await ev(p,`Object.fromEntries(['deck','om','omText','omQuote','omqText','omMore','rm','words','reopen541','pending541','rmbar','rmIcons'].map(id=>{const n=document.getElementById(id);if(!n)return [id,null];const r=n.getBoundingClientRect(),s=getComputedStyle(n);return [id,{text:n.textContent,cls:n.className,data:{...n.dataset},x:r.x,y:r.y,w:r.width,h:r.height,color:s.color,bg:s.backgroundColor,opacity:s.opacity,font:s.font,margin:s.margin,padding:s.padding}]}))`),null,2));
  const filename='541d-实装-'+device+'-'+tc+'-'+label+'.png',bytes=await shoot(p);writeFileSync(join(out,filename),bytes);shots.push({filename,scene,device,theme,sha256:sha(bytes),captured_at:new Date().toISOString()});
  const rp=await H.setup(w,h,theme,suspect?'suspect':scene);await H.ev(rp,referenceFixture+`;const s=document.createElement('style');s.textContent=${JSON.stringify(referenceCss)};document.head.append(s);install541c(${JSON.stringify(scene)});${scene==='history'?'':`for(const id of ['omTime','rmTime'])document.getElementById(id).textContent='· 14:11';`}`);await sleep(750);const referenceStable=await H.ev(rp,stableScript);writeFileSync(join(evidence,'stable-'+device+'-'+theme+'-'+scene+'.json'),JSON.stringify({product:productStable,reference:referenceStable},null,2));const referenceBytes=await H.shoot(rp);const frozenName='541c-frozen-'+device+'-'+tc+'-'+label+'.png';writeFileSync(join(frozenDir,frozenName),referenceBytes);const frozenDiff=await pixels(p,referenceBytes,bytes);await rp.dispose();
  const old=join(homedir()+'/coding/.agentj/outbox','541c-'+device+'-'+tc+'-改后-'+label+'.png');diffs.push({filename,product_sha256:sha(bytes),frozen_reference_sha256:sha(referenceBytes),frozen_reference:join(frozenDir,frozenName),frozen_diff:frozenDiff,reference:old,...await pixels(p,readFileSync(old),bytes)});
  await p.dispose();
 }
}finally{await B.close();await fake.stop();await web.stop();if(H)await H.close();writeFileSync(join(evidence,process.env.ONLY_SCENE?'capture-'+process.env.ONLY_SCENE+'-validation.json':process.env.ONLY_FIRST?'capture-normal-validation.json':'capture-validation.json'),JSON.stringify({started_at:startedAt,completed_at:new Date().toISOString(),source_tree:process.cwd(),source_manifest:sourceManifest,actual_source_sha256:actualSourceStart,source_unchanged:JSON.stringify(actualSourceStart)===JSON.stringify(actualSource()),shared_browser_process:true,test_clock:'Date.now fixed at ask+1000ms for 599s, product source unmodified',raster_settle:'same deck layout invalidation, inline display restored, geometry asserted unchanged',deterministic_compositor:process.env.P541_DETERMINISTIC==='1',fixture_sha256:sha(referenceFixture),fixture_css_sha256:sha(referenceCss),install_receipt:installReceipt,phase:process.env.P541_INSTALLED==='1'?'POSTINSTALL':'PREINSTALL',scope:'real product + signed v2 Noise host fixtures; no injected product DOM; synthetic seeded 2-record history',shots,diffs},null,2));}
console.log(JSON.stringify({shots:shots.length,diffs}));
