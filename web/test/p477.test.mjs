import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {demo,sections} from './p477-fixture.mjs';
test('P477 covers eleven formats with the original synthetic demonstration',()=>{assert.equal(sections.length,11);assert.match(demo,/flowchart LR/);assert.match(demo,/"kind":"line"/)});
test('P477 CSS only targets reply body text; never recolours shape containers',()=>{
 const css=readFileSync('web/public/app.css','utf8').split('/* P477:')[1]?.split(/\n\n\/\* P\d+/)[0].replace(/^[\s\S]*?\*\//,'').replace(/\/\*[\s\S]*?\*\//g,'');assert.ok(css);
 const rules=[...css.matchAll(/([^{}]+)\{([^{}]+)\}/g)];assert.equal(rules.length,5);
 for(const [,selector,body]of rules){assert.ok(selector.trim().startsWith('.rm #words'));assert.doesNotMatch(body,/background|border|stroke|--[a-z]+:/)}
 assert.match(css,/svg text\[text-anchor\]\{fill:var\(--lotion\)/);assert.match(css,/li::marker/);assert.match(css,/text-decoration-line:underline/);
});
test('P477 Mermaid colour and cache are local to reply body; security config is retained',()=>{
 const src=readFileSync('web/public/js/render.js','utf8');
 assert.match(src,/wrap.closest\('\.rm #words'\)/);assert.match(src,/replyBody \? ':reply' : ''/);assert.match(src,/if \(replyBody\) s = s.replace/);assert.match(src,/text,#\$\{id\} tspan/);assert.match(src,/securityLevel: 'strict', htmlLabels: false/);
});
