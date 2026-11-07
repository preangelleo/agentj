// #1025: approved business changes are explicit, source-bound review records, never pixel masks.
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {decodePng} from '../../tools/png-evidence.mjs';
export function formalOutsideRegions(directory,row){
 const image=decodePng(readFileSync(join(directory,'diff',row.name))),points=new Uint8Array(image.width*image.height),regions=[];let count=0;
 for(let p=0;p<points.length;p++){const i=p*4;if(image.rgba[i]===255&&image.rgba[i+1]===0&&image.rgba[i+2]===64&&image.rgba[i+3]===255){points[p]=1;count++;}}
 if(count!==row.outsidePixels)throw Error('Actual formal outside-pixel map differs');
 for(let p=0;p<points.length;p++)if(points[p]){const queue=[p];points[p]=0;let minX=image.width,minY=image.height,maxX=-1,maxY=-1;for(let i=0;i<queue.length;i++){const x=queue[i]%image.width,y=Math.floor(queue[i]/image.width);minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++){const xx=x+dx,yy=y+dy;if(xx<0||yy<0||xx>=image.width||yy>=image.height)continue;const n=yy*image.width+xx;if(points[n]){points[n]=0;queue.push(n);}}}regions.push({bbox:[minX,minY,maxX,maxY],pixels:queue.length});}
 return regions;
}
export function admitFormalDrift(comparison, decisions, sources, actualRegions={}){
 const errors=[];
 if(comparison.mode!=='baseline-noise-envelope'||comparison.sampleCount<3||comparison.errors?.length||!comparison.images)errors.push('Formal576 actual noise samples/comparison incomplete');
 if(decisions?.schemaVersion!==1||!Array.isArray(decisions.decisions))errors.push('Formal drift review contract missing');
 const used=new Set(),rows=[];
 for(const row of comparison.rows||[]){
  if(row.error||!Number.isInteger(row.outsidePixels)||row.envelopeFraction>0.005){errors.push(row.name+': invalid or UNCERTAIN formal baseline');continue;}
  if(!row.outsidePixels){rows.push({name:row.name,status:'within-formal-noise',outsidePixels:0});continue;}
  const match=(decisions?.decisions||[]).filter(x=>x.sceneImage===row.name&&x.baselineCommit===sources.baselineCommit&&x.referenceCommit===sources.referenceCommit&&x.diffSha256===row.diffSha256&&x.envelopeSha256===row.envelopeSha256&&x.outsidePixels===row.outsidePixels);
  if(match.length!==1||!/^#?\d+$|^P\d+$/.test(match[0].requirement||'')||!match[0].reason?.trim()||!match[0].evidence?.trim()||!Array.isArray(match[0].regions)||!match[0].regions.length||match[0].regions.some(x=>!Array.isArray(x.bbox)||x.bbox.length!==4||!x.reason?.trim())){errors.push(row.name+': outside formal envelope needs individually explained chairman-requested feature review');continue;}
  if(JSON.stringify(match[0].regions.map(x=>({bbox:x.bbox,pixels:x.pixels})))!==JSON.stringify(actualRegions[row.name])){errors.push(row.name+': review does not explain every actual outside-pixel region');continue;}
  used.add(match[0]);rows.push({name:row.name,status:'requested-business-change',review:match[0]});
 }
 if((decisions?.decisions||[]).some(x=>!used.has(x)))errors.push('Unused/stale formal drift approval cannot be carried forward');
 return {schema:'agentj-formal576-drift-v1',ok:!errors.length&&rows.length===comparison.images,sources,rows,errors};
}
