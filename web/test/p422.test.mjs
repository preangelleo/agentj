import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {readPng} from './p364-splash.test.mjs';
const read=p=>readFileSync(new URL('../public/'+p,import.meta.url));
const html=read('index.html').toString();
test('P422 both Android schemes use transparent any and separate safe-zone maskable icons',()=>{
 for(const scheme of ['light','dark']){
  const m=JSON.parse(read('manifest-'+scheme+'.webmanifest'));
  assert.equal(m.theme_color,'#141414');assert.equal(m.background_color,scheme==='dark'?'#141414':'#faf9f5');
  for(const i of m.icons){
   const [path,q]=i.src.split('?'),bytes=read(path),im=readPng(bytes);assert.equal(q,'v='+createHash('sha256').update(bytes).digest('hex').slice(0,8));
   for(const [x,y] of [[0,0],[im.w-1,0],[0,im.h-1],[im.w-1,im.h-1]])assert.deepEqual(im.px(x,y),i.purpose==='any'?[0,0,0,0]:[...m.background_color.slice(1).match(/../g).map(h=>parseInt(h,16)),255]);
   if(i.purpose==='maskable')for(let y=0;y<im.h;y++)for(let x=0;x<im.w;x++){
    if(im.px(x,y).slice(0,3).join()===im.px(0,0).slice(0,3).join())continue;
    assert.ok(Math.hypot(x-im.w/2,y-im.h/2)<=im.w*.4,'all meaningful pixels inside the maskable safe circle');
   }
  }
 }
});
test('P422 exact iPhone Air and other iOS startup links have both orientations and schemes, content hashes',()=>{
 const screens=JSON.parse(read('startup/screens.json'));assert.ok(screens.length>=8);
 for(const s of screens){const bytes=read('startup/'+s.file);assert.equal(bytes.readUInt32BE(16),s.width);assert.equal(bytes.readUInt32BE(20),s.height);
  const hash=createHash('sha256').update(bytes).digest('hex').slice(0,8);assert.ok(html.includes(`href="startup/${s.file}?v=${hash}" media="${s.media}"`));}
 for(const scheme of ['light','dark']){const b=read(`startup/420x912-3-portrait-${scheme}.png`);assert.equal(b.readUInt32BE(16),1260);assert.equal(b.readUInt32BE(20),2736);}
});
test('P422 non-iOS keeps Sooty; P459m iOS delegates fixed Noir document chrome',()=>{
 assert.equal([...html.matchAll(/<meta name="theme-color" content="([^"]+)"/g)].filter(m=>m[1]!=='#2c2b26').length,0);
 const r=read('js/relay.js').toString();assert.match(r,/function chromeTarget\(\)\{\s*return "#141414"/);
 const iosBranch=r.match(/if \(document\.documentElement\.dataset\.ios === "1"\) \{([\s\S]*?)\n  \}/)?.[1];
 assert.ok(iosBranch,'iOS chrome uses its own branch');
 assert.match(iosBranch,/const color = paintIOSChrome\(\)/,'iOS delegates page chrome colour');
 assert.match(iosBranch,/if \(color\) setThemeColor\(color\)/,'Safari, CriOS and standalone share fixed Noir metadata');
 assert.match(iosBranch,/return;/,'iOS never falls through to the non-iOS Sooty theme update');
 assert.doesNotMatch(read('js/boot.js').toString(),/localStorage/);
 assert.match(read('app.css').toString(),/\.satband,\.sabband\{background:#141414!important/);
});
test('P422 pull and hold share draft-preserving refresh, no wheel or mouse gesture handler',()=>{
 const a=read('app.js').toString();assert.match(a,/initPullRefresh\(\$\('main'\), \{blocked:relay.refreshBlocked,refresh:guardedRefresh\}\)/);
 assert.match(a,/await relay.saveDraft\(\).*relay.refreshBlocked\(\)/);
 const p=read('js/pull-refresh.js').toString();assert.ok(p.includes('main.scrollTop>0'));assert.ok(p.includes('distance>=72'));assert.ok(p.includes("touchcancel"));
 assert.doesNotMatch(p,/addEventListener\(['"](?:wheel|mouse|pointer)/);
});
