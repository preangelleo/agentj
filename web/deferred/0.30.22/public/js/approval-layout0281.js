import { friendsTellCard } from './friends-tell-card.js';
// Display-only projection. The full signed action remains below the title.
import { lang, t } from './t.js';
function plazaRemoval(e,context) {
 if(context?.envelope_verified!==true||context?.tool!=='agentj plaza'||context.envelope!==e||context.summary!==e?.details)return false;
 const prefix='agentj plaza\n',details=e.details;
 if(typeof details!=='string'||!details.startsWith(prefix))return false;
 try {const o=JSON.parse(details.slice(prefix.length)),hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
  return o&&o.operation==='uninstall'&&typeof o.package==='string'&&typeof o.target==='string'&&typeof o.backup==='string'
   &&hash(o.sha256)&&hash(o.files_sha256)&&hash(o.registry_sha256);
 } catch {return false;}
}
export function stepTitle(e,fallback='',context=null) {
 if(friendsTellCard(e,context))return lang()==='en'?'Send to this friend once':'仅向这个好友发送一次';
 if(plazaRemoval(e,context))return lang()==='en'?'Uninstall plaza package':'卸载广场包';
 const source=e?.source||{},quoted=[source.text,source.root?.text].filter(x=>typeof x==='string'&&x.trim()).map(x=>x.replace(/\s+/g,' ').trim());
 const action=String((lang()==='en'&&e?.action_en)||e?.action||'').replace(/\s+/g,' ').trim();
 const details=String(e?.details||''),cut=details.lastIndexOf('\n— ');
 let description=cut>=0?details.slice(cut+3).split('\nAgent ')[0].trim():'';
 try {const parsed=JSON.parse(details);if(typeof parsed?.description==='string')description=parsed.description;} catch {}
 description=description.replace(/\s+/g,' ').trim();
 const title=String((lang()==='en'&&e?.title_en)||e?.title||'').replace(/\s+/g,' ').trim();
 const quotedText=value=>quoted.some(q=>q===value||q.includes(value)||value.includes(q));
 const value=(description&&!quotedText(description)?description:title&&Array.from(title).length<=20&&!quotedText(title)?title:action&&!quotedText(action)?action:fallback).split(/[。！？\n]/u)[0];
 const chars=Array.from(value||fallback);return chars.length>20?chars.slice(0,19).join('')+'…':chars.join('');
}

// Projection of authenticated metadata only; never classify a command or change its authority.
export function stepReason(e,context=null) {
 const tell=friendsTellCard(e,context);
 if(tell)return lang()==='en'?`Friend: ${tell.friend_name}
Message: ${tell.text}
Only this exact message, once. No commands or native sandbox changes.`:`好友：${tell.friend_name}
消息：${tell.text}
仅此消息、仅此一次。不执行命令，不改变原生沙盒。`;
 if(plazaRemoval(e,context))return lang()==='en'?'Move the installed folder to a recoverable backup. Keep publisher trust and installation history; reinstalling needs the normal signed confirmation.':'把已安装目录移到可恢复备份，保留发布方信任和安装历史；重新安装仍需正常签名确认。';
 const norm=v=>String(v||'').replace(/\s+/g,' ').trim(), title=norm(stepTitle(e)).replace(/…$/,''), fullTitle=norm(e?.title);
 const distinct=v=>v&&v!==fullTitle&&v!==title&&!(title&&v.startsWith(title));
 for(const value of [lang()==='en'?e?.reason_en:e?.reason,lang()==='en'?e?.action_en:e?.action]){const s=norm(value);if(distinct(s))return s;}
 const effects=new Set(Array.isArray(e?.effects)?e.effects:[]),has=(...keys)=>keys.some(k=>effects.has(k));
 const kind=e?.special?.tool==='SecretPickup'||has('secret_display')?'secret':has('sensitive_delivery')?'delivery':has('sensitive_storage','credentials')?'credentials':has('money','spend')?'money':has('irreversible_destroy','irreversible_delete','delete')?'delete':has('approval_root','hooks')?'approval':has('send')?'send':'missing';
 return t(kind==='missing'?(Object.hasOwn(e||{},'reason')||Object.hasOwn(e||{},'reason_en')?'p0287.reason.emptyOrRepeated':'p0287.reason.missingFields'):'p0284.reason.'+kind);
}
