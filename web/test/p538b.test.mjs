import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
test('#538b browser default font does not resize settings; local reply choice remains correct without diagnostics', {timeout:60000},()=>{
 const result=spawnSync(process.execPath,[fileURLToPath(new URL('./p538b.mjs',import.meta.url))],{encoding:'utf8',timeout:55000});
 assert.equal(result.status,0,result.stdout+'\n'+result.stderr);
});
