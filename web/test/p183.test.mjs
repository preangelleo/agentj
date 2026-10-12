// P183 · file cards: the pure rules (auto-load, preview routing, CSV parsing), the mail-dialog door, and the static rules
// of the preview module (our own vendor files only, lazily; the docx HTML only in the empty sandbox; no eval in pdf.js).
// Run: node --test web/test/p183.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as W from '../public/js/mediawire.js';

const read = (r) => readFileSync(new URL(`../public/${r}`, import.meta.url), 'utf8');
const MIB = 1024 * 1024;
const it = (kind, bytes, name = 'x', mime = '') => ({ kind, bytes, name, mime });

test('autoLoad: pictures / PDFs ≤ 5 MiB, the rest ≤ 2 MiB; never on data saver, cellular or 2G', () => {
  assert.equal(W.autoLoad(it('image', 5 * MIB)), true);
  assert.equal(W.autoLoad(it('image', 5 * MIB + 1)), false);
  assert.equal(W.autoLoad(it('pdf', 4 * MIB)), true);
  assert.equal(W.autoLoad(it('file', 2 * MIB)), true);
  assert.equal(W.autoLoad(it('file', 2 * MIB + 1)), false);
  assert.equal(W.autoLoad(it('video', 3 * MIB)), false);
  assert.equal(W.autoLoad(it('audio', 100)), true);
  assert.equal(W.autoLoad(it('image', 10), { saveData: true }), false);
  assert.equal(W.autoLoad(it('image', 10), { type: 'cellular' }), false);
  assert.equal(W.autoLoad(it('image', 10), { effectiveType: 'slow-2g' }), false);
  assert.equal(W.autoLoad(it('image', 10), { effectiveType: '2g' }), false);
  assert.equal(W.autoLoad(it('image', 10), { type: 'wifi', effectiveType: '4g' }), true);
  assert.equal(W.autoLoad(it('weird', 10)), false);
  assert.equal(W.autoLoad(null), false);
});

test('previewKind: every kind the computer sends has a view or an honest "not supported"', () => {
  const k = (name, mime, kind = 'file') => W.previewKind({ name, mime, kind });
  assert.equal(k('a.png', 'image/png', 'image'), 'image');
  assert.equal(k('a.pdf', 'application/pdf', 'pdf'), 'pdf');
  assert.equal(k('a.html', 'text/html', 'html'), 'html');
  assert.equal(k('a.wav', 'audio/wav', 'audio'), 'audio');
  assert.equal(k('a.mp4', 'video/mp4', 'video'), 'video');
  assert.equal(k('a.md', 'text/markdown'), 'markdown');
  assert.equal(k('a.csv', 'text/csv'), 'table');
  assert.equal(k('a.tsv', 'text/tab-separated-values'), 'table');
  assert.equal(k('a.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'), 'sheet');
  assert.equal(k('a.ods', 'application/vnd.oasis.opendocument.spreadsheet'), 'sheet');
  assert.equal(k('a.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'), 'docx');
  assert.equal(k('a.json', 'application/json'), 'code');
  assert.equal(k('a.yaml', 'application/yaml'), 'code');
  assert.equal(k('a.xml', 'application/xml'), 'code');
  assert.equal(k('a.toml', 'application/toml'), 'code');
  assert.equal(k('a.txt', 'text/plain'), 'text');
  assert.equal(k('a.log', 'text/plain'), 'text');
  for (const [n, m] of [['a.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'], ['a.odt', 'application/vnd.oasis.opendocument.text'],
    ['a.epub', 'application/epub+zip'], ['a.zip', 'application/zip'], ['a.gz', 'application/gzip']]) assert.equal(k(n, m), 'none', n);
  assert.equal(W.codeLang('config.JSON'), 'json');
  assert.equal(W.codeLang('noext'), '');
});

test('parseDelimited: RFC 4180 quoting, CRLF, BOM, TSV, and the row / column / cell caps', () => {
  assert.deepEqual(W.parseDelimited('﻿a,b\r\n"x, y","say ""hi"""\r\n1,\n').rows, [['a', 'b'], ['x, y', 'say "hi"'], ['1', '']]);
  assert.deepEqual(W.parseDelimited('a\tb\nc\td', '\t').rows, [['a', 'b'], ['c', 'd']]);
  assert.deepEqual(W.parseDelimited('"multi\nline",z').rows, [['multi\nline', 'z']]);
  const lim = { ROWS: 2, COLS: 2, CELL: 3 };
  const r = W.parseDelimited('a,b,c\nlongcell,2\n3,4\n5,6\n', ',', lim);
  assert.deepEqual(r.rows, [['a', 'b'], ['lon…', '2']]); assert.equal(r.more, true);
  assert.deepEqual(W.parseDelimited('').rows, []);
});

test('mailfwd.js: hidden until P184 registers; the request reaches the dialog; registering tells the cards', async () => {
  const events = [];
  globalThis.document = { dispatchEvent: (e) => events.push(e.type) };
  globalThis.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = o && o.detail; } };
  const M = await import('../public/js/mailfwd.js');
  assert.equal(M.canMailForward(), false);
  assert.equal(M.mailForward({ mid: 'x' }), false);
  let got = null;
  M.registerMailForward((r) => { got = r; return 'opened'; });
  assert.equal(M.canMailForward(), true);
  assert.equal(M.mailForward({ kind: 'file', mid: 'm' }), 'opened');
  assert.deepEqual(got, { kind: 'file', mid: 'm' });
  M.registerMailForward(null);
  assert.equal(M.canMailForward(), false);
  assert.deepEqual(events, ['aj:mailfwd', 'aj:mailfwd']);
  delete globalThis.document; delete globalThis.CustomEvent;
});

test('preview.js: only our own vendor files, loaded on first need; docx HTML only in the empty sandbox; pdf.js without eval', () => {
  const src = read('js/preview.js');
  const libs = [...src.matchAll(/(pdf|pdfWorker|docx|sheet): '([^']+)'/g)].map((m) => m[2]);
  assert.deepEqual(libs, ['vendor/pdfjs/pdf.min.mjs', 'vendor/pdfjs/pdf.worker.min.mjs', 'vendor/mammoth/mammoth.browser.min.js', 'vendor/xlsx/xlsx.core.min.js']);
  for (const l of libs) assert.ok(read(l.replace(/^/, '')).length > 1000, l);
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:\\'"])\/\/.*$/gm, '$1');
  for (const bad of [/\binnerHTML\b/, /outerHTML/, /insertAdjacentHTML/, /DOMParser/, /\bfetch\s*\(/, /https?:\/\//, /\beval\b/, /\bimport\s*\(/, /localStorage/])
    assert.doesNotMatch(code, bad, `preview.js uses ${bad}`);
  assert.match(src, /isEvalSupported: false, enableXfa: false,\s*enableScripting: false/);
  assert.match(src, /for \(const \[k, v\] of Object\.entries\(sandboxAttrs\(\)\)\) f\.setAttribute\(k, v\);\n\s+f\.className = 'mframe';\n\s+f\.title = t\('media\.pv\.docx'\);/,
    'the docx frame takes the HTML preview\'s empty sandbox + csp');
  assert.doesNotMatch(read('index.html'), /vendor\/(pdfjs|mammoth|xlsx)/, 'nothing new on first paint');
  const media = read('js/media.js');
  assert.match(media, /if \(autoLoad\(m, net\(\)\)\) \{\n\s+if \(sc\.io\) sc\.io\.observe\(c\); else queueMicrotask\(\(\) => c\._load\(\)\);/, 'auto-load waits for the card to be on screen');
  assert.match(media, /bTg\.hidden = !peer\.tgfile;/); assert.match(media, /bMail\.hidden = !canMailForward\(\);/);
  assert.match(read('js/api.js'), /ask1\(\{ t: 'tg_fwd', mid \}, 'tg_fwd_res', 120000\)/, 'a file goes by its id; no bytes up');
});
