// P489: fixed public measurements only. Never serialize DOM datasets, UA, URLs or host objects wholesale.
const num = v => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= 1e7 ? Math.round(v * 1000) / 1000 : null;
const choice = (v, values) => values.includes(v) ? v : 'unknown';
const color = v => typeof v === 'string' && /^(?:#[0-9a-f]{6}|rgba?\([0-9.,% /+-]+\)|color\((?:srgb|display-p3) [0-9.e% /+-]+\))$/i.test(v) && v.length <= 128 ? v : null;
const transform = v => v === 'none' || /^matrix(?:3d)?\([0-9., e+-]+\)$/i.test(v || '') ? v : null;
const flag = v => v === '1' || v === 'true';
export function capture({build, meter, look} = {}) {
 const h = document.documentElement, b = document.body, w = document.getElementById('water');
 const layer = e => { if (!e) return null; const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {
  rect:Object.fromEntries(['x','y','width','height'].map(k=>[k,num(r[k])])),background:color(s.backgroundColor),
  display:choice(s.display,['none','block','flex','grid','inline','inline-block','contents']),opacity:num(Number(s.opacity)),transform:transform(s.transform)}; };
 const ua=navigator.userAgent, vv=window.visualViewport;
 const pct=k=>{
  if(k==='ctx' && w?.dataset.lvl) return num(Number(w.dataset.lvl));
  const bar=document.getElementById(k==='h5'?'m5h':k==='week'?'mWeek':'water');
  if(k!=='ctx' && bar?.firstElementChild) return bar.dataset.known==='1'?num(parseFloat(bar.firstElementChild.style.width)):null;
  return num(meter?.[k]?.pct);
 };
 return {v:1,at:Date.now(),ios:h.dataset.ios==='1',standalone:h.dataset.standalone==='1',
  ua:/CriOS/.test(ua)?'chrome':/FxiOS/.test(ua)?'firefox':/EdgiOS/.test(ua)?'edge':/iPhone.*Version\/.+Safari\//.test(ua)?'safari':'other',
  theme:h.dataset.theme==='light'||h.dataset.theme==='dark'?h.dataset.theme:'system',status:choice(b.dataset.status,['idle','working','waiting','question','offline']),
  conn:choice(b.dataset.conn,['on','off','up','down','quiet','connecting']),kb:flag(b.dataset.kb),known:flag(w?.dataset.known),compacting:flag(w?.dataset.compacting),
  level:num(parseFloat(w?.style.getPropertyValue('--lvl'))),bottom:color(getComputedStyle(h).getPropertyValue('--ios-bottom').trim()),
  innerHeight:num(window.innerHeight),clientHeight:num(h.clientHeight),vv:vv?{height:num(vv.height),offsetTop:num(vv.offsetTop),scale:num(vv.scale)}:null,
  layers:Object.fromEntries([['water',w],['body',b],['root',h],['band',document.getElementById('sabband')],['probe',document.getElementById('chromeProbe')]].map(([k,e])=>[k,layer(e)])),
  meter:{ctx:pct('ctx'),h5:pct('h5'),week:pct('week')},look:{factory:choice(look?.factory,['robots','dots']),custom:!!look?.id&&!['robots','dots'].includes(look.id)},build};
}
