// P426 #170: the 「我的设备」 group's signed post (fr_gsay) is the same object on both sides; the page reuses the chat's @ detection.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { controlObject, CONTROL_ACTIONS } from '../public/proto/wire.js';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');

test('fr_gsay control object mirrors controls.py', () => {
  assert.ok(CONTROL_ACTIONS.includes('fr_gsay'));
  assert.equal(controlObject('fr_gsay', { text: '@Mac M1 跑一下测试' }), 'devices\n@Mac M1 跑一下测试');
  const py = read('../../host/agentj/controls.py');
  assert.match(py, /ACTIONS \+= \("fr_gsay",\)/);
  assert.match(py, /return f"devices\\n\{obj\['text'\]\}"/);
  assert.equal(read('../public/proto/wire.js'), read('../../protocol/wire.js'));
});

test('friends page: reuses mentionAt, signs fr_gsay, never a plain friend message', () => {
  const js = read('../public/js/friends.js');
  assert.match(js, /import \{ mentionAt \} from '\.\/five-pages\.js'/);
  assert.match(js, /fr_gsay: \(o\) => \(\{ t: 'fr_gsay', text: o\.text \}\)/);
  assert.doesNotMatch(js, /t: 'fr_send'|t: 'fr_msg'/);
  const en = JSON.parse(read('../i18n/web.en.json')), zh = JSON.parse(read('../i18n/web.zh.json'));
  for (const k of Object.keys(en).filter((k) => k.startsWith('fr.dev.'))) assert.ok(zh[k], k);
});
