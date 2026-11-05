// #490: separate a floating surface only when its actual painted backdrop blends into it.
// Computed colours include custom themes; transparent scrims are composited over the page.
const colourCache=new Map();
export function rgba(value) {
  const m=String(value).match(/^rgba?\(([^)]+)\)$/);
  if(!m){
    // color-mix() resolves to color(srgb …) / oklab() in current browsers, including custom page themes.
    if(colourCache.has(value))return colourCache.get(value);
    if(typeof document==='undefined')return [0,0,0,0];
    const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
    const ctx=canvas.getContext('2d',{willReadFrequently:true});
    ctx.clearRect(0,0,1,1);ctx.fillStyle=value;ctx.fillRect(0,0,1,1);
    const pixel=ctx.getImageData(0,0,1,1).data,result=[...pixel.slice(0,3),pixel[3]/255];
    if(colourCache.size>128)colourCache.clear();colourCache.set(value,result);return result;
  }
  const a=m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
  return [a[0],a[1],a[2],a.length>3?a[3]:1];
}
export const over=(a,b)=>{const alpha=a[3]+b[3]*(1-a[3]);return alpha ? [...a.slice(0,3).map((v,i)=>(v*a[3]+b[i]*b[3]*(1-a[3]))/alpha),alpha] : [0,0,0,0];};
export function surfaceContrast(a,b) {
  const lum=c=>c.slice(0,3).map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((s,x,i)=>s+x*[.2126,.7152,.0722][i],0);
  const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
}
export function paintSurfaceOutline(node) {
  if(!node.getClientRects().length || node.closest('[hidden]'))return;
  const r=node.getBoundingClientRect(),x=Math.max(0,Math.min(innerWidth-1,r.left+r.width/2)),y=Math.max(0,Math.min(innerHeight-1,r.top+r.height/2));
  const stack=document.elementsFromPoint(x,y).filter(n=>n!==node&&!node.contains(n));
  let back=[255,255,255,1];
  for(const n of stack.reverse())back=over(rgba(getComputedStyle(n).backgroundColor),back);
  const fill=rgba(getComputedStyle(node).backgroundColor);
  const needs=fill[3]>.1 && surfaceContrast(over(fill,back),back)<1.5;
  if(node.classList.contains('aj-surface-outline')!==needs)node.classList.toggle('aj-surface-outline',needs);
}
export function initSurfaceOutlines() {
  let queued=false;
  const update=()=>{queued=false;for(const n of document.querySelectorAll('.toast.on,.sheet:not([hidden]),.ajmodal:not(.setpage):not([hidden]) > .box'))paintSurfaceOutline(n);};
  const queue=()=>{if(!queued){queued=true;requestAnimationFrame(update);}};
  new MutationObserver(queue).observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class','hidden','style','data-view','data-theme']});
  new MutationObserver(queue).observe(document.documentElement,{attributes:true,attributeFilter:['class','style','data-theme']});
  addEventListener('resize',queue);queue();
}
