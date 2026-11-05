import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
globalThis.crypto ??= webcrypto;
const {createInput,parseConnectors}=await import('../public/js/externalmcp-model.js');
test('MCP accepts exact HTTPS origins only; fields are bounded',()=>{
 assert.deepEqual(createInput(' Work ','https://assistant.example\nhttps://assistant.example'),{name:'Work',origins:['https://assistant.example']});
 for(const origin of ['http://assistant.example','https://assistant.example/','https://u:p@assistant.example','https://assistant.example/path','https://assistant.example?q=x','https://assistant.example#x','*','https://*.example']) assert.equal(createInput('Work',origin),null,origin);
 assert.equal(createInput('x'.repeat(41),'https://assistant.example'),null);assert.equal(createInput('Work',''),null);assert.equal(createInput('Work',Array(17).fill('https://a.example').join('\n')),null);
});
test('MCP metadata allowlist drops raw credentials, unknown fields and malformed connectors',()=>{
 const c={id:'A'.repeat(22),name:'Assistant',origins:['https://a.example','http://bad.example'],state:'active',scope:'request-only',created:1,last:{at:2},url:'https://hook.agentj.app/'+'H'.repeat(22)+'/'+'A'.repeat(22),token:'must-never-survive',body:'private'};
 const result=parseConnectors([c,{...c,id:'bad'},{...c,scope:'owner'},{...c,state:'unknown'}]);
 assert.equal(result.length,1);assert.deepEqual(result[0].origins,['https://a.example']);assert.equal(result[0].last,2);assert.equal(result[0].url,c.url);assert.ok(!JSON.stringify(result).includes('must-never'));assert.equal(result[0].body,undefined);
 for(const url of [c.url+'?token=bad',c.url+'#secret',c.url+'/extra',c.url.replace('hook.agentj.app','evil.example'),c.url.replace(c.id,'Z'.repeat(22))]) assert.equal(parseConnectors([{...c,url}])[0].url,'',url);
});
