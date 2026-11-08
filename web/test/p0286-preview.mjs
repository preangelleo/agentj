// Real Chromium preview negative control; a live local canary receives requests without interception.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {launch,newPage,navigate,evaluate,sleep,cdp,shoot} from './browser.mjs';
const out=resolve('reports/qa/0286/web/preview');mkdirSync(out,{recursive:true});
let hits=0;const canary=createServer((req,res)=>{hits++;res.end('canary')});await new Promise(r=>canary.listen(0,'127.0.0.1',r));const url=`http://127.0.0.1:${canary.address().port}/`;
const web=await startWebServer(),B=await launch(),p=await newPage(B,390,844,'light',{allow:[web.url,url]});
const hostile=`<meta http-equiv="refresh" content="0;url=http://127.0.0.1:9/refresh"><script>parent.postMessage('evil','*')</script><iframe src="http://127.0.0.1:9/frame"></iframe><style>@import url(http://127.0.0.1:9/import);body{background:url(http://127.0.0.1:9/css)}</style><img src="http://127.0.0.1:9/image"><svg xmlns="http://www.w3.org/2000/svg"><image href="http://127.0.0.1:9/svg"/><use href="http://127.0.0.1:9/use#x"/><a id="anim" href="#safe"><animate attributeName="href" values="http://127.0.0.1:9/smil" dur="1ms" fill="freeze"/><set attributeName="href" to="http://127.0.0.1:9/set" begin="0s"/>Animated link</a><foreignObject><img src="http://127.0.0.1:9/foreign"></foreignObject></svg><form action="http://127.0.0.1:9/form"><button>send</button></form><a href="http://127.0.0.1:9/link">outside</a><p>安全阅读内容</p>`.replaceAll("http://127.0.0.1:9/",url);
const probes=[];let target;
try{
 await navigate(p,web.url+'?lang=zh');
 p.on(m=>{if(m.method==='Network.requestWillBeSent'&&m.params.request.url.startsWith(url))probes.push(m.params.request.url);});
 await evaluate(p,`Promise.all([import('./js/mediawire.js'),import('./js/preview-document.js')]).then(([m,pv])=>{window.__evil=0;window.addEventListener('message',e=>{if(e.data==='evil')window.__evil++});const f=document.createElement('iframe');f.id='negative0286';f.style.cssText='position:fixed;inset:0;width:390px;height:600px;z-index:999999;background:white';for(const[k,v]of Object.entries(m.sandboxAttrs()))f.setAttribute(k,v);f.srcdoc=pv.previewDocument(${JSON.stringify(hostile)});document.body.append(f);window.__sanitized=f.srcdoc;})`);
 await sleep(500);const tree=await p.send('Page.getFrameTree'),targets=await B.browser.send('Target.getTargets');
 writeFileSync(join(out,'frame-diagnostic.json'),JSON.stringify({tree,targets:targets.targetInfos.map(x=>({type:x.type,url:x.url,id:x.targetId,context:x.browserContextId})),dom:await evaluate(p,`document.getElementById('negative0286').outerHTML`),problems:p.problems},null,2));
 const info=targets.targetInfos.find(x=>x.type==='iframe'&&x.browserContextId===p.ctx);
 assert.ok(info,'sandbox preview has isolated frame target');target=cdp(`ws://127.0.0.1:${B.port}/devtools/page/${info.targetId}`);await target.ready;
 await target.send('DOM.enable');const doc=await target.send('DOM.getDocument',{depth:-1,pierce:true});
 const dom=await target.send('DOM.getOuterHTML',{nodeId:doc.root.nodeId});
 const readable=dom.outerHTML;
 for(const selector of ['a:not([href])','form button']){
  const node=await target.send('DOM.querySelector',{nodeId:doc.root.nodeId,selector});assert.ok(node.nodeId,selector);
  const {model}=await target.send('DOM.getBoxModel',{nodeId:node.nodeId});const q=model.content,x=(q[0]+q[4])/2,y=(q[1]+q[5])/2;
  await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});
  await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});
 }
 await sleep(250);assert.ok(readable.includes('安全阅读内容'),'normal content is actually readable inside sandbox');assert.deepEqual(probes,[]);assert.equal(hits,0);assert.equal(await evaluate(p,'window.__evil'),0);
 const clean=await evaluate(p,'window.__sanitized');for(const marker of ['<script','<iframe','<animate','<set','http-equiv="refresh"','href="http','action='])assert.ok(!clean.includes(marker),marker);
 writeFileSync(join(out,'negative.png'),await shoot(p,''));
 writeFileSync(join(out,'results.json'),JSON.stringify({pass:true,canaryHits:hits,requestAttempts:probes,interception:false,executedScripts:0,readable:true,clickedAnchorAndSubmit:true,negativeFamilies:['script','refresh','iframe','CSS import/url','SVG image/use/foreignObject','SMIL animate/set','link navigation','form submit']},null,2));
 console.log('PASS real sandbox negative: readable, scripts0, requests0, canary0, click+submit inert');
}finally{target?.close();await p.dispose();await B.close();await web.stop();await new Promise(r=>canary.close(r));}
