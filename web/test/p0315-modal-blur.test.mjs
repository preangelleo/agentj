import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8');
const layout=readFileSync(new URL('../public/js/popup-layout.js',import.meta.url),'utf8');

test('centred interactive dialogs use one full-viewport veil at microphone blur strength',()=>{
 const rule=css.match(/\/\* 0315: a centred interactive card[\s\S]*?(?=#ctFwd \.sq\.ct-picker-page)/)?.[0];
 assert.ok(rule,'shared centred-dialog veil exists');
 assert.match(rule,/\.aj-shared-popup\[data-popup-system="center"\]:not\(\[hidden\]\)::before\s*\{[\s\S]*?position:fixed;inset:0/);
 assert.match(rule,/background:color-mix\(in srgb,var\(--popup-veil-color,var\(--pg\)\) 58%,transparent\)/);
 assert.match(rule,/backdrop-filter:blur\(18px\)/);
 assert.match(rule,/\.satband,\.sabband/);
 assert.match(rule,/dialog\.aj-shared-popup\[data-popup-system="center"\]:modal::backdrop/);
 assert.match(layout,/getComputedStyle\(document\.body\)\.getPropertyValue\('--pg'\)/);
 assert.match(layout,/root\.style\.setProperty\('--popup-veil-color',pageColor\)/);
});
