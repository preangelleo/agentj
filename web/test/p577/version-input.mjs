import{readFileSync}from'node:fs';import{resolve}from'node:path';import{createHash}from'node:crypto';
const sha=b=>createHash('sha256').update(b).digest('hex');
export function loadVersionInput(path,actualVersion){
 if(!path)return{path:null,binding:{mode:'raw'},moduleText:null,moduleSha256:null};
 const inputPath=resolve(path),bytes=readFileSync(inputPath);let value;try{value=JSON.parse(bytes)}catch{throw Error('Malformed public VERSION combined input JSON')}
 const keys=['v','version','algo','combined'];
 if(!value||Array.isArray(value)||typeof value!=='object'||Object.keys(value).some(k=>!keys.includes(k))||value.v!==1||value.algo!=='sha256'||typeof value.version!=='string'||!/^\d[\w.+-]{0,63}$/.test(value.version)||!/^[a-f0-9]{64}$/.test(value.combined||''))throw Error('Invalid explicit public VERSION combined input');
 if(!actualVersion||actualVersion.v!==value.v||actualVersion.version!==value.version||actualVersion.algo!==value.algo||!/^[a-f0-9]{64}$/.test(actualVersion.combined||'')||!actualVersion.files||typeof actualVersion.files!=='object')throw Error('VERSION input must preserve actual source version and required shape');
 const display={...actualVersion,combined:value.combined};
 const moduleText='// Explicit public combined input; all other VERSION fields remain actual source data.\nexport default '+JSON.stringify(display)+';\n';
 return{path:inputPath,binding:{mode:'same-version-combined-input',inputSha256:sha(bytes),version:value.version,combined:value.combined},moduleText,moduleSha256:sha(moduleText)};
}
export function versionModuleResponse(input,requestUrl,base){
 if(!input.moduleText)return null;
 const origin=new URL(base),request=new URL(requestUrl);
 if(origin.hostname!=='localhost'||origin.protocol!=='http:')throw Error('VERSION input is restricted to isolated localhost fixture');
 if(request.href!==new URL('version.js',origin).href)return null;
 return{status:200,contentType:'text/javascript; charset=utf-8',body:input.moduleText};
}
export const versionInputStable=input=>!input.path||sha(readFileSync(input.path))===input.binding.inputSha256;
