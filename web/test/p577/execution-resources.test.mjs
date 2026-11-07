import test from 'node:test';import assert from 'node:assert/strict';import{chooseExecutionJobs,countCpuSet}from'./execution-resources.mjs';
const GiB=1024**3,resource=(cpu,memory,load=0)=>({logicalCpus:cpu,effectiveCpus:cpu,availableMemory:memory*GiB,loadAverage:[load,load,load],cgroup:{complete:true}});
test('actual capacity permits 6–8 workers, and only ample low-load capacity permits 12',()=>{assert.equal(chooseExecutionJobs(resource(12,24)).chosenJobs,6);assert.equal(chooseExecutionJobs(resource(16,32,2)).chosenJobs,8);assert.equal(chooseExecutionJobs(resource(24,48,.5)).chosenJobs,12);assert.equal(chooseExecutionJobs(resource(64,128),{maximum:12}).chosenJobs,12);});
test('low cgroup CPU quota, memory and high load strictly cap concurrency; explicit jobs is only an upper bound',()=>{assert.equal(chooseExecutionJobs(resource(2,64)).chosenJobs,1);assert.equal(chooseExecutionJobs(resource(32,2)).chosenJobs,1);assert.ok(chooseExecutionJobs(resource(24,64,22)).chosenJobs<6);assert.equal(chooseExecutionJobs(resource(24,64),{requested:'2'}).chosenJobs,2);const missing=resource(24,64);missing.cgroup.complete=false;assert.equal(chooseExecutionJobs(missing).chosenJobs,2);for(const requested of ['0','17','garbage'])assert.throws(()=>chooseExecutionJobs(resource(24,64),{requested}));});
test('effective cpuset ranges are counted exactly, never inferred from host CPU count',()=>{assert.equal(countCpuSet('0-3,8,10-11'),7);assert.equal(countCpuSet('2-3,3'),2);assert.equal(countCpuSet(''),null);assert.throws(()=>countCpuSet('5-2'));assert.throws(()=>countCpuSet('all'));});

test('sixteen jobs require complete fresh capacity and every independent CPU, memory and load bound',()=>{
 assert.equal(chooseExecutionJobs(resource(24,85.63,1.18)).chosenJobs,16);
 assert.equal(chooseExecutionJobs(resource(24,72),{requested:'16'}).chosenJobs,16);
 for(const r of [resource(24,71.99),resource(23.99,128),resource(24,72,4)])assert.ok(chooseExecutionJobs(r,{requested:'16'}).chosenJobs<=12);
 const missing=resource(24,128);missing.cgroup.complete=false;assert.equal(chooseExecutionJobs(missing,{requested:'16'}).chosenJobs,2);
 assert.equal(chooseExecutionJobs(resource(24,128),{requested:'8'}).chosenJobs,8);
 assert.equal(chooseExecutionJobs(resource(24,128),{maximum:12}).chosenJobs,12);
 const policy=chooseExecutionJobs(resource(24,85.63,1.18));assert.equal(policy.perJobMemory,3*GiB);assert.ok(policy.reservedMemory>=85.63*GiB*.15);assert.ok(policy.chosenJobs<=policy.cpuBound&&policy.chosenJobs<=policy.memoryBound);
});
