// First-party explanatory copy only. The destination is fixed, never derived from text or API data.
export const ACCOUNT_URL = 'https://agentj.app/account/';
const TERMS=/Agent J 管理账号|账号后台|账户页|账号页|Agent J management account|account dashboard|account page/gi;
export function accountNodes(text,{className='hint__link'}={}){
 return String(text).split(new RegExp('('+TERMS.source+')','gi')).filter(Boolean).map(part=>{
  if(!new RegExp('^(?:'+TERMS.source+')$','i').test(part))return document.createTextNode(part);
  const a=document.createElement('a');a.className=className;a.setAttribute('data-aj-owner','aj-prose');
  a.href=ACCOUNT_URL;a.target='_blank';a.rel='noopener noreferrer';a.textContent=part;return a;
 });
}
// Trusted build-time HTML only. Attributes, comments and existing interactive/code/script regions are never rewritten.
export function accountHtml(html){
 const source=String(html),blocked=[],tags=new Set(['a','button','select','option','code','pre','script','style','textarea','title']);let at=0,out='';
 const text=s=>blocked.length?s:s.replace(TERMS,label=>`<a class="hint__link" data-aj-owner="aj-prose" href="${ACCOUNT_URL}" target="_blank" rel="noopener noreferrer">${label}</a>`);
 for(const m of source.matchAll(/<(?:!--[\s\S]*?--|(?:[^"'<>]|"[^"]*"|'[^']*')*)>/g)){
  out+=text(source.slice(at,m.index))+m[0];at=m.index+m[0].length;
  const tag=/^<\/?([a-z][\w-]*)/i.exec(m[0])?.[1]?.toLowerCase();
  if(tags.has(tag)){if(m[0].startsWith('</')){if(blocked.at(-1)===tag)blocked.pop();}else if(!m[0].endsWith('/>'))blocked.push(tag);}
 }
 return out+text(source.slice(at));
}
