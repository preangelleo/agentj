import {test} from 'node:test';
import assert from 'node:assert/strict';
import {browserEnvironment,probeEnvironment,publicPhoneLink} from '../public/js/phone-env.js';
const base={ua:'iPhone Safari',storage:{persist(){},persisted:async()=>true},supported:async()=>true,probe:async()=>true};
test('P98 in-app UA and platform fixtures',()=>{
 for(const app of ['MicroMessenger','QQ/8','QQBrowser','Weibo','Telegram','Line/14','FBAN','FBAV','Instagram']) assert.equal(browserEnvironment('iPhone '+app).inApp,true,app);
 assert.deepEqual(browserEnvironment('Android Chrome; wv)'),{platform:'android',inApp:true});
 assert.equal(browserEnvironment('Macintosh Safari',5).platform,'ios');
 assert.equal(browserEnvironment('iPhone Safari').inApp,false);
});
test('P98 storage failures, unsupported persist and authenticator are independent hints',async()=>{
 assert.equal((await probeEnvironment(base)).warn,false);
 for(const change of [{probe:async()=>{throw Error();}},{supported:async()=>false},{storage:{}},{storage:{persist(){},persisted:async()=>false}},{storage:{persist(){},persisted:async()=>{throw Error();}}},{ua:'Android MicroMessenger'}]) assert.equal((await probeEnvironment({...base,...change})).warn,true);
 const e=await probeEnvironment({...base,storage:{persist(){},persisted:async()=>false}});
 assert.equal(e.writable,true);assert.equal(e.platformAuth,true);assert.equal(e.privateBrowsing,undefined);
});
test('P98 copied public link strips secret fragments and query',()=>{
 assert.equal(publicPhoneLink('https://m.agentj.app/?relay=secret#p=one-use-secret'),'https://m.agentj.app/');
});
