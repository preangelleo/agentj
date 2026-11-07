import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

// Source-only governance checks; these do not qualify #577/0033 visuals or a release.
const rules = [
  ['reuse', '同语义组件优先复用'],
  ['upgrade', '现有能力不足时先升级既有组件'],
  ['no-duplicate', '不得为同语义需求另起一套布局或样式'],
  ['isolate', '异类组件独立隔离，但不得另创全局风格'],
];
const contracts = [
  ['CONSTITUTION', '## UI 组件复用（#393）'],
  ['PRODUCT_SPEC', '### UI 组件复用与语义边界（#393 / #577）'],
  ['WORKFLOW', '## UI 出稿审查（#393 / #577）'],
];
function section(source, heading) {
  const start = source.indexOf(heading + '\n');
  assert.notEqual(start, -1, `missing section: ${heading}`);
  return source.slice(start + heading.length).split(/\n#{1,3} /, 1)[0];
}
function check(body, checklist) {
  const policy = checklist ? body.split('\n').find(line => line.startsWith('- 核验 #393：')) || '' : body;
  for (const [name, text] of rules) assert.ok(policy.includes(text), `missing #393 ${name}`);
}
for (const [name, heading] of contracts) {
  const source = readFileSync(new URL(`../../../documentation/${name}.md`, import.meta.url), 'utf8');
  test(`#393 ${name} explicitly enforces the four component rules`, () => {
    check(section(source, heading), name === 'WORKFLOW');
  });
  for (const [rule, text] of rules) {
    test(`#393 ${name} rejects removal of ${rule}`, () => {
      const body = section(source, heading);
      assert.ok(body.includes(text), `negative control requires the real ${rule} clause`);
      assert.throws(() => check(body.replaceAll(text, ''), name === 'WORKFLOW'), /missing #393/);
    });
  }
}
