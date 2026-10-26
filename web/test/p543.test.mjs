// P543 (#308): an owner-channel message page (host card.channel) shows the words that were sent — plain text under the
// host's source label and the low-trust mark, long text folded behind 「展开」, the inbox location only behind 「…」.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const fn=source.slice(source.indexOf('const chanOpen = new Set();'),source.indexOf('// A command result page'));
function load(){
 const made=[];
 const ctx=vm.createContext({t:k=>k,showPage:()=>{ctx.redrawn=(ctx.redrawn||0)+1;},
   document:{createElement:tag=>{const n={tag,dataset:{},children:[],append(...c){this.children.push(...c);},appendChild(c){this.children.push(c);},addEventListener(k,f){this.click=f;}};made.push(n);return n;}}});
 vm.runInContext(fn+';this.chanCard=chanCard;this.chanWords=chanWords;this.paintChan=paintChan;this.chanOpen=chanOpen;',ctx);
 return ctx;
}
const box=()=>({children:[],replaceChildren(){this.children=[];},appendChild(c){this.children.push(c);}});
const page=(text,extra={})=>({id:7,source:{k:'sys',via:'wechat'},reply:'来自微信：'+text.slice(0,200),
  card:{channel:{source:'wechat',label:'来自微信',text,cut:false,files:['a.jpg'],paths:['/w/inbox/x/wechat-message.json','/w/inbox/x/a.jpg'],...extra}}});
test('the words, not the inbox path; plain text under the label and the low-trust mark',()=>{
 const c=load(),p=page('越疆科技今天为什么涨？');
 const ch=c.chanCard(p);assert.ok(ch);
 const w=c.chanWords(p,ch);
 assert.equal(w,'来自微信 · p543.chan.untrusted\n\n越疆科技今天为什么涨？');
 assert.doesNotMatch(w,/inbox|\.json/);
 const b=box();c.paintChan(b,p,ch);
 assert.equal(b.hidden,false);
 const d=b.children.find(x=>x.tag==='details');assert.ok(d,'paths only behind the details');
 assert.equal(d.children[0].textContent,'…');
 assert.match(d.children[1].textContent,/wechat-message\.json/);
 assert.ok(!b.children.some(x=>x.tag==='button'),'short text: no fold button');
 assert.match(source,/p\.source\?\.k === "friend_report" \|\| !!chan\);/,'rendered as plain text (never Markdown)');
});
test('long text folds behind 展开 and opens in place; a cut message says so when open',()=>{
 const c=load(),long='很'.repeat(900),p=page(long,{cut:true});
 const ch=c.chanCard(p);
 assert.ok(c.chanWords(p,ch).endsWith('…'));
 assert.ok(!c.chanWords(p,ch).includes('p543.chan.cut'));
 const b=box();c.paintChan(b,p,ch);
 const btn=b.children.find(x=>x.tag==='button');assert.equal(btn.textContent,'p543.chan.more');
 btn.click();assert.equal(c.redrawn,1);
 assert.ok(c.chanWords(p,ch).includes(long));assert.ok(c.chanWords(p,ch).endsWith('p543.chan.cut'));
 const b2=box();c.paintChan(b2,p,ch);assert.equal(b2.children.find(x=>x.tag==='button').textContent,'p543.chan.less');
});
test('only host sys pages with a known source count; older pages keep their words',()=>{
 const c=load();
 assert.equal(c.chanCard({source:{k:'phone'},card:{channel:{source:'wechat',text:'x'}}}),null);
 assert.equal(c.chanCard({source:{k:'sys'},card:{channel:{source:'sms',text:'x'}}}),null);
 assert.equal(c.chanCard({source:{k:'sys'},card:{}}),null);
 assert.ok(c.chanCard({source:{k:'sys'},card:{channel:{source:'telegram',text:''}}}));
});
