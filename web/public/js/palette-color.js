// P420: user-generated colors are explicitly exempt from the closed UI palette (#178).
export const valid = v => typeof v === 'string' && /^#[0-9a-f]{6}$/i.test(v);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const linear = v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
const rgb = hex => hex.slice(1).match(/../g).map(v => linear(parseInt(v, 16) / 255));
export function contrast(hex, ink) {
  const lum = h => {const [r,g,b] = rgb(h); return .2126*r + .7152*g + .0722*b;};
  const a=lum(hex), b=lum(ink); return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
}
export function oklch(hex) {
  const [r,g,b] = rgb(hex);
  const l=Math.cbrt(.4122214708*r+.5363325363*g+.0514459929*b);
  const m=Math.cbrt(.2119034982*r+.6806995451*g+.1073969566*b);
  const s=Math.cbrt(.0883024619*r+.2817188376*g+.6299787005*b);
  const a=1.9779984951*l-2.428592205*m+.4505937099*s;
  const b2=.0259040371*l+.7827717662*m-.808675766*s;
  return {l:.2104542553*l+.793617785*m-.0040720468*s, c:Math.hypot(a,b2), h:(Math.atan2(b2,a)*180/Math.PI+360)%360};
}
// Reduce chroma until in sRGB gamut, keeping lightness/hue; no channel clipping hue shifts.
export function fromOklch(l,c,h) {
  const channels = chroma => {
    const a=chroma*Math.cos(h*Math.PI/180), b=chroma*Math.sin(h*Math.PI/180);
    const x=(l+.3963377774*a+.2158037573*b)**3;
    const y=(l-.1055613458*a-.0638541728*b)**3;
    const z=(l-.0894841775*a-1.291485548*b)**3;
    return [4.0767416621*x-3.3077115913*y+.2309699292*z,-1.2684380046*x+2.6097574011*y-.3413193965*z,-.0041960863*x-.7034186147*y+1.707614701*z];
  };
  l=clamp(l,0,1);c=clamp(c,0,.4);
  let v=channels(c);
  if(v.some(n=>n < -1e-7 || n > 1.0000001)) {
    let lo=0,hi=c;
    for(let i=0;i<22;i++) {
      const mid=(lo+hi)/2;
      if(channels(mid).every(n=>n>=0 && n<=1))lo=mid;else hi=mid;
    }
    v=channels(lo);
  }
  return '#'+v.map(n=>{n=clamp(n,0,1);return Math.round(255*(n<=.0031308?12.92*n:1.055*n**(1/2.4)-.055)).toString(16).padStart(2,'0');}).join('');
}
export const DIRECTIONS = ['analogous','complementary','triadic','split','mono'];
export function generate(seed, direction='analogous', count=5, hue=0, saturation=100, lightness=0) {
  if(!valid(seed)) return [];
  const base=oklch(seed), n=[5,8,10].includes(count)?count:5;
  if(!DIRECTIONS.includes(direction))direction='analogous';
  hue=Number.isFinite(hue)?clamp(hue,-180,180):0;
  saturation=Number.isFinite(saturation)?clamp(saturation,0,200):100;
  lightness=Number.isFinite(lightness)?clamp(lightness,-40,40):0;
  const offsets={complementary:[0,180],triadic:[0,120,240],split:[0,150,210]};
  // Move the whole ladder at the gamut ends instead of clipping several steps to one color.
  const span=direction==='mono'?.6:.16;
  const center=clamp(base.l+lightness/100,.08+span/2,.97-span/2);
  return Array.from({length:n},(_,i)=>{
    const step=i/(n-1)-.5;
    const offset=direction==='mono'?0:direction==='analogous'?step*80:offsets[direction][i%offsets[direction].length];
    const l=center+step*span;
    return fromOklch(l,base.c*saturation/100,base.h+hue+offset);
  });
}
