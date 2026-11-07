// Post-hoc source admission only: never changes captured fixtures or recaptures images.
import{execFileSync}from'node:child_process';
import{readFileSync,lstatSync,readdirSync,mkdtempSync,rmSync,existsSync}from'node:fs';
import{resolve,join,relative}from'node:path';import{tmpdir}from'node:os';
import{pathToFileURL}from'node:url';import{createHash}from'node:crypto';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const git=(root,args,options={})=>execFileSync('git',args,{cwd:resolve(root),maxBuffer:512*1024*1024,stdio:['pipe','pipe','pipe'],...options});
const value=(root,...args)=>git(root,args,{encoding:'utf8'}).trim();
const isCommit=x=>typeof x==='string'&&/^[a-f0-9]{40}$/.test(x);
function hashMap(input,label){if(!input||typeof input!=='object'||Array.isArray(input)||!Object.keys(input).length||Object.entries(input).some(([p,h])=>!p||typeof h!=='string'||!/^[a-f0-9]{64}$/.test(h)))throw Error('Missing or invalid '+label);return Object.fromEntries(Object.entries(input).sort(([a],[b])=>a<b?-1:a>b?1:0));}
const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export function captureOrigin(runLog){
 const text=Buffer.isBuffer(runLog)?runLog.toString('utf8'):typeof runLog==='string'&&existsSync(runLog)?readFileSync(runLog,'utf8'):runLog;
 if(typeof text!=='string')throw Error('Actual capture run.log is required');
 const matches=[...text.matchAll(/^== olares-run [^\r\n]*\bsrc=([a-f0-9]{40})\s+tree=([a-f0-9]{40})(?=\s|$)[^\r\n]*$/gm)],unique=new Map(matches.map(m=>[m[1]+':'+m[2],{commit:m[1],tree:m[2]}]));
 if(unique.size!==1)throw Error('Capture run.log must identify exactly one source commit/tree');return [...unique.values()][0];
}
export async function withCaptureSnapshot(root,commit,callback){
 if(!isCommit(commit)||value(root,'rev-parse','--verify',commit+'^{commit}')!==commit)throw Error('An exact existing capture commit is required');
 const owned=mkdtempSync(join(tmpdir(),'aj-visual-admission-')),snapshotRoot=join(owned,'source');let added=false,primary;
 try{git(root,['worktree','add','--detach',snapshotRoot,commit]);added=true;if(value(snapshotRoot,'rev-parse','HEAD')!==commit)throw Error('Capture snapshot checkout differs from requested source');return await callback(snapshotRoot);}catch(error){primary=error;throw error;}finally{
  let cleanupError;try{if(added||existsSync(join(snapshotRoot,'.git')))git(root,['worktree','remove','--force',snapshotRoot]);}catch(error){cleanupError=error;}
  if(!cleanupError)rmSync(owned,{recursive:true,force:true});if(cleanupError){if(primary&&typeof primary==='object')primary.snapshotCleanupError=String(cleanupError);else throw cleanupError;}
 }
}
function gitEntries(root,commit){
 const entries=new Map();for(const item of git(root,['ls-tree','-rz','--full-tree',commit]).toString('utf8').split('\0').filter(Boolean)){const tab=item.indexOf('\t'),[mode,type,oid]=item.slice(0,tab).split(' ');entries.set(item.slice(tab+1),{mode,type,oid});}return entries;
}
function gitBlobHashes(root,entries,paths){
 const ids=[...new Set(paths.map(path=>{const entry=entries.get(path);if(!entry||entry.type!=='blob'||!['100644','100755'].includes(entry.mode))throw Error('Fixture is not a tracked regular Git source file: '+path);return entry.oid;}))];
 if(!ids.length)return new Map();const raw=git(root,['cat-file','--batch'],{input:ids.join('\n')+'\n'}),hashes=new Map();let offset=0;
 for(const id of ids){const end=raw.indexOf(10,offset),[oid,type,sizeText]=raw.subarray(offset,end).toString('utf8').split(' '),size=Number(sizeText);if(oid!==id||type!=='blob'||!Number.isSafeInteger(size)||size<0)throw Error('Invalid actual Git blob response');offset=end+1;const bytes=raw.subarray(offset,offset+size);if(bytes.length!==size||raw[offset+size]!==10)throw Error('Truncated actual Git blob');hashes.set(id,sha(bytes));offset+=size+1;}return hashes;
}
function actualFiles(root){const result={};function visit(directory){for(const name of readdirSync(directory).sort()){const path=join(directory,name),st=lstatSync(path);if(st.isSymbolicLink())throw Error('Current public files cannot contain a symlink');if(st.isDirectory())visit(path);else if(st.isFile())result[relative(root,path).split('\\').join('/')]=sha(readFileSync(path));else throw Error('Unsupported public filesystem entry');}}visit(root);return hashMap(result,'current actual public files');}
async function currentRuntimeBinding(root){
 const base=await import(pathToFileURL(join(root,'web/test/p577/fixture-binding.mjs')).href),critical=await import(pathToFileURL(join(root,'web/test/p577/publish-critical-profile.mjs')).href),product=base.resolveFixtureRoot(root);
 return hashMap({...base.fixtureFingerprint(product),...critical.criticalFixtureFingerprint(product)},'current runtime fixture binding');
}
export async function verifySourceBindings({currentRoot,snapshotRoot,fixtureFiles,captureFixtureBinding,origin}){
 const sourceCommit=value(snapshotRoot,'rev-parse','HEAD'),sourceTree=value(snapshotRoot,'rev-parse','HEAD^{tree}');
 if(!isCommit(origin?.commit)||!isCommit(origin?.tree)||sourceCommit!==origin.commit||sourceTree!==origin.tree)throw Error('Actual capture run origin differs from immutable snapshot');
 const snapshotModule=await import(pathToFileURL(join(snapshotRoot,'web/test/visual-publish.mjs')).href),frozenMap=hashMap(snapshotModule.fixtureFiles(snapshotRoot),'capture-source broad fixture map'),recordedMapPresent=fixtureFiles!==undefined&&fixtureFiles!==null;
 if(recordedMapPresent&&!equal(hashMap(fixtureFiles,'recorded broad fixture map'),frozenMap))throw Error('Recorded broad fixture map differs from original capture source bytes');
 const entries=gitEntries(snapshotRoot,sourceCommit),prefix='web/public/',publicPaths=[...entries.keys()].filter(path=>path.startsWith(prefix)),paths=[...new Set([...Object.keys(frozenMap),...publicPaths])],blobHashes=gitBlobHashes(snapshotRoot,entries,paths);
 for(const [path,expected]of Object.entries(frozenMap))if(blobHashes.get(entries.get(path)?.oid)!==expected)throw Error('Capture-source broad map differs from actual immutable Git bytes: '+path);
 const publicTree=value(snapshotRoot,'rev-parse',sourceCommit+':web/public'),currentPublicTree=value(currentRoot,'rev-parse','HEAD:web/public');if(currentPublicTree!==publicTree)throw Error('Current public Git tree differs from captured source');
 const publicFiles=hashMap(Object.fromEntries(publicPaths.map(path=>[path.slice(prefix.length),blobHashes.get(entries.get(path).oid)])),'capture public Git files');
 if(!equal(actualFiles(join(currentRoot,'web/public')),publicFiles))throw Error('Current actual public bytes differ from captured Git tree');
 const runtimeBinding=await currentRuntimeBinding(currentRoot);if(!equal(runtimeBinding,hashMap(captureFixtureBinding,'actual capture runtime fixture binding')))throw Error('Current runtime fixture bytes differ from actual capture binding');
 return {schema:'agentj-visual-admission-source-binding-v1',sourceCommit,sourceTree,publicTree,recordedMapPresent,broadMapSource:recordedMapPresent?'recorded-map-verified-against-capture-source-git':'capture-source-git-snapshot-not-claimed-by-raw-receipt',fixtureFiles:frozenMap,fixtureFilesSha256:sha(JSON.stringify(frozenMap)),recordedFixtureFilesSha256:recordedMapPresent?sha(JSON.stringify(frozenMap)):null,runtimeBinding,runtimeBindingSha256:sha(JSON.stringify(runtimeBinding)),publicFilesSha256:sha(JSON.stringify(publicFiles))};
}
