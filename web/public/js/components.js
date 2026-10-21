// Inline components: bounded data → native DOM. No HTML parser, URL fetch, evaluator or tool callback.
import contract, {RENDERERS} from './components.gen.js';
import {t} from './t.js';
import {SVG} from './icons.js';
export {contract};
const specs=new Map(contract.components.filter(x=>x.schema).map(x=>[x.name,x.schema]));
function valid(v,s,depth=0){
 if(depth>10)return false;
 if(s.enum)return s.enum.includes(v);
 if(s.type==='string')return typeof v==='string' && v.length>0 && v.length<=s.maxLength && !/[\x00-\x08\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]/.test(v);
 if(s.type==='number')return typeof v==='number' && Number.isFinite(v) && v>=s.minimum && v<=s.maximum;
 if(s.type==='array')return Array.isArray(v) && v.length>=s.minItems && v.length<=s.maxItems && v.every(x=>valid(x,s.items,depth+1));
 if(s.type==='object')return v!==null && typeof v==='object' && !Array.isArray(v) && s.required.every(k=>Object.hasOwn(v,k)) && Object.keys(v).every(k=>Object.hasOwn(s.properties,k) && valid(v[k],s.properties[k],depth+1));
 return false;
}
export function parseComponent(src){
 if(typeof src!=='string'||src.length>contract.maxChars)return null;
 try{
  const v=JSON.parse(src), spec=specs.get(v?.component);if(!spec||!valid(v,spec))return null;
  if(v.component==='flowchart'){
   const ids=new Set(v.nodes.map(n=>n.id));
   if(ids.size!==v.nodes.length||!v.edges.every(e=>ids.has(e.from)&&ids.has(e.to)))return null;
  }
  if(v.component==='form'){
   if(new Set(v.fields.map(f=>f.id)).size!==v.fields.length)return null;
   if(v.fields.some(f=>!/^[a-z][a-z0-9_-]{0,23}$/.test(f.id)||/password|passphrase|secret|token|credential|api.?key|private.?key|密码|密钥|口令|验证码|私钥/i.test(f.id+' '+f.label)||(f.type==='select'?!f.options:f.options)))return null;
  }
  return v;
 }catch{return null;}
}
const ctx=new WeakMap();
function el(doc,tag,text,cls){const n=doc.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function svg(doc,tag,attrs,text){const n=doc.createElementNS(SVG,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n;}
function graph(v,doc){
 const flow=v.component==='flowchart', w=flow?360:600,h=flow?v.nodes.length*64+20:280;
 const g=svg(doc,'svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':v.title,'font-size':flow?20:26});
 if(flow){
  const positions=new Map(v.nodes.map((n,i)=>[n.id,35+i*64]));
  // A vertical node lane plus edge labels keeps arbitrary graphs legible in a narrow reply.
  for(const n of v.nodes){const y=positions.get(n.id),letters=Array.from(n.label);g.append(svg(doc,'rect',{x:30,y:y-22,width:260,height:44,rx:8,fill:'none',stroke:'currentColor'}),svg(doc,'text',{x:160,y:y+5,'text-anchor':'middle'},letters.length>12?letters.slice(0,11).join('')+'…':n.label));}
  for(const e of v.edges){const a=positions.get(e.from),b=positions.get(e.to);g.append(svg(doc,'path',{d:`M290 ${a} L${310+v.edges.indexOf(e)%4*10} ${a} L${310+v.edges.indexOf(e)%4*10} ${b} L290 ${b}`,fill:'none',stroke:'currentColor'}),svg(doc,'text',{x:290,y:b+5},'◀'));}
 }else{
  const max=Math.max(1,...v.items.map(x=>Math.abs(x.value))), step=540/v.items.length;
  const points=[];
  v.items.forEach((x,i)=>{const px=30+step*(i+.5),y=130-x.value/max*95;points.push(`${px},${y}`);
   if(v.kind==='bar')g.append(svg(doc,'rect',{x:px-step*.35,y:Math.min(130,y),width:step*.7,height:Math.max(1,Math.abs(130-y)),fill:'currentColor',opacity:.65}));
   if(v.items.length<=8)g.append(svg(doc,'text',{x:px,y:(x.value<0?y+28:Math.max(25,y-8)),'text-anchor':'middle'},String(x.value)));
  });
  g.append(svg(doc,'path',{d:'M20 130 H580',stroke:'currentColor'}));
  if(v.kind==='line')g.append(svg(doc,'polyline',{points:points.join(' '),fill:'none',stroke:'currentColor','stroke-width':3}));
 }
 return g;
}
export function renderComponent(v,doc){
 if(RENDERERS[v.component]!==v.component)throw new Error('unregistered renderer');
 const box=el(doc,'section',undefined,'reply-component');box.dataset.component=v.component;box.append(el(doc,'h3',v.title));
 const action=(label,text)=>{const b=el(doc,'button',label,'rc-draft');b.type='button';b.disabled=true;b.addEventListener('click',e=>{e.stopPropagation();const c=ctx.get(box);if(c?.on)c.draft(t('components.prefix')+' '+text());});return b;};
 if(v.component==='flowchart'||v.component==='chart'){
  box.append(graph(v,doc));
  if(v.component==='flowchart'){const nodes=el(doc,'ul');for(const n of v.nodes)nodes.append(el(doc,'li',n.label));box.append(nodes);}
  const list=el(doc,'ul');
  for(const x of v.component==='chart'?v.items:v.edges){const a=v.component==='chart'?`${x.label}: ${x.value}`:`${v.nodes.find(n=>n.id===x.from).label} → ${v.nodes.find(n=>n.id===x.to).label}`;list.append(el(doc,'li',a));}box.append(list);
 }else if(v.component==='steps'){
  const list=el(doc,'ol');for(const x of v.items){const li=el(doc,'li',`${t('components.'+x.status)} · ${x.label}`);if(x.status==='active')li.setAttribute('aria-current','step');list.append(li);}box.append(list);
 }else if(v.component==='confirm'){
  box.append(el(doc,'p',v.message));for(const choice of v.choices)box.append(action(choice,()=>`${v.title}: ${choice}`));
 }else if(v.component==='form'){
  const fields=[];
  for(const f of v.fields){const label=el(doc,'label',f.label),input=el(doc,f.type==='select'?'select':'input');
   if(f.type==='select')for(const option of f.options)input.append(el(doc,'option',option));else{input.type=f.type;input.maxLength=1000;}
   input.disabled=true;fields.push([f,input]);label.append(input);box.append(label);
  }
  box.append(action(t('components.prepare'),()=>v.title+'\n'+fields.map(([f,i])=>f.label+': '+(f.type==='checkbox'?(i.checked?t('components.yes'):t('components.no')):i.value.slice(0,1000))).join('\n')));
 }
 if(v.component==='form'||v.component==='confirm')box.append(el(doc,'p',t('components.draftOnly'),'rc-note'));
 return box;
}
export function upgradeComponents(root){
 const marks=[...root.querySelectorAll('pre[data-lang="agentj"]')];
 let count=root.querySelectorAll('.reply-component').length;
 for(const pre of marks){if(count>=contract.maxComponents)break;const wrap=pre.closest('.codeblock');if(!wrap||wrap.closest('blockquote')&&wrap.closest('blockquote')!==root)continue;
  const v=parseComponent(pre.querySelector('code')?.textContent);if(!v)continue;
  try{wrap.replaceWith(renderComponent(v,root.ownerDocument));count++;}catch{/* A failed block keeps its source; the next block still renders. */}
 }
}
export function wireComponents(root,on,draft){
 for(const box of root.querySelectorAll('.reply-component')){
  const inQuote=box.parentElement?.closest('blockquote');
  const enabled=!!on&&(!inQuote||inQuote===root);
  ctx.set(box,{on:enabled,draft});
  for(const b of box.querySelectorAll('button,input,select'))b.disabled=!enabled;
 }
}
