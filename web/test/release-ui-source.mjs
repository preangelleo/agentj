// Frozen unapproved UI is tested without making it a live release dependency.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root=new URL('../',import.meta.url),plan=JSON.parse(readFileSync(new URL('deferred/0.30.22/manifest.json',root)));
const review=JSON.parse(readFileSync(new URL('ui-review-0322-r2.json',root)));
const hash=b=>createHash('sha256').update(b).digest('hex');
export function retainedUiSource(path){
 const row=plan.files.find(r=>r.path==='web/'+path);
 assert.ok(row,'retained release source is explicitly inventoried: '+path);
 const live=readFileSync(new URL(path,root)),saved=readFileSync(new URL(row.saved.replace('web/',''),root));
 assert.equal(hash(live),(review.active[row.path]?.sha256 || row.active_sha256),'active release binding: '+path);
 assert.equal(hash(saved),row.candidate_sha256,'retained candidate binding: '+path);
 return saved.toString();
}
