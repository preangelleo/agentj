import test from 'node:test';
import assert from 'node:assert/strict';
import MD from '../public/js/md.js';
const code = text => ({tag:'code',children:[{text}]});
test('P253: backtick indexing preserves escaped partial runs and exact run lengths',()=>{
  assert.deepEqual(MD.parseInline('\\``a`'),[{text:'`'},code('a')]);
  assert.deepEqual(MD.parseInline('``a`b``'),[code('a`b')]);
  assert.deepEqual(MD.parseInline('`a``b`'),[code('a``b')]);
  assert.deepEqual(MD.parseInline('``abc`'),[{text:'``abc`'}]);
});
test('P253: nested label code and subsequent parses keep independent delimiter indexes',()=>{
  const input='`a` [``b`c``](https://example.com) `d`';
  const result=MD.parseInline(input);
  assert.deepEqual(result[0],code('a'));
  assert.deepEqual(result.at(-1),code('d'));
  assert.deepEqual(result.find(x=>x.tag==='a').children,[code('b`c')]);
  MD.parse('```x\n[unclosed`\n```');
  MD.parseInline('``another`length');
  assert.deepEqual(MD.parseInline(input),result);
});
test('P253: impossible link openers retain normal inline formatting and unsafe URLs stay text',()=>{
  assert.deepEqual(MD.parseInline('[x] **bold**'),[{text:'[x] '},{tag:'strong',children:[{text:'bold'}]}]);
  assert.deepEqual(MD.parseInline('[`a`'),[{text:'['},code('a')]);
  const unsafe=MD.parseInline('[x](javascript:alert(1))');
  assert.equal(unsafe.some(x=>x.tag==='a'),false);
});
