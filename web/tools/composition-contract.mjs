import{createHash}from'node:crypto';import{declarations}from'./css-syntax.mjs';import{nativeSubjects,privateTokenRefs}from'./ownership-contract.mjs';
export const compositionBodySha=body=>createHash('sha256').update(body).digest('hex');
// A CSS role authorizes only this exact, reviewed rule. It is never an actor
// source for branchOwners/nativeSubjects or a default for the role's descendants.
export function validateComposition(rule,entry,norm,occurrence){
 const issues=[],role=norm.cssRoles?.[entry?.role];
 if(!role?.selectors?.some(s=>s.file===entry?.file&&s.selector===rule.head&&JSON.stringify(s.context)===JSON.stringify(rule.context)))issues.push('composition-role-has-no-independent-selector-contract');
 if(!entry||!role||entry.selector!==rule.head||entry.occurrence!==occurrence||JSON.stringify(entry.context)!==JSON.stringify(rule.context)||entry.bodySha256!==compositionBodySha(rule.body))issues.push('composition-contract-source-mismatch');
 if(!entry?.producerEvidence?.length||!role?.purpose||!entry?.sourceReview)issues.push('composition-contract-needs-independent-producer-review');
 if(!role?.sourceReview)issues.push('composition-role-lacks-independent-review');
 const ds=declarations(rule.body);
 const layoutProperties=['display','flex','flex-direction','flex-wrap','align-items','gap','width','margin','margin-top','margin-block-start'];
 if(role?.kind==='composition-layout'&&ds.some(d=>!layoutProperties.includes(d.property)))issues.push('composition-layout-cannot-acquire-appearance');if(!ds.length||ds.some(d=>!role?.properties.includes(d.property)))issues.push('composition-property-outside-role');
 if(nativeSubjects(rule.head,norm).length)issues.push('composition-role-cannot-authorize-native-control');
 const refs=privateTokenRefs(rule.body);if(refs.some(ref=>!ref.startsWith('--'+entry.role+'-')))issues.push('composition-private-token-crosses-role');
 return{ok:issues.length===0,issues,role:entry?.role};
}
