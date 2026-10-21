import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {join} from 'node:path';
const pub=new URL('../public/',import.meta.url);
const read=f=>readFileSync(new URL(f,pub),'utf8');
// Notifications may be centred (50%) or in flow; no top-anchored popup may return under a different name.
function topPopups(css){
 const clean=css.replace(/\/\*[\s\S]*?\*\//g,'');
 return [...clean.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(([,sel,body])=>
  /(?:toast|banner|notice|snack|notif)/i.test(sel)&&!/(?:\s|>)\.[\w-]+.*::(?:after|before)/.test(sel)&&
  /position\s*:\s*(?:fixed|absolute)/.test(body)&&/\btop\s*:\s*(?!50%|auto)[^;}]+/.test(body));
}
function cssFiles(dir){return readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?cssFiles(join(dir,e.name)):e.name.endsWith('.css')?[join(dir,e.name)]:[]);}
test('P440 lint: every shipped CSS file forbids top popup notifications',()=>{
 for(const f of cssFiles(pub.pathname)) assert.deepEqual(topPopups(readFileSync(f,'utf8')),[],f);
 assert.equal(topPopups('.new-notification{position:fixed;top:64px}').length,1,'positive control');
 assert.equal(topPopups('.new-toast{position:absolute;top:env(safe-area-inset-top)}').length,1);
 assert.equal(topPopups('.toast.circle{position:fixed;top:50%}').length,0);
});
test('P440: sudo and grants use the reply slot; auto approval always requests internal format',()=>{
 const ui=read('js/ui.js'),elev=read('js/elevate.js'),controls=read('js/controls.js');
 assert.match(read('index.html'),/id="reply-notices"[\s\S]*?id="rmbar"/);
 assert.match(read('js/share.js'),/dataset.view === 'pair' && !ready\) el\('pair-view'\)\.prepend\(box\)/);
 assert.match(elev,/replyNotice\(runEl\)/);assert.match(elev,/replyNotice\(grantBar\)/);
 assert.doesNotMatch(elev,/document.body.appendChild\((runEl|grantBar)\)/);
 assert.match(controls,/executionToast\(t\(m.by/);
 assert.match(ui,/export function executionToast[\s\S]*?shape: 'rect'/);
 for(const id of ['setup-banner','ar-renew','qr-banner','rescue-banner','estop-banner','grant-bar','share-notice'])assert.ok(ui.includes("'"+id+"'"));
});
