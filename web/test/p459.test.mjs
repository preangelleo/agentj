import test from 'node:test';import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=p=>readFileSync(new URL('../public/'+p,import.meta.url),'utf8');
test('P459 document-owned chrome cannot feed back into its page-colour probe',()=>{
 const css=read('app.css'),js=read('js/ios-chrome.js');
 assert.match(css,/\.probe\{[^}]*background:var\(--pg\)/);
 assert.match(js,/getElementById\('water'\)/);assert.doesNotMatch(js,/setProperty\('--ios-chrome'/);
 assert.doesNotMatch(js,/setThemeColor|localStorage|fetch\(|\.backgroundColor\s*=/);
 assert.match(css,/html\[data-ios="1"\] \.satband\{min-height:0\}/);
});
test('P459 diagnostic is URL opt-in and only reports layout/colour metadata',()=>{
 const js=read('js/ios-chrome.js');assert.match(js,/get\('iosdiag'\) === '1'/);
 for(const key of ['--ios-chrome','theme-color','--ios-safe-top','--ios-safe-bottom'])assert.ok(js.includes(key));
 assert.doesNotMatch(js,/\.value|innerHTML|cookie|localStorage|password|fetch\(/i);
});

test('P459m all iOS modes share Noir chrome and browser composer geometry',()=>{
 const css=read('app.css'),js=read('js/ios-chrome.js'),boot=read('js/boot.js');
 assert.match(boot,/iPad\|iPhone\|iPod/);
 assert.doesNotMatch(boot,/CriOS/,'no browser-specific device fork');
 assert.match(boot,/ios \? "#2c2b26" : "#141414"/);
 assert.ok(css.includes('html[data-ios="1"]:not([data-standalone="1"]) .composer{padding-bottom:12px}'));
 assert.match(css,/html\[data-ios="1"\]\{--ios-chrome:var\(--c-noir\)/);
 assert.match(js,/html.dataset.iosSafari/); // P470 scopes root-canvas repair to Safari tabs.
 assert.ok(js.includes("document.body.dataset.fs !== '1' && document.body.dataset.reader !== '1'"));
 assert.match(js,/let bottom = noir/);
});
test('P459m safe-area paint cannot cover editor controls and observes actual water',()=>{
 const css=read('app.css'),js=read('js/ios-chrome.js');
 const band=css.match(/html\[data-ios="1"\] \.sabband\{([^}]+)\}/)?.[1];
 assert.ok(band);assert.match(band,/z-index:0/);assert.match(band,/height:var\(--ios-safe-bottom\)/);
 assert.ok(css.includes('html[data-ios="1"] .water{background:var(--water-color)}'));
 assert.match(js,/getComputedStyle\(water\).backgroundColor/);
 assert.match(js,/waterWatch.observe\(water/);
 assert.doesNotMatch(css,/\.satband::after/,'no extra seam extends into content');
 assert.ok(css.includes('html[data-ios="1"] .ptt{bottom:auto;top:var(--vvtop,0px);height:var(--vvh,100dvh)}'),'recording bottom follows the visual viewport above the keyboard');
});
