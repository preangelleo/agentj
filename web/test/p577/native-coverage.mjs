export function aggregateNativeCoverage(rows, expectedIds) {
 const expected=new Set(expectedIds), seen=new Map(), errors=[];
 if(!expected.size||expected.size!==expectedIds.length)errors.push('Missing or duplicate required native contracts');
 for(const row of rows){const e=row.nativeControlEvidence;if(!e){errors.push({scene:row.name,error:'Missing native control evidence'});continue}for(const problem of [...(e.errors||[]),...(e.conflicts||[])])errors.push({scene:row.name,problem});
  const ids=new Set();for(const record of e.records||[]){if(!expected.has(record.id)||ids.has(record.id)||!Number.isInteger(record.matches)||record.matches<0){errors.push({scene:row.name,error:'Invalid native contract record',id:record.id});continue}ids.add(record.id);if(record.matches){const matches=seen.get(record.id)||[];matches.push({scene:row.name,width:row.width,height:row.height,theme:row.theme,lang:row.lang,matches:record.matches});seen.set(record.id,matches)}}
  if(ids.size!==expected.size)errors.push({scene:row.name,error:'Incomplete native contract record set'});
 }
 const contracts=expectedIds.map(id=>({id,observations:seen.get(id)||[],status:seen.has(id)?'observed':'not-instantiated-in-captured-scenes'}));
 return {schemaVersion:1,qualified:!errors.length&&contracts.every(c=>c.observations.length>0),required:expected.size,observed:seen.size,missing:contracts.filter(c=>!c.observations.length).map(c=>c.id),contracts,errors,scope:'Union of actual after scene DOM only; observed includes hidden instantiated subjects, not proof of foreground visual coverage or full matrix.'};
}
