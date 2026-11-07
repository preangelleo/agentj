// One-time #577 source migration: preserve the original selector's specificity with :where owner opt-in.
// Inputs are the frozen original Web brand typography/code sections, never the shared brand source.
import{readFileSync,writeFileSync}from'node:fs';import{rules,declarations}from'./css-syntax.mjs';import{splitSelectors}from'./ownership-contract.mjs';
const root='agentjarvis/web',reg=JSON.parse(readFileSync(root+'/component-ownership.json','utf8'));const owners=[...new Set([...JSON.parse(readFileSync(root+'/control-ownership-sites.json','utf8')).owners,...reg.components.map(x=>x.namespace),...Object.keys(reg.independentFamilies||{})])].sort();
const bare=/\b(?:h[1-6]|small|hr|a|code|kbd|samp|pre)\b/;
function role(head){if(/h([1-6])|aj-h([1-6])/.test(head))return'heading-'+(/h([1-6])|aj-h([1-6])/.exec(head).slice(1).find(Boolean));if(/\ba\b/.test(head))return'link'+(head.includes(':hover')?'-hover':'');if(/\bsmall\b|aj-small/.test(head))return'small';if(/\bhr\b/.test(head))return'divider';if(/kbd/.test(head))return'keycap';if(/samp/.test(head))return'sample';if(/:not\(pre\)/.test(head))return'inline-code';if(/pre\s+code/.test(head))return'code-in-block';if(/pre|aj-code/.test(head))return'code-block';return'inline-code';}
for(const path of ['styles/03-content-typography.css','styles/15-code.css']){let s=readFileSync(root+'/'+path,'utf8');for(const r of rules(s).reverse()){
 if(r.head.startsWith('@')||r.head.includes('data-aj-owner')||!bare.test(r.head))continue;
 // p/strong/b/selection are separately reviewed exact technical contracts, not component appearance.
 let text='';for(const branch of splitSelectors(r.head)){
  if(!bare.test(branch)){text+=branch+'{'+r.body+'}\n';continue;}
  let branches=[branch];const m=/:is\(([^()]*)\)/.exec(branch);if(m&&/\bh[1-6]\b/.test(m[1]))branches=splitSelectors(m[1]).map(arg=>({selector:branch.replace(m[0],m[0]+':where('+arg+')'),role:role(arg)}));
  for(const branchInfo of branches){const selector=typeof branchInfo==='string'?branchInfo:branchInfo.selector,name=typeof branchInfo==='string'?role(selector):branchInfo.role;
   const familyOwners=selector.startsWith('.aj-prose')?['aj-prose']:owners;
   for(const owner of familyOwners){const head=selector+':where([data-aj-owner="'+owner+'"])';
    const body=declarations(r.body).map(d=>{const imp=/\s*!important\s*$/.test(d.value)?' !important':'',v=d.value.replace(/\s*!important\s*$/,'');return d.property+':var(--'+owner+'-typography-'+name+'-'+d.property+','+v+')'+imp+';';}).join('');text+=head+'{'+body+'}\n';
   }
  }
 }
 s=s.slice(0,r.start)+text+s.slice(r.end);
}writeFileSync(root+'/'+path,s);}
console.log(JSON.stringify({owners:owners.length,typography:'explicit self owner, original branch specificity'}));
