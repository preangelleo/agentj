import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {checkNoticeSources,noticePlacementViolations} from './notice-placement-source.mjs';
globalThis.window ??= {};
const {noticeBounds}=await import('../public/js/ui.js');
test('#556 all shipped notice styles and native wrappers avoid bottom transient placement',()=>{assert.deepEqual(checkNoticeSources(),[]);});
test('#556 source gate rejects bottom overrides even if the base owns fixed placement',()=>{
 for(const css of ['.toast.rect.over-dlg{position:fixed;bottom:16px}', 'body:has(.dlg) .toast.square{bottom:calc(16px + env(safe-area-inset-bottom))}', '.new-snackbar{bottom:0}', '.page .msg{bottom:12px}', '.notice{transform:translateY(100%)}', '.toast{position:fixed;inset:auto 0 0}', '.toast{inset-block-end:16px}', '.toast{inset-block:auto 0}', 'Toast.makeText(this,s,Toast.LENGTH_SHORT).show()', 'Snackbar.make(view,s,1000).show()'])assert.ok(noticePlacementViolations(css).length,css);
 assert.deepEqual(noticePlacementViolations('.toast.rect{bottom:auto;position:relative}.notice-inline{margin-bottom:12px}'),[]);
});
test('#556 notices fit above input/icons when draft grows or keyboard reduces height',()=>{
 for(const height of [844,900,520,390])for(const top of [height-160,height-240])for(const natural of [156,250,2000]) {
  const b=noticeBounds(height,top,natural),half=Math.min(natural,b.maxHeight)/2;
  assert.ok(b.top-half>=16-1e-6);assert.ok(b.top+half<=top-12+1e-6);assert.ok(b.maxHeight>0);
 }
 assert.equal(noticeBounds(844,700,156).top,422,'normal notice remains in viewport centre');
});
test('#556 shell messages are escaped and only fall back to a centred rounded native card',()=>{
 for(const path of ['../../android/app/src/main/kotlin/app/agentj/android/MainActivity.kt','../../../android/app/src/main/kotlin/net/leowang/jarvis/MainActivity.kt']) {
  const s=readFileSync(new URL(path,import.meta.url),'utf8');assert.match(s,/originOf\(web\.url \?: ""\) == origin/);assert.match(s,/org\.json\.JSONObject\.quote\(s\)/);assert.match(s,/window\.relayNative\?\.notice\?\./);assert.match(s,/setGravity\(Gravity\.CENTER\)/);assert.match(s,/cornerRadius = dp\(20\)/);assert.match(s,/KEYCODE_ESCAPE/);assert.doesNotMatch(s,/Toast\.makeText/);
 }
});
