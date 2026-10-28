// P426 #170: isolated Chromium — the 「我的设备」 group on the friends page: own row under My devices, group view (who sent
// each message, to whom), @ device-name completion, and a real WebCrypto-signed fr_gsay. Screenshots: reports/qa/p426/friends/.
import { mkdirSync, writeFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost, sampleFriends } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';
import { parityRecorder } from '../../parity/lib.mjs';
const out = 'reports/qa/p426/friends/'; mkdirSync(out, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), br = await launch();
const failures = []; let checks = 0;
const check = (v, s) => { checks++; if (!v) failures.push(s); };
const shot = async (p, name) => { const { data } = await p.send('Page.captureScreenshot', { format: 'png' }); writeFileSync(out + name + '.png', Buffer.from(data, 'base64')); };
try {
  for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) {
    const tag = `${lang}-${scheme}`;
    fake.reset(); fake.st.prefs = { appearance: { language: lang, theme: scheme } };
    const p = await newPage(br, 390, 844, scheme, { allow: [web.url, fake.relay + '/'] });
    const base = web.url + (lang === 'en' ? '?lang=en' : '');
    await navigate(p, base); await waitState(p, 'idle'); await navigate(p, 'about:blank');
    await navigate(p, fake.newPairing(base)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await evaluate(p, `document.getElementById('a2hs-ok')?.click();document.getElementById('confirm-no')?.click()`);
    const F = fake.friends(sampleFriends());
    const now = Date.now();
    Object.assign(F.friends[0], { name: 'Mac M1', owner: '', same_owner: 'active' });
    Object.assign(F.friends[1], { name: 'Warden', owner: '', same_owner: 'active' });
    F.devices = { n: 2, unread: 1, last: { ts: now - 60000, text: lang === 'zh' ? '测试全部通过（182 项）' : 'All 182 tests passed' } };
    const mac = { id: F.friends[0].id, name: 'Mac M1' }, ward = { id: F.friends[1].id, name: 'Warden' };
    F.glog = { me: { id: F.me.id, name: 'Studio' }, members: [mac, ward], more: false, items: [
      { gid: '1'.repeat(16), from: F.me.id, self: true, name: 'Studio', by: 'phone', text: lang === 'zh' ? '@Mac M1 跑一下测试' : '@Mac M1 run the tests', to: [mac], kind: 'msg', ts: now - 300000 },
      { gid: '2'.repeat(16), from: mac.id, self: false, name: 'Mac M1', by: 'agent', text: lang === 'zh' ? '测试全部通过（182 项）' : 'All 182 tests passed', to: [{ id: F.me.id, name: 'Studio' }], kind: 'reply', ts: now - 60000 },
      { gid: '3'.repeat(16), from: ward.id, self: false, name: 'Warden', by: 'phone', text: lang === 'zh' ? '今晚我这台不接大活' : 'No big jobs on this one tonight', to: [], kind: 'msg', ts: now - 30000 },
    ] };
    await navigate(p, web.url + 'friends' + (lang === 'en' ? '?lang=en' : ''));
    await waitFor(p, `!!document.getElementById('fr-open-devices')`);
    check(await evaluate(p, `document.getElementById('fr-list').textContent.includes(${JSON.stringify(lang === 'zh' ? '我的设备' : 'My devices')})`), `${tag}: My devices heading`);
    check(await evaluate(p, `!!document.querySelector('#fr-open-devices .fr-dot')`), `${tag}: unread dot on the group row`);
    check(await evaluate(p, `document.querySelector('#fr-open-devices').closest('li').previousElementSibling.textContent`) === (lang === 'zh' ? '我的设备' : 'My devices'), `${tag}: group row first under My devices`);
    await shot(p, `${tag}-list`);
    await evaluate(p, `document.getElementById('fr-open-devices').click()`);
    await waitFor(p, `!!document.getElementById('fr-gsay') && document.querySelectorAll('#fr-gmsgs li').length===3`);
    const froms = await evaluate(p, `[...document.querySelectorAll('#fr-gmsgs .fr-gfrom')].map(x=>x.textContent)`);
    check(froms[0].includes(lang === 'zh' ? '本机 Studio' : 'Studio (this computer)') && froms[0].includes(lang === 'zh' ? '手机发的' : 'from the phone'), `${tag}: own message shows this computer + phone ${froms[0]}`);
    check(froms[1].startsWith('Mac M1') && froms[1].includes(lang === 'zh' ? '回复' : 'reply'), `${tag}: reply shows the device ${froms[1]}`);
    check(await evaluate(p, `document.querySelector('#fr-gmsgs .fr-gto').textContent`) === (lang === 'zh' ? '交给：Mac M1' : 'Handed to: Mac M1'), `${tag}: who it was handed to`);
    check(await evaluate(p, `document.getElementById('fr-title').textContent`) === (lang === 'zh' ? '我的设备 · 群聊' : 'My devices · group'), `${tag}: title`);
    // @ completion: '@' lists every device + all; '@wa' narrows; picking writes '@Warden ' and keeps the caret after it
    const type = (v) => evaluate(p, `(()=>{const t=document.getElementById('fr-gsay');t.focus();t.value=${JSON.stringify(v)};t.setSelectionRange(t.value.length,t.value.length);t.dispatchEvent(new Event('input',{bubbles:true}));})()`);
    await type('@');
    await waitFor(p, `!document.getElementById('fr-gmentions').hidden`);
    const opts = await evaluate(p, `[...document.querySelectorAll('#fr-gmentions [role=option]')].map(x=>x.textContent)`);
    check(JSON.stringify(opts) === JSON.stringify(['Mac M1', 'Warden', lang === 'zh' ? '所有设备' : 'All devices']), `${tag}: @ lists devices + all ${opts}`);
    await shot(p, `${tag}-mention`);
    await type((lang === 'zh' ? '请' : 'hi ') + '@wa');
    const narrowed = await evaluate(p, `[...document.querySelectorAll('#fr-gmentions [role=option]')].map(x=>x.textContent)`);
    check(JSON.stringify(narrowed) === JSON.stringify(['Warden']), `${tag}: narrowed ${narrowed}`);
    await evaluate(p, `document.querySelector('#fr-gmentions [role=option]').click()`);
    const v = await evaluate(p, `(()=>{const t=document.getElementById('fr-gsay');return [t.value,t.selectionStart,document.activeElement===t,document.getElementById('fr-gmentions').hidden]})()`);
    check(v[0] === (lang === 'zh' ? '请' : 'hi ') + '@Warden ' && v[1] === v[0].length && v[2] && v[3], `${tag}: picked mention ${JSON.stringify(v)}`);
    // keyboard: ArrowDown + Enter picks the second option
    await type('@');
    await evaluate(p, `(()=>{const t=document.getElementById('fr-gsay');for(const k of ['ArrowDown','Enter'])t.dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true}));})()`);
    check(await evaluate(p, `document.getElementById('fr-gsay').value`) === '@Warden ', `${tag}: keyboard pick`);
    const text = '@Mac M1 ' + (lang === 'zh' ? '跑一下测试' : 'run the tests');
    await type(text);
    await evaluate(p, `document.getElementById('fr-gmentions').hidden=true`);
    const before = F.writes.length;
    await evaluate(p, `document.getElementById('fr-gsay-send').click()`);
    for (let i = 0; i < 60 && F.writes.length === before; i++) await sleep(50);
    const w = F.writes.at(-1);
    check(w?.t === 'fr_gsay' && w.text === text && w.sigOk === true, `${tag}: genuine signed fr_gsay ${JSON.stringify(w)}`);
    await waitFor(p, `document.querySelectorAll('#fr-gmsgs li').length===4`);
    check(await evaluate(p, `document.getElementById('fr-gsay').value`) === '', `${tag}: composer cleared`);
    check(await evaluate(p, `document.documentElement.scrollWidth<=innerWidth`), `${tag}: no overflow`);
    check(await evaluate(p, `document.documentElement.getAttribute('data-theme')===${JSON.stringify(scheme)}`), `${tag}: actual theme`);
    await shot(p, `${tag}-group`);
    check(!F.refused.length, `${tag}: nothing refused ${F.refused}`);
    check(!p.problems.length, `${tag}: browser console clean ${p.problems.join(',')}`);
    await p.dispose();
  }
} finally { await br.close(); await fake.stop(); await web.stop(); }
writeFileSync(out + 'result.json', JSON.stringify({ checks, failures }, null, 2) + '\n');
console.log(JSON.stringify({ checks, failures })); if (failures.length) process.exitCode = 1;
{ const rec = parityRecorder('web/test/p426_friends.mjs'); rec.record('p426-my-devices-group', !failures.length); rec.write(); }
