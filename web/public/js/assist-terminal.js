// #468: customer-only consent, read-only observation and approvals. Never an owner terminal or a helper input channel.
import { request, done, channel, isReady, peer, sendApp, gen } from './session.js';
import { signedWrite } from './controls.js';
import { controlMessage, controlObject, sealValue, elevateFields, elevateDigest, elevateMessage, sha256hex, b64u, unb64u } from '../proto/wire.js';
import { assertPrf, salt32 } from './vault.js';
import { signKey } from './store.js';
import { myDeviceId } from './api.js';
import { approve, canAssert, ownKey, elevateChallenge } from './faceid.js';
import { registerLayer } from './layers.js';
import { t, onLang } from './t.js';
import { copyable } from './copycode.js';
import { el, mk as nativeMk, toast } from './ui.js';
// Exact factory for this schema/view; ordinary aj-action controls remain independent actors.
function mk(tag, cls, text) {
  cls=cls?.replace(/\baj-btn(?=\b|--)/g,'aj-action');
  const action=typeof cls==='string'&&cls.split(/\s+/).includes('aj-action');
  return nativeMk(tag,cls,text,action?'aj-action':'aj-terminal',action?'dialog':undefined);
}

export const MINUTES = [15, 30, 60], DEFAULT_MINUTES = 30;
const ID = /^[A-Za-z0-9._:-]{1,80}$/, NONCE = /^[a-f0-9]{32}$/;
const fresh = () => ({ active: false, id: '', until: 0, key_id: '', connected: false, discovery: 'not_started' });
let grantFa = '', challengeTimer = 0;
let state = fresh(), minutes = DEFAULT_MINUTES, challenge = null, granting = false, revoking = false;
let opened = false, root, timer, terminal = null, terminalId = '', seq = 0, pending = null, faId = '', report = null;
let logGid = '', logPage = null, logRequest = null, logError = '', logPageNumber = 0;
let login = null, retrying = false, retryAt = 0;
let grid = {cols:80,rows:24};
let load = null, generation = 0, signing = false, output = '', grantRequest = null;
export function parseState(m, now = Date.now()) {
  if (!m || typeof m !== 'object' || m.scope !== 'terminal') return null;
  const until = Number.isFinite(m.until) ? (m.until < 1e12 ? m.until * 1000 : m.until) : 0;
  if (m.active !== true || until <= now || !ID.test(m.id || '')) return fresh();
  return { active: true, id: m.id, until, key_id: ID.test(m.key_id || '') ? m.key_id : '', connected: m.connected === true, discovery: ['publishing','ready','failed'].includes(m.discovery) ? m.discovery : 'not_started' };
}
export function validEvent(m, s, id, last = 0) {
  if (!s.active || s.until <= Date.now() || m?.gid !== s.id || !m.event || typeof m.event.t !== 'string') return false;
  const e = m.event;
  if (e.t === 'term_ready') return ID.test(e.id || '');
  if (!id || e.id !== id) return false;
  if (e.t === 'term_out') return typeof (e.d ?? e.data) === 'string' && (e.d ?? e.data).length <= 65536 && (Number.isInteger(e.seq) && e.seq > last);
  return ['term_confirm', 'term_sudo', 'term_close', 'term_resize'].includes(e.t);
}
export function grantObject(n, c) { return { minutes: n, key_id: c.key_id, seat: c.seat, scope: 'terminal' }; }
const B = (id, key, fn) => { const b = mk('button', 'aj-btn', t(key)); b.type = 'button'; b.id = id; b.addEventListener('click', fn); return b; };
const text = (tag, key, cls = '') => mk(tag, cls, t(key));
function ensure() {
  if (root) return;
  const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = new URL('../assist-terminal.css', import.meta.url).href; document.head.append(css);
  root = mk('section', 'ast'); root.id = 'assist-terminal-view'; root.hidden = true; root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-labelledby', 'ast-title');
  const head = mk('header', 'ast__head'); const title = text('h2', 'p468.viewerTitle'); title.id = 'ast-title';
  head.append(title, B('ast-stop', 'p468.stop', revoke), B('ast-close', 'p468.close', close));
  const status = mk('p'); status.id = 'ast-status'; status.setAttribute('role', 'status');
  const note = text('p', 'p468.observe'); note.id = 'ast-note'; const host = mk('div', 'ast__screen'); host.id = 'ast-screen';
  const wait = text('p', 'p468.wait'); wait.id = 'ast-wait';
  const card = mk('section', 'ast__confirm'); card.id = 'ast-confirm'; card.hidden = true; card.setAttribute('aria-live', 'polite');
  const summary = mk('pre'); summary.id = 'ast-command'; summary.setAttribute('data-cc', 'block');
  card.append(text('h3', 'p468.confirm'), copyable(summary), B('ast-yes', 'p468.allow', allow), B('ast-no', 'p468.deny', deny));
  const log = B('ast-log', 'p468.log', readLog); const result = mk('pre', 'ast__report'); result.id = 'ast-report'; result.setAttribute('data-cc', 'block');
  const lc = mk('section', 'ast__login'); lc.id = 'ast-login'; lc.hidden = true; lc.setAttribute('aria-labelledby','ast-login-title');
  const lh = text('h3','p408.loginTitle'); lh.id = 'ast-login-title'; const lp = text('p','p408.pwNote'); lp.id = 'ast-login-note';
  const label = text('label','p408.loginLabel'); label.htmlFor = 'ast-login-password'; label.id = 'ast-login-label';
  const pw = document.createElement('input'); pw.setAttribute('data-aj-owner','aj-terminal'); pw.type = 'password'; pw.id = 'ast-login-password'; pw.autocomplete = 'off'; pw.setAttribute('autocapitalize','off'); pw.setAttribute('spellcheck','false');
  const bad = mk('p'); bad.id = 'ast-login-bad'; bad.setAttribute('role','status');
  lc.append(lh,lp,label,pw,bad,B('ast-login-yes','p408.pwSend',()=>loginAnswer(true)),B('ast-login-no','sheet.cancel',()=>loginAnswer(false)));
  const discovery = mk('p'); discovery.id='ast-discovery'; discovery.setAttribute('role','status');
  const retry = B('ast-discovery-retry','p468.discoveryRetry',retryDiscovery);
  const logs=mk('section','ast__logs');logs.id='ast-logs';logs.hidden=true;
  const logHeading=text('h3','p468.logPageTitle');logHeading.id='ast-log-title';
  const logStatus=mk('p');logStatus.id='ast-log-status';logStatus.setAttribute('role','status');
  const logMeta=mk('details'),logSummary=text('summary','p468.logRange');logSummary.id='ast-log-range-label';const logScope=mk('p');logScope.id='ast-log-scope';logMeta.append(logSummary,logScope);
  const logText=mk('pre','ast__log-text');logText.id='ast-log-text'; logText.setAttribute('data-cc', 'block');
  const earlier=B('ast-log-earlier','p468.logEarlier',()=>fetchLog(true));
  logs.append(logHeading,logStatus,logMeta,copyable(logText),earlier);
  root.append(head, status, discovery, retry, note, wait, lc, host, card, log, logs, copyable(result)); document.body.append(root);
  registerLayer({ element: () => root, open: () => opened, close, fallback: () => el('ast-open'), rank: 65 });
}
function open() { // @ix app.assist_terminal_view
 ensure(); opened = true; root.hidden = false; paint(); el('ast-close')?.focus({ preventScroll: true }); mount(); }
function close() { if(el('ast-login-password'))el('ast-login-password').value=''; opened = false; if (root) root.hidden = true; el('ast-open')?.focus({ preventScroll: true }); }
async function mount() {
  if (terminal || !opened) return;
  const g = generation;
  try {
    load ||= import('./assist-viewer.js');
    const m = await load;
    if (g !== generation || terminal || !opened) return;
    terminal = m.mount(el('ast-screen'), grid);
    if (output) terminal.write(output);
  } catch { toast(t('p468.viewerFailed'), 6000); }
}
function clearLive() {
  clearTimeout(challengeTimer); challengeTimer=0;
  clearLogin();
  generation++; grid = {cols:80,rows:24}; pending = null; signing = false; terminalId = ''; seq = 0; output = '';
  terminal?.dispose(); terminal = null; faId = ''; challenge = null;
}
export function onState(m) {
  const s = parseState(m);
  // Shared task-grant end notifications must also close a former terminal scope.
  if (!s) { if ((m?.active === false || m?.active === true) && state.active) { state = fresh(); clearLive(); paint(); } return; }
  if (s.active && state.id === s.id && typeof m.connected !== 'boolean') s.connected = state.connected;
  if(s.active && state.id!==s.id){resetLog(s.id);report=null;}
  if (!s.active || state.id !== s.id) clearLive();
  if (s.active) faId = grantFa || ownKey() || '';
  state = s; paint(); if (state.active && opened) mount();
}
export function onEvent(m) {
  if (!validEvent(m, state, terminalId, seq)) return;
  const e = m.event;
  if (e.t === 'term_ready') {
    if (terminalId && terminalId !== e.id) { terminal?.dispose(); terminal = null; output = ''; }
    if (terminalId !== e.id) seq = 0;
    terminalId = e.id; state.connected = true;
    resizeLocal(e);
    if (typeof e.fa === 'string') faId = e.fa;
    if (opened) mount();
  } else if (e.t === 'term_out') {
    if (Number.isInteger(e.seq)) seq = e.seq;
    const d = e.d ?? e.data; output = (output + d).slice(-262144); terminal?.write(d);
  } else if (e.t === 'term_resize') { resizeLocal(e);
  } else if (e.t === 'term_confirm') {
    if (typeof e.line !== 'string' || e.line.length > 16384 || !NONCE.test(e.n || '') || !Number.isInteger(e.seq)) return;
    if (e.uv !== 0 && (typeof e.fa_ch !== 'string' || !(e.fa || faId))) return;
    pending = { ...e, deadline: Date.now() + 120000 }; signing = false; open();
  } else if (e.t === 'term_sudo') {
    if (typeof e.rid !== 'string' || !ID.test(e.rid) || !NONCE.test(e.n || '') || !e.fa_ch || !(e.fa || faId) || !Array.isArray(e.argv) || !e.argv.every(x => typeof x === 'string')) return;
    pending = { ...e, deadline: Date.now() + 60000 }; signing = false; open();
  } else if (e.t === 'term_close') { state.connected = false; pending = null; signing = false; terminalId = ''; }
  paint();
}
function resizeLocal(e) {
  if (!Number.isInteger(e.cols) || !Number.isInteger(e.rows) || e.cols < 20 || e.cols > 400 || e.rows < 5 || e.rows > 200) return;
  grid = {cols:e.cols,rows:e.rows}; terminal?.resize(grid.cols,grid.rows);
}
export function onReport(m) {
  if (!m || !ID.test(m.gid || '') || !m.report || typeof m.report !== 'object') return;
  if (state.active && m.gid !== state.id) return;
  if(logGid && logGid!==m.gid)return;
  if(!logGid)logGid=m.gid;
  report = m.report; paint();
}
function clearLogin() {
  if (login?.value) login.value.fill(0);
  login = null;
  if (el('ast-login-password')) el('ast-login-password').value = '';
}
/** A trusted host marker binds this card to the current assist grant. Owner-terminal cards never enter here. */
export function validLogin(m, s, now = Date.now()) {
  return !!(s?.active && s.until > now && m?.kind === 'login' && m.assist_gid === s.id && m.term === s.id.slice(0,16) && NONCE.test(m.id || '') && NONCE.test(m.n || '') && typeof m.fa === 'string' && m.fa);
}
export async function onElev(m) {
  if (!validLogin(m,state)) return;
  let epk;
  try { epk=unb64u(m.epk); if(epk.length!==32) return; } catch { return; }
  if (m.state === 'running') { if(login?.id===m.id){login.busy=true;paint();} return; }
  clearLogin();
  const c = {id:m.id,n:m.n,epk,fa:m.fa,gid:state.id,term:m.term,ch:channel(),generation:gen(),busy:false,bad:Number.isInteger(m.bad)?m.bad:0,tries:Number.isInteger(m.tries)?m.tries:3,deadline:Date.now()+Math.min(600,Math.max(1,Number(m.ttl)||120))*1000};
  login = c;
  c.digest=await elevateDigest('login',elevateFields({kind:'login',term:c.term}));
  c.faCh=await elevateChallenge(c.ch,await myDeviceId(),c.id,'login',c.n,c.digest).catch(()=>null);
  if(login!==c || !state.active || state.id!==c.gid || c.generation!==gen()) return;
  open(); paintLogin(); el('ast-login-password')?.focus({preventScroll:true});
}
export function onElevResult(m) {
  if(!login || m?.id!==login.id) return false;
  if(m.t==='elev_done') { clearLogin(); paint(); return true; }
  if(m.t==='elev_refused') { login.busy=false;login.refused=true;paint();return true; }
  return false;
}
function paintLogin() {
  if(!root)return;
  if(login && (!state.active || login.gid!==state.id || login.deadline<=Date.now())) clearLogin();
  el('ast-login').hidden=!login;
  for(const [id,key] of [['ast-login-title','p408.loginTitle'],['ast-login-note','p408.pwNote'],['ast-login-label','p408.loginLabel'],['ast-login-yes','p408.pwSend'],['ast-login-no','sheet.cancel']])el(id).textContent=t(key);
  el('ast-login-yes').disabled=!login || login.busy || !login.faCh || !isReady();
  el('ast-login-no').disabled=!login || login.busy || !isReady();
  el('ast-login-password').disabled=!login || login.busy;
  el('ast-login-bad').textContent=login?.refused?t('p408.faRefused'):login?.bad?t('p408.pwBad',{n:login.tries}):'';
}
function loginAnswer(ok) { // @ix appr.assist_terminal_login
  const c=login;
  if(!c || c.busy || !state.active || state.id!==c.gid || state.until<=Date.now() || c.deadline<=Date.now() || c.generation!==gen())return;
  const input=el('ast-login-password');
  if(ok && !input.value){el('ast-login-bad').textContent=t('p408.pwEmpty');return;}
  let p;
  try{p=ok?approve(c.faCh,c.fa):Promise.resolve(null);}catch{p=Promise.reject(new Error('passkey'));}
  c.value=ok?new TextEncoder().encode(input.value):null;input.value='';c.busy=true;paintLogin();
  p.then(async fa=>{
    try {
      if(login!==c || !state.active || state.id!==c.gid || c.generation!==gen() || c.ch!==channel() || c.deadline<=Date.now())return;
      if(ok&&!fa)throw new Error('passkey');
      const sk=await signKey(),dev=await myDeviceId(),ts=Date.now();if(!sk)throw new Error('key');
      const msg={t:'elev_answer',id:c.id,ok,n:c.n,ts};let ctSha=null;
      if(ok){const sealed=await sealValue(c.epk,c.value,c.ch,dev,c.id,'login',c.n,c.digest);msg.epk=b64u(sealed.epk);msg.ct=b64u(sealed.ct);ctSha=sealed.ctSha;}
      msg.sig=b64u(new Uint8Array(await crypto.subtle.sign({name:'Ed25519'},sk.priv,elevateMessage(c.ch,dev,c.id,'login',ok?'allow':'deny',c.n,ts,c.digest,ctSha))));
      if(fa)msg.fa=fa;
      if(login!==c || !state.active || state.id!==c.gid || c.generation!==gen() || c.ch!==channel() || c.deadline<=Date.now())return;
      await sendApp(msg,c.generation);
      if(login===c){c.sent=true;c.busy=true;paintLogin();}
    }finally{c.value?.fill(0);c.value=null;}
  }).catch(()=>{if(login===c){c.busy=false;c.refused=true;paintLogin();}});
}
function discoveryText() {
  if(state.connected)return t('p468.connected');
  return t('p468.discovery.'+(['publishing','ready','failed'].includes(state.discovery)?state.discovery:'not_started'));
}
export function onNotice(m) {
  if(!state.active)return;
  if(m?.what==='discoverable')state.discovery='ready';
  else if(m?.what==='discover_failed')state.discovery='failed';
  else return;
  paint();
}
async function retryDiscovery() { // @ix app.assist_terminal_discover_retry
  if(!state.active || retrying || Date.now()<retryAt || !isReady())return;
  retrying=true;retryAt=Date.now()+15000;paint();
  const g=state.id,r=await ask({t:'assist_discover_retry'});
  retrying=false;
  if(state.id===g && r.ok!==true)toast(t('p468.discovery.failed'),6000);
  paint();
}
async function sign(action, target, n) {
  const sk = await signKey(); if (!sk || !isReady()) throw new Error('offline');
  const ts = Date.now(); const d = await controlMessage(channel(), await myDeviceId(), action, n, ts, target);
  return { n, ts, sig: b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, d))) };
}
function ask(obj) {
  return new Promise(resolve => {
    let r; const tm = setTimeout(() => { if (r) done(r); resolve({ ok: false, why: 'timeout' }); }, 20000);
    r = request(obj, m => {
      if (!['ctl_res', 'offline', 'assist_log_res'].includes(m.t)) return;
      clearTimeout(tm); done(r); resolve(m);
    });
    if (!r) { clearTimeout(tm); resolve({ ok: false, why: 'offline' }); }
  });
}
async function prepare() {
  challenge = null;
  const n = minutes, ch = channel(), token = grantRequest = {};
  paintCard(el('rs-help-body'));
  const c = await ask({ t: 'assist_term_grant', minutes: n, scope: 'terminal' });
  if (grantRequest !== token || n !== minutes || ch !== channel()) return;
  grantRequest = null;
  if (c.ok !== false || c.why !== 'passkey_required' || !NONCE.test(c.n || '') || !ID.test(c.key_id || '') || !ID.test(String(c.seat || '')) || !c.fa || !c.fa_ch) {
    toast(t('p468.unavailable'), 6000); paintCard(el('rs-help-body')); return;
  }
  challenge = { ...c, seat: String(c.seat), minutes: n, channel: ch, at: Date.now() };
  const current=challenge;clearTimeout(challengeTimer);challengeTimer=setTimeout(()=>{if(challenge===current){challenge=null;paintCard(el('rs-help-body'));}},100001);
  paintCard(el('rs-help-body'));
}
function grant() { // @ix app.assist_terminal_grant
  const c = challenge;
  if (granting) return;
  if (!challengeTarget(c,minutes,channel())) { prepare(); return; }
  // Start WebAuthn inside the customer's click, before awaiting any other work.
  let p; try { p = approve(unb64u(c.fa_ch), c.fa); } catch { p = Promise.reject(new Error('passkey')); }
  granting = true; paintCard(el('rs-help-body'));
  p.then(async fa => {
    if (c !== challenge || c.channel !== channel()) return;
    const signed = await sign('assist_term_grant', grantObject(minutes, c), c.n);
    if (c !== challenge || c.channel !== channel()) return;
    const res = await ask({ t: 'assist_term_grant', minutes, scope: 'terminal', ...signed, fa });
    if (res.ok !== true) throw new Error('refused');
    grantFa = faId = c.fa; challenge = null;
    toast(t('p468.granted'), 5000);
  }).catch(() => { toast(t('p468.notGranted'), 5000); challenge = null; }).finally(() => { granting = false; paint(); });
}
async function revoke() { // @ix app.assist_terminal_stop
  if (!state.active || revoking) return;
  revoking = true; paint();
  const r = await signedWrite('assist_revoke', {}, { t: 'assist_revoke' });
  revoking = false;
  if (r.ok === true) { state = fresh(); clearLive(); toast(t('p468.ended'), 5000); }
  else toast(t('p468.stopFailed'), 6000);
  paint();
}
function answer(obj) { return ask({ t: 'assist_term_answer', gid: state.id, obj }); }
async function deny() {
  const c = pending;
  if (!c || !state.active) return;
  pending = null; signing = false; paint();
  await answer(c.t === 'term_sudo' ? { t: 'term_sudo_no', id: terminalId, rid: c.rid } : { t: 'term_confirm_no', id: terminalId, seq: c.seq, n: c.n });
}
function allow() { // @ix appr.assist_terminal_answer
  const c = pending, gid = state.id, id = terminalId;
  if (!c || signing || !state.active || state.until <= Date.now() || c.deadline <= Date.now() || !isReady()) return;
  let p; try { p = c.t === 'term_sudo' ? assertPrf(unb64u(c.fa_ch), c.fa || faId, salt32(c.salt), salt32(c.salt) ? salt32(c.vn) : null) : (c.uv === 0 ? Promise.resolve({fa: undefined, prf: null}) : approve(unb64u(c.fa_ch), c.fa || faId).then(fa => ({fa,prf:null}))); } catch { p = Promise.reject(new Error('passkey')); }
  signing = true; paint();
  p.then(async ({fa, prf}) => {
    try {
    if (pending !== c || !state.active || state.until <= Date.now() || state.id !== gid || terminalId !== id || c.deadline <= Date.now()) return;
    const action = c.t === 'term_sudo' ? 'term_sudo' : 'term_line';
    const obj = c.t === 'term_sudo' ? {id, rid: c.rid} : {id, line: c.line};
    const signed = await sign(action, obj, c.n);
    let sealed = {};
    if (prf) {
      if (typeof c.epk !== 'string' || unb64u(c.epk).length !== 32) throw new Error('shape');
      const digest = await sha256hex(new TextEncoder().encode(controlObject(action, obj)));
      const v = await sealValue(unb64u(c.epk), prf, channel(), await myDeviceId(), c.n, 'term_prf', c.n, digest);
      sealed = {pepk:b64u(v.epk),pct:b64u(v.ct)};
    }
    if (pending !== c || !state.active || state.until <= Date.now() || state.id !== gid || terminalId !== id || c.deadline <= Date.now()) return;
    const r = await answer({ t: c.t === 'term_sudo' ? 'term_sudo_ok' : 'term_line', ...obj, ...(c.t === 'term_confirm' ? {seq:c.seq} : {}), ...signed, ...(fa ? { fa } : {}), ...sealed });
    if (r.ok !== true) toast(t('p468.notRun'), 5000);
    if (pending === c) pending = null;
    } finally { prf?.fill(0); }
  }).catch(() => toast(t('p468.notRun'), 5000)).finally(() => { signing = false; paint(); });
}
function resetLog(gid = '') {
  if(logRequest){clearTimeout(logRequest.timer);if(logRequest.r)done(logRequest.r);}
  logRequest=null;logGid=gid;logPage=null;logError='';logPageNumber=0;
}
export function parseLogPage(m, expectedGid = '', requestedCursor = null) {
  if(!m || m.t!=='assist_log_res')return null;
  if(m.gid && expectedGid && m.gid!==expectedGid)return null;
  if(['bad_cursor','pruned','shape','not_owner'].includes(m.why))return {error:m.why};
  if(!ID.test(m.gid||'') || !Array.isArray(m.entries) || m.entries.length>200 || typeof m.more!=='boolean')return {error:'shape'};
  if(m.entries.some(e=>!e||typeof e!=='object'||Array.isArray(e)||(e.gid!==undefined&&e.gid!==m.gid)))return {error:'shape'};
  if(JSON.stringify(m.entries).length>131072)return {error:'shape'};
  const next=typeof m.next_cursor==='string'&&m.next_cursor?m.next_cursor:null;
  if(m.more && (!next || next.length>1024 || /[\x00-\x1f\x7f]/.test(next) || next===requestedCursor))return {error:'shape'};
  if(m.report && (typeof m.report!=='object' || (m.report.id!==undefined&&m.report.id!==m.gid)))return {error:'shape'};
  return {gid:m.gid,entries:m.entries,report:m.report||null,more:m.more,next_cursor:m.more?next:null,cursor:requestedCursor};
}
async function readLog() { // @ix app.assist_terminal_log
  return fetchLog(false);
}
function fetchLog(earlier = false) { // @ix app.assist_terminal_log_earlier
  if(!isReady())return toast(t('p468.offline'),5000);
  if(logRequest || (earlier && (!logPage?.more || !logPage.next_cursor)))return;
  const expected=state.active?state.id:logGid||(ID.test(report?.id||'')?report.id:'');
  const cursor=earlier?logPage.next_cursor:null;
  const req={gid:expected,cursor,r:null,timer:0};logRequest=req;logError='';
  const finish=()=>{clearTimeout(req.timer);if(req.r)done(req.r);if(logRequest===req)logRequest=null;};
  req.r=request({t:'assist_log',...(expected?{gid:expected}:{}),...(cursor?{cursor}:{})},m=>{
    if(logRequest!==req)return;
    if(m.t==='offline'){finish();logError='offline';paint();return;}
    if(m.t!=='assist_log_res')return;
    const page=parseLogPage(m,req.gid,req.cursor);
    if(page===null)return;
    finish();
    if(page.error){logError=page.error;paint();return;}
    if((state.active&&page.gid!==state.id)||(logGid&&page.gid!==logGid))return;
    logGid=page.gid;logPage=page;logPageNumber=earlier?logPageNumber+1:1;
    if(page.report)report=page.report;
    paint();
  });
  if(!req.r){logRequest=null;logError='offline';paint();return;}
  req.timer=setTimeout(()=>{if(logRequest!==req)return;finish();logError='timeout';paint();},20000);
  paint();
}
function paintLog() {
  el('ast-log').disabled=!!logRequest || !isReady();
  el('ast-log').textContent=t(logPage?'p468.logLatest':'p468.log');
  el('ast-logs').hidden=!logPage&&!logRequest&&!logError;
  el('ast-log-title').textContent=t('p468.logPageTitle');el('ast-log-range-label').textContent=t('p468.logRange');
  const status=logError?t('p468.logError.'+logError):logRequest?t('p468.logLoading'):logPage?t('p468.logPageInfo',{n:logPageNumber,count:logPage.entries.length})+' '+t(logPage.more?'p468.logHasEarlier':'p468.logStart'):'';
  el('ast-log-status').textContent=status;
  el('ast-log-scope').textContent=logPage?logScopeText(logPage,logPageNumber):'';
  el('ast-log-text').textContent=logPage?logEntriesText(logPage.entries):'';
  el('ast-log-earlier').textContent=t('p468.logEarlier');el('ast-log-earlier').hidden=!logPage?.more;el('ast-log-earlier').disabled=!!logRequest||!!logError||!isReady();
}
export function logScopeText(page, number) {
  const times=page.entries.map(e=>e.ts||e.at).filter(v=>Number.isFinite(v)).map(v=>new Date(v*(v<1e12?1000:1)).toLocaleString());
  const gid=page.gid.length>8?page.gid.slice(0,8)+'…':page.gid;
  return t('p468.logScope',{gid,n:number,from:times[0]||t('p468.logUnknownTime'),to:times.at(-1)||t('p468.logUnknownTime')});
}
export function logEntriesText(entries) {
  const clean=s=>String(s).replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,'').replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g,'').replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g,'');
  const lines=[];
  for(const e of entries){
    const ev=typeof e.ev==='string'?e.ev.replace(/^terminal_/,''):'';
    const label=['open','output','action','input','report','end','grant','command'].includes(ev)?ev:'other';
    const result=['allow','deny','ready','done','refused'].includes(e.result)?e.result:e.result?'other':'';
    const at=Number.isFinite(e.ts||e.at)?new Date((e.ts||e.at)*((e.ts||e.at)<1e12?1000:1)).toLocaleString():'';
    const words=['line','text','command','data','action','why','result'].filter(k=>typeof e[k]==='string').map(k=>clean(e[k]));
    lines.push([at,t('p468.event.'+label),...(result?[t('p468.result.'+result)]:[]),...words].filter(Boolean).join(' · '));
  }
  return lines.join('\n');
}
function paint() {
  ensure();
  if (state.active && state.until <= Date.now()) { state = fresh(); clearLive(); }
  for (const [id,key] of [['ast-title','viewerTitle'],['ast-note','observe'],['ast-stop','stop'],['ast-close','close'],['ast-yes','allow'],['ast-no','deny'],['ast-log','log']]) el(id).textContent = t('p468.'+key);
  el('ast-confirm').firstElementChild.textContent = t('p468.confirm');
  const left = Math.max(0, Math.ceil((state.until - Date.now()) / 60000));
  el('ast-status').textContent = state.active ? t('p468.remaining', { n: left, who: state.key_id }) + ' · ' + t(state.connected ? 'p468.connected' : 'p468.wait') : t('p468.ended');
  el('ast-stop').disabled = !state.active || revoking;
  el('ast-wait').hidden = state.connected;
  if (pending && pending.deadline <= Date.now()) { pending = null; signing = false; }
  el('ast-confirm').hidden = !pending;
  if (pending) el('ast-command').textContent = pending.t === 'term_sudo' ? pending.argv.join(' ').slice(0, 4000) : pending.line;
  el('ast-yes').hidden = false;
  el('ast-yes').disabled = signing || !state.active || !isReady();
  el('ast-no').disabled = signing || !state.active;
  paintLogin();
  paintLog();
  el('ast-discovery').textContent = state.active && !state.connected ? discoveryText() : '';
  el('ast-discovery').hidden = !state.active || state.connected;
  el('ast-discovery-retry').hidden = !state.active || state.connected || state.discovery !== 'failed';
  el('ast-discovery-retry').textContent = t('p468.discoveryRetry');
  el('ast-discovery-retry').disabled = retrying || Date.now() < retryAt || !isReady();
  el('ast-report').hidden = !report;
  el('ast-report').parentElement.hidden = !report;
  el('ast-report').textContent = report ? t('p468.reportIsSummary')+'\n'+reportText(report) : '';
  document.body.toggleAttribute('data-assist-terminal', state.active);
  for (const id of ['ast-banner','ast-float']) {
    let banner = el(id);
    if (!banner) {
      banner = mk('div', 'ast__banner' + (id === 'ast-float' ? ' ast__banner--float' : '')); banner.id = id;
      banner.append(B(id+'-open', 'p468.view', open), B(id+'-stop', 'p468.stop', revoke));
      const slot = el('rescue-banner');
      if (id === 'ast-banner' && slot?.parentElement) slot.before(banner); else document.body.append(banner);
    }
    banner.hidden = !state.active || opened; el(id+'-stop').disabled = revoking; el(id+'-stop').textContent = t('p468.stop');
    el(id+'-open').textContent = t('p468.banner', { n: left });
  }
  paintCard(el('rs-help-body'));
  clearTimeout(timer); if (state.active) timer = setTimeout(paint, 1000);
}
export function challengeTarget(c, selected, currentChannel, now = Date.now()) {
  if(!c || c.minutes!==selected || c.channel!==currentChannel || !Number.isFinite(c.at) || now<c.at || now-c.at>100000 || !ID.test(c.key_id||'') || !ID.test(c.seat||''))return null;
  return {key_id:c.key_id,seat:c.seat,minutes:c.minutes};
}
export function paintCard(body) {
  if (!body) return;
  let box = el('ast-card');
  if (!peer.assist_term1 && !state.active && !report) { box?.remove(); return; }
  ensure();
  if (!box) { box = mk('section', 'ast__intro'); box.id = 'ast-card'; }
  if (box.parentNode !== body) body.append(box);
  const nodes = [text('h5', 'p468.title'), text('p', 'p468.explain')];
  if (state.active) { const status=mk('p','',discoveryText()); status.setAttribute('role','status'); nodes.push(status,B('ast-open', 'p468.view', open), B('ast-card-stop', 'p468.stop', revoke)); if(state.discovery==='failed'){const r=B('ast-card-retry','p468.discoveryRetry',retryDiscovery);r.disabled=retrying||Date.now()<retryAt||!isReady();nodes.push(r);} }
  else {
    if (peer.assist_term1) {
      const chips = mk('div', 'ast__durations'); chips.setAttribute('role', 'radiogroup'); chips.setAttribute('aria-label', t('p468.duration'));
      for (const n of MINUTES) { const b = B('ast-min-' + n, 'p468.duration', () => { minutes = n; challenge = null; grantRequest = null; prepare(); }); b.className='ast-duration';b.setAttribute('data-aj-owner','aj-terminal');b.removeAttribute('data-aj-action-context');b.textContent = t('p468.minutes', { n }); b.setAttribute('role', 'radio'); b.setAttribute('aria-checked', String(minutes === n)); b.disabled = granting; chips.append(b); }
      const target=challengeTarget(challenge,minutes,channel());
      nodes.push(chips);
      if(target){
        const details=mk('dl','ast__authorization');details.id='ast-authorization-target';
        for(const [key,value] of [['officialIdentity',target.key_id],['targetComputer',target.seat],['scope',t('p468.scopeTerminal')],['duration',t('p468.minutes',{n:target.minutes})]]){
          const term=text('dt','p468.'+key),valueNode=mk('dd','',value);valueNode.dataset.field=key;details.append(term,valueNode);
        }
        nodes.push(details);
      }
      const b = B('ast-grant', target ? 'p468.authorize' : 'p468.prepare', target ? grant : prepare); b.disabled = granting || !!grantRequest || !canAssert() || !isReady();
      nodes.push(b);
    }
    if (report || logPage) nodes.push(B('ast-open', report?'p468.report':'p468.log', open));
  }
  box.replaceChildren(...nodes);
}
export function gone() { if(logRequest){clearTimeout(logRequest.timer);if(logRequest.r)done(logRequest.r);logRequest=null;logError='offline';} clearLogin(); pending = null; signing = false; challenge = null; grantRequest = null; state.connected = false; terminalId = ''; if (root) paint(); }
export function reportText(r) {
  const lines = [];
  const dt = value => Number.isFinite(value) ? new Date(value * (value < 1e12 ? 1000 : 1)).toLocaleString() : '';
  if (typeof r.summary === 'string') lines.push(r.summary.slice(0, 8000));
  if(r.official_statement && typeof r.official_statement==='object'){
    lines.push(t('p468.officialStatement'));
    for(const key of ['summary','diagnosis','verification'])if(typeof r.official_statement[key]==='string')lines.push(t('p468.statement.'+key)+': '+r.official_statement[key].slice(0,8000));
  }
  if(Number.isInteger(r.operations_shown)&&Number.isInteger(r.operations_total)&&r.operations_shown>=0&&r.operations_total>=r.operations_shown)lines.push(t('p468.summaryCount',{shown:r.operations_shown,total:r.operations_total}));
  if(Array.isArray(r.operations)){
    lines.push(t('p468.operationList'));
    for(const op of r.operations.slice(-200)){
      if(!op||typeof op!=='object'||typeof op.command!=='string')continue;
      const result=['confirmed','refused','done','failed','unknown','requested','running'].includes(op.state)?op.state:'unknown';
      const exit=Number.isInteger(op.exit_status)?String(op.exit_status):t('p468.exitUnknown');
      lines.push([dt(op.at),op.command.slice(0,4000),t('p468.operation.'+result),t('p468.exitStatus',{value:exit})].filter(Boolean).join(' · '));
    }
  }
  if (r.started_at) lines.push(t('p468.reportStarted', {value: dt(r.started_at)}));
  if (r.ended_at) lines.push(t('p468.reportEnded', {value: dt(r.ended_at)}));
  if (ID.test(r.key_id || '')) lines.push(t('p468.reportHelper', {value:r.key_id}));
  if (typeof r.reason === 'string') lines.push(t('p468.reportReason', {value:reasonText(r.reason)}));
  if (Number.isInteger(r.confirmed_actions) && Number.isInteger(r.refused_actions)) lines.push(t('p468.reportCounts', {yes:r.confirmed_actions,no:r.refused_actions}));
  if (Array.isArray(r.unresolved) && r.unresolved.length) lines.push(t('p468.reportUnresolved'));
  if (r.ended_at) lines.push(t('p468.reportNote'));
  for (const e of (Array.isArray(r.events) ? r.events.slice(-200) : [])) {
    if (!e || typeof e !== 'object') continue;
    const event = typeof e.ev === 'string' ? e.ev.replace(/^terminal_/, '') : '';
    const what = ['open','output','action','input','report','end','grant','command'].includes(event) ? event : 'other';
    const result = typeof e.result === 'string' ? e.result : '';
    const resultKey = ['allow','deny','ready','done','refused'].includes(result) ? result : 'other';
    const output = typeof e.data === 'string' ? e.data.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,'').replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g,'').replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g,'').slice(0,8192) : '';
    const words = ['line','text','command'].map(k => typeof e[k] === 'string' ? e[k].slice(0,4000) : '').filter(Boolean);
    const message = [t('p468.event.'+what), ...(result ? [t('p468.result.'+resultKey)] : []), ...words, output].filter(Boolean).join(' · ');
    lines.push([dt(e.ts || e.at), message].filter(Boolean).join(' · '));
  }
  return lines.join('\n').slice(0, 65536);
}
export function reasonText(reason) {
  const key = ['revoked','expired','closed','idle','estop','audit','ended','unpaired','seat','key','disconnected'].includes(reason) ? reason : 'ended';
  return t('p468.reason.'+key);
}

onLang(() => { if (root) paint(); });
export const _debug = () => ({ state: { ...state }, minutes, terminalId, pending: !!pending, seq, opened, log:logPage?{gid:logPage.gid,count:logPage.entries.length,more:logPage.more,number:logPageNumber,cursor:logPage.cursor,next_cursor:logPage.next_cursor}:null, logBusy:!!logRequest,logError, login:login ? {id:login.id,busy:login.busy,bad:login.bad} : null, fa: !!faId, output: output.length });
