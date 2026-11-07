// Presentation and local batch preparation only. Host remains the binding/UV authority.
export const OWNER_HOSTS_MAX=12;
export const ROW_KEYS=['alias','hostname','user','port','public_key','fingerprint','peer','expires','owner'];
export const cleanRow=row=>Object.fromEntries(ROW_KEYS.map(k=>[k,row[k]]));
export function groupedRows(state){
 const groups=[],byKey=new Map();
 for(const row of state?.rows||[]){
  const key=state.valid===true&&row.owner==='self'&&typeof row.fingerprint==='string'&&row.fingerprint.startsWith('SHA256:')&&typeof row.public_key==='string'?row.fingerprint+'\n'+row.public_key:null;
  let group=key&&byKey.get(key);if(!group){group={rows:[]};groups.push(group);if(key)byKey.set(key,group);}group.rows.push(row);
 }
 return groups;
}
export function batchRows(state,drafts){
 const aliases=new Set(),removed=new Set();
 for(const {row,editingAlias}of drafts){
  if(aliases.has(row.alias))throw Error('duplicate');aliases.add(row.alias);
  if((state.rows||[]).some(old=>old.alias===row.alias&&old.alias!==editingAlias))throw Error('duplicate');
  if(editingAlias)removed.add(editingAlias);
 }
 const rows=[...(state.rows||[]).filter(row=>!removed.has(row.alias)).map(cleanRow),...drafts.map(d=>cleanRow(d.row))];
 if(rows.length>OWNER_HOSTS_MAX)throw Error('limit');
 return rows;
}
export async function validDraft(row,now=Date.now()/1000){
 if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(row.alias)||!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}$/.test(row.hostname)||!/^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/.test(row.user)||!Number.isInteger(row.port)||row.port<1||row.port>65535||!Number.isInteger(row.expires)||row.expires<=now||row.expires>now+90*86400||row.owner!=='self')return false;
 try{
  const [kind,key,...extra]=row.public_key.split(' ');if(kind!=='ssh-ed25519'||!key||extra.length)return false;
  const bytes=Uint8Array.from(atob(key),c=>c.charCodeAt(0)),prefix=[0,0,0,11,115,115,104,45,101,100,50,53,53,49,57,0,0,0,32];
  if(bytes.length!==51||!prefix.every((v,i)=>bytes[i]===v))return false;
  const digest=new Uint8Array(await crypto.subtle.digest('SHA-256',bytes));
  return row.fingerprint==='SHA256:'+btoa(String.fromCharCode(...digest)).replace(/=+$/,'');
 }catch{return false;}
}
export function reviewRows(rows){return rows.map(row=>`${row.alias} · ${row.user}@${row.hostname}:${row.port}\n${row.public_key}\n${row.fingerprint}\n${row.peer||'—'} · ${row.expires}`).join('\n\n');}
