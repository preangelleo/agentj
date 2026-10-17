// P302: the second menu-page hotfix (owner 2026-10-15 09:0x, Mac Chrome) at 390 × 844 (phone) and 1440 × 900 (desktop),
// system dark / light — the spacing audit (text never touches a divider, buttons keep their gaps), the header that stays
// on top while a page scrolls (back + title + reload), the skills tabs kept under it, Esc = back on every page / layer,
// readable labels on the light workflow cards, the five-card menu on the Noir canvas with a Lama 「Agent J」 card, and
// the @ list in the slash list's dark form with a light mention token in the composer.
// `node test/p302.mjs before` = shots only (the shipped 0.21.1a1 page); no tag = shots + checks.
// Own Chromium (never :9222), the real client against fakehost.mjs.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitFor,waitState,shoot,sleep,key,swipe} from './browser.mjs';
const tag=process.argv[2]||'after', CHECK=tag==='after';
const root=fileURLToPath(new URL('../../../',import.meta.url));const out=root+`reports/qa/p302/${tag}/`;mkdirSync(out,{recursive:true});
const fx=JSON.parse(readFileSync(root+'host/tests/fixtures/p244_wf.json','utf8'));
// enough companies that the workflows page scrolls on a 1440 × 900 desktop too
const head=structuredClone(fx.wf_list[0]);const more=head.items.filter(w=>w.wdb).map(w=>({...structuredClone(w),folder:w.folder+'-2',company:(w.company||w.folder)+' 二部'}));
head.items=[...head.items,...more,...more.map(w=>({...w,folder:w.folder+'b',company:w.company+'b'}))];
const list=[head,...fx.wf_list.slice(1)];
const DOC='# 长文档\n\n'+Array.from({length:40},(_,i)=>`第 ${i+1} 段：正文一段，用来把页面撑长。`).join('\n\n')+'\n';
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();let checks=0;const results=[];
const ev=(p,s)=>evaluate(p,s),click=(p,s)=>ev(p,s==='#orb'?`(()=>{const e=document.getElementById('orb');e.dispatchEvent(new PointerEvent('pointerdown',{button:0}));e.dispatchEvent(new PointerEvent('pointerup',{button:0}));})()`:`document.querySelector(${JSON.stringify(s)}).click()`);
const cev=(p,s)=>CHECK?evaluate(p,s):null;
const check=(v,label)=>{if(!CHECK)return;assert.ok(v,label);checks++;results.push(label);};
// WCAG contrast of an element's text against the first opaque background up its tree
const CONTRAST=`(e)=>{if(typeof e==='string')e=document.querySelector(e);if(!e)return 0;const rgb=s=>{const m=s.match(/[\\d.]+/g).map(Number),k=s.startsWith('color(')?255:1;return {c:m.slice(0,3).map(v=>v*k),a:m[3]??1};};
 const lum=c=>{const [r,g,b]=c.map(v=>{v/=255;return v<=.03928?v/12.92:((v+.055)/1.055)**2.4});return .2126*r+.7152*g+.0722*b;};
 let n=e,stack=[];while(n&&n.nodeType===1){const b=rgb(getComputedStyle(n).backgroundColor);if(b.a>0){stack.push(b);if(b.a>=1)break;}n=n.parentElement;}
 let base=stack.length&&stack.at(-1).a>=1?stack.pop().c:[255,255,255];while(stack.length){const b=stack.pop();base=base.map((v,i)=>v*(1-b.a)+b.c[i]*b.a);}
 const f=rgb(getComputedStyle(e).color),fc=f.c.map((v,i)=>v*f.a+base[i]*(1-f.a));const a=lum(fc),b=lum(base);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);}`;
// the spacing audit (owner: 「文字和下边的虚线之间没有 margin……行间距、文字和线条的距离、按钮上下之间、左右之间」):
// ① text inside a box with a drawn top / bottom border keeps ≥ MIN px from that line; ② a line drawn on a box's top keeps
// ≥ MIN px from the text of the box right above; ③ sibling buttons never overlap and keep ≥ MIN px between them.
const SPACING=`(sel,MIN=8)=>{const bad=[],seen=new Set();const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&!e.closest('[hidden]')&&getComputedStyle(e).visibility!=='hidden';};
 const name=e=>(e.id?'#'+e.id:e.tagName.toLowerCase()+(e.className&&typeof e.className==='string'?'.'+e.className.trim().split(/\\s+/).join('.'):'')+(e.id?'':'「'+e.textContent.trim().slice(0,12)+'」'));
 const texts=e=>{const out=[],w=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);while(w.nextNode()){const t=w.currentNode;if(!t.textContent.trim()||!vis(t.parentElement))continue;const r=document.createRange();r.selectNodeContents(t);for(const q of r.getClientRects())if(q.width>0&&q.height>0)out.push(q);}return out;};
 const line=(e,side)=>{const s=getComputedStyle(e),w=parseFloat(s['border'+side+'Width']);return w>0&&s['border'+side+'Style']!=='none'&&!/rgba\\(\\d+, \\d+, \\d+, 0\\)/.test(s['border'+side+'Color']);};
 for(const box of document.querySelectorAll(sel))for(const e of [box,...box.querySelectorAll('*')]){if(!vis(e)||/^(BUTTON|INPUT|TEXTAREA|SELECT|CODE|KBD|SUMMARY|IMG|svg|path)$/i.test(e.tagName)||e.closest('button,code,pre,.fp-robot'))continue;
  const r=e.getBoundingClientRect();if(r.height<30)continue;const s=getComputedStyle(e);
  if(line(e,'Bottom')){const edge=r.bottom-parseFloat(s.borderBottomWidth);for(const q of texts(e)){const d=edge-q.bottom;if(d<MIN&&q.bottom<=r.bottom+1){const k=name(e)+' bottom';if(!seen.has(k)){seen.add(k);bad.push(k+' '+d.toFixed(1)+'px');}}}}
  if(line(e,'Top')){const edge=r.top+parseFloat(s.borderTopWidth);for(const q of texts(e)){const d=q.top-edge;if(d<MIN&&q.top>=r.top-1){const k=name(e)+' top';if(!seen.has(k)){seen.add(k);bad.push(k+' '+d.toFixed(1)+'px');}}}
   const prev=e.previousElementSibling;if(prev&&vis(prev))for(const q of texts(prev)){const d=r.top-q.bottom;if(d<MIN&&q.left<r.right&&q.right>r.left){const k=name(prev)+' → '+name(e)+' line';if(!seen.has(k)){seen.add(k);bad.push(k+' '+d.toFixed(1)+'px');}}}}
 }
 for(const box of document.querySelectorAll(sel)){const groups=new Map();for(const b of box.querySelectorAll('button,.aj-btn,[role=button]')){if(!vis(b)||getComputedStyle(b).position==='absolute'||getComputedStyle(b).position==='fixed')continue;const p=b.parentElement;if(!groups.has(p))groups.set(p,[]);groups.get(p).push(b);}
  for(const bs of groups.values())for(let i=0;i<bs.length;i++)for(let j=i+1;j<bs.length;j++){const a=bs[i].getBoundingClientRect(),c=bs[j].getBoundingClientRect();
   const vo=Math.min(a.bottom,c.bottom)-Math.max(a.top,c.top),ho=Math.min(a.right,c.right)-Math.max(a.left,c.left);
   const gap=vo>4?Math.max(c.left-a.right,a.left-c.right):ho>4?Math.max(c.top-a.bottom,a.top-c.bottom):99;
   // a list's full-width rows (Settings rows, divider-separated) and a label · switch · label pair are one control, not two buttons
   const pw=bs[i].parentElement.getBoundingClientRect().width,row=a.width>=pw*.8&&c.width>=pw*.8&&ho>4;
   if(gap<MIN&&!row&&!(bs[i].closest('.busy-list,.twoway,.segrow,.seg,[role=tablist]')||bs[i].matches('.fp-mention,.fp-file,.fp-card-head,.fp-link,#sug button,.slashmenu button'))){const k=name(bs[i])+' ↔ '+name(bs[j]);if(!seen.has(k)){seen.add(k);bad.push(k+' gap '+gap.toFixed(1)+'px');}}}}
 return bad;}`;
// the element under a point is the control itself (not covered by the scrolled content)
const ONTOP=`(sel)=>{const e=document.querySelector(sel);if(!e)return false;const r=e.getBoundingClientRect();if(r.bottom<=0||r.top<0)return false;const x=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return !!x&&(e===x||e.contains(x));}`;
const VIEWS=[[390,844,'dark'],[390,844,'light'],[1440,900,'dark'],[1440,900,'light']];
const audit={};
try{
 for(const [w,h,scheme] of VIEWS){const name=`${w}-${scheme}`;
  fake.reset();fake.st.caps=['wf1'];fake.st.prefs={appearance:{language:'zh',theme:'system'},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
  fake.st.onApp=async(c,m,send)=>{if(m.t==='wf_list'){if(m.etag){await send({t:'wf_same',r:m.r});return true;}for(const x of list)await send({...x,r:m.r});return true;}
   if(m.t==='wf_doc'){await send({t:'wf_doc',ok:true,r:m.r,folder:m.folder,name:m.name,title:m.name,text:DOC});return true;}
   if(m.t==='skill_get'){await send({...fx.skill_get,r:m.r,text:DOC});return true;}return false;};
  const p=await newPage(B,w,h,scheme,{allow:[web.url,fake.relay+'/'],touch:w<500});await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
  await ev(p,`document.getElementById('phone-env-dismiss')?.click()`);await sleep(250);
  const snap=async file=>writeFileSync(out+`${file}-${name}.png`,await shoot(p,''));
  const esc=async()=>{await key(p,'Escape');await sleep(250);};
  const spacing=async(sel,label)=>{const bad=await ev(p,`(${SPACING})(${JSON.stringify(sel)})`);audit[label+' '+name]=bad;check(!bad.length,'spacing '+label+' '+name+' '+bad.join(' · '));};
  const open=async page=>{if(await ev(p,`document.getElementById('five-menu').hidden`)){await click(p,'#orb');await waitFor(p,`!document.getElementById('five-menu').hidden`);}await click(p,`.fp-menu-card[data-page="${page}"]`);await sleep(300);};
  const scroller=`(document.querySelector('#five-page .fp-scroll')||document.getElementById('five-page'))`;
  // ---- 0 the five-card menu: Noir from the moment it opens; the fifth card Lama with Noir text ----
  await click(p,'#orb');await waitFor(p,`!document.getElementById('five-menu').hidden`);await sleep(300);await snap('0-menu');
  check(await cev(p,`getComputedStyle(document.getElementById('five-menu')).backgroundColor==='rgb(44, 43, 38)'`),'menu canvas = Noir as it opens '+name);
  check(await cev(p,`getComputedStyle(document.querySelector('.top')).backgroundColor==='rgb(44, 43, 38)'`),'header = Noir with the menu open '+name);
  check(await cev(p,`document.querySelector('meta[name="theme-color"]').content==='#2c2b26'`),'OS bar = Noir with the menu open '+name);
  check(await cev(p,`(()=>{const c=document.querySelector('.fp-menu-card[data-page="agentj"]'),s=getComputedStyle(c);return s.backgroundColor==='rgb(219, 178, 147)'&&s.color==='rgb(44, 43, 38)';})()`),'「Agent J」 card = Lama #dbb293 + Noir text '+name);
  // the four Heavy cards keep P244's Lotion text (2.8–3.6:1, reported to Jarvis, not changed in a hotfix); the Lama card reads ≥ 4.5:1
  check(await cev(p,`(()=>{const c=document.querySelector('.fp-menu-card[data-page="agentj"]');return (${CONTRAST})(c.querySelector('h2'))>=4.5&&(${CONTRAST})(c.querySelector('p'))>=4.5;})()`),'「Agent J」 card text ≥ 4.5:1 '+name);
  await esc();check(await cev(p,`document.getElementById('five-menu').hidden`),'Esc closes the menu '+name);
  // ---- 1 workflows: expand a card, scroll to the end; back / title / reload stay on top; labels readable ----
  await open('workflows');await waitFor(p,`document.querySelectorAll('[data-folder]').length>=8`);
  await ev(p,`document.querySelector('[data-folder] .fp-card-head').click()`);await sleep(250);await snap('1-workflows-open');
  check(await cev(p,`[...document.querySelectorAll('#five-body .fp-card .fp-tab')].length===3&&[...document.querySelectorAll('#five-body .fp-card .fp-tab')].every(t=>(${CONTRAST})(t)>=4.5)`),'workflow card labels ≥ 4.5:1 on the light card '+name);
  check(await cev(p,`document.querySelector('#five-body .fp-card .fp-tab[aria-pressed="true"]')?.textContent.length>0`),'the open label is marked '+name);
  await spacing('#five-page','workflows');
  await ev(p,`${scroller}.scrollTop=1e6`);await sleep(250);await snap('1b-workflows-scrolled');
  check(await cev(p,`${scroller}.scrollTop>100`),'workflows page scrolls '+name);
  check(await cev(p,`(${ONTOP})('#five-page .fp-header .fp-back')&&(${ONTOP})('#five-page .fp-header .fp-back:last-child')&&(${ONTOP})('#five-title')`),'back + title + reload stay on top after scrolling '+name);
  await esc();check(await cev(p,`document.getElementById('five-page').hidden&&document.body.dataset.fivePage==='chat'`),'Esc leaves the workflows page '+name);
  // ---- 2 skills: the three tabs stay under the header while the list scrolls, and still switch ----
  await open('skills');await waitFor(p,`document.querySelectorAll('#five-body .fp-card').length>10`);await snap('2-skills');
  await ev(p,`${scroller}.scrollTop=1e6`);await sleep(250);await snap('2b-skills-scrolled');
  check(await cev(p,`[...document.querySelectorAll('#five-page .fp-tab')].length===3&&[...document.querySelectorAll('#five-page .fp-tab')].every(t=>(${ONTOP})('#five-page .fp-tab[data-k="'+t.dataset.k+'"]'))`),'all three skill tabs stay visible after scrolling '+name);
  await ev(p,`document.querySelector('#five-page .fp-tab[data-k="local"]')?.click()`);await sleep(200);
  check(await cev(p,`document.querySelector('#five-page .fp-tab[data-k="local"]').getAttribute('aria-pressed')==='true'&&(${ONTOP})('#five-page .fp-tab[data-k="all"]')`),'a tab switches from the scrolled list '+name);
  await ev(p,`document.querySelector('#five-page .fp-tab[data-k="all"]')?.click()`);await sleep(150);
  check(await cev(p,`(()=>{const g=[...document.querySelectorAll('#five-body .fp-group')],bar=document.querySelector('#five-page .fp-sticky').getBoundingClientRect();return g.every(x=>{const r=x.getBoundingClientRect();return r.top>=bar.bottom-1||r.bottom<=bar.top;});})()`),'the category label sits under the tabs, not over them '+name);
  await spacing('#five-page','skills');
  // ---- 3 skill detail: the back bar stays on top of a long page; Esc = one level ----
  await ev(p,`document.querySelector('#five-body .fp-card-head').click()`);await waitFor(p,`!document.getElementById('five-detail').hidden`);await sleep(300);
  await ev(p,`document.querySelector('#five-detail details.fp-raw')&&(document.querySelector('#five-detail details.fp-raw').open=true)`);await sleep(300);
  await ev(p,`document.getElementById('five-detail').scrollTop=1e6`);await sleep(200);await snap('3-skill-detail-scrolled');
  check(await cev(p,`document.getElementById('five-detail').scrollTop>100&&(${ONTOP})('#five-detail .fp-back')`),'detail back stays on top after scrolling '+name);
  await spacing('#five-detail','skill detail');
  await esc();check(await cev(p,`document.getElementById('five-detail').hidden&&!document.getElementById('five-page').hidden`),'Esc closes the detail, the page stays '+name);
  await esc();check(await cev(p,`document.getElementById('five-page').hidden`),'second Esc leaves the skills page '+name);
  // ---- 4 Agent J + a document, memory ----
  await open('agentj');await waitFor(p,`document.querySelectorAll('#five-body .fp-card').length>2`);await snap('4-agentj');await spacing('#five-page','agentj');
  await ev(p,`document.querySelectorAll('#five-body .fp-card-head')[1].click()`);await waitFor(p,`!document.getElementById('five-detail').hidden&&!!document.querySelector('#five-detail .fp-prose')`);await sleep(200);
  await ev(p,`document.getElementById('five-detail').scrollTop=1e6`);await sleep(200);await snap('4b-doc-scrolled');
  check(await cev(p,`(${ONTOP})('#five-detail .fp-back')`),'document back stays on top '+name);
  await esc();await esc();check(await cev(p,`document.getElementById('five-page').hidden`),'Esc Esc leaves document + Agent J page '+name);
  await open('memory');await sleep(200);await snap('5-memory');await spacing('#five-page','memory');
  await esc();check(await cev(p,`document.getElementById('five-page').hidden`),'Esc leaves the memory page '+name);
  // ---- 6 the robot card (Leo's first shot): the last line of a task keeps its distance from the dashed line ----
  await fake.send({t:'busy',by:'Agent J',tasks:[{id:'p290',who:'P290',kind:'temp',label:'second version integration',c:1,since:Math.floor(Date.now()/1000)}]});
  await waitFor(p,`!document.getElementById('busyDots').hidden`);await click(p,'#busyDots');await waitFor(p,`!document.getElementById('busyDlg').hidden`);await sleep(300);await snap('6-busy');
  await spacing('#busyDlg .sq','robot card');
  await esc();check(await cev(p,`document.getElementById('busyDlg').hidden`),'Esc closes the robot card '+name);
  await fake.send({t:'busy',by:'Agent J',tasks:[]});
  // ---- 7 the @ list and the slash list: one dark form; the mention token light like the composer text ----
  await ev(p,`(()=>{const i=document.getElementById('input');i.focus();i.value='现在 @';i.setSelectionRange(i.value.length,i.value.length);i.dispatchEvent(new Event('input'));})()`);await sleep(250);await snap('7-mentions');
  const mentionBg=await cev(p,`getComputedStyle(document.getElementById('five-mentions')).backgroundColor`);
  check(CHECK?await cev(p,`!document.getElementById('five-mentions').hidden`):true,'@ list opens '+name);
  check(CHECK?await cev(p,`[...document.querySelectorAll('#five-mentions .fp-mention')].every(b=>(${CONTRAST})(b)>=4.5)`):true,'@ list rows ≥ 4.5:1 '+name);
  await ev(p,`document.querySelector('#five-mentions .fp-mention')?.click()`);await sleep(250);await snap('7b-mention-token');
  check(await cev(p,`(()=>{const t=document.querySelector('#workflow-compose .workflow-token'),c=document.getElementById('workflow-compose');return !!t&&getComputedStyle(t).color===getComputedStyle(c).color;})()`),'mention token = the composer text colour '+name);
  check(await cev(p,`(()=>{const m=getComputedStyle(document.querySelector('#workflow-compose .workflow-token')).color.match(/\\d+/g).map(Number);return m[0]>200&&m[1]>200&&m[2]>200;})()`),'mention token in a light (Lotion) colour, not near-black '+name+' '+await cev(p,`getComputedStyle(document.querySelector('#workflow-compose .workflow-token')).color`));
  await ev(p,`(()=>{const i=document.getElementById('input');i.value='/';i.setSelectionRange(1,1);i.dispatchEvent(new Event('input'));})()`);await sleep(300);await snap('8-slash');
  const sugBg=await cev(p,`getComputedStyle(document.getElementById('sug')).backgroundColor`);
  check(!CHECK||(mentionBg===sugBg&&mentionBg!=='rgb(250, 249, 245)'),`@ list background = slash list background (${mentionBg} / ${sugBg}) `+name);
  await ev(p,`(()=>{const i=document.getElementById('input');i.value='';i.dispatchEvent(new Event('input'));})()`);
  await ev(p,`document.getElementById('workflow-compose')&&(document.getElementById('workflow-compose').textContent='')`);await esc();
  // ---- 10–16 (owner 09:26 追加) ----
  const IN=`document.getElementById('input')`;
  const setIn=async v=>{await ev(p,`(()=>{const i=${IN};i.focus();i.value=${JSON.stringify(v)};i.setSelectionRange(i.value.length,i.value.length);i.dispatchEvent(new Event('input',{bubbles:true}));})()`);await sleep(200);};
  // 11: 「/」 committed through an IME composition opens the list; a dismissed 「/」 opens again once the text changed
  await ev(p,`(()=>{const i=${IN};i.focus();i.value='';i.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));i.value='/';i.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));i.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'/'}));})()`);await sleep(300);
  check(await cev(p,`!document.getElementById('sug').hidden&&document.querySelectorAll('#sug button').length>0`),'「/」 from an IME composition opens the command list '+name);
  await esc();check(await cev(p,`document.getElementById('sug').hidden`),'Esc closes the command list '+name);
  await setIn('');await setIn('/');
  check(await cev(p,`!document.getElementById('sug').hidden`),'「/」 again after Esc + delete opens the list again '+name);
  await setIn('');
  // 12: a finished /command is a Peek-a-Blue chip with Noir text; the field still holds the plain text
  await setIn('/good-morning 早');await snap('12-command-chip');
  check(await cev(p,`(()=>{const c=document.querySelector('#workflow-compose .cmd-token'),s=c&&getComputedStyle(c);return !!c&&c.textContent==='/good-morning'&&s.backgroundColor==='rgb(199, 222, 220)'&&s.color==='rgb(44, 43, 38)';})()`),'/command chip = Peek-a-Blue + Noir '+name);
  check(await cev(p,`${IN}.value==='/good-morning 早'`),'the field keeps the plain /command text '+name);
  check(await cev(p,`(${CONTRAST})('#workflow-compose .cmd-token')>=4.5`),'/command chip text ≥ 4.5:1 '+name);
  await setIn('/good');check(await cev(p,`!document.querySelector('#workflow-compose:not([hidden]) .cmd-token')`),'an unfinished /command is not a chip yet '+name);
  await setIn('');await ev(p,`document.getElementById('workflow-compose')&&(document.getElementById('workflow-compose').textContent='')`);await ev(p,`${IN}.focus()`);
  // 13: with the keyboard up (a visual viewport scrolled 300 px down, 360 px tall) the @ list and the / list stay below its top
  await ev(p,`void Object.defineProperty(window,'visualViewport',{configurable:true,value:Object.assign(new EventTarget(),{offsetTop:300,offsetLeft:0,height:360,width:innerWidth,scale:1,pageTop:300})})`);
  await setIn('现在 @');
  check(await cev(p,`(()=>{const m=document.getElementById('five-mentions'),r=m.getBoundingClientRect(),c=document.getElementById('input').closest('.composer').getBoundingClientRect();return !m.hidden&&r.top>=300+8-0.5&&r.bottom<=c.top+0.5;})()`),'@ list stays inside the visible area above the composer (keyboard up) '+name+' '+await cev(p,`JSON.stringify([document.getElementById('five-mentions').getBoundingClientRect(),document.getElementById('input').closest('.composer')?.getBoundingClientRect().top])`));
  await setIn('');await setIn('/');
  check(await cev(p,`(()=>{const m=document.getElementById('sug'),r=m.getBoundingClientRect();return !m.hidden&&r.top>=300+6-0.5;})()`),'/ list stays inside the visible area (keyboard up) '+name+' '+await cev(p,`JSON.stringify(document.getElementById('sug').getBoundingClientRect())`));
  await setIn('');await ev(p,`void delete window.visualViewport`);await ev(p,`${IN}.blur()`);
  // 14: the menu corner carries the page's own total, not the card's position
  await open('workflows');await waitFor(p,`document.querySelectorAll('[data-folder]').length>=8`);await esc();
  await click(p,'#orb');await waitFor(p,`!document.getElementById('five-menu').hidden`);await sleep(250);await snap('14-menu-counts');
  check(await cev(p,`(()=>{const w=document.querySelector('.fp-menu-card[data-page="workflows"] .fp-current').textContent,k=document.querySelector('.fp-menu-card[data-page="skills"] .fp-current').textContent;return /[0-9]+ 家公司/.test(w)&&/[0-9]+ 个技能/.test(k)&&!/^[0-9]$/.test(document.querySelector('.fp-menu-card[data-page="memory"] .fp-current').textContent);})()`),'menu: 「N 家公司」 / 「N 个技能」, no bare position number '+name+' '+await cev(p,`[...document.querySelectorAll('.fp-menu-card .fp-current')].map(x=>x.textContent).join('｜')`));
  await esc();
  // 10: the 「coding · N 家公司」 line keeps its distance from the first card
  await open('workflows');await waitFor(p,`document.querySelectorAll('[data-folder]').length>=8`);
  check(await cev(p,`(()=>{const m=document.querySelector('#five-body>.fp-meta').getBoundingClientRect(),c=document.querySelector('#five-body>.fp-card').getBoundingClientRect();return c.top-m.bottom>=8;})()`),'meta line ↔ first card ≥ 8 px '+name);
  // 15: a card opens in place — its title does not move, the next card is pushed down; near the bottom only a minimal scroll
  const sc=`document.getElementById('five-page')`;
  await ev(p,`${sc}.scrollTop=400`);await sleep(150);
  const r15=await cev(p,`(()=>{const cards=[...document.querySelectorAll('#five-body .fp-card')],view=${sc}.getBoundingClientRect(),floor=document.querySelector('#five-page .fp-sticky').getBoundingClientRect().bottom;
    const c=cards.find(x=>x.getBoundingClientRect().top>floor+20),i=cards.indexOf(c),t0=c.getBoundingClientRect().top,n0=cards[i+1].getBoundingClientRect().top;c.querySelector('.fp-card-head').click();
    const t1=c.getBoundingClientRect().top,n1=cards[i+1].getBoundingClientRect().top;return {i,t0,t1,n0,n1,open:!!c.querySelector('.fp-expanded')};})()`);
  check(!CHECK||(r15.open&&Math.abs(r15.t1-r15.t0)<1&&r15.n1>r15.n0+20),'card opens in place: title still, next card pushed down '+name+' '+JSON.stringify(r15));
  const r15b=await cev(p,`(()=>{const cards=[...document.querySelectorAll('#five-body .fp-card')],view=${sc}.getBoundingClientRect(),floor=document.querySelector('#five-page .fp-sticky').getBoundingClientRect().bottom;
    const c=cards.filter(x=>{const r=x.getBoundingClientRect();return r.top>floor&&r.top<view.bottom-60;}).at(-1);const head=c.querySelector('.fp-card-head');head.click();
    const r=c.getBoundingClientRect(),h=head.getBoundingClientRect();return {open:!!c.querySelector('.fp-expanded'),others:document.querySelectorAll('#five-body .fp-card .fp-expanded').length,headTop:h.top,floor,bottom:r.bottom,view:view.bottom};})()`);
  check(!CHECK||(r15b.open&&r15b.others===1&&r15b.headTop>=r15b.floor-0.5&&r15b.headTop<r15b.view),'a card near the bottom: one open card, its title stays in view '+name+' '+JSON.stringify(r15b));
  check(!CHECK||(r15b.bottom<=r15b.view+0.5||r15b.headTop<=r15b.floor+12+0.5),'…and it scrolled only as far as needed (bottom in view, or title at the header) '+name);
  await snap('15-card-open-in-place');
  const r15c=await cev(p,`(()=>{const c=document.querySelector('#five-body .fp-card[data-open]'),t0=c.getBoundingClientRect().top;c.querySelector('.fp-card-head').click();return {shut:!c.dataset.open,t0,t1:c.getBoundingClientRect().top};})()`);
  check(!CHECK||(r15c.shut&&Math.abs(r15c.t1-r15c.t0)<1),'closing a card keeps its title still '+name);
  // 22: no robot wears its ground's colour — every robot on the page (cards, the open card's people) ≥ ΔE00 MIN_DE from its ground
  await ev(p,`(()=>{const c=[...document.querySelectorAll('#five-body .fp-card')].find(x=>x.querySelector('.fp-at'));c.querySelector('.fp-card-head').click();})()`);await sleep(100);
  const r22=await cev(p,`import('./js/robot-color.js').then(m=>{const bad=[];let n=0;for(const i of document.querySelectorAll('#five-body .fp-robot')){const g=m.groundOf(i);if(!g)continue;n++;const r=+(i.dataset.shown??i.dataset.r),d=m.deltaE(m.ROBOT_HEX[r],g.rgb);if(d<m.MIN_DE)bad.push(r+'@'+g.rgb+' '+d.toFixed(1));}return {n,bad};})`);
  check(!CHECK||(r22.n>5&&!r22.bad.length),'every robot stands out from its card (ΔE00) '+name+' '+JSON.stringify(r22));
  // 16: from the left edge to the right = back (touch only; the same step as Esc)
  if(w<500){
   await swipe(p,6,500,220,512);await sleep(300);
   check(await cev(p,`document.getElementById('five-page').hidden&&document.body.dataset.fivePage==='chat'`),'edge swipe leaves the workflows page '+name);
   await open('skills');await waitFor(p,`document.querySelectorAll('#five-body .fp-card').length>10`);
   await ev(p,`document.querySelector('#five-body .fp-card-head').click()`);await waitFor(p,`!document.getElementById('five-detail').hidden`);await sleep(250);
   await swipe(p,6,500,220,505);await sleep(300);
   check(await cev(p,`document.getElementById('five-detail').hidden&&!document.getElementById('five-page').hidden`),'edge swipe on a detail = one level back '+name);
   await swipe(p,200,500,380,505);await sleep(300);
   check(await cev(p,`!document.getElementById('five-page').hidden`),'a swipe that starts mid-screen does not go back '+name);
   await swipe(p,6,300,40,700);await sleep(300);
   check(await cev(p,`!document.getElementById('five-page').hidden`),'a vertical drag from the edge does not go back '+name);
   await swipe(p,6,500,220,505);await sleep(300);
   check(await cev(p,`document.getElementById('five-page').hidden`),'edge swipe leaves the skills page '+name);
   await swipe(p,6,500,220,505);await sleep(300);
   check(await cev(p,`document.body.dataset.fivePage==='chat'&&document.getElementById('five-menu').hidden`),'edge swipe on the chat does nothing '+name);
  }else await esc();
  // ---- 9 settings (the other menu pages): spacing, Esc ----
  await click(p,'#setBtn');await waitFor(p,`!document.getElementById('settings').hidden`);await sleep(300);await snap('9-settings');await spacing('#settings','settings');
  // 追加 25' (owner 10-2x): 「Agent 与遥控器」 = the Agent J host row first, 「AI 登录救援」 second; the row carries the new logo
  // P332 (Leo 10-16): the AI login entry moved into Settings → 紧急修复 (card ③, the second section); the host row stays first
  check(await cev(p,`(()=>{const s=document.getElementById('set-s-agent'),k=[...s.children].filter(e=>e.tagName!=='H3');const n=s.nextElementSibling;return k[0].classList.contains('setid')&&n&&n.id==='set-s-rescue'&&!!document.getElementById('set-auth-nav').closest('#rs-auth');})()`),'host row first, 紧急修复 second with the AI login entry '+name);
  check(await cev(p,`/brand\\/img\\/logo-mark\\.png/.test(document.querySelector('#set-s-agent .setid img').getAttribute('src'))`),'host row = the new logo '+name);
  // 追加 23: the AI rescue page on the spacing tokens
  await click(p,'#set-auth-nav');await waitFor(p,`!document.getElementById('set-auth').hidden`);await sleep(300);await snap('9b-auth');await spacing('#set-auth','auth rescue');
  await esc();check(await cev(p,`document.getElementById('set-auth').hidden&&!document.getElementById('settings').hidden`),'Esc leaves the AI rescue page for Settings '+name);
  if(name==='390-dark'){
   // 追加 26 (owner 10-20: 「这个 Toggle 点击就没有反应」): every Settings switch writes its key and shows the new state
   // P290 merge: Docker is greyed while isolation is off (isoScope), so it is flipped before the isolation switch
   const SW=['set-speak','set-wake','set-risk','set-goal','set-qswitch','set-docker','set-iso','set-hb','set-updates','set-mode'];
   for(const id of SW){
    const key=await ev(p,`document.getElementById('${id}').dataset.pref||''`),before=await ev(p,`document.getElementById('${id}').getAttribute('aria-checked')`),n0=fake.st.prefSets.length;
    await ev(p,`document.getElementById('${id}').scrollIntoView({block:'center'})`);await click(p,'#'+id);
    await waitFor(p,`document.getElementById('${id}').getAttribute('aria-checked')!==${JSON.stringify(before)}`).catch(()=>{});await sleep(400);
    if(await ev(p,`!document.getElementById('confirm').hidden`)){await click(p,'#confirm-yes');await sleep(400);}
    const after=await ev(p,`document.getElementById('${id}').getAttribute('aria-checked')`),sent=fake.st.prefSets.slice(n0);
    check(after!==before&&sent.length>0&&(!key||sent.some(m=>m.key===key)),`switch ${id} writes ${key||sent.map(m=>m.key).join('+')} and stays flipped (${before}→${after})`);
    if(key==='quota.auto_switch')check(fake.st.prefs.quota&&fake.st.prefs.quota.auto_switch===(after==='true'),'quota.auto_switch saved on the Agent J host');
   }
   // a refused save puts the switch back AND says why in a centred card (not only a line far up the page)
   fake.st.prefRefuse='busy';const q0=await ev(p,`document.getElementById('set-qswitch').getAttribute('aria-checked')`);
   await click(p,'#set-qswitch');await waitFor(p,`!document.getElementById('confirm').hidden`);await sleep(200);await snap('9c-switch-refused');
   check(await cev(p,`document.getElementById('confirm-no').hidden&&/没有改成|did not change/.test(document.getElementById('confirm').textContent)`),'refused switch → centred notice with one button');
   check(await cev(p,`document.getElementById('set-qswitch').getAttribute('aria-checked')===${JSON.stringify(q0)}`),'refused switch flips back');
   await click(p,'#confirm-yes');await sleep(200);check(await cev(p,`document.getElementById('confirm').hidden&&!document.getElementById('confirm-no').hidden`),'notice closes, the confirm card is whole again');
   fake.st.prefRefuse=null;
  }
  await esc();check(await cev(p,`document.getElementById('settings').hidden`),'Esc closes Settings '+name);
  if(w<500){await click(p,'#setBtn');await waitFor(p,`!document.getElementById('settings').hidden`);await sleep(300);await swipe(p,6,500,220,505);await sleep(350);
   check(await cev(p,`document.getElementById('settings').hidden`),'edge swipe closes Settings '+name);}
  check(!p.problems.length,'no console errors '+name+' '+p.problems.join(' | '));
  await p.dispose();
 }
 writeFileSync(out+'spacing-audit.json',JSON.stringify(audit,null,2));
 if(CHECK){console.log(`P302 PASS ${checks} checks`);writeFileSync(out+'browser.json',JSON.stringify({ok:true,checks,results},null,2));}
 else console.log('P302 shots',out);
}finally{await B.close();await fake.stop();await web.stop();}
