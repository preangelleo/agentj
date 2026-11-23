// 0.30.47-0.30.65 hosts cannot update themselves on Linux with umask 0002. The newest page shows the one terminal line on
// their failed-update cards: display only, chosen from the connection's host version and the old host's fixed words.
import {test} from 'node:test';import assert from 'node:assert/strict';import {readFileSync,readdirSync} from 'node:fs';
globalThis.window={};const {oldUpdaterFixOf,OLD_UPDATER_FIX}=await import('../public/js/repair-actions.js');
const zh='升级到 0.30.69 没成功（已校验的安装包在安装前被改动或替换，已停止安装）；Agent J 主机仍是 0.30.65，照常可用；你关闭了自动上报，没有发给客服。';
const en='The upgrade to 0.30.69 did not go through (the verified package changed or was replaced before installing, so installation stopped); still on 0.30.65, working as before.';
const night='自动升级未完成，原因：install_artifact_changed；Agent J 主机仍是 0.30.65，照常可用。';
const card=(reply,extra={})=>({source:{k:'cmd'},card:{cmd:'update',kind:'error',repair:'reconnect'},end:'done',reply,...extra});
test('the failed update card of an affected host version offers the line',()=>{
 for(const v of ['0.30.47','0.30.58','0.30.60','0.30.65'])for(const reply of [zh,en])assert.equal(oldUpdaterFixOf(card(reply),v),true,v);
 assert.equal(oldUpdaterFixOf({source:{k:'sys',repair:'reconnect'},end:'done',reply:night},'0.30.65'),true);
 // 0.30.47-0.30.57 (no subreason, another tail: seen on signed 0.30.47 and 0.30.52) say the same fixed words
 assert.equal(oldUpdaterFixOf(card('升级到 0.30.69 没成功（已校验的安装包在安装前被改动或替换，已停止安装）；请让我运行 agentj doctor 检查一下；你关闭了自动上报，没有发给客服。'),'0.30.52'),true);
});
test('fixed hosts, other failures and anything not from this host never do',()=>{
 for(const v of ['0.30.46','0.30.66','0.30.69','0.31.65','1.30.65','0.30.65+local1','0.30.650',null,undefined,65])assert.equal(oldUpdaterFixOf(card(zh),v),false,String(v));
 assert.equal(oldUpdaterFixOf(card('升级未完成：依赖包下载失败。'),'0.30.65'),false);
 assert.equal(oldUpdaterFixOf(card(undefined),'0.30.65'),false);
 assert.equal(oldUpdaterFixOf({...card(zh),card:{cmd:'update',kind:'ok',repair:'reconnect'}},'0.30.65'),false);
 assert.equal(oldUpdaterFixOf({source:{k:'agent'},reply:zh,end:'done'},'0.30.65'),false);
 assert.equal(oldUpdaterFixOf({source:{k:'phone'},reply:zh,end:'done'},'0.30.65'),false);
 assert.equal(oldUpdaterFixOf(card(zh,{source:{k:'cmd',forward:{}}}),'0.30.65'),false);
 assert.equal(oldUpdaterFixOf(card(zh,{source:{k:'cmd',quote:{id:1}}}),'0.30.65'),false);
 assert.equal(oldUpdaterFixOf(card(zh,{end:'open'}),'0.30.65'),false);
});
const dict=(f)=>JSON.parse(readFileSync(new URL('../i18n/'+f,import.meta.url),'utf8'));
test('the line is the one verified on signed 0.30.60 and 0.30.65; the words promise nothing',()=>{
 assert.equal(OLD_UPDATER_FIX,'( umask 077; chmod -R go-w ~/.local/state/agentj/install-artifacts/dependencies; agentj update apply --yes )');
 const zh=dict('web.zh.json')['p0371.oldfix.why'],en=dict('web.en.json')['p0371.oldfix.why'];
 // r3 ①: plain words, nothing that reads like tampering; not every failure with the host's words is this fault, so
 // the words must not promise, and must say what to do if it fails again
 assert.match(zh,/多半是这个旧版本升级程序的一个已知问题/);assert.match(zh,/通常就能升级/);assert.match(zh,/仍失败就不要重复/);assert.match(zh,/联系客服/);
 assert.doesNotMatch(zh,/(?<!Agent J )主机/);assert.doesNotMatch(zh+en,/改动|替换|篡改|tamper|replaced|changed or/);
 assert.match(en,/most likely/);assert.match(en,/usually updates it/);assert.match(en,/do not run it again/);assert.match(en,/contact support via the repair guide/);
 // what the host's own sentence said that is still true: the version it stays on (the connection's own), working, pairing kept
 for(const x of [zh,en]){assert.match(x,/\{v\}/);assert.equal(x.split('{v}').length,2);}
 assert.match(zh,/照常可用，配对和设置都不变/);assert.match(en,/still runs \{v\} as before, pairing and settings unchanged/);
 // r4 A (security review): what the line does, in the card's own words: the cache folder, update + restart now, tasks cut
 assert.match(zh,/依赖缓存目录改成仅本人可写/);assert.match(zh,/立即升级并重启 Agent J/);assert.match(zh,/正在进行的任务会被打断/);
 assert.match(en,/dependency cache writable only by you/);assert.match(en,/updates and restarts Agent J at once/);assert.match(en,/interrupting any task in progress/);
 // real Ubuntu, inside the service cgroup: the line is cut before UPGRADE_RESULT prints, the upgrade is done
 assert.match(zh,/网页终端.*断开.*agentj --version/);assert.match(en,/web terminal disconnects at the end.*agentj --version/);
 // r3 ②: one paragraph, r4: four sentences
 assert.equal(zh.split('。').filter(Boolean).length,4);assert.equal(en.split(/\.(?:\s|$)/).filter(Boolean).length,4);assert.doesNotMatch(zh+en,/\n/);
 // r4 A: the run button's label says it updates too
 assert.match(dict('web.zh.json')['p0371.oldfix.run'],/执行并升级/);assert.match(dict('web.en.json')['p0371.oldfix.run'],/Run and update/);
 for(const k of ['p0371.oldfix.why','p0371.oldfix.raw','p0371.oldfix.cut','p0371.oldfix.run']){
  for(const f of ['web.zh.src.json','web.zh.override.json'])assert.equal(dict(f)[k],dict('web.zh.json')[k],f+' '+k);
  assert.ok(dict('web.en.json')[k],k);assert.doesNotMatch(dict('web.zh.json')[k]+dict('web.en.json')[k],/UPGRADE_RESULT/);
 }
 assert.match(dict('web.zh.json')['p0371.oldfix.cut'],/如果.*正常/);assert.doesNotMatch(dict('web.zh.json')['p0371.oldfix.cut'],/成功|已升级/);
});
// r3: the card's own DOM (a tiny document stand-in: tag, class, dataset, children, textContent)
function fakeDocument(){
 const node=(tag)=>({tag,tagName:tag.toUpperCase(),nodeType:1,className:'',dataset:{},attrs:{},children:[],text:'',listeners:{},classList:{add(){},contains:()=>false},
  getAttribute(k){return this.attrs[k]??null;},appendChild(n){this.children.push(n);return n;},
  set textContent(v){this.text=String(v);},get textContent(){return this.text+this.children.map(c=>c.textContent).join('');},
  setAttribute(k,v){this.attrs[k]=v;},append(...n){this.children.push(...n);},addEventListener(k,f){this.listeners[k]=f;},
  querySelector(sel){const want=sel==='button'?n=>n.tag==='button':n=>n.className.split(' ').includes(sel.slice(1));const walk=n=>{for(const c of n.children){if(want(c))return c;const d=walk(c);if(d)return d;}return null;};return walk(this);}});
 return {createElement:node,createElementNS:(_,tag)=>node(tag)};
}
const all=(n,f,out=[])=>{if(f(n))out.push(n);for(const c of n.children)all(c,f,out);return out;};
test('r3 the card: plain words, the fixed line, the host text folded whole, no reconnect button',async()=>{
 globalThis.document=fakeDocument();
 const {paintRepair,oldUpdaterWords}=await import('../public/js/repair-actions.js');
 const page=card(zh,{id:7});
 const words=oldUpdaterWords(page,'0.30.65');
 assert.equal(typeof words,'string');assert.doesNotMatch(words,/改动或替换|changed or was replaced/);
 assert.equal(oldUpdaterWords(card(zh,{id:9}),'0.30.69'),null);assert.equal(oldUpdaterWords(card('升级未完成：依赖包下载失败。',{id:10}),'0.30.65'),null);
 for(const usable of [false,true]){
  const box=document.createElement('div'),calls=[];let ran=0;
  assert.equal(paintRepair(box,page,{ready:()=>true,supported:()=>true,run:()=>{ran++;},hostVersion:()=>'0.30.65',
   runLine:(...args)=>{calls.push(args);return usable;}}),true);
  const fix=box.children[0],raw=all(box,n=>n.dataset.oldUpdaterRaw==='1');
  assert.equal(fix.dataset.oldUpdaterFix,'1');assert.equal(fix.dataset.run,usable?'1':'0');
  // the run button is the terminal module's alone, offered the constant and nothing from the page
  // r4 B: the card hands over the block and the page, never a command
  assert.equal(calls.length,1);assert.equal(calls[0].length,2);assert.equal(calls[0][1],page);assert.equal(calls[0][0].className,'codeblock');assert.ok(!calls[0].some(x=>typeof x==='string'));
  const code=all(fix,n=>n.tag==='code');assert.equal(code.length,1);assert.equal(code[0].textContent,OLD_UPDATER_FIX);
  assert.equal(all(fix,n=>n.tag==='pre')[0].dataset.lang,'sh');
  // the original message is still there, whole and unchanged, in a fold (never deleted)
  assert.equal(raw.length,1);assert.equal(raw[0].tag,'details');assert.equal(raw[0].children[0].tag,'summary');assert.equal(raw[0].children[1].text,zh);
  // one guide link; no 「修复连接」, nothing on the card calls ctx.run
  assert.equal(all(box,n=>n.tag==='a').length,1);assert.equal(all(box,n=>n.dataset.repair==='reconnect').length,0);
  for(const n of all(box,n=>n.listeners.click))n.listeners.click({preventDefault(){},stopPropagation(){}});
  assert.equal(ran,0);
 }
 // a fixed host keeps its own card: 「修复连接」 and no fixed line, runLine never asked
 const fresh=card(zh,{id:8}),box=document.createElement('div');let asked=0;
 paintRepair(box,fresh,{ready:()=>true,supported:()=>true,run:()=>{},hostVersion:()=>'0.30.69',runLine:()=>{asked++;return true;}});
 assert.equal(asked,0);assert.equal(all(box,n=>n.dataset.oldUpdaterFix==='1').length,0);assert.equal(all(box,n=>n.dataset.repair==='reconnect').length,1);
 assert.equal(oldUpdaterWords(fresh,'0.30.69'),null);
 // r4 D: page 7 was drawn for 0.30.65; after the update the same page keeps the plain words (that version) and the fold,
 // never back to the alarming sentence; no 「执行」 is offered on a host that is no longer in the range
 const after=oldUpdaterWords(page,'0.30.69');
 assert.equal(after,words);assert.match(after,/0\.30\.65/);
 const box2=document.createElement('div');let asked2=0;
 paintRepair(box2,page,{ready:()=>true,supported:()=>true,run:()=>{},hostVersion:()=>'0.30.69',runLine:()=>{asked2++;return true;}});
 assert.equal(asked2,0);assert.equal(all(box2,n=>n.dataset.oldUpdaterFix==='1').length,1);assert.equal(all(box2,n=>n.dataset.oldUpdaterRaw==='1')[0].children[1].text,zh);
 assert.equal(all(box2,n=>n.dataset.repair==='reconnect').length,0);
 // a page whose marks no longer hold is never kept by memory alone
 assert.equal(oldUpdaterWords({...page,source:{k:'cmd',forward:{}}},'0.30.69'),null);
});
const src=(f)=>readFileSync(new URL('../public/'+f,import.meta.url),'utf8');
test('r4 「执行」: one constant, one switch (off), one caller, only the web terminal',async()=>{
 const line=await import('../public/js/old-updater-line.js');
 assert.equal(line.OLD_UPDATER_FIX,OLD_UPDATER_FIX);assert.equal(line.OLD_FIX_RUN,false);   // off until a real 0.30.65 host + a real phone ran it
 assert.deepEqual(Object.keys(line).sort(),['OLD_FIX_RUN','OLD_UPDATER_FIX']);
 const lineSrc=src('js/old-updater-line.js');
 assert.match(lineSrc,/^export const OLD_FIX_RUN = false;$/m);assert.doesNotMatch(lineSrc,/\blet\b|\bvar\b|function|=>|import/);
 const tc=src('js/termcommands.js');
 const f=tc.slice(tc.indexOf('export function fixedLine('),tc.indexOf('/** One tap = one run'));
 // B: no command parameter: what runs is the module constant, whoever calls
 assert.match(tc,/import \{OLD_UPDATER_FIX, OLD_FIX_RUN\} from '\.\/old-updater-line\.js';/);
 assert.match(f,/^export function fixedLine\(wrap, page, \{ redraw = \(\) => \{\} \} = \{\}\) \{/);
 assert.doesNotMatch(f,/\bcmd\b/);
 assert.match(f,/if \(!OLD_FIX_RUN \|\| /);                                   // C: the switch comes first
 assert.match(f,/!termUsable\(\)/);
 assert.match(f,/go\(key, OLD_UPDATER_FIX, 'term', null, page\)/);          // the 「!」 path with run: the host's own signed confirm decides
 assert.doesNotMatch(f,/'agent'|'named'|H\.say|cmdExec|sendApp|--danger/);   // never the Agent, never a chat message, no new frame
 assert.match(f,/t\('p0371\.oldfix\.run'\)/);                                // A: the label says 「执行并升级」
 // D: the "expected at the last step" words only after the signed resend went out
 assert.match(tc,/r\.sent === true && r\.st === 'fail' && !r\.out && \['closed', 'offline'\]\.includes\(r\.why\)/);
 // one caller in the whole page: relay.js hands over the block, the page and a redraw: nothing that could be a command
 const callers=[];
 const walk=(d)=>{for(const e of readdirSync(new URL('../public/'+d,import.meta.url),{withFileTypes:true})){const p=d+e.name;if(e.isDirectory()){if(e.name!=='vendor')walk(p+'/');}else if(/\.(m?js|html)$/.test(e.name)){const n=(src(p).match(/\bfixedLine\s*\(/g)||[]).length;if(n)callers.push(p+':'+n);}}};
 walk('');
 assert.deepEqual(callers.sort(),['js/relay.js:1','js/termcommands.js:1']);   // the definition and the one call
 assert.equal((src('js/relay.js').match(/\bfixedLine\b/g)||[]).length,2);   // relay: the import and that call, no alias
 const rl=src('js/relay.js');
 assert.match(rl,/runLine:\(block,page\)=>fixedLine\(block,page,\{redraw:/);
 const ra=src('js/repair-actions.js');
 const paint=ra.slice(ra.indexOf('function paintOldUpdaterFix('),ra.indexOf('export function paintRepair('));
 assert.match(paint,/ctx\.runLine\?\.\(block,page\)===true/);assert.doesNotMatch(paint,/ctx\.run\(|addEventListener|sendApp|innerHTML/);
 assert.match(paint,/mk\('p','',page\.reply\)/);
 assert.match(ra,/import \{OLD_UPDATER_FIX\} from '\.\/old-updater-line\.js';/);assert.equal((ra.match(/umask 077/g)||[]).length,0);
});
test('r4 D: reading aloud and the reader use the words on screen',()=>{
 const rl=src('js/relay.js');
 assert.match(rl,/speaker\.tap\(p\.id, shownReply\(p\)\)/);assert.match(rl,/const p = currentPage\(\), tx = shownReply\(p\);/);assert.match(rl,/const tx = shownReply\(p\);\n  rdAt = /);
 assert.doesNotMatch(rl.slice(rl.indexOf('function openReader(){'),rl.indexOf('let rdPaging')),/p\.reply/);
 assert.doesNotMatch(rl.slice(rl.indexOf('function rdSync(){'),rl.indexOf('function rdSync(){')+700),/p\.reply\.trim\(\)/);
 assert.match(rl,/function copyReply\(\)\{ copyOut\(currentPage\(\)\.reply/);   // copy stays the host's own text (what support needs)
});
test('r4 C: the phone gate is registered',()=>{
 const g=readFileSync(new URL('../../../.jarvis/p44-gates.py',import.meta.url),'utf8');
 assert.match(g,/^    'p0371-phone': \(\[str\(node\), 'agentjarvis\/web\/test\/p0371-phone\.mjs'\], root\),/m);
});
