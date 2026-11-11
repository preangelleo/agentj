import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');

test('590: mail controls use shared components and the dynamic host heading uses section rhythm', () => {
  const html = read('../public/index.html');
  assert.match(html, /id="mail-enable-next" class="aj-action aj-action--primary"/);
  assert.match(html, /class="aj-check-input" id="mail-trusted-check" disabled/);
  assert.match(read('../public/js/owner-hosts.js'), /const title = node\('h3', '', 'setg'\); title\.dataset\.title = ''/);
});

test('590: retired offline animation has neither shipped consumer nor manifest entry', () => {
  const css = read('../public/app.css');
  const manifest = read('../animation-ownership.json');
  assert.doesNotMatch(css, /aj-recovery-off-pulse|\.offdlg\[data-state="reconnecting"\]/);
  assert.doesNotMatch(manifest, /aj-recovery-off-pulse|\.offdlg\[data-state/);
});
