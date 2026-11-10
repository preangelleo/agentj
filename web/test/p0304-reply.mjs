import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,sleep} from './browser.mjs';
const out=fileURLToPath(new URL('../../../reports/qa/p0304/',import.meta.url));mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
const fence=x=>'```agentj\n'+JSON.stringify(x)+'\n```';
const body='[Send this](reply:Send this)\n\n'+fence({component:'confirm',title:'Choice',message:'Pick one',choices:['First','Second']})+'\n\n'+fence({component:'form',title:'Details',fields:[{id:'note',label:'Note',type:'text'}]})+'\n\n'+fence({component:'steps',title:'Progress',items:[{label:'Review',status:'active'}]})+'\n\n'+fence({component:'chart',title:'Volume',kind:'bar',items:[{label:'Today',value:4}]})+'\n\n[External](https://example.com)';
const font='/usr/share/fonts/liberation/LiberationSans-Regular.ttf';
// The composer stays one line tall (P412) and scrolls inside. Lines wrap at 390 px, so measure the draft line's real
// offset with the same textarea (value up to the end of the needle), restore the value, then put that line in the box.
async function revealDraft(p,needle){
 const r=await evaluate(p,`(()=>{const i=document.getElementById('input'),v=i.value,at=v.lastIndexOf(${JSON.stringify(needle)});if(at<0)return null;
  const cs=getComputedStyle(i),lh=parseFloat(cs.lineHeight)||parseFloat(cs.fontSize)*1.45,pad=parseFloat(cs.paddingTop)+parseFloat(cs.paddingBottom);
  i.value=v.slice(0,at+${JSON.stringify(needle)}.length);const line=Math.max(0,i.scrollHeight-pad-lh);i.value=v;
  i.scrollTop=Math.round(line);return {line,top:i.scrollTop,same:i.value===v}})()`);
 assert.ok(r,'draft text present in composer: '+needle);assert.ok(r.same,'composer value restored');await sleep(80);return r;
}
async function shot(p,filename,label){
 const {data}=await p.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
 const labeled=execFileSync('magick',['png:-','-gravity','north','-background','#2c2b26','-fill','#faf9f5','-font',font,'-pointsize','16','-splice','0x32','-annotate','+0+7',label,'png:-'],{input:Buffer.from(data,'base64'),maxBuffer:24*1024*1024});
 writeFileSync(out+filename,labeled);
}
try{for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
 fake.reset();fake.st.prefs={appearance:{language:lang,theme}};
 const p=await newPage(B,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`(()=>{for(const id of ['confirm-no','a2hs-ok','phone-env-dismiss']){const e=document.getElementById(id);if(e&&e.getBoundingClientRect().height)e.click();}})()`);
  await waitFor(p,`document.documentElement.dataset.theme===${JSON.stringify(theme)}`);
  assert.equal(await evaluate(p,`matchMedia('(prefers-color-scheme: dark)').matches`),theme==='dark');
  await fake.send(theme==='light'?{t:'theme',v:1,id:'qa-light',colors:{menu:{background:'#faf9f5',panel:'#f0eee6',text:'#141414',accent:'#141414',line:'#e3dacc'}}}:{t:'theme',v:1,factory:'robots'});
  await waitFor(p,`getComputedStyle(document.documentElement).getPropertyValue('--menu-bg').trim()==='${theme==='light'?'#faf9f5':'#2c2b26'}'`);
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
  const turn=await fake.addTurn({k:'phone',dev:'x',text:'Show options'},body,'done');
  await waitFor(p,`document.querySelector('#words [data-component=confirm] button:not(:disabled)')`);
  const styles=await evaluate(p,`(()=>{const css=s=>getComputedStyle(document.querySelector(s));return {reply:css('#words .rbtn').paddingLeft,confirm:css('#words .rc-confirm').backgroundColor,form:css('#words .rc-draft').backgroundColor,link:css('#words a[href^="http"]').color,hint:document.querySelector('#words [data-component=confirm] .rc-note').textContent,theme:document.documentElement.dataset.theme,deep:css('html').getPropertyValue('--deep').trim(),menu:css('html').getPropertyValue('--menu-bg').trim(),state:css('html').getPropertyValue('--c-idle').trim(),canvas:css('body').backgroundColor}})()`);
  assert.equal(styles.theme,theme);assert.equal(styles.deep,theme==='dark'?'28%':'0%');
  assert.equal(styles.menu,theme==='light'?'#faf9f5':'#2c2b26');
  assert.equal(styles.state,theme==='light'?'#629887':'#629887','idle state hue stays safe for Lotion reply text');
  assert.equal(styles.reply,'20px');assert.equal(styles.confirm,'rgb(44, 43, 38)');assert.equal(styles.form,'rgb(44, 43, 38)');assert.notEqual(styles.link,'rgb(250, 249, 245)');
  assert.match(styles.hint,lang==='zh'?/长按/:/hold/);
  // Six-component style sheet: reply, confirm, form, link + existing chart/steps in one safe fixture.
  const tag=`${lang}-${theme}-${width}`;
  await evaluate(p,`document.querySelector('#words [data-component=confirm]').scrollIntoView({block:'center'})`);await sleep(80);
  await shot(p,`reply-${tag}.png`,`${lang.toUpperCase()} / ${theme.toUpperCase()} / ${width}px / reply + confirm`);
  for(const [name,selector] of [['confirm','[data-component=confirm]'],['form','[data-component=form]'],['steps','[data-component=steps]'],['chart','[data-component=chart]'],['external-link','a[href^="https://example.com"]']]){
   await evaluate(p,`document.querySelector('#words ${selector}').scrollIntoView({block:'center'})`);await sleep(60);
   await shot(p,`sheet-${tag}-${name}.png`,`${lang.toUpperCase()} / ${theme.toUpperCase()} / ${width}px / ${name}`);
  }
  const before=fake.st.says.length;
  // A secondary contextmenu (also fired by a sustained touch) drafts, without sending.
  await evaluate(p,`document.querySelectorAll('#words [data-component=confirm] button')[1].dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true}))`);
  assert.match(await evaluate(p,`document.getElementById('input').value`),/Choice: Second/);
  assert.equal(fake.st.says.length,before,'draft does not send');
  await evaluate(p,`document.querySelector('#words [data-component=confirm]').scrollIntoView({block:'center'})`);
  await revealDraft(p,'Choice: Second');
  await shot(p,`sheet-${tag}-confirm-draft.png`,`${lang.toUpperCase()} / ${theme.toUpperCase()} / ${width}px / confirm draft: Choice: Second`);
  await evaluate(p,`(()=>{const b=document.querySelectorAll('#words [data-component=confirm] button')[1];b.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',button:0}));})()`);await sleep(650);
  assert.equal((await evaluate(p,`document.getElementById('input').value.match(/Choice: Second/g)?.length||0`)),2,'600ms hold drafts exactly once');
  await evaluate(p,`(()=>{const b=document.querySelectorAll('#words [data-component=confirm] button')[1];b.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true}));b.click();})()`);
  assert.equal(fake.st.says.length,before,'hold and contextmenu must not send');
  await evaluate(p,`document.querySelector('#words [data-component=form] button').click()`);
  assert.match(await evaluate(p,`document.getElementById('input').value`),/Details/);
  assert.equal(fake.st.says.length,before,'form remains draft-only');
  await evaluate(p,`document.querySelector('#words [data-component=form]').scrollIntoView({block:'center'})`);
  await revealDraft(p,'Details');
  await shot(p,`sheet-${tag}-form-draft.png`,`${lang.toUpperCase()} / ${theme.toUpperCase()} / ${width}px / form draft: Details`);
  await evaluate(p,`(()=>{const b=document.querySelector('#words [data-component=confirm] button');b.click();b.click();})()`);
  await sleep(250);
  assert.equal(fake.st.says.length,before+1,'double tap sends once');assert.match(fake.st.says.at(-1).text,/Choice: First/);
  assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
 }finally{await p.dispose();}
}writeFileSync(out+'reply-results.json',JSON.stringify({verdict:'PASS',cases:8},null,2)+'\n');}finally{await B.close();await fake.stop();await web.stop();}
