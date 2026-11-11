// 0.30.8 (chairman 11-11, Oppo home-screen app: 「点话筒每次点了就退出，点三四次才行」). The browser behaviour lives in
// p0308-mic-android.mjs (real page, Chromium Android emulation, red on ffd7ca9b9d); this pins the rules it relies on so a
// later edit cannot drop them without web-unit going red.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const relay = read('../public/js/relay.js'), app = read('../public/app.js');

test('0.30.8: a touch take whose microphone is still opening waits for the prompt blur when the press is taken away', () => {
  assert.match(relay, /const micOpening = p => !!p && p\.touch && !p\.cancel && !p\.rec && !p\.stream && !p\.over && typeof p\.id !== "string";/);
  const end = relay.slice(relay.indexOf('const pressEnd = (how, e) => {'), relay.indexOf('mic.addEventListener("pointerup"'));
  assert.match(end, /\(how === "pointercancel" \|\| how === "lostpointercapture"\) && !ptt\.locked && micOpening\(ptt\) && document\.visibilityState === "visible"\)\{/);
  // the prompt's blur / hide locks (window blur #508 rule, microphoneAway); a plain cancel still cancels after the short wait (P272)
  assert.match(end, /p\.cancelWait = setTimeout\(\(\) => \{ if \(ptt === p && !p\.locked\) pttEnd\(p, "cancel"\); \}, PROMPT_WAIT_MS\); return;/);
  assert.match(relay, /const PROMPT_WAIT_MS = 160;/);
  assert.match(relay, /function pttLock\(p\)\{\n  clearTimeout\(p\.pend\); clearTimeout\(p\.cancelWait\);/);
  assert.match(relay, /clearTimeout\(p\.openTimer\); clearTimeout\(p\.cancelWait\);/, 'pttEnd clears the wait');
  assert.match(relay, /if \(ptt\?\.touch && !ptt\.cancel && !ptt\.rec && document\.visibilityState === "visible"\) \{\n      if \(!ptt\.locked\) pttLock\(ptt\);/, 'the #508 blur rule that does the locking');
  assert.ok(end.indexOf('micOpening(ptt)') < end.indexOf('pttEnd(ptt, released ? "release" : "cancel")'), 'checked before the cancel path');
});

test('0.30.8: a page hidden by a permission dialog keeps an opening take; pagehide and audio already flowing still end it (P496)', () => {
  assert.match(relay, /function microphoneAway\(gone\)\{\n  if \(gone !== true && micOpening\(ptt\)\)\{[^\n]*\n    if \(!ptt\.openHidAt\) ptt\.openHidAt = Date\.now\(\);\n    if \(!ptt\.locked\) pttLock\(ptt\);\n    return;\n  \}/);
  assert.match(relay, /document\.visibilityState !== "visible"\) microphoneAway\(\)/, 'visibilitychange still routes through microphoneAway');
  assert.match(relay, /window\.addEventListener\("pagehide", \(\) => microphoneAway\(true\)\);/, 'pagehide always ends the take');
  assert.doesNotMatch(relay, /window\.addEventListener\("pagehide", microphoneAway\)/, 'the event object must not be read as `gone`');
  // microphone.js still stops a grant that lands while hidden; such an abort is not reported as 「no microphone」
  assert.match(read('../public/js/microphone.js'), /if \(generation !== epoch \|\| !visible\(\)\) \{ stop\(s\); throw abort\(\); \}/);
  assert.match(relay, /if \(err\?\.name !== "AbortError"\) toast\(t\('r\.ptt\.noMic'\), 3000\);/);
});

test('0.30.8 举一反三: the QR scanner and the camera sheet wait out a hide while still asking, never live while hidden', () => {
  assert.match(app, /const asking = view === 'scan' && scanActive && !scanStream;\n  if \(document\.hidden\) \{ if \(!asking\) stopScan\(\); else if \(!scanHidAt\) scanHidAt = Date\.now\(\); return; \}/);
  assert.match(app, /if \(document\.hidden \|\| hiddenTooLong\(scanHidAt\)\) \{ stream\.getTracks\(\)\.forEach\(tr => tr\.stop\(\)\); stopScan\(\); return; \}/);
  assert.match(app, /window\.addEventListener\('pagehide', \(\) => stopScan\(\)\);/);
  assert.match(relay, /const asking = !el\("cam"\)\.hidden && !camStream;\n    if \(document\.visibilityState === "hidden"\)\{ if \(asking && !camHidAt\) camHidAt = Date\.now\(\); else if \(!el\("cam"\)\.hidden && camStream\) closeCam\(\); return; \}/);
  assert.match(relay, /if \(box\.hidden \|\| document\.visibilityState !== "visible" \|\| hiddenTooLong\(camHidAt\)\)\{ s\.getTracks\(\)\.forEach\(tr => tr\.stop\(\)\); if \(!box\.hidden\) closeCam\(\); return; \}/);
  assert.match(relay, /addEventListener\("pagehide", \(\) => \{ if \(!el\("cam"\)\.hidden\) closeCam\(\); \}\);/);
});

test('0.30.8 security r1 L3: the wait behind a hiding permission dialog is bounded (P496 exact rule)', async () => {
  const mic = read('../public/js/microphone.js');
  assert.match(mic, /^\/\/ P496 exact rule \(0\.30\.8 int1 security r1 L3\)/m);
  const {PROMPT_HIDE_MAX_MS, hiddenTooLong} = await import('../public/js/microphone.js');
  assert.equal(PROMPT_HIDE_MAX_MS, 3000);
  assert.equal(hiddenTooLong(0, 1e9), false, 'never hidden');
  assert.equal(hiddenTooLong(1000, 1000 + 500), false, '0.5 s: the Android prompt switch keeps the take');
  assert.equal(hiddenTooLong(1000, 1000 + 3000), false, 'the bound itself keeps it');
  assert.equal(hiddenTooLong(1000, 1000 + 5000), true, '5 s: closed on return');
  // microphone: the return closes a take still opening after a long hide, and a grant that beat the event is dropped
  assert.match(relay, /function microphoneBack\(\)\{\n  const p = ptt;\n  if \(!p \|\| !p\.openHidAt\) return;\n  const long = hiddenTooLong\(p\.openHidAt\);\n  p\.openHidAt = 0;\n  if \(long && micOpening\(p\)\) pttEnd\(p, "cancel", true\);/);
  assert.match(relay, /if \(document\.visibilityState !== "visible"\) microphoneAway\(\);\n    else microphoneBack\(\);/);
  assert.match(relay, /if \(!p\.over && hiddenTooLong\(p\.openHidAt\)\) pttEnd\(p, "cancel", true\);/);
  // QR scan + camera sheet: the same rule on return
  assert.match(app, /const long = hiddenTooLong\(scanHidAt\); scanHidAt = 0;\n  if \(long && asking\) stopScan\(\);/);
  assert.match(relay, /const long = hiddenTooLong\(camHidAt\); camHidAt = 0;\n    if \(long && asking\) closeCam\(\);/);
});
