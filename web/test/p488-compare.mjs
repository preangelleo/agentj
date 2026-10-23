// Every before/after page: changes limited to bottom safe area and the wave surface.
import assert from 'node:assert/strict';import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';import {execFileSync} from 'node:child_process';
const root='reports/qa/p488',before=JSON.parse(readFileSync(root+'/before/results.json')),after=JSON.parse(readFileSync(root+'/after/results.json')),rows=[],failures=[];
mkdirSync(root+'/diff',{recursive:true});
for(const a of after.rows){const b=before.rows.find(b=>a.label===b.label);assert.ok(b);assert.equal(a.rect.top,b.rect.top);const raw=p=>execFileSync('magick',[p,'-depth','8','rgb:-'],{maxBuffer:8e6}),x=raw(b.path),y=raw(a.path);let changed=0,outside=0,seam=0,bottom=0;
 for(let py=0;py<a.height;py++)for(let px=0;px<a.width;px++){const i=(py*a.width+px)*3;if(x[i]===y[i]&&x[i+1]===y[i+1]&&x[i+2]===y[i+2])continue;changed++;if(py>=a.height-a.bottom)bottom++;else if(a.level&&py>=Math.floor(a.rect.top)-19&&py<=Math.ceil(a.rect.top))seam++;else outside++}
 if(outside)failures.push(a.label+' '+outside+' outside intended paint');
 const diff=`${root}/diff/${a.label}.png`;execFileSync('magick',[b.path,a.path,'-compose','difference','-composite','-evaluate','multiply','4',diff]);
 rows.push({label:a.label,before:b.path,after:a.path,diff,changed,outside,seam,bottom});
}
const combined=execFileSync(process.execPath,['web/build.mjs','--print-hash'],{encoding:'utf8'}).trim();assert.equal(after.combined,combined);
writeFileSync(root+'/comparison.json',JSON.stringify({base:after.base,combined,rows,failures},null,2)+'\n');
writeFileSync(root+'/index.html',`<!doctype html><html lang="zh"><meta charset="utf-8"><title>P488 安全区红绿对比</title><style>body{background:#2c2b26;color:#faf9f5;font:16px sans-serif;margin:24px}section{border-top:1px solid #888;padding:18px 0}figure{display:inline-block;margin:4px}img{width:280px;max-width:30vw}a{color:#c7dedc}</style><h1>P488：修前 / 修后 / 差异 ×4</h1><p>WebKit 页面截图，iPhone 15 / Air 合成安全区；不包含浏览器工具栏或真实系统 home 指示条。保留水位过渡。只允许底部34px及原水面19px内颜色变化。</p><p>优先看 Air / standalone / working / 0.5，再看 Safari 同格、零水位和满水位。<a href="recovery/results.json">恢复/编辑/键盘检查</a> · <a href="platforms/results.json">Android/桌面零差异</a></p>`+rows.map(r=>`<section id="${r.label}"><h2>${r.label}</h2><p>变化 ${r.changed} 像素，范围外 ${r.outside}</p>`+['before','after','diff'].map(side=>`<figure><figcaption>${side}</figcaption><a href="${r[side].replace(root+'/','')}"><img loading="lazy" src="${r[side].replace(root+'/','')}"></a></figure>`).join('')+'</section>').join('')+'</html>');
console.log(JSON.stringify({pairs:rows.length,failures},null,2));assert.equal(failures.length,0);
