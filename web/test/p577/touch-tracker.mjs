// Chromium 152 InputHandler::touch_points_ belongs to one protocol handler.
// Never use a new CDP session's empty state to claim another session is clear.
export function createTouchTracker(){
 const sessions=new Map(),taps={started:0,completed:0,inFlight:0,unknown:false};
 const tracker={
  registerNewSession(cdp,label='owned-cdp'){
   if(sessions.has(cdp))return cdp;
   const state={id:label+'-'+(sessions.size+1),points:new Map(),inFlight:0,unknown:false,detached:false},send=cdp.send.bind(cdp),detach=cdp.detach?.bind(cdp);sessions.set(cdp,state);
   cdp.send=async(method,params={})=>{
    if(method!=='Input.dispatchTouchEvent'){const result=await send(method,params);if(method==='Input.disable')state.points.clear();if(/^Input\.(?:synthesize|emulateTouch)/.test(method))state.unknown=true;return result;}
    if(state.detached||state.unknown)throw Error('Touch command uses an unknown/detached session');if(state.inFlight){state.unknown=true;throw Error('Concurrent touch commands have unknown ordering');}state.inFlight++;
    try{const result=await send(method,params),points=params.touchPoints||[];if(params.type==='touchStart'||params.type==='touchMove')points.forEach((p,i)=>state.points.set(p.id??i,{...p}));else if(params.type==='touchCancel'||params.type==='touchEnd'&&!points.length)state.points.clear();else if(params.type==='touchEnd')points.forEach((p,i)=>state.points.delete(p.id??i));else state.unknown=true;return result;}catch(error){state.unknown=true;throw error;}finally{state.inFlight--;}
   };
   if(detach)cdp.detach=async()=>{if(state.points.size||state.inFlight||state.unknown)throw Error('Cannot detach a touch session with active or unknown points');await detach();state.detached=true;};return cdp;
  },
  async tap(run){taps.started++;taps.inFlight++;try{const result=await run();taps.completed++;return result;}catch(error){taps.unknown=true;throw error;}finally{taps.inFlight--;}}
  ,async reset({atBlank=false}={}){
   if(taps.unknown||taps.inFlight||[...sessions.values()].some(s=>s.unknown||s.inFlight||s.detached&&s.points.size))throw Error('Touch state is unknown or still in flight; cannot prove release');
   if(!atBlank&&[...sessions.values()].some(s=>s.points.size))throw Error('Touch cancellation requires the verified blank boundary');
   const rows=[];let attempted=0,dispatched=0;for(const [cdp,s]of sessions){const activeBefore=[...s.points.keys()];let cancelDispatched=false,protocolEmptyEvidence=null;if(activeBefore.length){attempted++;try{await cdp.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]});dispatched++;cancelDispatched=true;}catch(error){if(!atBlank||!/Protocol error \(Input\.dispatchTouchEvent\): Must send a TouchStart first to start a new touch\.$/.test(error.message||''))throw error;protocolEmptyEvidence={sessionId:s.id,method:'Input.dispatchTouchEvent',type:'touchCancel',error:error.message,scope:'same-registered-handler-at-blank'};s.points.clear();s.unknown=false;}}rows.push({id:s.id,detached:s.detached,activeBefore,activeAfter:[...s.points.keys()],stateKnown:!s.unknown,inFlight:s.inFlight,cancelAttempted:activeBefore.length>0,cancelDispatched,protocolEmptyEvidence});}
   if(rows.some(s=>s.activeAfter.length))throw Error('Touch cancel left tracked points active');
   return {status:dispatched?'cancelled':'no-active-touch',attempted:attempted>0,dispatched:dispatched>0,sessions:rows,playwrightTaps:{...taps},inputPaths:['tracked-locator-tap','tracked-page-touchscreen-tap','registered-cdp-input']};
  }
 };return tracker;
}
