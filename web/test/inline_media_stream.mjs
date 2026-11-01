// Inline media streaming: real isolated phone client, Noise fakehost, synthetic PNG only.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {deflateSync, crc32} from 'node:zlib';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,shoot,sleep} from './browser.mjs';
const out=process.env.AJ_SHOTS || 'reports/qa/inline-media-20261101/web';mkdirSync(out,{recursive:true});
const before=process.env.AJ_MEDIA_BEFORE==='1';
function png(){
 const chunk=(t,d)=>{const len=Buffer.alloc(4);len.writeUInt32BE(d.length);const td=Buffer.concat([Buffer.from(t),d]);const c=Buffer.alloc(4);c.writeUInt32BE(crc32(td));return Buffer.concat([len,td,c]);};
 const h=Buffer.alloc(13);h.writeUInt32BE(128,0);h.writeUInt32BE(80,4);h[8]=8;h[9]=2;
 const raw=Buffer.alloc((128*3+1)*80);for(let y=0;y<80;y++)for(let x=0;x<128;x++)raw.set(x<64?[199,222,220]:[235,201,183],y*385+1+x*3);
 return Buffer.concat([Buffer.from('\x89PNG\r\n\x1a\n','latin1'),chunk('IHDR',h),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);
}
const bytes=png(), mids=['A','B','C','D'].map(c=>c.repeat(22));
const refs=['/srv/fixture/a.png','~/fixture/b.png','/srv/fixture/c.png','~/fixture/d.png'];
const text=refs.map((r,i)=>`${i<2?'!':''}[Preview ${i+1}](${r})`).join('\n\n');
const items=refs.map((ref,i)=>({mid:mids[i],name:ref.split('/').pop(),ref,mime:'image/png',kind:'image',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')}));
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
const results=[];
const rec=parityRecorder('web/test/inline_media_stream.mjs');
try{
 for(const width of [390,1440])for(const theme of ['dark','light']){
  fake.reset();const lang=theme==='dark'?'zh':'en';
  let requests=0;
  fake.st.onApp=async(c,m,send)=>{if(m.t!=='media_get')return false;requests++;await send({t:'media_chunk',mid:m.mid,o:m.o,d:bytes.subarray(m.o).toString('base64url'),last:true});return true;};
  const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
  const tag=`${width}-${theme}`;let passed=false;
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();document.documentElement.dataset.theme='${theme}'`);
   await fake.send({t:'status',s:'working',agent:'codex',name:'Agent J'});
   const turn=await fake.addTurn({k:'phone',text:'Display synthetic previews'},text,'open');
   await waitFor(p,`document.querySelectorAll('#words .mslot').length===4`);
   writeFileSync(`${out}/${before?'before':'after'}-${tag}-pending.png`,await shoot(p));
   const pending=await evaluate(p,`[...document.querySelectorAll('#words .mslot')].map(s=>({state:s.dataset.unsent,text:s.textContent,links:s.querySelectorAll('a,img').length}))`);
   if(before){results.push({tag,pending});continue;}
   assert.ok(pending.every(s=>s.state==='wait'&&!s.text.includes('](')&&s.links===0),'all four complete references have non-clickable preparing notes');
   assert.equal(requests,0,'no path/URL fetch while waiting for metadata');
   // A half reference remains ordinary text; no media read is invented by the UI.
   await fake.updateTurn(turn.id,{reply:{text:text+'\n\n![Partial](~/fixture/half'}});
   await waitFor(p,`document.getElementById('words').textContent.includes('half')`);
   assert.equal(await evaluate(p,`document.querySelectorAll('#words .mslot').length`),4);
   assert.equal(requests,0);
   // Rejection replaces its pending slot while the turn is still active; metadata supplies the other three.
   await fake.updateTurn(turn.id,{media:items.slice(0,3),media_skip:[{name:'d.png',why:'gone'}]});
   await waitFor(p,`document.querySelectorAll('#words .mslot[data-mid]').length===3&&!!document.querySelector('#words .mslot[data-skip]')`);
   await waitFor(p,`document.querySelector('#words .mcard img')?.naturalWidth===128`);
   assert.equal(fake.st.turns.at(-1).end,'open');
   assert.equal(await evaluate(p,`document.body.dataset.status`),'working');
   await fake.send({t:'status',s:'working',agent:'codex',name:'Agent J',tool:'waiting'});
   writeFileSync(`${out}/after-${tag}-open-image-failure.png`,await shoot(p));
   await evaluate(p,`document.querySelector('#words .mslot[data-skip]').scrollIntoView({block:'center'})`);
   writeFileSync(`${out}/after-${tag}-open-failure-detail.png`,await shoot(p));
   const open=await evaluate(p,`({images:document.querySelectorAll('#words .mslot[data-mid]').length,failure:document.querySelector('#words .mslot[data-skip]').textContent,raw:document.getElementById('words').textContent.includes('Preview 1](')})`);
   assert.equal(open.raw,false);assert.ok(open.failure.length>0);
   // More work and final/end cannot duplicate the cards or opaque ids.
   await fake.updateTurn(turn.id,{reply:{text:text+'\n\nContinuing tools, waiting for the next result.'}});
   await waitFor(p,`document.getElementById('words').textContent.includes('Continuing tools')`);
   assert.equal(fake.st.turns.at(-1).end,'open');
   await fake.updateTurn(turn.id,{end:'done'});
   await waitFor(p,`document.querySelectorAll('#words .mslot[data-mid]').length===3`);
   assert.equal(await evaluate(p,`document.querySelectorAll('#words .mcard').length+document.querySelectorAll('#mstrip .mcard').length`),3);
   // A page with metadata still absent reports delay, then accepts metadata later without a stale timer overwriting it.
   const late=await fake.addTurn({k:'phone',text:'Delayed preview'},'[Delayed](~/fixture/a.png)','open');
   await waitFor(p,`document.querySelector('#words .mslot')?.dataset.unsent==='wait'`);
   await sleep(6200);
   await waitFor(p,`document.querySelector('#words .mslot')?.dataset.unsent==='1'`);
   const delayed=await evaluate(p,`document.querySelector('#words .mslot').textContent`);
   assert.ok(delayed&&!delayed.includes(']('));
   writeFileSync(`${out}/after-${tag}-delayed.png`,await shoot(p));
   await fake.updateTurn(late.id,{media:[{...items[0],ref:'~/fixture/a.png'}]});
   await waitFor(p,`!!document.querySelector('#words .mslot[data-mid] img')`);
   assert.equal(await evaluate(p,`document.querySelector('#words .mslot .mskip')===null`),true);
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
   results.push({tag,pending,open,delayed,endCards:3,isolated:true});
   passed=true;
  }finally{rec.record(`active-local-media ${tag}`,passed);await p.dispose();}
 }
 writeFileSync(`${out}/${before?'red':'green'}-results.json`,JSON.stringify(results,null,2));
 if(before)assert.ok(results.every(r=>r.pending.every(s=>s.state==='wait')),'RED: activity page must replace complete references with preparing state');
 console.log('inline-media phone: all passed');
}finally{rec.write();await B.close();await fake.stop();await web.stop();}
