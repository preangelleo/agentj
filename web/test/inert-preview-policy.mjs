// Exact reviewed parser boundary. No file-wide markup exception.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
const policy=JSON.parse(readFileSync(new URL('../technical-policy.json',import.meta.url),'utf8')).inertMarkup;
export function checkedPreviewRemainder(path, source){
 if(path!==policy.file)return source;
 const marker='export function previewDocument(html) {';
 const start=source.indexOf(marker);assert.ok(start>=0,'reviewed inert parser exists');
 const body=source.slice(start);
 assert.equal(createHash('sha256').update(body).digest('hex'),policy.functionSha256,'inert parser exact reviewed body');
 assert.match(body,/const template = document\.createElement\('template'\)/);
 assert.match(body,/clean\(template\.content\);\n  return '<!doctype html><meta http-equiv="Content-Security-Policy"/);
 assert.equal((body.match(/innerHTML/g)||[]).length,2,'one inert parse and one cleaned serialization');
 assert.ok(body.indexOf('clean(template.content)')<body.lastIndexOf('template.innerHTML'));
 return source.slice(0,start);
}
