import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=n=>readFileSync(new URL('../public/'+n,import.meta.url),'utf8');
test('P460 sheets retain stage overlay, never reply-notices',()=>{
 const relay=read('js/relay.js'),css=read('app.css'),html=read('index.html');
 assert.doesNotMatch(relay,/replyNotice\(el\(["']sheet/);
 assert.doesNotMatch(css,/#reply-notices\s*>\s*\.sheet|data-permission-inset/);
 assert.match(html,/id="stage"[\s\S]*?id="sheet"/);
 assert.match(read('js/ui.js'),/if \(node\?\.classList.contains\('sheet'\)\) return node/);
});
