// P414 (PROTOCOL §37.9–§37.14, ADR-P414-bang) — static and pure rules of the phone side: 「!」 detection (edge cases), the
// H1c refusal of Unicode format characters / long space runs, elevateMessage's `save:` tail = the host's signed_message bytes,
// the vshare digest and the vault_share object text = the host's, PRF concat (prf1 ‖ prf2) and the seal kinds (vault ·
// vault_save with digest|sha256(ct) · vshare · vault_share · term_prf) opened like the host does, the zeroed buffers, the
// result-card / vault-block parsers, the routing hooks (relay.js stays a two-line hook), no double-click handler in the
// terminal, nothing stored, dictionaries complete. Rendering and the real flow: p414.mjs (real browser, real Noise).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';
import * as wire from '../../protocol/wire.js';
import * as pageWire from '../public/proto/wire.js';
import { prfJoin, prfEval, salt32, sealPrf } from '../public/js/vault.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const BANG = read('../public/js/bang.js'), TERM = read('../public/js/terminal.js'), VAULT = read('../public/js/vault.js');
const VROW = read('../public/js/vaultrow.js'), ELEV = read('../public/js/elevate.js'), HOOK = read('../public/js/termhook.js');
const RELAY = read('../public/js/relay.js'), APP = read('../public/app.js'), SNAP = read('../public/js/snap.js'), HTML = read('../public/index.html');
const CSS = read('../public/terminal.css'), APPCSS = read('../public/app.css'), SET = read('../public/js/settings.js');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const zhSrc = JSON.parse(read('../i18n/web.zh.src.json')), zhOv = JSON.parse(read('../i18n/web.zh.override.json'));
const code = (s) => s.replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
const subtle = webcrypto.subtle, enc = new TextEncoder();
const hex = (b) => Buffer.from(b).toString('hex');
const plain = (x) => JSON.parse(JSON.stringify(x));   // values made in a vm context (another realm)

/** A function of bang.js (pure; the module itself needs the page) evaluated on its own source. */
function fromSource(src, names, ctx = {}) {
  const body = src.split('\n').filter((l) => !l.startsWith('import ')).join('\n').replace(/^export /gm, '');
  const box = { ...ctx };
  vm.runInNewContext(body.slice(0, body.indexOf('// ----')) + '\n' + names.map((n) => `globalThis.${n} = ${n};`).join('\n'), box);
  return box;
}
const B = fromSource(BANG, ['bangLine', 'UNSAFE', 'CARD_LINES']);
const termOf = (() => {
  const s = BANG.slice(BANG.indexOf("const STATES = ['done'"), BANG.indexOf('let shownFor'));
  const box = {}; vm.runInNewContext(s.replace(/^export /gm, '') + '\nglobalThis.termOf = termOf;', box); return box.termOf;
})();

test('「!」 detection: one line, 「!」 first, something after it; 「!!」 / a space before / multi-line / 「！」 before Chinese stay chat', () => {
  const yes = { '!ls': 'ls', '! ls -la ': 'ls -la', '!ls -la\n': 'ls -la', '!sudo apt update': 'sudo apt update', '!echo "a  b"': 'echo "a  b"',
    '！ls': 'ls', '！ git status': 'git status', '!git log --oneline | head': 'git log --oneline | head', '!你好': '你好' };
  for (const [t, line] of Object.entries(yes)) assert.equal(B.bangLine(t), line, JSON.stringify(t));
  for (const t of ['!', '!   ', '!!', '!!ls', '!!!', ' !ls', 'hi !ls', 'ls', '', '!ls\nmore', 'a\n!ls', '！注意', '！ 注意', '！！', '！', '! ls',
    null, undefined, 42]) assert.equal(B.bangLine(t), null, JSON.stringify(t));
  assert.equal(B.CARD_LINES, 12);
  // relay.js: a two-line hook, before the stop / offline checks (a 「!」 line never queues as chat), never with files or quotes
  const say = RELAY.slice(RELAY.indexOf('async function say(voiceReady = false){'), RELAY.indexOf('sending = true; refreshSend();'));
  const hook = say.indexOf('C.bang(text)');
  assert.ok(hook > 0 && hook < say.indexOf('C.estopOn()') && hook < say.indexOf('C.connected()'), 'before the stop / offline checks');
  assert.match(say, /const bang = !atts\.length && !quotes\.length && C\.bang \? C\.bang\(text\) : false;\n\s+if \(bang\)\{ if \(await bang\)\{ pushHist\(text\); settleField\(text\); autosize\(\); saveDraft\(\); \} return; \}/);
  assert.match(APP, /bang: bang\.take,/);
  assert.match(BANG, /export function take\(text\) \{\n\s+const line = bangLine\(text\);\n\s+if \(line === null\) return false;/);
  assert.match(BANG, /if \(s === 'ok'\) return termhook\.bang\(line, newSid\(\)\);\n\s+explain\(s\);\n\s+return false;/, 'off / not allowed / older host → the explanation, the field kept');
});

test('H1c: Unicode format characters (bidi, zero-width, BOM, soft hyphen) and > 8 spaces never go out — chat 「!」 and the terminal input', () => {
  for (const bad of ['ls ‮rm', '⁦x⁩', 'a​b', 'a‌b', 'a‍b', '﻿ls', 'a­b', 'a⁠b', 'echo a' + ' '.repeat(9) + 'b'])
    assert.ok(B.UNSAFE.test(bad), JSON.stringify(bad));
  for (const ok of ['ls -la', 'echo "你好"', 'printf "%s"   x', 'a' + ' '.repeat(8) + 'b', '😀', 'café']) assert.ok(!B.UNSAFE.test(ok), JSON.stringify(ok));
  const send = TERM.slice(TERM.indexOf('function sendLine('), TERM.indexOf('function badLine('));
  assert.ok(send.indexOf('UNSAFE.test(line)') > 0 && send.indexOf('UNSAFE.test(line)') < send.indexOf("t: 'term_line'"), 'checked before it is sent');
  assert.match(BANG, /if \(UNSAFE\.test\(line\)\) \{ toast\(t\('p414\.cf'\), 5000, 'error'\); return false; \}/);
  assert.match(TERM, /if \(cmd && \(CTRL\.test\(cmd\) \|\| UNSAFE\.test\(cmd\)\)\) return;/, 'a term_go line carrying them is not shown');
});

test('§37.10 M2: elevateMessage(..., grant, save) = the host signed_message bytes (save after grant), both wire copies', () => {
  const A = ['C'.repeat(22), 'D'.repeat(16), 'a'.repeat(32), 'sudo', 'allow', 'b'.repeat(32), 1700000000000, 'c'.repeat(64), 'd'.repeat(64)];
  // vectors from host/agentj/elevate.py signed_message (P414): grant then save · save alone
  const both = 'agentjarvis-elevate-v1\nCCCCCCCCCCCCCCCCCCCCCC\nDDDDDDDDDDDDDDDD\n' + 'a'.repeat(32) + '\nsudo\nallow\n' + 'b'.repeat(32) + '\n1700000000000\n'
    + 'c'.repeat(64) + '\n' + 'd'.repeat(64) + '\ngrant:' + 'e'.repeat(64) + '\nsave:' + 'f'.repeat(64);
  const saveOnly = both.replace('\ngrant:' + 'e'.repeat(64), '');
  for (const w of [wire, pageWire]) {
    assert.equal(new TextDecoder().decode(w.elevateMessage(...A, 'e'.repeat(64), 'f'.repeat(64))), both);
    assert.equal(new TextDecoder().decode(w.elevateMessage(...A, null, 'f'.repeat(64))), saveOnly);
    assert.equal(new TextDecoder().decode(w.elevateMessage(...A, null, null)), both.slice(0, both.indexOf('\ngrant:')), 'no tail without grant / save');
    // §37.11: vault_share object text = "share"; the vshare digest = the host's shown_digest over [dev, cred, paired, flags]
    assert.ok(w.CONTROL_ACTIONS.includes('vault_share'));
    assert.equal(w.controlObject('vault_share', {}), 'share');
    assert.deepEqual(w.elevateFields({ kind: 'vshare', dev: 'dev123', cred: 'credABC', paired: 1760000000, flags: ['web', 'account'] }), ['dev123', 'credABC', '1760000000', 'web,account']);
  }
  return wire.elevateDigest('vshare', ['dev123', 'credABC', '1760000000', 'web,account']).then((d) =>
    assert.equal(d, 'a4c4aaa4b1595a9c87bc30efa04edab16896456cc6bc71d769a737a946c231c5', 'host shown_digest vector'));
});

test('wire twin: protocol/wire.js and web/public/proto/wire.js are the same bytes', () => {
  assert.equal(read('../public/proto/wire.js'), read('../../protocol/wire.js'));
});

/** The host's side of §11's seal. */
async function unseal(priv, hostEpk, s, aadParts) {
  const pk = await subtle.importKey('raw', s.epk, { name: 'X25519' }, false, []);
  const shared = new Uint8Array(await subtle.deriveBits({ name: 'X25519', public: pk }, priv, 256));
  const ikm = await subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
  const k = await subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array([...hostEpk, ...s.epk]), info: enc.encode(wire.SEAL_CONTEXT) }, ikm, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  return new Uint8Array(await subtle.decrypt({ name: 'AES-GCM', iv: s.ct.slice(0, 12), additionalData: enc.encode([wire.SEAL_CONTEXT, ...aadParts].join('\n')) }, k, s.ct.slice(12)));
}
test('§37.9 M3: PRF = prf1 ‖ prf2 (64 B) with a next salt, prf1 (32 B) alone; eval.second only with vn; inputs zeroed', () => {
  const a = new Uint8Array(32).fill(7), b = new Uint8Array(32).fill(9);
  const j = prfJoin(a.buffer.slice(0), b.buffer.slice(0));
  assert.equal(j.length, 64); assert.ok(j.slice(0, 32).every((x) => x === 7) && j.slice(32).every((x) => x === 9));
  assert.equal(prfJoin(a, null).length, 32);
  const src = new Uint8Array(32).fill(5); prfJoin(src, null);
  assert.ok(src.every((x) => x === 0), 'the authenticator buffers are zeroed after the copy');
  assert.equal(prfJoin(null, b), null); assert.equal(prfJoin(new Uint8Array(16), null), null, 'a short first output is not used');
  assert.deepEqual(prfEval('S', null), { prf: { eval: { first: 'S' } } });
  assert.deepEqual(prfEval('S', 'N'), { prf: { eval: { first: 'S', second: 'N' } } });
  const s32 = Buffer.alloc(32, 1).toString('base64url');
  assert.equal(salt32(s32).length, 32); assert.equal(salt32(Buffer.alloc(31).toString('base64url')), null); assert.equal(salt32(''), null); assert.equal(salt32(7), null);
  assert.match(VAULT, /if \(!b \|\| b\.length !== 32\) return a\.slice\(\);/);
  assert.match(TERM, /const salt = saltOf\(m\.salt\), vn = saltOf\(m\.vn\)/, 'term_sudo: vn');
  assert.match(TERM, /vn: saltOf\(m\.vn\), epk: m\.epk/, 'term_open: vn');
  assert.match(ELEV, /assertPrf\(c\.faCh, c\.fa, c\.v\.vs, vaultUse \? c\.v\.vn : null\)/, 'sudo / vshare cards: vn only when the vault is used');
});

test('seal kinds: vault · vault_save (AAD digest|sha256(ct)) · vshare · vault_share — opened like the host, the PRF zeroed', async () => {
  const kp = await subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
  const pub = new Uint8Array(await subtle.exportKey('raw', kp.publicKey));
  const digest = 'ab'.repeat(32), ctSha = 'cd'.repeat(32);
  for (const [kind, dg] of [['vault', digest], ['vault_save', digest + '|' + ctSha], ['vshare', digest], ['vault_share', digest], ['term_prf', digest]]) {
    const prf = new Uint8Array(64).fill(3);
    const s = await sealPrf(pub, prf, 'CH', 'DEV', 'ID' + kind, kind, 'N'.repeat(32), dg);
    assert.ok(prf.every((x) => x === 0), 'zeroed after sealing ' + kind);
    const out = await unseal(kp.privateKey, pub, s, ['CH', 'DEV', 'ID' + kind, kind, 'N'.repeat(32), dg]);
    assert.equal(out.length, 64); assert.ok(out.every((x) => x === 3), kind);
    await assert.rejects(unseal(kp.privateKey, pub, s, ['CH', 'DEV', 'ID' + kind, kind === 'vault' ? 'sudo' : 'vault', 'N'.repeat(32), dg]), 'another kind does not open ' + kind);
    assert.equal(s.ctSha, hex(new Uint8Array(await subtle.digest('SHA-256', s.ct))));
  }
  // elevate.js: which kind each answer uses, the signed save tail, vault:1, the PRF zeroed on every path
  assert.match(ELEV, /sealPrf\(c\.epk, prf, ch, dev, c\.id, c\.kind === 'vshare' \? 'vshare' : 'vault', c\.n, digest\)/);
  assert.match(ELEV, /if \(c\.kind === 'sudo'\) m\.vault = 1;/);
  assert.match(ELEV, /sealPrf\(c\.epk, prf, ch, dev, c\.id, 'vault_save', c\.n, digest \+ '\|' \+ ctSha\)/);
  assert.match(ELEV, /m\.pepk = b64u\(p2\.epk\); m\.pct = b64u\(p2\.ct\); saveSha = await sha256hex\(p2\.ct\);/);
  assert.match(ELEV, /elevateMessage\(ch, dev, c\.id, c\.kind, ok \? 'allow' : 'deny', c\.n, ts, digest, ctSha, grant, saveSha\)/);
  assert.match(ELEV, /\} finally \{\n\s+if \(value\) value\.fill\(0\);\n\s+prf\?\.fill\(0\);/);
  assert.match(VAULT, /try \{ return await sealValue\(hostEpk, prf, channel, device, id, kind, nonce, digest\); \} finally \{ prf\.fill\(0\); \}/);
  assert.match(VROW, /sealPrf\(unb64u\(c\.epk\), got\.prf, ch, dev, c\.n, 'vault_share', c\.n, digest\)/);
  assert.match(VROW, /controlMessage\(ch, dev, 'vault_share', c\.n, ts, \{\}\)/);
  // Face ID (with the PRF) starts inside the tap: before the first await of decide()
  const d = ELEV.slice(ELEV.indexOf('async function decide(ok, how)'), ELEV.indexOf('// #116 (PROTOCOL §11.1)'));
  const first = d.search(/await (?!assertPrf|approve)/);
  assert.ok(d.indexOf('await assertPrf(') > 0 && d.indexOf('await assertPrf(') < first, 'the assertion is the first await');
  assert.match(VAULT, /export function assertPrf\(challenge, credId, first, second\) \{\n\s+const publicKey/, 'not async: get() inside the tap');
});

test('result card and vault blocks: parsers (pure), the card above the reply, the row only with the host block', () => {
  assert.equal(termOf(null), null); assert.equal(termOf({}), null); assert.equal(termOf({ out: 3 }), null);
  assert.deepEqual(plain(termOf({ out: 'x', cut: true, hidden: 2, state: 'running', sid: 's' })), { out: 'x', cut: true, hidden: 2, state: 'running' });
  assert.deepEqual(plain(termOf({ out: 'y', hidden: -1, state: 'weird' })), { out: 'y', cut: false, hidden: 0, state: 'done' });
  // the host's ≤ 16 000 code points (head + 「\n…\n」 + tail, before the mask's words) arrive whole; the page caps at 32 768 units
  assert.equal(termOf({ out: 'z'.repeat(16003) }).out.length, 16003); assert.equal(termOf({ out: '😀'.repeat(16000) }).out.length, 32000);
  assert.equal(termOf({ out: 'z'.repeat(40000) }).out.length, 32768);
  assert.match(SNAP, /\.\.\.\(k === 'phone' && src\.term && typeof src\.term === 'object' \? \{ term: src\.term \} : \{\}\) \}/, 'snap passes src.term of an owner page');
  assert.match(RELAY, /\n  C\.termCard\?\.\(p\);/); assert.match(APP, /termCard: bang\.paintCard,/);
  assert.match(BANG, /pre\.textContent = term\.out \|\| t\('p414\.card\.empty'\);/, 'output as text, never HTML');
  assert.match(BANG, /if \(term\.hidden\) notes\.push\(t\('p414\.card\.hidden', \{ n: term\.hidden \}\)\);/, 'H3: 「已隐藏 N 处」');
  // the vault block (§37.9) and the sudo card's `v` (§37.10)
  const V = fromSource(VROW.slice(VROW.indexOf('const HEX32')), ['parse', 'state']);
  assert.equal(V.parse(undefined), null); assert.equal(V.parse({ has: 1 }), null);
  assert.equal(V.state(V.parse({ has: true, any: true, peers: 0, mode: 'prf' })), 'has');
  assert.equal(V.state(V.parse({ has: false, any: true, peers: 2, mode: null })), 'share');
  assert.equal(V.state(V.parse({ has: false, any: true, peers: 0, mode: null })), 'nopeer');
  assert.equal(V.state(V.parse({ has: false, any: true, peers: 0, mode: 'local' })), 'local');
  assert.equal(V.state(V.parse({ has: false, any: false, peers: 0, mode: null })), 'off');
  assert.equal(V.state(null), 'none');
  const vaultOf = (() => { const s = ELEV.slice(ELEV.indexOf('export function vaultOf('), ELEV.indexOf('const RESULT = ')); const box = { salt32 }; vm.runInNewContext(s.replace(/^export /, '') + '\nglobalThis.vaultOf = vaultOf;', box); return box.vaultOf; })();
  const s32 = Buffer.alloc(32, 2).toString('base64url');
  assert.equal(vaultOf(null), null); assert.equal(vaultOf({ has: 1 }), null, 'no salt → no vault offer');
  assert.deepEqual(plain(Object.keys(vaultOf({ has: 1, vs: s32 }))), ['has', 'vs', 'vn']);
  assert.equal(vaultOf({ has: 1, vs: s32 }).has, true); assert.equal(vaultOf({ has: 0, vs: s32, vn: s32 }).vn.length, 32);
  assert.match(ELEV, /const useVault = \(c\) => c\.kind === 'sudo' && !!c\.v && c\.v\.has && !!c\.fa && !viaHelper\(c\);/, 'M5: `has` = a prf wrap; Face ID needed');
  assert.match(ELEV, /if \(!c\.dev \|\| !c\.cred \|\| c\.paired === null \|\| !c\.flags \|\| c\.name === null \|\| !c\.v \|\| !c\.v\.has\) return;/, 'M4: a vshare card shows who asks or is not shown');
  // host build: a vshare card ends `saved`; a vault answer the wrap does not open → the card again with has 0 and no stale
  assert.match(ELEV, /if \(c\.kind === 'vshare' && \(m\.result === 'saved' \|\| m\.result === 'done'\)\) text = t\('p414\.e\.vsDone'\);/);
  assert.match(ELEV, /if \(c\.kind === 'sudo' && c\.v && !c\.v\.has && !c\.stale && old && \(old\.sentVault \|\| old\.vaultMiss\)\) c\.vaultMiss = true;/);
  assert.match(ELEV, /c\.name = str\(m\.name \?\? '', 128\);/, 'the host\'s 64-code-point label, emoji included');
  const safety = HTML.slice(HTML.indexOf('id="set-s-safety"'), HTML.indexOf('id="set-s-app"'));
  assert.match(safety, /<div class="setrow setterm" data-paired id="set-vault-row" hidden>/);
  assert.match(SET, /vaultrow\.render\(hostInfo\);/);
  assert.match(VROW, /signedWrite\('term_forget', \{\}, \{ t: 'term_forget' \}\)/, '清除这台 = P408 term_forget');
});

test('terminal page: no double-click / double-tap anywhere; Esc + back collapse; the chip; term_go never sends; routing', () => {
  for (const src of [TERM, CSS]) assert.doesNotMatch(code(src), /dblclick|dblTap|doubleTap|detail\s*===?\s*2|clickCount/i, 'no double-click handler');
  assert.match(TERM, /function escape\(\) \{\n\s+if \(S\.phase === 'ready'\) return collapse\(\);/);
  assert.match(TERM, /close: \(\) => escape\(\)/);
  assert.match(TERM, /if \(S\.phase === 'ready' && \$\('term-sheetwrap'\)\.hidden\) \{ pushed = false; collapse\(\); return; \}/, 'back = Esc');
  assert.match(TERM, /\$\('term-exit'\)\.addEventListener\('click', \(\) => collapse\(\)\);/);
  for (const ev of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel']) assert.ok(TERM.includes(`g.addEventListener('${ev}'`), ev);
  assert.match(TERM, /if \(!cancel && dy > min\(\)\) \{ collapse\(\); return; \}/, 'past the threshold → collapse, else spring back');
  assert.match(CSS, /\.term-grab\{[^}]*touch-action:none/);
  assert.doesNotMatch(CSS.slice(CSS.indexOf('.term-top{'), CSS.indexOf('}', CSS.indexOf('.term-top{'))), /border-bottom/, 'no header rule');
  assert.doesNotMatch(CSS.slice(CSS.indexOf('.term-bottom{'), CSS.indexOf('}', CSS.indexOf('.term-bottom{'))), /border-top/, 'no footer rule');
  // term_go (§37.13, H1b): empty input → the line goes in, never sent; else a chip; it never sends anything
  const go = TERM.slice(TERM.indexOf('function go(m, info)'), TERM.indexOf('function paintGo()'));
  assert.doesNotMatch(go, /send\(|sendLine\(|sendApp\(/);
  assert.match(go, /if \(!i\.value\) \{ i\.value = g\.cmd; S\.go = null; S\.goNote = true; setInput\(''\); \}\n\s+else S\.go = g;/);
  assert.match(TERM, /title: t\(agent \? 'p414\.agentTitle' : 'p408\.confirmTitle'\), pre: line,/, 'H1: 「这条命令由 Agent 填写」 + the full line');
  assert.match(HOOK, /if \(m && m\.t === 'term_go'\) \{ if \(usable\(\)\) load\(\)\.then\(\(x\) => x\.go\(m, termInfo\(\)\), \(\) => \{\}\); return; \}/);
  assert.match(APP, /case 'term_bang': case 'term_go': termhook\.msg\(m\); return;/);
  // §37.12 (host build): `failed` = it ran, only the chat page is missing → said once, never offered back; `running` → put back
  const onBang = TERM.slice(TERM.indexOf('function onBang(m)'), TERM.indexOf('/** term_go {cmd?, h}'));
  assert.match(onBang, /if \(m\.why === 'failed'\) \{ toast\(t\('p414\.bangFailed'\), 5000, 'error'\); return; \}/);
  assert.ok(onBang.indexOf("m.why === 'failed'") < onBang.indexOf('restore(b.line)'));
  // the host's 120 s run from the signed resend of a confirmed 「!」 line: the phone's wait is re-armed there
  assert.match(TERM, /if \(bangSid && S\.bang && S\.bang\.sid === bangSid\) \{ clearTimeout\(S\.bang\.timer\); S\.bang\.timer = setTimeout\(\(\) => bangEnd\(bangSid\), BANG_MS\); \}/);
  // a card that needs the owner brings the collapsed page back (and it goes down again once answered)
  assert.match(TERM, /if \(m\.t === 'term_confirm'\) \{ expand\(true\); return confirmLine\(m\); \}/);
  assert.match(TERM, /if \(m\.t === 'term_sudo'\) \{ expand\(true\); return sudoAsk\(m\); \}/);
  // 截屏发到聊天: the existing attachment path, never a send
  const shot = TERM.slice(TERM.indexOf('async function shot()'), TERM.indexOf('export function wrapRows('));
  assert.match(shot, /const r = await H\.attach\(file\);/); assert.doesNotMatch(shot, /sendApp|say\(|\.click\(\)/);
  assert.match(APP, /attach: \(f\) => relay\.attachShot\(f, true\)/);
  assert.match(shot, /g\.fillStyle = '#2c2b26'; g\.fillRect\(0, 0, W, Hh\);/, 'Noir ground'); assert.match(shot, /g\.fillStyle = '#faf9f5';/, 'Lotion words');
});

test('nothing kept: no storage, no HTML, no logging in the new modules; the page stays lazy; closed palette only', () => {
  for (const [name, src] of [['bang', BANG], ['vault', VAULT], ['vaultrow', VROW], ['terminal', TERM]]) {
    assert.doesNotMatch(code(src), /localStorage|sessionStorage|indexedDB|dbPut|putSealed|caches\.|innerHTML|insertAdjacentHTML|outerHTML|document\.write|console\.|\bimport\s*\(|\bfetch\s*\(/, name);
  }
  assert.doesNotMatch(code(APP) + code(BANG) + code(VROW) + code(SET), /terminal\.js|terminal\.css/, 'terminal.js stays lazy');
  const hexes = [...new Set([...CSS.matchAll(/#[0-9a-f]{6}\b/gi)].map((m) => m[0].toLowerCase()))].sort();
  assert.deepEqual(hexes, ['#141414', '#2c2b26', '#71bbde', '#858585', '#f7827e', '#faf9f5', '#ffe292']);
  const card = APPCSS.slice(APPCSS.indexOf('/* P414 (§37.10)'), APPCSS.indexOf('.omtxt.bang'));
  assert.doesNotMatch(card, /#[0-9a-f]{3,6}\b/i, 'the card uses palette variables');
  assert.match(card, /\.bangcard\{[^}]*background:var\(--noir\);color:var\(--lotion\)/);
});

test('dictionaries: every p414 key in zh / en / zh source / zh override; zh hand-written; plain words', () => {
  const used = new Set();
  for (const src of [BANG, TERM, VAULT, VROW, ELEV, HOOK]) for (const m of src.matchAll(/t\('(p414\.[A-Za-z_.]+)'/g)) if (!m[1].endsWith('.')) used.add(m[1]);
  for (const m of HTML.matchAll(/data-i18n="(p414\.[A-Za-z_.]+)"/g)) used.add(m[1]);
  for (const s of ['has', 'local', 'share', 'nopeer', 'off']) used.add('p414.v.st.' + s);
  for (const w of VROW.match(/const WHY = \[([^\]]+)\]/)[1].match(/'(\w+)'/g).map((x) => x.slice(1, -1))) used.add('p414.v.why.' + w);
  for (const s of ['running', 'closed']) used.add('p414.card.' + s);
  for (const f of ['web', 'account', 'app']) used.add('p414.e.flag.' + f);
  assert.ok(used.size > 80, 'the scan found the keys ' + used.size);
  for (const k of used) {
    for (const [name, d] of [['zh', zh], ['en', en], ['zh.src', zhSrc], ['zh.override', zhOv]]) assert.ok(typeof d[k] === 'string' && d[k].length > 1, `${name} ${k}`);
    assert.equal(zh[k], zhOv[k], 'zh = the override (written by hand, no model call) ' + k);
    assert.doesNotMatch(en[k], /[㐀-鿿]/, 'en has no Chinese ' + k);
  }
  const vals = Object.entries(zh).concat(Object.entries(en)).filter(([k]) => k.startsWith('p414.')).map(([, v]) => v).join('\n');
  assert.doesNotMatch(vals, /试用|trial|relay|harness|PTY|Noise|握手|PRF|kid|wrap|HKDF/i, 'plain words only');
  assert.doesNotMatch(vals, /拿不到明文|cannot read the plain/i, 'never the unqualified 「AI 拿不到明文」 claim (N2)');
  assert.match(zh['p414.ex.off'], /`agentj terminal enable`/); assert.match(zh['p414.ex.more'], /https:\/\/agentj\.app\/docs\/web-terminal\//);
});


// ---------------------------------------------------------------- round-3 code review (R3-1, R3-4)
/** A slice of terminal.js (from `a` up to `b`) run in a vm with stubs; → the box. */
function termSlice(a, b, names, ctx) {
  const src = TERM.slice(TERM.indexOf(a), TERM.indexOf(b));
  assert.ok(src.length > 20, a);
  const box = { ...ctx };
  vm.runInNewContext(src + '\n' + names.map((n) => `globalThis.${n} = ${n};`).join('\n'), box);
  return box;
}
const tick = () => new Promise((r) => setTimeout(r, 0));

test('R3-1: a 「!」 line while a program reads input is never sent — the terminal full screen + the explanation; why:"program" too', () => {
  const calls = [];
  const box = termSlice('function bang(line, sid, info)', '/** term_go {cmd?, h}', ['bang', 'onBang'], {
    S: { phase: 'ready', bang: null, atPrompt: false },
    sendLine: (...a) => { calls.push(['send', ...a]); return true; }, expand: () => calls.push(['expand']),
    toast: (m) => calls.push(['toast', m]), t: (k) => k, open: () => calls.push(['open']), restore: (l) => calls.push(['restore', l]),
    paintChip: () => {}, clearTimeout: () => {}, toastAction: () => {}, H: {}, notRun: (q) => calls.push(['notRun', q]) });
  assert.equal(box.bang('hunter2', 'b'.repeat(22)), false);
  assert.deepEqual(calls, [['expand'], ['toast', 'p414.bangProgram']]);
  box.S.atPrompt = true;
  assert.equal(box.bang('ls', 'b'.repeat(22)), true);
  assert.deepEqual(calls.at(-1), ['send', 'ls', 'b'.repeat(22)]);
  calls.length = 0;
  box.S.bang = { sid: 'c'.repeat(22), line: 'pw', timer: 0 };
  box.onBang({ t: 'term_bang', sid: 'c'.repeat(22), ok: false, why: 'program' });
  assert.deepEqual(calls, [['expand'], ['toast', 'p414.bangProgram']], 'not offered back as a 「!」 line');
  assert.equal(box.S.bang, null);
  calls.length = 0;
  box.S.bang = { sid: 'd'.repeat(22), seq: 5, line: 'pwd', timer: 0 };
  box.onBang({ t: 'term_bang', sid: 'd'.repeat(22), ok: false, why: 'running' });
  assert.deepEqual(calls.slice(0, 2), [['notRun', 5], ['toast', 'p414.bangRunning']], '`running`: it did not run → the prompt state back');
  const nr = termSlice('/** P414 (R3-1): line `seq` was not run', 'function badLine(key)', ['notRun'], { S: { pre: { seq: 5, was: true }, atPrompt: false }, paintProg: () => {} });
  nr.notRun(4); assert.equal(nr.S.atPrompt, false); nr.notRun(5); assert.equal(nr.S.atPrompt, true);
  assert.match(TERM, /onExpire: \(\) => \{ out\(t\('p408\.expired'\) \+ '\\n'\); notRun\(m\.seq\);/);
  assert.match(TERM, /onNo: \(\) => \{ out\(t\('p408\.notRun'\) \+ '\\n'\); notRun\(m\.seq\);/);
  assert.match(zh['p414.bangProgram'], /终端里有程序在等输入，请在终端里直接回答（这一行不会发给 Agent J）/);
});

test('R3-4: introYes / sudoAsk zero the PRF in a finally when signing throws before the seal', async () => {
  const prfs = [];
  const assertFa = () => { const prf = new Uint8Array([7, 7, 7, 7]); prfs.push(prf); return Promise.resolve({ fa: {}, prf }); };
  const signCtl = async () => { throw new Error('no_key'); };
  const common = { assertFa, signCtl, sheetBusy: () => {}, toast: () => {}, t: (k) => k, closeSheet: () => {}, send: () => {},
    sealPrf: async () => ({}), Error, Promise, Date };
  const refused = [];
  const intro = termSlice('function introYes()', 'function openStep2(obj)', ['introYes'], { ...common,
    S: { phase: 'intro', ch: { at: Date.now(), fa_ch: 'x', fa: 'f', salt: 's', n: 'n', epk: 'e' } }, NONCE_MS: 60000,
    $: () => ({}), fetchChallenge: () => {}, refuse: (w) => refused.push(w), paintChrome: () => {}, status: () => {}, openStep2: () => {} });
  intro.introYes();
  await tick(); await tick(); await tick();
  assert.deepEqual([...prfs[0]], [0, 0, 0, 0], 'introYes');
  assert.deepEqual(refused, ['passkey_failed']);
  let sheet = null;
  const sudo = termSlice('function sudoAsk(m)', '// ---------------------------------------------------------------- end, close', ['sudoAsk'], { ...common,
    S: { fa: 'f', id: 'i' }, HEX32: /^[0-9a-f]{32}$/, LINE_MAX: 4096, saltOf: (x) => x, epkOk: () => true,
    openSheet: (o) => { sheet = o; } });
  sudo.sudoAsk({ argv: ['ls'], rid: 'r', n: 'a'.repeat(32), fa_ch: 'x', salt: 's', epk: 'e' });
  sheet.onYes();
  await tick(); await tick(); await tick();
  assert.deepEqual([...prfs[1]], [0, 0, 0, 0], 'sudoAsk');
});
