import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const app = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const click = app.slice(app.indexOf('function testFaceId()'), app.indexOf('function testPkNotice('));
assert.match(click, /ask1\(\{ t: 'pk_check_ch' \}, 'pk_check_ch_res'/);
assert.match(click, /m\.why !== 'no_check'/);
assert.match(click, /for \(let attempt = 0; attempt < 2; attempt\+\+\)/);
assert.match(click, /session\.channel\(\) === ch0/);
console.log('PASS click-time challenge and bounded expired retry');
