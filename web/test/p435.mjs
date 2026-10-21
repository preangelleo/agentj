// Chromium + fake host. Real encrypted IndexedDB, injected historical rows; not physical iPhone evidence.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitFor, waitState, evaluate, sleep, shoot} from './browser.mjs';
const out = new URL('../../../reports/qa/p435/', import.meta.url); mkdirSync(out, {recursive: true});
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const results = {};
try {
  for (const language of ['zh', 'en']) {
    fake.reset();
    const p = await newPage(B, 390, 844, 'light', {touch: true, allow: [web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url + '?lang=' + language)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await sleep(1000);
    // Below threshold: no chunk ever lands. A 0.5s boundary remains valid.
    const buffer = await evaluate(p, `(async () => {
      const r = await import('./js/longrec.js');
      const id = await r.startTake('audio/mp4');
      await r.addChunk(id, 0, 0, new Blob([new Uint8Array(5)]), 4000);
      const absent = await r.segBlob(id, 0, 'audio/mp4');
      await r.dropTake(id);
      const good = await r.startTake('');
      await r.addChunk(good, 0, 0, new Blob([new Uint8Array(200)], {type:'audio/mp4'}), 250);
      await r.addChunk(good, 0, 1, new Blob([new Uint8Array(200)], {type:'audio/mp4'}), 250);
      const present = await r.segBlob(good, 0, 'audio/mp4'); const saved = await r.pending();
      if (saved.length && saved[0].mime !== 'audio/mp4') throw new Error('lost chunk MIME'); await r.dropTake(good);
      return {absent: absent === null, size: present?.size, type: present?.type};
    })()`);
    assert.deepEqual(buffer, {absent: true, size: 400, type: 'audio/mp4'});
    // Historical bug: done:0 previously fell back to wall time; tiny data with a long clock also recovered.
    await evaluate(p, `(async () => {
      const {sealBytes} = await import('./js/store.js');
      const rec = await sealBytes(new Uint8Array(5)), larger = await sealBytes(new Uint8Array(800));
      const db = await new Promise(res => { const q = indexedDB.open('agentj-rec', 1); q.onsuccess = () => res(q.result); });
      await new Promise(res => { const t = db.transaction(['takes','chunks'], 'readwrite');
        for (const [id, ms, bytes] of [['zero', 0, larger], ['tiny', 8000, rec], ['subsecond', 400, larger]]) {
          t.objectStore('takes').put({id, t0: Date.now(), mime: 'audio/mp4', segs: {0: {ms, done:true}}});
          t.objectStore('chunks').put({take:id, seg:0, i:0, ms:8000, rec:bytes});
        } t.oncomplete = res;
      }); db.close();
    })()`);
    await navigate(p, web.url + '?lang=' + language); await waitState(p, 'ready'); await sleep(1600);
    assert.equal(await evaluate(p, `document.getElementById('confirm').hidden`), true, 'no recovery dialog for old empty takes');
    assert.deepEqual(await evaluate(p, `import('./js/longrec.js').then(r => r.pending())`), []);
    // New valid saved take → correct left/right labels; MP4 MIME and .m4a survive restore and upload.
    await evaluate(p, `(async () => { const r = await import('./js/longrec.js'); const id = await r.startTake('audio/mp4;codecs=mp4a.40.2');
      await r.addChunk(id, 0, 0, new Blob([new Uint8Array(2048)], {type:'audio/mp4'}), 2000); await r.closeSeg(id, 0, 2000); })()`);
    await navigate(p, web.url + '?lang=' + language); await waitState(p, 'ready');
    await waitFor(p, `!document.getElementById('confirm').hidden`, 5000);
    assert.equal(await evaluate(p, `document.getElementById('confirm-no').textContent`), language === 'zh' ? '丢弃' : 'Discard');
    assert.equal(await evaluate(p, `document.getElementById('confirm-yes').textContent`), language === 'zh' ? '放回附件栏' : 'Put them back');
    writeFileSync(new URL(`recover-${language}.png`, out), await shoot(p, ''));
    await evaluate(p, `document.getElementById('confirm-yes').click()`);
    await waitFor(p, `document.querySelectorAll('#tray .chip[data-st="ready"]').length === 1`, 8000);
    const blob = [...fake.st.blobs.values()].at(-1);
    assert.equal(blob.mime, 'audio/mp4'); assert.match(blob.name, /\.m4a$/); assert.equal(blob.origin, 'recording');
    // A separately picked 5-byte audio attachment cannot upload/send; the inline notice is bilingual.
    const before = fake.st.opens.length;
    await evaluate(p, `(() => { const d = new DataTransfer(); d.items.add(new File([new Uint8Array(5)], 'empty.webm', {type:'audio/webm'}));
      const i = document.querySelector('input[type=file]'); i.files = d.files; i.dispatchEvent(new Event('change', {bubbles:true})); })()`);
    await sleep(200);
    assert.equal(fake.st.opens.length, before);
    assert.ok(await evaluate(p, `document.body.textContent.includes(${JSON.stringify(language === 'zh' ? '录音太短，没有发出' : 'Recording too short. Not sent.')})`));
    // Owner-only controls: cancelling sends nothing; confirming sends one valid signature.
    const controls = [];
    fake.st.onApp = async (c, m, send) => {
      if (m.t !== 'estop' && !(m.t === 'rescue_do' && m.action === 'restart_session')) return false;
      const ok = await fake.verifyControl(c, m.t, m, m.t === 'estop' ? {} : {action:'restart_session'});
      controls.push({type:m.t, ok});
      await send(m.t === 'estop' ? {t:'ctl_res',r:m.r,ok} : {t:'reset_done',r:m.r,flow:'restart_session',ok,text:'Session ready'});
      return true;
    };
    await evaluate(p, `document.getElementById('slashBtn').click()`);
    assert.equal(await evaluate(p, `document.querySelectorAll('[data-estop="stop"]').length`),0);
    await evaluate(p, `import('./js/settings.js').then(s=>s.open())`);
    await evaluate(p, `document.getElementById('set-estop').click()`);
    await waitFor(p, `!document.getElementById('confirm').hidden`,3000);
    assert.ok(await evaluate(p, `document.getElementById('confirm-text').textContent.includes(${JSON.stringify(language==='zh'?'定时任务':'scheduled tasks')})`));
    await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
    await waitFor(p, `document.getElementById('confirm').hidden`,3000);
    assert.equal(controls.length,0,'Esc cancels emergency stop');
    await evaluate(p, `document.getElementById('set-estop').click(); document.getElementById('confirm-yes').click()`);
    await waitFor(p, `document.getElementById('confirm').hidden`,3000); await sleep(150);
    assert.deepEqual(controls,[{type:'estop',ok:true}]);
    await evaluate(p, `import('./js/settings.js').then(s=>s.openRescue())`);
    assert.equal(await evaluate(p, `document.getElementById('rs-reset').nextElementSibling.id`),'rs-session');
    await evaluate(p, `document.getElementById('rs-session-go').click()`);
    await waitFor(p, `!document.getElementById('confirm').hidden`,3000);
    await evaluate(p, `document.getElementById('confirm-no').click()`);
    assert.equal(controls.length,1,'cancel does not restart');
    await evaluate(p, `document.getElementById('rs-session-go').click(); document.getElementById('confirm-yes').click()`);
    await sleep(250);
    assert.deepEqual(controls,[{type:'estop',ok:true},{type:'rescue_do',ok:true}]);
    writeFileSync(new URL(`safety-${language}.png`,out), await shoot(p,''));
    results[language] = 'PASS: pre-persistence floor, legacy cleanup/no dialog, bilingual recovery, MP4 MIME, tiny attachment blocked';
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(new URL('browser.json', out), JSON.stringify(results, null, 2) + '\n');
console.log(results);

if (process.env.AJ_PARITY_OUT) writeFileSync(process.env.AJ_PARITY_OUT, JSON.stringify({suite:'web/test/p435.mjs', results:{'p435-empty-recording':'pass'}}));
