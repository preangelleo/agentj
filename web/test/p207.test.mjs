// P207 (§1, §3, 追加 1) static contract: one centred-dialog token, the usage dialog, copy icons, two-way switches / one-row
// segments, the add-remote scan icon, the scan page icons, the heartbeat controls and the 通讯录 page — every key in zh and en.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const html = read('../public/index.html'), app = read('../public/app.css'), menu = read('../public/menu.css');
const flat = (o, p = '', out = {}) => { for (const [k, v] of Object.entries(o)) { const kk = p ? p + '.' + k : k; if (v && typeof v === 'object') flat(v, kk, out); else out[kk] = v; } return out; };

test('追加 1: one dialog background token (Noir), no light variant, theme-overridable, three shapes', () => {
  assert.match(app, /--dlg-bg:var\(--c-noir\);/);
  assert.match(app, /\.gdlg\{[^}]*--dbg:var\(--dlg-bg\);--dfg:var\(--dlg-fg\)\}/);
  assert.doesNotMatch(app, /\.gdlg\{[^}]*--c-lotion/);
  assert.doesNotMatch(menu, /\.ajconfirm\{--dbg:var\(--c-lotion\)/);
  assert.match(menu, /\.ajconfirm\{--dbg:var\(--dlg-bg\)/);
  for (const k of ['rect', 'rounded', 'circle']) assert.match(app, new RegExp(`\\.gdlg--${k} \\.sq`));
  assert.match(read('../public/js/theme.js'), /DIALOG_VARS = \{ background: \['--dlg-bg'\] \}/);
  for (const f of ['elevate', 'secretout', 'logincard', 'longtask']) assert.match(read(`../public/js/${f}.js`), /'ajmodal dlgcard /, f);
});

test('§1: Weekly / 5H open the centred dialog, never a toast', () => {
  assert.match(html, /id="usageDlg" class="gdlg/);
  const relay = read('../public/js/relay.js');
  const fn = relay.slice(relay.indexOf('function meterToast('), relay.indexOf('/** The single entry'));
  assert.match(fn, /openUsage\(/);
  assert.doesNotMatch(fn, /\btoast\(/);
});

test('§3: copy icons, two-way switches, one-row segments, scan icon, scan page icons, heartbeat, contacts', () => {
  const copies = html.match(/<button type="button" class="setcopy"[^>]*>/g);
  assert.ok(copies.length >= 8);
  for (const b of copies) assert.match(b, /aria-label="复制命令" title="复制命令"/);
  assert.doesNotMatch(html, /data-i18n="set\.copy">复制命令</);
  for (const id of ['set-lang', 'set-mode', 'set-updates']) assert.match(html, new RegExp(`role="switch" id="${id}" class="tw-sw"`));
  assert.doesNotMatch(html, /class="setval setwide"/, 'no ⇄ cycling pill left');
  assert.match(html, /<div class="segrow" role="group" data-i18n-attr="aria-label=set\.theme"/);
  assert.doesNotMatch(html, /id="rm-scan"/);
  assert.match(html, /id="rm-add"[^>]*>[^<]*<\/button>\s*<button type="button" id="rm-scan-icon"/);
  for (const id of ['scan-back', 'scan-trigger', 'scan-stop']) assert.match(html, new RegExp(`id="${id}" class="scan-ico[^"]*"[^>]*aria-label=`));
  assert.match(html, /class="scan-finder"[^>]*><i class="sf-c sf-tl"><\/i><i class="sf-c sf-tr"><\/i><i class="sf-c sf-bl"><\/i><i class="sf-c sf-br"><\/i><b class="sf-sweep"><\/b>/);
  assert.match(read('../public/app.js'), /scanActive = true; paintScanTrigger\(\);/);
  assert.match(html, /<select id="set-hb-every"/);
  for (const v of [30, 60, 120, 180, 240, 360, 480, 720, 1440]) assert.match(html, new RegExp(`<option value="${v}">`));
  for (const id of ['set-hb-custom-n', 'set-hb-daily', 'set-hb-time', 'set-contacts-nav', 'set-contacts', 'ctm-list', 'ctm-q', 'ctm-add', 'ctm-import', 'ctm-export', 'ctmEdit'])
    assert.equal(html.split(`id="${id}"`).length, 2, id);
  const s = read('../public/js/settings.js');
  assert.match(s, /export const SAY_MS = 4000;/);
  assert.ok(!html.includes('set-status-bar'), 'P340 #22: the old notice bar under the Settings header is gone');
  const c = read('../public/js/ctmanage.js');
  for (const t of ["'ct_list'", "'ct_save'", "'ct_rm'", "'ct_export'", "'ct_import'", "signedWrite('mail_allow'"]) assert.ok(c.includes(t), t);
});

test('P207 i18n: every p207set key in zh (src, override, built) and en, same placeholders', () => {
  const used = new Set();
  for (const f of ['../public/index.html', '../public/js/settings.js', '../public/js/ctmanage.js', '../public/js/usagedlg.js', '../public/js/relay.js'])
    for (const m of read(f).matchAll(/p207set\.[A-Za-z.]+[A-Za-z]/g)) used.add(m[0]);
  assert.ok(used.size > 30, String(used.size));
  const D = Object.fromEntries(['web.zh.src', 'web.zh.override', 'web.zh', 'web.en'].map((n) => [n, flat(JSON.parse(read(`../i18n/${n}.json`)))]));
  const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join();
  for (const k of used) {
    for (const n of Object.keys(D)) assert.equal(typeof D[n][k], 'string', `${k} in ${n}`);
    assert.equal(ph(D['web.zh'][k]), ph(D['web.en'][k]), k);
  }
});
