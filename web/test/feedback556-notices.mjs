// #556: real page + local encrypted host, no live passwords, UV or production state.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {generateKeyPairSync} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const before=process.env.P556_BEFORE, out=`reports/qa/sudo-save-toast-556-20261106/web/${before?'before':'after'}`;
mkdirSync(out,{recursive:true});
const web=await startWebServer(before?{publicDir:before}:{}),fake=await startFakeHost(),B=await launch(),checks=[];
const geometry=`(()=>{const n=document.getElementById('toast'),r=n.getBoundingClientRect(),cs=getComputedStyle(n),ids=['tools','input'],protectedRects=ids.map(id=>document.getElementById(id)).filter(e=>e&&!e.closest('[hidden]')&&e.getClientRects().length).map(e=>{const r=e.getBoundingClientRect();return {id:e.id,x:r.x,y:r.y,right:r.right,bottom:r.bottom,w:r.width,h:r.height}}),overlaps=protectedRects.filter(p=>r.left<p.right&&r.right>p.x&&r.top<p.bottom&&r.bottom>p.y).map(p=>p.id);return {classes:n.className,parent:n.parentElement.id||n.parentElement.className,top:r.top,bottom:r.bottom,left:r.left,right:r.right,w:r.width,h:r.height,position:cs.position,z:cs.zIndex,radius:cs.borderRadius,bg:cs.backgroundColor,overlaps,protectedRects,height:innerHeight,scrollHeight:n.scrollHeight}})()`;
try {
 for(const width of [390,1440])for(const theme of ['light','dark']) {
  fake.reset();fake.setUp(true);
  const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  const tag=`zh-${theme}-${width}`;
  try {
   await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}'`);
   const dev=await evaluate(p,`import('./js/api.js').then(m=>m.myDeviceId())`);
   await fake.addTurn({k:'phone',dev,text:'检查备份任务'},'已检查备份任务。执行结果会显示在回复卡下方。');
   await waitFor(p,`document.getElementById('pg').textContent==='1 / 1'`);
   const check=async(name,{center=false,reply=false,modal=false,negative=false}={})=>{
    await sleep(300);const g=await evaluate(p,geometry);checks.push({tag,name,...g});
    if(!before){
     if(negative)assert.ok(g.overlaps.length,'restoring old bottom placement must be detected');
     else {assert.deepEqual(g.overlaps,[],`${tag}/${name}: ${JSON.stringify(g)}`);assert.ok(g.h>0);assert.ok(g.top>=0&&g.bottom<=g.height+1,JSON.stringify(g));}
     if(center)assert.ok(Math.abs((g.top+g.bottom)/2-g.height/2)<1.5,JSON.stringify(g));
     if(reply)assert.equal(await evaluate(p,`document.getElementById('reply-notices').contains(document.getElementById('toast'))`),true);
     if(modal)assert.equal(await evaluate(p,`(()=>{const t=document.getElementById('toast'),e=document.getElementById('elev');return Number(getComputedStyle(t).zIndex)>Number(getComputedStyle(e).zIndex)})()`),true,'notice above password card');
    }
    writeFileSync(`${out}/${tag}-${name}.png`,await shoot(p,''));
   };
   const show=spec=>evaluate(p,`import('./js/ui.js').then(m=>m.notify(${JSON.stringify({...spec,ms:60000})}))`).catch(e=>{throw e});
   await show({kind:'info',text:'已复制'});await check('circle',{center:true});
   await show({kind:'info',text:'这是一条说明，完整内容显示在居中圆角通知卡内。'});await check('square',{center:true});
   await show({kind:'warn',text:'备份检查已完成，请检查结果。'});await check('reply-result',{reply:true});
   const epk=generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x;
   await fake.send({t:'elev',id:'a'.repeat(32),kind:'sudo',n:'b'.repeat(32),epk,ttl:120,tries:3,cmd:'mkdir -p /var/lib/agentj-workspaces/fixture',why:'创建工作目录',effect:'创建测试目录',passkey_required:true,fa:'c'.repeat(22),fa_s:'local'});
   await waitFor(p,`!!document.getElementById('elev')&&!document.getElementById('elev').hidden`);
   await show({kind:'warn',text:'命令已执行，但管理员密码没有保存。下次仍需输入密码。'});await check('sudo-remember-result',{center:true,modal:true});
   await show({kind:'info',text:'已完成'});await check('modal-circle',{center:true,modal:true});
   await show({kind:'info',text:'已为这次操作更新状态，详情显示在卡片内。'});await check('modal-square',{center:true,modal:true});
   if(!before){
    await evaluate(p,`document.getElementById('toast').style.cssText='position:fixed;left:50%;top:auto;bottom:16px;transform:translate(-50%,0);z-index:9999'`);
    await check('negative-old-bottom',{negative:true});
    await evaluate(p,`document.getElementById('toast').style.cssText=''`);
    await show({kind:'warn',text:'命令已执行，但管理员密码没有保存。'});
    await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
    assert.equal(await evaluate(p,`document.getElementById('toast').classList.contains('on')`),false,'Escape dismisses transient results');
    await evaluate(p,`import('./js/elevate.js').then(m=>m.clear());document.getElementById('input').value='长草稿\\n'.repeat(10);document.getElementById('input').dispatchEvent(new Event('input',{bubbles:true}))`);
    await p.send('Emulation.setDeviceMetricsOverride',{width,height:520,deviceScaleFactor:1,mobile:width===390});
    await show({kind:'warn',text:'长草稿和较小视窗下，这条通知仍应避开输入框与五个图标。'.repeat(12)});
    // Force the modal-style fallback while retaining the actual protected composer geometry.
    await evaluate(p,`(()=>{const n=document.getElementById('toast');document.body.appendChild(n);n.classList.add('over-dlg');dispatchEvent(new Event('resize'))})()`);
    await check('small-height-long-draft');
    await show({kind:'info',text:'这是一条完整的长通知，需要触摸滚动查看全部文字。'.repeat(100)});
    await check('small-height-long-square');
    const hit=await evaluate(p,`(()=>{const n=document.getElementById('toast'),r=n.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2,overflow:n.scrollHeight>n.clientHeight,events:getComputedStyle(n).pointerEvents}})()`);
    assert.equal(hit.events,'auto','long square accepts scrolling');assert.equal(hit.overflow,true);
    await p.send('Input.dispatchMouseEvent',{type:'mouseMoved',x:hit.x,y:hit.y});
    await p.send('Input.dispatchMouseEvent',{type:'mouseWheel',x:hit.x,y:hit.y,deltaX:0,deltaY:160});await sleep(150);
    assert.ok(await evaluate(p,`document.getElementById('toast').scrollTop>0`),'real wheel scrolls the long notice');
    await p.send('Emulation.setDeviceMetricsOverride',{width,height:width===390?844:900,deviceScaleFactor:1,mobile:width===390});
    await evaluate(p,`document.getElementById('input').value='';document.getElementById('input').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('setBtn').click()`);
    await waitFor(p,`!document.getElementById('settings').hidden`);
    await show({kind:'error',text:'设置保存失败，请再试一次。'});await check('settings-result',{center:true});
    assert.equal(await evaluate(p,`document.getElementById('settings').contains(document.getElementById('toast'))`),true,'local page owns result');
    await evaluate(p,`import('./js/settings.js').then(m=>m.close());window.relayNative.notice('文件没有加进附件，请再试一次。')`);await check('native-page-notice',{center:true});
   }
  }finally{await p.dispose();}
 }
}finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(out+'/geometry.json',JSON.stringify({before:!!before,checks},null,2)+'\n');
console.log(`PASS #556 ${before?'before captured':'after'}: ${checks.length} measured notice placements`);
