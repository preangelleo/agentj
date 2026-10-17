import {focusDraft} from './composer-focus.js';
// P244: in-memory, read-only catalogue over the existing paired Noise session. DOM only; no HTML parser or persistence.
import {request, done, peer, isReady, ask1} from './session.js';
import {registerLayer} from './layers.js';
import {composeMentions} from './workflow-compose.js';
import {fitAbove} from './fit.js';
import {settleRobots} from './robot-color.js';
import {RelayMD} from './md.js';
import {t, lang, onLang} from './t.js';
import * as jc from './jcards.js';   // P360: Agent J Cards — deck cards 6+ and their pages
import * as PF from './pagefind.js';
export const mentionText = w => `@${w.folder}\uFF08CEO ${w.ceo || w.folder}\uFF09`;
export function mentionAt(text, at=text.length) {
 const s=text.slice(0,at), m=/(^|[\s\u3000，。！？；：、（(【\[\p{Script=Han}])@([^\s，。！？；：、（）()【】\[\]]*)$/u.exec(s);
 return m ? {start:s.length-m[2].length-1, query:m[2]} : null;
}
export function robotFor(folder, role, ceo=0){let h=2166136261;const text=`${folder}/${role}`;for(let i=0;i<text.length;i++){h^=text.charCodeAt(i);h=Math.imul(h,16777619);}const pool=Array.from({length:16},(_,i)=>i).filter(i=>i!==ceo&&i!==14);return pool[(h>>>0)%pool.length];}
let composer;
let H, pic=null, current='chat', busy=false, epoch=0, filter='all', selected=null, anchor=null, timer;
// P363 (#70): findQ = the page's own search (top-right magnifier, / , Ctrl+K); pinned = the workflow a link opened, first and open
let findQ='', pinned=null, pinScroll=false;
const $=id=>document.getElementById(id);
const names=()=>[['chat',t('p244.fivepages1'),'◌'],['workflows',t('p244.fivepages2'),'⌘'],['skills',t('p244.fivepages3'),'✧'],['memory',t('p244.fivepages4'),'◇'],['agentj','Agent J','△']];
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,fn,cls='fp-action'){const n=node('button',text,cls);n.type='button';n.onclick=fn;return n;}
function icon(key){const ns='http://www.w3.org/2000/svg',s=document.createElementNS(ns,'svg');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('class','fp-line-icon');s.setAttribute('aria-hidden','true');s.setAttribute('fill','none');s.setAttribute('stroke','currentColor');s.setAttribute('stroke-width','1.7');s.setAttribute('stroke-linecap','round');s.setAttribute('stroke-linejoin','round');
 const paths={chat:'M5 4h14v12H9l-4 4V4z',workflows:'M12 4v6M5 10h14M5 10v6M19 10v6M9 4h6M2 16h6v5H2zM16 16h6v5h-6z',skills:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',memory:'M6 4h12v16H6zM9 8h6M9 12h6M9 16h4',lock:'M7 10V7a5 5 0 0 1 10 0v3M5 10h14v11H5zM12 14v3',back:'M15 5l-7 7 7 7',reload:'M20 12a8 8 0 1 1-2.4-5.7M20 4v5h-5',doc:'M8.6 4.4H5.8a.8.8 0 0 0-.8.8v16a.8.8 0 0 0 .8.8h12.4a.8.8 0 0 0 .8-.8v-16a.8.8 0 0 0-.8-.8h-2.8M8.6 3.4h1.9a1.5 1.5 0 0 1 3 0h1.9v3.2H8.6zM7.9 10.3l2.6 2.6M10.5 10.3l-2.6 2.6M13.2 10.6h3.3M13.2 12.7h3.3M7.8 17.4l1.3 1.3 2.4-2.6M13.2 16.4h3.3M13.2 18.5h3.3',all:'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',global:'M12 2a10 10 0 1 0 0 20 10 10 0 1 0 0-20M2 12h20M12 2c-6 6-6 14 0 20M12 2c6 6 6 14 0 20',local:'M3 11l9-8 9 8M6 10v11h12V10M10 21v-7h4v7'};
 const path=document.createElementNS(ns,'path');path.setAttribute('d',paths[key]||paths.doc);s.append(path);return s;
}
function iconButton(key,label,fn){const b=button('',fn,'fp-back menuicon');b.setAttribute('aria-label',label);b.title=label;b.append(icon(key));return b;}
function robot(r,working=false){const n=node('span',undefined,'fp-robot');n.dataset.r=String(r||0);n.style.backgroundPositionY=`-${(r||0)*24}px`;if(working)n.classList.add('working');return n;}
export function clear(){composer?.clear();jc.clear();epoch++;clearTimeout(timer);pic=null;busy=false;selected=null;closeDetail();closeMenu();go('chat');}
export function refresh(){
 syncPlus();if(H&&isReady())jc.refresh();
 if(current==='card'){render();return;}
 if(!H||busy||!isReady())return;
 if(!peer.wf1){pic=null;if(current!=='chat')render();return;}
 busy=true;const e=epoch, items=[],skills=[],memory=[];
 const r=request({t:'wf_list',...(pic?{etag:pic.etag}:{})},m=>{
  if(e!==epoch)return;
  if(m.t==='wf_skills')skills.push(...(m.skills||[]));
  if(m.t==='wf_mem')memory.push(...(m.memory||[]));
  if(m.t==='wf_items'){items.push(...(m.items||[]));if(!m.more){pic={...m,items,skills:m.skills||skills,memory:m.memory||memory};finish();}}
  else if(m.t==='wf_same')finish();
  else if(m.t==='offline')finish(true);
 });
 function finish(failed=false){done(r);clearTimeout(timer);busy=false;if(failed)pic=null;if(current!=='chat')render();}
 if(!r){busy=false;return;} timer=setTimeout(()=>finish(true),15000);
}
export function go(page,folder,at){
 if(!H)return;if(page!==current||folder)dropFind();current=page;selected=folder||null;anchor=at||null;pinned=page==='workflows'&&folder||null;pinScroll=!!pinned;closeMenu();closeDetail();
 $('srchBtn')?.setAttribute('aria-label',t(page==='chat'?'r.find.btn':'p363.find'));
 $('five-page').hidden=page==='chat';document.body.dataset.fivePage=page;place();$('five-page').scrollTop=0;
 if(page==='chat'){H.chat();arm();return;}H.chat();render();if(page!=='card')refresh();arm();
}
export function toggleMenu(){if(!$('five-menu'))return;const e=$('five-menu');if(!e.hidden){closeMenu();return;}place();buildDeck(true);jc.refresh();}
// P360: rebuilt in place when the computer's card list changes while the menu is open (focus stays on the same card)
function buildDeck(focus){const e=$('five-menu'),was=document.activeElement?.closest?.('.fp-menu-card'),key=was&&(was.dataset.page||'card:'+was.dataset.card);
 e.replaceChildren();const deck=node('div',undefined,'fp-menu-deck');
 for(const [i,[key,title,icon]] of names().entries()){
  const b=button('',()=>go(key),'fp-menu-card fp-dark-'+i);b.dataset.page=key;
  const head=node('div',undefined,'fp-menu-head');
  if(key==='agentj'){const img=node('img');img.src='assets/five-robot.png';img.alt='';head.append(img);}else head.append(globalIcon(key));
  head.append(node('span',[current===key&&t('p244.fivepages5'),menuCount(key)].filter(Boolean).join(' · '),'fp-current'));
  b.append(head,node('h2',title),node('p',[t('p244.fivepages6'),t('p244.fivepages7'),t('p244.fivepages8'),t('p244.fivepages9'),t('p244.fivepages10')][i]));deck.append(b);
 }
 // P360: the owner's cards stack below the five (Apple Wallet: one fixed peek and card size, cards.css — the deck scrolls)
 const mine=jc.deck();mine.forEach((c,j)=>deck.append(jc.deckCard(c,5+j,()=>go('card',c.id))));deck.style.setProperty('--fp-n',String(5+mine.length));deck.dataset.n=String(5+mine.length);
 deck.onclick=ev=>{if(ev.target===deck)closeMenu();};e.append(deck);e.hidden=false;const back=key&&[...deck.children].find(b=>(b.dataset.page||'card:'+b.dataset.card)===key);if(focus||back)(back||deck.querySelector('button')).focus({preventScroll:!focus});arm();
}
// P302 (owner: 「右边看到工作流是 2……这个 2 代表什么呢？」): it was the card's position (1–5), read as a count. Now the corner
// carries the same total the page shows (「35 家公司」, 「120 个技能」) — or nothing until the computer answered
function menuCount(key){if(!pic)return '';if(key==='workflows')return `${pic.counts.wdb} ${t('p244.fivepages13')}`;if(key==='skills')return `${pic.skills.length} ${t('p302.skills')}`;return '';}
export function closeMenu(){if($('five-menu')){$('five-menu').hidden=true;arm();}}
const globalIcon=icon;
// P276: 「初始化 / 复查组织结构」 sends the main Agent one standard prompt (the light confirmation card of P267 is not out yet:
// sent at once, the note under the button says so); no workflow found → the three workflows that come with Agent J.
// After one tap (this page) or once the host sees a registered workflow (org.ready, every device), it reads 「复查」.
const STARTERS=['llm-wiki','script-article-writing','ebook-writing'];let orgUsed=false;
function say(text){go('chat');H.insert(text);H.send();}
// P308: the button also says how many companies have only a CEO (org.nostaff); such a card carries 「只有 CEO，还没有员工」.
function orgBar(){const review=orgUsed||!!pic.org?.ready,box=node('div',undefined,'fp-org'),n=pic.org?.nostaff|0,label=t(review?'p276.review':'p276.setup');
 const b=button(n?t('p308.count',{label,n}):label,()=>{orgUsed=true;say(t(review?'p276.reviewPrompt':'p276.setupPrompt'));},'fp-action fp-secondary');b.dataset.org=review?'review':'setup';
 box.append(b,node('p',t('p276.note'),'fp-meta'));return box;}
function starters(body){body.append(node('h3',t('p276.starterTitle'),'fp-group'));
 STARTERS.forEach((id,i)=>{const name=t(`p276.s${i+1}name`),c=card(name,t(`p276.s${i+1}desc`),i+3,()=>{});c.dataset.starter=id;c.querySelector('button').prepend(robot(i+1));
  c.append(node('small',t('p276.starterTag')),button(t('p276.install'),()=>say(t('p276.installPrompt',{id,name})),'fp-action fp-secondary'));body.append(c);});}
function card(title,note,i,fn){const c=node('article',undefined,'fp-card fp-tint-'+i%10);const head=button('',fn,'fp-card-head');if(current!=='workflows')head.append(globalIcon(current==='agentj'?'doc':current));head.append(node('span',title,'fp-card-title'));c.append(head);if(note)c.append(node('p',note));return c;}
function empty(text){$('five-body').append(node('p',text,'fp-empty'));}
let shown=0;
function render(){renderNow();settleRobots($('five-body'));
 // P363 (#70): the company a link opened stands right under the header, open — once (later renders keep the owner's scroll)
 if(pinScroll&&pic&&current==='workflows')pinTo(20);
 const b=$('five-find');if(b)PF.say(b,findQ.trim()&&pic?(shown?t('p363.cards',{n:shown}):t('p363.none')):'');}
// the list may still be growing (robots, the computer's fresh list): try for a few frames until the card is in place
function pinTo(n){requestAnimationFrame(()=>{const c=$('five-body').querySelector('.fp-card[data-open]'),page=$('five-page');if(!c||!pinScroll||page.hidden||current!=='workflows'){if(n>0&&pinScroll&&!page.hidden)pinTo(n-1);return;}
 const d=c.getBoundingClientRect().top-page.querySelector('.fp-sticky').getBoundingClientRect().bottom-12;page.scrollTop+=d;if(Math.abs(d)<2||n<=0)pinScroll=false;else pinTo(n-1);});}
// P363 (#70): the magnifier on a five page searches that page (its cards: names, CEO, folder, aliases, descriptions); on an
// open document it is the in-text find; on the card wall the five cards (the wall's header is P360's). → false = not ours.
const FIND_PH={workflows:'p363.findWf',skills:'p363.findSkills',memory:'p363.findMem',agentj:'p363.findAgentj'};
export function find(){
 if($('five-detail')&&!$('five-detail').hidden)return PF.openIn($('five-detail'),'p363.findDoc');
 if($('five-menu')&&!$('five-menu').hidden)return PF.openIn($('five-menu'),'p363.findCards');
 if(!$('five-page')||$('five-page').hidden||current==='chat')return false;
 const b=$('five-find');if(!b.hidden){closeFind();return true;}
 PF.label(b,FIND_PH[current]||'p363.findPage');b.hidden=false;const i=b.querySelector('input');i.focus();i.select();return true;}
// Esc / the magnifier again: the query goes and every card is back; the next Esc leaves the page (layers, rank 4 > page 3)
function closeFind(){const b=$('five-find');if(!b)return false;const was=!b.hidden||!!findQ;const q=findQ;dropFind();if(q&&current!=='chat')render();if(document.activeElement===b.querySelector('input'))$('srchBtn')?.focus();return was;}
function dropFind(){findQ='';const b=$('five-find');if(!b)return;b.hidden=true;b.querySelector('input').value='';PF.say(b,'');}
export const findOpen=()=>!!$('five-find')&&!$('five-find').hidden&&!$('five-page').hidden;
function renderNow(){shown=0;const body=$('five-body'),bar=$('five-bar');body.replaceChildren();bar.replaceChildren();bar.hidden=true;$('five-title').textContent=names().find(x=>x[0]===current)?.[1]||'';
 if(current==='card'){jc.render(body,selected,{title:x=>{$('five-title').textContent=x;},say,draft:x=>{go('chat');H.insert(x);},nav:openJlink,at:anchor});anchor=null;return;}   // P360
 if(!pic){empty(isReady()&&!peer.wf1?t('p244.fivepages11'):t('p244.fivepages12'));return;}
 if(current==='workflows'){
  body.append(node('p',`${pic.root_name} · ${pic.counts.wdb} ${t('p244.fivepages13')} · ${pic.counts.busy} ${t('p244.fivepages14')}`,'fp-meta'),orgBar());
  if(!pic.counts.wdb)starters(body);
  const ts=PF.terms(findQ),hit=(...xs)=>!ts.length||PF.matches(xs.filter(Boolean).join(' '),ts);
  const ws=pic.items.filter(w=>w.folder===pinned).concat(pic.items.filter(w=>w.folder!==pinned)).filter(w=>hit(w.company,w.folder,w.ceo,w.one,w.py,(w.agents||[]).map(a=>`${a.name} ${a.title||''}`).join(' ')));shown=ws.length;
  ws.forEach((w,i)=>{const c=card(w.company||w.folder,w.one||t('p244.fivepages15'),i,()=>toggleCard(c,w));c.dataset.folder=w.folder;if(w.wdb)c.dataset.lap=String(w.lap||0);
   c.querySelector('button').prepend(robot(w.wdb?w.robot:14,w.busy));c.append(node('small',w.ceo?`CEO ${w.ceo} · ${w.folder}`:w.folder));
   if(w.wdb)c.append(button('@',()=>insertMention(w), 'fp-at'));if(w.wdb&&w.reg===false)c.append(node('small',t('p276.unreg'),'fp-unreg'));if(w.wdb&&w.staff===0)c.append(node('small',t('p308.nostaff'),'fp-unreg fp-nostaff'));
   if(selected===w.folder)openCard(c,w);body.append(c);
  });if(!pic.items.length)empty(t('p244.fivepages19'));
 }else if(current==='skills'){
  // P302: the three tabs live in the sticky bar under the header, so they switch from anywhere in a long list
  const tabs=node('div',undefined,'fp-tabs');for(const [k,label] of [['all',t('p244.fivepages20')],['global',t('p244.fivepages21')],['local',t('p244.fivepages22')]]){const b=button(label,()=>{filter=k;render();$('five-page').scrollTop=0;},'fp-tab');b.dataset.k=k;const mark=globalIcon(k);mark.classList.add('fp-category-icon','fp-category-'+k);b.prepend(mark);b.setAttribute('aria-pressed',filter===k);tabs.append(b);}bar.append(tabs);bar.hidden=false;
  const ts=PF.terms(findQ),sk=pic.skills.filter(s=>filter==='all'||(filter==='global'?s.scope!=='local':s.scope==='local')).filter(s=>!ts.length||PF.matches(['/'+s.name,...(s.aliases||[]),s.desc,s.desc_zh,s.folder,skillLine(s)].filter(Boolean).join(' '),ts));shown=sk.length;
  let group='';sk.forEach((s,i)=>{const g=s.folder||t('p244.fivepages23');if(g!==group){body.append(node('h3',g,'fp-group'));group=g;}body.append(card('/'+s.name,skillLine(s),i,()=>skill(s)));});
 }else if(current==='agentj'){
  const ts=PF.terms(findQ),hit=(...xs)=>!ts.length||PF.matches(xs.filter(Boolean).join(' '),ts);shown=0;
  if(hit(t('p244.fivepages24'),t('p244.fivepages25'),'core')){shown++;body.append(card(t('p244.fivepages24'),t('p244.fivepages25'),1,()=>doc('@core','core',true)));
  const lock=icon('lock');lock.classList.add('fp-lock');body.lastChild.querySelector('.fp-line-icon')?.remove();body.lastChild.querySelector('button').prepend(lock);}
  (pic.agentj.docs||[]).filter(d=>hit(d.title,d.name)).forEach((d,i)=>{shown++;body.append(card(d.title||d.name,d.name,i+2,()=>doc('',d.name)));});if(pic.agentj.hidden)body.append(node('p',t('p244.fivepages26'),'fp-meta'));
 }else if(current==='memory'){
  const ts=PF.terms(findQ);shown=0;
  pic.memory.forEach((m,i)=>{const w=pic.items.find(w=>w.folder===m.owner),title=m.owner==='agent'?t('p244.fivepages27'):(w?.company||m.owner);
   if(ts.length&&!PF.matches([title,m.owner,w?.ceo,...m.files.map(f=>`${f.title||''} ${f.name}`)].filter(Boolean).join(' '),ts))return;shown++;const c=card(title,`${m.files.length} ${t('p244.fivepages28')}`,i,()=>{});
   for(const f of m.files){if(m.owner==='agent'&&f.src!=='root'){c.append(node('p',f.name));continue;}c.append(button(f.title||f.name,()=>doc(m.owner==='agent'?'':m.owner,f.name),'fp-file'));}if(m.owner==='agent')c.append(button(t('p244.fivepages29'),()=>{go('chat');H.memory();}));body.append(c);});
 }
}
function openCard(c,w){const tabs=node('div',undefined,'fp-tabs');for(const [k,label] of [['agents',t('p244.fivepages16')],['docs',t('p244.fivepages17')],['skills',t('p244.fivepages18')]]){const b=button(label,()=>expand(c,w,k),'fp-tab');b.dataset.k=k;tabs.append(b);}c.append(tabs,node('div',undefined,'fp-expanded'));c.dataset.open='1';expand(c,w,'agents');}
function shutCard(c){if(!c)return;for(const n of c.querySelectorAll(':scope>.fp-tabs,:scope>.fp-expanded'))n.remove();delete c.dataset.open;}
// P302 (owner: 「原地展开……向下展开，把下边的卡往下推」): a card opens where it is — its title does not move (the list is not
// rebuilt; whatever an open card above gives back is taken out of the scroll), the content pushes the cards below down, and
// only when the opened card runs past the bottom does the page scroll — by the least amount, never moving the title out
function toggleCard(c,w){const page=$('five-page'),top=c.getBoundingClientRect().top;
 const prev=selected&&[...$('five-body').querySelectorAll('.fp-card[data-open]')].find(n=>n!==c);shutCard(prev);
 if(selected===w.folder){selected=null;shutCard(c);}else{selected=w.folder;openCard(c,w);}
 page.scrollTop+=c.getBoundingClientRect().top-top;if(!selected)return;
 const r=c.getBoundingClientRect(),view=page.getBoundingClientRect(),floor=page.querySelector('.fp-sticky').getBoundingClientRect().bottom,gap=12;
 const over=r.bottom+gap-view.bottom,room=r.top-floor-gap;if(over>0&&room>0)page.scrollTop+=Math.min(over,room);}
function expand(c,w,tab){const box=c.querySelector('.fp-expanded');box.replaceChildren();for(const b of c.querySelectorAll('.fp-tabs .fp-tab'))b.setAttribute('aria-pressed',b.dataset.k===tab);if(tab==='docs')for(const d of w.docs)box.append(button(d.title||d.name,()=>doc(w.folder,d.name),'fp-file'));else if(tab==='skills')for(const id of w.skills){const s=pic.skills.find(s=>s.id===id);if(s)box.append(button('/'+s.name,()=>skill(s),'fp-file'));}else{
 const row=node('div',undefined,'fp-person');row.append(robot(w.robot,w.busy),node('strong',w.ceo||w.folder));box.append(row);
 for(const a of w.agents){const row=node('div',undefined,'fp-person');row.append(robot(robotFor(w.folder,a.name,w.robot)),node('span',(a.title||a.name)+(a.desc?' · '+a.desc:'')));row.dataset.agent=a.name;box.append(row);}}
 if(!box.childNodes.length)box.append(node('p',t('p244.fivepages30')));
 if(c.isConnected)settleRobots(c);
}
function closeDetail(){if($('five-detail')){$('five-detail').hidden=true;arm();}}
// P302 (owner: 「点击 ESC 也没办法退出」, 「与安卓返回手势一致」): Esc and the Android back gesture are the same step back —
// detail → page → chat. One history entry stands while any five surface is open; the back gesture pops it and closes the
// top surface (and re-arms if something is still open); closing from the page takes the entry back without a step.
let armed=false,armT=0;
const fiveOpen=()=>['five-detail','five-menu','five-page'].some(id=>$(id)&&!$(id).hidden);
// settled once per task: menu → page closes the menu before the page opens, which must not read as "all closed"
function arm(){clearTimeout(armT);armT=setTimeout(armNow,0);}
function armNow(){const on=fiveOpen();
 if(on&&!armed){armed=true;history.pushState({...(history.state||{}),ajFive:1},'');}
 else if(!on&&armed){armed=false;if(history.state?.ajFive)history.back();}}
function stepBack(){if(!$('five-detail').hidden)closeDetail();else if(!$('five-menu').hidden)closeMenu();else if(!$('five-page').hidden)go('chat');}
// P299: the page's top edge = the header's bottom edge (measured: the header height depends on the safe area and font size)
// P360 (Leo 10-17: 「卡的左边界和左上角 icon 的左边界垂直对齐」): the deck's left edge = the logo's left edge
function place(){const t=document.querySelector('.top'),o=$('orb'),r=document.documentElement.style;if(t)r.setProperty('--fp-top',Math.round(t.getBoundingClientRect().bottom)+'px');
 const x=o&&o.getClientRects().length?Math.round(o.getBoundingClientRect().left):0;if(x>0)r.setProperty('--fp-left',x+'px');}
// P360 (Leo 10-17: 「右上角这个加号……在所有的页面都可以保留……本质上在所有的页面上它都是一个 Skill」): the header's + (cards.css ·
// topfit.js TOP_ORDER) is on every page and names the page's adding skill — a card's page: the card's own `plus` (card.json;
// none → agentj-cards). It puts `/<skill> ` (a chip, workflow-compose.js) at the front of the message box and returns to the
// chat; nothing is sent — the owner says what to add, and the skill tells the Agent where.
export const PLUS={chat:'agentj-cards',cards:'agentj-cards',workflows:'agentj-capability',skills:'agentj-commands',memory:'agentj-add-memory',agentj:'agentj-add-doc'};
const SKILL_NAME=/^[a-z0-9][a-z0-9-]{1,63}$/;
export function plusSkill(){if($('five-menu')?.hidden===false)return PLUS.cards;if(current==='card'){const c=jc.cards().find(x=>x.id===selected);return c&&SKILL_NAME.test(c.plus||'')?c.plus:PLUS.cards;}return PLUS[current]||PLUS.chat;}
export function plusShown(){return document.body.dataset.view==='chat'&&jc.available()&&isReady();}
function syncPlus(){const b=$('cardPlus');if(!b)return;const on=plusShown();if(b.hidden===on)b.hidden=!on;const k=plusSkill();if(b.dataset.skill!==k||b.dataset.lang!==lang()){b.dataset.skill=k;b.dataset.lang=lang();b.title=t('p360.plusTitle',{cmd:'/'+k});b.setAttribute('aria-label',`${t('p360.plus')} /${k}`);}}
export function plusCard(){const cmd='/'+plusSkill()+' ',known=new Set([...Object.values(PLUS),...jc.cards().map(c=>c.plus)]);go('chat');const input=$('input');
 const m=/^[/\uFF0F]([a-z0-9][a-z0-9-]{1,63})(?:\s+|$)/.exec(input.value),rest=(m&&known.has(m[1])?input.value.slice(m[0].length):input.value).replace(/^\s+/,'');
 input.value=cmd+rest;input.setSelectionRange(cmd.length,cmd.length);input.dispatchEvent(new Event('input'));try{input.focus({preventScroll:true});}catch(_){}composer?.paint(true);}
// P360 (Leo 10-17: 「首先是定位某个卡片，其次是卡片上的某个位置」): one internal address form for everything the phone opens —
// skill/<name> · workflow/<folder> · doc/[<folder>/]<file>.md · card/<id>[#<anchor>] · page/<page> (host jcards.JLINK_RE).
// A card's links, `agentj card open` (§34 jcard_open) and card addresses / @mentions in messages all land here.
const SYS=['chat','workflows','skills','memory','agentj'];
const sysNames=()=>names().map(x=>x[1]);
export function openTarget(id,at){if(!H||typeof id!=='string')return;const a=typeof at==='string'&&jc.ANCHOR.test(at)?at:null;
 if(id==='cards'){go('chat');if($('five-menu').hidden)toggleMenu();return;}if(SYS.includes(id)){go(id);return;}if(jc.ID.test(id))go('card',id,a);}
// the computer's push (`agentj card open`): never while the owner is reading a confirmation, or is in Settings / another view
export function pushOpen(id,at){const c=$('confirm'),st=$('settings');if((c&&!c.hidden)||(st&&!st.hidden)||document.body.dataset.view!=='chat')return;openTarget(id,at);}
export function openJlink(s){s=String(s||'');if(!jc.JLINK.test(s))return;const i0=s.indexOf('/'),k=s.slice(0,i0),v=s.slice(i0+1);
 if(k==='page')return openTarget(v);if(k==='card'){const [id,at]=v.split('#');return openTarget(id,at);}if(k==='workflow')return go('workflows',v);
 if(k==='skill'){const sk=pic?.skills.find(x=>x.name===v||x.aliases?.includes(v));if(sk)skill(sk);else go('skills');return;}
 const i=v.lastIndexOf('/');if(/\.md$/.test(v))doc(i<0?'':v.slice(0,i),v.slice(i+1));}
// two cards with one name, written bare (`@记事本`): which one
function chooseCard(list){const box=sheet();box.append(node('h2',t('p360.choose'),'fp-detail-title'));for(const m of list){const b=button(m.label.slice(1),()=>openTarget(m.c.id),'fp-file jc-choose');b.dataset.card=m.c.id;b.prepend(jc.icon(m.c.icon,'fp-line-icon'));box.append(b);}}
// P299: the owner's language first — a Chinese owner sees the host's desc_zh, not the English trigger list
const shipped=(s,k)=>{if(s.scope!=='bundled')return '';const key=`p299.${k}.${s.name}`,v=t(key);return v===key?'':v;};
const skillLine=s=>(lang()==='zh'&&s.desc_zh)||shipped(s,'sk')||s.desc;
const bare=text=>String(text||'').replace(/^---\r?\n[\s\S]*?\r?\n(?:---|\.\.\.)\r?\n/,'');
function prose(m){const body=node('div',undefined,'fp-prose');if(!m.ok){body.append(node('p',t('p244.fivepages32')));return body;}
 body.append(RelayMD.toDOM(RelayMD.parse(bare(m.text)),document));linkify(body);if(m.cut)body.append(node('p',t('p244.fivepages33')));return body;}
function sheet(){const box=$('five-detail');box.replaceChildren();const bar=node('div',undefined,'fp-detail-bar');bar.append(iconButton('back',t('p299.back'),closeDetail));box.append(bar);box.scrollTop=0;box.hidden=false;arm();return box;}
async function detail(req,name,action){const e=epoch,box=sheet();box.append(node('p',name,'fp-file-name'));const wait=node('p',t('p244.fivepages31'),'fp-meta');box.append(wait);const m=await ask1(req,req.t,15000);if(e!==epoch||box.hidden)return;
 wait.replaceWith(prose(m));if(m.ok&&action)box.append(button(action.title,action.fn));else box.append(button(t('p244.fivepages34'),closeDetail));
}
function doc(folder,name,core=false){detail({t:'wf_doc',folder,name},name,core?null:{title:t('p244.fivepages35'),fn:()=>{go('chat');H.insert(`${t('p244.fivepages36')}${folder?folder+'/':''}documentation/${name}`);H.send();}});}
async function skill(s){const e=epoch,box=sheet(),zh=lang()==='zh';box.append(node('h2','/'+s.name+(s.aliases?.length?' · '+s.aliases.join(' / '):''),'fp-detail-title'));
 const sum=node('section',undefined,'fp-summary');sum.append(node('p',skillLine(s),'fp-lead'));
 const use=node('p',undefined,'fp-use');use.append(node('b',t('p299.use')),document.createTextNode((zh&&s.use_zh)||shipped(s,'su')||t('p299.useAny',{cmd:'/'+s.name})));sum.append(use);
 sum.append(node('p',t('p244.fivepages38')+(s.folder||(s.scope==='bundled'?t('p244.fivepages39'):t('p244.fivepages40'))),'fp-meta'),node('p',t('p244.fivepages41')+'/'+s.name+(s.aliases?.length?' · '+s.aliases.map(a=>'/'+a).join(' · '):''),'fp-meta'));
 box.append(sum,button(t('p244.fivepages37'),()=>{go('chat');H.insert('/'+s.name+' ');}));
 const raw=node('details',undefined,'fp-raw'),sm=node('summary',t('p299.raw'));const wait=node('p',t('p244.fivepages31'),'fp-meta');raw.append(sm,wait);box.append(raw);
 const m=await ask1({t:'skill_get',id:s.id},'skill_get',15000);if(e!==epoch||box.hidden)return;wait.replaceWith(prose(m));}
function insertCard(m,replace){go('chat');const input=$('input');if(replace){input.setRangeText(m.text+' ',replace.start,input.selectionStart,'end');input.dispatchEvent(new Event('input'));focusDraft(input);}else H.insert(m.text+' ');composer?.paint(true);}
function insertMention(w,replace){go('chat');const input=$('input');if(replace){input.setRangeText(mentionText(w)+' ',replace.start,input.selectionStart,'end');input.dispatchEvent(new Event('input'));focusDraft(input);}else H.insert(mentionText(w)+' ');composer?.paint(true);}
// cardsOnly (the owner's own input card, P360): only @cards and card addresses become links there
export function linkify(box,cardsOnly=false){const cards=jc.available()?jc.mentions(sysNames()):[];if((!pic||cardsOnly)&&!jc.available())return;const ws=pic&&!cardsOnly?pic.items.filter(w=>w.wdb):[],sk=pic&&!cardsOnly?pic.skills:[];const entries=[];
 for(const s of sk)for(const name of [s.name,...(s.aliases||[])])entries.push({text:'/'+name,type:'skill',fn:()=>skill(s)});
 for(const w of ws){for(const text of [mentionText(w),w.folder,w.company].filter(x=>x&&x.length>=3))entries.push({text,type:'workflow',fn:()=>go('workflows',w.folder)});if(w.ceo)entries.push({text:w.ceo,type:'person',fn:()=>go('workflows',w.folder)});for(const d of w.docs)entries.push({text:d.name,type:'doc',fn:()=>doc(w.folder,d.name)});}
 if(!cardsOnly)entries.push({text:'\u5DE5\u4F5C\u6D41',type:'workflow',fn:()=>go('workflows')},{text:'workflow',type:'workflow',fn:()=>go('workflows')},{text:'workflows',type:'workflow',fn:()=>go('workflows')},{text:t('p244.fivepages4'),type:'memory',fn:()=>go('memory')});
 // P360 @cards: `@名（card/id）` (what a mention writes) shows as `@名`; a bare `@名` opens the one card with that name (two: a chooser)
 const by=new Map();for(const m of cards){entries.push({text:m.text,label:m.label,type:'card',card:m.c.id,fn:()=>openTarget(m.c.id)});const n='@'+jc.nameOf(m.c);if(!by.has(n))by.set(n,[]);by.get(n).push(m);}
 for(const [n,list] of by)entries.push({text:n,type:'card',card:list.length>1?'':list[0].c.id,fn:list.length>1?()=>chooseCard(list):()=>openTarget(list[0].c.id)});
 entries.sort((a,b)=>b.text.length-a.text.length);
 // a card address card/<id>[#<anchor>] (an Agent's reply): linked when the id is a page or a card this computer has; a `code`
 // span that is exactly one address becomes the link too
 const known=id=>id==='cards'||SYS.includes(id)||jc.cards().some(c=>c.id===id&&c.on),addr=(s,at)=>{if(!jc.available()||!s.startsWith('card/',at)||/[A-Za-z0-9_/.-]/.test(s[at-1]||''))return null;const m=jc.ADDR.exec(s.slice(at));return m&&known(m[1])&&!/[A-Za-z0-9_/-]/.test(s[at+m[0].length]||'')?m:null;};
 const cardLink=m=>{const b=button(m[0],e=>{e.stopPropagation();openTarget(m[1],m[2]);},'fp-link fp-link-card');b.dataset.card=m[1];if(m[2])b.dataset.at=m[2];return b;};
 for(const c of box.querySelectorAll('code')){if(c.closest('pre,a,button'))continue;const s=c.textContent,m=addr(s,0);if(m&&m[0]===s)c.replaceWith(cardLink(m));}
 const walker=document.createTreeWalker(box,NodeFilter.SHOW_TEXT),nodes=[];while(walker.nextNode())if(!walker.currentNode.parentElement.closest('a,button,code,pre,.fp-link'))nodes.push(walker.currentNode);
 for(const n of nodes){const s=n.textContent;let out=document.createDocumentFragment(),at=0,changed=false,inBook=false;while(at<s.length){if(s[at]==='《')inBook=true;let match=!inBook&&entries.find(e=>s.startsWith(e.text,at)&&(!/[A-Za-z0-9_]/.test(e.text[0])||!/[A-Za-z0-9_/.-]/.test(s[at-1]||''))&&(!e.text.startsWith('/')||!at||/[\s，。！？；：、（(【\[]/u.test(s[at-1]))&&(!/[A-Za-z0-9_/-]/.test(e.text.at(-1))||!/[A-Za-z0-9_/-]/.test(s[at+e.text.length]||'')));
 const ad=!match&&addr(s,at);if(ad){out.append(cardLink(ad));at+=ad[0].length;changed=true;continue;}
 if(match){const b=button(match.label||match.text,match.fn,'fp-link fp-link-'+match.type);if(match.card!==undefined){b.dataset.card=match.card;b.addEventListener('click',e=>e.stopPropagation());}out.append(b);at+=match.text.length;changed=true;}else{out.append(document.createTextNode(s[at]));if(s[at]==='》')inBook=false;at++;}}if(changed)n.replaceWith(out);}
}
export function init(h){H=h;$('cardPlus')?.addEventListener('click',plusCard);
 // the + follows the view (body data-view / data-five-page), the three five surfaces and the computer's capability
 const mo=window.MutationObserver&&new MutationObserver(syncPlus);mo?.observe(document.body,{attributes:true,attributeFilter:['data-view','data-five-page','data-conn']});
 // 搜索 on the card wall searches the chat: the wall steps aside first
 $('srchBtn')?.addEventListener('click',()=>{if($('five-menu')&&!$('five-menu').hidden)go('chat');},true);jc.onChange(()=>{syncPlus();if($('five-menu')&&!$('five-menu').hidden)buildDeck(false);});const page=node('section');page.id='five-page';page.hidden=true;const head=node('header',undefined,'fp-header');head.append(iconButton('back',t('p299.back'),()=>go('chat')));const title=node('h1');title.id='five-title';head.append(title,iconButton('reload',t('p299.reload'),refresh));const body=node('div');body.id='five-body';
 const stick=node('div',undefined,'fp-sticky'),bar=node('div',undefined,'fp-bar');bar.id='five-bar';bar.hidden=true;const fbox=PF.field('five-find','p363.findWf');fbox.querySelector('input').addEventListener('input',e=>{findQ=e.target.value;render();page.scrollTop=0;});stick.append(head,bar,fbox);page.append(stick,body);
 if(window.ResizeObserver)new ResizeObserver(()=>page.style.setProperty('--fp-sticky-h',Math.round(stick.getBoundingClientRect().height)+'px')).observe(stick);
 const menu=node('section');menu.id='five-menu';menu.hidden=true;menu.setAttribute('role','dialog');menu.setAttribute('aria-label',t('p244.fivepages42'));menu.onclick=e=>{if(e.target===menu)closeMenu();};
 const sheet=node('section');sheet.id='five-detail';sheet.hidden=true;sheet.setAttribute('role','dialog');sheet.setAttribute('aria-label',t('p244.fivepages43'));
 const pop=node('div');pop.id='five-mentions';pop.hidden=true;pop.setAttribute('role','listbox');document.body.append(page,menu,sheet,pop);for(const n of [page,menu,sheet])mo?.observe(n,{attributes:true,attributeFilter:['hidden']});syncPlus();
 registerLayer({element:()=>page,open:()=>!page.hidden,close:()=>go('chat'),fallback:()=>$('orb'),rank:3,trap:false});registerLayer({element:()=>menu,open:()=>!menu.hidden,close:closeMenu,fallback:()=>$('orb'),rank:35});
 addEventListener('popstate',()=>{if(!armed)return;armed=false;stepBack();});registerLayer({element:()=>sheet,open:()=>!sheet.hidden,close:closeDetail,fallback:()=>$('orb'),rank:36});
 registerLayer({element:()=>fbox,open:()=>findOpen()&&sheet.hidden&&menu.hidden,close:closeFind,fallback:()=>$('srchBtn'),rank:4,trap:false});
 // P363 (#70): / on a five page opens (or focuses) its search, as on the shortcut sheet — never from a field
 addEventListener('keydown',e=>{if(e.key!=='/'||e.ctrlKey||e.metaKey||e.altKey||e.isComposing||page.hidden||current==='chat'||!sheet.hidden||!menu.hidden)return;const g=e.target;if(g&&(g.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(g.tagName)))return;e.preventDefault();e.stopImmediatePropagation();if(fbox.hidden)find();else fbox.querySelector('input').focus();},true);
 // P360: @ also names the owner's cards (chip 「@记事本」, the message carries 「@记事本（card/notes）」 for the Agent)
 const input=$('input');composer=composeMentions(input,()=>[...(pic?.items.filter(w=>w.wdb).map(w=>({text:mentionText(w),label:'@'+(w.ceo||w.folder)+' '+w.folder}))||[]),...(jc.available()?jc.mentions(sysNames()).map(m=>({text:m.text,label:m.label})):[])]);let options=[],at=null,index=0;
 function suggestions(){at=mentionAt(input.value,input.selectionStart);if(!at||(!pic&&!jc.available())){pop.hidden=true;return;}const q=at.query.toLowerCase();
  options=[...(pic?pic.items.filter(w=>w.wdb&&`${w.folder} ${w.ceo} ${w.company} ${w.py||''}`.toLowerCase().includes(q)):[]),
   ...(jc.available()?jc.mentions(sysNames()).filter(m=>`${jc.nameOf(m.c)} ${m.c.id}`.toLowerCase().includes(q)):[])].slice(0,20);index=0;paint();}
 function paint(){pop.replaceChildren();options.forEach((o,i)=>{let b;if(o.c){b=button(o.label.slice(1),()=>pick(i),'fp-mention fp-mention-card');b.dataset.card=o.c.id;const dot=node('span',undefined,'jc-dot jc-accent-'+o.c.accent);dot.append(jc.icon(o.c.icon,'jc-dot-icon'));b.prepend(dot);}
  else{b=button(`${o.company||o.folder} · ${o.ceo||''}`,()=>pick(i),'fp-mention');b.prepend(robot(o.robot,o.busy));}b.setAttribute('role','option');b.setAttribute('aria-selected',i===index);pop.append(b);});pop.hidden=!options.length;settleRobots(pop);fit();}
 // P302: above the composer, never under the status bar (keyboard up or not)
 // while the list is open it follows the composer (the body follows the keyboard a few frames later, or animates)
 let fitAt=null,fitF=0;function fit(){fitAt=null;if(!fitF)fitF=requestAnimationFrame(()=>fitNow());fitNow(true);}
 function fitNow(direct){if(!direct)fitF=0;if(pop.hidden)return;const c=input.closest('.composer')||input,top=c.getBoundingClientRect().top,vt=window.visualViewport?.offsetTop||0,k=top+'/'+vt;if(k!==fitAt){fitAt=k;fitAbove(pop,top);}if(!fitF)fitF=requestAnimationFrame(()=>fitNow());}
 window.visualViewport?.addEventListener('resize',fit);if(window.ResizeObserver&&input.closest('.composer'))new ResizeObserver(fit).observe(input.closest('.composer'));window.visualViewport?.addEventListener('scroll',fit);addEventListener('resize',fit);
 function pick(i){const where=at,o=options[i];pop.hidden=true;if(o.c)insertCard(o,where);else insertMention(o,where);}
 input.addEventListener('input',suggestions);input.addEventListener('click',suggestions);input.addEventListener('keydown',e=>{if(pop.hidden)return;if(['ArrowDown','ArrowUp','Enter','Tab','Escape'].includes(e.key)){e.preventDefault();e.stopImmediatePropagation();if(e.key==='Escape')pop.hidden=true;else if(e.key==='Enter'||e.key==='Tab')pick(index);else{index=(index+(e.key==='ArrowDown'?1:-1)+options.length)%options.length;paint();}}},true);
onLang(()=>{syncPlus();if(current!=='chat')render();});addEventListener('resize',place);
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refresh();});
}
