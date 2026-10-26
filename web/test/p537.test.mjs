import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {controlObject,CONTROL_ACTIONS} from '../../protocol/wire.js';
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
test('P537 heartbeat signed object binds draft operation and revision',()=>{
 assert.ok(CONTROL_ACTIONS.includes('heartbeat_write'));
 const target={op:'save',text:'中文\ntext',sha:'previous'};
 assert.equal(controlObject('heartbeat_write',target),JSON.stringify({op:'save',sha:'previous',text:'中文\ntext'}));
 for(const key of Object.keys(target))assert.notEqual(controlObject('heartbeat_write',target),controlObject('heartbeat_write',{...target,[key]:'changed'}));
});
test('P537 settings cannot introduce an inline multiline textarea',()=>{
 const html=read('../public/index.html');
 const settings=html.slice(html.indexOf('<section id="settings"'),html.indexOf('<div class="reader'));
 assert.doesNotMatch(settings,/<textarea\b/);
 for(const module of ['settings','mail','mail-servers','ctmanage','contacts','imessage','local-files','heartbeat-prompt']){
  assert.doesNotMatch(read('../public/js/'+module+'.js'),/(?:mk|createElement)\(['"]textarea['"]|['"]textarea['"]\s*:\s*['"]input['"]/);
 }
 assert.match(read('../public/js/settings.js'),/watchSettings\(el\('settings'\)\)/);
 assert.match(read('../public/js/contacts.js'),/textField\(i, label\)/);
});
