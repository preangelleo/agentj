import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseHtml} from '../tools/generate-node-owners.mjs';
const read=f=>readFileSync(new URL('../public/'+f,import.meta.url),'utf8');
export function mkParagraphs(source){
 const out=[];const rx=/\bmk\(\s*(['"])p\1/g;let m;
 while((m=rx.exec(source))){const start=source.indexOf('(',m.index),args=[];let at=start+1,level=0,quote='',end=start+1;
 for(;end<source.length;end++){const c=source[end];if(quote){if(c==='\\'){end++;continue;}if(c===quote)quote='';continue;}if(c==='"'||c==="'"||c==='`'){quote=c;continue;}if('([{'.includes(c))level++;else if(')]}'.includes(c)){if(!level&&c===')'){args.push(source.slice(at,end).trim());break;}level--;}else if(c===','&&!level){args.push(source.slice(at,end).trim());at=end+1;}}
 if(end===source.length)throw Error('Unterminated mk paragraph');out.push({start:m.index,end:end+1,args});
 }return out;
}
test('#577 source6 command roots have distinct skins while command IDs and payload flags remain',()=>{
 const html=read('index.html'),ui=read('js/ui.js'),relay=read('js/relay.js');
 assert.match(html,/<div class="aj-approval-command" id="cmdBox">/);
 assert.match(html,/<pre id="cmd"[^>]*data-aj-owner="aj-approval"/);
 assert.match(ui,/box\.classList\.toggle\('aj-notice-command', cmd\)/);
 assert.doesNotMatch(ui,/classList\.toggle\('cmd',/);
 assert.match(ui,/if \(cmd\) \{ fillText\(n, t\)/);
 assert.match(relay,/pre\.classList\.toggle\("fold", cmdOpenKey === null\)/);
});
test('#577 source6 actual static page paragraphs have direct semantic owners, including embedded hints',()=>{
 const ps=parseHtml(read('index.html')).filter(n=>n.tag==='p'&&(()=>{for(let p=n;p;p=p.parent)if(p.attrs.id==='pages')return true;return false;})());
 assert.ok(ps.length>45);for(const p of ps){assert.ok(p.attrs['data-aj-owner'],'p '+p.line+' '+p.attrs.id);assert.ok(Object.hasOwn(p.attrs,'data-aj-page-copy'),'explicit page paragraph context '+p.line);}
 for(const [id,owner]of Object.entries({'camera-hint':'aj-pairing','pair-error':'aj-pairing','takeover-note':'aj-recovery','act-status':'aj-history','bots-status':'aj-bots','fr-status':'aj-friends','phone-env-text':'aj-pairing'}))assert.equal(ps.find(n=>n.attrs.id===id)?.attrs['data-aj-owner'],owner,id);
});
test('#577 source6 paragraph callsites declare purpose, not whole mixed-module or tag defaults',()=>{
 for(const [f,owner]of Object.entries({'js/controls.js':'aj-history','js/keys.js':'aj-account','js/setup.js':'aj-setup','js/bots.js':'aj-bots','js/local-files.js':'aj-file','js/friends.js':'aj-friends'})){
 let s=read(f);if(f==='js/friends.js')s=s.slice(0,s.indexOf('export function renderAsk('));const calls=mkParagraphs(s);assert.ok(calls.length>0,f);
 for(const c of calls){assert.equal(c.args[3],"'"+owner+"'",f+' '+c.args.slice(0,2));if(f!=='js/local-files.js')assert.equal(s.slice(c.start-9,c.start),'pageCopy(',f+' explicit copy slot');}
 }
 assert.match(read('js/local-files.js'),/mk\('span', 'small local-files-path',[^;]*, 'aj-file'\)/);
 assert.match(read('js/busy.js'),/claim\.setAttribute\('data-aj-owner','aj-busy'\); claim\.className = 'error'/);
 // Skin roles keep .small/error class order and the existing busy actor.
 assert.match(read('js/controls.js'),/mk\('p', 'error small', t\('tasks\.bad',[^\n]*'aj-history'\)/);
 assert.match(read('js/friends.js'),/mk\('p', 'error small', undefined, 'aj-friends'\)/);
});

test('#577 source6 real DOM notice toggles only its skin while text, actions and command payload remain',async()=>{
 const {launchBrowser}=await import('../../tests/lib/browser.mjs');const browser=await launchBrowser();
 try{const page=await browser.newPage(390,844);await page.eval(`document.body.innerHTML='<div id="toast" class="toast"></div>'`);
 const pageCopySource=/export function pageCopy\(node\) \{[^\n]+\}/.exec(read('js/ui.js'))[0].replace('export function','function');
 await page.eval(`{${pageCopySource};const p=document.createElement('p');p.dataset.ajOwner='aj-history';p.className='small';p.textContent='History description';p.addEventListener('click',()=>window.pageCopyHit=1);window.copyP=p;window.samePageCopyNode=pageCopy(p)===p;document.body.append(p);const bare=document.createElement('p');pageCopy(bare);window.unownedStillUnowned=!bare.hasAttribute('data-aj-owner');}`);
 assert.deepEqual(await page.eval(`({same:window.samePageCopyNode,owner:window.copyP.dataset.ajOwner,skin:window.copyP.className,text:window.copyP.textContent,slot:window.copyP.hasAttribute('data-aj-page-copy'),unowned:window.unownedStillUnowned})`),{same:true,owner:'aj-history',skin:'small',text:'History description',slot:true,unowned:true});
 await page.eval('window.copyP.click()');assert.equal(await page.eval('window.pageCopyHit'),1,'slot metadata preserves existing handlers');
 const source=read('js/ui.js'),notify=source.slice(source.indexOf('export function notify(spec)'),source.indexOf('// the glance:')).replace('export function','function');
 await page.eval(`window.hit=0;window.spec={kind:'info',text:'Run '+String.fromCharCode(96)+'agentj pair'+String.fromCharCode(96),ms:0,actions:[{label:'Open',fn(){window.hit++;}}]};
 {const el=id=>document.getElementById(id);const CMD_TOAST_MS=5000;const notifyFormat=spec=>({kind:spec.kind,accent:'--state',icon:'info',shape:'rect',ms:spec.ms});const icon=()=>document.createElement('svg');const mk=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls||'';if(text!==undefined)n.textContent=text;return n;};const fillText=(node,text)=>node.textContent=text;const replyNotice=()=>{};const toastOff=()=>document.getElementById('toast').classList.remove('on');${notify};window.realNotify=notify;notify(window.spec);}`);
 assert.deepEqual(await page.eval(`({skin:document.getElementById('toast').classList.contains('aj-notice-command'),old:document.getElementById('toast').classList.contains('cmd'),payload:window.spec.text,text:document.querySelector('.nt-body').textContent,action:document.querySelector('.undo').textContent,role:document.getElementById('toast').getAttribute('role')})`),{skin:true,old:false,payload:'Run `agentj pair`',text:'Run `agentj pair`',action:'Open',role:'status'});
 await page.eval(`document.querySelector('.undo').click()`);assert.equal(await page.eval('window.hit'),1,'original action callback runs once');
 await page.eval(`window.realNotify({kind:'info',text:'Plain notice',ms:0})`);assert.equal(await page.eval(`document.getElementById('toast').classList.contains('aj-notice-command')`),false,'next plain notice clears command modifier');
 await page.eval(`document.getElementById('toast').remove()`);await page.close();
 }finally{await browser.close();}
});

test('#577 technical press roots retain their own header and reply actors',()=>{
 const nodes=parseHtml(read('index.html'));
 for(const [id,owner]of Object.entries({orb:'aj-header',meta:'aj-header',agent:'aj-reply'}))assert.equal(nodes.find(n=>n.attrs.id===id)?.attrs['data-aj-owner'],owner,id+' technical selection root needs direct semantic metadata');
 const agent=nodes.find(n=>n.attrs.id==='agent');assert.ok(agent.classes.includes('speaker'));
 assert.match(read('app.js'),/\$\('agent'\)\.textContent = displayAgentName\(\)/);
 assert.match(read('js/agentname.js'),/longPress\(\$\('agent'\)/,'the reply speaker remains the rename trigger');
 assert.match(read('js/relay.js'),/pressable\(el\("orb"\), \(\) => C\.menuPages\(\), \(\) => C\.goChat\(\)\)/);
});
