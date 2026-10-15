// P270: real client, local fake host, owned Chromium. UI fixtures contain no live credentials.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {mkdirSync,writeFileSync} from 'node:fs';
import {randomBytes,createHash} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,key,rect,tap,shoot,sleep} from './browser.mjs';
const rec=parityRecorder('web/test/p270.mjs');
const out=process.env.P270_SCREEN_OUT || 'reports/qa/p270/screens'; mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const id=()=>randomBytes(16).toString('hex');
const report='# 调研报告\n\n| 客户凭据 | 配置说明 |\n| --- | --- |\n| APIFY_TOKEN | secret=true；credentials 说明从 Apify Console 获取 |\n\n示例：`<只显示一次>`，没有真实密钥。';
const marker='sk-'+randomBytes(24).toString('hex');
const secret='# 密钥配置报告\n\n此处仅为合成验收夹具。\n\n```text\nAPI_TOKEN='+marker+'\n```\n\n'+Array.from({length:32},(_,i)=>'## 第 '+(i+1)+' 项\n\n可以滚动阅读，复制仍保留原文。').join('\n\n')+'\n\n<script>window.P270_LEAK=1</script>';
try {
 for(const [name,w,h,touch] of [['phone',390,844,true],['desktop',1440,900,false]]){
  fake.reset();
  const p=await newPage(B,w,h,'light',{touch,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url)); await waitState(p,'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
   await evaluate(p,"document.getElementById('phone-env-dismiss')?.click(); document.getElementById('a2hs-ok')?.click()");
   const turn=await fake.addTurn({k:'phone',text:'看调研报告'},'[调研报告](out/REPORT.md)','done');
   const mid=randomBytes(16).toString('base64url'),bytes=Buffer.from(report);
   fake.st.onApp=async(c,m,send)=>{if(m.t!=='media_get')return false;await send({t:'media_chunk',mid:m.mid,o:0,d:bytes.toString('base64url'),last:true});return true;};
   await fake.updateTurn(turn.id,{media:[{mid,name:'REPORT.md',mime:'text/markdown',kind:'file',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),ref:'out/REPORT.md'}]});
   await waitFor(p,"!!document.querySelector('.mcard[data-kind=file][data-state=ready]')");
   assert.equal(await evaluate(p,"!!document.getElementById('sout') && !document.getElementById('sout').hidden"),false);
   writeFileSync(out+'/'+name+'-ordinary-file.png',await shoot(p));
   await evaluate(p,"document.getElementById('readBtn').click()");
   await waitFor(p,"document.body.dataset.reader==='1'");
   const sid=id();
   await evaluate(p,`import('./js/secretout.js').then(m=>m.add({id:${JSON.stringify(sid)},n:${JSON.stringify(id())},kind:'file',name:'配置报告',filename:'config.md',purpose:'P270 合成夹具',size:${Buffer.byteLength(secret)},ttl:600}))`);
   await waitFor(p,"document.getElementById('sout') && !document.getElementById('sout').hidden");
   assert.equal(await evaluate(p,"document.getElementById('sout-expand').hidden"),true);
   writeFileSync(out+'/'+name+'-pickup-masked.png',await shoot(p));
   // Value event is the post-Face-ID seam. Authentication remains covered by test_p73_secret/e2e_p73.
   await evaluate(p,`import('./js/secretout.js').then(m=>m.value({id:${JSON.stringify(sid)},data:${JSON.stringify(Buffer.from(secret).toString('base64'))}}))`);
   await waitFor(p,"!!document.querySelector('#sout-content h1')");
   assert.equal(await evaluate(p,"window.P270_LEAK===1"),false);
   writeFileSync(out+'/'+name+'-pickup-preview.png',await shoot(p));
   await evaluate(p,"document.getElementById('sout-expand').click()");
   await waitFor(p,"document.getElementById('sout').classList.contains('sout-full')");
   assert.ok(await evaluate(p,"document.querySelector('#sout>.box').getBoundingClientRect().width >= innerWidth-2"));
   assert.ok(await evaluate(p,"document.getElementById('sout-content').scrollHeight > document.getElementById('sout-content').clientHeight"));
   await evaluate(p,"document.getElementById('sout-full-copy').click()"); await sleep(100);
   assert.equal(await evaluate(p,"navigator.clipboard.readText()"),secret);
   writeFileSync(out+'/'+name+'-pickup-fullscreen.png',await shoot(p));
   await evaluate(p,"document.getElementById('sout-content').scrollTop=700");
   assert.ok(await evaluate(p,"document.getElementById('sout-content').scrollTop>0"));
   await key(p,'Escape'); await waitFor(p,"!document.getElementById('sout').classList.contains('sout-full')");
   assert.ok(await evaluate(p,"!document.getElementById('sout').hidden && document.getElementById('sout-content').textContent.includes('API_TOKEN') && document.body.dataset.reader==='1'"));
   await sleep(150); await evaluate(p,"document.getElementById('sout-content').scrollTop=0");
   if(touch){const r=await rect(p,'#sout-content h1');await tap(p,r.x,r.y);await sleep(100);await tap(p,r.x,r.y);}
   else await evaluate(p,"document.getElementById('sout-content').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))");
   await waitFor(p,"document.getElementById('sout').classList.contains('sout-full')");
   await evaluate(p,"history.back()"); await waitFor(p,"!document.getElementById('sout').classList.contains('sout-full')");
   assert.ok(await evaluate(p,"!document.getElementById('sout').hidden && document.body.dataset.reader==='1'"));
   await evaluate(p,"document.getElementById('sout-expand').click();document.getElementById('sout-back').click()");
   await waitFor(p,"!document.getElementById('sout').classList.contains('sout-full')");
   await sleep(150);
   await evaluate(p,"document.getElementById('sout-expand').click()");
   await evaluate(p,"import('./js/secretout.js').then(m=>m.clear())");
   await waitFor(p,"document.getElementById('sout').hidden");
   assert.equal(await evaluate(p,"document.getElementById('sout-content').textContent"),'');
   assert.ok(!(await evaluate(p,"JSON.stringify({...localStorage,...sessionStorage})")).includes(marker));
   assert.equal(await evaluate(p,"document.getElementById('sout').classList.contains('sout-full')"),false);
   // Timeout and pagehide also wipe the very same fullscreen DOM.
   await sleep(150);
   for (const reason of ['timeout','pagehide']) {
     const next=id();
     await evaluate(p,`(() => {const original=window.setTimeout;window.setTimeout=(fn,ms,...args)=>{if(ms===120000)window.__p270wipe=fn;return original(fn,ms,...args);};})()`);
     await evaluate(p,`import('./js/secretout.js').then(m=>{m.add({id:${JSON.stringify(next)},n:${JSON.stringify(id())},kind:'file',name:'配置',filename:'config.md',size:100,ttl:600});m.value({id:${JSON.stringify(next)},data:${JSON.stringify(Buffer.from(secret).toString('base64'))}});})`);
     await waitFor(p,"!document.getElementById('sout-expand').hidden");
     await evaluate(p,"document.getElementById('sout-expand').click()");
     await evaluate(p,reason==='timeout'?"window.__p270wipe();delete window.__p270wipe":"dispatchEvent(new Event('pagehide'))");
     await waitFor(p,"document.getElementById('sout').hidden");
     assert.equal(await evaluate(p,"document.getElementById('sout-content').textContent"),'');
     await sleep(150);
   }
   console.log('PASS '+name+': ordinary file, masked card, Markdown, icon/double tap, scroll/copy, ESC/back, revoke wipe');
  }finally{await p.close();}
 }
}finally{await B.close();await fake.stop();await web.stop();}

rec.record('aj-secret-pickup-card',true);rec.write();
