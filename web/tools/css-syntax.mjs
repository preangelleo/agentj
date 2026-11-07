// CSS structural parser for ownership tooling: comments/strings/escapes and nested at-rules are lexical tokens.
// It retains source offsets so a diagnostic and rewrite refer to the exact authored rule.
export function rules(text, context=[], base=0) {
 const out=[]; let start=0, open=-1, depth=0, quote='', comment=false;
 for(let i=0;i<text.length;i++){
  const c=text[i], pair=text.slice(i,i+2);
  if(comment){if(pair==='*/'){comment=false;i++;}continue;}
  if(quote){if(c==='\\'){i++;continue;}if(c===quote)quote='';continue;}
  if(pair==='/*'){comment=true;i++;continue;}if(c==='"'||c==="'"){quote=c;continue;}
  if(c==='{'){if(depth===0)open=i;depth++;}
  if(c==='}'){
   if(--depth<0)throw new Error('Unbalanced CSS closing brace');
   if(depth===0){
    const raw=text.slice(start,open), head=stripComments(raw).trim();
    let lead=0;while(lead<raw.length){if(/\s/.test(raw[lead])){lead++;continue;}if(raw.startsWith('/*',lead)){const end=raw.indexOf('*/',lead+2);if(end<0)throw Error('Unterminated leading comment');lead=end+2;continue;}break;}
    const headAt=start+lead, body=text.slice(open+1,i), node={head,body,context,start:base+headAt,open:base+open,end:base+i+1};
    if(head.startsWith('@')&&!/^@(?:font-face|property|(?:-webkit-)?keyframes)\b/.test(head))out.push(...rules(body,[...context,head],base+open+1));
    else {if(!/^@(?:-webkit-)?keyframes\b/.test(head)&&structuralBraces(body))throw Error('Unsupported nested style block in '+head);out.push(node);}
    start=i+1;
   }
  }
 }
 if(depth||quote||comment)throw new Error('Unterminated CSS block/string/comment');
 const tail=stripComments(text.slice(start)).trim();
 if(tail&&!tail.split(';').every(x=>!x.trim()||/^@(?:import|charset)\b/.test(x.trim())))throw new Error('Unsupported CSS trailing syntax; inspect the source file without logging its contents');
 return out;
}
export function declarations(body){
 const out=[];let start=0,quote='',depth=0,comment=false;
 for(let i=0;i<=body.length;i++){
  const c=body[i],pair=body.slice(i,i+2);
  if(comment){if(pair==='*/'){comment=false;i++;}continue;}
  if(quote){if(c==='\\'){i++;continue;}if(c===quote)quote='';continue;}
  if(pair==='/*'){comment=true;i++;continue;}if(c==='"'||c==="'"){quote=c;continue;}
  if(c==='('||c==='[')depth++;if(c===')'||c===']')depth--;
  if((c===';'&&!depth)||i===body.length){const raw=body.slice(start,i),clean=stripComments(raw).trim(),k=clean.indexOf(':');if(k>=0)out.push({property:clean.slice(0,k).trim(),value:clean.slice(k+1).trim(),raw,start,end:i});start=i+1;}
 }
 return out;
}
export function classNames(selector){const out=[];let quote='',bracket=0;for(let i=0;i<selector.length;i++){const c=selector[i];if(quote){if(c==='\\'){i++;continue;}if(c===quote)quote='';continue;}if(c==='"'||c==="'"){quote=c;continue;}if(c==='['){bracket++;continue;}if(c===']'){bracket--;continue;}if(!bracket&&c==='.'){const m=/^-?[_a-zA-Z]+[\w-]*/.exec(selector.slice(i+1));if(m){out.push(m[0]);i+=m[0].length;}}}return out;}

function structuralBraces(text){let quote='',comment=false;for(let i=0;i<text.length;i++){const c=text[i],pair=text.slice(i,i+2);if(comment){if(pair==='*/'){comment=false;i++;}continue;}if(quote){if(c==='\\'){i++;continue;}if(c===quote)quote='';continue;}if(pair==='/*'){comment=true;i++;continue;}if(c==='"'||c==="'"){quote=c;continue;}if(c==='{'||c==='}')return true;}return false;}

// True CSS comments only: strings and escaped quote characters retain byte semantics.
export function stripComments(text){let out='',quote='';for(let i=0;i<text.length;i++){const c=text[i];if(quote){out+=c;if(c==='\\'&&i+1<text.length){out+=text[++i];continue;}if(c===quote)quote='';continue;}if(c==='"'||c==="'"){quote=c;out+=c;continue;}if(text.startsWith('/*',i)){const end=text.indexOf('*/',i+2);if(end<0)throw Error('Unterminated CSS comment');i=end+1;continue;}out+=c;}return out;}
