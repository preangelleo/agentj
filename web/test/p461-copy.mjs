// Recheck only the two pre-P458 copy-notice expectations; retain real clipboard checks.
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch} from './browser.mjs';
import {runCases} from './parity_cases.mjs';
const only=['copy-reply','key-y-copy'];
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();let r;
try{r=await runCases({B,web,fake,only});assert.deepEqual(r.fails,[]);assert.deepEqual(Object.keys(r.results).sort(),[...only].sort());assert.ok(Object.values(r.results).every(x=>x==='pass'));}
finally{await B.close();await fake.stop();await web.stop();}
writeFileSync('reports/qa/p461/copy-compat.json',JSON.stringify({combined:JSON.parse(readFileSync('web/public/version.json','utf8')).combined,rechecked:r.results,qualified:2,scope:'two current-source clipboard/tick cases only; historical P461 parity logs are not a P472 prerequisite'},null,2)+'\n');
console.log('P461 copy compatibility PASS 2/2; current-source real clipboard + tick-only assertions');
