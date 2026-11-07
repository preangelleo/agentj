// #427 · Settings → AI 账号 on the real page against the local fake host (synthetic view: Claude 「主号」 + 「备用」, one Codex
// account): the list (current chip, method, quota or 「—」), 「设为当前」 → acct_use + the toast, the current account's delete
// disabled with its note, rename (inline field, Esc puts it away without leaving the page), delete = acct_del at once and the
// host's own confirmation card is the only confirmation (no page sheet; the page waits; a 'denied' answer keeps the row), 添加账号 → name rules → auth_start {kind, route, account:{name}} → the flow card on this page → done →
// the list re-read, view.conflict → Claude's 「设为当前」 greyed with the reason (variable names only) right below it, 44 px targets, no horizontal overflow, no console errors.
// 390 / 1440 × light / dark × zh / en → reports/qa/harness-multi-account/web-*.png (+ web-phone.json).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep, key } from './browser.mjs';

const out = process.env.ACCT_OUT || 'reports/qa/harness-multi-account';
mkdirSync(out, { recursive: true });
const $ = (id) => `document.getElementById('${id}')`;
const URL_ = 'https://claude.com/cai/oauth/authorize?code=true&client_id=x&state=s1';
const NOW = Math.floor(Date.now() / 1000);
const L = {
  zh: { main: '主号', backup: '备用', codex: '工作', use: '设为当前', current: '当前', ren: '改名', del: '删除', add: '添加账号', dash: '额度 —', toast: '主 Agent 下一条消息起用这个账号', added: '已添加', empty: '名字不能为空', dup: '这个名字已经用过了', noOver: '不会覆盖已有的账号', conflict: 'ANTHROPIC_API_KEY' },
  en: { main: 'Main', backup: 'Backup', codex: 'Work', use: 'Make current', current: 'Current', ren: 'Rename', del: 'Delete', add: 'Add account', dash: 'Quota —', toast: 'uses it from its next message', added: 'Added', empty: "can't be empty", dup: 'already used', noOver: 'never replaces an existing one', conflict: 'ANTHROPIC_API_KEY' },
};
function sample(s) {
  return {
    claude: { active: 'c1a1c1a1', items: [
      { id: 'c1a1c1a1', name: s.main, method: 'token', added: NOW - 86400 * 9, quota: { pct: 96, window: 'h5', at: NOW - 120 } },
      { id: 'c2b2c2b2', name: s.backup, method: 'token', added: NOW - 3600, quota: { pct: 12, window: 'week', at: NOW - 86400 } }] },
    codex: { active: 'd3c3d3c3', items: [{ id: 'd3c3d3c3', name: s.codex, method: 'device', added: NOW - 86400 * 2, quota: null }] },
    conflict: [],
  };
}
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const checks = [], shots = [];
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const lang of ['zh', 'en']) {
    const s = L[lang], tag = `${lang}-${theme}-${width}`;
    fake.reset(); fake.setUp(true);
    let view = sample(s), authView = { dot: false, flow: null, harnesses: [] };
    const seen = []; let holdDel = null;   // acct_del waits here like the host's confirmation card
    fake.st.onApp = async (c, m, send) => {
      if (String(m.t).startsWith('acct_')) {
        seen.push({ ...m }); let ok = true, why;
        const h = view[m.harness];
        if (m.t === 'acct_use') { if (h.items.some((i) => i.id === m.id)) view = { ...view, [m.harness]: { ...h, active: m.id } }; else { ok = false; why = 'not_found'; } }
        if (m.t === 'acct_rename') view = { ...view, [m.harness]: { ...h, items: h.items.map((i) => i.id === m.id ? { ...i, name: m.name } : i) } };
        if (m.t === 'acct_del') { const allow = await new Promise((res) => { holdDel = res; }); holdDel = null; if (!allow) { ok = false; why = 'denied'; } else if (h.active === m.id) { ok = false; why = 'active'; } else view = { ...view, [m.harness]: { ...h, items: h.items.filter((i) => i.id !== m.id) } }; }
        await send({ t: 'acct_res', r: m.r, ok, ...(why ? { why } : {}), view });
        return true;
      }
      if (String(m.t).startsWith('auth_')) {
        seen.push({ ...m });
        if (m.t === 'auth_start') authView = { ...authView, flow: { kind: m.kind, route: m.route, stage: 'url', url: URL_, expires_at: NOW + 600, account: m.account.name } };   // the host's flow names the account as a string
        if (m.t === 'auth_cancel') authView = { ...authView, flow: null };
        await send({ t: 'auth_res', r: m.r, ok: true, view: authView });
        return true;
      }
      return false;
    };
    const p = await newPage(B, width, width === 390 ? 844 : 1000, theme, { touch: width === 390, allow: [web.url, fake.relay + '/'] });
    const save = async (name) => { const f = resolve(out, `web-${tag}-${name}.png`); writeFileSync(f, await shoot(p, '')); shots.push(f); };
    const rowSel = (h, name) => `[...document.querySelectorAll('#acct-list [data-kind=${h}] .acctrow')].find(r=>r.querySelector('.acctname').textContent===${JSON.stringify(name)})`;
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}'`);
      await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`);
      await evaluate(p, `${$('set-accts-nav')}.scrollIntoView({block:'center'})`);
      if (width === 390 && theme === 'dark') await save('nav');
      await evaluate(p, `${$('set-accts-nav')}.click()`);
      await waitFor(p, `!${$('set-accts')}.hidden && document.querySelectorAll('#acct-list .acctrow').length===3`);
      assert.equal(seen[0].t, 'acct_get');

      // the list: current chip on 主号 and on the Codex account, quota or 「—」, the current account's delete disabled + note
      const list = await evaluate(p, `[...document.querySelectorAll('#acct-list .acctrow')].map(r=>({name:r.querySelector('.acctname').textContent,cur:!!r.querySelector('.acctcur'),meta:r.querySelector('.acctmeta').textContent,del:r.querySelector('.acctdel').disabled,use:!!r.querySelector('.acctuse')}))`);
      assert.deepEqual(list.map((r) => [r.name, r.cur, r.del, r.use]), [[s.main, true, true, false], [s.backup, false, false, true], [s.codex, true, true, false]]);
      assert.match(list[0].meta, /96/); assert.match(list[1].meta, /12/); assert.ok(list[2].meta.includes(s.dash), list[2].meta);
      assert.ok(await evaluate(p, `!!${rowSel('claude', s.main)}.querySelector('.acctdelnote')`), 'why the delete is off');
      assert.equal(await evaluate(p, `document.querySelectorAll('#acct-list .acctconflict').length`), 0);
      await evaluate(p, `${$('set-accts')}.scrollTop=0`);
      await save('list');

      // 设为当前 → acct_use {harness, id}; toast says next message + background tasks
      await evaluate(p, `${rowSel('claude', s.backup)}.querySelector('.acctuse').click()`);
      await waitFor(p, `!!${rowSel('claude', s.backup)}?.querySelector('.acctcur')`);
      const use = seen.find((m) => m.t === 'acct_use'); assert.equal(use.harness, 'claude'); assert.equal(use.id, 'c2b2c2b2');
      await waitFor(p, `(${$('toast')}?.textContent||'').includes(${JSON.stringify(s.backup)})`);
      assert.ok((await evaluate(p, `${$('toast')}.textContent`)).includes(s.toast));
      await save('switched');

      // rename (inline field); Esc (owned by the Settings layer) puts the field away first and stays on the page
      await evaluate(p, `${rowSel('codex', s.codex)}.querySelector('.acctren').click()`);
      await waitFor(p, `!!document.querySelector('#acct-list [data-kind=codex] .acctform input')`);
      await key(p, 'Escape');
      await waitFor(p, `!document.querySelector('#acct-list .acctform')`);
      assert.equal(await evaluate(p, `${$('set-accts')}.hidden`), false, 'Esc closed only the field');
      await evaluate(p, `${rowSel('codex', s.codex)}.querySelector('.acctren').click()`);
      await waitFor(p, `!!document.querySelector('#acct-list [data-kind=codex] .acctform input')`);
      await evaluate(p, `(()=>{const i=document.querySelector('#acct-list [data-kind=codex] .acctform input');i.value='Codex 2';i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))})()`);
      await waitFor(p, `!!${rowSel('codex', 'Codex 2')}`);
      assert.deepEqual((({ harness, id, name }) => ({ harness, id, name }))(seen.find((m) => m.t === 'acct_rename')), { harness: 'codex', id: 'd3c3d3c3', name: 'Codex 2' });

      // delete 主号 (no longer current): acct_del goes at once, no page sheet; the page waits for the host's card
      const nDel = () => seen.filter((m) => m.t === 'acct_del').length;
      const delOnce = async (allow, shot) => {
        await evaluate(p, `${rowSel('claude', s.main)}.querySelector('.acctdel').click()`);
        await waitFor(p, `${$('acct-msg')}.textContent.includes(${JSON.stringify(s.main)})`);
        for (let i = 0; i < 50 && !holdDel; i++) await sleep(50);
        assert.ok(holdDel, 'acct_del sent without a page sheet');
        assert.equal(await evaluate(p, `${$('confirm')}.hidden`), true, 'no page confirmation sheet');
        assert.equal(await evaluate(p, `${rowSel('claude', s.main)}.querySelector('.acctdel').disabled`), true, 'one removal at a time');
        if (shot) { await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`); await save('delete-wait'); }
        holdDel(allow);
      };
      await delOnce(false, width === 390);
      await waitFor(p, `${$('acct-msg')}.classList.contains('ardanger')`);
      assert.ok(await evaluate(p, `!!${rowSel('claude', s.main)}`), 'denied on the card → the account stays');
      await delOnce(true, false);
      await waitFor(p, `!${rowSel('claude', s.main)}`);
      assert.equal(nDel(), 2, 'denied once, approved once'); assert.ok(seen.filter((m) => m.t === 'acct_del').every((m) => m.id === 'c1a1c1a1'));

      // 添加账号: name rules on the page, then auth_start with account.name; the flow shows here; done → list re-read
      await evaluate(p, `document.querySelector('#acct-list [data-kind=claude] .acctadd').click()`);
      await waitFor(p, `!!document.querySelector('#acct-list [data-kind=claude] .acctform input')`);
      assert.ok((await evaluate(p, `document.querySelector('#acct-list [data-kind=claude] .acctform').textContent`)).includes(s.noOver));
      const submit = (v) => evaluate(p, `(()=>{const f=document.querySelector('#acct-list [data-kind=claude] .acctform');const i=f.querySelector('input');i.value=${JSON.stringify(v)};f.querySelector('.aj-action--primary').click()})()`);
      await submit('   ');
      await waitFor(p, `!document.querySelector('#acct-list [data-kind=claude] .acctform__err').hidden`);
      assert.ok((await evaluate(p, `document.querySelector('#acct-list .acctform__err').textContent`)).includes(s.empty));
      await submit(s.backup.toUpperCase() === s.backup ? s.backup : s.backup.toLowerCase());
      assert.ok((await evaluate(p, `document.querySelector('#acct-list .acctform__err').textContent`)).includes(s.dup));
      if (width === 390) await save('add-name');
      assert.equal(seen.filter((m) => m.t === 'auth_start').length, 0, 'no start before a valid name');
      const NEW = lang === 'zh' ? '工作号' : 'Office';
      await submit(NEW);
      await waitFor(p, `!${$('acct-flow')}.hidden && ${$('acct-flow')}.textContent.includes(${JSON.stringify(NEW)})`);
      const st = seen.find((m) => m.t === 'auth_start');
      assert.deepEqual({ kind: st.kind, route: st.route, account: st.account }, { kind: 'claude', route: 'setup_token', account: { name: NEW } });
      assert.ok(await evaluate(p, `!!${$('acct-flow')}.querySelector('a.arlink')`), 'authorize link on this page');
      assert.equal(await evaluate(p, `document.querySelector('#acct-list [data-kind=claude] .acctadd').disabled`), true, 'one add at a time');
      await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`);
      assert.equal(await evaluate(p, `${$('acct-flow')}.textContent.includes(${JSON.stringify(lang==='zh'?'AI 登录救援':'AI login rescue')})`), false, 'no pointer to the other page');
      await evaluate(p, `${$('set-accts')}.scrollTop=0`);
      await save('add-flow');
      view = { ...view, claude: { ...view.claude, items: [...view.claude.items, { id: 'e4e4e4e4', name: NEW, method: 'token', added: NOW, quota: null }] } };
      const before = seen.filter((m) => m.t === 'acct_get').length;
      authView = { ...authView, flow: { kind: 'claude', route: 'setup_token', stage: 'done', account: NEW } };
      await fake.send({ t: 'auth_rescue', view: authView });
      await waitFor(p, `!!${rowSel('claude', NEW)}`);
      assert.ok(seen.filter((m) => m.t === 'acct_get').length > before, 'list re-read after the login');
      await waitFor(p, `(${$('toast')}?.textContent||'').includes(${JSON.stringify(s.added)})`);

      // view.conflict: Claude's 「设为当前」 greyed, the reason right below it with the variable names only (anything else dropped)
      view = { ...view, conflict: ['ANTHROPIC_API_KEY', 'not a name <b>'] };
      await evaluate(p, `import('./js/accounts.js').then(m=>m.load())`);
      await waitFor(p, `!!document.querySelector('#acct-list .acctconflict')`);
      const cf = await evaluate(p, `document.querySelector('#acct-list .acctconflict').textContent`);
      assert.ok(cf.includes(s.conflict)); assert.ok(!cf.includes('<b>'));
      const cfRow = await evaluate(p, `(()=>{const r=${rowSel('claude', NEW)};const u=r.querySelector('.acctuse'),n=r.querySelector('.acctconflict');return {dis:u.disabled,desc:u.getAttribute('aria-describedby'),id:n.id,after:!!(u.compareDocumentPosition(n)&Node.DOCUMENT_POSITION_FOLLOWING)}})()`);
      assert.ok(cfRow.dis && cfRow.desc === cfRow.id && cfRow.after, JSON.stringify(cfRow));
      const usesBefore = seen.filter((m) => m.t === 'acct_use').length;
      await evaluate(p, `${rowSel('claude', NEW)}.querySelector('.acctuse').click()`); await sleep(200);
      assert.equal(seen.filter((m) => m.t === 'acct_use').length, usesBefore, 'a greyed button sends nothing');
      assert.equal(await evaluate(p, `document.querySelectorAll('#acct-list [data-kind=codex] .acctconflict').length`), 0, 'Codex is not affected');
      authView = { ...authView, flow: null }; await fake.send({ t: 'auth_rescue', view: authView });
      await waitFor(p, `${$('acct-flow')}.hidden`);
      await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`);
      await evaluate(p, `${$('set-accts')}.scrollTop=0`);
      await save('conflict');

      // geometry: every button ≥ 44 px, no sideways overflow, no console errors
      const geom = await evaluate(p, `(()=>{const e=${$('set-accts')};const bs=[...e.querySelectorAll('button')].filter(b=>b.getClientRects().length).map(b=>b.getBoundingClientRect().height);return {overflow:e.scrollWidth>e.clientWidth+1,min:Math.min(...bs),n:bs.length}})()`);
      assert.equal(geom.overflow, false, JSON.stringify(geom)); assert.ok(geom.min >= 44, JSON.stringify(geom));
      assert.deepEqual(p.problems.filter((x) => !/favicon/.test(x)), [], 'console');
      await evaluate(p, `${$('set-back')}.click()`); await waitFor(p, `${$('set-accts')}.hidden`);
      checks.push({ width, theme, lang, ok: true, buttons: geom.n, minButton: geom.min });
    } finally { await p.dispose(); }
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(resolve(out, 'web-phone.json'), JSON.stringify({ checks, shots: shots.map((f) => f.split('/').pop()) }, null, 2) + '\n');
console.log(`#427 AI accounts phone: ${checks.length} viewport/theme/language cases — list, switch + toast, rename + Esc, delete via the host card only (deny keeps it), add → auth_start{account} → flow → re-read, conflict greys Claude 设为当前, ≥44 px, no overflow · ${shots.length} shots → ${out}`);
