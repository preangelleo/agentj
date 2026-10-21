import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {join} from 'node:path';
const dir=new URL('../public/',import.meta.url);
const read=p=>readFileSync(new URL(p,dir),'utf8');
test('P445: raw clipboard writers are confined to reviewed primitives and protected/selection paths',()=>{
 const allowed={
  'js/md.js':'clipboard primitive with legacy fallback',
  'js/copycode.js':'shared copy operation and visible feedback',
  'js/shotcopy.js':'synchronous image ClipboardItem primitive',
  'js/keys.js':'passkey-approved Safari ClipboardItem and direct-only retry',
  'js/secretout.js':'secret pickup uses direct-only clipboard writer',
  'js/relay.js':'automatic selection/reference intake preserves system selection'
 };
 const files=['app.js',...readdirSync(new URL('js/',dir)).filter(n=>n.endsWith('.js')).map(n=>'js/'+n)];
 const bad=files.filter(f=>/\.writeText\(|clipboard\.write\(|RelayMD\.copyText\(/.test(read(f))&&!allowed[f]&&f!=='js/webhooks.js');
 // Webhooks' injected default writer must be consumed through the same feedback component.
 assert.match(read('js/webhooks.js'),/copyButton\(u, button, D.copy\)/);
 assert.deepEqual(bad,[],'new clipboard entry points must use copyButton, or document their special activation/approval contract here');
});
test('P445: pure one-button noticeSheet cannot open or resolve a destructive confirmation',()=>{
 const s=read('js/ui.js'),body=s.slice(s.indexOf('export function noticeSheet'),s.indexOf('export const confirmOpen'));
 assert.doesNotMatch(body,/confirmSheet\(|confirm-yes|confirm-no|actions:/);
 assert.match(body,/ms: 2000/);
 assert.match(read('js/asr-setup.js'),/kind:'success', shape:'circle'.*ms:1800/);
 assert.doesNotMatch(read('js/asr-setup.js'),/p387Voice.dismiss/);
 assert.match(read('app.js'),/gearHelp: \(\) => noticeSheet\(/);
});
