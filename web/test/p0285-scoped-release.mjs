// Narrow release capture: real isolated Chromium with explicit UA/touch emulation.
// Geometry fixture only; no physical biometric, actual Host, or command-execution claim.
import {spawnSync} from 'node:child_process';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {createHash} from 'node:crypto';
const out=resolve(process.env.SCOPED_VISUAL_OUT||'reports/qa/0285-final-scoped-visual');
mkdirSync(out,{recursive:true});
const scenes=['approval','faceid-approve','faceid-wait','faceid-save','faceid-confirm','confirm','help-card'];
const rows=[],runs=[];
for(const [device,width] of [['android',390],['iphone',390],['desktop',1440]]){
  const dir=resolve(out,device);
  const argv=['web/test/p0285-rework-a.mjs'];
  const result=spawnSync(process.execPath,argv,{env:{...process.env,A_DEVICE:device,A_WIDTHS:String(width),A_LANGS:'zh',A_THEMES:'light,dark',A_SCENES:scenes.join(','),A_DYNAMIC:'1',A_INVENTORY:'0',A_PHASE:'after',A_CAPTURE:'1',A_OUT:dir},encoding:'utf8',timeout:180000});
  const log=resolve(out,device+'.log');writeFileSync(log,(result.stdout||'')+(result.stderr||''));
  if(result.status!==0)throw new Error(device+' related capture failed; see '+log);
  const proof=JSON.parse(readFileSync(resolve(dir,'results.json')));
  if(!proof.productStable||proof.failures.length||proof.rows.length!==14)throw new Error(device+' incomplete capture');
  if(runs.length&&JSON.stringify(proof.productManifest)!==JSON.stringify(runs[0].productManifest))throw new Error('product drift between devices');
  for(const row of proof.rows){
    if(row.device!==device||row.width!==width||!scenes.includes(row.scene))throw new Error('capture identity mismatch');
    const file=resolve(dir,row.file),bytes=readFileSync(file);
    if(!bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))throw new Error('invalid screenshot');
    rows.push({device,width,theme:row.theme,lang:row.lang,scene:row.scene,browserIdentity:row.browserIdentity,file,sha256:createHash('sha256').update(bytes).digest('hex')});
  }
  runs.push({device,argv:[process.execPath,...argv],log,checks:proof.checks.length,productManifest:proof.productManifest,scriptSha256:proof.scriptSha256});
}
writeFileSync(resolve(out,'capture-manifest.json'),JSON.stringify({schema:'agentj-scoped-capture-v1',fixture:'Isolated Chromium UA/touch emulation; synthetic geometry only, not physical or actual Host approval',rows,runs},null,2)+'\n');
console.log(JSON.stringify({result:'PASS',devices:3,configurations:6,screenshots:rows.length,checks:runs.reduce((n,r)=>n+r.checks,0)}));
