import test from'node:test';import assert from'node:assert/strict';import{readFileSync}from'node:fs';
import{accountHtml,accountNodes,ACCOUNT_URL}from'../../brand/account-links.js';import{plan,SETS}from'../../brand/sync.mjs';
import {retainedUiSource} from './release-ui-source.mjs';
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
test('official account links never rewrite attributes, existing links, code, scripts or interactive labels',()=>{
 const src='<p title="account page > 账户页">打开账号页和 account page。</p><a href="https://elsewhere.invalid/">account page</a><code>账户页</code><script>"account page"</script><button>账号页</button><!-- account page -->';
 const out=accountHtml(src);assert.equal((out.match(/href="https:\/\/agentj.app\/account\/"/g)||[]).length,2);assert.ok(out.includes('title="account page > 账户页"'));assert.ok(out.includes('<a href="https://elsewhere.invalid/">account page</a>'));for(const s of['<code>账户页</code>','<script>"account page"</script>','<button>账号页</button>','<!-- account page -->'])assert.ok(out.includes(s));assert.equal(accountHtml(out),out);assert.match(out,/rel="noopener noreferrer"/);
});
test('runtime accountNodes uses fixed origin and text nodes, never interprets markup',()=>{
 const prior=globalThis.document;try{globalThis.document={createTextNode:text=>({textContent:text}),createElement:tag=>({tagName:tag,setAttribute(k,v){this[k]=v}})};const nodes=accountNodes('<img src=x onerror=evil()> account page',{className:'hint__link'});assert.equal(nodes[0].textContent,'<img src=x onerror=evil()> ');assert.equal(nodes[1].tagName,'a');assert.equal(nodes[1].href,ACCOUNT_URL);assert.equal(nodes[1].textContent,'account page');assert.equal(nodes[1].rel,'noopener noreferrer');}finally{globalThis.document=prior;}
});
test('one brand plan ships the helper only to consuming phone/admin; admin uses normal module and asset discovery',()=>{
 for(const set of['web','admin'])assert.ok(SETS[set].includes('account-links.js'));for(const set of['app','site'])assert.ok(!SETS[set].includes('account-links.js'));assert.ok(plan('/target','web').some(x=>x.dst==='/target/brand/account-links.js'));
 assert.match(read('../../host/agentj/admin/index.html'),/<script type="module" src="\/app.js"><\/script>/);assert.match(read('../../host/agentj/admin.py'),/brand\.rglob\("\*"\)/);assert.match(read('../../host/agentj/admin/app.js'),/accountNodes\(p,/);assert.match(retainedUiSource('public/js/t.js'),/accountNodes\(p\.slice/);assert.match(retainedUiSource('build.mjs'),/return accountHtml\(html\)/);assert.match(read('../public/js/t.js'),/accountNodes/);assert.match(read('../build.mjs'),/return accountHtml\(html\)/);
});
