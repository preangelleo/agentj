import test from 'node:test';
import assert from 'node:assert/strict';
import {createUnread} from '../public/js/unread.js';

test('owner maximum clears earlier pages without holes and ignores stale frames', () => {
  const u=createUnread(); u.bind('A'); u.owner(1,0);
  assert.deepEqual(u.list([1,2,3]),[1,2,3]);
  assert.equal(u.see(3,[1,2,3],true),true);
  assert.deepEqual(u.list([1,2,3]),[]);
  u.owner(1,1); assert.equal(u.mark(),3);
  assert.equal(u.owner(0,99),false); assert.equal(u.mark(),3);
  u.owner(2,0); assert.deepEqual(u.list([4]),[4]);
});
test('host isolation and invalid receipts', () => {
  const u=createUnread(); u.bind('A'); u.owner(1,100);
  u.bind('B'); u.owner(1,0); assert.equal(u.mark(),0);
  for(const v of [NaN,-1,true,1.5,Number.MAX_SAFE_INTEGER+1]) assert.equal(u.owner(1,v),false);
});

test('a receipt missed by the network survives reload for the same host, never another epoch', () => {
 const mem=new Map(); globalThis.localStorage={getItem:k=>mem.get(k)||null,setItem:(k,v)=>mem.set(k,v)};
 const a=createUnread();a.bind('retry');a.owner(3,0);a.see(8,[8],true);
 const b=createUnread();b.bind('retry');b.owner(3,0);assert.equal(b.mark(),8);
 b.owner(4,0);assert.equal(b.mark(),0);
 delete globalThis.localStorage;
});
