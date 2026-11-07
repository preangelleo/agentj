import test from 'node:test';import assert from 'node:assert/strict';import{mkdtempSync,writeFileSync,rmSync,readFileSync}from'node:fs';import{tmpdir}from'node:os';import{join}from'node:path';import{criticalConfigs,comparisonFixture,criticalReply,criticalSceneSelection,criticalOwnershipScope}from'./publish-critical-profile.mjs';
test('critical profile is exactly three widths, both themes/languages and 496 PNG per side',()=>{const m=JSON.parse(readFileSync(new URL('./scene-manifest.json',import.meta.url))).publishCritical;const configs=criticalConfigs();assert.equal(configs.length,12);assert.deepEqual([...new Set(configs.map(c=>c.width))],[390,834,1440]);assert.equal(new Set(m.scenes).size,42);assert.equal(configs.length*m.scenes.length-configs.filter(c=>c.width>390).length,496);assert.ok(m.scenes.includes('wallet-automation'));assert.ok(!m.scenes.includes('legacy-tasks'));});
test('comparison input binds one explicit display version and fixed time, not arbitrary source overrides',()=>{const root=mkdtempSync(join(tmpdir(),'critical-fixture-'));try{const file=join(root,'input.json'),input={schemaVersion:1,policy:'fixed-interface-data-only',displayVersion:'0.27.1',now:1793980000000};writeFileSync(file,JSON.stringify(input));assert.match(comparisonFixture(file).sha256,/^[a-f0-9]{64}$/);for(const patch of [{policy:'mask-all'},{now:Date.now()},{displayVersion:4}]){writeFileSync(file,JSON.stringify({...input,...patch}));assert.throws(()=>comparisonFixture(file));}}finally{rmSync(root,{recursive:true,force:true});}});
test('automation fixture has real task/native typed controls and explicit page count without executing either',()=>{const[t,m]=criticalReply({t:'jcard_get',id:'automation'});assert.equal(t,'jcard_get');assert.equal(m.pagination.total,3);assert.equal(m.items[0].control.target.kind,'agentj-task');assert.equal(m.items[1].control.target.source,'systemd');assert.equal(criticalReply({t:'jcard_get',id:'automation',automation_page:1})[1].items[0].id,'tail-sample');assert.equal(criticalReply({t:'native_scheduler_control'}),null);});

test('explicit critical subset is nonempty, unique, known and content hashed',()=>{
 const root=mkdtempSync(join(tmpdir(),'critical-scenes-'));
 try{const file=join(root,'scenes.json');
  for(const scenes of [[],['unknown'],['chat-idle','chat-idle'],[null]]){writeFileSync(file,JSON.stringify({schemaVersion:1,scenes}));assert.throws(()=>criticalSceneSelection(file));}
  writeFileSync(file,JSON.stringify({schemaVersion:1,scenes:['chat-idle','settings-main']}));
  const selected=criticalSceneSelection(file);assert.deepEqual(selected.scenes,['chat-idle','settings-main']);assert.match(selected.sha256,/^[a-f0-9]{64}$/);
  assert.equal(criticalSceneSelection().scenes.length,42);
 }finally{rmSync(root,{recursive:true,force:true});}
});
test('formal subset names39 real old scenes and individually attributes the three added scenes',()=>{
 const file=new URL('./formal-critical-scenes.json',import.meta.url),manifest=JSON.parse(readFileSync(file)),selected=criticalSceneSelection(file);
 assert.equal(selected.scenes.length,39);assert.match(manifest.status,/not been captured/);
 assert.deepEqual(manifest.excluded.map(e=>[e.scene,e.ticket]),[['publish-owner-hosts-list','feedback:400'],['publish-owner-hosts-edit','feedback:400'],['wallet-automation','feedback:369']]);
 for(const e of manifest.excluded){assert.ok(e.source);assert.ok(e.implementation);assert.ok(!selected.scenes.includes(e.scene));}
 const flow=readFileSync(new URL('./publish-critical-profile.mjs',import.meta.url),'utf8');
 assert.match(flow,/if\(selected.has\('publish-owner-hosts-list'\)\|\|selected.has\('publish-owner-hosts-edit'\)\)\{/);
 assert.match(flow,/if\(selected.has\('wallet-automation'\)\)\{/);
 assert.doesNotMatch(flow,/notApplicable[^\n]*(?:owner-hosts|automation)/);
 const capture=readFileSync(new URL('./capture.mjs',import.meta.url),'utf8');assert.match(capture,/scenes:selectedCritical.scenes/);assert.match(capture,/criticalSceneSelection:selectedCritical/);
});

test('legacy ownership scope accepts only explicit formal pre577 source identities and never the primary profile',()=>{
 const selected=criticalSceneSelection(new URL('./formal-critical-scenes.json',import.meta.url));
 for(const source of selected.legacyOwnerSources)assert.equal(criticalOwnershipScope(selected,source),'formal-pre577-source');
 assert.throws(()=>criticalOwnershipScope(selected,'0'.repeat(64)),/Unreviewed/);
 assert.throws(()=>criticalOwnershipScope({...selected,legacyOwnerSources:[selected.legacyOwnerSources[0],'0'.repeat(64)]},selected.legacyOwnerSources[0]),/Unreviewed/);
 assert.throws(()=>criticalOwnershipScope({...selected,sha256:null},selected.legacyOwnerSources[0]),/Unreviewed/);
 assert.equal(criticalOwnershipScope(criticalSceneSelection(),'0'.repeat(64)),'577-component-ownership');
 const capture=readFileSync(new URL('./capture.mjs',import.meta.url),'utf8');
 assert.match(capture,/SIDE === 'after' && !formalPre577/);assert.match(capture,/row.ownerAuditOK = SIDE === 'before' \|\| formalPre577 \? null/);assert.match(capture,/ownershipAuditScope,/);
});
