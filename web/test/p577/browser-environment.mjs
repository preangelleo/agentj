import{createReadStream,existsSync}from'node:fs';import{createHash}from'node:crypto';
export function parseBrowserArguments(text){
 const args=[];let token='',quote=null;
 for(let i=0;i<text.length;i++){const c=text[i];if(c==='\\'&&['\\','"',"'",' ','\t'].includes(text[i+1])){token+=text[++i];continue}if(quote){if(c===quote)quote=null;else token+=c}else if(c==='"'||c==="'")quote=c;else if(/\s/.test(c)){if(token){args.push(token);token=''}}else token+=c;}
 if(quote)throw Error('Incomplete quoted browser command line');if(token)args.push(token);return args;
}
export function protectBrowserArguments(args){return args.map(arg=>/^--(?:proxy|.*(?:token|secret|credential|password|api-key)).*=/i.test(arg)&&!arg.startsWith('--password-store=')?arg.split('=')[0]+'=<redacted sha256:'+createHash('sha256').update(arg.slice(arg.indexOf('=')+1)).digest('hex')+'>':arg)}
export function normalizeBrowserArguments(args){
 const profile=args.find(a=>a.startsWith('--user-data-dir='))?.slice('--user-data-dir='.length);
 return protectBrowserArguments(args).map(arg=>{if(profile)arg=arg.split(profile).join('<temporary-isolated-profile>');return arg.replace(/^(--(?:crashpad-handler-pid|parent-process-id)=).+$/,'$1<process-id>')});
}
async function fileSha(path){const hash=createHash('sha256');for await(const chunk of createReadStream(path))hash.update(chunk);return hash.digest('hex')}
export async function browserEnvironment(browser,requestedBinary){
 const session=await browser.newBrowserCDPSession(),system=await session.send('SystemInfo.getInfo');let args,method='Browser.getBrowserCommandLine',limitation=null;
 try{args=(await session.send('Browser.getBrowserCommandLine')).arguments}catch(error){method='SystemInfo.getInfo.commandLine';limitation='Browser.getBrowserCommandLine unavailable without --enable-automation; no extra automation flag was added';args=parseBrowserArguments(system.commandLine)}
 if(!args?.length)throw Error('Actual browser arguments unavailable');
 const own=(await session.send('SystemInfo.getProcessInfo')).processInfo.find(p=>p.type==='browser');
 const processExe=process.platform==='linux'&&own?'/proc/'+own.id+'/exe':null;
 const binaryPath=processExe&&existsSync(processExe)?processExe:args[0];
 const renderer=Object.fromEntries(['displayType','glRenderer','glImplementationParts','glVersion','skiaBackendType','hardwareSupportsVulkan','subpixelFontRendering'].map(k=>[k,system.gpu.auxAttributes[k]]));
 return{stable:{requestedBinary,actualBinary:args[0],binarySha256:await fileSha(binaryPath),binaryHashMethod:processExe&&existsSync(processExe)?'own SystemInfo browser process executable':'actual command executable',actualArguments:normalizeBrowserArguments(args),argumentsSource:method,renderer},raw:{arguments:protectBrowserArguments(args),source:method,limitation,normalization:'Only temporary isolated profile paths and process IDs are normalized for comparison. Potential secret argument values are redacted with a digest.'}};
}
