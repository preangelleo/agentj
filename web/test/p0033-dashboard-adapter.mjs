// Offline adapter only. #577 owns capture/settle/PNG comparison; this module never captures or masks a page.
import {createHash} from 'node:crypto';
import {resolve,join} from 'node:path';
import {readdirSync,readFileSync,lstatSync} from 'node:fs';
import {startDashboard} from '../../dashboard/test/server.mjs';
export const FIXED_NOW=1793980000000;
export async function startCriticalDashboard({publicRoot,now=FIXED_NOW}={}){
 if(!publicRoot)throw Error('Frozen Dashboard publicRoot is required');
 const assetFiles=Object.fromEntries(readdirSync(resolve(publicRoot),{recursive:true}).filter(p=>{const st=lstatSync(join(publicRoot,p));if(st.isSymbolicLink())throw Error('Frozen Dashboard assets cannot contain symlinks');return st.isFile();}).sort().map(p=>[p,createHash('sha256').update(readFileSync(join(publicRoot,p))).digest('hex')]));
 const assetsSha256=createHash('sha256').update(JSON.stringify(assetFiles)).digest('hex');
 const dash=await startDashboard({publicRoot:resolve(publicRoot),now});
 try{
  const run=(sql,...args)=>dash.db.raw.prepare(sql).run(...args);
  // Synthetic local session, never an account credential or real login/step-up.
  const user='visual-0033',token='Q'.repeat(43),credential='dmlzdWFsLTAwMzM';
  run('INSERT INTO users(id,email,handle,created_at) VALUES(?,?,?,?)',user,'visual@example.invalid','dmlzdWFsLTAwMzMtdXNlcg',now-86400000);
  run('INSERT INTO passkeys(cred_id,user_id,public_key_jwk,alg,created_at,rp_id) VALUES(?,?,?,?,?,?)',credential,user,'{}',-7,now-86400000,'localhost');
  run("INSERT INTO sessions(token_hash,user_id,created_at,verified_at,expires_at,auth_method,auth_cred_id) VALUES(?,?,?,?,?,'passkey',?)",createHash('sha256').update(token).digest('hex'),user,now,now,now+43200000,credential);
  return {assetsSha256,url:dash.appUrl+'/',origin:dash.appOrigin,cookie:{name:'__Host-aj_s',value:token,domain:'localhost',path:'/',secure:true,httpOnly:true,sameSite:'Strict'},stop:dash.stop,mail:dash.mail};
 }catch(e){await dash.stop();throw e;}
}
export async function prepareDashboardPage({page,context,dashboard,config}){
 const allowed=new URL(dashboard.origin).origin;
 await page.route('**/*',route=>new URL(route.request().url()).origin===allowed?route.continue():route.abort('blockedbyclient'));
 // Existing p577 context owns fixed time, viewport, DPR, theme, language and screenshot settling.
 await context.addCookies([dashboard.cookie]);
 await page.goto(dashboard.url);
 await page.waitForFunction(()=>document.getElementById('app-main')?.hidden===false);
 await page.evaluate(({theme,lang})=>{document.documentElement.dataset.theme=theme;window.AJLang.set(lang);},config);
 await page.waitForFunction(()=>document.fonts.status==='loaded');
 return {name:'publish-dashboard-account',target:'#app-main',sourceScope:'dashboard-preserved',preservedAssetsSha256:dashboard.assetsSha256};
}
