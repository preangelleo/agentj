import test from 'node:test';
import assert from 'node:assert/strict';
import {todoItems, todoPage, TODO_BATCH} from '../public/js/todo-view.js';
const rows = Array.from({length:141},(_,i)=>({id:'t'+i,owner:i%2?'owner':'agent',status:i===2?'done':i===3?'cancelled':'pending',done:i===4}));
test('default hides all closed states, owner filter composes before paging',()=>{
 assert.equal(todoItems(rows).length,138);
 assert.equal(todoItems(rows,{hideDone:false}).length,141);
 assert.ok(todoItems(rows,{owner:'owner'}).every(x=>x.owner==='owner'&&x.status!=='cancelled'));
 assert.equal(todoPage(todoItems(rows,{owner:'owner'})).length,10);
});
test('first ten and ten more retain stable source ordering and final remainder',()=>{
 const all=todoItems(rows,{hideDone:false});
 assert.equal(TODO_BATCH,10);assert.deepEqual(todoPage(all).map(x=>x.id),rows.slice(0,10).map(x=>x.id));
 assert.equal(todoPage(all,20).length,20);assert.equal(todoPage(all,140).length,140);assert.equal(todoPage(all,150).length,141);
 assert.equal(todoPage([]).length,0);assert.equal(todoPage(rows.slice(0,3)).length,3);
});
test('search matches unloaded records before pagination',()=>{
 const matches=x=>x.id==='t140';assert.deepEqual(todoPage(todoItems(rows,{matches})).map(x=>x.id),['t140']);
});
