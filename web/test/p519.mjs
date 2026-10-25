// P519: real Noise pairing + registry projection + production Markdown/linkify + four chairman-approved visual locks (2026-10-25: 卡名自动链接批准).
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,shoot,sleep} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url)),out=root+'reports/qa/p519/',base=root+'web/test/fixtures/p519/';
mkdirSync(out,{recursive:true});mkdirSync(base,{recursive:true});
const fx=JSON.parse(readFileSync(root+'host/tests/fixtures/p360_cards.json'));
const rec=parityRecorder('web/test/p519.mjs'),web=await startWebServer({port:0}),fake=await startFakeHost(),B=await launch();
const q=JSON.stringify,evidence=[];
const check=(a,b,label)=>{assert.deepEqual(a,b,label);rec.record(label,true);};
let cards;
try{
for(const lang of ['zh','en']){
 const dict=JSON.parse(readFileSync(root+'web/i18n/web.'+lang+'.json'));
 check(fx.list.system.map(c=>[c.id,c.name[lang]]),[['chat',dict['p244.fivepages1']],['workflows',dict['p244.fivepages2']],['skills',dict['p244.fivepages3']],['memory',dict['p244.fivepages4']],['agentj','Agent J']],'system-display-names-'+lang);
}

for(const [name,lang,theme,negative] of [['zh-light','zh','light',false],['zh-dark','zh','dark',false],['en-light','en','light',false],['code-excluded','zh','light',true]]){
 fake.reset();cards=structuredClone(fx.list.cards);fake.st.caps=['wf1','jcards1'];
 fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 fake.st.onApp=async(c,m,send)=>{
  if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards,system:fx.list.system});return true;}
  if(m.t==='jcard_get'){await send({t:'jcard_get',r:m.r,...fx.get[lang][m.id]});return true;}
  if(m.t==='jcard_set'){cards.find(c=>c.id===m.id).on=m.on;await send({t:'jcard_res',r:m.r,ok:true,id:m.id,on:m.on});return true;}
  return false;
 };
 const p=await newPage(B,390,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
 await evaluate(p,`(async()=>{window.jc=await import('./js/jcards.js');window.fp=await import('./js/five-pages.js');window.md=await import('./js/md.js');await jc.refresh();window.matrix=(text,owner=false,names=true)=>{const b=document.createElement('div');b.append(md.RelayMD.toDOM(md.RelayMD.parse(text),document));fp.linkify(b,owner,names);return {links:[...b.querySelectorAll('.fp-link')].map(x=>[x.textContent,x.dataset.card||'']),protected:b.querySelectorAll('pre .fp-link,code .fp-link,a .fp-link').length,text:b.textContent};};})()`);
 if(name==='zh-light'){
 const cases=[
  ['待办卡，然后待办。',['待办卡','todo']],['待办，然后待办卡。',['待办','todo']],
  ['To-do card then TODO and to-do.',['To-do card','todo']],['cAlEnDaR',['cAlEnDaR','calendar']],
  ['İ 待办卡',['待办卡','todo']],['《待办卡》',['待办卡','todo']],['记事本 / Notebook',['记事本','notes']],['技能卡 Skills',['技能卡','skills']],
 ];
 for(const [s,first] of cases)check((await evaluate(p,`matrix(${q(s)})`)).links,[first],'match-'+s);
 for(const s of ['`待办卡`、`待办`','```text\n待办卡 待办 Notebook\n```','[待办卡](https://example.com/待办)','https://example.com/待办卡?q=Notebook','www.example.com/待办卡','example.com/待办卡?card=Notebook','Notebookish myCalendar calendar_name','[x](https://example.com/Calendar)']){
 const r=await evaluate(p,`matrix(${q(s)})`);check(r.links,[],'excluded-'+s);check(r.protected,0,'protected-'+s);
 }
 check((await evaluate(p,`matrix('待办卡 记事本 Calendar',true)`)).links,[],'owner-plain');
 check((await evaluate(p,`matrix('待办卡',false,false)`)).links,[],'non-agent-document');
 check((await evaluate(p,`matrix('文件卡 Files Obsidian 调色板卡')`)).links,[],'default-off');
 check((await evaluate(p,`matrix('**待办卡** / 待办\\n\\nNotebook')`)).links,[['待办卡','todo'],['Notebook','notes']],'dedup-across-dom');
 await evaluate(p,`jc.setOn('todo',false)`);check((await evaluate(p,`matrix('待办卡 TODO')`)).links,[],'switched-off');
 await evaluate(p,`jc.setOn('todo',true)`);
 // A custom card gets both languages and literal (non-regex) aliases; a collision reuses the chooser.
 cards.push({...cards[0],id:'custom-test',official:false,name:{zh:'项目卡',en:'Project card'},aliases:['A+B (C)'],on:true});
 await evaluate(p,`jc.refresh()`);check((await evaluate(p,`matrix('项目卡 Project card A+B (C)')`)).links,[['项目卡','custom-test']],'custom-bilingual');
 check((await evaluate(p,`matrix('A+B (C)')`)).links,[['A+B (C)','custom-test']],'literal-alias');
 cards.pop();await evaluate(p,`jc.refresh()`);
 cards.push({...cards[0],id:'duplicate-test',official:false,name:{zh:'记事本',en:'Notebook'},aliases:[],on:true});await evaluate(p,`jc.refresh()`);
 check((await evaluate(p,`matrix('记事本')`)).links,[['记事本','']],'duplicate-chooser');
 await evaluate(p,`(()=>{const b=document.createElement('div');b.append(md.RelayMD.toDOM(md.RelayMD.parse('记事本'),document));fp.linkify(b,false,true);b.querySelector('.fp-link-card').click();})()`);
 await waitFor(p,`document.querySelectorAll('#five-detail .jc-choose').length===2`);
 check(await evaluate(p,`[...document.querySelectorAll('#five-detail .jc-choose')].map(x=>x.textContent)`),['记事本 · 官方','记事本 · 我的'],'duplicate-labels');
 await evaluate(p,`document.querySelector('#five-detail .jc-choose[data-card="notes"]').click()`);await waitFor(p,`document.body.dataset.fivePage==='card' && document.getElementById('five-title').textContent==='记事本'`);rec.record('duplicate-navigation',true);
 await evaluate(p,`fp.go('chat')`);await waitFor(p,`!history.state?.ajFive`);await sleep(400);cards.pop();await evaluate(p,`jc.refresh()`);
 rec.record('matrix',true);
 }
 const reply=negative?'正文里的待办卡可以点击。\n\n代码保持原样：\n\n```text\n待办卡\n待办\nNotebook\n```\n\n行内代码：`日历卡`\n\n已有链接：[记事本](https://example.com/notebook)\n\n再提到待办，不重复加链接。':lang==='zh'?'已经把事项记到待办卡。\n\n您也可以打开日历卡查看安排，或到记事本补充想法。\n\n技能卡里可以查看已有技能。\n\n再提到待办，保持普通文字。':'I added the item to the To-do card.\n\nOpen Calendar to review your schedule, or Notebook to add your thoughts.\n\nThe Skills card lists your available skills.\n\nMentioning to-do again keeps plain text.';
 await fake.addTurn({k:'phone',text:lang==='zh'?'把事项放进待办卡，再看看日历卡。':'Add it to the To-do card and check Calendar.'},reply);
 await waitFor(p,`document.querySelector('#words .fp-link-card')`);await sleep(450);
 assert.ok(await evaluate(p,`document.getElementById('omText').textContent.includes(${q('待办卡')}) || document.getElementById('omText').textContent.includes('To-do card')`));
 check(await evaluate(p,`document.querySelectorAll('#omText .fp-link-card').length`),0,'owner-render-'+name);
 check(await evaluate(p,`document.querySelectorAll('#words pre .fp-link,#words code .fp-link,#words a .fp-link').length`),0,'protected-render-'+name);
 check(await evaluate(p,`document.documentElement.dataset.theme`),theme,'theme-'+name);
 const links=await evaluate(p,`[...document.querySelectorAll('#words .fp-link-card')].map(x=>[x.textContent,x.dataset.card])`);
 check(links,negative?[['待办卡','todo']]:lang==='zh'?[['待办卡','todo'],['日历卡','calendar'],['记事本','notes'],['技能卡','skills']]:[['To-do card','todo'],['Calendar','calendar'],['Notebook','notes'],['Skills card','skills']],'reply-links-'+name);
 const region=await evaluate(p,`(()=>{const r=document.getElementById('words').getBoundingClientRect();return [Math.floor(r.x),Math.floor(r.y),Math.ceil(r.right),Math.ceil(r.bottom)]})()`);
 const shot=await shoot(p,'unused');writeFileSync(out+name+'.png',shot);
 if(process.env.P519_WRITE_BASELINE==='1')writeFileSync(base+name+'.png',shot);
 const baseline=readFileSync(base+name+'.png');
 const pin=JSON.parse(readFileSync(base+'manifest.json'))[name];assert.equal(createHash('sha256').update(baseline).digest('hex'),pin.sha256,'approved baseline bytes');
 const visual=await evaluate(p,`(async()=>{const load=async(src)=>{const i=new Image();i.src=src;await i.decode();return i;};const a=await load('data:image/png;base64,${baseline.toString('base64')}'),b=await load('data:image/png;base64,${shot.toString('base64')}');if(a.width!==b.width||a.height!==b.height)throw Error('viewport changed');const box=${q(region)},w=box[2]-box[0],h=box[3]-box[1];const pixels=i=>{const c=document.createElement('canvas');c.width=w;c.height=h;const x=c.getContext('2d');x.drawImage(i,-box[0],-box[1]);return x.getImageData(0,0,w,h).data;};const x=pixels(a),y=pixels(b);let diff=0;for(let i=0;i<x.length;i+=4)if(Math.max(Math.abs(x[i]-y[i]),Math.abs(x[i+1]-y[i+1]),Math.abs(x[i+2]-y[i+2]))>16)diff++;return {diff,pixels:w*h,box};})()`);
 assert.equal(visual.diff,0,JSON.stringify(visual));rec.record('visual-'+name,true);
 evidence.push({name,links,visual,baselineSha256:createHash('sha256').update(baseline).digest('hex'),problems:p.problems});
 if(name==='zh-light'){
 await evaluate(p,`document.querySelector('#words .fp-link-card[data-card="todo"]').click()`);
 await waitFor(p,`document.body.dataset.fivePage==='card' && document.getElementById('five-title').textContent==='待办'`);rec.record('navigation',true);
 await evaluate(p,`fp.go('chat')`);await waitFor(p,`!history.state?.ajFive`);await sleep(400);
 await evaluate(p,`document.getElementById('readBtn').click()`);await waitFor(p,`!document.getElementById('rd').hidden`);
 await evaluate(p,`jc.setOn('todo',false)`);await waitFor(p,`!document.querySelector('#words .fp-link-card[data-card="todo"]') && !document.querySelector('#rdWords .fp-link-card[data-card="todo"]')`);rec.record('live-off',true);
 await evaluate(p,`jc.setOn('todo',true)`);await waitFor(p,`document.querySelector('#words .fp-link-card[data-card="todo"]') && document.querySelector('#rdWords .fp-link-card[data-card="todo"]')`);rec.record('live-on',true);
 await sleep(400);await evaluate(p,`document.getElementById('rdReadBtn').click()`);await waitFor(p,`document.getElementById('rd').hidden && !history.state?.ajReader`);await sleep(400);
 await fake.addTurn({k:'agent',text:''},'这一版的清单我记在待办卡上了。');await waitFor(p,`document.getElementById('words').textContent.includes('这一版的清单') && document.querySelector('#words .fp-link-card')?.textContent==='待办卡'`);rec.record('unsolicited-report',true);
 cards.find(c=>c.id==='todo').on=false;
 await fake.addTurn({k:'agent',text:''},'已关闭待办卡。');await waitFor(p,`document.getElementById('words').textContent.includes('已关闭待办卡') && !document.querySelector('#words .fp-link-card[data-card="todo"]')`);rec.record('host-off-before-reply',true);
 cards.find(c=>c.id==='todo').on=true;
 cards.push({...cards[0],id:'new-card',official:false,name:{zh:'项目卡',en:'Project card'},aliases:[],on:true});
 await fake.addTurn({k:'agent',text:''},'待办卡已开启，也做好了项目卡。');await waitFor(p,`document.querySelector('#words .fp-link-card[data-card="todo"]') && document.querySelector('#words .fp-link-card[data-card="new-card"]')`);rec.record('host-install-before-reply',true);
 }
 assert.deepEqual(p.problems,[]);console.log('PASS',name,JSON.stringify(visual));
 }finally{await p.dispose();}
}
writeFileSync(out+'evidence.json',JSON.stringify(evidence,null,2));
}finally{rec.write();await B.close();await fake.stop();await web.stop();}
