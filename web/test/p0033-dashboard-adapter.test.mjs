import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startCriticalDashboard,FIXED_NOW} from './p0033-dashboard-adapter.mjs';
test('real offline Dashboard accepts only its synthetic session and frozen publicRoot',async()=>{
 const root=mkdtempSync(join(tmpdir(),'aj0033-dashboard-'));
 writeFileSync(join(root,'index.html'),'<html><body>frozen-0033-account</body></html>');
 const dash=await startCriticalDashboard({publicRoot:root});
 try{
  const headers={cookie:dash.cookie.name+'='+dash.cookie.value};
  const page=await fetch(dash.url,{headers});assert.equal(page.status,200);assert.match(await page.text(),/frozen-0033-account/);
  const result=await fetch(dash.url+'api/me',{headers});assert.equal(result.status,200);const me=await result.json();assert.equal(me.email,'visual@example.invalid');assert.equal(me.passkeys,1);assert.equal(me.auth,'passkey');
  const anonymous=await fetch(dash.url+'api/me');assert.equal(anonymous.status,401);
  assert.deepEqual(dash.mail,[],'no local or external login email sent');
  assert.equal(FIXED_NOW,1793980000000);
 }finally{await dash.stop();rmSync(root,{recursive:true,force:true});}
});
test('adapter refuses an implicit live or unfrozen public root',async()=>{
 await assert.rejects(()=>startCriticalDashboard(),/Frozen Dashboard publicRoot/);
});
