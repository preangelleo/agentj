import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window = {};
const {authOf} = await import('../public/js/authcard.js');
import {elevateFields,elevateDigest} from '../../protocol/wire.js';
const auth={kind:'claude',url:'https://claude.com/cai/oauth/authorize?state=fixture',code:'',expires_at:2000000000,round:1,rounds:3};
test('P492 official host URL and bounded authorization fields only',()=>{
 assert.deepEqual(authOf(auth),auth);
 for(const url of ['javascript:alert(1)','http://claude.com/cai/oauth/authorize','https://claude.com.evil.test/cai/oauth/authorize','https://owner@claude.com/cai/oauth/authorize','https://claude.com:444/cai/oauth/authorize','https://claude.com/cai/oauth/authorize#state','https://claude.com/else','https://claude.com/cai/oauth/authorize?\nstate=x'])assert.equal(authOf({...auth,url}),null,url);
 for(const change of [{round:undefined},{rounds:undefined},{round:0},{round:4},{rounds:4},{expires_at:'2000000000'},{code:'<script>'}])assert.equal(authOf({...auth,...change}),null);
 assert.ok(authOf({...auth,kind:'codex',url:'https://auth.openai.com/codex/device',code:'ABCD-1234',rounds:1}));
});
test('P492 every authorization field is signed; ordinary card digest is unchanged',async()=>{
 const c={kind:'secret',name:'CLAUDE_AUTH_CODE',purpose:'fixture',dest:'local',verify:'live',auth};
 assert.deepEqual(elevateFields(c),[c.name,c.purpose,c.dest,c.verify,auth.kind,auth.url,'','2000000000','1','3']);
 const digest=await elevateDigest('secret',elevateFields(c));
 for(const [k,v] of Object.entries({kind:'codex',url:auth.url+'&changed=1',code:'DIFF-1234',expires_at:2000000001,round:2,rounds:2}))assert.notEqual(await elevateDigest('secret',elevateFields({...c,auth:{...auth,[k]:v}})),digest,k);
 assert.deepEqual(elevateFields({...c,auth:undefined}),[c.name,c.purpose,c.dest,c.verify]);
});
