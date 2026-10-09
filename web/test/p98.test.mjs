import {test} from 'node:test';
import assert from 'node:assert/strict';
import {SharedNotice} from '../public/js/shared-notice.js';
import * as snap from '../public/js/snap.js';
const follow=(agent='codex',id='a')=>({agent,id:id.repeat(32)});
const storage=()=>{const m=new Map();return {getItem:k=>m.get(k),setItem:(k,v)=>m.set(k,v)};};
test('P98 following persists across refresh and reconnect but native session and host changes show once',()=>{
 const st=storage(), shown=[];let n=new SharedNotice(st);
 n.update('host',follow(),'following',a=>shown.push(a));
 n.update('host',follow(),'following',a=>shown.push(a));
 n=new SharedNotice(st);n.update('host',follow(),'following',a=>shown.push(a));
 n.update('host',null,null,a=>shown.push(a));n.update('host',follow(),'following',a=>shown.push(a));
 n.update('host',follow('claude','b'),null,a=>shown.push(a));
 n.update('host',follow('opencode','c'),null,a=>shown.push(a));
 n.update('other',follow(),null,a=>shown.push(a));
 assert.deepEqual(shown,['codex','claude','opencode','codex']);
});
test('P98 actionable writer is never consumed as an informational toast; recovery does not repeat',()=>{
 const n=new SharedNotice(storage());let count=0;
 n.update('host',follow(),'desktop_writer',()=>count++);
 n.update('host',follow(),'following',()=>count++);assert.equal(count,0);
 n.update('host',follow('codex','b'),'following',()=>count++);assert.equal(count,1);
});
test('P98 denied storage stays deduplicated and malformed metadata is ignored',()=>{
 const n=new SharedNotice({getItem(){throw Error();},setItem(){throw Error();}});let count=0;
 n.update('host',follow(),null,()=>count++);n.update('host',follow(),null,()=>count++);
 n.update('host',{agent:'codex',id:'raw-native-id'},null,()=>count++);assert.equal(count,1);
 snap.configure({onChange(){},onTurn(){}});
 snap.setMeter({shared_follow:follow('claude'),shared_status:'desktop_writer'});
 assert.deepEqual(snap.snapshot().usage.shared_follow,follow('claude'));
 snap.setMeter({shared_follow:{agent:'unknown',id:'a'.repeat(32)}});assert.equal(snap.snapshot().usage.shared_follow,null);
});
