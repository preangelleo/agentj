// N/A is an explicit scene contract, never an escape from native DOM evidence.
export function nativeMatrixRows(rows,{critical=false,mode='full'}={}){
 return rows.filter(row=>{
  if(row.notApplicable){
   const reason=critical?'Desktop UA uses browser install menu':'Desktop user agent uses browser install menu, not iOS instructions';
   if(mode!=='full'||row.name!=='settings-ios'||!(row.width>390)||row.notApplicable!==reason)throw Error('Undeclared uncaptured native scene: '+row.name);
   return false;
  }
  if(row.sourceScope==='dashboard-preserved'){
   if(!critical||row.name!=='publish-dashboard-account')throw Error('Undeclared non-App native scene: '+row.name);
   return false;
  }
  return true;
 });
}
