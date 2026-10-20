// P168: the disconnected card (offline-card.js) and the human fallback mailbox feedback@agentj.app — pure parts + wiring.
//   threshold constant · duration text · mailto (address, subject, body template, no secrets) · both dictionaries ·
//   the card is the P150 gdlg square (no second modal look) · blur + no-backdrop-filter fallback + reduced motion ·
//   every mail link in the page is feedback@ (founder@ / me@ gone) · session.js exposes the real retry numbers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
globalThis.window = {};
const OC = await import('../public/js/offline-card.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const HTML = read('../public/index.html'), CSS = read('../public/app.css'), APP = read('../public/app.js');
const SRC = read('../public/js/offline-card.js'), SESSION = read('../public/js/session.js');
const DICT = (await import('../public/i18n.js')).default;

test('P168 offline card: the 30-minute threshold is a named constant; duration text', () => {
  assert.equal(OC.LONG_OFFLINE_MS, 30 * 60 * 1000);
  assert.equal(OC.FEEDBACK_EMAIL, 'feedback@agentj.app');
  // P393 (#122): the window is the single five-second link gate (js/reconnect.js); was 6000 before
  assert.equal(OC.SHOW_AFTER_MS, 5000);
  assert.match(APP, /const QUIET_MS = DOWN_DEBOUNCE_MS;/, 'the card waits exactly the quiet window (a blink never blurs the page)');
  assert.equal(OC.isLong(30 * 60 * 1000), false);
  assert.equal(OC.isLong(30 * 60 * 1000 + 1), true);
  assert.deepEqual(OC.sinceText(59_000), ['off.since.lt1', {}]);
  assert.deepEqual(OC.sinceText(-5), ['off.since.lt1', {}]);
  assert.deepEqual(OC.sinceText(31 * 60_000), ['off.since.min', { n: 31 }]);
  assert.deepEqual(OC.sinceText(125 * 60_000), ['off.since.hour', { h: 2, m: 5 }]);
});

test('P168 offline card: the mailto carries address, subject and a body template — never a secret', () => {
  OC.configure({ version: '0.18.4a1', device: () => 'iPhone · Safari' });
  const parts = OC.mailParts();
  const href = OC.mailtoHref(parts);
  assert.ok(href.startsWith('mailto:feedback@agentj.app?subject='), href.slice(0, 60));
  const u = new URL(href);
  assert.equal(u.pathname, 'feedback@agentj.app');
  assert.deepEqual([...u.searchParams.keys()], ['subject', 'body'], 'no cc / bcc / anything else');
  const body = u.searchParams.get('body');
  assert.equal(u.searchParams.get('subject'), 'Agent J 连不上');
  for (const want of ['账号 ID', '看到了什么', '从什么时候开始', '截图', '0.18.4a1', 'iPhone · Safari']) assert.ok(body.includes(want), want);
  assert.match(body, /从什么时候开始：\d{4}-\d\d-\d\d \d\d:\d\d/);
  assert.doesNotMatch(body, /\{\w+\}/, 'every placeholder filled');
  for (const bad of [/wss?:\/\//, /#p=/, /\bpsk\b/i, /token/i, /channel/i, /hostPub/i, /AJ-[0-9A-Z]{4}-/]) assert.doesNotMatch(href, bad);
  // the template is the same in English, same placeholders
  const ph = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
  assert.equal(ph(DICT.zh['off.mailBody']), '{device},{since},{ver}');
  assert.equal(ph(DICT.en['off.mailBody']), ph(DICT.zh['off.mailBody']));
  assert.match(DICT.en['off.mailBody'], /Account ID[\s\S]*What I saw[\s\S]*When it started[\s\S]*Screenshot/);
  // what goes into it comes only from display data: version + device label (app.js), never the host record
  assert.match(APP, /version: VERSION\.version,\n    device: deviceLabel,/);
  const code = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
  assert.doesNotMatch(code, /dbGet|session\.|channel|hostPub|relay\b/, 'the card module reads no pairing data');
});

test('P168 offline card: both dictionaries, same placeholders; the two promised sentences', () => {
  const keys = ['off.title.reconnecting', 'off.title.computer', 'off.since.lt1', 'off.since.min', 'off.since.hour', 'off.prog.waitHost', 'off.prog.next',
    'off.prog.trying', 'off.prog.connecting', 'off.do', 'off.tip1', 'off.tip2', 'off.later', 'off.retry', 'off.long', 'off.longNote', 'off.copy',
    'off.copied', 'off.mailSubject', 'off.mailBody', 'help.support', 'help.mail'];
  const ph = (s) => (String(s).match(/\{\w+\}/g) || []).sort().join();
  for (const k of keys) {
    assert.ok(DICT.zh[k] && DICT.en[k], k);
    assert.equal(ph(DICT.zh[k]), ph(DICT.en[k]), k);
  }
  assert.equal(DICT.zh['off.long'], '长时间连不上？写信到 feedback@agentj.app');
  assert.equal(DICT.en['off.long'], "Still can't connect? Email feedback@agentj.app");
  for (const l of ['zh', 'en']) {
    assert.ok(!DICT[l]['off.tip2'].includes('agentj '), 'P375: offline card uses the menu recovery entry, commands stay on the website');
    assert.ok(DICT[l]['help.support'].includes('`agentj support`'));
    assert.ok(DICT[l]['help.mail'].includes('feedback@agentj.app'));
  }
  const cli = readFileSync(new URL('../../host/agentj/cli.py', import.meta.url), 'utf8');
  assert.match(cli, /dc\.add_argument\("--reconnect"/, 'agentj doctor --reconnect exists');
  assert.match(cli, /support\.add_parser\(sub\)/, 'agentj support exists');
});

test('P168 offline card: the P150 gdlg square, registered as a layer, never takes focus; blur + fallback + reduced motion', () => {
  for (const id of ['offDlg', 'offTitle', 'offSince', 'offProg', 'offLong', 'offMail', 'offCopy', 'offCopyNote', 'offLater', 'offRetry'])
    assert.equal(HTML.split(`id="${id}"`).length, 2, id);
  assert.match(HTML, /<div id="offDlg" class="gdlg offdlg" role="dialog" aria-modal="false"[^>]*hidden>\n    <div class="sq">/);
  assert.match(HTML, /<div class="gdlg-row"><button id="offLater"[^>]*>[^<]*<\/button><button id="offRetry" class="go"/);
  assert.match(SRC, /registerLayer\(\{ element: \(\) => d, open: isOpen, close, fallback: \(\) => \$\('setBtn'\), trap: false, rank: 4 \}\)/);
  assert.doesNotMatch(SRC, /\.focus\(/, 'never moves focus (the owner may be typing into the offline queue)');
  assert.doesNotMatch(SRC, /innerHTML|insertAdjacentHTML|outerHTML/);
  assert.match(CSS, /\.gdlg\.offdlg\{[^}]*-webkit-backdrop-filter:blur\(8px\)[^}]*backdrop-filter:blur\(8px\)/);
  assert.match(CSS, /@supports not \(\(backdrop-filter:blur\(1px\)\) or \(-webkit-backdrop-filter:blur\(1px\)\)\)\{\.gdlg\.offdlg\{background:rgba\(96,96,96,\.78\)\}\}/);
  assert.match(CSS, /@media\(prefers-reduced-motion:reduce\)\{\.offdlg\[data-state="reconnecting"\] \.off-icon svg\{animation:none\}\}/);
  assert.match(CSS, /@media\(prefers-reduced-motion:reduce\)\{\.gdlg \.sq\{animation:none\}\}/, 'the shared square entry respects it too');
  // the copy button is the app's copy glyph (the reply bar's) with the check for "done"
  assert.ok(HTML.includes('<rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'));
  assert.match(SRC, /RelayMD\.copyText\(FEEDBACK_EMAIL\)/);
});

test('P168 wiring: app.js feeds the card the painted state, session.js the real retry numbers; outage start in the host record', () => {
  assert.match(APP, /import \* as offline from '\.\/js\/offline-card\.js';/);
  assert.match(APP, /relay\.connLost\(\); offline\.dropped\(\); \}/, 'counted from the drop, not from the end of the quiet window');
  assert.match(APP, /const m = offMode\(\);\n  if \(m\) offline\.update\(m, key\);/);
  assert.match(APP, /retry: \(\) => \{ if \(session\.retryInfo\(\)\.nextAt\) session\.reconnectNow\('manual'\); else resume\(\)\.catch\(\(\) => \{\}\); \}/);
  assert.match(APP, /persist: \(at\) => ticketQueue\(async \(\) => \{/);
  assert.match(APP, /offline\.restore\(host\.downAt\); askPersist\(\); show\('chat'\); resume\(\);/);
  assert.match(SESSION, /export const retryInfo = \(\) => \(\{ attempts: retries, nextAt \}\);/);
  assert.match(SESSION, /retries\+\+; nextAt = Date\.now\(\) \+ wait;/);
  assert.match(SESSION, /backoff = BACKOFF_FIRST; retries = 0; nextAt = null;/);
});

test('P168 feedback mailbox: every mail link of the page is feedback@agentj.app; the error / removed pages and Settings say when to use it', () => {
  const mails = [...HTML.matchAll(/href="(mailto:[^"]*)"/g)].map((m) => m[1]);
  assert.ok(mails.length >= 4, mails.join());
  for (const m of mails) assert.equal(m, 'mailto:feedback@agentj.app');
  const pub = ['index.html', 'app.js', 'js/settings.js', 'js/offline-card.js', 'i18n.js'].map((f) => read('../public/' + f)).join('\n');
  assert.doesNotMatch(pub, /founder@agentj\.app|\bme@agentj\.app/);
  assert.match(HTML, /<section id="error-view"[\s\S]*id="error-help-support" data-i18n="help\.support"[\s\S]*id="error-help-mail" data-i18n="help\.mail"[\s\S]*<\/section>/);
  assert.match(HTML, /<section id="revoked-view"[\s\S]*data-i18n="help\.mail"[\s\S]*<\/section>/);
  assert.match(HTML, /id="set-help-mail" data-i18n="help\.mail"/);
});
