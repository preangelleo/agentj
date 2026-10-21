// P428 item 4: pure helpers of the calendar's two-way parts (js/calsync.js) — sync result → one status line, source tag,
// conflict explanation, undo failure text — and the p428 dictionary (both languages, no internal words, no trial words).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=(p)=>readFileSync(new URL(p,import.meta.url),'utf8');
globalThis.window??={};
const S=await import('../public/js/calsync.js');
const {t}=await import('../public/js/t.js');
const DICT=(await import('../public/i18n.js')).default;
const subs=[{name:'work',kind:'google',enabled:true},{name:'icloud-home',kind:'caldav',enabled:true},{name:'family',kind:'caldav',enabled:true},{name:'feed',kind:'ics',enabled:true},{name:'old',kind:'google',enabled:false}];

test('sync status line: done / offline / auth per kind / conflicts / failure', ()=>{
  const ok=(sync,extra)=>({ok:true,events:[],...extra,sync:{ok:true,name:'work',pulled:0,pushed:0,queued:0,conflicts:0,linked_duplicates:0,...sync}});
  assert.deepEqual(S.syncStatus(ok({pulled:3,pushed:2})),['p428.done',{pulled:3,pushed:2}]);
  assert.deepEqual(S.syncStatus(ok({ok:false,offline:true,why:'offline'})),['p428.offline']);
  assert.deepEqual(S.syncStatus(ok({ok:false,why:'offline'})),['p428.offline']);
  assert.deepEqual(S.syncStatus(ok({ok:false,why:'auth'}),'google'),['p428.authGoogle']);
  assert.deepEqual(S.syncStatus(ok({ok:false,why:'auth'}),'caldav'),['p428.authCaldav']);
  assert.deepEqual(S.syncStatus(ok({ok:true,why:'too_soon',conflicts:1})),['p428.tooSoon']);
  assert.deepEqual(S.syncStatus(ok({conflicts:2})),['p428.conflicts',{count:2}]);
  assert.deepEqual(S.syncStatus(ok({ok:false,why:'conflict'},{conflicts:[{remote:'work'},{remote:'other'}]})),['p428.conflicts',{count:1}]);
  for(const why of ['gone','failed','full','disabled','not_found'])assert.deepEqual(S.syncStatus(ok({ok:false,why})),['p428.failed'],why);
  assert.deepEqual(S.syncStatus({ok:false,why:'not_two_way'}),['p428.failed']);
  assert.deepEqual(S.syncStatus({ok:false,why:'bad_op'}),['p428.failed']);
  assert.deepEqual(S.syncStatus({t:'timeout'}),['p428.failed']);
  assert.deepEqual(S.syncStatus(undefined),['p428.failed']);
});

test('source tag: Google, iCloud, else the calendar name; strip lists enabled two-way calendars only', ()=>{
  assert.equal(S.remoteLabel('work',subs),'Google');
  assert.equal(S.remoteLabel('icloud-home',subs),'iCloud');
  assert.equal(S.remoteLabel('family',subs),'family');
  assert.equal(S.remoteLabel('gone',subs),'gone');
  assert.deepEqual(S.twoWay(subs).map(s=>s.name),['work','icloud-home','family']);
  assert.deepEqual(S.twoWay(undefined),[]);
});

test('source tag: two enabled calendars of one kind show their own names (QA D8)', ()=>{
  const two=[...subs,{name:'family-g',kind:'google',enabled:true},{name:'icloud-work',kind:'caldav',enabled:true}];
  assert.equal(S.remoteLabel('work',two),'work');
  assert.equal(S.remoteLabel('family-g',two),'family-g');
  assert.equal(S.remoteLabel('icloud-home',two),'icloud-home');
  assert.equal(S.remoteLabel('icloud-work',two),'icloud-work');
  assert.equal(S.remoteLabel('family',two),'family');
  // a turned-off second Google does not count; one of each kind keeps the plain label
  assert.equal(S.remoteLabel('work',[...subs,{name:'g2',kind:'google',enabled:false}]),'Google');
  assert.equal(S.remoteLabel('icloud-home',[...subs,{name:'family-g',kind:'google',enabled:true}]),'iCloud');
});

test('conflict buttons say what happens, per conflict type (QA D1)', ()=>{
  const lbl=c=>S.conflictChoices(c,'Google').map(([k,v,side])=>[t(k,v),side]);
  assert.deepEqual(lbl({remote_state:'deleted',local:{title:'a'}}),[['也删掉','remote'],['留着','local']]);
  assert.deepEqual(lbl({remote_state:'changed',local:null}),[['留着Google的','remote'],['还是删掉','local']]);
  assert.deepEqual(lbl({remote_state:'changed',local:{title:'a'}}),[['用我的版本','local'],['用Google的','remote']]);
  const en=(k,v)=>DICT.en[k].replace(/\{(\w+)\}/g,(m,x)=>v[x]);
  assert.deepEqual(S.conflictChoices({remote_state:'deleted',local:{}},'iCloud').map(([k,v])=>en(k,v)),['Delete here too','Keep it']);
  assert.deepEqual(S.conflictChoices({remote_state:'changed',local:null},'iCloud').map(([k,v])=>en(k,v)),["Keep iCloud's",'Delete it anyway']);
});

test('conflict text: changed / deleted there / deleted here; undo failure text', ()=>{
  assert.deepEqual(S.conflictText({remote_state:'changed',local:{title:'a'}},'Google'),['p428.changed',{remote:'Google'}]);
  assert.deepEqual(S.conflictText({remote_state:'deleted',local:{title:'a'}},'iCloud'),['p428.deleted',{remote:'iCloud'}]);
  assert.deepEqual(S.conflictText({remote_state:'changed',local:null},'Google'),['p428.localDeleted',{remote:'Google'}]);
  assert.equal(S.undoText('nothing_to_undo'),'p428.nothingToUndo');
  for(const why of ['readonly','not_found','conflict','full',undefined])assert.equal(S.undoText(why),'p428.undoFailed');
  assert.equal(t('p428.changed',{remote:'Google'}),'两边都改过，日历里先用了Google的版本');
  assert.equal(t('p428.done',{pulled:3,pushed:2}),'已同步：收到 3 条，发出 2 条');
  assert.equal(DICT.en['p428.done'],'Synced: {pulled} in, {pushed} out');
});

test('p428 strings: both languages, same placeholders, plain words only', ()=>{
  const keys=Object.keys(DICT.en).filter(k=>k.startsWith('p428.'));
  assert.ok(keys.length>=25,keys.length);
  for(const k of keys){
    assert.ok(DICT.zh[k],k);
    const ph=s=>(s.match(/\{\w+\}/g)||[]).sort().join();
    assert.equal(ph(DICT.zh[k]),ph(DICT.en[k]),k);
    for(const w of [/outbox/i,/etag/i,/revision/i,/\bremote\b(?!\})/i,/trial/i,/试用/,/远端/])assert.doesNotMatch(DICT.en[k]+DICT.zh[k],w,k);
  }
  assert.equal(DICT.zh['p428.offline'],'离线：改动已保存在本机，联网后自动同步');
  assert.equal(DICT.en['p428.offline'],'Offline: changes are saved here and sync when back online');
});

test('calendar.js wires the strip, conflicts, tags and the undo line; text only', ()=>{
  const cal=read('../public/js/calendar.js'),sync=read('../public/js/calsync.js');
  assert.match(cal,/from '\.\/calsync\.js'/);
  assert.match(cal,/op:'sync',name\},90000/);
  assert.match(cal,/op:'undo',revision:m\.revision\}/);
  assert.match(cal,/op:'resolve',revision:m\.revision,entry,keep:side/);
  assert.match(cal,/res\.why==='conflict'\?'p413\.conflict':'p413\.failed'/);   // P413's conflict-on-save message kept
  assert.doesNotMatch(read('../public/cards.css').match(/\.jc-cal-flash\{[^}]*\}/)[0],/sticky|fixed|absolute/);   // QA D5: in flow
  for(const src of [cal,sync])assert.doesNotMatch(src,/innerHTML|insertAdjacentHTML|fetch\(|https?:\/\//);
});
