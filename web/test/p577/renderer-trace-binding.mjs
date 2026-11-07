import {mkdirSync,writeFileSync}from'node:fs';import{join}from'node:path';import{gzipSync}from'node:zlib';import{createHash}from'node:crypto';
// Chromium 152.0.7977.82 tracing_handler.cc FillFrameData / ReadyToCommitNavigation.
// Tracing must be initiated on the owned Page handler, not the Browser handler.
export function archiveRendererTrace(directory,events,{frameId,loaderId}={}){
 mkdirSync(join(directory,'isolation'),{recursive:true});const file='isolation/renderer-trace.json.gz',bytes=gzipSync(JSON.stringify({traceEvents:events})),sha256=createHash('sha256').update(bytes).digest('hex');writeFileSync(join(directory,file),bytes);
 const eventCounts={};for(const e of events)eventCounts[e.name]=(eventCounts[e.name]||0)+1;
 const navigationEvents=events.filter(e=>/FrameCommittedInBrowser|TracingStartedInBrowser|CommitLoad|navigationStart|ProcessReadyInBrowser/.test(e.name));
 const census={schema:'p577-renderer-trace-census-v1',frameId,loaderId,events:events.length,eventCounts,navigationEvents,raw:{file,sha256},traceTarget:'owned-main-page'};
 writeFileSync(join(directory,'isolation/renderer-trace-census.json'),JSON.stringify(census,null,2)+'\n');
 return {file,sha256,frameId,census};
}
export function rendererTraceBinding(events,frameId){let binding=null;for(const event of events){const d=event.args?.data;if(event.name==='FrameCommittedInBrowser'&&d?.frame===frameId&&Number.isInteger(d.processId)&&d.processId>0)binding={eventName:event.name,processId:d.processId,frameId};}if(!binding)throw Error('Actual frame-to-renderer trace binding unavailable; raw trace and census retained');return binding;}
