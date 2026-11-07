import test from 'node:test';import assert from 'node:assert/strict';import {spawnSync} from 'node:child_process';import {mkdtempSync,rmSync,mkdirSync} from 'node:fs';import{join}from'node:path';import{fileURLToPath}from'node:url';
const web=fileURLToPath(new URL('../',import.meta.url)),reports=join(web,'reports');mkdirSync(reports,{recursive:true});
function run(tool){const tmp=mkdtempSync(join(reports,'p577-unit-'));try{const out=spawnSync(process.execPath,[join(web,'tools',tool),'--output',join(tmp,'result.json')],{encoding:'utf8',env:{...process.env,TMPDIR:tmp},timeout:60000});assert.equal(out.status,0,out.stdout+'\n'+out.stderr);}finally{rmSync(tmp,{recursive:true,force:true});}}
test('#577 all authored CSS has registered ownership and no cross-owner appearance',()=>run('css-namespace-lint.mjs'));
test('#577 namespace/parser adversarial controls',()=>run('namespace-negative-controls.mjs'));
test('#577 impact cannot qualify missing, empty or source-unbound evidence',()=>run('impact-negative-controls.mjs'));
test('#577 every registered owner has its explicit native contracts',()=>{const out=spawnSync(process.execPath,[join(web,'tools/sync-native-contracts.mjs'),'--check'],{encoding:'utf8',timeout:60000});assert.equal(out.status,0,out.stdout+'\n'+out.stderr);});

test('#577 audited node ownership source cannot drift',()=>{const out=spawnSync(process.execPath,[join(web,'tools/generate-node-owners.mjs'),'--check'],{encoding:'utf8',timeout:60000});assert.equal(out.status,0,out.stdout+'\n'+out.stderr);});

test('#577 native impact requires source-bound actual dictionary consumers',()=>run('native-impact-negative-controls.mjs'));
