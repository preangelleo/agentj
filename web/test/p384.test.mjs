import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const boot=readFileSync(new URL('../public/js/boot.js',import.meta.url),'utf8');
function launch(userAgent,platform,touch,standalone=false,displayMode=false){
 const metas=['#faf9f5','#141414'].map(content=>({content,setAttribute(k,v){this[k]=v;}}));
 const apple={content:'default',setAttribute(k,v){this[k]=v;}};
 const root={dataset:{},style:{}};
 const ctx={navigator:{userAgent,platform,maxTouchPoints:touch,standalone},document:{documentElement:root,querySelectorAll:()=>metas,querySelector:()=>apple},localStorage:{getItem:()=> '#629887'},matchMedia:()=>({matches:displayMode})};ctx.window=ctx;
 vm.runInNewContext(boot,ctx);return {colors:metas.map(m=>m.content),apple:apple.content,ios:root.dataset.ios,bg:root.style.backgroundColor};
}
for(const [name,ua,platform,touch] of [['iPhone','iPhone','iPhone',5],['iPad','iPad','iPad',5],['iPod','iPod','iPod',5],['iPad desktop','Macintosh','MacIntel',5]])for(const standalone of [false,true])test(`${name} ${standalone?'PWA':'Safari'} first paint ignores stale state color`,()=>{
 const r=launch(ua,platform,touch,standalone);assert.deepEqual(r,{colors:['#141414','#141414'],apple:'black-translucent',ios:'1',bg:'#141414'});
});
for(const [name,ua,platform,touch] of [['Android','Android','Linux armv8l',5],['Mac','Macintosh','MacIntel',0],['one-pointer Mac','Macintosh','MacIntel',1]])test(`${name} retains browser color policy`,()=>{
 const r=launch(ua,platform,touch);assert.equal(r.ios,undefined);assert.deepEqual(r.colors,['#faf9f5','#141414']);assert.equal(r.apple,'default');
});
test('Android installed splash remains Sooty',()=>assert.deepEqual(launch('Android','Linux',5,false,true).colors,['#141414','#141414']));
test('boot follows the Apple meta before CSS so its first-frame override exists',()=>{
 const html=readFileSync(new URL('../public/index.html',import.meta.url),'utf8');assert.ok(html.indexOf('apple-mobile-web-app-status-bar-style')<html.indexOf('src="js/boot.js"'));assert.ok(html.indexOf('src="js/boot.js"')<html.indexOf('href="app.css"'));
});
