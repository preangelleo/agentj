import {test} from 'node:test';
import assert from 'node:assert/strict';
import {controlMessage} from '../../protocol/wire.js';
import {readFileSync} from 'node:fs';
test('owner signatures bind template, payment, billing and channel settings',async()=>{
 const request={op:'save',id:'a'.repeat(32),config:{template:'paid_qa',paid_qa:{free_questions:3,payment_url:'https://example.com/pay'},provider:{source:'own'},own_model:{base_url:'https://openrouter.ai/api/v1',model:'openai/gpt-4.1-mini',daily_tokens:1000},telegram:{enabled:true}}};
 const sign=r=>controlMessage('channel','owner','bots_write','b'.repeat(32),123,{request:r});
 const original=await sign(request);
 for(const config of [{...request.config,template:'companion'},{...request.config,telegram:{enabled:false}},{...request.config,paid_qa:{...request.config.paid_qa,free_questions:4}},{...request.config,provider:{source:'main'}},{...request.config,own_model:{...request.config.own_model,daily_tokens:2000}}])assert.notDeepEqual(original,await sign({...request,config}));
});
test('template and channel configuration never render a token/key input',()=>{
 const s=readFileSync(new URL('../public/js/bots.js',import.meta.url),'utf8');
 assert.doesNotMatch(s,/type\s*=\s*['"]password|localStorage|innerHTML/);
 for(const op of ['telegram_key','model_key'])assert.ok(s.includes(`op:'${op}'`));
 assert.ok(s.includes('Telegram can see messages'));assert.ok(s.includes('payment integration'));
});
