import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {inflateSync} from 'node:zlib';
// Existing p340 screenshot decoder, reused without importing its full audit runner.
function png(buf){let o=8,w=0,h=0,ct=6;const idat=[];
 while(o<buf.length){const n=buf.readUInt32BE(o),t=buf.toString('ascii',o+4,o+8);if(t==='IHDR'){w=buf.readUInt32BE(o+8);h=buf.readUInt32BE(o+12);ct=buf[o+17];}if(t==='IDAT')idat.push(buf.subarray(o+8,o+8+n));o+=12+n;}
 const bpp=ct===6?4:3,raw=inflateSync(Buffer.concat(idat)),st=w*bpp,px=new Uint8Array(w*h*3);let prev=new Uint8Array(st);
 for(let y=0;y<h;y++){const f=raw[y*(st+1)],line=raw.subarray(y*(st+1)+1,(y+1)*(st+1)),cur=new Uint8Array(st);
  for(let i=0;i<st;i++){const a=i>=bpp?cur[i-bpp]:0,b=prev[i],c=i>=bpp?prev[i-bpp]:0;let v=line[i];
   if(f===1)v+=a;else if(f===2)v+=b;else if(f===3)v+=(a+b)>>1;else if(f===4){const p=a+b-c,pa=Math.abs(p-a),pb=Math.abs(p-b),pc=Math.abs(p-c);v+=pa<=pb&&pa<=pc?a:pb<=pc?b:c;}cur[i]=v&255;}
  for(let x=0;x<w;x++)for(let k=0;k<3;k++)px[(y*w+x)*3+k]=cur[x*bpp+k];prev=cur;}
 return {w,h,px};}

const root=process.argv[2] || 'reports/qa/feedback02610-web',results=[];
for(const width of [390,1440])for(const scheme of ['light','dark']){
 const name=`${width}-${scheme}-settings-outline.png`,im=png(readFileSync(`${root}/${name}`));let count=0;
 for(let y=0;y<im.h;y++)for(let x=0;x<im.w;x++){
  const r=Math.hypot(x-im.w/2,y-im.h/2),i=(y*im.w+x)*3;
  if(r>=73&&r<=81&&im.px[i]===98&&im.px[i+1]===152&&im.px[i+2]===135)count++;
 }
 assert.ok(count>100,`${name}: visible Emerald ring, got ${count} pixels`);results.push({name,emeraldRingPixels:count});
}
writeFileSync(`${root}/pixels.json`,JSON.stringify({ok:true,results},null,2)+'\n');console.log(JSON.stringify({ok:true,results}));
