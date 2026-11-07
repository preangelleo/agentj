// Narrow adapter smoke, not 0033 screenshot qualification. Uses an existing Playwright installation only.
import assert from 'node:assert/strict';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {startCriticalDashboard,prepareDashboardPage} from './p0033-dashboard-adapter.mjs';
if(!process.env.AJ_PLAYWRIGHT_MODULE)throw Error('Existing AJ_PLAYWRIGHT_MODULE required; do not install a package');
const {chromium}=await import(pathToFileURL(process.env.AJ_PLAYWRIGHT_MODULE));
const dash=await startCriticalDashboard({publicRoot:fileURLToPath(new URL('../../dashboard/public/',import.meta.url))});
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 let configs=0;
 for(const config of [{width:390,height:844,theme:'light',lang:'zh'},{width:1440,height:900,theme:'dark',lang:'en'}]){
  const context=await browser.newContext({viewport:{width:config.width,height:config.height},colorScheme:config.theme,serviceWorkers:'block'});
  try{
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   const result=await prepareDashboardPage({page,context,dashboard:dash,config});
   assert.equal(result.name,'publish-dashboard-account');assert.ok(await page.locator('#app-main').isVisible());
   assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),config.theme);
   assert.equal(await page.evaluate(()=>window.AJLang.get()),config.lang);
   assert.deepEqual(errors,[]);configs++;
  }finally{await context.close();}
 }
 assert.deepEqual(dash.mail,[]);console.log(JSON.stringify({adapterConfigs:configs,screenshots:0,qualified:false}));
}finally{await browser?.close();await dash.stop();}
