// P417 #186 (live outage, Leo 10-21 flow) targeted Chromium. The starting phone: one signed tap on the friends page is its
// consent — the SAME page shows the read-only code and 「等待另一台确认」 in place, no card, no second tap, and flips to
// 「已是同一个主人」 when the host says so; declined / expired / ended rounds say why and what next. The other side's
// phone: the card with the same code and one confirm (order line), shown in front of Settings; a stale card closes with
// its reason; old `ended` and new `gone` both close cards. Local paired fake host only.
import { mkdirSync, writeFileSync, readdirSync, rmSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost, sampleFriends } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep, shoot } from './browser.mjs';
const out = 'reports/qa/p417-same-owner/'; mkdirSync(out, { recursive: true });
for (const f of readdirSync(out)) if (f.endsWith('.png')) rmSync(out + f);
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), br = await launch();
const failures = []; let checks = 0;
const check = (v, s) => { checks++; if (!v) failures.push(s); };
const T = {
  zh: { wait: '等待另一台确认', hint: '你这边已经确认', reaching: '正在联系另一台 Agent J 主机', done: '已是同一个主人', turn: '另一台已经发起',
    declined: '被拒绝了', expired: '已过期', ended: '对方已取消同一个主人', order: '点一次确认就完成', stale: '这张卡已经过时',
    phone: '这台手机没能确认', old: '还是旧版本', closed: '这张卡已处理', start: '这一页会显示一串六位数字' },
  en: { wait: 'Waiting for the other Agent J host to confirm', hint: 'You have confirmed on this side', reaching: 'Reaching the other Agent J host',
    done: 'Already Same owner', turn: 'The other side started this', declined: 'was declined', expired: 'so it expired',
    ended: 'The other side ended Same owner', order: 'one tap here finishes it', stale: 'This card is out of date',
    phone: 'This phone could not confirm', old: 'older Agent J', closed: 'This card is done', start: 'this page shows a six-digit code' },
};
const ok = (pr) => pr.then(() => true, () => false);
const toastHas = (p, s) => ok(waitFor(p, `document.getElementById('toast').textContent.includes(${JSON.stringify(s)})`, 4000));
const mainHas = (p, s) => ok(waitFor(p, `(document.getElementById('fr-so')?.textContent || '').includes(${JSON.stringify(s)})`, 4000));
try {
  for (const lang of ['zh', 'en']) {
    const L = T[lang];
    fake.reset();
    const p = await newPage(br, 390, 844, 'light', { allow: [web.url, fake.relay + '/'] });
    const base = web.url + (lang === 'en' ? '?lang=en' : '');
    await navigate(p, base); await waitState(p, 'idle'); await navigate(p, 'about:blank');
    await navigate(p, fake.newPairing(base)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    const dismiss = () => evaluate(p, `document.getElementById('a2hs-ok')?.click(); document.getElementById('confirm-no')?.click(); document.getElementById('phone-env-dismiss')?.click();`);
    await dismiss();
    const F = fake.friends(sampleFriends()), fid = F.friends[0].id, fr = F.friends[0];
    const noCard = () => evaluate(p, `import('./js/snap.js').then((m) => !m.cardIds().length && !document.getElementById('frOwnerAllow'))`);
    const hostSays = async (fields) => { Object.assign(fr, fields); await fake.send({ t: 'fr_changed', friend: fid }); };

    // ---- 1. the starting phone: Settings → friends → details → 「同一个主人」 → one signed tap, the code shows in place
    await navigate(p, base.replace('?lang=en', '') + 'friends' + (lang === 'en' ? '?lang=en' : ''));
    await waitFor(p, `!!document.querySelector('#fr-list .fr-item')`); await dismiss();
    await evaluate(p, `document.querySelector('#fr-list .fr-item').click()`);
    await waitFor(p, `!!document.getElementById('fr-open-detail')`);
    await evaluate(p, `document.getElementById('fr-open-detail').click()`);
    await waitFor(p, `!!document.querySelector('#fr-group option[value="same-owner"]')`);
    await evaluate(p, `const e=document.getElementById('fr-group');e.value='same-owner';e.dispatchEvent(new Event('change',{bubbles:true}))`);
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    check(await evaluate(p, `document.getElementById('confirm').textContent.includes(${JSON.stringify(L.start)})`), `${lang}: start sheet says the code shows on this page`);
    await evaluate(p, `document.getElementById('confirm-yes').click()`);
    for (let j = 0; j < 60 && !F.writes.length; j++) await sleep(50);
    check(F.writes.at(-1)?.sigOk === true && F.writes.at(-1)?.op === 'group' && F.writes.at(-1)?.value === 'same-owner', `${lang}: start is one signed write`);
    // the other computer has not answered yet: waiting, no code
    await hostSays({ same_owner: 'pending', same_owner_code: '', same_owner_wait: true });
    check(await mainHas(p, L.reaching), `${lang}: before the other computer answers: reaching it`);
    // its half arrived: the code, read-only, on the same page
    await hostSays({ same_owner: 'pending', same_owner_code: '360442', same_owner_wait: true });
    check(await ok(waitFor(p, `document.getElementById('fr-so-code')?.textContent === '360442' && !document.querySelector('#fr-so input')`, 4000)), `${lang}: read-only code on the page`);
    check(await mainHas(p, L.wait), `${lang}: page shows 「等待另一台确认」 in place`);
    check(await mainHas(p, L.hint), `${lang}: page says where to confirm`);
    check(await evaluate(p, `document.body.dataset.view === 'friends'`) && await noCard(), `${lang}: starter stays on the page, gets no card`);
    check(F.writes.length === 1, `${lang}: no second tap needed on the starting side`);
    writeFileSync(out + `${lang}-starter-waiting.png`, await shoot(p));
    // the other side confirmed once → live flip
    await hostSays({ same_owner: 'active', same_owner_code: undefined, same_owner_wait: undefined });
    check(await mainHas(p, L.done), `${lang}: page flips to 「已是同一个主人」`);
    check(await evaluate(p, `!document.getElementById('fr-so-code')`), `${lang}: code gone once done`);
    writeFileSync(out + `${lang}-starter-done.png`, await shoot(p));
    // ended rounds say why + next step
    for (const end of ['declined', 'expired', 'ended']) {
      await hostSays({ same_owner: 'none', same_owner_end: end });
      check(await mainHas(p, L[end]), `${lang}: ${end} round explained on the page`);
      if (end === 'declined') writeFileSync(out + `${lang}-starter-declined.png`, await shoot(p));
    }
    // a host started on the other side: this page shows the code and sends the owner to the card
    await hostSays({ same_owner: 'pending', same_owner_code: '360442', same_owner_wait: false, same_owner_end: undefined });
    check(await mainHas(p, L.turn) && await evaluate(p, `document.getElementById('fr-so-code')?.textContent === '360442'`), `${lang}: invited side page: code + confirm on the card`);
    // an older host without the code fields keeps its old wording
    await hostSays({ same_owner: 'pending', same_owner_code: undefined, same_owner_wait: undefined });
    check(await mainHas(p, lang === 'zh' ? '等待对方确认' : 'Waiting for the other device'), `${lang}: older host keeps the old pending text`);
    await hostSays({ same_owner: 'none', same_owner_end: undefined });

    // ---- 2. the other side's phone, Settings open: the card comes in front with the code, one confirm finishes it
    await navigate(p, base); await waitState(p, 'ready'); await dismiss();
    const pairing = (id) => fake.ask({ t: 'ask', id, tool: 'peer_question', summary: 'Same owner pairing', ttl: 86400, cat: ['send'],
      pq: { friend: fid, name: 'Kai', pairing: true, code: '360442', challenge: 'a'.repeat(32) } });
    const open = () => ok(waitFor(p, `!!document.getElementById('frOwnerAllow')`, 5000));
    const closed = () => ok(waitFor(p, `import('./js/snap.js').then((m) => !m.cardIds().length && !document.getElementById('frOwnerAllow'))`, 5000));
    await evaluate(p, `import('./js/settings.js').then((m) => m.open())`);
    await waitFor(p, `!document.getElementById('settings').hidden`);
    const card = '1'.repeat(32);
    await pairing(card);
    check(await ok(waitFor(p, `document.getElementById('settings').hidden && !!document.getElementById('frOwnerAllow')`, 5000)), `${lang}: pairing card in front of Settings`);
    check(await evaluate(p, `document.querySelector('.fr-owner-code')?.textContent === '360442' && document.getElementById('frAsk').textContent.includes(${JSON.stringify(L.order)})`), `${lang}: card shows code + one-tap order`);
    writeFileSync(out + `${lang}-other-card-over-settings.png`, await shoot(p));
    const before = F.writes.length;
    await evaluate(p, `document.getElementById('frOwnerAllow').click()`);
    for (let j = 0; j < 60 && F.writes.length === before; j++) await sleep(50);
    check(F.writes.at(-1)?.sigOk === true && F.writes.at(-1)?.op === 'owner-confirm' && F.writes.at(-1)?.value === 'a'.repeat(32), `${lang}: one signed confirm`);
    await fake.send({ t: 'ask_done', id: card, result: 'gone' });
    check(await closed(), `${lang}: card closes on gone`);
    check(await toastHas(p, L.closed), `${lang}: closed card says what happens next`);

    // any other 「请你点头」 card is in front of Settings too
    await evaluate(p, `import('./js/settings.js').then((m) => m.open())`);
    await waitFor(p, `!document.getElementById('settings').hidden`);
    const q = '5'.repeat(32);
    await fake.ask({ t: 'ask', id: q, tool: 'peer_question', summary: '好友任务：检查后台进展', ttl: 120, cat: ['send'], pq: { friend: fid, name: 'Kai', text: '检查后台进展', draft: '' } });
    check(await ok(waitFor(p, `document.getElementById('settings').hidden && !!document.getElementById('frQSend')`, 5000)), `${lang}: approval card in front of Settings`);
    await fake.send({ t: 'ask_done', id: q, result: 'deny' });
    await waitFor(p, `!document.getElementById('frQSend')`, 5000).catch(() => {});

    // ---- 3. a 0.25.0a1 host closes cards with `ended`
    const old = '2'.repeat(32);
    await pairing(old);
    check(await open(), `${lang}: card for old host opens`);
    await fake.send({ t: 'ask_done', id: old, result: 'ended' });
    check(await closed(), `${lang}: ask_done ended closes the card`);

    // ---- 4. a stale card (an older round's code): the tap says why, and the card closes itself
    const stale = '3'.repeat(32);
    await pairing(stale);
    check(await open(), `${lang}: stale card opens`);
    F.why = 'expired';
    await evaluate(p, `document.getElementById('frOwnerAllow').click()`);
    check(await toastHas(p, L.stale), `${lang}: stale confirm says why and what next`);
    writeFileSync(out + `${lang}-stale.png`, await shoot(p));
    check(await closed(), `${lang}: stale card closes itself`);

    // ---- 5. refusals keep the card and say what to do
    for (const [why, say] of [['phone_required', L.phone], ['peer_old', L.old]]) {
      const id = (why === 'peer_old' ? '6' : '4').repeat(32);
      await pairing(id);
      check(await open(), `${lang}: ${why} card opens`);
      F.why = why;
      await evaluate(p, `document.getElementById('${why === 'peer_old' ? 'frOwnerAllow' : 'frOwnerDeny'}').click()`);
      check(await toastHas(p, say), `${lang}: ${why} says what to do`);
      check(await open(), `${lang}: ${why} card stays with its buttons`);
      await fake.send({ t: 'ask_done', id, result: 'gone' });
      check(await closed(), `${lang}: ${why} card closes on gone`);
    }
    await p.dispose();
  }
} finally { await br.close(); await fake.stop(); await web.stop(); }
writeFileSync(out + 'result.json', JSON.stringify({ checks, failures }, null, 2) + '\n');
console.log(JSON.stringify({ checks, failures })); if (failures.length) process.exitCode = 1;
