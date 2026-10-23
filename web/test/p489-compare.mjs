// Audit immutable screenshots; keep the original red matrix intact.
import assert from 'node:assert/strict';import {readFileSync,writeFileSync} from 'node:fs';import {execFileSync} from 'node:child_process';
const root='reports/qa/p489/on0254/matrix',before=JSON.parse(readFileSync(root+'/before/results.json')),after=JSON.parse(readFileSync(root+'/after/results.json'));
const rows=[],failures=[];assert.equal(before.rows.length,54);assert.equal(after.rows.length,54);
for(const a of after.rows){
 const b=before.rows.find(r=>r.width===a.width&&r.mode===a.mode&&r.label===a.label);assert.ok(b);
 const rgb=p=>execFileSync('magick',[p,'-depth','8','rgb:-'],{maxBuffer:8e6}),ab=rgb(a.path),bb=rgb(b.path);
 let changed=0,outside=0,wave=0,bottom=0;const waveTop=Math.floor(Math.min(a.rect.top,b.rect.top))-20,waveBottom=Math.ceil(Math.max(a.rect.top,b.rect.top));
 for(let y=0;y<a.height;y++)for(let x=0;x<a.width;x++){
  const i=(y*a.width+x)*3;if(![0,1,2].some(k=>Math.abs(ab[i+k]-bb[i+k])>1))continue;changed++;
  if(y>=a.height-a.bottom)bottom++;else if(y>=waveTop&&y<=waveBottom)wave++;else outside++;
 }
 const target=a.mode==='chrome'&&['qa-j','air-0','unknown'].includes(a.label);
 if(outside||(!target&&bottom)|| (target&&!bottom))failures.push(`${a.width}-${a.mode}-${a.label}: unexpected changed pixels`);
 const diff=`${root}/diff-${a.width}-${a.mode}-${a.label}.png`;
 execFileSync('magick',[b.path,a.path,'-compose','difference','-composite',diff]);
 rows.push({width:a.width,mode:a.mode,label:a.label,before:b.path,after:a.path,diff,target,changed,bottom,wave,outside});
}
const result={base:before.base,combined:after.combined,rows,failures,policy:'Compare RGB tolerance1. Only CriOS zero/unknown bottom is intended. Existing paused waves may differ in initial phase: exclude exactly measured wave surface +/-20px; all other pixels must be unchanged.'};writeFileSync(root+'/comparison.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({pairs:rows.length,changed:rows.filter(r=>r.bottom).length,outside:rows.reduce((s,r)=>s+r.outside,0),failures},null,2));assert.equal(failures.length,0);
