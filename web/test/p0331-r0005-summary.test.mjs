// R-0005: one-line summary for /background rows. Pure display over the history text the row already shows on
// expansion; the live row stays on the fixed 0331 text until the chairman approves (R0005_APPROVED).
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {activitySummary} from '../public/js/phone-activity.js';
test('agent-started records lead with the Agent words, others with the arrived message',()=>{
 assert.equal(activitySummary({source:{k:'agent',via:'heartbeat',text:'Fixed heartbeat check text'},reply:'All 3 scheduled jobs ran.'}),'All 3 scheduled jobs ran.');
 assert.equal(activitySummary({source:{k:'auto',name:'news',text:'Daily news sent (12 items)'},reply:'Noted.'}),'Daily news sent (12 items)');
 assert.equal(activitySummary({source:{k:'sys',text:''},reply:{text:'Host updated'}}),'Host updated');
 assert.equal(activitySummary({source:{k:'agent',via:'loop',text:'loop prompt'},reply:''}),'loop prompt');
 assert.equal(activitySummary({source:{k:'sys'},reply:''}),'');
});
test('JSON, fences, rules and markup never become the line',()=>{
 assert.equal(activitySummary({source:{k:'auto',text:'{"schema":"automation-report.v1"}'},reply:'Raw background notice'}),'Raw background notice');
 assert.equal(activitySummary({source:{k:'auto',text:'["a",1]\n[{"x":1}]'},reply:''}),'');
 assert.equal(activitySummary({source:{k:'agent'},reply:'```json\n{"ok":true}\nsecret-looking line inside fence\n```\n---\n## **Backup** done\n- more'}),'Backup done');
 assert.equal(activitySummary({source:{k:'sys',text:'[link](x) kept'}}),'[link](x) kept');
});
test('one line, bounded, silent markers removed by the caller cleaner',()=>{
 const long='x'.repeat(400);
 assert.equal(activitySummary({source:{k:'sys',text:long}}).length,160);
 assert.equal(activitySummary({source:{k:'sys',text:'  a\t b \n c'}}),'a b');
 assert.equal(activitySummary({source:{k:'agent'},reply:'[silent] quiet result'},s=>s.replace('[silent]','').trim()),'quiet result');
});
test('approved (0.30.34): every served page shows the line; no preview query, no new control',()=>{
 const src=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
 assert.doesNotMatch(src,/R0005_APPROVED|R0005_FAIL_LINE|preview=r0005|r0005fail|r0005Local/);
 const fn=src.slice(src.indexOf('function activityLine(turn, src){'),src.indexOf('function silentWhen(ts){'));
 assert.match(fn,/activityFailed\(turn\) \? label \+ ' · ' \+ t\('p0331\.failure'\) : label/);
 assert.match(fn,/activitySummary\(turn, cleanSilentMarkers\)/);
 assert.doesNotMatch(fn,/createElement|addEventListener|onclick|button/i);
});
