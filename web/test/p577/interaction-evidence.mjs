import{join}from'node:path';
import{nativeImpactVerifier}from'../../tools/native-impact.mjs';
import{validateNativeProofRows}from'./native-receipt-proof.mjs';
export const interactionScenes=['reply','source-quote','robot'].flatMap(f=>['hover','focus-visible','active'].map(s=>f+'-'+s));
export function validateInteractionEvidence(samples,fixtureBinding,sourceCombined,proof){
 const errors=[];
 if(!proof?.nativeContext||!proof.webRoot)return{ok:false,requiredScenes:interactionScenes,errors:['Core native source context absent; schema-only proof cannot qualify actors']};
 const verifier=nativeImpactVerifier(proof.afterRoot||join(proof.webRoot,'public'),sourceCombined,proof.nativeContext,error=>errors.push(error));
 if(!verifier.required)errors.push('Core verifier found no exact native source contracts');
 for(const name of interactionScenes){
  const found=samples.filter(s=>(s.scene||s.name)===name);if(found.length!==1){errors.push({scene:name,error:'Each interaction state requires exactly one independently sampled snapshot'});continue}
  const s=found[0];errors.push(...validateNativeProofRows([{...s,name}],fixtureBinding,sourceCombined,proof).errors);
  if(s.sourceCombined!==sourceCombined)errors.push({scene:name,error:'Interaction source binding absent'});
  try{verifier.consume(s,ref=>{const node=s.nodes?.[ref.nodeId];if(typeof ref.nodeId!=='string'||!ref.nodeId||!node||node.nodeId!==ref.nodeId)throw Error('Invalid primary actor/scope reference');return node})}catch(error){errors.push({scene:name,error:'Core native verifier failed closed: '+String(error)})}
 }
 return{ok:!errors.length,requiredScenes:interactionScenes,errors,scope:'Actual core actor/selector/scope validation for these nine snapshots only; not whole native-consumer or visual coverage qualification.'};
}
