// Scheduling evidence only. Never part of the pixel/environment stability fingerprint.
import {cpus,availableParallelism,freemem,totalmem,loadavg} from 'node:os';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
const GiB=1024**3;
const read=path=>{try{return readFileSync(path,'utf8').trim()}catch{return null}};
export function countCpuSet(text){if(typeof text!=='string'||!text.trim())return null;const values=new Set();for(const part of text.split(',')){const m=/^(\d+)(?:-(\d+))?$/.exec(part.trim());if(!m)throw Error('Invalid cpuset');const a=Number(m[1]),b=Number(m[2]??m[1]);if(b<a||b-a>100000)throw Error('Invalid cpuset range');for(let n=a;n<=b;n++)values.add(n);}return values.size||null;}
export function collectExecutionResources(){
 const processCgroup=read('/proc/self/cgroup'),mountInfo=read('/proc/self/mountinfo'),mountLine=mountInfo?.split('\n').find(line=>line.includes(' - cgroup2 '));
 const mountFields=mountLine?.split(' '),mountPoint=mountFields?.[4]||'/sys/fs/cgroup',mountRoot=mountFields?.[3]||'/',processPath=processCgroup?.split('\n').find(line=>line.startsWith('0::'))?.slice(3);
 let candidate=processPath&&processPath.startsWith(mountRoot)?resolve(mountPoint,processPath.slice(mountRoot.length).replace(/^\//,'')):resolve(mountPoint,(processPath||'').replace(/^\//,''));
 const namespaceFallback=!candidate.startsWith(mountPoint)||!existsSync(candidate+'/cpu.max');
 if(namespaceFallback)candidate=mountPoint;
 const hierarchy=[];let current=candidate;
 while(current.startsWith(mountPoint)){hierarchy.push({directory:current,cpuMax:read(current+'/cpu.max'),cpuset:read(current+'/cpuset.cpus.effective')??read(current+'/cpuset.cpus'),memoryMax:read(current+'/memory.max'),memoryCurrent:read(current+'/memory.current')});if(current===mountPoint)break;const next=dirname(current);if(next===current)break;current=next;}
 const quotas=hierarchy.flatMap(row=>{const m=/^(\d+)\s+(\d+)$/.exec(row.cpuMax||'');return m&&Number(m[2])>0?[Number(m[1])/Number(m[2])]:[]});
 const sets=hierarchy.flatMap(row=>{try{const n=countCpuSet(row.cpuset);return n?[n]:[]}catch{return[]}});
 const allowed=availableParallelism(),logical=cpus().length,effectiveCpus=Math.min(allowed,logical,...quotas,...sets);
 const boundedAvailable=hierarchy.flatMap(row=>/^\d+$/.test(row.memoryMax||'')&&/^\d+$/.test(row.memoryCurrent||'')?[Math.max(0,Number(row.memoryMax)-Number(row.memoryCurrent))]:[]);
 const memoryInfo=read('/proc/meminfo'),memAvailable=/^MemAvailable:\s+(\d+)\s+kB$/m.exec(memoryInfo||''),osFreeMemory=freemem(),osAvailableMemory=memAvailable?Number(memAvailable[1])*1024:osFreeMemory;
 const availableMemory=Math.min(osAvailableMemory,...boundedAvailable);
 return {schema:'p577-execution-resources-v1',observedAt:new Date().toISOString(),logicalCpus:logical,availableParallelism:allowed,effectiveCpus,osTotalMemory:totalmem(),osFreeMemory,osAvailableMemory,memAvailableRaw:memAvailable?.[0]||null,availableMemory,loadAverage:loadavg(),cgroup:{version:mountLine?2:null,selection:namespaceFallback?'namespace-root-fallback':'process-cgroup-path',processPath:processPath||null,mountPoint,mountRoot,hierarchy,quotaCpus:quotas.length?Math.min(...quotas):null,cpusetCpus:sets.length?Math.min(...sets):null,complete:(!namespaceFallback||processPath==='/')&&hierarchy.some(row=>row.cpuMax!==null&&row.memoryMax!==null&&row.memoryCurrent!==null)}};
}
export function chooseExecutionJobs(resources,{requested='auto',maximum=16}={}){
 if(requested!=='auto'&&(!Number.isInteger(Number(requested))||Number(requested)<1||Number(requested)>16))throw Error('jobs must be auto or 1..16');
 const cpu=Number(resources.effectiveCpus),memory=Number(resources.availableMemory),logical=Number(resources.logicalCpus),load=Number(resources.loadAverage?.[0]||0);
 if(!Number.isFinite(cpu)||cpu<=0||!Number.isFinite(memory)||memory<0||!Number.isFinite(logical)||logical<=0)throw Error('Invalid resource observation');
 const loadRatio=Math.min(1,Math.max(0,load/logical)),cpuBound=Math.max(1,Math.floor(cpu*.75*Math.max(.2,1-loadRatio))),reserve=Math.max(2*GiB,memory*.15),memoryBound=Math.max(1,Math.floor(Math.max(0,memory-reserve)/(3*GiB)));
 const fullyObserved=resources.cgroup?.complete===true,ample16=fullyObserved&&cpu>=24&&memory>=72*GiB&&loadRatio<.35&&cpuBound>=16&&memoryBound>=16;
 const generous=cpu>=24&&memory>=48*GiB&&loadRatio<.35,target=ample16?16:generous?12:cpu>=12?8:cpu>=8?6:Math.max(1,Math.floor(cpu*.65));
 const missingCgroup=resources.cgroup?.complete!==true,conservativeBound=missingCgroup?2:16;
 const chosenJobs=Math.max(1,Math.min(maximum,16,target,cpuBound,memoryBound,conservativeBound,requested==='auto'?16:Number(requested)));
 return {requested,chosenJobs,maximum:Math.min(maximum,16),cpuBound,memoryBound,loadRatio,reservedMemory:reserve,perJobMemory:3*GiB,reason:[ample16?'Complete cgroup, 24+ effective CPUs, 72+ GiB available and actual CPU/memory bounds >=16: allow up to 16':generous?'24+ effective CPUs, 48+ GiB available and low load: allow up to 12':'Preserve CPU/memory headroom; prefer 6–8 only when resources support it',missingCgroup?'Cgroup observation incomplete: conservatively cap at 2':`Actual cgroup/affinity effective CPUs=${cpu}`,`load/CPU=${loadRatio.toFixed(3)}; availableMemory=${memory}; bounds cpu=${cpuBound},memory=${memoryBound}`]};
}
