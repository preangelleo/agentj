#!/usr/bin/env node
// P563 追加 3 (贾维斯审查 10-28): when multi-line text goes full screen, ✓ is Save every time — its label, its title and what
// it does — not only when the field is the one thing its form saves. Single-line fields stay inline (P561 追加 13).
// Real Chromium, isolated fake host + local relay (its own headless browser, never :9222):
//   a) a multi-line field among other fields (the contact card's 备注, the real case): ✓ labelled 保存 / Save → the card is
//      saved in one tap (one ct_card_answer ok:true carrying the new note and the other fields), the editor and card close
//   b) the same on a settings form that holds another field: ✓ submits it once, the other field's value included
//   c) a multi-line field that is all its form saves: ✓ submits it once (unchanged); ⌘/Ctrl+Enter is the same Save
//   d) a single-line settings field (mail address): typed in place, never full screen
// zh / en × 390 / 1440. `P563_PHASE=before P563_PUBLIC=<HEAD tree>/web/public` records what the old tree did.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { startWebServer, PUBLIC_DIR } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, evaluate as ev, waitFor, sleep, shoot } from './browser.mjs';

const phase = process.env.P563_PHASE === 'before' ? 'before' : 'after';
const OUT = resolve(process.env.P563_SHOTS || 'reports/qa/p563/fsedit');
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, publicDir: process.env.P563_PUBLIC ? resolve(process.env.P563_PUBLIC) + '/' : PUBLIC_DIR });
const fake = await startFakeHost(), B = await launch();
const FS_OPEN = `!document.getElementById('fsEd').hidden`, FS_SHUT = `document.getElementById('fsEd').hidden`;
let failed = 0, checks = 0;
const notes = [];
const ok = (name, fn) => fn().then(() => { checks++; console.log('  ✓ ' + name); }, (e) => { failed++; console.log('  ✗ ' + name + ': ' + (e && e.stack || e)); });
const typeFs = (p, v) => ev(p, `(()=>{const t=document.getElementById('fsText');t.value=${JSON.stringify(v)};t.dispatchEvent(new Event('input'))})()`);
const checkLabel = (p) => ev(p, `(()=>{const b=document.getElementById('fsSend');return {aria:b.getAttribute('aria-label'),title:b.title,icon:b.textContent.trim()}})()`);

try {
  for (const lang of ['zh', 'en']) for (const [w, h] of [[390, 844], [1440, 1000]]) {
    const tag = `${lang}-${w}`, SAVE = lang === 'zh' ? '保存' : 'Save';
    await ok('✓ = Save ' + tag, async () => {
      fake.reset();
      fake.st.caps = ['contacts'];
      fake.st.prefs = { appearance: { language: lang, theme: 'dark' } };
      const got = [];
      fake.st.onApp = async (c, m) => { if (!/^ct_/.test(m.t)) return false; got.push(m); return true; };
      const p = await newPage(B, w, h, 'dark', { touch: w < 500, allow: [web.url, fake.relay + '/'] });
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
        await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
        await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);

        // a) the contact card: 备注 is one field among name / nicknames / e-mail / phone / org
        const id = 'ab'.repeat(16);
        await fake.send({ t: 'ct_card', id, n: 'n1', mode: 'new', contact: { name: '赵六', emails: [{ v: 'zhao.liu@example.com' }] }, base: null, why: '', ttl: 600 });
        await waitFor(p, `!!document.getElementById('ctCard') && !document.getElementById('ctCard').hidden && document.getElementById('ct-f-name')?.value==='赵六'`);
        await ev(p, `document.querySelector('#ct-f-note + .text-editor-field').click()`); await waitFor(p, FS_OPEN);
        const lab = await checkLabel(p);
        await typeFs(p, '周三下午方便\nprefers e-mail');
        writeFileSync(`${OUT}/3-fsedit-contact-note-${tag}-${phase}.png`, await shoot(p, ''));
        await ev(p, `document.getElementById('fsSend').click()`); await sleep(400);
        const a = got.filter((m) => m.t === 'ct_card_answer');
        const cardOpen = await ev(p, `!document.getElementById('ctCard').hidden`);
        if (phase === 'before') notes.push(`${tag}: contact 备注 ✓ label=${lab.aria} → ct_card_answer sent=${a.length}, card still open=${cardOpen}`);
        else {
          assert.deepEqual(lab, { aria: SAVE, title: SAVE, icon: '✓' }, '✓ is labelled Save');
          assert.ok(await ev(p, FS_SHUT), 'editor closed');
          assert.equal(a.length, 1, 'one tap on ✓ saved the card');
          assert.equal(a[0].ok, true);
          assert.equal(a[0].contact.note, '周三下午方便\nprefers e-mail');
          assert.equal(a[0].contact.name, '赵六'); assert.equal(a[0].contact.emails[0].v, 'zhao.liu@example.com', 'the other fields go with it');
          await fake.send({ t: 'ct_card_done', id, result: 'saved' });
          await waitFor(p, `document.getElementById('ctCard').hidden`);
        }
        if (phase === 'before') { await ev(p, `document.getElementById('ctCard-no')?.click()`); await sleep(200); }

        // b / c) settings forms (the real settings page has no multi-line field today; the rule is the editor's, so a form is added)
        await ev(p, `document.getElementById('setBtn').click()`); await waitFor(p, `!document.getElementById('settings').hidden`);
        await ev(p, `(()=>{window.p563Sub=[];for(const [fid,other] of [['p563-multi',true],['p563-sole',false]]){const f=document.createElement('form');f.id=fid;
          f.addEventListener('submit',e=>{e.preventDefault();window.p563Sub.push({form:fid,long:f.querySelector('[name=long]').value,other:f.querySelector('[name=other]')?.value??null})});
          if(other){const i=document.createElement('input');i.name='other';i.value='kept';f.append(i)}
          const t=document.createElement('textarea');t.name='long';t.id=fid+'-long';t.value='one';t.setAttribute('aria-label','Long text');f.append(t);
          const s=document.createElement('button');s.type='submit';s.textContent='Save';f.append(s);document.getElementById('settings').append(f)}})()`);
        await waitFor(p, `document.getElementById('p563-multi-long').type==='hidden'&&document.getElementById('p563-sole-long').type==='hidden'`);
        for (const [fid, how] of [['p563-multi', 'click'], ['p563-sole', 'click'], ['p563-multi', 'ctrl-enter']]) {
          await ev(p, `document.querySelector('#${fid} .text-editor-field').click()`); await waitFor(p, FS_OPEN);
          const l2 = await checkLabel(p);
          await typeFs(p, `${fid}\n${how}`);
          if (how === 'click') await ev(p, `document.getElementById('fsSend').click()`);
          else {
            await ev(p, `document.getElementById('fsText').focus()`);
            await p.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Enter', code: 'Enter', modifiers: 2, windowsVirtualKeyCode: 13 });
            await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', modifiers: 2, windowsVirtualKeyCode: 13 });
          }
          await sleep(300);
          const sub = await ev(p, `window.p563Sub.splice(0)`);
          if (phase === 'before') notes.push(`${tag}: ${fid} (${how}) ✓ label=${l2.aria} → form submitted ${sub.length}×`);
          else {
            assert.deepEqual(l2, { aria: SAVE, title: SAVE, icon: '✓' }, fid + ' ✓ is labelled Save');
            assert.ok(await ev(p, FS_SHUT), fid + ' editor closed');
            assert.deepEqual(sub, [{ form: fid, long: `${fid}\n${how}`, other: fid === 'p563-multi' ? 'kept' : null }], `${fid} (${how}): submitted once`);
          }
          if (await ev(p, FS_OPEN)) await ev(p, `document.getElementById('fsExit').click()`);
        }
        await ev(p, `for(const id of ['p563-multi','p563-sole'])document.getElementById(id).remove()`);

        // d) single-line: inline, no full screen
        await ev(p, `document.getElementById('mail-addr').click()`); await sleep(150);
        const inline = await ev(p, `${FS_SHUT}&&!document.getElementById('mail-addr').readOnly&&!document.getElementById('mail-addr').dataset.textField`);
        if (phase === 'before') notes.push(`${tag}: single-line mail address inline=${inline}`);
        else assert.ok(inline, 'single-line field stays inline');
        if (phase === 'after') assert.equal(p.problems.length, 0, 'page errors: ' + p.problems.join(' | '));
      } finally { await p.dispose(); }
    });
  }
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
for (const n of notes) console.log('  ' + n);
console.log(failed ? `p563-fsedit (${phase}): ${failed} FAILED` : `p563-fsedit (${phase}): ${checks} passed → ${OUT}`);
process.exit(failed ? 1 : 0);
