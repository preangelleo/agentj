// P294 (Leo 10-15): the font-size preview reads as a preview, not a file; setting titles carry no scope in brackets.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const zh = JSON.parse(read('../i18n/web.zh.json')).set, src = JSON.parse(read('../i18n/web.zh.src.json')).set;
const en = JSON.parse(read('../i18n/web.en.json')).set;
const html = read('../public/index.html');

test('P294: the font sample explains itself — no file name, code, link or English code word', () => {
  for (const s of [zh.fontSample, src.fontSample, en.fontSample]) {
    assert.doesNotMatch(s, /restock|\.\w{1,4}\b|`|https?:|\/\//, s);
    assert.match(s, /−/, s);                                   // it points at the − / + buttons right above
  }
  assert.doesNotMatch(zh.fontSample, /[A-Za-z]/, zh.fontSample);
  assert.equal(zh.fontSampleLab, '预览');
  assert.equal(en.fontSampleLab, 'Preview');
});

test('P294: a 「预览」 label sits right above the sample box, not clickable', () => {
  assert.match(html, /<span class="setsamplelab" aria-hidden="true" data-i18n="set\.fontSampleLab">预览<\/span>\s*<p class="setsample" id="set-font-sample"/);
  assert.doesNotMatch(html, /restock/);
});

test('P294: setting titles carry no scope in brackets; the notes say where isolation and Docker apply', () => {
  assert.equal(zh.iso, '隔离运行');
  assert.equal(en.iso, 'Isolated sandbox');
  for (const [k, v] of Object.entries(zh)) if (typeof v === 'string' && /^(iso|docker|mode)$/.test(k)) assert.doesNotMatch(v, /[（(]/, `${k}: ${v}`);
  assert.match(src.isoNote, /^只管 Agent J 自己启动的 Agent（独立会话和定时任务）/);
  assert.match(src.isoShared, /共享会话/);
  assert.match(src.dockerNote, /^依附于隔离运行/);
  assert.match(src.dockerOff, /不生效/);
  assert.match(html, /id="set-iso-shared" hidden data-i18n="set\.isoShared"/);
  assert.match(html, /id="set-docker-off" hidden data-i18n="set\.dockerOff"/);
});
