// Resolve ONLY component-private fallback tokens to inspect unchanged initial appearance in static contracts.
// Palette/design and explicitly declared private references without an inline fallback remain dependencies.
export function resolveDefaults(css){
 let out='';for(let i=0;i<css.length;){
  if(css.startsWith('/*',i)){const end=css.indexOf('*/',i+2);if(end<0)throw Error('Unterminated CSS comment');out+=css.slice(i,end+2);i=end+2;continue;}
  if(css[i]==="'"||css[i]==='\"'){const quote=css[i];let end=i+1;for(;end<css.length;end++){if(css[end]==='\\'){end++;continue;}if(css[end]===quote)break;}out+=css.slice(i,end+1);i=end+1;continue;}
  if(css.startsWith('var(--aj-',i)){
   let d=1,k=i+4,comma=-1,quote='';for(;k<css.length;k++){const c=css[k];if(quote){if(c==='\\'){k++;continue;}if(c===quote)quote='';continue;}if(c==='"'||c==="'"){quote=c;continue;}if(c==='(')d++;else if(c===')'){if(--d===0)break;}else if(c===','&&d===1&&comma<0)comma=k;}
   if(d)throw Error('Unterminated component token near '+css.slice(i,i+90));
   if(comma<0){out+=css.slice(i,k+1);i=k+1;continue;}
   out+=resolveDefaults(css.slice(comma+1,k));i=k+1;
  }else out+=css[i++];
 }return out;
}
