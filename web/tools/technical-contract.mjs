import{createHash}from'node:crypto';import{declarations}from'./css-syntax.mjs';
export const declarationDigest=body=>createHash('sha256').update(JSON.stringify(declarations(body).map(({property,value})=>({property,value})))).digest('hex');
export const validatesTechnical=(rule,entry)=>rule.head===entry.selector&&JSON.stringify(rule.context)===JSON.stringify(entry.context)&&declarationDigest(rule.body)===entry.declarationSha256&&(!entry.bodySha256||createHash('sha256').update(rule.body).digest('hex')===entry.bodySha256);
