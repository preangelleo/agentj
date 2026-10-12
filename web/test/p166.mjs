// P166 — any file from the 「文件」 button (audio, Office, unknown types) and the iPhone shortcut's Files-app handoff
// (`?from=share&via=file` → 「选择分享的文件」 opens the picker inside the same tap). Real Chromium + Noise fake host.
// Not covered here: the iOS share sheet, Shortcuts' Save File action and the iOS document picker (pending on a real iPhone).
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync, readFileSync, mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitFor, waitState, evaluate, shoot} from './browser.mjs';

const out = new URL('../../../reports/qa/p166/browser/', import.meta.url); mkdirSync(out, {recursive: true});
const docs = new URL('../../site/content/docs/22-share-screenshot/', import.meta.url);
const dir = mkdtempSync(join(tmpdir(), 'p166-'));
const m4a = join(dir, '新录音 3.m4a'), docx = join(dir, '季度报告.docx'), odd = join(dir, 'notes.sketch');
const ff = spawnSync('ffmpeg', ['-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=3',
  '-ac', '1', '-c:a', 'aac', '-b:a', '16k', m4a]);
assert.equal(ff.status, 0, 'ffmpeg makes the synthetic recording');
writeFileSync(docx, Buffer.concat([Buffer.from('PK\x03\x04'), Buffer.alloc(300, 7)]));
writeFileSync(odd, Buffer.from('synthetic unknown format'));
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const posts = []; web.server.on('request', r => { if (r.method === 'POST' && r.url !== '/.aj/rt') posts.push(r.url); });
const results = {};
const fresh = (ids) => [...fake.st.blobs.entries()].filter(([id, b]) => !ids.has(id) && b.bytes).map(([, b]) => b);
async function click(p, id){
  const b = await evaluate(p, `(()=>{const r=document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  await p.send('Input.dispatchMouseEvent', {type: 'mousePressed', button: 'left', clickCount: 1, ...b});
  await p.send('Input.dispatchMouseEvent', {type: 'mouseReleased', button: 'left', clickCount: 1, ...b});
}
try {
  for (const language of ['zh', 'en']){
    const p = await newPage(B, 390, 844, 'light', {touch: true, allow: [web.url, fake.relay + '/']});
    await p.send('DOM.enable');
    await navigate(p, fake.newPairing(web.url + '?lang=' + language)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');

    // 1. the 「文件」 picker takes anything
    assert.equal(await evaluate(p, 'document.getElementById("fDoc").accept'), '*/*');
    let seen = new Set(fake.st.blobs.keys());
    const {root} = await p.send('DOM.getDocument', {depth: -1});
    const {nodeId} = await p.send('DOM.querySelector', {nodeId: root.nodeId, selector: '#fDoc'});
    await p.send('DOM.setFileInputFiles', {nodeId, files: [m4a, docx, odd]});
    await waitFor(p, `document.querySelectorAll('#tray .chip[data-st="ready"]').length===3`, 15000);
    const got = fresh(seen);
    const byName = Object.fromEntries(got.map(b => [b.name, b]));
    assert.match(byName['新录音 3.m4a'].mime, /^audio\/(x-m4a|mp4)$/);
    assert.ok(byName['新录音 3.m4a'].bytes.equals(readFileSync(m4a)), 'exact recording bytes over Noise');
    assert.equal(byName['季度报告.docx'].mime, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
    assert.equal(byName['notes.sketch'].mime, 'application/octet-stream', 'an unknown type still goes');
    const before = fake.st.says.length;
    await evaluate(p, `document.getElementById('input').focus()`);
    await p.send('Input.insertText', {text: language === 'zh' ? '帮我总结这段录音' : 'Summarise this recording'});
    const shot1 = await shoot(p, ''); writeFileSync(new URL(`picker-${language}.png`, out), shot1);
    await click(p, 'send'); await waitFor(p, `document.querySelectorAll('#tray .chip').length===0`);
    assert.equal(fake.st.says.length, before + 1); assert.equal(fake.st.says.at(-1).att.length, 3);
    results['file-picker-any-type-' + language] = 'PASS';

    // 2. the shortcut's Files-app handoff: one tap opens the picker, the file joins the tray, nothing is sent by itself
    await navigate(p, web.url + '?lang=' + language + '&from=share&via=file'); await waitState(p, 'ready');
    await waitFor(p, '!document.getElementById("share-paste").hidden && !document.getElementById("share-paste").disabled');
    const label = await evaluate(p, 'document.getElementById("share-paste").textContent');
    const status = await evaluate(p, 'document.getElementById("share-status").textContent');
    assert.equal(label, language === 'zh' ? '选择分享的文件' : 'Choose the shared file');
    assert.match(status, language === 'zh' ? /「文件」App/ : /Files app/);
    assert.ok(await evaluate(p, 'document.getElementById("share-paste").getBoundingClientRect().height') >= 44);
    const shot2 = await shoot(p, ''); writeFileSync(new URL(`iphone-file-${language}.png`, out), shot2);
    if (process.env.P166_DOC_SHOTS === '1') writeFileSync(new URL(`iphone-file.${language}.png`, docs), shot2);
    await p.send('Page.setInterceptFileChooserDialog', {enabled: true});
    const chooser = p.waitFor('Page.fileChooserOpened');
    seen = new Set(fake.st.blobs.keys());
    await click(p, 'share-paste');
    const opened = await chooser;
    assert.equal(opened.mode, 'selectMultiple');
    await p.send('DOM.setFileInputFiles', {backendNodeId: opened.backendNodeId, files: [m4a]});
    await p.send('Page.setInterceptFileChooserDialog', {enabled: false});
    await waitFor(p, `document.querySelectorAll('#tray .chip[data-st="ready"]').length===1`, 15000);
    assert.equal(fresh(seen).length, 1); assert.equal(fake.st.says.length, before + 1, 'a share never sends by itself');
    results['iphone-files-handoff-' + language] = 'PASS';

    // 3. the clipboard shortcut (P110) keeps its own label
    await navigate(p, web.url + '?lang=' + language + '&from=share'); await waitState(p, 'ready');
    await waitFor(p, '!document.getElementById("share-paste").hidden');
    assert.equal(await evaluate(p, 'document.getElementById("share-paste").textContent'), language === 'zh' ? '粘贴截图' : 'Paste screenshot');
    results['p110-clipboard-label-kept-' + language] = 'PASS';
    assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
  }
  assert.deepEqual(posts, [], 'no upload endpoint: bytes only over the Noise session');
} finally { await B.close(); await fake.stop(); await web.stop(); rmSync(dir, {recursive: true, force: true}); }
writeFileSync(new URL('results.json', out), JSON.stringify({results, scope: 'Real Chromium + Noise fake host; iOS share sheet / Shortcuts Save File / iOS document picker pending on iPhone', serverPosts: posts}, null, 2) + '\n');
console.log(JSON.stringify(results));
if (process.env.AJ_PARITY_OUT) writeFileSync(process.env.AJ_PARITY_OUT, JSON.stringify({suite: 'web/test/p166.mjs', results: Object.fromEntries(Object.entries(results).map(([k, v]) => [k, v.toLowerCase()]))}));
