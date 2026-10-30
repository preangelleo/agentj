// P414 contract — the PHONE half, as a JSON-lines co-process of p414_contract_host.py (which drives the REAL host classes).
// Everything here is the page's own code: proto/wire.js (seal, control-v1 / §11 signatures, digests), js/vault.js (prfJoin,
// sealPrf, salt32), js/faceid.js (elevateChallenge), and — evaluated from their source in a vm, since the modules need the
// page — elevate.js add() / vaultOf() / done(), bang.js bangLine() / termOf(), vaultrow.js parse() / state(), terminal.js
// onBang() and its CTRL rule. What a tap does next (decide(), introYes(), share(), the confirm sheet) is composed here from
// those same functions in the order the page uses them; p414.mjs walks the real page against the fake host for the rest.
// The authenticator is simulated: PRF(salt) = HMAC-SHA256(per-phone secret, salt); clientDataJSON carries the challenge the
// page would have asked for, and the host checks it (passkey.client_data). One request per line in, one answer per line out.
import { createInterface } from 'node:readline';
import { readFileSync } from 'node:fs';
import { createHmac, createPrivateKey, randomBytes, sign as edSign } from 'node:crypto';
import vm from 'node:vm';
import * as wire from '../public/proto/wire.js';
import { prfJoin, sealPrf, salt32 } from '../public/js/vault.js';
import { elevateChallenge } from '../public/js/faceid.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const ELEV = read('../public/js/elevate.js'), BANG = read('../public/js/bang.js'), VROW = read('../public/js/vaultrow.js'), TERM = read('../public/js/terminal.js');
const enc = new TextEncoder();
const strip = (s) => s.replace(/^export /gm, '');
const between = (src, a, b) => { const i = src.indexOf(a), j = src.indexOf(b, i + 1); if (i < 0 || j < 0) throw new Error('source moved: ' + a); return src.slice(i, j); };

// ---------------------------------------------------------------- the page's parsers, from source
const bangBox = {};
vm.runInNewContext(strip(between(BANG, 'const HEAD = ', '/** relay.js say()')) + strip(between(BANG, "const STATES = ['done'", 'let shownFor'))
  + '\nglobalThis.bangLine = bangLine; globalThis.UNSAFE = UNSAFE; globalThis.termOf = termOf;', bangBox);
const vrowBox = {};
vm.runInNewContext(strip(between(VROW, 'const HEX32', 'export const vaultInfo')) + '\nlet info = null;\nglobalThis.parse = parse; globalThis.state = state;', vrowBox);
const CTRL = new RegExp(TERM.match(/export const CTRL = \/(.+)\/;/)[1]);
/** terminal.js onBang() with its collaborators recorded. */
function onBangOf(S, calls) {
  const box = { S, calls, t: (k) => k, toast: (k) => calls.push(['toast', k]), restore: (l) => calls.push(['restore', l]),
    bangEnd: (sid) => { if (S.bang && S.bang.sid === sid) S.bang = null; },
    notRun: () => {}, programWaits: () => calls.push(['programWaits']) };   // P414 (R3-1)
  vm.runInNewContext(between(TERM, 'function onBang(m)', '/** term_go {cmd?, h}') + '\nglobalThis.onBang = onBang;', box);
  return box.onBang;
}

// per phone: keys, its own copy of elevate.js's card table
const phones = new Map();
function phone(name) { const p = phones.get(name); if (!p) throw new Error('no phone ' + name); return p; }
function elevBox(p) {
  const box = { unb64u: wire.unb64u, salt32, elevateDigest: wire.elevateDigest, elevateFields: wire.elevateFields, elevateChallenge,
    channel: () => p.channel, myDeviceId: async () => p.device, notified: [], t: (k) => k, Date };
  const src = 'let shownId = null, busy = false, timer = 0;\nconst cards = new Map(), completed = new Set();\nfunction render() {}\n'
    + 'function notify(x) { notified.push(x); }\nfunction clearTimeout() {}\n'
    + strip(between(ELEV, 'const str = (v, n)', '/** The computer did not accept'))
    + '\nglobalThis.add = add; globalThis.done = done; globalThis.vaultOf = vaultOf; globalThis.cards = cards;';
  vm.runInNewContext(src, box);
  return box;
}

const P = (p, salt) => new Uint8Array(createHmac('sha256', p.prfSecret).update(salt).digest());   // the simulated PRF
const fa = (p, challenge, cred) => ({ id: cred, cd: wire.b64u(enc.encode(JSON.stringify({ type: 'webauthn.get', challenge: typeof challenge === 'string' ? challenge : wire.b64u(challenge),
  origin: 'https://m.agentj.app', crossOrigin: false }))), ad: 'AA', sig: 'AA' });
const sign = (p, bytes) => wire.b64u(edSign(null, Buffer.from(bytes), p.key));
async function signCtl(p, action, obj, n) {   // terminal.js signCtl / vaultrow.js / controls.js signedWrite
  const ts = Date.now();
  return { n, ts, sig: sign(p, await wire.controlMessage(p.channel, p.device, action, n, ts, obj)) };
}
async function sealTermPrf(p, epk, prf, n, action, obj) {   // terminal.js sealPrf (kind term_prf, id = nonce = n)
  const digest = await wire.sha256hex(enc.encode(wire.controlObject(action, obj)));
  const s = await sealPrf(wire.unb64u(epk), prf, p.channel, p.device, n, 'term_prf', n, digest);
  return { pepk: wire.b64u(s.epk), pct: wire.b64u(s.ct) };
}
const view = (c) => c && { id: c.id, kind: c.kind, n: c.n, tries: c.tries, bad: c.bad, stale: !!c.stale, vaultMiss: !!c.vaultMiss, name: c.name ?? null,
  dev: c.dev ?? null, cred: c.cred ?? null, paired: c.paired ?? null, flags: c.flags ?? null, fa: c.fa ?? null, faCh: c.faCh ? wire.b64u(c.faCh) : null,
  v: c.v ? { has: c.v.has, vs: wire.b64u(c.v.vs), vn: c.v.vn ? wire.b64u(c.v.vn) : null } : null,
  useVault: c.kind === 'sudo' && !!c.v && c.v.has && !!c.fa, offerSave: c.kind === 'sudo' && !!c.v && !c.v.has && !!c.fa };

const OPS = {
  /** {name, priv, pub, channel, device} — the phone's Ed25519 approval key (raw, test-only) and identity. */
  init({ name, priv, pub, channel, device }) {
    const key = createPrivateKey({ key: { kty: 'OKP', crv: 'Ed25519', d: priv, x: pub }, format: 'jwk' });
    const p = { name, key, channel, device, prfSecret: randomBytes(32) };
    p.elev = elevBox(p);
    phones.set(name, p);
    return { ok: true };
  },
  newSid: () => ({ sid: wire.newId22() }),
  bangLine: ({ text }) => ({ line: bangBox.bangLine(text), unsafe: typeof bangBox.bangLine(text) === 'string' && bangBox.UNSAFE.test(bangBox.bangLine(text)) }),
  termOf: ({ term }) => ({ term: JSON.parse(JSON.stringify(bangBox.termOf(term))) }),
  block({ vault }) { const v = vrowBox.parse(vault); return { parsed: v && JSON.parse(JSON.stringify(v)), state: vrowBox.state(v) }; },
  /** terminal.js onBang with S.bang = {sid, line}: what the page does with the host's term_bang. */
  onBang({ msg, sid, line }) { const S = { bang: { sid, line } }, calls = []; onBangOf(S, calls)(msg); return { calls, cleared: S.bang === null }; },
  /** terminal.js go(): the line it would put into the input ('' = none), or null when it refuses to show it. */
  go({ msg }) {
    const cmd = typeof msg.cmd === 'string' ? msg.cmd.slice(0, 4096) : '';
    if (cmd && (CTRL.test(cmd) || bangBox.UNSAFE.test(cmd))) return { input: null };
    return { input: cmd };
  },
  /** elevate.js add(): the card as the page keeps it (null = not shown). */
  async card({ name, msg }) {
    const p = phone(name);
    p.elev.add(msg);
    const c = p.elev.cards.get(msg.id);
    if (c && c.fa) for (let i = 0; i < 200 && !c.faCh; i++) await new Promise((r) => setTimeout(r, 5));
    return { card: view(c) };
  },
  /** elevate.js done(): the words the page shows. */
  done({ name, msg }) { const p = phone(name); p.elev.notified.length = 0; p.elev.done(msg); return { notified: p.elev.notified.map((x) => ({ kind: x.kind, text: x.text })) }; },
  /** elevate.js decide(): how = vault | typed | typed_save | vshare | deny. wrongPrf = another authenticator's PRF. */
  async answer({ name, id, how, password, wrongPrf }) {
    const p = phone(name), c = p.elev.cards.get(id);
    if (!c) throw new Error('no card');
    const ok = how !== 'deny';
    const vaultUse = ok && (c.kind === 'vshare' || how === 'vault');
    if (how === 'vault' && !(c.kind === 'sudo' && c.v && c.v.has && c.fa)) throw new Error('the page would not offer the vault here');
    if (how === 'typed_save' && !(c.kind === 'sudo' && c.v && !c.v.has && c.fa)) throw new Error('the page would not offer 记住 here');
    const typed = ok && !vaultUse;
    const save = how === 'typed_save';
    const digest = await wire.elevateDigest(c.kind, wire.elevateFields(c));
    const ts = Date.now();
    const m = { t: 'elev_answer', id: c.id, ok, n: c.n, ts };
    const src = wrongPrf ? { prfSecret: randomBytes(32) } : p;
    let prf = null, ctSha = null, saveSha = null;
    if (vaultUse || save) prf = prfJoin(P(src, c.v.vs), vaultUse && c.v.vn ? P(src, c.v.vn) : null);
    if (vaultUse) {
      const s = await sealPrf(c.epk, prf, p.channel, p.device, c.id, c.kind === 'vshare' ? 'vshare' : 'vault', c.n, digest);
      m.epk = wire.b64u(s.epk); m.ct = wire.b64u(s.ct); ctSha = s.ctSha;
      if (c.kind === 'sudo') m.vault = 1;
    } else if (typed) {
      const s = await wire.sealValue(c.epk, enc.encode(password), p.channel, p.device, c.id, c.kind, c.n, digest);
      m.epk = wire.b64u(s.epk); m.ct = wire.b64u(s.ct); ctSha = s.ctSha;
      if (prf) {
        const p2 = await sealPrf(c.epk, prf, p.channel, p.device, c.id, 'vault_save', c.n, digest + '|' + ctSha);
        m.pepk = wire.b64u(p2.epk); m.pct = wire.b64u(p2.ct); saveSha = await wire.sha256hex(p2.ct);
      }
    }
    m.sig = sign(p, wire.elevateMessage(p.channel, p.device, c.id, c.kind, ok ? 'allow' : 'deny', c.n, ts, digest, ctSha, null, saveSha));
    if (ok && c.fa) m.fa = fa(p, c.faCh, c.fa);
    if (m.vault) c.sentVault = true;
    if (ok) c.running = true;
    return { msg: m, prfLen: vaultUse || save ? (vaultUse && c.v.vn ? 64 : 32) : 0 };
  },
  /** terminal.js introYes(): step 2 of term_open from the host's passkey_required answer. */
  async termOpen({ name, res, r }) {
    const p = phone(name), salt = salt32(res.salt), vn = salt32(res.vn);
    const prf = salt ? prfJoin(P(p, salt), vn ? P(p, vn) : null) : null;
    const obj = { mode: 'line' };
    const signed = await signCtl(p, 'term_open', obj, res.n);
    const sealed = prf ? await sealTermPrf(p, res.epk, prf, res.n, 'term_open', obj) : {};
    return { msg: { t: 'term_open', r, ...signed, fa: fa(p, res.fa_ch, res.fa), ...sealed }, prfLen: prf ? (vn ? 64 : 32) : 0 };
  },
  /** terminal.js confirmLine() 「运行」: the signed resend (keeps `bang`). */
  async confirm({ name, msg, cred, bang }) {
    const p = phone(name);
    const signed = await signCtl(p, 'term_line', { id: msg.id, line: msg.line }, msg.n);
    const uv = msg.uv === 0 ? 0 : 1;   // P573: uv 0 = the button tier — signed, no Face ID (as confirmLine)
    return { msg: { t: 'term_line', id: msg.id, seq: msg.seq, line: msg.line, ...signed, ...(uv ? { fa: fa(p, msg.fa_ch, cred) } : {}), ...(bang ? { bang } : {}) },
      agent: msg.agent === 1 || msg.agent === true };
  },
  /** vaultrow.js share() step 2 from the host's passkey_required answer (PRF over the fresh salt, prf1 only). */
  async vaultShare({ name, res, r }) {
    const p = phone(name);
    const HEX32 = /^[0-9a-f]{32}$/;
    if (!(res.ok === false && res.why === 'passkey_required' && HEX32.test(res.n || '') && typeof res.fa === 'string' && typeof res.fa_ch === 'string'
      && salt32(res.salt) && salt32(res.epk))) return { msg: null };
    const prf = prfJoin(P(p, salt32(res.salt)), null);
    const signed = await signCtl(p, 'vault_share', {}, res.n);
    const digest = await wire.sha256hex(enc.encode(wire.controlObject('vault_share', {})));
    const s = await sealPrf(wire.unb64u(res.epk), prf, p.channel, p.device, res.n, 'vault_share', res.n, digest);
    return { msg: { t: 'vault_share', r, ...signed, fa: fa(p, res.fa_ch, res.fa), pepk: wire.b64u(s.epk), pct: wire.b64u(s.ct) } };
  },
};

const rl = createInterface({ input: process.stdin });
for await (const line of rl) {
  let out;
  try {
    const q = JSON.parse(line);
    out = { ok: true, ...(await OPS[q.op](q)) };
  } catch (e) { out = { ok: false, error: String(e && e.stack || e) }; }
  process.stdout.write(JSON.stringify(out) + '\n');
}
