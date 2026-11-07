// #556: keep transient notices out of the composer. Runtime geometry lives in feedback556-notices.mjs.
import {readFileSync,readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {resolve,relative} from 'node:path';
const root=fileURLToPath(new URL('../../../',import.meta.url));
export function noticePlacementViolations(source,file='fixture.css') {
 const issues=[],plain=source.replace(/\/\*[\s\S]*?\*\//g,'');
 for(const match of plain.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
  const [,selector,body]=match;
  if(!/(?:toast|snackbar|notification|notice|\.msg\b)/i.test(selector))continue;
  // Any override can inherit fixed/absolute placement from a previous rule.
  if([...body.matchAll(/(?:^|;)\s*bottom\s*:\s*([^;}]+)/gi)].some(m => !/^(auto|initial|unset)\s*(?:!important)?$/i.test(m[1].trim())))issues.push(`${file}: bottom-anchored notice: ${selector.trim()}`);
  if(/(?:^|;)\s*inset-(?:block-end|block)\s*:\s*(?!auto\s*[;}])[^;}]+/i.test(body))issues.push(`${file}: logical bottom notice anchor`);
  for(const m of body.matchAll(/(?:^|;)\s*inset\s*:\s*([^;}]+)/gi)) {
   const values=m[1].trim().match(/(?:calc|var|env)\([^)]*\)|[^\s]+/g)||[];
   const top=values[0],bottom=values.length<3?values[0]:values[2];
   if(top==='auto'&&bottom!=='auto')issues.push(`${file}: shorthand bottom notice anchor`);
  }
  if(/transform\s*:[^;}]*translateY\(100%/i.test(body))issues.push(`${file}: notice enters from composer edge`);
 }
 if(/Toast\.makeText\s*\(|Snackbar\.make\s*\(/.test(source))issues.push(`${file}: native bottom transient bypasses shared notice`);
 return issues;
}
export function noticeSourceFiles() {
 const files=[];
 for(const dir of ['web/public','agentjarvis/dashboard/public','agentjarvis/site/static']) {
  const walk=d=>{for(const e of readdirSync(resolve(root,d),{withFileTypes:true})) {const p=d+'/'+e.name;if(e.isDirectory())walk(p);else if(/\.css$/.test(p))files.push(p);}};walk(dir);
 }
 for(const dir of ['agentjarvis/android/app/src/main','android/app/src/main']) { const walk=d=>{for(const e of readdirSync(resolve(root,d),{withFileTypes:true})) {const p=d+'/'+e.name;if(e.isDirectory())walk(p);else if(/\.kt$/.test(p))files.push(p);}};walk(dir); }
 files.push('pwa/index.html');
 return files;
}
export function checkNoticeSources() {return noticeSourceFiles().flatMap(file=>noticePlacementViolations(readFileSync(resolve(root,file),'utf8'),file));}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
 const errors=checkNoticeSources();if(errors.length){console.error(errors.join('\n'));process.exitCode=1;}else console.log(`PASS notice-placement-source: ${noticeSourceFiles().length} styles/native sources; no bottom notice anchors`);
}
