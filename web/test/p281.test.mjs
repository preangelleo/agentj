// P281 (owner, 2026-10-14, Mac web page on /switch-harness): 「◆?」 at the top of the reply + three dots, and no sign of what
// the switch was doing. U+FFFD (and a lone surrogate) is cleaned out of every page's text at ingestion (old stored pages too);
// every wait line in the reading area starts with the theme's dancing robot, a wait without words is the robot alone, and a
// running /switch-harness shows the host's own progress line. Real browsers: p281.mjs (390 / 1440, light / dark).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {dropReplacement, toPage} from '../public/js/snap.js';
const read=(p)=>readFileSync(new URL(p,import.meta.url),'utf8');
const src=read('../public/js/relay.js'), css=read('../public/app.css'), html=read('../public/index.html'), busy=read('../public/js/busy.js');
const zh=JSON.parse(read('../i18n/web.zh.json')).r;
const words=src.slice(src.indexOf('const NO_ECHO_MS'),src.indexOf('function fill('));

test('P281 U+FFFD: a lone block, the edges and lone surrogates go; inside a sentence it stays', ()=>{
  assert.equal(dropReplacement('�'),'');
  assert.equal(dropReplacement('�\n\n董事长，先用大白话说一下'),'董事长，先用大白话说一下');   // the owner's page, as stored
  assert.equal(dropReplacement('好的\n�\n继续'),'好的\n继续');
  assert.equal(dropReplacement('a�b'),'a�b');
  assert.equal(dropReplacement('\ud83d'),'');
  assert.equal(dropReplacement('ok\udc00'),'ok');
  assert.equal(dropReplacement('😀 中文 emoji'),'😀 中文 emoji');
  const p=toPage({id:7,ts:1,src:{k:'phone',text:'/switch-harness'},reply:{text:'�\n\n董事长'},end:'done'});
  assert.equal(p.reply,'董事长','every page is cleaned where it enters the page');
  assert.equal(toPage({id:8,src:{k:'phone'},reply:{text:'�'},end:'open'}).reply,'','a stray block alone is no reply');
});

test('P281 a split multi-byte character never reaches the page as U+FFFD (fatal decoder on whole frames)', ()=>{
  const bytes=new TextEncoder().encode('董事长😀，切换中');
  for(let cut=1;cut<bytes.length;cut++){
    const d=new TextDecoder('utf-8'); const s=d.decode(bytes.slice(0,cut),{stream:true})+d.decode(bytes.slice(cut));
    assert.equal(s,'董事长😀，切换中');
  }
  assert.match(read('../public/proto/wire.js'),/new TextDecoder\('utf-8', \{ fatal: true \}\)/);
});

function ctx(cur={status:'working',hostStatus:'working'}){
  const DICT={zh:{'r.replyWorking':zh.replyWorking,'r.ownerAddress':zh.ownerAddress,'r.replyPending':zh.replyPending,
    'r.replyStopped':zh.replyStopped}};
  const t=(k,v)=>String(DICT.zh[k]??k).replace(/\{(\w+)\}/g,(m,n)=>v&&n in v?String(v[n]):m);
  const painted=[], robots=[], c=vm.createContext({setTimeout:()=>0,clearTimeout(){},Date,historySync:false,shownKey:'1|x',wordsSrc:null,
    cur,t,el:()=>({dataset:{}}),fill:(_,x,plain)=>painted.push([x,plain]),afterWords:(r)=>robots.push(!!r),rdFollow(){}});
  vm.runInContext(words,c);
  return {c,painted,robots,run:(js)=>vm.runInContext(js,c)};
}

test('P281 the robot leads every wait line, never a finished reply or a dead end', ()=>{
  const {robots,painted,run}=ctx();
  run(`renderWords('',true)`); assert.equal(robots.at(-1),true,'working, no words yet: robot + 「…已经收到…」');
  assert.match(painted.at(-1)[0],/已经收到/);
  run(`renderWords('',true,'正在探测 Codex（2/3），每个最多 1 分钟…')`); assert.equal(robots.at(-1),true,'a progress / queue label');
  assert.equal(painted.at(-1)[0],'正在探测 Codex（2/3），每个最多 1 分钟…');
  run(`renderWords('正在写的回复',true)`); assert.equal(robots.at(-1),false,'streaming words: the robot sits under them (.thinking)');
  run(`renderWords('Agent J 主机正忙，消息已排队，忙完就接手。',true,'',true)`); assert.equal(robots.at(-1),true,'the host\'s own wait line');
  run(`renderWords('答完了',false)`); assert.equal(robots.at(-1),false);
  run(`cur={status:'idle',hostStatus:'stopped'}`); run(`renderWords('',true)`);
  assert.equal(robots.at(-1),false,'「已停下」 is no wait');
  run(`themeGen++; wordsSrc=null; cur={status:'working',hostStatus:'working'}`); run(`renderWords('',true)`);
  assert.equal(robots.at(-1),true,'a new theme redraws the same line with its robot');
});

test('P281 wiring: the dots are gone, the host lines are recognised, the theme redraws', ()=>{
  assert.match(html,/<div class="thinking" id="thinking" aria-hidden="true"><\/div>/,'no hard-coded dots');
  assert.match(src,/const hostWait = open && \(c\.wait === true \|\| c\.queued === true\) \|\| \(typeof c\.switch === "string" && p === ps\[ps\.length - 1\]\);/);
  assert.match(src,/paintThinking\(visibleReply\(p\) && !hostWait \? progress : ""\);/);
  assert.match(src,/onTheme\(\(\) => \{ themeGen\+\+; wordsSrc = null; thinkingSrc = null; showPage\(\); \}\);/);
  assert.match(src,/import \{ waitRobot \} from '\.\/busy\.js';/);
  assert.match(busy,/export function waitRobot\(\)/);
  assert.match(busy,/i\.style\.backgroundImage = `url\(\$\{sheet\(24\)\}\)`;/,'the theme\'s own sheet');
  assert.match(busy,/if \(dotsMode\(\)\) \{/,'the dots theme keeps its dots');
  assert.match(css,/body\[data-wait-line="1"\] \.thinking\{display:none\}/);
  assert.match(css,/body\[data-status=working\] \.thinking,body\[data-page-progress="1"\] \.thinking\{display:flex\}/);
  assert.match(css,/@keyframes wb-dance\{from\{background-position-x:0\}to\{background-position-x:calc\(-12\*var\(--wb\)\)\}\}/);
  assert.doesNotMatch(css,/\.thinking i\{/,'the three breathing dots are gone');
});
