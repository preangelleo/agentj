// Runs inside Chromium. Measures rendered geometry, never CSS-source strings.
export function measurePage({scene, baselineHeader=null, baselineRows=[], pairs=[], groups=[]}={}) {
  const visible=n=>!!n&&!n.closest('[hidden]')&&getComputedStyle(n).visibility!=='hidden'&&getComputedStyle(n).opacity!=='0'&&n.getBoundingClientRect().width>0&&n.getBoundingClientRect().height>0;
  const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
  const id=n=>n.id?'#'+n.id:n.tagName.toLowerCase()+'.'+[...n.classList].join('.');
  const checks=[],measurements=[];
  const add=(gate,name,pass,detail)=>checks.push({gate,name,pass,detail});
  const distance=(a,b)=>{const x=Math.max(a.x-b.right,b.x-a.right,0),y=Math.max(a.y-b.bottom,b.y-a.bottom,0);return Math.hypot(x,y);};
  function gap(a,b,min,why){const A=rect(a),B=rect(b),value=distance(A,B);measurements.push({kind:'gap',a:id(a),b:id(b),value,min,why,A,B});add(1,id(a)+' → '+id(b),value+.6>=min,{value,min,why});}
  for(const [a,b,min=8] of pairs){const A=document.querySelector(a),B=document.querySelector(b);if(visible(A)&&visible(B))gap(A,B,min,'explicit semantic text/card/control boundary');else add(1,`${a} → ${b}`,false,{missingOrHidden:true});}
  for(const [selector,min=8]of groups){if(!document.querySelector(selector))add(1,selector,false,{missingGroup:true});for(const group of document.querySelectorAll(selector)){if(!visible(group))continue;const nodes=[...group.children].filter(n=>visible(n)&&getComputedStyle(n).position!=='absolute'&&getComputedStyle(n).position!=='fixed'&&n.textContent.trim());for(let i=1;i<nodes.length;i++)gap(nodes[i-1],nodes[i],min,'adjacent semantic blocks in '+selector);}}
  // These shared components are icon-only; menu navigation rows and labelled CTAs are not icon toolbars.
  const icons=[...document.querySelectorAll('.top button,.rmbar button,.rdacts button,.rdfhd .act,.tools .tool,.codeblock .bar .runbtn,.codeblock .bar .copy,.omcopy,.cmdcopy')].filter(visible);
  for(const n of icons){const walker=document.createTreeWalker(n,NodeFilter.SHOW_TEXT);const labels=[];while(walker.nextNode()){const t=walker.currentNode;if(!t.textContent.trim()||t.parentElement.closest('svg,[aria-hidden="true"],.vh,.sr-only'))continue;const range=document.createRange();range.selectNodeContents(t);if([...range.getClientRects()].some(r=>r.width>1&&r.height>1))labels.push(t.textContent.trim());}add(2,id(n),labels.length===0,{labels});}
  const header=[...document.querySelectorAll('.top button')].filter(visible).map(n=>({id:n.id,svg:[...n.querySelectorAll('svg')].map(s=>s.innerHTML.replace(/\s+/g,' ')).join('')}));
  if(baselineHeader){for(const n of [...document.querySelectorAll('.top button')].filter(visible)){const r=rect(n);add(5,id(n)+' remains inside header viewport',r.x>=-.5&&r.right<=innerWidth+.5,{r,viewport:innerWidth});}}
  if(baselineHeader)add(5,'header icons equal formal 0.28.4',JSON.stringify(header)===JSON.stringify(baselineHeader),{actual:header,baseline:baselineHeader});
  const top=document.querySelector('.top'),week=document.querySelector('#mWeek'),rows=[];
  if(visible(top)&&visible(week)){const H=rect(top),W=rect(week);for(const n of document.querySelector('#chat-view').children){if(n===week)break;if(visible(n)){const R=rect(n);if(R.height>=8&&R.width>=innerWidth*.5&&R.bottom>H.bottom+.5&&R.y<W.y+.5)rows.push(id(n));}}add(3,'no new row between header and main quota',rows.every(x=>baselineRows.includes(x)),{rows,baselineRows,header:H,quota:W});}
  const paint=n=>{for(let p=n;p;p=p.parentElement){const c=getComputedStyle(p).backgroundColor;if(c!=='rgba(0, 0, 0, 0)'&&c!=='transparent')return c;}return 'transparent';};
  const color=token=>{const n=document.createElement('i');n.style.color=`var(${token})`;document.body.append(n);const c=getComputedStyle(n).color;n.remove();return c;};
  const surface=(selector,system)=>{const n=document.querySelector(selector);if(!visible(n)){add(4,selector,false,{missingOrHidden:true});return;}const R=rect(n),bg=paint(n),fg=getComputedStyle(n).color,expected=color(system==='grown'?'--pg':'--c-noir');measurements.push({kind:'surface',selector,system,R,bg,fg,expected});if(system==='grown'){add(4,selector+' full viewport',Math.abs(R.x)<=1&&Math.abs(R.width-innerWidth)<=1,{R,viewport:innerWidth});add(4,selector+' state background',bg===expected,{bg,expected});const meter=document.querySelector('#m5h');if(meter&&visible(meter))add(4,selector+' grows above 5h quota',Math.abs(R.bottom-rect(meter).y)<=2,{bottom:R.bottom,quota:rect(meter)});}else{add(4,selector+' Noir',bg===expected,{bg,expected});const rgb=fg.match(/[\d.]+/g)?.map(Number)||[];add(4,selector+' light ink',rgb.length>=3&&Math.min(...rgb.slice(0,3))>=200,{fg});}}
  if(scene==='toast'){const n=[...document.querySelectorAll('.toast')].find(visible),text=n?.querySelector('.nt-body');add(1,'toast present and not clipped',!!n&&!!text&&n.scrollHeight<=n.clientHeight+1&&text.scrollWidth<=text.clientWidth+1,{present:!!n,height:n?.clientHeight,scrollHeight:n?.scrollHeight,textWidth:text?.clientWidth,textScrollWidth:text?.scrollWidth});}
  if(scene==='approval')surface('#sheet','grown');
  if(scene==='faceid')surface('#pkp>.box','grown');
  if(scene==='confirm')surface('#confirm>.box','centered');
  if(scene==='download')surface('#private-file-locator>.sq','centered');
  if(scene==='busy')surface('#busyDlg>.sq','centered');
  return {scene,checks,measurements,header,rows,iconCount:icons.length};
}
