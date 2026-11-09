// Presentation only: approval/biometric cards grow from the quota boundary; other dialogs stay Noir and centered.
let lastFrame=null,lastWidth=0;
const nativeGrowTokens=new WeakMap();
const $=id=>document.getElementById(id);
const visible=n=>n&&!n.hidden&&(n.id==='sheet'?document.body.dataset.sheet==='1':n.tagName==='DIALOG'?n.open:n.getClientRects().length>0&&getComputedStyle(n).visibility!=='hidden');
export function layoutPopups(roots) {
 const vv=globalThis.visualViewport,edgeTop=vv?.offsetTop||0,edgeBottom=edgeTop+(vv?.height||innerHeight),s=$('stage')?.getBoundingClientRect(),r=$('rm')?.getBoundingClientRect(),c=document.querySelector('.composer')?.getBoundingClientRect();
 if(r?.width>1)lastWidth=r.width;
 if(s?.width>1&&s.height>1)lastFrame={top:s.top,left:s.left,width:s.width,bottom:s.bottom};
 const frame=lastFrame||{top:Math.max(edgeTop,document.querySelector('.top')?.getBoundingClientRect().bottom||60),left:0,width:innerWidth,bottom:edgeBottom-16};
 const top=Math.max(edgeTop,frame.top),bottom=Math.min(edgeBottom,frame.bottom,c?.height>1&&c.top>top?c.top:edgeBottom),height=Math.max(44,bottom-top),width=Math.min(lastWidth||frame.width-32,frame.width);
 const settings=$('settings')&&!$('settings').hidden;
 const quotaRect=$('m5h')?.getBoundingClientRect(),bodyRect=document.body.getBoundingClientRect(),water=$('water');
 if(water&&quotaRect?.height>0){const floor=Math.max(0,bodyRect.bottom-quotaRect.top)+'px';if(water.style.getPropertyValue('--water-floor')!==floor)water.style.setProperty('--water-floor',floor);}
 for(const root of roots){
  if(!visible(root))continue;
  const settingPasskey=settings&&root.matches('.ajmodal.pkp,#confirm,#sheet.auth-approval');
  const system=settingPasskey?'center':root.matches('#sheet,.approval-special541,.ajmodal.pkp,#elev,.ajmodal.sout:not(.sout-full),.ajmodal.lqr')||root.dataset.popupKind==='grow'?'grow':'center';
  if(system==='grow'){const wet='color-mix(in srgb,var(--sooty) 13%,'+getComputedStyle(document.body).backgroundColor+')';if(root.style.getPropertyValue('--popup-water')!==wet)root.style.setProperty('--popup-water',wet);}
  if(root.dataset.popupSystem!==system)root.dataset.popupSystem=system;
  // A moved into a Noir native B dialog still belongs to the current page state.
  if(system==='grow'&&root.closest('dialog:modal')){
   if(!nativeGrowTokens.has(root))nativeGrowTokens.set(root,['--pg','--ink'].map(k=>[k,root.style.getPropertyValue(k)]));
   const page=getComputedStyle(document.body);for(const[k,value]of [['--pg',page.backgroundColor],['--ink',page.color]])if(root.style.getPropertyValue(k)!==value)root.style.setProperty(k,value);
  }else if(nativeGrowTokens.has(root)){for(const[k,value]of nativeGrowTokens.get(root))value?root.style.setProperty(k,value):root.style.removeProperty(k);nativeGrowTokens.delete(root);}
  const quota=$('m5h')?.getBoundingClientRect(),growBottom=quota?.height>0?Math.min(edgeBottom,quota.top):bottom,cardHeight=system==='grow'?Math.max(44,growBottom-top):height;
  // Settings covers the viewport, independently of the chat stage's reading floor.
  const settingCenter=settings&&system==='center',viewLeft=vv?.offsetLeft||0,viewWidth=vv?.width||innerWidth;
  const geometry=settingCenter?{top:edgeTop,left:viewLeft,width:viewWidth,height:edgeBottom-edgeTop,cardWidth:Math.min(30*parseFloat(getComputedStyle(document.documentElement).fontSize),viewWidth-32),center:(edgeTop+edgeBottom)/2}:{top,left:system==='grow'?0:frame.left,width:system==='grow'?innerWidth:frame.width,height:cardHeight,cardWidth:system==='grow'?innerWidth:width,cardLeft:system==='grow'?0:frame.left+(frame.width-width)/2,center:top+height/2};
  if(settingCenter)geometry.cardLeft=viewLeft+(viewWidth-geometry.cardWidth)/2;
  for(const[k,v]of Object.entries(geometry)){const prop='--popup-'+k.replace(/[A-Z]/g,m=>'-'+m.toLowerCase()),value=v+'px';if(root.style.getPropertyValue(prop)!==value)root.style.setProperty(prop,value);}
  // ontop's fixed approval stays in the browser top layer, but must not inherit
  // the centered native dialog's transformed containing block or overflow clip.
  if(root.matches('dialog:modal')&&root.querySelector(':scope>.ontop-float')){
   const value=root.getBoundingClientRect().height+'px';if(root.style.getPropertyValue('--popup-native-height')!==value)root.style.setProperty('--popup-native-height',value);
  }
  if(!root.classList.contains('aj-shared-popup'))root.classList.add('aj-shared-popup');
  const context=settings?'settings':'chat';if(root.dataset.popupContext!==context)root.dataset.popupContext=context;
 }
}
