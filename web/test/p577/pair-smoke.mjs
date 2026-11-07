#!/usr/bin/env node
// Four existing minimal scenes per side; validates same-Page/new-document topology, never a matrix qualification.
import {parseArgs} from 'node:util';import {resolve,join,dirname} from 'node:path';import {fileURLToPath} from 'node:url';
import {mkdirSync,existsSync,writeFileSync} from 'node:fs';import {spawn} from 'node:child_process';
const{values:o}=parseArgs({options:{source:{type:'string'},'source-after':{type:'string'},fixtures:{type:'string'},out:{type:'string'},modules:{type:'string'},chromium:{type:'string'},audit:{type:'boolean',default:false}}});
for(const key of ['source','fixtures','out'])if(!o[key])throw Error('pair-smoke.mjs --source <public> [--source-after <public>] --fixtures <repository> --out <new directory> --modules <existing modules> --chromium <binary> [--audit]');
const directory=resolve(o.out);if(existsSync(directory))throw Error('Smoke output must be a new directory');mkdirSync(directory,{recursive:true});
const scriptRoot=dirname(fileURLToPath(import.meta.url)),job={directory,scriptRoot,index:0,mode:'minimal',width:390,theme:'light',lang:'zh',audit:o.audit,sourceBefore:resolve(o.source),sourceAfter:resolve(o['source-after']||o.source),fixtures:resolve(o.fixtures),modules:o.modules&&resolve(o.modules),chromium:o.chromium&&resolve(o.chromium)};
const file=join(directory,'job.json');writeFileSync(file,JSON.stringify(job,null,2)+'\n');
process.exitCode=await new Promise((done,reject)=>{const child=spawn(process.execPath,[join(scriptRoot,'pair-worker.mjs'),file],{env:process.env,stdio:'inherit'});child.once('error',reject);child.once('exit',code=>done(code??1));});
