import test from 'node:test';import assert from 'node:assert/strict';import{auditArchiveJson}from'./archive-transport.mjs';
test('P577 string transport keeps the existing JSON archive semantics for nested, nullable and numeric values',()=>{
 const previous=globalThis.__p577AuditRuntime;
 try{globalThis.__p577AuditRuntime=meta=>({...meta,extra:{nil:null,absent:undefined,nan:NaN,infinity:Infinity,negativeZero:-0,items:[undefined,null,false,1.25,'中文']}});
  const actual=JSON.parse(auditArchiveJson(JSON.stringify({scene:'public fixture',nested:{empty:[],value:0.125,truth:true}})));
  assert.deepEqual(actual,{scene:'public fixture',nested:{empty:[],value:0.125,truth:true},extra:{nil:null,nan:null,infinity:null,negativeZero:0,items:[null,null,false,1.25,'中文']}});
 }finally{if(previous===undefined)delete globalThis.__p577AuditRuntime;else globalThis.__p577AuditRuntime=previous;}
});
