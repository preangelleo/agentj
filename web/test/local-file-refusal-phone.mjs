import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, key, shoot, sleep } from './browser.mjs';

const out=process.env.AJ_FILE_REFUSAL_OUT||'reports/qa/local-file-refusal';
const before=process.env.AJ_FILE_REFUSAL_BEFORE;
mkdirSync(out,{recursive:true});
const web=await startWebServer({publicDir:before||undefined}),fake=await startFakeHost(),browser=await launch();
const q=JSON.stringify,rows=[];
const bytes=Buffer.from('A synthetic shareable note.\n');
const item={mid:'X'.repeat(22),name:'note.txt',kind:'file',mime:'text/plain',bytes:bytes.length,
 sha256:createHash('sha256').update(bytes).digest('hex'),ref:'~/coding/.agentj/outbox/note.txt'};
const internal='~/.claude/skills/feedback-to-agentj-father/SKILL.md';
const state='~/.local/state/agentj/activity.json';

try {
 for(const width of [390,1440])for(const language of ['zh','en'])for(const theme of ['light','dark']){
  const zh=language==='zh';
  const skill=zh?'反馈给 Agent J Father 技能':'Feedback to Agent J Father skill';
  const request=zh?'发我检查结果。':'Send me the check results.';
  const opening=zh?'我已按':'I followed the ';
  const end=zh?'完成检查。':' to check what happened.';
  const stateLabel=zh?'Agent J 主机状态':'Agent J host status';
  const fileLabel=zh?'原附件':'Original attachment';
  const preparing=zh?'正在准备文件…':'Preparing file…';
  const unavailable=zh?'这份文件无法发送。':"This file couldn't be shared.";
  const privateName=zh?'这份资料':'This file';
  const privateNote=zh?'内部文件夹':'internal folder';
  const reply=`${opening}[${skill}](${internal})${end}\n\n[${stateLabel}](${state})\n\n[${fileLabel}](outside/external.txt)\n\n[note.txt](${item.ref})`;
  const skips=[{name:privateName,why:'private'},{name:privateName,why:'private'},{name:'external.txt',why:'outside'}];
  fake.reset();fake.setUp(true);fake.st.prefSet=true;
  fake.st.prefs={appearance:{language,theme},voice:{speak_replies:false,wake_enabled:false}};
  const p=await newPage(browser,width,844,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try {
   const pulls=[];
   fake.st.onApp=async(_conn,m,send)=>{
    if(m.t!=='media_get')return false;
    pulls.push(m.mid);
    assert.equal(m.mid,item.mid);assert.equal(m.o,0);
    await send({t:'media_chunk',mid:m.mid,o:0,d:bytes.toString('base64url'),last:true});
    return true;
   };
   await navigate(p,fake.newPairing(web.url+'?lang='+language));
   await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   await evaluate(p,`for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b&&b.offsetParent)b.click();}`);
   const answer=await fake.addTurn({k:'phone',name:zh?'你':'You',text:request},reply,'open');
   await waitFor(p,`document.querySelector('#words .mslot[data-label=${q(fileLabel)}]')?.textContent.includes(${q(preparing)})`);
   if(!before) {
    const plain=await evaluate(p,`[...document.querySelectorAll('#words .minternal')].map(n=>[n.textContent,n.dataset.ref||''])`);
    assert.deepEqual(plain,[[skill,''],[stateLabel,'']]);
   }
   const offered=before?[item]:[item,{...item,mid:'Z'.repeat(22),name:'SKILL.md',ref:internal}];
   await fake.updateTurn(answer.id,{end:'done',media:offered,media_skip:skips});
   await waitFor(p,`document.querySelector('#words .mslot[data-skip] .mskip')?.textContent.includes('external.txt') && !!document.querySelector('#words .mcard[data-state=ready]')`);
   const checks=async(root)=>evaluate(p,`(()=>{
    const n=document.querySelector(${q(root)}),slots=[...n.querySelectorAll('.mslot')];
    return {text:n.textContent,plain:[...n.querySelectorAll('.minternal')].map(x=>x.textContent),
     loading:n.querySelectorAll('.mpending').length,refusal:slots.find(s=>s.dataset.skip==='1')?.textContent,
     attachments:n.querySelectorAll('.mcard').length,strip:document.getElementById('mstrip').textContent};
   })()`);
   const main=await checks('#words');
   assert.equal(main.attachments,1);assert.ok(main.refusal.includes('external.txt'));
   if(before){assert.ok(main.loading>=2);assert.ok(main.strip.includes(privateNote));}
   else {assert.deepEqual(main.plain,[skill,stateLabel]);assert.equal(main.loading,0);assert.equal(main.strip,'');}
   const path=join(out,`file-refusal-${width}-${language}-${theme}.png`);
   writeFileSync(path,await shoot(p));
   await evaluate(p,`document.getElementById('readBtn').click()`);
   await waitFor(p,`!document.getElementById('rd').hidden && !!document.querySelector('#rdWords .mcard')`);
   const reader=await checks('#rdWords');
   assert.equal(reader.attachments,1);assert.ok(reader.refusal.includes('external.txt'));
   if(!before){assert.deepEqual(reader.plain,[skill,stateLabel]);assert.equal(reader.loading,0);assert.ok(!reader.text.includes(privateNote));}
   if(!before){
    await sleep(450);
    await key(p,'Escape');
    await waitFor(p,`document.getElementById('rd').hidden`);
    const owner={k:'phone',dev:fake.devs.at(-1).id,text:request};
    const single=await fake.addTurn(owner,'[Alias](alias/original.txt)','open');
    await waitFor(p,`document.querySelector('#words .mpending')?.textContent===${q(preparing)}`);
    await fake.updateTurn(single.id,{media_skip:[{name:privateName,why:'private'}]});
    await waitFor(p,`document.querySelector('#words .mskip')?.textContent===${q(unavailable)}`);
    assert.equal((await checks('#words')).strip,'');
    await fake.updateTurn(single.id,{end:'done'});
    const delayed=await fake.addTurn(owner,`[Option A](alias/a.txt)\n\n[Option B](${item.ref})`,'open');
    await waitFor(p,`document.querySelectorAll('#words .mpending').length===2`);
    await fake.updateTurn(delayed.id,{end:'done',media_skip:[{name:privateName,why:'private'}]});
    await waitFor(p,`[...document.querySelectorAll('#words .mslot .mskip')].filter(n=>n.textContent===${q(unavailable)}).length===2`);
    assert.equal((await checks('#words')).strip,'');
    await fake.updateTurn(delayed.id,{media:[item]});
    await waitFor(p,`document.querySelector('#words .mcard') && document.querySelector('#words .mslot[data-label="Option A"] .mskip')?.textContent===${q(unavailable)}`);
   }
   assert.ok(pulls.every(mid=>mid===item.mid));
   rows.push({width,language,theme,path,verifiedAttachments:main.attachments,mediaRequests:pulls.length});
  } finally {await p.dispose();}
 }
 console.log(`PASS local file refusal (${before?'baseline':'candidate'}), ${rows.length} Chromium chat and reader scenes`);
} finally {
 writeFileSync(join(out,'browser.json'),JSON.stringify({physical:false,rows},null,2));
 await browser.close();await fake.stop();await web.stop();
}
