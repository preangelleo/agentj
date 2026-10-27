// P554 (chairman 10-27): the reply card's 转发 icon is the 回复 icon mirrored left↔right — the curved arrow both had before
// P536 swapped it for a straight ↗ without review. The reader clones the card's buttons (relay.js RD_ACTS), so one lock
// covers both. A new forward glyph needs the chairman's review first, then this test changes with it.
// Plus the P554 host-level 转发去向: the signed forward_set control text, and no unsigned pref_set path.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {controlObject, CONTROL_ACTIONS} from '../public/proto/wire.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const html = read('../public/index.html');
const glyph = (id) => {
  const btn = html.match(new RegExp(`<button[^>]*id="${id}"[\\s\\S]*?</button>`))[0];
  return [...btn.match(/<svg class="g g0"[\s\S]*?<\/svg>/)[0].matchAll(/<path d="([^"]+)"/g)].map((m) => m[1]);
};
// Each path → absolute segments [kind, …end point (+ arc radii / flags)]; mirroring = x → 24 − x, an arc's sweep flips.
function segments(d) {
  const toks = d.match(/[a-zA-Z]|-?[\d.]+/g), ARGS = {M: 2, L: 2, H: 1, V: 1, A: 7}, out = [];
  let cmd = '', x = 0, y = 0, i = 0;
  while (i < toks.length) {
    if (/[a-zA-Z]/.test(toks[i])) cmd = toks[i++];
    const n = ARGS[cmd.toUpperCase()], a = toks.slice(i, i + n).map(Number), rel = cmd !== cmd.toUpperCase();
    i += n;
    const C = cmd.toUpperCase();
    if (C === 'M' || C === 'L') { x = rel ? x + a[0] : a[0]; y = rel ? y + a[1] : a[1]; out.push([C === 'M' && out.length === 0 ? 'M' : 'L', x, y]); }
    else if (C === 'H') { x = rel ? x + a[0] : a[0]; out.push(['L', x, y]); }
    else if (C === 'V') { y = rel ? y + a[0] : a[0]; out.push(['L', x, y]); }
    else { x = rel ? x + a[5] : a[5]; y = rel ? y + a[6] : a[6]; out.push(['A', a[0], a[1], a[2], a[3], a[4], x, y]); }
    if (cmd === 'M') cmd = 'L'; else if (cmd === 'm') cmd = 'l';   // implicit line-to after a move
  }
  return out;
}
const mirror = (segs) => segs.map((s) => (s[0] === 'A' ? ['A', s[1], s[2], s[3], s[4], 1 - s[5], 24 - s[6], s[7]] : [s[0], 24 - s[1], s[2]]));

test('转发 is 回复 mirrored left↔right (the pre-P536 curved arrow, not a new glyph)', () => {
  const reply = glyph('replyBtn'), fwd = glyph('fwdBtn');
  assert.equal(fwd.length, reply.length, 'same number of strokes');
  for (let i = 0; i < reply.length; i++) assert.deepEqual(segments(fwd[i]), mirror(segments(reply[i])), `stroke ${i + 1}`);
  assert.deepEqual(fwd, ['M14 5l6 6-6 6', 'M20 11h-9a7 7 0 0 0-7 7v1'], 'the exact glyph from before 7f03d3bc3 (P536)');
  assert.ok(!html.includes('M5 19 19 5M6 5h13v13'), 'the unreviewed straight ↗ is gone');
});

test('forward_set: signed, no Face ID, object = dest plus a migrate line; never in pref_set', () => {
  assert.ok(CONTROL_ACTIONS.includes('forward_set'));
  assert.equal(controlObject('forward_set', {dest: 'tg', migrate: false}), 'tg');
  assert.equal(controlObject('forward_set', {dest: 'wx', migrate: true}), 'wx\nmigrate');
  const fc = read('../public/js/forward-choice.js'), settings = read('../public/js/settings.js');
  assert.match(fc, /signedWrite\('forward_set', \{dest, migrate\}, \{t: 'forward_set', dest, migrate\}\)/);
  assert.ok(!/signedWriteFa\('forward_set'/.test(fc), 'no Face ID step');
  assert.ok(!settings.includes("'ui.forward_destination'"), 'not a pref_set key on the phone');
  assert.match(settings, /forwarding\.fromHost\(prefs, hostInfo\)/);
});
