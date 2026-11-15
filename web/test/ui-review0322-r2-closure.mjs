import {readFileSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {join} from 'node:path';
const checks=[],check=(name,ok,detail)=>checks.push({name,ok:!!ok,detail});
const read=(ref,path)=>execFileSync('git',['show',ref+':web/public/'+path]);
for(const path of ['js/t.js','js/bots.js','js/ui.js','js/rescue.js','js/rvpair.js','js/media.js','js/forms.js','js/fsedit.js','js/popup-layout.js'])check('closed UI source remains actual 0.30.21: '+path,read('70c8cf3e5',path).equals(read('HEAD',path)),path);
check('U08 authcard unchanged from task base incl retained exact-domain safety',read('5bba7fcee9','js/authcard.js').equals(read('HEAD','js/authcard.js')),'0.30.21 had broader Kimi domain matching; task keeps existing safety restriction.');
const prior=JSON.parse(readFileSync(process.env.UI_PRIOR_INDEX,'utf8'));
check('prior full matrix complete',prior.rows.length===112&&!prior.rows.some(r=>r.error||r.problems?.length),{scenes:prior.rows.length,source:prior.refs.after});
for(const theme of ['light','dark']){const b=prior.rows.find(r=>r.phase==='before'&&r.theme===theme&&r.name==='main'),a=prior.rows.find(r=>r.phase==='after'&&r.theme===theme&&r.name==='main');const shape=r=>r.visibleNodes.map(n=>[n.tag,n.id]);check(theme+' main no new visible row or control',JSON.stringify(shape(a))===JSON.stringify(shape(b)),{before:shape(b),after:shape(a)});}
const index=JSON.parse(readFileSync(process.env.UI_FINAL_INDEX,'utf8'));
check('final matrix no errors',index.rows.length>=72&&!index.rows.some(r=>r.error||r.problems?.length),{scenes:index.rows.length,source:index.refs.after});
for(const r of index.rows.filter(r=>r.phase==='after'&&['relay-keys','auth-checking'].includes(r.name))){const b=index.rows.find(b=>b.phase==='before'&&b.name===r.name&&b.theme===r.theme&&b.width===r.width);const bh=b.headings.find(h=>h.id==='set-title'||h.parent?.id==='set-head'||h.text===r.headings[0]?.text),ah=r.headings.find(h=>h.text===bh?.text);check(r.name+'/'+r.theme+'/'+r.width+' settings header unchanged',bh&&ah&&Math.abs(bh.y-ah.y)<1,{before:bh,after:ah});}
writeFileSync(join(process.env.OLARES_RUN_OUT,'closure.json'),JSON.stringify({checks,passed:checks.filter(c=>c.ok).length,failed:checks.filter(c=>!c.ok).length},null,2));console.log('UI CLOSURE',checks.filter(c=>c.ok).length,'PASS',checks.filter(c=>!c.ok).length,'FAIL');if(checks.some(c=>!c.ok))process.exitCode=1;
