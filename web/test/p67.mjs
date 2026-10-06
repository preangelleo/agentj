// P67 local mobile-equivalent browser regression, fake host, real encrypted transport / fake camera.
// No real pairing URL or credentials in screenshots. QA-host acceptance is a separate integration step.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out = new URL('../../../reports/qa/p67-web/',import.meta.url);
mkdirSync(out,{recursive:true});
const log=[];
const ok=(condition,name)=>{assert.ok(condition,name);log.push({test:name,ok:true});console.log('PASS '+name);};
const web=await startWebServer();const fake=await startFakeHost();const B=await launch();
try {
 const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
 await navigate(p,web.url);await waitState(p,'idle');
 ok(await evaluate(p,`document.querySelector('.pair__hero').compareDocumentPosition(document.getElementById('pair-controls')) & Node.DOCUMENT_POSITION_FOLLOWING`),'pairing hero precedes paste field');
 await shoot(p,'').then(x=>writeFileSync(new URL('pairing.png',out),x));
 await evaluate(p,`document.getElementById('scan').click()`);
 await waitFor(p,`document.body.dataset.view==='scan' && document.getElementById('scan-video').srcObject?.active`);
 const cam=await evaluate(p,`(()=>{const v=document.getElementById('scan-video').getBoundingClientRect(); const b=document.getElementById('scan-trigger').getBoundingClientRect();return {w:v.width,h:v.height,control:b.bottom<=innerHeight,active:document.getElementById('scan-video').srcObject.active}})()`);
 ok(cam.w===390 && cam.h===844 && cam.control && cam.active && await evaluate(p,`getComputedStyle(document.querySelector('.top')).display==='none'`),'new camera page fills 390x844 screen with visible scan button');
 await shoot(p,'').then(x=>writeFileSync(new URL('camera.png',out),x));
 await evaluate(p,`window.p67Track=document.getElementById('scan-video').srcObject.getTracks()[0]; document.getElementById('scan-trigger').click()`);
 ok(await evaluate(p,`document.getElementById('scan-trigger').textContent==='暂停扫描'`),'camera Scan starts scanning and exposes pause');
 await evaluate(p,`document.getElementById('scan-back').click()`);await waitFor(p,`document.body.dataset.view==='pair'`);
 ok(await evaluate(p,`window.p67Track.readyState==='ended' && !document.getElementById('scan-video').srcObject`),'camera Back releases camera track');
 await evaluate(p,`document.getElementById('scan').click()`);await waitFor(p,`document.getElementById('scan-video').srcObject?.active`);
 await evaluate(p,`window.p67Track=document.getElementById('scan-video').srcObject.getTracks()[0];history.back()`);await waitFor(p,`document.body.dataset.view==='pair'`);
 ok(await evaluate(p,`window.p67Track.readyState==='ended'`),'browser Back releases camera track');
 await evaluate(p,`window.p67Gum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=()=>Promise.reject(new DOMException('denied','NotAllowedError'));document.getElementById('scan').click()`);
 await waitFor(p,`document.body.dataset.view==='scan' && !document.getElementById('camera-hint').hidden`);
 ok(await evaluate(p,`document.getElementById('camera-hint').textContent.includes('相机') && !document.getElementById('scan-back').hidden`),'camera permission rejection keeps visible new-page help and Back');
 await evaluate(p,`navigator.mediaDevices.getUserMedia=window.p67Gum;document.getElementById('scan-trigger').click()`);
 await waitFor(p,`document.body.dataset.view==='scan' && document.getElementById('scan-video').srcObject?.active`);
 ok(true,'camera permission retry starts a new stream in same page');
 await evaluate(p,`document.getElementById('scan-back').click()`);
 fake.st.asr='not_installed';
 fake.st.models={models:[{id:'p67/gpt-6',name:'p67/gpt-6',efforts:[]},{id:'p67/gpt-5',name:'p67/gpt-5',efforts:[]}],default:{model:'p67/gpt-6'}};
 fake.st.meter={model:'p67/gpt-6',ctx:{used:32000,max:128000},quota_windows:[{window:'weekly',pct:21},{window:'daily',pct:7}]};
 fake.st.onApp=async (_c,m,send)=>{
  if(m.t==='models_get'){await send({t:'models',r:m.r,...fake.st.models});return true;}
  if(m.t==='provider_get'){await send({t:'providers',r:m.r,providers:[{id:'p67',name:'Test provider',key_editable:true}]});return true;}
  if(m.t==='provider_key'){await send({t:'provider_res',r:m.r,ok:true});return true;}
  if(m.t==='asr_install'){await send({t:'asr_install_res',r:m.r,ok:true,state:'ready'});return true;}
 };
 const pairing=fake.newPairing(web.url);
 await evaluate(p,`window.BarcodeDetector=class {static async getSupportedFormats(){return ['qr_code']} async detect(){return [{rawValue:${JSON.stringify(pairing)}}]}};document.getElementById('scan').click()`);
 await waitFor(p,`document.body.dataset.view==='scan' && document.getElementById('scan-video').srcObject?.active`);
 await evaluate(p,`window.p67Track=document.getElementById('scan-video').srcObject.getTracks()[0];document.getElementById('scan-trigger').click()`);
 await waitState(p,'awaiting-approval'); await sleep(300);
 ok(await evaluate(p,`document.body.dataset.view==='sas' && window.p67Track.readyState==='ended'`),'successful scan ends camera and retains pairing approval page after browser history pop');
await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await waitFor(p,`document.getElementById('metaModel').textContent==='p67/gpt-6'`);
 ok(await evaluate(p,`document.getElementById('mWeek').querySelector('.lab').textContent==='weekly 21%' && document.getElementById('m5h').querySelector('.lab').textContent==='daily 7%'`),'weekly/daily quota labels follow provider measurements');
 ok(await evaluate(p,`document.getElementById('water').dataset.lvl==='25' && document.getElementById('water').title==='32000 / 128000'`),'context reports exact used/limit and 25% waterline');
 await fake.send({t:'status',v:'idle',name:'QA-J'});
 await evaluate(p,`document.getElementById('aj-menu').open=true`);
 ok(await evaluate(p,`(()=>{const e=document.querySelector('.ajmenu__name');return e.clientHeight>=44 && getComputedStyle(e).flexShrink==='0'})()`),'menu agent name has non-shrinking full-height row');
 await shoot(p,'').then(x=>writeFileSync(new URL('menu.png',out),x));
 await evaluate(p,`document.getElementById('open-models').click()`);
 await waitFor(p,`document.getElementById('provider-options').children.length===1`);
 await shoot(p,'').then(x=>writeFileSync(new URL('models-key.png',out),x));
 await evaluate(p,`document.getElementById('model-options').children[1].click()`);
 await waitFor(p,`document.getElementById('provider-status').textContent===''`);await sleep(100);
 ok(fake.st.modelSets.some(m=>m.model==='p67/gpt-5'),'OpenCode model selection reaches model_set provider/model id');
 await evaluate(p,`document.getElementById('provider-options').children[0].click()`);
 await waitFor(p,`document.getElementById('provider-status').textContent.includes('密钥卡')`);
 ok(fake.log.some(m=>m.t==='provider_key'&&m.provider==='p67'),'provider Key entry requests existing encrypted key card without key values');
 await evaluate(p,`document.getElementById('models-back').click();document.getElementById('mic').dispatchEvent(new PointerEvent('pointerdown',{pointerId:1,clientY:700,bubbles:true,isPrimary:true,button:0}))`);
 await waitFor(p,`!document.getElementById('confirm').hidden`);
 await shoot(p,'').then(x=>writeFileSync(new URL('asr-install.png',out),x));
 await evaluate(p,`document.getElementById('confirm-yes').click()`);await sleep(200);
 ok(fake.log.some(m=>m.t==='asr_install' && m.enable===true),'missing ASR offers host install before recording');
 await fake.send({t:'meter',model:'p67/gpt-5',quota_windows:[],ctx:{used:32000,max:128000}});
 await waitFor(p,`document.getElementById('mWeek').hidden && document.getElementById('m5h').hidden`);
 ok(await evaluate(p,`getComputedStyle(document.getElementById('mWeek')).display==='none' && getComputedStyle(document.getElementById('m5h')).display==='none'`),'missing usage probe hides both quota bars');
 await fake.send({t:'meter',model:'p67/gpt-5',quota_windows:[{window:'monthly',pct:14}],ctx:{used:32000,max:128000}});
 await waitFor(p,`document.getElementById('mWeek').querySelector('.lab').textContent==='monthly 14%'`);
 ok(await evaluate(p,`document.getElementById('m5h').hidden`),'monthly-only usage shows measured window only');
 await fake.send({t:'hist_turn',epoch:fake.st.epoch,turn:{id:999,ts:Date.now(),src:{k:'sys'},reply:{text:'这是一条测试提示。'},end:'done'}});
 await waitFor(p,`document.getElementById('words').textContent.includes('测试提示')`);
 ok(await evaluate(p,`document.getElementById('om').hidden`),'system notice without source input hides ambiguous original-message card');
 await sleep(6500);
 await fake.send({t:'meter',model:'unknown',quota_windows:[],ctx:{used:32000,max:null}});
 await waitFor(p,`document.getElementById('water').title==='32000 tokens'`);
 ok(await evaluate(p,`document.getElementById('water').dataset.known==='0' && document.getElementById('water').dataset.lvl==='' && document.getElementById('water').style.getPropertyValue('--lvl')==='0' && document.getElementById('water').getAttribute('aria-label')==='32000 tokens'`),'unknown limit retains used tokens without percentage or waterline');
 await fake.send({t:'meter',model:'p67/gpt-6',quota_windows:[],ctx:{used:32000,max:128000}});
 await waitFor(p,`document.getElementById('water').title==='32000 / 128000'`);
 const water=await evaluate(p,`(()=>{const w=document.getElementById('water');return {level:w.dataset.lvl,height:w.getBoundingClientRect().height,containerHeight:w.parentElement.getBoundingClientRect().height,title:w.title,known:w.dataset.known}})()`);
 ok(water.known==='1' && water.height>0 && water.title==='32000 / 128000','context water has nonzero computed height and exact token label');
 writeFileSync(new URL('context-computed.json',out),JSON.stringify(water,null,2)+'\n');
 await shoot(p,'').then(x=>writeFileSync(new URL('notice-context.png',out),x));
 ok(p.problems.length===0,'no browser errors');
 writeFileSync(new URL('browser-results.json',out),JSON.stringify({kind:'local fake host, not QA-machine acceptance',viewport:'390x844',tests:log,errors:p.problems},null,2)+'\n');
} finally {await B.close();await fake.stop();await web.stop();}
