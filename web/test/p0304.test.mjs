import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const pub = (f) => readFileSync(fileURLToPath(new URL('../public/' + f, import.meta.url)), 'utf8');

test('worker upgrade changes its version and keeps per-seat relative scope', () => {
  const sw = pub('sw.js');
  const push = pub('js/push.js');
  assert.match(sw, /const SW_VERSION = 'aj-web-2026-11-09-0304-push'/);
  assert.match(push, /register\('sw\.js' \+ .*\{ scope: '\.\/' \}/);
  assert.match(push, /getSubscription\(\)/);
  assert.match(push, /!sub && hostEnabled === true/);
  assert.match(push, /generation === intent && !explicitlyOff\(\) && hostEnabled !== false/);
  assert.match(push, /if \(hostEnabled === enabled\) return/);
  assert.match(push, /localOff = true; \/\/ revoke in memory synchronously/);
  assert.match(push, /const durable = await markOff\(true\)/);
  assert.match(push, /await storedOff\(\)/);
  assert.match(push, /AJSeat\?\.db\?\.\('aj-push-intent'\)/);
  assert.match(push, /push intent store timeout/);
  assert.match(pub('app.js'), /case 'push_state': push\.gotPushStatus\(m\.on, m\.r\)/);
});
