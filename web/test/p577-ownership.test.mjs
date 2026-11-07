import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {launchBrowser} from '../../tests/lib/browser.mjs';
import {ownershipAuditSource} from '../tools/ownership-audit.mjs';
import {buttonAuditSource} from '../tools/button-audit.mjs';

test('#577 real DOM ownership rejects missing, unknown and cross-family appearance; action anchors retain geometry audit', async () => {
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage(390,844);
    await page.eval(`document.body.innerHTML = '<button class="busy-one" data-aj-owner="aj-busy">Robot</button><button class="omq" data-aj-owner="aj-quote">Quote</button><button class="opt" data-aj-owner="aj-choice">Option</button><button class="aj-action" data-aj-owner="aj-action">Save</button>';`);
    assert.equal((await page.eval(ownershipAuditSource)).ok,true);
    for (const cls of ['busy-one','omq','opt']) {
      await page.eval(`document.querySelector('.${cls}').classList.add('aj-action')`);
      assert.ok((await page.eval(ownershipAuditSource)).conflicts.length > 0, cls+' cannot borrow action appearance');
      await page.eval(`document.querySelector('.${cls}').classList.remove('aj-action')`);
    }
    for(const skin of ['aj-action--primary','aj-action--quiet','aj-icon-action--primary','aj-icon-action--quiet','aj-navigation-action--primary','aj-navigation-action--quiet','aj-dialog-action','aj-dialog-action--primary'])for(const cls of ['busy-one','omq','opt']){
      await page.eval(`document.querySelector('.${cls}').classList.add('${skin}')`);
      assert.ok((await page.eval(ownershipAuditSource)).conflicts.length>0,cls+' cannot borrow modifier-only '+skin);
      await page.eval(`document.querySelector('.${cls}').classList.remove('${skin}')`);
    }
    await page.eval(`document.body.innerHTML='<div class="omq" data-aj-owner="aj-quote"><button class="aj-action" data-aj-owner="aj-action">Nested independent action</button></div>'`);
    assert.equal((await page.eval(ownershipAuditSource)).ok,true,'legitimate nested action is a separate component, not a skin on the quote root');
    await page.eval(`document.body.innerHTML='<div class="rmtop" id="rmTop"><span>Time</span></div>'`);
    assert.ok((await page.eval(ownershipAuditSource)).missing.some(n=>n.id==='rmTop'),'technical selection scope needs a directly owned root, not only owned child buttons');
    await page.eval(`document.querySelector('#rmTop').dataset.ajOwner='aj-reply'`);
    assert.equal((await page.eval(ownershipAuditSource)).ok,true);
    await page.eval(`document.body.innerHTML='<button class="opt" data-aj-owner="aj-choice">Option</button>'`);
    await page.eval(`document.querySelector('.opt').removeAttribute('data-aj-owner')`);
    assert.equal((await page.eval(ownershipAuditSource)).missing.length,1);
    await page.eval(`document.querySelector('.opt').setAttribute('data-aj-owner','web')`);
    assert.equal((await page.eval(ownershipAuditSource)).missing.length,1, 'generic unregistered owner cannot hide missing ownership');
    await page.eval(`document.body.innerHTML='<a class="aj-action" href="#" data-aj-owner="aj-action" style="display:block;width:48px;height:140px;white-space:normal">Long wrapped action anchor</a>'`);
    assert.equal((await page.eval(buttonAuditSource)).length,1,'renamed action anchor still receives original 1.5x height audit');
    await page.eval(`document.body.innerHTML='<div class="ajmodal"><button class="aj-action" data-aj-owner="aj-action">Confirm</button></div>'`);
    assert.equal((await page.eval(ownershipAuditSource)).conflicts.length,1,'dialog action context cannot depend on ancestor styling');
    await page.eval(`document.querySelector('button').setAttribute('data-aj-action-context','dialog')`);
    assert.equal((await page.eval(ownershipAuditSource)).ok,true);
    await page.eval(`document.body.innerHTML='<div contenteditable="true"></div><audio controls></audio><video controls></video><iframe sandbox></iframe>'`);
    assert.equal((await page.eval(ownershipAuditSource)).missing.length,4,'editable, native media and opaque frame hosts require ownership without tabindex');
    await page.eval(`document.body.innerHTML='<section id="rd"><div class="rdacts" data-aj-owner="aj-reader"><button data-aj-owner="aj-reply">Reader action</button></div></section><div id="tray"><div class="chip"><button class="x" data-aj-owner="aj-reply">Remove</button></div></div><div id="menu"><button data-aj-owner="aj-reply">Command</button></div><div id="sug"><button data-aj-owner="aj-reply">Suggestion</button></div><div id="sheet"><div class="qfree"><textarea data-aj-owner="aj-reply"></textarea></div></div>'`);
    const wrong = await page.eval(ownershipAuditSource);
    assert.equal(wrong.missing.length,0,'all deliberately wrong owners are registered namespaces');
    assert.equal(wrong.semanticFailures.length,5,'known reply owner must not own reader, attachment, menu, suggestion or answer controls');
    assert.equal(wrong.ok,false);
    await page.eval(`document.querySelector('#rd button').dataset.ajOwner='aj-reader';document.querySelector('#tray button').dataset.ajOwner='aj-attachment';document.querySelector('#menu button').dataset.ajOwner='aj-menu';document.querySelector('#sug button').dataset.ajOwner='aj-suggestion';document.querySelector('#sheet textarea').dataset.ajOwner='aj-choice'`);
    assert.equal((await page.eval(ownershipAuditSource)).ok,true,'the original semantic contracts, rather than observed metadata, decide the correct repair');
    const html=readFileSync(new URL('../public/index.html',import.meta.url),'utf8');
    const body=/<body[^>]*>([\s\S]*)<\/body>/.exec(html)[1];
    await page.eval('document.body.innerHTML='+JSON.stringify(body));
    const staticAudit=await page.eval(ownershipAuditSource);
    assert.deepEqual(staticAudit.missing,[],'actual static HTML focus and typography nodes have explicit known owners');
    assert.deepEqual(staticAudit.semanticFailures,[],'actual static HTML follows the normative semantic contracts');
    const relay=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
    const readerEnd=relay.search(/\n(?:async\s+)?function rdCopy\(\)\s*\{/);
    assert.ok(readerEnd>relay.indexOf('const RD_ACTS ='),'actual reader constructor ends at the complete sync/async copy declaration');
    const readerConstructor=relay.slice(relay.indexOf('const RD_ACTS ='),readerEnd);
    await page.eval(`{const el=id=>document.getElementById(id);const glyph=()=>document.createElement('span');${readerConstructor};rdBuildActs();}`);
    const cloneOwners=await page.eval(`Array.from(document.querySelector('#rdActs').children,b=>({id:b.id,owner:b.dataset.ajOwner}))`);
    assert.equal(cloneOwners.length,6,'actual reader constructor creates all six original footer actions');
    assert.ok(cloneOwners.every(b=>b.owner==='aj-reader'),'cloning reply business state does not clone its visual owner');
    await page.eval(`document.getElementById('speakBtn').setAttribute('disabled','');document.getElementById('speakBtn').setAttribute('data-state','playing');`);
    const mirrored=await page.eval(`({disabled:document.getElementById('rdSpeakBtn').disabled,state:document.getElementById('rdSpeakBtn').getAttribute('data-state'),owner:document.getElementById('rdSpeakBtn').dataset.ajOwner})`);
    assert.deepEqual(mirrored,{disabled:true,state:'playing',owner:'aj-reader'},'real MutationObserver still mirrors business state and preserves reader ownership');
    assert.deepEqual((await page.eval(ownershipAuditSource)).semanticFailures,[]);
    await page.close();
  } finally { await browser.close(); }
});
