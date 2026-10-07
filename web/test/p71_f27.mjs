// 0.16 (P71 F27): a message may be files alone — a voice note, a picture, a document — with the text field left empty, so
// the phone's soft keyboard never has to open just to type a full stop. The real page (public/js/relay.js) against
// fakehost.mjs in an independent headless Chromium (test/browser.mjs, never the shared CDP on :9222):
//   empty field + empty tray → Send off; spaces only → still off; a file in the tray → Send on with the field empty → the
//   say carries text "" + the file; a long voice take (relayNative.take, the same path as a native shell's take) → a
//   recording attachment → Enter in the empty field sends it; a failed file alone → Send off; offline: a file alone queues,
//   shows its name on the pending line, and goes out with text "" on reconnect.
// Run: node web/test/p71_f27.mjs      Shots: /tmp/aj-web-shots/p71-f27-*.png (AJ_SHOTS)
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startFakeHost } from './fakehost.mjs';
import { startWebServer } from './serve.mjs';
import { launch, newPage, navigate, evaluate, waitFor, waitState, key, shoot } from './browser.mjs';

const SHOTS = process.env.AJ_SHOTS || '/tmp/aj-web-shots';
mkdirSync(SHOTS, { recursive: true });
const server = await startWebServer();
const fake = await startFakeHost();
const B = await launch();
const st = fake.st;
const ok = [];
const step = (s) => { ok.push(s); console.log('  ✓ ' + s); };

const sendOff = (p) => evaluate(p, `document.getElementById('send').disabled`);
const setField = (p, v) => evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ${JSON.stringify(v)}; i.dispatchEvent(new Event('input')); })()`);
const pick = (p, name, body, type) => evaluate(p, `(() => { const dt = new DataTransfer(); dt.items.add(new File([${JSON.stringify(body)}], ${JSON.stringify(name)}, {type: ${JSON.stringify(type)}})); const f = document.getElementById('fDoc'); f.files = dt.files; f.dispatchEvent(new Event('change', {bubbles: true})); })()`);
const chips = (p) => evaluate(p, `[...document.querySelectorAll('#tray .chip')].map(c => c.dataset.st)`);
const lastSay = () => st.says.filter((m) => m && m.t === 'say').pop();
// a 95 s 16 kHz mono WAV (over the field's 90 s cap) handed in like a native wake-word take → an audio attachment
const LONG_TAKE = `(() => { const n = 95 * 16000, b = new Uint8Array(44 + n * 2), v = new DataView(b.buffer); const s = (o, t) => { for (let i = 0; i < t.length; i++) b[o + i] = t.charCodeAt(i); };
  s(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); s(8, 'WAVE'); s(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); s(36, 'data'); v.setUint32(40, n * 2, true);
  let bin = ''; for (let i = 0; i < b.length; i += 8192) bin += String.fromCharCode(...b.subarray(i, i + 8192)); return window.relayNative.take(btoa(bin), 95); })()`;

let page;
try {
  page = await newPage(B, 390, 844, 'light', { allow: [server.url, fake.relay] });
  await navigate(page, fake.newPairing(server.url));
  await waitState(page, 'awaiting-approval');
  await fake.approve();
  await waitState(page, 'ready');

  // ---- nothing to say: Send stays off; spaces are not words
  assert.equal(await sendOff(page), true, 'empty field, empty tray: Send off');
  await setField(page, '   \n ');
  assert.equal(await sendOff(page), true, 'spaces only: Send off');
  await key(page, 'Enter', { code: 'Enter', keyCode: 13 });
  assert.equal(st.says.length, 0, 'Enter on spaces sends nothing');
  await setField(page, '');
  step('empty / spaces only: Send off, nothing sent');

  // ---- a document alone: Send turns on with the field empty, the say is text "" + the file
  await pick(page, 'note.txt', 'hello from F27', 'text/plain');
  await waitFor(page, `[...document.querySelectorAll('#tray .chip')].every(c => c.dataset.st === 'ready') && document.querySelectorAll('#tray .chip').length === 1`, 15000);
  assert.equal(await sendOff(page), false, 'a file alone: Send on');
  assert.equal(await evaluate(page, `document.getElementById('input').value`), '');
  writeFileSync(join(SHOTS, 'p71-f27-file-only-390.png'), await shoot(page));
  await evaluate(page, `document.getElementById('send').click()`);
  await waitFor(page, `document.querySelectorAll('#tray .chip').length === 0`, 15000);
  let m = lastSay();
  assert.equal(m.text, '', 'no words');
  assert.equal((m.att || []).length, 1, 'the file went');
  assert.equal(st.turns[st.turns.length - 1].src.att[0].name, 'note.txt');
  assert.equal(st.slashes ? st.slashes.length : 0, 0, 'no command was parsed');
  await waitFor(page, `document.getElementById('send').disabled`);
  step('a file alone: Send on, say {text: "", att: [1]}, tray and Send cleared');

  // ---- a long voice take alone (a recording attachment), sent with Enter in the empty field
  assert.equal(await evaluate(page, LONG_TAKE), true, 'relayNative.take accepted the take');
  await waitFor(page, `document.querySelectorAll('#tray .chip[data-st="ready"]').length === 1`, 30000);
  assert.equal(st.opens.filter(Boolean).pop().origin, 'recording');
  assert.equal(await sendOff(page), false, 'a voice note alone: Send on');
  writeFileSync(join(SHOTS, 'p71-f27-voice-only-390.png'), await shoot(page));
  await evaluate(page, `document.getElementById('input').focus()`);
  await key(page, 'Enter', { code: 'Enter', keyCode: 13 });
  await waitFor(page, `document.querySelectorAll('#tray .chip').length === 0`, 15000);
  m = lastSay();
  assert.equal(m.text, '');
  assert.equal((m.att || []).length, 1);
  assert.equal(st.turns[st.turns.length - 1].src.att[0].kind === 'audio' || /\.wav$/.test(st.turns[st.turns.length - 1].src.att[0].name), true, 'the recording went');
  step('a voice note alone: Enter in the empty field sends {text: "", att: [recording]}');

  // ---- a failed file alone is nothing to send
  st.blobErr = 'quota';
  await pick(page, 'bad.txt', 'refused', 'text/plain');
  await waitFor(page, `document.querySelector('#tray .chip') && document.querySelector('#tray .chip').dataset.st === 'failed'`, 15000);
  assert.equal(await sendOff(page), true, 'a failed file alone: Send off');
  await evaluate(page, `document.querySelector('#tray .chip .x').click()`);
  await waitFor(page, `document.querySelectorAll('#tray .chip').length === 0`);
  assert.equal(await sendOff(page), true);
  step('a failed file alone: Send off');

  // ---- offline: a file alone queues (its name on the pending line) and goes out with text "" on reconnect
  const before = st.says.length;
  fake.setUp(false);
  await waitFor(page, `window.__ajState !== 'ready'`, 15000);
  await pick(page, 'later.txt', 'queued offline', 'text/plain');
  await waitFor(page, `document.querySelectorAll('#tray .chip').length === 1`);
  assert.equal(await sendOff(page), false, 'offline, a file alone: Send on (it queues)');
  await evaluate(page, `document.getElementById('send').click()`);
  await waitFor(page, `document.querySelectorAll('#outbox .obi').length === 1`);
  assert.equal(await evaluate(page, `document.querySelector('#outbox .obi .obt').textContent`), 'later.txt', 'the pending line names the file');
  assert.equal(await chips(page).then((c) => c.length), 0, 'the file left the tray with its message');
  assert.equal(st.says.length, before, 'nothing sent while away');
  writeFileSync(join(SHOTS, 'p71-f27-offline-file-only-390.png'), await shoot(page));
  fake.setUp(true);
  await waitFor(page, `document.getElementById('outbox').hidden`, 20000);
  m = lastSay();
  assert.equal(m.text, '');
  assert.equal((m.att || []).length, 1);
  assert.equal(st.turns[st.turns.length - 1].src.att[0].name, 'later.txt');
  step('offline: a file alone queues and is sent {text: "", att: [1]} on reconnect');

  assert.equal(page.problems.length, 0, page.problems.join('\n'));
  assert.equal(page.offsite.length, 0, page.offsite.join('\n'));
  console.log(`P71 F27 browser PASS (${ok.length} steps) · shots in ${SHOTS}`);
} finally {
  if (page) await page.dispose().catch(() => {});
  await B.close(); await server.stop(); await fake.stop();
}
