// #538: computed typography contract, measured on production DOM and stylesheets.
// Snapshot changes require a task explicitly authorizing typography changes; never auto-update in CI.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { startWebServer } from './serve.mjs';
import { launch, newPage, navigate, evaluate } from './browser.mjs';
export const snapshotPath = fileURLToPath(new URL('./fixtures/p538-typography-02612.json', import.meta.url));
export async function typography(publicDir, outFile) {
  const sourceRoot = publicDir ? resolve(publicDir) : fileURLToPath(new URL('../public/', import.meta.url));
  const relay = readFileSync(resolve(sourceRoot,'js/relay.js'),'utf8');
  const classify = relay.match(/w\.className = \"words\"[^;]+;/)?.[0];
  if(!classify) throw Error('Reply size classifier missing; review contract');
  const server = await startWebServer(publicDir ? { publicDir: resolve(publicDir) } : {});
  const browser = await launch();
  const rows = {};
  try {
    for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const lang of ['zh', 'en']) {
      const p = await newPage(browser, width, 900, theme, { allow: [server.url] });
      try {
        // Rendering-only contract: use the real HTML and CSS without initiating pairing/network.
        await p.send('Network.setBlockedURLs', { urls: ['*.js', '*.mjs'] });
        await navigate(p, server.url);
        const result = await evaluate(p, `(async () => {
          document.documentElement.lang=${JSON.stringify(lang)};
          document.documentElement.dataset.theme=${JSON.stringify(theme)};
          document.body.dataset.status='idle'; document.body.dataset.conn='on';
          const sample=${JSON.stringify(lang === 'zh' ? '聊天正文与批准卡片，字号保持原样。' : 'Chat text and approval cards keep their original size.')};
          document.querySelector('#words').textContent=sample;
          document.querySelector('#sheetTitle').textContent=sample;
          document.querySelector('#cmd').textContent='pwd';
          document.querySelector('#settings').hidden=false;
          await document.fonts.ready;
          const selectors=['html','body','#words','#sheetTitle','#cmd','#set-title','.setrow .setlab','.setval'];
          const scales=[0.8,0.9,1,1.1,1.25,1.4,1.6], rows={};
          for(const page of ['m','s','l']) for(const rs of scales){
            if(page==='m') delete document.documentElement.dataset.fz; else document.documentElement.dataset.fz=page;
            document.documentElement.style.setProperty('--rs',String(rs));
            for(const count of [240,241,600,601]){
              const w=document.querySelector('#words'),tx=sample.repeat(count).slice(0,count);
              w.textContent=tx;
              ${classify}
              const cls=count;
              const values={};
              for(const selector of selectors){const e=document.querySelector(selector);if(!e)throw Error('Missing typography selector '+selector);const c=getComputedStyle(e);values[selector]={fontSize:c.fontSize,lineHeight:c.lineHeight,...(selector==='#words'?{className:e.className}:{})};}
              rows[page+'/'+rs+'/'+(cls||'short')]=values;
            }
          }
          return rows;
        })()`);
        rows[`${width}/${theme}/${lang}`] = result;
      } finally { await p.dispose(); }
    }
  } finally { await browser.close(); await server.stop(); }
  if (outFile) { mkdirSync(resolve(outFile, '..'), { recursive: true }); writeFileSync(outFile, JSON.stringify(rows, null, 2)+'\n'); }
  return rows;
}
// Real settings module initialization, not a duplicate implementation of its size mapping.
export async function storedSizes(publicDir) {
  const server=await startWebServer(publicDir ? {publicDir:resolve(publicDir)} : {}), browser=await launch();const results=[];
  try {
    for(const page of ['m','s','l']) for(const scale of [null,0.8,0.9,1,1.1,1.25,1.4,1.6,'invalid']) {
      const p=await newPage(browser,390,844,'light',{allow:[server.url]});
      try {
        const seed=await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`${scale===null ? "localStorage.removeItem('aj.fontScale');" : "localStorage.setItem('aj.fontScale',"+JSON.stringify(String(scale))+");"}localStorage.setItem('aj.pageFz','${page}');localStorage.setItem('p538-sentinel','keep');`});
        await navigate(p,server.url);
        const result=await evaluate(p,`({scale:localStorage.getItem('aj.fontScale'),page:localStorage.getItem('aj.pageFz'),rs:document.documentElement.style.getPropertyValue('--rs'),fz:document.documentElement.dataset.fz||'m',sentinel:localStorage.getItem('p538-sentinel')})`);
        results.push({page,scale,result});
        if(page==='l' && scale===1.25) {
          await p.send('Page.removeScriptToEvaluateOnNewDocument',{identifier:seed.identifier});
          await navigate(p,server.url);
          const reloaded=await evaluate(p,`({scale:localStorage.getItem('aj.fontScale'),page:localStorage.getItem('aj.pageFz'),rs:document.documentElement.style.getPropertyValue('--rs'),fz:document.documentElement.dataset.fz||'m',sentinel:localStorage.getItem('p538-sentinel')})`);
          if(JSON.stringify(reloaded)!==JSON.stringify(result))throw Error('Same-origin reload changed saved sizes: '+JSON.stringify({result,reloaded}));
        }
      } finally {await p.dispose();}
    }
  } finally {await browser.close();await server.stop();}
  return results;
}
if(process.argv[1]===fileURLToPath(import.meta.url)) {
  const args=process.argv.slice(2);
  function option(name){ const i=args.indexOf(name); if(i<0)return undefined; if(!args[i+1]||args[i+1].startsWith('--'))throw Error('Missing '+name);return args[i+1]; }
  function publicPath(root){ return root && resolve(root,'web/public'); }
  const candidate=publicPath(option('--candidate-root'));
  const baseline=publicPath(option('--baseline-root'));
  const actual=await typography(candidate,option('--out'));
  const expected=baseline ? await typography(baseline) : JSON.parse(readFileSync(snapshotPath)).computed;
  const {isDeepStrictEqual}=await import('node:util');
  const storage=await storedSizes(candidate);
  for(const {page,scale,result} of storage) {
    const want={scale:scale===null?null:String(scale),page,rs:scale===null||scale==='invalid'?'1':String(scale),fz:page,sentinel:'keep'};
    if(!isDeepStrictEqual(result,want))throw Error('Stored size changed: '+JSON.stringify({page,scale,result,want}));
  }
  if(!isDeepStrictEqual(actual,expected)){
    let drift=[];
    for(const profile of Object.keys(actual))for(const size of Object.keys(actual[profile]))for(const selector of Object.keys(actual[profile][size])) {
      const a=actual[profile][size][selector],e=expected[profile]?.[size]?.[selector];
      if(!isDeepStrictEqual(a,e))drift.push({profile,size,selector,expected:e,actual:a});
    }
    console.error(JSON.stringify({status:'FAIL',reason:'computed typography drift; explicit task authorization and reviewed snapshot required',differences:drift.slice(0,20),total:drift.length},null,2));process.exitCode=1;
  } else console.log('PASS #538: 8 viewport/theme/language profiles × 84 size variants × 8 elements match previous formal typography.');
}
