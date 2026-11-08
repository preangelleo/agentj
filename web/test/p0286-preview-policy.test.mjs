import {test} from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import {checkedPreviewRemainder} from './inert-preview-policy.mjs';
const path='js/preview-document.js',source=readFileSync(new URL('../public/'+path,import.meta.url),'utf8');
test('0286 inert parser is the single exact registered markup boundary; remainder stays subject to the global ban',()=>{
 const rest=checkedPreviewRemainder(path,source);assert.doesNotMatch(rest,/innerHTML|outerHTML|insertAdjacentHTML/);
 for(const evil of [source+'\ndocument.body.innerHTML = raw;',source.replace('clean(template.content);',''),source.replace('clean(template.content);','return html;'),source.replace('template.innerHTML = String(html || \'\');','template.innerHTML = String(html || \'\'); template.innerHTML = raw;')])assert.throws(()=>checkedPreviewRemainder(path,evil));
 assert.match(checkedPreviewRemainder('js/another.js','document.body.innerHTML = raw'),/innerHTML/,'an unrelated module is never exempted');
});
