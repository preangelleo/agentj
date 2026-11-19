// Presentation only: existing history provenance never becomes execution authority.
const BACKGROUND = new Set(['heartbeat','quota','hook','api','mcp','loop','task','background_done','auto']);
const MERGED = new Set(["这条和下一条回复一起回您。", "I'll answer this together with the next reply."]);
export function mergedTurn(p){
  return ['phone','host'].includes(p?.source?.k) && p.end === 'done' &&
    (p.card?.merged === true || MERGED.has(p.reply?.trim()));
}
export function activityKind(p){
  const s=p?.source;
  if (!s || s.input_channel || p.card?.channel || s.k==='friend_report' || s.k==='support') return null;
  // Requests and recovery controls remain on their existing actionable surface.
  const keys=Object.keys(p.card || {});
  if (p.login || p.form || p.media?.length || s.repair || s.guide || keys.some(k=>!['merged','queued','wait','busy_stalled','notice','activity_failure'].includes(k))) return null;
  if (s.k==='sys') return 'system';
  if (s.k==='auto' || s.k==='task' || s.k==='agent' && BACKGROUND.has(s.via)) return 'background';
  return null;
}
// The history source records who triggered the turn. Its final reply can still be the main Agent's.
export function backgroundReport(p){
  if (p?.end !== 'done' || p.silent || !p.reply?.trim() || pendingAutomation(p)) return false;
  const source = p.source;
  if (source?.input_channel || p.card?.channel) return false;
  return source?.k === 'agent' && BACKGROUND.has(source.via) ||
    source?.k === 'auto' && source.source_kind === 'automation' && source.source_role === 'data';
}
export function activityFailed(p){return p?.card?.activity_failure===true || p?.end==='failed' || Array.isArray(p?.card?.busy_stalled);}
export function mergeOwnerInputs(rows){
  const out = [];
  let pending = [];
  for (const turn of rows){
    if (mergedTurn(turn)){
      pending.push(out.length);
      out.push({...turn, reply:''});
      continue;
    }
    if (pending.length && ['phone','host'].includes(turn.source?.k)){
      const inputs = pending.map(i => out[i]);
      const sources = [...inputs.map(row => row.source), turn.source];
      for (const i of pending) out[i] = null;
      out.push({...turn, mergedIds:[...inputs.map(row => row.id),turn.id],
        source:{...turn.source,text:sources.map(s=>s?.text || '').filter(Boolean).join('\n\n'),att:sources.flatMap(s=>s?.att || [])}});
      pending = [];
    } else {
      // A completed background report can land between related owner messages.
      if (pending.length && !backgroundReport(turn)) pending = [];
      out.push(turn);
    }
  }
  return out.filter(Boolean);
}

// 0332: host-normalized provenance is presentation data, never execution authority.
const OWNER_KINDS = new Set(['phone','terminal','wechat','telegram','email']);
export function ownerInput(p){
  const s=p?.source;
  if (!s || s.via==='hook') return false;
  if (typeof s.source_kind==='string' || typeof s.source_role==='string')
    return s.source_role==='owner' && OWNER_KINDS.has(s.source_kind);
  // Old hosts identify paired phone and native terminal inputs unambiguously.
  // Channel kind alone also describes visitors, so it is insufficient.
  return s.k==='phone' || s.k==='host';
}
function pendingQueue(p){
  if (['failed','stopped'].includes(p?.end)) return false;
  return p?.card?.queued===true && p.end==='open' || p?.card?.held==='pending' || p?.card?.held?.state==='pending';
}
export function ownerQueue(p){return ownerInput(p) && pendingQueue(p);}
export function pendingAutomation(p){
  if (ownerInput(p) || ['failed','stopped'].includes(p?.end)) return false;
  const c=p?.card || {};
  // Rejected requests and actionable recovery/approval cards retain their existing surface.
  if (p?.login || p?.form || p?.media?.length || p?.source?.repair || p?.source?.guide ||
      Object.keys(c).some(k=>!['queued','held','waited_s','stuck','wait','notice','detail','activity_failure','busy_stalled'].includes(k))) return false;
  return pendingQueue(p) || ['open','done'].includes(p?.end) && !p?.reply?.trim();
}

// R-0005 (0.30.31, chairman approval pending — relay.js keeps it off by default): one line that says what a
// background record is about. Display only: it reads the same host-projected history text the row already shows on
// expansion, never a new field. Agent-started records lead with the Agent's words (their prompt is a fixed check
// text); system and automation records lead with the message that arrived. JSON, code fences and markup are skipped.
const MARKUP = /^(?:#{1,6}\s+|[-*+]\s+|>\s*|\d+[.)]\s+)+/;
function oneLine(v){
  const s = typeof v === 'string' ? v : typeof v?.text === 'string' ? v.text : '';
  let fence = false;
  for (const raw of s.split('\n')){
    if (raw.trim().startsWith('```')){ fence = !fence; continue; }
    if (fence || /^[-=*_\s]{3,}$/.test(raw.trim())) continue;
    const l = raw.replace(MARKUP, '').replace(/\*\*|__|`/g, '').replace(/\s+/g, ' ').trim();
    if (!l || /^\{|^\[\s*[[{"\d\]]/.test(l)) continue;
    return l.length > 160 ? l.slice(0, 159) + '…' : l;
  }
  return '';
}
export function activitySummary(p, clean = x => x){
  const text = p?.source?.text, reply = clean(typeof p?.reply === 'string' ? p.reply : p?.reply?.text || '');
  const order = p?.source?.k === 'agent' ? [reply, text] : [text, reply];
  for (const v of order){ const l = oneLine(v); if (l) return l; }
  return '';
}
