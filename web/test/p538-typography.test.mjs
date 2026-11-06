import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { typography, snapshotPath, storedSizes } from './p538-typography.mjs';
test('#538 computed typography matches previous formal release at all supported user sizes', { timeout: 60000 }, async () => {
  assert.deepEqual(await typography(), JSON.parse(readFileSync(snapshotPath)).computed,
    'Typography drift requires a task explicitly authorizing size/line-height changes and a reviewed snapshot.');
});

test('#538 real page startup preserves all stored font/page sizes and unrelated preferences', {timeout:60000}, async()=>{
  for(const {page,scale,result} of await storedSizes()) assert.deepEqual(result,
    {scale:scale===null?null:String(scale),page,rs:scale===null||scale==='invalid'?'1':String(scale),fz:page,sentinel:'keep'});
});
