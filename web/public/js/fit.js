// P302 (owner: 「手机上用 @ 弹出文档选择框，然后顶部被盖住了……没有计算到顶部的时间、电池、信号」): a list that grows upward from
// the composer never passes the top of what is actually visible — the visual viewport (the soft keyboard shrinks it and may
// scroll it) minus the status-bar safe area. Used by the @ list and the / list.
let probe=null;
export function safeTop(){
 if(!probe){probe=document.createElement('div');probe.style.cssText='position:fixed;top:0;left:0;width:0;height:0;visibility:hidden;pointer-events:none;padding-top:env(safe-area-inset-top,0px)';document.body.append(probe);}
 return parseFloat(getComputedStyle(probe).paddingTop)||0;
}
// the top edge (in the same coordinates as getBoundingClientRect) a list may reach
export function visibleTop(gap=8){const vv=window.visualViewport;return (vv?Math.max(0,vv.offsetTop):0)+safeTop()+gap;}
// P302 追加 28 (owner 10-15: 「所有弹窗……最高限度就是 message card 现在的这个顶部……不会和 weekly 百分比重叠」): the one ceiling
// every sheet that rises from the bottom stops at — the top of the message card, i.e. under the WEEKLY rule and its label.
// DESIGN_TOKENS.md「Sheet ceiling」. Off the chat page (no WEEKLY rule on screen) it is just the visible top.
export function sheetCeil(){
 let y=visibleTop(0);const w=document.getElementById('mWeek'),lab=w&&w.querySelector('.lab'),d=document.getElementById('deck');
 for(const e of [w,lab])if(e&&e.getClientRects().length&&e.getBoundingClientRect().height>0)y=Math.max(y,e.getBoundingClientRect().bottom+(e===lab?4:0));
 // the message card's top = the deck's content top (it is there with or without a quota line on screen)
 if(d&&d.getClientRects().length)y=Math.max(y,d.getBoundingClientRect().top+(parseFloat(getComputedStyle(d).paddingTop)||0));
 return Math.round(y);
}
// publishes the ceiling as CSS: --ui-sheet-ceil (viewport px) and, for the question / approval sheet that lives inside the
// stage, --ui-sheet-stage-top (px from the stage's top)
export function syncCeil(){
 const y=sheetCeil(),root=document.documentElement.style,st=document.getElementById('stage');
 root.setProperty('--ui-sheet-ceil',y+'px');
 if(st&&st.getClientRects().length)root.setProperty('--ui-sheet-stage-top',Math.max(0,y-st.getBoundingClientRect().top)+'px');
 return y;
}
let ceilWired=false;
export function wireCeil(){
 if(ceilWired)return;ceilWired=true;let f=0;const go=()=>{if(!f)f=requestAnimationFrame(()=>{f=0;syncCeil();});};
 addEventListener('resize',go);window.visualViewport?.addEventListener('resize',go);
 new MutationObserver(go).observe(document.body,{attributes:true,attributeFilter:['data-sheet','data-view','style','class']});
 const st=document.getElementById('stage');if(st&&window.ResizeObserver)new ResizeObserver(go).observe(st);
 go();
}
// el = a position:fixed list; it ends `gap` px above anchorTop and is at most `cap` px tall
export function fitAbove(el,anchorTop,cap=300,gap=8){
 const top=sheetCeil(),room=Math.max(96,anchorTop-gap-top);el.style.maxHeight=Math.min(cap,room)+'px';el.style.bottom='auto';
 el.style.top=Math.max(top,anchorTop-gap-el.offsetHeight)+'px';
}
// el = an absolutely placed list that already grows upward from its parent: only cap its height
export function capAbove(el,gap=6){const r=el.parentElement.getBoundingClientRect();el.style.maxHeight='';const css=parseFloat(getComputedStyle(el).maxHeight)||Infinity;
 el.style.maxHeight=Math.max(96,Math.min(css,r.top-gap-sheetCeil()))+'px';}
