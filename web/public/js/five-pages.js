// P244: in-memory, read-only catalogue over the existing paired Noise session. DOM only; no HTML parser or persistence.
import {request, done, peer, isReady, ask1} from './session.js';
import {registerLayer} from './layers.js';
import {composeMentions} from './workflow-compose.js';
import {RelayMD} from './md.js';
import {t, lang, onLang} from './t.js';
export const mentionText = w => `@${w.folder}\uFF08CEO ${w.ceo || w.folder}\uFF09`;
export function mentionAt(text, at=text.length) {
 const s=text.slice(0,at), m=/(^|[\s\u3000，。！？；：、（(【\[\p{Script=Han}])@([^\s，。！？；：、（）()【】\[\]]*)$/u.exec(s);
 return m ? {start:s.length-m[2].length-1, query:m[2]} : null;
}
export function robotFor(folder, role, ceo=0){let h=2166136261;const text=`${folder}/${role}`;for(let i=0;i<text.length;i++){h^=text.charCodeAt(i);h=Math.imul(h,16777619);}const pool=Array.from({length:16},(_,i)=>i).filter(i=>i!==ceo&&i!==14);return pool[(h>>>0)%pool.length];}
let composer;
let H, pic=null, current='chat', busy=false, epoch=0, filter='all', selected=null, timer;
const $=id=>document.getElementById(id);
const names=()=>[['chat',t('p244.fivepages1'),'◌'],['workflows',t('p244.fivepages2'),'⌘'],['skills',t('p244.fivepages3'),'✧'],['memory',t('p244.fivepages4'),'◇'],['agentj','Agent J','△']];
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,fn,cls='fp-action'){const n=node('button',text,cls);n.type='button';n.onclick=fn;return n;}
function icon(key){const ns='http://www.w3.org/2000/svg',s=document.createElementNS(ns,'svg');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('class','fp-line-icon');s.setAttribute('aria-hidden','true');s.setAttribute('fill','none');s.setAttribute('stroke','currentColor');s.setAttribute('stroke-width','1.7');s.setAttribute('stroke-linecap','round');s.setAttribute('stroke-linejoin','round');
 const paths={chat:'M5 4h14v12H9l-4 4V4z',workflows:'M12 4v6M5 10h14M5 10v6M19 10v6M9 4h6M2 16h6v5H2zM16 16h6v5h-6z',skills:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',memory:'M6 4h12v16H6zM9 8h6M9 12h6M9 16h4',lock:'M7 10V7a5 5 0 0 1 10 0v3M5 10h14v11H5zM12 14v3',doc:'M8.6 4.4H5.8a.8.8 0 0 0-.8.8v16a.8.8 0 0 0 .8.8h12.4a.8.8 0 0 0 .8-.8v-16a.8.8 0 0 0-.8-.8h-2.8M8.6 3.4h1.9a1.5 1.5 0 0 1 3 0h1.9v3.2H8.6zM7.9 10.3l2.6 2.6M10.5 10.3l-2.6 2.6M13.2 10.6h3.3M13.2 12.7h3.3M7.8 17.4l1.3 1.3 2.4-2.6M13.2 16.4h3.3M13.2 18.5h3.3',all:'M6 6h1v1H6zM17 6h1v1h-1zM6 17h1v1H6zM17 17h1v1h-1z',global:'M12 2a10 10 0 1 0 0 20 10 10 0 1 0 0-20M2 12h20M12 2c-6 6-6 14 0 20M12 2c6 6 6 14 0 20',local:'M3 11l9-8 9 8M6 10v11h12V10M10 21v-7h4v7'};
 const path=document.createElementNS(ns,'path');path.setAttribute('d',paths[key]||paths.doc);s.append(path);return s;
}
function robot(r,working=false){const n=node('span',undefined,'fp-robot');n.style.backgroundPositionY=`-${(r||0)*24}px`;if(working)n.classList.add('working');return n;}
export function clear(){composer?.clear();epoch++;clearTimeout(timer);pic=null;busy=false;selected=null;closeDetail();closeMenu();go('chat');}
export function refresh(){
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
export function go(page,folder){
 if(!H)return;current=page;selected=folder||null;closeMenu();closeDetail();
 $('five-page').hidden=page==='chat';document.body.dataset.fivePage=page;
 if(page==='chat'){H.chat();return;}H.chat();render();refresh();
}
export function toggleMenu(){if(!$('five-menu'))return;const e=$('five-menu');if(!e.hidden){closeMenu();return;}
 e.replaceChildren();const deck=node('div',undefined,'fp-menu-deck');
 for(const [i,[key,title,icon]] of names().entries()){
  const b=button('',()=>go(key),'fp-menu-card fp-dark-'+i);b.dataset.page=key;
  const head=node('div',undefined,'fp-menu-head');
  if(key==='agentj'){const img=node('img');img.src='assets/five-helmet.png';img.alt='';head.append(img);}else head.append(globalIcon(key));
  head.append(node('span',current===key?t('p244.fivepages5'):String(i+1),'fp-current'));
  b.append(head,node('h2',title),node('p',[t('p244.fivepages6'),t('p244.fivepages7'),t('p244.fivepages8'),t('p244.fivepages9'),t('p244.fivepages10')][i]));deck.append(b);
 }e.append(deck);e.hidden=false;deck.querySelector('button').focus();
}
export function closeMenu(){if($('five-menu'))$('five-menu').hidden=true;}
const globalIcon=icon;
function card(title,note,i,fn){const c=node('article',undefined,'fp-card fp-tint-'+i%10);const head=button('',fn,'fp-card-head');if(current!=='workflows')head.append(globalIcon(current==='agentj'?'doc':current));head.append(node('span',title,'fp-card-title'));c.append(head);if(note)c.append(node('p',note));return c;}
function empty(text){$('five-body').append(node('p',text,'fp-empty'));}
function render(){const body=$('five-body');body.replaceChildren();$('five-title').textContent=names().find(x=>x[0]===current)?.[1]||'';
 if(!pic){empty(isReady()&&!peer.wf1?t('p244.fivepages11'):t('p244.fivepages12'));return;}
 if(current==='workflows'){
  body.append(node('p',`${pic.root_name} · ${pic.counts.wdb} ${t('p244.fivepages13')} · ${pic.counts.busy} ${t('p244.fivepages14')}`,'fp-meta'));
  pic.items.forEach((w,i)=>{const c=card(w.company||w.folder,w.one||t('p244.fivepages15'),i,()=>{selected=selected===w.folder?null:w.folder;render();});c.dataset.folder=w.folder;if(w.wdb)c.dataset.lap=String(w.lap||0);
   c.querySelector('button').prepend(robot(w.wdb?w.robot:14,w.busy));c.append(node('small',w.ceo?`CEO ${w.ceo} · ${w.folder}`:w.folder));
   if(w.wdb)c.append(button('@',()=>insertMention(w), 'fp-at'));
   if(selected===w.folder){const tabs=node('div',undefined,'fp-tabs');for(const [label,fn] of [[t('p244.fivepages16'),()=>expand(c,w,'agents')],[t('p244.fivepages17'),()=>expand(c,w,'docs')],[t('p244.fivepages18'),()=>expand(c,w,'skills')]])tabs.append(button(label,fn,'fp-tab'));c.append(tabs,node('div',undefined,'fp-expanded'));expand(c,w,'agents');}body.append(c);
  });if(!pic.items.length)empty(t('p244.fivepages19'));
 }else if(current==='skills'){
  const tabs=node('div',undefined,'fp-tabs');for(const [k,label] of [['all',t('p244.fivepages20')],['global',t('p244.fivepages21')],['local',t('p244.fivepages22')]]){const b=button(label,()=>{filter=k;render();},'fp-tab');const mark=globalIcon(k);mark.classList.add('fp-category-icon','fp-category-'+k);b.prepend(mark);b.setAttribute('aria-pressed',filter===k);tabs.append(b);}body.append(tabs);
  let group='';pic.skills.filter(s=>filter==='all'||(filter==='global'?s.scope!=='local':s.scope==='local')).forEach((s,i)=>{const g=s.folder||t('p244.fivepages23');if(g!==group){body.append(node('h3',g,'fp-group'));group=g;}body.append(card('/'+s.name,s.desc,i,()=>skill(s)));});
 }else if(current==='agentj'){
  body.append(card(t('p244.fivepages24'),t('p244.fivepages25'),1,()=>doc('@core','core',true)));
  const lock=icon('lock');lock.classList.add('fp-lock');body.lastChild.querySelector('.fp-line-icon')?.remove();body.lastChild.querySelector('button').prepend(lock);
  (pic.agentj.docs||[]).forEach((d,i)=>body.append(card(d.title||d.name,d.name,i+2,()=>doc('',d.name))));if(pic.agentj.hidden)body.append(node('p',t('p244.fivepages26'),'fp-meta'));
 }else if(current==='memory'){
  pic.memory.forEach((m,i)=>{const w=pic.items.find(w=>w.folder===m.owner),title=m.owner==='agent'?t('p244.fivepages27'):(w?.company||m.owner);const c=card(title,`${m.files.length} ${t('p244.fivepages28')}`,i,()=>{});
   for(const f of m.files){if(m.owner==='agent'&&f.src!=='root'){c.append(node('p',f.name));continue;}c.append(button(f.title||f.name,()=>doc(m.owner==='agent'?'':m.owner,f.name),'fp-file'));}if(m.owner==='agent')c.append(button(t('p244.fivepages29'),()=>{go('chat');H.memory();}));body.append(c);});
 }
}
function expand(c,w,tab){const box=c.querySelector('.fp-expanded');box.replaceChildren();if(tab==='docs')for(const d of w.docs)box.append(button(d.title||d.name,()=>doc(w.folder,d.name),'fp-file'));else if(tab==='skills')for(const id of w.skills){const s=pic.skills.find(s=>s.id===id);if(s)box.append(button('/'+s.name,()=>skill(s),'fp-file'));}else{
 const row=node('div',undefined,'fp-person');row.append(robot(w.robot,w.busy),node('strong',w.ceo||w.folder));box.append(row);
 for(const a of w.agents){const row=node('div',undefined,'fp-person');row.append(robot(robotFor(w.folder,a.name,w.robot)),node('span',a.name+' · '+a.desc));box.append(row);}}
 if(!box.childNodes.length)box.append(node('p',t('p244.fivepages30')));
}
function closeDetail(){if($('five-detail'))$('five-detail').hidden=true;}
async function detail(req,title,action,metadata=[]){const e=epoch,box=$('five-detail');box.replaceChildren();box.append(button('←',closeDetail,'fp-back'),node('h2',title));box.hidden=false;for(const text of metadata)box.append(node('p',text,'fp-meta'));const body=node('div',t('p244.fivepages31'),'fp-prose');box.append(body);const m=await ask1(req,req.t,15000);if(e!==epoch||box.hidden)return;
 body.replaceChildren();if(!m.ok){body.append(node('p',t('p244.fivepages32')));return;}
 body.append(RelayMD.toDOM(RelayMD.parse(m.text),document));linkify(body);if(m.cut)body.append(node('p',t('p244.fivepages33')));if(action)box.append(button(action.title,action.fn));else box.append(button(t('p244.fivepages34'),closeDetail));
}
function doc(folder,name,core=false){detail({t:'wf_doc',folder,name},name,core?null:{title:t('p244.fivepages35'),fn:()=>{go('chat');H.insert(`${t('p244.fivepages36')}${folder?folder+'/':''}documentation/${name}`);H.send();}});}
function skill(s){detail({t:'skill_get',id:s.id},'/'+s.name+(s.aliases?.length?' · '+s.aliases.join(' / '):''),{title:t('p244.fivepages37'),fn:()=>{go('chat');H.insert('/'+s.name+' ');}},[s.desc,t('p244.fivepages38')+(s.folder||(s.scope==='bundled'?t('p244.fivepages39'):t('p244.fivepages40'))),t('p244.fivepages41')+'/'+s.name+(s.aliases?.length?' · '+s.aliases.map(a=>'/'+a).join(' · '):'')]);}
function insertMention(w,replace){go('chat');const input=$('input');if(replace){input.setRangeText(mentionText(w)+' ',replace.start,input.selectionStart,'end');input.dispatchEvent(new Event('input'));input.focus();}else H.insert(mentionText(w)+' ');composer?.paint(true);}
export function linkify(box){if(!pic)return;const ws=pic.items.filter(w=>w.wdb),sk=pic.skills;const entries=[];
 for(const s of sk)for(const name of [s.name,...(s.aliases||[])])entries.push({text:'/'+name,type:'skill',fn:()=>skill(s)});
 for(const w of ws){for(const text of [mentionText(w),w.folder,w.company].filter(x=>x&&x.length>=3))entries.push({text,type:'workflow',fn:()=>go('workflows',w.folder)});if(w.ceo)entries.push({text:w.ceo,type:'person',fn:()=>go('workflows',w.folder)});for(const d of w.docs)entries.push({text:d.name,type:'doc',fn:()=>doc(w.folder,d.name)});}
 entries.push({text:'\u5DE5\u4F5C\u6D41',type:'workflow',fn:()=>go('workflows')},{text:'workflow',type:'workflow',fn:()=>go('workflows')},{text:'workflows',type:'workflow',fn:()=>go('workflows')},{text:t('p244.fivepages4'),type:'memory',fn:()=>go('memory')});entries.sort((a,b)=>b.text.length-a.text.length);
 const walker=document.createTreeWalker(box,NodeFilter.SHOW_TEXT),nodes=[];while(walker.nextNode())if(!walker.currentNode.parentElement.closest('a,button,code,pre,.fp-link'))nodes.push(walker.currentNode);
 for(const n of nodes){const s=n.textContent;let out=document.createDocumentFragment(),at=0,changed=false,inBook=false;while(at<s.length){if(s[at]==='《')inBook=true;let match=!inBook&&entries.find(e=>s.startsWith(e.text,at)&&(!/[A-Za-z0-9_]/.test(e.text[0])||!/[A-Za-z0-9_/.-]/.test(s[at-1]||''))&&(!e.text.startsWith('/')||!at||/[\s，。！？；：、（(【\[]/u.test(s[at-1]))&&(!/[A-Za-z0-9_/-]/.test(e.text.at(-1))||!/[A-Za-z0-9_/-]/.test(s[at+e.text.length]||'')));
 if(match){const b=button(match.text,match.fn,'fp-link fp-link-'+match.type);out.append(b);at+=match.text.length;changed=true;}else{out.append(document.createTextNode(s[at]));if(s[at]==='》')inBook=false;at++;}}if(changed)n.replaceWith(out);}
}
export function init(h){H=h;const page=node('section');page.id='five-page';page.hidden=true;const head=node('header',undefined,'fp-header');head.append(button('←',()=>go('chat'),'fp-back'));const title=node('h1');title.id='five-title';head.append(title,button('↻',refresh,'fp-back'));const body=node('div');body.id='five-body';page.append(head,body);
 const menu=node('section');menu.id='five-menu';menu.hidden=true;menu.setAttribute('role','dialog');menu.setAttribute('aria-label',t('p244.fivepages42'));menu.onclick=e=>{if(e.target===menu)closeMenu();};
 const sheet=node('section');sheet.id='five-detail';sheet.hidden=true;sheet.setAttribute('role','dialog');sheet.setAttribute('aria-label',t('p244.fivepages43'));
 const pop=node('div');pop.id='five-mentions';pop.hidden=true;pop.setAttribute('role','listbox');document.body.append(page,menu,sheet,pop);
 registerLayer({element:()=>menu,open:()=>!menu.hidden,close:closeMenu,fallback:()=>$('orb'),rank:35});registerLayer({element:()=>sheet,open:()=>!sheet.hidden,close:closeDetail,fallback:()=>$('orb'),rank:36});
 const input=$('input');composer=composeMentions(input,()=>pic?.items.filter(w=>w.wdb).map(w=>({text:mentionText(w),label:'@'+(w.ceo||w.folder)+' '+w.folder}))||[]);let options=[],at=null,index=0;
 function suggestions(){at=mentionAt(input.value,input.selectionStart);if(!at||!pic){pop.hidden=true;return;}options=pic.items.filter(w=>w.wdb&&`${w.folder} ${w.ceo} ${w.company} ${w.py||''}`.toLowerCase().includes(at.query.toLowerCase())).slice(0,20);index=0;paint();}
 function paint(){pop.replaceChildren();options.forEach((w,i)=>{const b=button(`${w.company||w.folder} · ${w.ceo||''}`,()=>pick(i),'fp-mention');b.prepend(robot(w.robot,w.busy));b.setAttribute('role','option');b.setAttribute('aria-selected',i===index);pop.append(b);});pop.hidden=!options.length;}
 function pick(i){const where=at;pop.hidden=true;insertMention(options[i],where);}
 input.addEventListener('input',suggestions);input.addEventListener('click',suggestions);input.addEventListener('keydown',e=>{if(pop.hidden)return;if(['ArrowDown','ArrowUp','Enter','Tab','Escape'].includes(e.key)){e.preventDefault();e.stopImmediatePropagation();if(e.key==='Escape')pop.hidden=true;else if(e.key==='Enter'||e.key==='Tab')pick(index);else{index=(index+(e.key==='ArrowDown'?1:-1)+options.length)%options.length;paint();}}},true);
onLang(()=>{if(current!=='chat')render();});
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refresh();});
}
