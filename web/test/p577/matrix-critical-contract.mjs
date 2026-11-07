import{criticalOwnershipScope}from'./publish-critical-profile.mjs';
import{nativeMatrixRows}from'./native-matrix-rows.mjs';
export function validateCriticalShardReceipt(shard,{selection,sourceCombined}){
 const scope=criticalOwnershipScope(selection,sourceCombined);
 if(shard.sourceCombined!==sourceCombined||shard.ownershipAuditScope!==scope||JSON.stringify(shard.criticalSceneSelection)!==JSON.stringify(selection)||JSON.stringify(shard.coverage?.requiredScenes?.scenes)!==JSON.stringify(selection.scenes))throw Error('Critical shard actual source, scope or selected scene binding differs');
 if(shard.configs?.length!==1)throw Error('Critical shard requires one actual configuration');
 for(const cfg of shard.configs){const names=shard.rows.filter(r=>['width','height','theme','lang'].every(k=>r[k]===cfg[k])).map(r=>r.name).sort();if(JSON.stringify(names)!==JSON.stringify([...selection.scenes].sort()))throw Error('Critical shard scene set missing, duplicated or extra');}
 const appRows=nativeMatrixRows(shard.rows,{critical:true,mode:'full'});
 if(scope==='formal-pre577-source'){if(shard.nativeControlCoverage!==null||appRows.some(r=>r.ownerAuditOK!==null))throw Error('Formal pre577 source cannot claim 577 ownership qualification');}
 else if(shard.side==='after'&&appRows.some(r=>r.ownerAuditOK!==true))throw Error('577 candidate still requires strict actual component ownership');
 return scope;
}
