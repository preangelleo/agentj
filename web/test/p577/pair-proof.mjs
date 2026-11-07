// Admission of the owned-Page isolation evidence; topology success is never pixel qualification.
import {pairPasses}from'./pair-matrix.mjs';
import {readFileSync} from 'node:fs';
import {resolve,sep,join} from 'node:path';
import {gunzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import {decodePng,pngEvidence} from '../../tools/png-evidence.mjs';
const hash=x=>createHash('sha256').update(x).digest('hex');
export const PAGE_ENVELOPE_SCHEMA='p577-owned-page-envelope-v3';
export const PAGE_PAIR_SCHEMA='p577-owned-page-pair-v2';
export const PAGE_PAIR_TOPOLOGY='one-owned-page-new-documents-per-side';
const nonempty=x=>typeof x==='string'&&x.length>0;
export function validatePagePair(r,{sources,allowedResponses={},manifestResponseAllowed,viewport}){
 const errors=[];
 if(r?.schema!==PAGE_PAIR_SCHEMA||r?.topology!==PAGE_PAIR_TOPOLOGY||!Number.isInteger(r?.browserProcessId)||r.browserProcessId<=0||!nonempty(r.contextId)||!nonempty(r.pageTargetId))errors.push('Actual owned Page v2 identity missing; old fresh-context proof cannot qualify');
 const fontMap=side=>Object.entries(sources?.[side]||{}).filter(([p])=>/\.(?:woff2?|ttf|otf)$/.test(p)).sort();
 if(JSON.stringify(fontMap('before'))!==JSON.stringify(fontMap('after')))errors.push('Shared renderer font resources differ; internal font-cache isolation is not claimed');
 const sides=r?.sides||[];if(sides.length!==2||sides[0]?.side!=='before'||sides[1]?.side!=='after')errors.push('Exactly two independently loaded source sides required');
 const loaders=new Set(),nonces=new Set();
 for(const row of sides){
  const side=row.side,i=row.isolation||{},storage=i.initialStorage||{},cleanup=i.cleanup||{},files=sources?.[side]||{};
  if(row.code!==0||row.error||!row.sourceStable||!row.fixtureStable||!/^[a-f0-9]{64}$/.test(row.environmentFingerprint||''))errors.push(side+': execution/source/fixture/environment incomplete');
  if(i.contextId!==r.contextId||i.pageTargetId!==r.pageTargetId||!nonempty(i.loaderId)||!nonempty(i.documentNonce)||loaders.has(i.loaderId)||nonces.has(i.documentNonce)||i.oldSentinelAbsent!==true)errors.push(side+': same owned Page with new document/realm identity not proven');
  loaders.add(i.loaderId);nonces.add(i.documentNonce);
  if(storage.origin!=='http://localhost:57780'||['localKeys','sessionKeys','indexedDBNames','cacheNames'].some(k=>!Array.isArray(storage[k])||storage[k].length)||storage.serviceWorkerCount!==0)errors.push(side+': initial source origin storage is not observed empty');
  const cleared=x=>x.usageAfter===0&&Array.isArray(x.domStorage)&&x.domStorage.length===2&&[true,false].every(local=>x.domStorage.filter(s=>s.isLocalStorage===local&&['cleared','no-active-frame'].includes(s.status)).length===1)&&x.domStorageCleared===x.domStorage.every(s=>s.status==='cleared');
  if(!Array.isArray(i.clearedOrigins)||!i.clearedOrigins.some(x=>x.origin===storage.origin&&cleared(x))||i.clearedOrigins.some(x=>!cleared(x)))errors.push(side+': actual synthetic origin clearing evidence incomplete');
  if(cleanup.complete!==true||cleanup.aboutBlankURL!=='about:blank'||cleanup.oldRealmSentinelSet!==true||cleanup.cookiesRemaining!==0||cleanup.renderResourceCachesCleared!==false||!Array.isArray(cleanup.origins))errors.push(side+': previous document cleanup or honest internal-cache boundary missing');
  if(!cleanup.oldOriginStorage||cleanup.oldOriginStorage.origin!==storage.origin||cleanup.oldOriginStorage.localKeysAfter?.length!==0||cleanup.oldOriginStorage.sessionKeysAfter?.length!==0||cleanup.fakeConnectionsRemaining!==0||cleanup.fakeHostStopped!==true||cleanup.sourceServerListening!==false||cleanup.sourceServerStopped!==true||cleanup.webSocketsClosed!==cleanup.webSocketsObserved)errors.push(side+': old source realm/storage/socket/server shutdown was not observed');
  if(!Number.isInteger(i.rendererProcessId)||i.rendererProcessId<=0||i.rendererEvidence?.processId!==i.rendererProcessId||i.rendererEvidence?.frameId!==i.frameId)errors.push(side+': actual frame/renderer trace identity missing');
  if(JSON.stringify(cleanup.retainedPhaseDispatchers)!==JSON.stringify(['source-response-delivery','synthetic-origin-inventory','websocket-lifecycle']))errors.push(side+': unreviewed persistent phase hook');
  for(const key of ['initScriptsRemoved','routesRemoved','listenersRemoved','cdpSessionsDetached','auxiliaryPagesClosed'])if(!Number.isInteger(cleanup[key])||cleanup[key]<0)errors.push(side+': missing actual '+key+' cleanup count');
  for(const [label,input]of [['initial',i.blankInput],['cleanup',cleanup.blankInput]]){
   const sameViewport=v=>v?.width===viewport?.width&&v?.height===viewport?.height;
   if(!input||!sameViewport(input.viewport)||!sameViewport(input.playwrightViewport)||!sameViewport(input.screen)||!sameViewport(input.cdpMetrics?.mappedDeviceViewport)||input.devicePixelRatio!==1)errors.push(side+': '+label+' device viewport reset not independently observed');
   const visual=input?.visualViewport;if(!visual||Math.round(visual.width*visual.scale)!==viewport?.width||Math.round(visual.height*visual.scale)!==viewport?.height)errors.push(side+': '+label+' actual visual viewport differs');
   if(input?.locale!==(viewport?.lang==='en'?'en-US':'zh-CN')||input?.timeZone!=='Asia/Tokyo'||input?.media?.colorScheme!==viewport?.theme||input?.media?.reducedMotion!=='reduce'||input?.media?.observed?.dark!==(viewport?.theme==='dark')||input?.media?.observed?.reduce!==true||input?.permissions?.cleared!==true||input?.permissions?.granted?.length!==0)errors.push(side+': '+label+' original locale/media/permission inputs not restored');
   if(input?.pointer?.x!==0||input?.pointer?.y!==0||JSON.stringify(input?.pointer?.releasedButtons)!==JSON.stringify(['left','middle','right'])||JSON.stringify(input?.pointer?.releasedModifiers)!==JSON.stringify(['Shift','Control','Alt','Meta']))errors.push(side+': '+label+' held input/pointer state not reset');
   const touch=input?.touchReset,taps=touch?.playwrightTaps;
   if(!touch||input.touchCancelDispatched!==touch.dispatched||!taps||taps.unknown!==false||taps.inFlight!==0||taps.started!==taps.completed)errors.push(side+': '+label+' touch input completion unknown');
   if(r.mode==='touch'){const sessions=touch?.sessions||[];if(!['no-active-touch','cancelled'].includes(touch?.status)||!sessions.length||new Set(sessions.map(x=>x.id)).size!==sessions.length||sessions.some(x=>!x.id||x.stateKnown!==true||x.inFlight!==0||!Array.isArray(x.activeAfter)||x.activeAfter.length)||touch.attempted!==sessions.some(x=>x.cancelAttempted)||touch.dispatched!==sessions.some(x=>x.cancelDispatched)||(touch.status==='cancelled')!==touch.dispatched)errors.push(side+': '+label+' original touch handler release not observed');for(const session of sessions){if(session.cancelAttempted&&!session.cancelDispatched){const e=session.protocolEmptyEvidence;if(e?.sessionId!==session.id||e.method!=='Input.dispatchTouchEvent'||e.type!=='touchCancel'||e.scope!=='same-registered-handler-at-blank'||!e.error?.endsWith('Must send a TouchStart first to start a new touch.'))errors.push(side+': '+label+' empty evidence came from an unrelated/unknown handler');}}}
   else if(touch?.status!=='not-enabled'||touch?.dispatched!==false)errors.push(side+': '+label+' unexpected touch state in mouse configuration');
   if(input?.historyReset!==true||input?.history?.currentIndex!==0||input?.history?.entries?.length!==1||input.history.entries[0].url!=='about:blank')errors.push(side+': '+label+' history retains old source document');
  }
  const navigation=i.sourceNavigation;
  if(navigation?.method!=='renderer-location.replace'||navigation.from!=='about:blank'||navigation.history?.currentIndex!==0||navigation.history?.entries?.length!==1||navigation.history.entries[0].url!==navigation.url||!navigation.url?.startsWith('http://localhost:57780/'))errors.push(side+': first source history/new document replacement not observed');
  const responses=i.sourceResponses||[],seen=new Set();
  for(const response of responses){
   const {path,sourceSha256,responseSha256,noStore,count}=response;
   if(response.delivery!=='browser-response-body'||!Object.hasOwn(files,path)||sourceSha256!==files[path]||noStore!==true||!Number.isInteger(count)||count<1)errors.push(side+': response does not bind actual source bytes/no-store: '+path);
   const variants=allowedResponses?.[side]?.[path],allowed=(Array.isArray(variants)?variants:[variants]).some(x=>typeof x==='string'?x===responseSha256:x?.sha256===responseSha256&&x.transformation===response.transformation);
   if(responseSha256!==sourceSha256&&!allowed&&!(response.transformation==='seat-manifest'&&manifestResponseAllowed?.(side,response)))errors.push(side+': unapproved response transformation: '+path);
   seen.add(path);
  }
  for(const path of ['index.html','app.js','app.css','brand/base.css','version.js'])if(!seen.has(path))errors.push(side+': core source response missing: '+path);
 }
 if(sides.length===2&&sides[0].isolation?.rendererProcessId!==sides[1].isolation?.rendererProcessId)errors.push('Actual main-frame renderer changed between source documents');
 if(sides.length===2&&sides[0].environmentFingerprint!==sides[1].environmentFingerprint)errors.push('Actual static rendering environments differ');
 return errors;
}
function verifyPageArtifacts(root,r,options,labels={}){
 const errors=[];
 for(const side of r.sides||[]){try{const evidence=side.isolation.rendererEvidence,file=resolve(root,labels[side.side]||side.side,evidence.file);if(!file.startsWith(root+sep))throw Error('trace path escapes');const bytes=readFileSync(file);if(hash(bytes)!==evidence.sha256||evidence.eventName!=='FrameCommittedInBrowser')throw Error('renderer trace hash/type mismatch');const events=JSON.parse(gunzipSync(bytes)).traceEvents;if(!events.some(e=>e.name==='FrameCommittedInBrowser'&&e.args?.data?.frame===evidence.frameId&&e.args?.data?.processId===evidence.processId))throw Error('frame-to-renderer mapping missing from actual trace');}catch(error){errors.push(side.side+': actual renderer trace proof invalid: '+String(error));}}
 for(const side of r.sides||[]){try{const blank=side.isolation.cleanup.blank,path=resolve(root,labels[side.side]||side.side,blank.file);if(!path.startsWith(root+sep))throw Error('blank path escapes');const bytes=readFileSync(path),png=decodePng(bytes),ev=pngEvidence(bytes),color=png.rgba.subarray(0,4);if(hash(bytes)!==blank.sha256||ev.rgbaSha256!==blank.rgbaSha256||png.width!==blank.width||png.height!==blank.height||png.width!==options.viewport?.width||png.height!==options.viewport?.height||!Array.isArray(blank.uniformRGBA)||!color.equals(Buffer.from(blank.uniformRGBA)))throw Error('blank image binding mismatch');for(let n=0;n<png.rgba.length;n+=4)if(!color.equals(png.rgba.subarray(n,n+4)))throw Error('old UI remains in cleared viewport');}catch(error){errors.push(side.side+': actual blank-surface proof invalid: '+String(error));}}
 return errors;
}

export function verifyPagePair(directory,options){const root=resolve(directory),r=JSON.parse(readFileSync(join(root,'pair.json'))),errors=[...validatePagePair(r,options),...verifyPageArtifacts(root,r,options)];if(errors.length)throw Error('Owned Page isolation admission rejected: '+JSON.stringify(errors));return r;}
export function validatePageEnvelope(r,options){
 const errors=[];let passes=[];
 if(r?.schema!==PAGE_ENVELOPE_SCHEMA||r.profile!=='publish-critical'||r.mode!=='full'||!Number.isInteger(r.baselineSamples)||r.baselineSamples<3)errors.push('Critical envelope v3 requires at least three baseline samples');
 else passes=pairPasses(r);
 if(options.baselineSamples!==undefined&&r?.baselineSamples!==options.baselineSamples)errors.push('Baseline sample count differs from required phase contract');
 const rows=r?.sides||[];if(rows.length!==passes.length)errors.push('Independent baseline/candidate pass count incomplete');
 for(let index=0;index<passes.length;index++){const pass=passes[index],row=rows[index];if(row?.side!==pass.label||row?.captureSide!==pass.captureSide||row?.sampleIndex!==pass.sampleIndex)errors.push('Envelope pass label/capture side mismatch: '+pass.label);}
 const candidate=rows.at(-1);for(const row of rows.slice(0,-1)){const projected={...r,schema:PAGE_PAIR_SCHEMA,sides:[{...row,side:'before'},{...candidate,side:'after'}]};errors.push(...validatePagePair(projected,options).map(e=>row.side+': '+e));}
 for(const key of ['loaderId','documentNonce'])if(new Set(rows.map(x=>x.isolation?.[key])).size!==passes.length||rows.some(x=>!nonempty(x.isolation?.[key])))errors.push('Envelope '+key+' must be new across every pass');
 return errors;
}
function verifiedFile(root,binding,expected){if(!binding||binding.file!==expected)throw Error('Missing/exchanged actual capture artifact: '+expected);const path=resolve(root,binding.file);if(!path.startsWith(root+sep))throw Error('Capture artifact path escapes');const bytes=readFileSync(path);if(hash(bytes)!==binding.sha256)throw Error('Actual capture artifact hash differs: '+expected);return JSON.parse(bytes);}
const sortedMap=value=>JSON.stringify(Object.entries(value||{}).sort(([a],[b])=>a.localeCompare(b)));
export function verifyPageEnvelope(directory,options){
 const root=resolve(directory),r=JSON.parse(readFileSync(join(root,'pair.json'))),errors=validatePageEnvelope(r,options),rows=r.sides||[],candidate=rows.at(-1);let fixture=null;
 for(const row of rows.slice(0,-1)){const projected={...r,schema:PAGE_PAIR_SCHEMA,sides:[{...row,side:'before'},{...candidate,side:'after'}]};errors.push(...verifyPageArtifacts(root,projected,options,{before:row.side,after:'after'}).map(e=>row.side+': '+e));}
 for(const row of rows){try{
  const capture=verifiedFile(root,row.captureReceipt,row.side+'/result.json');
  if(capture.side!==row.captureSide||capture.source!==row.source||capture.sourceCombined!==row.sourceCombined||capture.environmentFingerprint!==row.environmentFingerprint||capture.sourceStable!==row.sourceStable||capture.fixtureStable!==row.fixtureStable||JSON.stringify(capture.pairedCapture?.isolation)!==JSON.stringify(row.isolation))throw Error('Pass row differs from its actual capture result');
  const expected=options.sources?.[row.captureSide];for(const name of ['before','after']){const actual=verifiedFile(root,row.sourceManifests?.[name],row.side+'/source-'+name+'.json');if(sortedMap(actual)!==sortedMap(expected))throw Error('Actual source bytes differ from frozen pass source: '+name);}
  const binding=JSON.stringify(capture.fixtureBinding);if(!binding)throw Error('Actual fixture binding missing');if(fixture!==null&&binding!==fixture)throw Error('Actual fixture binding changed across passes');fixture=binding;
 }catch(error){errors.push(row.side+': '+String(error));}}
 if(errors.length)throw Error('Owned Page envelope isolation admission rejected: '+JSON.stringify(errors));return r;
}

export function verifyPairPhase(directory,{sources,configs,mode,profile=null,allowedResponses={},manifestResponseAllowed,baselineSamples=1}){
 const proofs=[];
 for(let index=0;index<configs.length;index++){
  const path=join(directory,'pairs',String(index).padStart(2,'0')),r=(baselineSamples>1?verifyPageEnvelope:verifyPagePair)(path,{sources,allowedResponses,manifestResponseAllowed,viewport:configs[index],baselineSamples});
  if(r.index!==index||r.mode!==mode||(r.profile||null)!==profile||(r.baselineSamples??1)!==baselineSamples)throw Error('Pair configuration/phase identity mismatch');
  const bytes=readFileSync(join(path,'pair.json'));proofs.push({index,baselineSamples:r.baselineSamples??1,passes:r.sides.map(x=>({side:x.side,captureSide:x.captureSide||x.side})),schema:r.schema,topology:r.topology,file:`pairs/${String(index).padStart(2,'0')}/pair.json`,sha256:hash(bytes),browserProcessId:r.browserProcessId,contextId:r.contextId,pageTargetId:r.pageTargetId});
 }
 return proofs;
}

export function manifestAllowance({manifests,seatRoute,seatManifest}){return(side,response)=>{try{if(!/^manifest(?:-dark|-light)?\.webmanifest$/.test(response.path))return false;const u=new URL(response.requestUrl);if(u.origin!=='http://localhost:57780'||response.requestPath!==u.pathname+u.search)return false;const seat=seatRoute(u.pathname);if(!seat||seat.rest!=='/'+response.path||!manifests?.[side]?.[response.path])return false;const body=JSON.stringify(seatManifest(JSON.parse(manifests[side][response.path]),seat.seat,u.searchParams));return hash(body)===response.responseSha256;}catch{return false;}};}
