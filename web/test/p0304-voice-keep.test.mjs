// 0.30.6 (#0304): the header of a host notice without owner input, and Settings → 阅读和声音 → 「语音原文件保留」
// (host key inbox.voice_retention_hours, enum 0/1/6/24/72/168/720, default 24, written through the existing pref_set).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const src = JSON.parse(read('../i18n/web.zh.src.json')), ov = JSON.parse(read('../i18n/web.zh.override.json'));
const html = read('../public/index.html'), settings = read('../public/js/settings.js');

test('#0304 A: a host notice header says 「Agent J 主机提示」 / "Agent J host notice"', () => {
  for (const d of [zh, src, ov]) assert.equal(d['p0286.inputUnavailable'], 'Agent J 主机提示');
  assert.equal(en['p0286.inputUnavailable'], 'Agent J host notice');
  const all = JSON.stringify([zh, en, src, ov]) + read('../public/i18n.js');
  assert.doesNotMatch(all, /未保存触发原文|Trigger text was not saved/);
  assert.match(read('../public/js/relay.js'), /if \(header\) ot\.textContent = t\('p0286\.inputUnavailable'\);/);
});

test('#0304 B: the retention row offers exactly the host enum, 24 h marked default, both languages', () => {
  const row = html.match(/<div class="setrow" id="set-voice-keep-row" hidden>[\s\S]*?<\/div>/);
  assert.ok(row, 'row present and hidden until the host lists the key');
  assert.match(row[0], /<select id="set-voice-keep" class="aj-select" data-pref="inbox\.voice_retention_hours" data-aj-owner="aj-settings">/);
  const opts = [...row[0].matchAll(/<option value="(\d+)" data-i18n="([^"]+)">([^<]+)<\/option>/g)];
  assert.deepEqual(opts.map((o) => Number(o[1])), [1, 6, 24, 72, 168, 720, 0]);
  for (const [, , key, text] of opts) { assert.equal(zh[key], text); assert.ok(en[key], key); }
  assert.match(zh['p0304.h24'], /默认/); assert.match(en['p0304.h24'], /default/);
  for (const key of ['p0304.voiceKeep', 'p0304.voiceKeepNote']) { assert.ok(zh[key] && en[key], key); assert.equal(src[key], zh[key]); assert.equal(ov[key], zh[key]); }
  assert.ok(html.includes(`data-i18n="p0304.voiceKeepNote">${zh['p0304.voiceKeepNote']}</p>`));
  for (const s of [zh['p0304.voiceKeepNote'], en['p0304.voiceKeepNote']]) assert.doesNotMatch(s, /电脑|computer|试用|trial/i);
  assert.match(settings, /PREF_KEYS\.push\('inbox\.voice_retention_hours'\)/);
  assert.match(settings, /el\('set-voice-keep-row'\)\.hidden = !H\.paired\(\) \|\| !prefSettable\('inbox\.voice_retention_hours'\)/);
  assert.match(settings, /setPref\('inbox\.voice_retention_hours', Number\(el\('set-voice-keep'\)\.value\)\)/);
});
