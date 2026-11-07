import assert from 'node:assert/strict';
import{createHash}from'node:crypto';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,key,sleep} from './browser.mjs';
const out=process.env.P0284_READER_OUT||'reports/qa/p0284-reader';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),b=await launch(),rows=[];
const fileData=Buffer.from('# 仅文件内容\n\n[文件里的伪按钮](reply:不得发送)');
const cards=JSON.parse(readFileSync('host/tests/fixtures/p360_cards.json')).list.cards;
const component={component:'steps',title:'合成步骤',items:[{label:'界面检查',status:'done'},{label:'等待选择',status:'active'}]};
const fence=String.fromCharCode(96).repeat(3),reply='选择下一步：\n\n[第1步进0.28.4](reply:第1步进0.28.4) · [先不动](reply:先不动)\n\n'+fence+'agentj\n'+JSON.stringify(component)+'\n'+fence+'\n\n查看 card/todo。';
// Preserve the reader's existing 350ms anti-ghost-click boundary.
async function open(p){await sleep(400);await evaluate(p,`document.getElementById('readBtn').click()`);await waitFor(p,`!document.getElementById('rd').hidden`);await sleep(400);}
async function choose(p,value){await evaluate(p,`document.querySelector('#rdWords button.rbtn[data-say="${value}"]').click()`);await waitFor(p,`document.getElementById('rd').hidden`);}
try{for(const width of[390,600,1440])for(const theme of['light','dark']){
 fake.reset();fake.st.caps=['wf1','jcards1'];fake.st.prefs={appearance:{theme,language:'zh'},voice:{speak_replies:false}};
 fake.st.onApp=async(c,m,send)=>{if(m.t==='media_get'){await send({t:'media_chunk',mid:m.mid,o:0,d:fileData.toString('base64url'),last:true});return true;}if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards});return true;}if(m.t==='jcard_get'){await send({t:'jcard_get',r:m.r,ok:true,id:m.id,template:'checklist',items:[],actions:[],links:[],anchors:[]});return true;}return false;};
 const p=await newPage(b,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()`);
  await fake.addTurn({k:'phone',name:'你',text:'合成阅读回放'},reply);await waitFor(p,`document.querySelectorAll('#words button.rbtn').length===2&&!!document.querySelector('#words .fp-link')`);await open(p);
  const content=await evaluate(p,`({buttonLooks:[...document.querySelectorAll('#rdWords button.rbtn')].map(n=>({height:n.getBoundingClientRect().height,border:parseFloat(getComputedStyle(n).borderTopWidth)})),mainButtons:[...document.querySelectorAll('#words button.rbtn')].map(n=>n.dataset.say),readerButtons:[...document.querySelectorAll('#rdWords button.rbtn')].map(n=>n.dataset.say),mainComponents:document.querySelectorAll('#words .reply-component').length,readerComponents:document.querySelectorAll('#rdWords .reply-component').length,mainLinks:document.querySelectorAll('#words .fp-link').length,readerLinks:document.querySelectorAll('#rdWords .fp-link').length,overflow:document.documentElement.scrollWidth>innerWidth})`);
  for(const button of content.buttonLooks){assert.ok(button.height>=44);assert.equal(button.border,1);}assert.deepEqual(content.readerButtons,content.mainButtons);assert.equal(content.readerComponents,content.mainComponents);assert.ok(content.readerComponents>0);assert.equal(content.readerLinks,content.mainLinks);assert.ok(content.readerLinks>0);assert.equal(content.overflow,false);
  const file=`${out}/${width}-${theme}-reader.png`;writeFileSync(file,await shoot(p));
  await evaluate(p,`document.querySelector('#rdWords .fp-link[data-card="todo"]').click()`);await waitFor(p,`document.getElementById('rd').hidden&&document.body.dataset.fivePage==='card'`);assert.equal(fake.st.says.length,0);
  await evaluate(p,`import('./js/five-pages.js').then(m=>m.go('chat'))`);await open(p);await choose(p,'第1步进0.28.4');assert.equal(fake.st.says.length,1);assert.equal(fake.st.says[0].text,'第1步进0.28.4');
  if(width===390&&theme==='light'){
   const earlier=await fake.addTurn({k:'phone',name:'你',text:'旧问题'},reply);await fake.addTurn({k:'agent'},'新的合成页面');await waitFor(p,`document.getElementById('words').textContent.includes('新的合成页面')`);await sleep(400);await evaluate(p,`document.getElementById('pgPrev').click()`);await waitFor(p,`document.querySelectorAll('#words button.rbtn').length===2`);await open(p);await choose(p,'先不动');assert.equal(fake.st.says.at(-1).reply_to,earlier.id);
   for(const[src,text,end]of[[{k:'sys'},'[系统按钮](reply:系统按钮)','done'],[{k:'friend_report',name:'外部'},'[外部按钮](reply:外部按钮)','done'],[{k:'phone',name:'你',text:'引用测试'},'> [引用](reply:引用)\n\n`[代码](reply:代码)`\n\n[命令](reply:/stop)','done'],[{k:'phone',name:'你',text:'还在写'},'[流式选项](reply:流式选项)','open']]){
    const turn=await fake.addTurn(src,text,end),label=text.match(/\[([^\]]+)/)?.[1]||'';await evaluate(p,`document.getElementById('pgLatest').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))`);await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify(label)})`);await open(p);const before=fake.st.says.length;
    assert.equal(await evaluate(p,`document.querySelectorAll('#rdWords button.rbtn:not([aria-disabled="true"])').length`),0,'read-only/untrusted/unfinished text gets no active owner send');await evaluate(p,`document.querySelector('#rdWords button.rbtn')?.click()`);assert.equal(fake.st.says.length,before);
    console.log('p0284-reader-negative',src.k,end,label,before);if(end==='open'){await fake.updateTurn(turn.id,{end:'done'});await waitFor(p,`!!document.querySelector('#rdWords button.rbtn[aria-disabled="false"]')`);await choose(p,'流式选项');assert.equal(fake.st.says.length,before+1);}else{await evaluate(p,`document.getElementById('rdClose').click()`);await waitFor(p,`document.getElementById('rd').hidden`);}
   }
   const fileTurn=await fake.addTurn({k:'phone',name:'你',text:'查看合成文件'},'这是一个合成附件。');const mid='a'.repeat(22);await fake.updateTurn(fileTurn.id,{media:[{mid,name:'synthetic-reader.md',mime:'text/markdown',kind:'file',bytes:fileData.length,sha256:createHash('sha256').update(fileData).digest('hex'),ref:'out/synthetic-reader.md'}]});await evaluate(p,`document.getElementById('pgLatest').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))`);await waitFor(p,`document.querySelector('.mcard[data-mid="${mid}"]')?.dataset.state==='ready'`);await sleep(400);await evaluate(p,`document.querySelector('.mcard[data-mid="${mid}"] .pv').click()`);await waitFor(p,`document.getElementById('rd').dataset.file==='1'&&document.getElementById('rdWords').textContent.includes('仅文件内容')`);cards[0].desc.zh+='（合成刷新）';await evaluate(p,`import('./js/jcards.js').then(m=>m.refresh())`);assert.equal(await evaluate(p,`document.querySelectorAll('#rdWords button.rbtn').length`),0,'untrusted Markdown file never gains owner-send controls after registry refresh');assert.ok(await evaluate(p,`document.getElementById('rdWords').textContent.includes('仅文件内容')`));await sleep(400);await evaluate(p,`document.getElementById('rdClose').click()`);
  }
  rows.push({width,theme,content,file});console.log('PASS p0284-reader-shared-reply-rendering',width,theme);
 }finally{await p.dispose();}
}}finally{writeFileSync(`${out}/result.json`,JSON.stringify({physical:false,rows},null,2));await b.close();await fake.stop();await web.stop();}
