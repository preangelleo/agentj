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
export function activityFailed(p){return p?.card?.activity_failure===true || p?.end==='failed' || Array.isArray(p?.card?.busy_stalled);}
export function mergeOwnerInputs(rows){
  const out=[]; let pending=[];
  for (const p of rows){
    if (mergedTurn(p)){pending.push(p);continue;}
    if (pending.length && ['phone','host'].includes(p.source?.k)) {
      const sources=[...pending.map(x=>x.source),p.source];
      out.push({...p,mergedIds:[...pending.map(x=>x.id),p.id],source:{...p.source,text:sources.map(x=>x?.text || '').filter(Boolean).join('\n\n'),
        att:sources.flatMap(x=>x?.att || [])}}); pending=[];
    } else {out.push(...pending.map(x=>({...x,reply:''})),p);pending=[];}
  }
  out.push(...pending.map(x=>({...x,reply:''})));
  return out;
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
