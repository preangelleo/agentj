import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
const pub=new URL('../public/',import.meta.url);
const read=f=>readFileSync(new URL(f,pub),'utf8');
// Notifications may be centred (50%) or in flow; no top-anchored popup may return under a different name.
function topPopups(css){
 const clean=css.replace(/\/\*[\s\S]*?\*\//g,'');
 return [...clean.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(([,sel,body])=>
  /(?:toast|banner|notice|snack|notif)/i.test(sel)&&!/(?:\s|>)\.[\w-]+.*::(?:after|before)/.test(sel)&&
  /position\s*:\s*(?:fixed|absolute)/.test(body)&&/\btop\s*:\s*(?!50%|auto)[^;}]+/.test(body));
}
function checkedPreviewFallback(css){
 const fallback=topPopups(css).filter(([,sel])=>sel.trim()==='#toast[data-preview-failure="1"].on');
 assert.equal(fallback.length,1,'exactly one released bootstrap fallback');
 assert.equal(createHash('sha256').update(fallback[0][2]).digest('hex'),'137f770f8609522e975c912aab865a86ff880eda830fc8d173698e53f11c3e07','complete formal 0.30.21 fallback body');
 const flow=[...css.replace(/\/\*[\s\S]*?\*\//g,'').matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(([,sel])=>sel.trim()==='.chatv>#toast[data-preview-failure="1"].on');
 assert.deepEqual(flow.map(([,sel,body])=>createHash('sha256').update(body).digest('hex')),['dff2d2ba0eb25be29145b1ec30f817548723b03d55581bc5440e662c54866fef','7f3b71fd07a3b92355e427bf7e82d269ad1450ae0172fe85dfedaa3f65f89ace'],'complete released chat-flow override and landscape spacing');
 return fallback[0];
}
// The released close control is positioned within its notice, and the
// released modal result uses a reviewed centered token. Neither is a new popup.
function checkedReleasedPlacements(css){
 const found=topPopups(css);checkedPreviewFallback(css);
 const reviewed=new Map([
  ['.toast:is(.circle,.square) .nt-close','940c01c8b24ea88d82f7bbfd63192ff6ac41c408a8e493a2e70515372f1f38ce'],
  ['.toast.rect.over-dlg','1a425b7a0b2ebbeabdbab071c82ae682fd0bd8305702a8bbb34ce5aea840a91e']
 ]);
 for(const [selector,bodySha] of reviewed){
  const rows=found.filter(([,sel])=>sel.trim()===selector);
  assert.equal(rows.length,1,'exactly one reviewed placement: '+selector);
  assert.equal(createHash('sha256').update(rows[0][2]).digest('hex'),bodySha,'complete formal 0.30.21 placement: '+selector);
 }
 const ui=read('js/ui.js');
 assert.equal(createHash('sha256').update(ui).digest('hex'),'3931ccf877149de037cb7cc27bd58b0cd2ed84ec9437e46503559103c43c6d3c','exact owner-approved0e858 final UI close-button and center-token producer');
 assert.match(ui,/if \(spec\.close\) \{\s*const x = document\.createElement\('button'\); x\.type = 'button'; x\.className = 'nt-close';/);
 assert.match(ui,/x\.addEventListener\('click', \(\) => hideNotice\(box\)\);\s*kids\.push\(x\);\s*\}\s*box\.replaceChildren\(\.\.\.kids\);/);
 assert.match(ui,/box\.style\.setProperty\('--aj-notice-center-y', `\$\{bounds\.top \+ offset\}px`\);/);
 return found.filter(([,sel])=>sel.trim()!=='#toast[data-preview-failure="1"].on'&&!reviewed.has(sel.trim()));
}
function cssFiles(dir){return readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?cssFiles(join(dir,e.name)):e.name.endsWith('.css')?[join(dir,e.name)]:[]);}
test('P440 lint: every shipped CSS file forbids top popup notifications',()=>{
 for(const f of cssFiles(pub.pathname)) {
  const css=readFileSync(f,'utf8'),found=topPopups(css);
  // Released 0.30.21 has one preview bootstrap status fallback. In chat its
  // exact child selector overrides that fallback to ordinary document flow.
  if(f===new URL('app.css',pub).pathname){
   assert.deepEqual(checkedReleasedPlacements(css),[],f);
   assert.match(read('js/preview-failure.js'),/banner\.dataset\.previewFailure = '1'; placeStrip\(banner\)/);
   assert.match(read('js/preview-failure.js'),/document\.body\.dataset\.view === 'chat'\) chat\.insertBefore\(banner, stage\)/);
  }else assert.deepEqual(found,[],f);
 }
 // Build mutations from the same comment-normalized input that produced
 // the match. A negative control must change bytes before testing refusal.
 const appCss=read('app.css').replace(/\/\*[\s\S]*?\*\//g,''),fallback=checkedPreviewFallback(appCss);
 for(const [why,mutated] of [
  ['changed top cannot inherit the exception',appCss.replace(fallback[0],fallback[0].replace(/}$/, ';top:0!important}'))],
  ['a duplicate exception selector is rejected',appCss+'\n'+fallback[0]],
  ['the released in-flow override cannot become a popup',appCss.replace('position:relative;top:auto;left:auto;bottom:auto;transform:none;flex:none;', 'position:fixed;top:0;left:auto;bottom:auto;transform:none;flex:none;')]
 ]){
  assert.notEqual(mutated,appCss,'negative control actually changes CSS: '+why);
  assert.throws(()=>checkedPreviewFallback(mutated),why);
 }
 for(const [why,mutated] of [
  ['close-control top cannot drift',appCss.replace('position:absolute;top:6px;right:6px;margin:0','position:absolute;top:0!important;right:6px;margin:0')],
  ['a root popup cannot impersonate the nested close control',appCss.replace('.toast:is(.circle,.square) .nt-close{','.nt-close{')],
  ['a centered result cannot gain a top anchor',appCss.replace('top:var(--aj-notice-center-y,50%);bottom:auto;', 'top:0!important;bottom:auto;')],
  ['a duplicate close-control exception is rejected',appCss+'\n.toast:is(.circle,.square) .nt-close{position:absolute;top:6px;right:6px;margin:0}']
 ]){
  assert.notEqual(mutated,appCss,'negative control actually changes CSS: '+why);
  assert.throws(()=>checkedReleasedPlacements(mutated),why);
 }
 assert.equal(topPopups('.new-notification{position:fixed;top:64px}').length,1,'positive control');
 assert.equal(topPopups('.new-toast{position:absolute;top:env(safe-area-inset-top)}').length,1);
 assert.equal(topPopups('.toast.circle{position:fixed;top:50%}').length,0);
});
test('P440: sudo and grants use the reply slot; #562 automatic approval stays silent',()=>{
 const ui=read('js/ui.js'),elev=read('js/elevate.js'),controls=read('js/controls.js');
 assert.match(read('index.html'),/id="reply-notices"[\s\S]*?id="rmbar"/);
 assert.match(read('js/share.js'),/dataset.view === 'pair' && !ready\) el\('pair-view'\)\.prepend\(box\)/);
 assert.match(elev,/replyNotice\(runEl\)/);assert.match(elev,/replyNotice\(grantBar\)/);
 assert.doesNotMatch(elev,/document.body.appendChild\((runEl|grantBar)\)/);
 assert.doesNotMatch(controls,/executionToast\(t\(m.by/);
 assert.match(ui,/export function executionToast[\s\S]*?shape: 'rect'/);
 for(const id of ['setup-banner','ar-renew','qr-banner','rescue-banner','estop-banner','grant-bar','share-notice'])assert.ok(ui.includes("'"+id+"'"));
});
