// P547 (PASSKEY-SEAT-MODEL §3): host_moved validation (js/hostmove.js), the two new signed controls in both wire copies =
// host controls.object_text, the masked transfer prompt (js/seatxfer.js) and the Settings row. Browser leg: p547.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { readFileSync, existsSync } from 'node:fs';
import * as wire from '../../protocol/wire.js';
import * as pageWire from '../public/proto/wire.js';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
globalThis.document ??= { getElementById: () => null, addEventListener() {}, body: null };
const { parseHostMoved, movedRecord, movedAt } = await import('../public/js/hostmove.js');
const { maskPrompt, maskToken } = await import('../public/js/seatxfer.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const b64u = (n, v = 1) => Buffer.alloc(n, v).toString('base64url');

// the page's own rule (app.js allowRelay) on a real origin
const allow = (url) => { const u = new URL(url); return u.protocol === 'wss:' && !u.port && ['relay.agentj.app', 'alpha-relay.agentjarvis.net'].includes(u.hostname); };
const RELAY = 'wss:' + '//relay.agentj.app';
const ok = { t: 'host_moved', relay: RELAY, channel: b64u(16), hostPub: b64u(32, 2), name: 'Mac mini' };

test('host_moved: our relay, 16-byte channel, 32-byte host key → the parts; anything else is ignored', () => {
  const mv = parseHostMoved(ok, allow);
  assert.equal(mv.relay, RELAY);
  assert.equal(mv.channel, ok.channel);
  assert.equal(mv.hostPub.length, 32);
  assert.equal(mv.name, 'Mac mini');
  for (const bad of [
    { relay: 'wss:' + '//evil.example' }, { relay: 'ws:' + '//relay.agentj.app' }, { relay: 'wss:' + '//relay.agentj.app:8443' }, { relay: 'not a url' },
    { channel: b64u(15) }, { channel: b64u(17) }, { channel: b64u(16) + '=' }, { channel: 'AAAA+AAAAAAAAAAAAAAAAA' },
    { hostPub: b64u(31) }, { hostPub: b64u(33) }, { hostPub: 12 }, { t: 'other' }, { relay: RELAY + '/' + 'x'.repeat(220) },
  ]) assert.equal(parseHostMoved({ ...ok, ...bad }, allow), null, JSON.stringify(bad));
  assert.equal(parseHostMoved({ ...ok, name: 'A‮B\u0007  C' }, allow).name, 'AB C', 'display text only');
  assert.equal(parseHostMoved(null, allow), null);
});

test('host_moved: the stored pairing keeps the device keys, points at the new host, has no passkey there yet', () => {
  const old = { relay: RELAY, channel: b64u(16, 9), hostPub: new Uint8Array(32), approved: true, dk: 'DEVICEKEY', pk: true, pkAsked: true, rtAt: 5, plat: 'macos', boot: 'launchd', name: 'old' };
  const mv = parseHostMoved(ok, allow);
  const rec = movedRecord(old, mv);
  assert.equal(rec.dk, 'DEVICEKEY');
  assert.equal(rec.channel, ok.channel);
  assert.equal(rec.hostPub, mv.hostPub);
  assert.equal(rec.approved, true);
  assert.equal(rec.pk, false);
  assert.equal(rec.pkAsked, false);
  assert.equal(rec.name, 'Mac mini');
  for (const k of ['rtAt', 'plat', 'boot']) assert.ok(!(k in rec), k + ' belonged to the old computer');
});

test('seat_transfer / seat_transfer_cancel: object {} → "transfer" / "cancel" in both wire copies and host controls.py', async () => {
  assert.equal(read('../public/proto/wire.js'), read('../../protocol/wire.js'));
  for (const w of [wire, pageWire]) {
    assert.ok(w.CONTROL_ACTIONS.includes('seat_transfer') && w.CONTROL_ACTIONS.includes('seat_transfer_cancel'));
    assert.equal(w.controlObject('seat_transfer', {}), 'transfer');
    assert.equal(w.controlObject('seat_transfer_cancel', {}), 'cancel');
  }
  const msg = new TextDecoder().decode(await wire.controlMessage('C'.repeat(22), 'D'.repeat(22), 'seat_transfer', '0'.repeat(32), 1, {}));
  assert.match(msg, /^agentjarvis-control-v1\nC{22}\nD{22}\nseat_transfer\n0{32}\n1\n[0-9a-f]{64}$/);
  const py = new URL('../../host/agentj/controls.py', import.meta.url);
  if (existsSync(py)) {
    const src = readFileSync(py, 'utf8');
    assert.match(src, /if action == "seat_transfer":[^\n]*\n\s+return "transfer"/);
    assert.match(src, /if action == "seat_transfer_cancel":[^\n]*\n\s+return "cancel"/);
  }
});

test('the transfer prompt: the code masked as ajt_••••…last 4 until the eye is tapped; 「复制」 copies the whole prompt', () => {
  const tok = 'ajt_' + 'Q'.repeat(39) + 'wxyz';
  const prompt = `在新电脑上运行：curl … | sh ，然后输入转移码 ${tok} 。`;
  assert.equal(maskToken(tok), 'ajt_••••…wxyz');
  const m = maskPrompt(prompt, tok);
  assert.ok(!m.includes(tok) && m.includes('ajt_••••…wxyz'));
  assert.ok(!maskPrompt(prompt, null).includes(tok), 'masked even without the token field');
  const js = read('../public/js/seatxfer.js');
  assert.match(js, /pr\.textContent = shown \? promptText\(\) : maskPrompt\(promptText\(\), P\.token\);/);
  assert.match(js, /copy\.addEventListener\('click', \(\) => copyButton\(promptText\(\), copy\)\);/, 'the copy is the full prompt');
  assert.match(js, /signedWriteFa\('seat_transfer', \{\}, \{ t: 'seat_transfer' \}, faceId\)/);
  assert.match(js, /signedWrite\('seat_transfer_cancel', \{\}, \{ t: 'seat_transfer_cancel' \}\)/);
  assert.match(js, /res\.why === 'passkey_needed'\) return needKey\(\)/);
  assert.doesNotMatch(js, /localStorage|sessionStorage|dbPut|innerHTML|console\./, 'the code is never stored or logged');
});

test('Settings → Agent 与遥控器: the row, its state line and 「撤销」; dictionaries complete in both languages', () => {
  const html = read('../public/index.html');
  const block = html.slice(html.indexOf('id="set-s-agent"'), html.indexOf('id="set-s-rescue"'));
  for (const id of ['set-seat-xfer', 'set-seat-xfer-state', 'set-seat-xfer-cancel']) assert.ok(block.includes(`id="${id}"`), id);
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  const keys = Object.keys(zh).filter((k) => /^(pkp|sxf|hmv)\./.test(k));
  assert.ok(keys.length > 60);
  const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',');
  for (const k of keys) { assert.equal(typeof en[k], 'string', 'en ' + k); assert.equal(ph(zh[k]), ph(en[k]), k); }
  assert.equal(zh['sxf.row'], '转移席位到另一台 Agent J 主机');
  assert.equal(zh['sxf.pkElsewhere'], '去有钥匙的设备操作');
  assert.equal(zh['hmv.rescan'], '扫码配对新的 Agent J 主机');
  for (const k of ['sxf.step1', 'sxf.step2', 'sxf.step3']) assert.ok(zh[k] && en[k]);
  assert.match(zh['sxf.step2'], /「绑定你的席位」/);
  const app = read('../public/app.js');
  assert.match(app, /case 'host_moved': hostMoved\(m\)/);
  assert.match(app, /const mv = parseHostMoved\(m, allowRelay\);/);
});

test('review M2: host_moved never switches silently — 「切过去」 / 「留在这里」 sheet first; only 「切过去」 rewrites the pairing', () => {
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  assert.equal(zh['hmv.ask'], '席位已转到新的 Agent J 主机「{name}」（{time}）。让这台遥控器切过去吗？');
  assert.equal(zh['hmv.go'], '切过去');
  assert.equal(zh['hmv.stay'], '留在这里');
  for (const k of ['hmv.askTitle', 'hmv.ask', 'hmv.go', 'hmv.stay']) assert.ok(en[k] && zh[k], k);
  assert.match(en['hmv.ask'], /\{name\}[\s\S]*\{time\}/);
  // the zh dictionary is the polished copy of the source (override = the source text)
  const src = JSON.parse(read('../i18n/web.zh.src.json')), ov = JSON.parse(read('../i18n/web.zh.override.json'));
  for (const k of ['hmv.askTitle', 'hmv.ask', 'hmv.go', 'hmv.stay']) { assert.equal(src[k], zh[k], k); assert.equal(ov[k], zh[k], k); }
  const app = read('../public/app.js');
  const body = app.slice(app.indexOf('async function hostMoved(m)'), app.indexOf('function moveFailed()'));
  const ask = body.indexOf("confirmSheet(t('hmv.askTitle'), t('hmv.ask', {"), put = body.indexOf("dbPut('host', movedRecord(h, mv))");
  assert.ok(ask > 0 && put > ask, 'the sheet comes before the stored pairing is touched');
  assert.match(body, /t\('hmv\.go'\), false, t\('hmv\.stay'\)\)/);
  assert.match(body, /if \(!go\) \{ moveStayed\.add\(key\); return; \}/, '「留在这里」 changes nothing');
  // P550 (P545 × P547): a seat's page writes the move into the new seat's page instead (copyToSeat + this page's movedTo)
  const stay = body.indexOf('if (!go) { moveStayed.add(key); return; }');
  const writes = [...body.matchAll(/dbPut\(|copyToSeat\(|ticketQueue\(/g)].map((x) => x.index);
  assert.equal((body.match(/dbPut\(/g) || []).length, 2, 'writes: the moved record, or (seat page) movedTo');
  assert.ok(stay > ask && writes.length >= 4 && writes.every((i) => i > stay), 'every write after 「切过去」');
  assert.ok(body.indexOf('resume()') > put && body.lastIndexOf('ticketQueue(renew.clear)') > put, 'reconnect only after 「切过去」');
  assert.match(body, /const h = await dbGet\('host'\);[^\n]*re-read/);
});

test('movedAt: HH:MM, 24 h, zero-padded, local clock; host_moved.at (unix s) is optional and checked', () => {
  const at = Math.floor(new Date(2026, 9, 26, 9, 7).getTime() / 1000);
  assert.equal(parseHostMoved({ ...ok, at }, allow).at, at);
  for (const bad of [undefined, '1800000000', 1.5, -1, 0, 1e13]) assert.equal(parseHostMoved({ ...ok, at: bad }, allow).at, 0, String(bad));
  assert.equal(movedAt(at * 1000), '09:07');
  assert.match(read('../public/app.js'), /time: movedAt\(mv\.at \? mv\.at \* 1000 : Date\.now\(\)\)/);
  assert.equal(movedAt(new Date(2026, 9, 26, 7, 5).getTime()), '07:05');
  assert.equal(movedAt(new Date(2026, 9, 26, 23, 59).getTime()), '23:59');
  assert.match(movedAt(), /^\d\d:\d\d$/);
});

test('sxf.elsewhereHint names the real Settings labels (set.gAgent, sxf.row) in both languages', () => {
  for (const f of ['../i18n/web.zh.json', '../i18n/web.en.json']) {
    const d = JSON.parse(read(f));
    assert.ok(d['sxf.elsewhereHint'].includes(d.set.gAgent), f + ' ' + d.set.gAgent);
    assert.ok(d['sxf.elsewhereHint'].includes(d['sxf.row']), f);
  }
});
