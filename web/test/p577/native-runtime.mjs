// Bind independently observed native actors to the primary DOM node dictionary by exact path.
export function bindNativeRuntime(sample, audit) {
 const evidence=structuredClone(audit.nativeControlEvidence||{records:[],conflicts:[],errors:[]});evidence.errors||=[];
 const paths=new Map();for(const [id,node]of Object.entries(sample.nodes||{})){if(!node.path||paths.has(node.path)){evidence.errors.push({error:'Missing/duplicate primary native node path',nodeId:id});continue}paths.set(node.path,id)}
 for(const record of evidence.records||[]){const seen=new Set();record.elements=(record.elements||[]).map(element=>{const nodeId=paths.get(element.path),scopeNodeId=paths.get(element.scopePath);if(!scopeNodeId||!nodeId||!(element.path===element.scopePath||element.path.startsWith(element.scopePath+'>'))||sample.nodes[nodeId].nodeId!==nodeId||seen.has(nodeId)||!Number.isInteger(element.bindingIndex)){evidence.errors.push({contractId:record.id,error:'Unresolved/duplicate native consumer path or binding index',path:element.path});return{nodeId:null,scopeNodeId:scopeNodeId||null,bindingIndex:element.bindingIndex}}seen.add(nodeId);return{nodeId,scopeNodeId,bindingIndex:element.bindingIndex}});if(record.matches!==record.elements.length)evidence.errors.push({contractId:record.id,error:'Native match count disagrees with actual consumers'})}
 sample.nativeContractBinding=structuredClone(audit.nativeContractBinding||null);sample.nativeControlEvidence=evidence;
 if(!sample.nativeContractBinding||evidence.schemaVersion!==1||!evidence.records?.length)evidence.errors.push({error:'Missing native source binding or contract records'});
 return evidence;
}
