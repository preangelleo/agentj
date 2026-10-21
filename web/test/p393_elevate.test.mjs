// #116 / #112: static contract of the 10-minute same-class admin approval on the page.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
const en=JSON.parse(read('../i18n/web.en.json')), zh=JSON.parse(read('../i18n/web.zh.json')), src=JSON.parse(read('../i18n/web.zh.src.json')), ov=JSON.parse(read('../i18n/web.zh.override.json'));
const KEYS=['elevGrant.toggle','elevGrant.toggleFmt','elevGrant.active','elevGrant.revoke','elevGrant.revoked','elevGrant.autoRan','elevGrant.autoFail'];
test('grant messages are dispatched and the revoke goes to the host',()=>{
  const app=read('../public/app.js'), el=read('../public/js/elevate.js');
  assert.match(app,/case 'elev_grants': elevate\.grants\(m\)/);
  assert.match(app,/case 'elev_auto': elevate\.auto\(m\)/);
  assert.match(el,/sendApp\(\{ t: 'elev_revoke' \}/);
  assert.match(el,/const grant = ok && !nopw && c\.grant && \$\('elev-grant-on'\)\.checked \? c\.grant\.h : null;/,'only when switched on');
  assert.match(el,/elevateMessage\(ch, dev, c\.id, c\.kind, ok \? 'allow' : 'deny', c\.n, ts, digest, ctSha, grant(?:, saveSha)?\)/,'signed');   // P414: + the save: tail (M2)
  assert.match(el,/\$\('elev-grant-on'\)\.checked = false;/,'off by default on every card shown');
  assert.match(el,/c\.grant = !c\.interpreter && g &&/,'a shell / interpreter card never offers the window');
  assert.match(el,/export function clear\(\) \{\n  clearTimeout\(recoveryTimer\); recoveryTimer = 0;\n  grants\(\[\]\);/,'unpair forgets the bar');
});
test('the signed message carries the grant line only when switched on',async()=>{
  const {elevateMessage}=await import('../../protocol/wire.js');
  const d=new TextDecoder(), a=['C','D','r'.repeat(32),'sudo','allow','n',1,'d','c'];
  assert.equal(d.decode(elevateMessage(...a)),d.decode(elevateMessage(...a,null)));
  assert.equal(d.decode(elevateMessage(...a,'ab')),d.decode(elevateMessage(...a))+'\ngrant:ab');
});
test('both languages say what the approval allows, the exception and revoke now',()=>{
  for(const k of KEYS){ assert.ok(en[k],k); assert.ok(zh[k],k); assert.equal(src[k],zh[k],k); assert.equal(ov[k],zh[k],k); }
  assert.match(en['elevGrant.toggle'],/\{n\} minutes.*read-only \{prog\}.*same format/); assert.match(en['elevGrant.toggleFmt'],/\{fmt\}/);
  assert.match(zh['elevGrant.toggle'],/\{n\} 分钟内，同样参数格式的 \{prog\} 只读命令不再询问/); assert.match(zh['elevGrant.toggleFmt'],/\{fmt\}/);
  assert.equal(zh['elevGrant.revoke'],'收回授权'); assert.equal(en['elevGrant.revoke'],'Revoke');
  for(const k of KEYS) assert.doesNotMatch(en[k]+zh[k],/P39\d|#11\d|试用|trial/i);
});
