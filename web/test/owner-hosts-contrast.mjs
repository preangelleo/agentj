// Run inside the real browser. Resolve CSS colors through its sRGB canvas, then composite alpha backgrounds.
export function renderedContrast(selector){
 const node=document.querySelector(selector),canvas=document.createElement('canvas');canvas.width=canvas.height=1;const ctx=canvas.getContext('2d');
 const rgba=value=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=value;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].map((v,i)=>i===3?v/255:v);};
 const over=(front,back)=>{const alpha=front[3]+back[3]*(1-front[3]);return [...front.slice(0,3).map((v,i)=>alpha?(v*front[3]+back[i]*back[3]*(1-front[3]))/alpha:0),alpha];};
 const chain=[];for(let n=node;n;n=n.parentElement){const css=getComputedStyle(n);chain.push({background:css.backgroundColor,opacity:css.opacity});}
 const css=getComputedStyle(node);let background=[0,0,0,0],foreground=rgba(css.color);
 for(const item of chain){const bg=rgba(item.background);background=over(background,bg);foreground=over(foreground,bg);background[3]*=Number(item.opacity);foreground[3]*=Number(item.opacity);}
 background=over(background,[255,255,255,1]).slice(0,3);foreground=over(foreground,[255,255,255,1]).slice(0,3);
 const luminance=rgb=>rgb.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);
 const f=luminance(foreground),b=luminance(background);
 return{foregroundCSS:css.color,backgroundCSS:css.backgroundColor,foreground,background,ratio:(Math.max(f,b)+.05)/(Math.min(f,b)+.05),fontSize:css.fontSize,chain,viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio}};
}
