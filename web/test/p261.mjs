// P261: real shipped page + local Noise fake host; no production / QA account writes.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, key } from './browser.mjs';
const OUT = process.env.AJ_SHOTS || fileURLToPath(new URL('../../../reports/qa/p261/screens/', import.meta.url));
export const CASES = { a: 'folded-count-and-height', b: 'three-line-budget-at-all-sizes', c: 'expand-button-double-click-hotkey', d: 'page-refresh-and-zero-count' };
const images = Array.from({length:5}, (_,i)=>({name:`screenshot-${i+1}.png`,kind:'image'}));
const mixed = [...images,{name:'notes.txt',kind:'file'},{name:'voice.wav',kind:'audio'}];
const LONG = '很长的主人消息，保留所有细节，收起时只占有限空间。'.repeat(35);
const Q = {id:1,who:'示例主人的 iPhone Air · iOS Safari',ts:1760000000000,text:'这是引用的完整内容。'.repeat(30)};
export async function runP261(){
  mkdirSync(OUT,{recursive:true});
  const rec=parityRecorder('web/test/p261.mjs');
  let web, fake, B, checks=0;
  const check=(v,l)=>{assert.ok(v,l);checks++;};
  const step=async(c,f)=>{try{await f();rec.record(CASES[c],true);}catch(e){rec.record(CASES[c],false);throw e;}};
  try {
    web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});fake=await startFakeHost();B=await launch();
    for(const width of [393,1440]) for(const theme of ['light','dark']) {
      fake.reset();
      const p=await newPage(B,width,852,theme,{touch:width===393,allow:[web.url,fake.relay+'/']});
      const ev=s=>evaluate(p,s);
      const click=()=>ev(`document.getElementById('omMore').click()`);
      const save=async n=>writeFileSync(join(OUT,`${width}-${theme}-${n}.png`),await shoot(p,''));
      let ownerDevice;
      const add=async(text,att=[],quote=null)=>{
        const turn=await fake.addTurn({k:'phone',dev:ownerDevice,text,att,quote},'回复内容有足够的空间可读。\n\n'+ '展开原话可以查看全部细节。'.repeat(5));
        await waitFor(p,`document.getElementById('pg').textContent.startsWith('${fake.st.turns.length} /') && !document.getElementById('om').classList.contains('open')`);
      };
      const height=()=>ev(`document.getElementById('om').getBoundingClientRect().height`);
      const count=()=>ev(`document.getElementById('omAttCount').textContent.trim()`);
      try {
        await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
        ownerDevice=await ev(`import('./js/api.js').then(m=>m.myDeviceId())`);
        await ev(`document.getElementById('phone-env-dismiss')?.click()`);
        await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
        await step('a',async()=>{
          await add('帮我看看这五张截图。');const plain=await height();
          check(await ev(`document.getElementById('om').dataset.src==='leo'`),'fixture is this paired owner remote');
          await add('帮我看看这五张截图。',images);
          check(Math.abs(await height()-plain)<.1,'five screenshots do not add height');
          check(await count()==='5','all five counted');
          check(await ev(`document.getElementById('omAtt').hidden && !document.getElementById('omAtt').children.length`),'no folded attachment nodes');
          check(await ev(`!!document.querySelector('#omAttCount svg[stroke="currentColor"]')`),'line icon inherits text colour');
          check(await ev(`document.getElementById('omAttCount').getBoundingClientRect().right <= document.getElementById('omMore').getBoundingClientRect().left`),'count left of expand');
          await save('five-folded');await click();
          check(await ev(`document.querySelectorAll('#omAtt .omchip').length===5`),'expand shows all five');await save('five-expanded');await click();
          await add('混合附件',mixed);check(await count()==='7','images files and audio counted together');
          await click();check(await ev(`document.querySelectorAll('#omAtt .omchip').length===7`),'all mixed attachments expand');await click();
        });
        await step('b',async()=>{
          for(const fz of ['s','m','l']){
            await ev(`document.documentElement.dataset.fz='${fz}'`);
            // Reference is the old screenshot layout: one quote heading + excerpt, body one line, same padding.
            await add('还有做完呢？',[],Q);const cap=await height();
            const scale={s:.85,m:1,l:1.2}[fz];
            check(cap<=74+82.65*scale+.2,`reference ${cap} <= ${74+82.65*scale}, metrics ${JSON.stringify(await ev(`[...document.querySelectorAll('#omhd,#om .omhd,#omQuote,#omText,#om .qh,#om .qt')].map(e=>({cls:e.className,h:e.getBoundingClientRect().height,lh:getComputedStyle(e).lineHeight}))`))}`);
            for(const quote of [null,Q]) for(const text of ['短消息',LONG,'第一行\n\n第二行\n第三行\n第四行','']) for(const att of [[],mixed]){
              await add(text,att,quote);
              const h=await height();check(h<=cap+.15,`${width}/${theme}/${fz}: ${h} <= reference ${cap}`);
              const lines=await ev(`getComputedStyle(document.getElementById('omText')).webkitLineClamp`);
              check(lines===(quote?'1':'3'),'quote gets one body line, otherwise three');
            }
            await add(LONG,mixed,Q);await save(`${fz}-quote-folded`);
          }
          await ev(`document.documentElement.dataset.fz='m'`);
          await add(LONG,mixed,Q);await click();
          check(await ev(`document.getElementById('omText').textContent.length===${LONG.length} && document.getElementById('omqText').scrollHeight<=document.getElementById('omqText').clientHeight+1`),'expanded original and full quote retained');await click();
        });
        await step('c',async()=>{
          await click();check(await ev(`document.getElementById('omMore').getAttribute('aria-expanded')==='true'`),'button expands');
          await key(p,'Escape');await waitFor(p,`!document.getElementById('om').classList.contains('open')`);
          await ev(`document.activeElement?.blur()`);await key(p,'o');await waitFor(p,`document.getElementById('om').classList.contains('open')`);await key(p,'o');
          await waitFor(p,`!document.getElementById('om').classList.contains('open')`);
          const point=await ev(`(()=>{const r=document.getElementById('omText').getBoundingClientRect();return{x:r.x+10,y:r.y+8}})()`);
          for(let i=1;i<=2;i++){
            await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:i,...point});
            await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:i,...point});
          }
          await waitFor(p,`document.getElementById('om').classList.contains('open')`);
          check(await ev(`document.querySelectorAll('#omAtt .omchip').length===7`),'double click reveals all attachments');await click();
        });
        await step('d',async()=>{
          await add('无附件');check(await ev(`document.getElementById('omAttCount').hidden`),'zero has no count');
          await key(p,'ArrowLeft');await waitFor(p,`document.getElementById('omAttCount').textContent.trim()==='7'`);
          check(await ev(`!document.getElementById('omAttCount').hidden && !document.getElementById('omAtt').children.length`),'paging restores count, stays folded');
          await key(p,'ArrowRight');await waitFor(p,`document.getElementById('omAttCount').hidden`);
          await click();await add('新消息',[mixed[6]]);
          check(await count()==='1' && await ev(`!document.getElementById('omAtt').children.length`),'new page clears expanded nodes and updates count');
        });
      } finally {await p.close();}
    }
    writeFileSync(join(OUT,'assertions.json'),JSON.stringify({checks,widths:[393,1440],themes:['light','dark'],fontSizes:['s','m','l']},null,2));
    console.log(`P261 PASS: ${checks} assertions`);
  } finally {if(B)await B.close();if(fake)await fake.stop();if(web)await web.stop();rec.write();}
}
if(process.argv[1]===fileURLToPath(import.meta.url))await runP261();
