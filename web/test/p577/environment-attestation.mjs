// No page or screenshot: attest the same capture launcher and its actual browser/environment.
import {createHash} from 'node:crypto';
import {readFileSync,realpathSync,readdirSync,lstatSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {dirname,resolve} from 'node:path';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const hashFiles=files=>Object.fromEntries([...new Set(files.map(file=>realpathSync(file)))].sort().map(file=>[file,sha(readFileSync(file))]));
export function collectFontEnvironment(){
 const files=execFileSync('fc-list',['--format','%{file}\n'],{encoding:'utf8',maxBuffer:16*1024*1024}).trim().split('\n').filter(Boolean);
 if(!files.length)throw Error('Actual font file inventory unavailable');
 const configText=execFileSync('fc-conflist',[],{encoding:'utf8',maxBuffer:16*1024*1024});
 const configs=configText.split('\n').filter(line=>line.startsWith('+ ')).map(line=>line.slice(2,line.indexOf(': ',2))).filter(Boolean);
 if(!configs.length)throw Error('Active fontconfig inventory unavailable');
 return {systemFontFiles:hashFiles(files),fontConfigFiles:hashFiles(configs),fontEnvironment:Object.fromEntries(['FONTCONFIG_FILE','FONTCONFIG_PATH','FREETYPE_PROPERTIES','LANG','LC_ALL','LC_CTYPE'].map(key=>[key,process.env[key]??null]))};
}
export function collectRuntimeEnvironment(packageFiles){
 const packages=Object.fromEntries(packageFiles.map(file=>{const root=dirname(file),pkg=JSON.parse(readFileSync(file)),files=Object.fromEntries(readdirSync(root,{recursive:true}).filter(name=>{const path=resolve(root,name);return !name.split('/').includes('node_modules')&&lstatSync(path).isFile()&&/\.(?:[cm]?js|json|node)$/.test(name);}).sort().map(name=>[name,sha(readFileSync(resolve(root,name)))]));return[pkg.name,{version:pkg.version,sha256:sha(Buffer.from(JSON.stringify(files)))}];}));
 return {nodeBinary:{path:realpathSync(process.execPath),sha256:sha(readFileSync(process.execPath))},playwrightRuntime:packages};
}
function canonical(value){if(Array.isArray(value))return value.map(canonical);if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));return value;}
function verifyMap(map,label){if(!map||typeof map!=='object'||Array.isArray(map)||!Object.keys(map).length||Object.values(map).some(value=>typeof value!=='string'||!/^([a-f0-9]{64})$/.test(value)))throw Error('Missing or invalid '+label+' hashes');}
export function stableEnvironment(environment){
 const e=environment;if(!e?.nodeBinary||!/^([a-f0-9]{64})$/.test(e.nodeBinary.sha256||'')||!e.playwrightRuntime||!['playwright','playwright-core'].every(name=>/^([a-f0-9]{64})$/.test(e.playwrightRuntime[name]?.sha256||'')))throw Error('Actual Node/Playwright runtime binding unavailable');if(!e?.browser||!/^([a-f0-9]{64})$/.test(e.browser.binarySha256||''))throw Error('Actual browser binary hash unavailable');
 verifyMap(e.fontFiles,'source font');verifyMap(e.systemFontFiles,'system font');verifyMap(e.fontConfigFiles,'fontconfig');
 if(!Array.isArray(e.launchFlags)||!Array.isArray(e.browser.actualArguments)||!e.browser.actualArguments.length||!e.browser.renderer||!e.browserVersion||!e.nodeVersion||!e.playwrightVersion||!e.platform||!e.architecture||!e.browser.actualBinary||!e.browser.argumentsSource||typeof e.browser.renderer.displayType!=='string'||!e.browser.renderer.displayType||typeof e.browser.renderer.glRenderer!=='string'||!e.browser.renderer.glRenderer)throw Error('Incomplete actual capture environment');
 const bindings=e.sourceOwnedAssets||[];
 const normalize=arg=>{
  if(typeof arg!=='string')throw Error('Invalid actual browser argument');
  for(const item of bindings){
   if(item.flag!=='--use-file-for-fake-video-capture'||item.sourcePath!=='web/test/p577/camera.y4m'||typeof item.path!=='string'||!/^([a-f0-9]{64})$/.test(item.sha256||''))throw Error('Unsupported source-owned argument binding');
   if(arg===item.flag+'='+item.path)return item.flag+'=<source:'+item.sourcePath+';sha256:'+item.sha256+'>';
  }
  return arg;
 };
 const browser={...e.browser,actualArguments:e.browser.actualArguments.map(normalize)};
 const result={schema:'p577-environment-v1',nodeBinarySha256:e.nodeBinary.sha256,playwrightRuntime:e.playwrightRuntime,nodeVersion:e.nodeVersion,playwrightVersion:e.playwrightVersion,playwrightChromiumContract:e.playwrightChromiumContract,platform:e.platform,architecture:e.architecture,browserVersion:e.browserVersion,browser,renderer:e.renderer,chromium:e.chromium,launchFlags:e.launchFlags.map(normalize),deviceScaleFactor:e.deviceScaleFactor,timezoneId:e.timezoneId,locales:e.locales,mediaPlayback:e.mediaPlayback,motion:e.motion,serviceWorkers:e.serviceWorkers,syntheticOrigin:e.syntheticOrigin,fontFiles:e.fontFiles,systemFontFiles:e.systemFontFiles,fontConfigFiles:e.fontConfigFiles,fontEnvironment:e.fontEnvironment,sourceOwnedAssets:bindings.map(({path,...binding})=>binding)};
 return canonical(result);
}
export const environmentFingerprint=environment=>sha(Buffer.from(JSON.stringify(stableEnvironment(environment))));
