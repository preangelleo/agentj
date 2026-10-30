import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
test('P579L unconfirmed switch names current reading and exposes retry recovery',()=>{
 for(const lang of ['zh','en']){
  const d=JSON.parse(read(`../i18n/web.${lang}.json`));
  assert.match(d.r.pill.why.unconfirmed,/\{model\}/);
  assert.doesNotMatch(d.r.pill.why.unconfirmed,/保持不变|unchanged|主机上|on the host/i);
  assert.equal(d['p579.chipOwner'],lang==='zh'?'主人':'Owner');
  assert.equal(d['p579.chipGuest'],lang==='zh'?'访客':'Guest');
  assert.ok(d['p579.retryModel']);
 }
 const s=read('../public/js/relay.js');
 assert.match(s,/model: cur\?\.usage\?\.model/);
 assert.match(s,/toastAction\(t\('r.pill.fail'[\s\S]{0,150}p579.retryModel[\s\S]{0,100}pill.want = pick; pillSend\(pick\)/);
});

test('P0261 mail owner label matches the approved Owner chip',()=>{
 for(const lang of ['zh','en']){
  const d=JSON.parse(read(`../i18n/web.${lang}.json`));
  const label=lang==='zh'?'主人':'Owner';
  assert.equal(d.mail.roleOwner,label);
  assert.ok(d.mail.roleNote.includes(label));
  assert.ok(d.ct.noMe.includes(label));
  assert.doesNotMatch(d.mail.roleNote,/我本人|“Me”/);
 }
});
