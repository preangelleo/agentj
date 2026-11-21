import { friendsTellCard } from './friends-tell-card.js';
// Display-only projection. The full signed action remains below the title.
import { lang, t } from './t.js';
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
function plazaDetails(e,context) {
 if(context?.envelope_verified!==true||context?.tool!=='agentj plaza'||context.envelope!==e||context.summary!==e?.details)return null;
 const prefix='agentj plaza\n';
 if(typeof e.details!=='string'||!e.details.startsWith(prefix))return null;
 try {return JSON.parse(e.details.slice(prefix.length));} catch {return null;}
}
function plazaRemoval(o) {
 return o?.operation==='uninstall'&&typeof o.package==='string'&&typeof o.target==='string'&&typeof o.backup==='string'
  &&hash(o.sha256)&&hash(o.files_sha256)&&hash(o.registry_sha256);
}
function installPackage(p) {
 return typeof p?.package==='string'&&/^[a-z0-9][a-z0-9-]{1,39}$/.test(p.package)
  &&typeof p.version==='string'&&/^[0-9A-Za-z.+-]{1,64}$/.test(p.version)
  &&typeof p.target==='string'&&typeof p.certified==='boolean'&&hash(p.sha256)
  &&typeof p.digest==='string'&&/^(?:[0-9a-f]{16}|[0-9a-f]{64})$/.test(p.digest);
}
function plazaInstallReason(e,o) {
 if(e?.action!=='agentj plaza'||!o||Object.hasOwn(o,'operation')||!Array.isArray(o.items))return '';
 const grouped=Array.isArray(o.packages),packages=grouped?o.packages:[o];
 if(!packages.length||packages.length>25||!packages.every(installPackage)||(grouped&&!hash(o.group_sha256)))return '';
 const src=e.source;
 const owner=src?.source_trust==='owner'&&['owner_remote','owner_terminal','owner_channel'].includes(src.source_type)
  &&src.verified===true&&src.suspicious===false&&!src.history_outside_window
  &&Number.isSafeInteger(src.message_id)&&src.message_id>0&&src.message_id===src.trigger_message_id&&Boolean(src.text);
 const label=p=>t('approval.plazaPackage',{name:p.package,version:p.version,signature:t(p.certified?'approval.plazaSigned':'approval.plazaCommunity')});
 const [first,second]=packages.slice(0,2).map(label);
 const names=second?t('approval.plazaTwoPackages',{first,second}):first;
 const visible=packages.length>2?t('approval.plazaMore',{names,n:packages.length-2}):names;
 const sha=(grouped?o.group_sha256:o.sha256).slice(0,12);
 return t(owner?'approval.plazaOwnerInstall':'approval.plazaInstall',{packages:visible,sha});
}
export function stepTitle(e,fallback='',context=null) {
 if(friendsTellCard(e,context))return lang()==='en'?'Send to this friend once':'仅向这个好友发送一次';
 if(plazaRemoval(plazaDetails(e,context)))return lang()==='en'?'Uninstall plaza package':'卸载广场包';
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
 const plaza=plazaDetails(e,context);
 if(plazaRemoval(plaza))return lang()==='en'?'Move the installed folder to a recoverable backup. Keep publisher trust and installation history; reinstalling needs the normal signed confirmation.':'把已安装目录移到可恢复备份，保留发布方信任和安装历史；重新安装仍需正常签名确认。';
 const norm=v=>String(v||'').replace(/\s+/g,' ').trim(), title=norm(stepTitle(e)).replace(/…$/,''), fullTitle=norm(e?.title);
 const distinct=v=>v&&v!==fullTitle&&v!==title&&!(title&&v.startsWith(title));
 const reason=norm(lang()==='en'?e?.reason_en:e?.reason);
 if(distinct(reason))return reason;
 const install=plazaInstallReason(e,plaza);
 if(install)return install;
 const hostRescue=context?.envelope_verified===true && context.host_tool===true &&
  context.tool==='AuthRescue' && context.envelope===e && context.summary===e?.details && e?.action==='AuthRescue';
 const action=norm(lang()==='en'?e?.action_en:e?.action);
 if(distinct(action)&&!(hostRescue&&action==='AuthRescue'))return action;
 const effects=new Set(Array.isArray(e?.effects)?e.effects:[]),has=(...keys)=>keys.some(k=>effects.has(k));
 const kind=e?.special?.tool==='SecretPickup'||has('secret_display')?'secret':has('sensitive_delivery')?'delivery':has('sensitive_storage','credentials')?'credentials':has('money','spend')?'money':has('irreversible_destroy','irreversible_delete','delete')?'delete':has('approval_root','hooks')?'approval':has('send')?'send':'missing';
 if(hostRescue&&kind==='missing')return t('approval.rescueReason');
 return t(kind==='missing'?(Object.hasOwn(e||{},'reason')||Object.hasOwn(e||{},'reason_en')?'p0287.reason.emptyOrRepeated':'p0287.reason.missingFields'):'p0284.reason.'+kind);
}
