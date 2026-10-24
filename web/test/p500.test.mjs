// Installation contract, not an emulation of SpringBoard's compositor.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const read=p=>readFileSync(new URL('../public/'+p,import.meta.url),'utf8');
function boot(ios,dark,standalone=false) {
 const link={href:'manifest.webmanifest',setAttribute(k,v){this[k]=v;}};
 let change;
 const scheme={matches:dark,addEventListener(k,f){change=f;}};
 const root={dataset:{},style:{}};
 const context={navigator:{userAgent:ios?'iPhone':'Android',platform:ios?'iPhone':'Linux',maxTouchPoints:5,standalone},document:{documentElement:root,querySelector:()=>link,querySelectorAll:()=>[]},matchMedia:q=>q.includes('prefers-color')?scheme:{matches:standalone}};
 context.window=context;vm.runInNewContext(read('js/boot.js'),context);
 const first=link.href;scheme.matches=!dark;change?.();return {first,changed:link.href};
}
for(const standalone of [false,true])for(const dark of [false,true])test(`P500 iOS ${standalone?'installed':'adding'} ${dark?'dark':'light'} keeps legacy manifest across scheme changes`,()=>{
 assert.deepEqual(boot(true,dark,standalone),{first:'manifest.webmanifest',changed:'manifest.webmanifest'});
});
for(const dark of [false,true])test(`P500 Android theme-specific splash retained ${dark}`,()=>{
 assert.deepEqual(boot(false,dark),{first:`manifest-${dark?'dark':'light'}.webmanifest`,changed:`manifest-${dark?'light':'dark'}.webmanifest`});
});
test('P500 static install status style matches pre-P393 default; runtime boot never rewrites it',()=>{
 assert.equal(read('index.html').match(/<meta name="apple-mobile-web-app-status-bar-style" content="([^"]+)"/)?.[1],'default');
 assert.doesNotMatch(read('js/boot.js'),/apple-mobile-web-app-status-bar-style/);
});
test('P500 legacy manifest retains identity, standalone, scope and dark backing',()=>{
 const m=JSON.parse(read('manifest.webmanifest'));
 assert.equal(m.display,'standalone');assert.equal(m.start_url,'./');assert.equal(m.scope,'./');
 assert.equal(m.background_color,'#141414');assert.equal(m.theme_color,'#141414');
 assert.match(read('index.html'),/viewport-fit=cover/);
});
