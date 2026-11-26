// Only Host-generated system metadata offers the existing allowlisted repair control.
import {mk} from './ui.js';
import {t} from './t.js';
import {copyable} from './copycode.js';
import {OLD_UPDATER_FIX} from './old-updater-line.js';
export function repairOf(page) {
 const s=page?.source,c=page?.card;
 if(!s||s.quote||page.assist||page.end==='open')return false;
 return s.k==='sys'&&s.repair==='reconnect'||s.k==='cmd'&&c?.cmd==='update'&&['ok','error'].includes(c.kind)&&c.repair==='reconnect';
}
export const replyName=(page,normal)=>repairOf(page)?'AgentJ':normal;
const upgradeGuide = 'https://agentj.app/docs/install/#upgrade-failed';
function guideLink() {
 const link=mk('a','aj-upgrade-repair-guide',t('p0353.repair.guide'),'aj-reply');
 link.href=upgradeGuide;link.target='_blank';link.rel='noopener noreferrer';
 return link;
}
function repairGuide(page) {
 const s=page.source;
 if(s.via||s.forward||page.reply?.includes(upgradeGuide))return null;
 if(s.k!=='sys'&&page.card?.kind!=='error')return null;
 return guideLink();
}
// 0.30.47-0.30.65 on Linux: the host's own updater wrote its dependency cache with the service umask (0002 on Ubuntu) and
// then refused those files, on every attempt. A newer package cannot change the updater that installs it, so this page
// (always the newest) shows the one terminal line. The host version is the connection's own; the words are that host's fixed
// vocabulary, used only to narrow the match. Those words read like tampering, so the card says in plain words what most
// likely happened (the page cannot see the sub-reason: never a promise) and keeps the host's own text one tap away.
// The line is a constant (old-updater-line.js). 「执行」 exists only behind that file's switch and while this remote may use
// the web terminal (termcommands.js fixedLine: the 「!」 path with `run`, the host's own signed confirm); otherwise copy
// only: nothing is sent to the host.
export {OLD_UPDATER_FIX};
const OLD_UPDATER_WORDS=['install_artifact_changed','已校验的安装包在安装前被改动或替换','the verified package changed or was replaced before installing'];
export function oldUpdaterFixOf(page,hostVersion) {
 if(!repairOf(page)||page.source.via||page.source.forward)return false;
 if(page.source.k==='cmd'&&page.card.kind!=='error')return false;
 const m=/^0\.30\.(\d{1,3})$/.exec(typeof hostVersion==='string'?hostVersion:'');
 if(!m||+m[1]<47||+m[1]>65)return false;
 return typeof page.reply==='string'&&OLD_UPDATER_WORDS.some(w=>page.reply.includes(w));
}
// A card drawn this way stays this way while the page lives (memory only: page id → the host version it was drawn for). After
// the update the connection reports the new version; the same page must not turn back into the alarming sentence. The marks
// of the page are checked again with the remembered version, so memory alone never keeps a card.
const drawn=new Map();
function oldCardVersion(page,hostVersion) {
 if(oldUpdaterFixOf(page,hostVersion)){
  if(Number.isInteger(page.id)){drawn.delete(page.id);drawn.set(page.id,hostVersion);while(drawn.size>64)drawn.delete(drawn.keys().next().value);}
  return hostVersion;
 }
 const v=Number.isInteger(page?.id)?drawn.get(page.id):undefined;
 return v&&oldUpdaterFixOf(page,v)?v:null;
}
/** The card's own words instead of the old host's (relay.js: the page, reading aloud, the reader); null = the host's text. */
export function oldUpdaterWords(page,hostVersion) {
 const v=oldCardVersion(page,hostVersion);
 return v?t('p0371.oldfix.why',{v}):null;
}
function paintOldUpdaterFix(box,page,ctx) {
 // The reply's own code block: one line that scrolls sideways, so a wrap is never read as a space when typed by hand.
 // Its buttons sit in the bar above the code (the reply code block's layout), never on top of a character.
 const fix=mk('div','cmdresume'),block=mk('div','codeblock'),bar=mk('div','bar'),pre=mk('pre'),code=mk('code','',OLD_UPDATER_FIX);
 const copy=copyable(code).querySelector('button');copy.classList.add('copy');
 fix.dataset.oldUpdaterFix='1';pre.dataset.lang='sh';bar.append(copy);pre.append(code);block.append(bar,pre);fix.append(block);box.append(fix);
 // 「执行」 only while the host is still one of those versions (live), and only if the terminal module offers it
 fix.dataset.run=oldUpdaterFixOf(page,ctx.hostVersion?.())&&ctx.runLine?.(block,page)===true?'1':'0';
 box.append(guideLink());
 // The host's own message, whole and unchanged (textContent): folded, never dropped.
 const raw=mk('details','cmdresume');raw.dataset.oldUpdaterRaw='1';
 raw.append(mk('summary','',t('p0371.oldfix.raw'),'aj-reply'),mk('p','',page.reply));box.append(raw);
}
export function paintRepair(box,page,ctx) {
 if(!repairOf(page))return false;
 // 「修复连接」 restarts the link to the relay; this failure is in the updater, with the link up: not offered on this card.
 if(oldCardVersion(page,ctx.hostVersion?.())){paintOldUpdaterFix(box,page,ctx);return true;}
 const guide=repairGuide(page);
 if(guide&&page.source.k==='cmd')box.append(guide);
 const b=mk('button','cmdundo',t('p0284.repair.button'),'aj-reply');b.type='button';b.dataset.repair='reconnect';
 const blocked=!ctx.ready()?t('p0284.repair.offline'):!ctx.supported()?t('p0284.repair.unsupported'):'';
 b.disabled=!!blocked;
 b.addEventListener('click',()=>{if(ctx.ready()&&ctx.supported())ctx.run();});
 box.append(b);
 if(blocked)box.append(mk('p','small',blocked));
 const more=mk('details','cmdresume'),label=mk('summary','',t('p339.details'),'aj-reply');
 more.append(label);
 if(guide&&page.source.k==='sys')more.append(guide);
 more.append(copyable(mk('code','','agentj doctor --reconnect')));box.append(more);return true;
}
// 0.30.7: a Codex sandbox refusal caused by the owner's own config.toml carries the host's typed card.fix — never inferred
// from the page's text (the notice also shows the terminal commands). One button: the signed cmd_run by NAME + Face ID.
export const FIX = 'codex_full_access';
export function fixOf(page) {
 const s=page?.source,c=page?.card;
 if(!s||s.quote||s.via||s.forward||page.assist||page.end==='open')return false;
 return s.k==='sys'&&!!c&&typeof c==='object'&&c.fix===FIX;
}
export function fixResultText(m) {
 if(m?.ok&&m.unchanged)return t('p0307.fix.already');
 if(m?.ok&&m.restart==='requested')return t('p0307.fix.restarting');
 if(m?.ok)return t('p0307.fix.manual');
 return t('p0307.fix.failed');
}
export function paintFix(box,page,ctx) {
 if(!fixOf(page))return false;
 const b=mk('button','cmdundo',t('p0307.fix.button'),'aj-reply');b.type='button';b.dataset.fix=FIX;
 const blocked=!ctx.ready()?t('p0284.repair.offline'):!ctx.supported()?t('p0307.fix.unsupported'):'';
 b.disabled=!!blocked;
 b.addEventListener('click',async()=>{
  if(!ctx.ready()||!ctx.supported()||b.dataset.busy)return;
  b.dataset.busy='1';b.disabled=true;
  try{const m=await ctx.run();if(m?.why!=='cancel')ctx.say(fixResultText(m));}
  finally{delete b.dataset.busy;b.disabled=!ctx.ready()||!ctx.supported();}
 });
 box.append(b);
 if(blocked)box.append(mk('p','small',blocked));
 return true;
}
