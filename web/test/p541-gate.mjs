#!/usr/bin/env node
// Existing p44 phase runs these real-client suites against the selected --tree.
import {spawnSync} from 'node:child_process';
const base=new URL('./',import.meta.url);
for(const file of ['p541.mjs','p541-special.mjs','p541-boundary.mjs','p541-question.mjs','p541-review-follow.mjs','p541-colors.mjs']){
 const r=spawnSync(process.execPath,[new URL(file,base).pathname],{cwd:process.cwd(),env:process.env,stdio:'inherit'});
 if(r.status!==0)process.exit(r.status||1);
}
