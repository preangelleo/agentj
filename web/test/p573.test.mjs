// P573 step 2 (web): the static half of the host contract from engineer C (approval tiers) and the reader's file stepping.
// Browser half: p573.mjs (task_grant line, auto by task, one-tap approval, file reader actions, ⑫).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const relay = read('../public/js/relay.js'), snap = read('../public/js/snap.js'), controls = read('../public/js/controls.js');
const term = read('../public/js/terminal.js'), elev = read('../public/js/elevate.js'), media = read('../public/js/media.js');
const fnOf = (src, name) => { const i = src.indexOf(`function ${name}(`); assert.ok(i >= 0, name); return src.slice(i, src.indexOf('\n}\n', i) + 2); };

test('task_grant: kept from the ask (≤ 200 chars, zh + en), painted as one line above the buttons, never signed', () => {
  assert.match(snap, /task_grant: typeof m\.task_grant === 'string' \? m\.task_grant\.slice\(0, 200\) : ''/);
  assert.match(snap, /task_grant_en: typeof m\.task_grant_en === 'string'/);
  const f = fnOf(relay, 'paintTaskGrant');
  assert.match(f, /el\("apprBtns"\)\.before\(n\)/);
  assert.match(f, /lang\(\) === "en" && p\.task_grant_en/);
  assert.match(f, /n\.textContent = tx/, 'text only');
  assert.match(relay, /paintTaskGrant\(p, open\);/);
  assert.doesNotMatch(read('../public/js/api.js'), /task_grant/, 'the signature covers what it always covered');
});
test('auto by task: its own toast and activity line, zh + en', async () => {
  assert.match(controls, /m\.by === 'task' \? 'p573\.autoTask'/);
  assert.match(controls, /r\.k === 'auto' && r\.by === 'task'\) return t\('p573\.act\.task', v\)/);
  const d = (await import('../public/i18n.js')).default;
  for (const k of ['p573.autoTask', 'p573.act.task']) { assert.match(d.zh[k], /本任务/); assert.match(d.en[k], /this task/); }
  assert.match(d.zh['p573.autoTask'], /\{tool\}.*\{summary\}/); assert.match(d.en['p573.act.task'], /\{task\}\{cats\}\{tool\} \{t\}/);
});
test('term_confirm: uv 0 = the button alone (no WebAuthn, no fa sent); uv 1 or no uv = Face ID as before', () => {
  const f = fnOf(term, 'confirmLine');
  assert.match(f, /const uv = m\.uv === 0 \? 0 : 1;/);
  assert.match(f, /\(uv && \(typeof m\.fa_ch !== 'string' \|\| !S\.fa\)\)/, 'fa_ch / a passkey only needed for uv 1');
  assert.match(f, /if \(!uv\) p = Promise\.resolve\(\{ fa: undefined \}\);\s*else try \{ p = assertFa\(m\.fa_ch, S\.fa, null\);/);
  assert.match(f, /\.\.\.\(fa \? \{ fa \} : \{\}\)/);
  assert.match(f, /signCtl\('term_line'/, 'the line is still signed by this remote');
  assert.match(f, /pk: uv \? \{ fa: S\.faS \|\| null \} : null/);
});
test('sudo card: password + 同意 = one tap on a P573 host; the helper path with helper_uv still asks Face ID; older hosts unchanged', () => {
  assert.match(elev, /c\.tier573 = 'helper_uv' in m; c\.helperUv = m\.helper_uv === 1 \|\| m\.helper_uv === true;/);
  assert.match(elev, /const needFa = vaultUse \|\| \(c\.tier573 \? c\.passkey_required \|\| \(nopw && c\.helperUv\) : !c\.buttonOnly && !!c\.fa\);/);
  assert.match(elev, /if \(ok && needFa\) \{/);
  assert.match(elev, /allow = node\('button', 'elev-allow', 'aj-btn aj-btn--primary'\); allow\.type = 'submit';/, '同意 is a plain submit button (no hold)');
});
test('file reader: ← / → step through the same reply\'s files (the viewer\'s order); a picture / PDF closes the reader first', () => {
  assert.match(media, /export function fileNeighbour\(c, d\) \{ const list = navList\(c\)/);
  const f = fnOf(relay, 'rdFileStep');
  assert.match(f, /fileNeighbour\(c, dir\)/);
  assert.match(f, /if \(!readsInReader\(next\)\)\{[^]*closeReader\(\); if \(pushed\) await afterPop\(\);/);
  assert.match(relay, /if \(rdAt\?\.file\)\{ rdFileStep\(dir\); return; \}/);
  assert.match(fnOf(relay, 'rdFileFill'), /n\.className = "rdftext"/, 'plain text keeps its line breaks');
});
test('file reader: the card is looked up live by media id; no × in its header', () => {
  const f = fnOf(relay, 'rdFileCard');
  assert.match(f, /x\.dataset\.mid === c\.dataset\.mid/);
  assert.doesNotMatch(fnOf(relay, 'rdFileBuild'), /glyph\("close"\)/);
});
