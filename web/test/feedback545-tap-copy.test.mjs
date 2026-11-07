import {test} from 'node:test';
import assert from 'node:assert/strict';
globalThis.window = {};
const {shortFieldMatches: fields} = await import('../public/js/shortfields.js');
import {RelayMD} from '../public/js/md.js';
const values = (s, opts) => fields(s, opts).map(x => x.value);
test('#545 synthetic email, Chinese neighbours and punctuation', () => {
  assert.deepEqual(values('邮箱 support@example.invalid、team@example.invalid，fixture.user@example.invalid。'), ['support@example.invalid','team@example.invalid','fixture.user@example.invalid']);
  assert.deepEqual(values('（账号 ID zypn-q9q5），网址agentj.app 与 alpha-app.agentjarvis.net。'), ['zypn-q9q5','agentj.app','alpha-app.agentjarvis.net']);
});
test('#545 explicit identifiers only; ordinary hyphenated prose stays text', () => {
  assert.deepEqual(values('Use one-tap to finish. account ID: abc_123. AJ-ABCD-1234-EFGH-5678 @username'), ['abc_123','AJ-ABCD-1234-EFGH-5678','@username']);
  assert.deepEqual(values('the value zypn-q9q5 is an unlabeled word'), []);
});
test('#545 owner inline values copy without delimiters and HTTP remains a link', () => {
  assert.deepEqual(values('run `git status -sb`; `https://agentj.app`', {inlineCode:true}), ['git status -sb','https://agentj.app']);
  assert.deepEqual(fields('visit https://agentj.app/help, then agentj.app/help.').map(x=>[x.value,!!x.href]), [['https://agentj.app/help',true],['agentj.app/help',false]]);
  assert.deepEqual(values('`email@example.com`', {inlineCode:true}), ['email@example.com']);
});
test('#545 mail links retain only a validated single address, never query or client action', () => {
  assert.equal(RelayMD.mailHref('mailto:support@example.invalid?subject=hello'), 'mailto:support@example.invalid');
  assert.equal(RelayMD.mailHref('MAILTO:hello%2B545@agentj.app?subject=hello'), 'mailto:hello+545@agentj.app');
  assert.equal(RelayMD.safeHref('mailto:a@b.io?bcc=c@d.io'), null);
  assert.equal(RelayMD.safeHref('mailto:x@example.com,evil@example.com'), null);
  assert.equal(RelayMD.safeHref('javascript:alert(1)'), null);
});

test('#545 bare copy fields retain native selectstart and context menus', async () => {
  const {controlTarget, initControlGestures} = await import('../public/js/control-gestures.js');
  const n = {closest: s => s === '[data-cc="bare"]' ? {} : null};
  assert.equal(controlTarget(n), null);
  assert.equal(controlTarget({nodeType:3, parentElement:n}), null);
  const listeners = []; initControlGestures({addEventListener: (type, fn) => listeners.push(fn)});
  for (const fn of listeners) { let blocked = false; fn({target:n,cancelable:true,preventDefault(){blocked=true}}); assert.equal(blocked,false); }
});
