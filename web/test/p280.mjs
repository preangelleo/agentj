// P280 — real phone page, encrypted local fake peer (all processes owned and reaped), zh/en × 390/1440:
// 1) typing /clear + Enter + confirming leaves no `/clear 清空对话（可撤销）` suggestion card hanging over the input;
// 2) 「撤销清空」 shows under the new-conversation greeting until the first message, then becomes a plain status line.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,shoot,sleep,key} from './browser.mjs';
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const out='reports/qa/p280/phone';mkdirSync(out,{recursive:true});
const COPY={
 zh:{greet:'董事长，翻开新一页，晚上咱们做点啥？\n上一段对话我替您收好了，发新消息之前都能撤销。',msg:'帮我看看明天的日程',undo:'撤销清空',gone:'新对话已经开始，来不及撤销了（旧对话存在Agent J 主机上）'},
 en:{greet:"New page, boss. What shall we do tonight?\nI've put the last conversation away — you can undo until you send a new message.",msg:"What's on tomorrow?",undo:'Undo clear',gone:'The new conversation has started — too late to undo (the old one is saved on the Agent J host)'},
};
try{
 for(const lang of ['zh','en'])for(const [w,h] of [[390,844],[1440,900]]){
  const tag=`${lang}-${w}`,c=COPY[lang];
  fake.reset();const p=await newPage(B,w,h,'light',{touch:w<800,allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,"if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click()");
  const dev=await evaluate(p,"import('./js/api.js').then(api=>api.myDeviceId())");
  // 1) /clear typed, suggestion picked by Enter, confirmed → the list is gone (it used to reopen when the sheet closed)
  await evaluate(p,"document.getElementById('input').focus()");
  await p.send('Input.insertText',{text:'/clear'});
  await waitFor(p,"!document.getElementById('sug').hidden");
  await key(p,'Enter');
  await waitFor(p,"!document.getElementById('confirm').hidden");
  writeFileSync(`${out}/${tag}-confirm.png`,await shoot(p,''));
  await evaluate(p,"document.getElementById('confirm-yes').click()");
  await waitFor(p,"document.getElementById('input').value===''");
  await sleep(400);
  assert.ok(fake.st.slashes.some(m=>m.cmd==='clear'&&m.confirm===true),'the clear was sent, confirmed');
  assert.equal(await evaluate(p,"document.getElementById('sug').hidden"),true,`${tag}: no stale /clear suggestion`);
  // 2) the host's new-conversation card: undo while nothing has been said yet …
  const card=await fake.addTurn({k:'cmd',dev,text:'/clear'},c.greet,'done',{cmd:'clear',ok:true,kind:'ok',undo:true,sep:true});
  await waitFor(p,`!!document.querySelector('#cmdx .cmdundo')&&document.getElementById('words').textContent.includes(${JSON.stringify(c.greet.split('\n')[0])})`);
  assert.equal(await evaluate(p,"document.querySelector('#cmdx .cmdundo').textContent"),c.undo);
  writeFileSync(`${out}/${tag}-undo-open.png`,await shoot(p,''));
  // … then the first message opens the new conversation: back on the card, no button — a status line
  await fake.addTurn({k:'phone',dev,text:c.msg},'echo: '+c.msg,'done');
  await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify('echo: '+c.msg)})`);
  await evaluate(p,"document.getElementById('pgPrev').click()");
  await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify(c.greet.split('\n')[0])})`);
  await waitFor(p,"!!document.querySelector('#cmdx .cmdundogone')");
  assert.equal(await evaluate(p,"!!document.querySelector('#cmdx .cmdundo')"),false,`${tag}: no undo button after the new conversation began`);
  assert.equal(await evaluate(p,"document.querySelector('#cmdx .cmdundogone').textContent"),c.gone);
  writeFileSync(`${out}/${tag}-undo-gone.png`,await shoot(p,''));
  assert.ok(card.id>0);
  await p.dispose();
  console.log(`PASS ${tag} no stale suggestion · undo open · undo gone after the first message`);
 }
}finally{await B.close();await fake.stop();await web.stop();}
