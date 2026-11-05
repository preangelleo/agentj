// 0.26.9 #472 / §E6 + §E3 (lane w3-cards) — the real phone page against the local fake host (real Noise, own headless Chromium,
// never :9222); no customer machine, no real sudo, no credentials (every id / key / password below is made up).
//   · the sudo / admin-password card (the ufw case), the API-key card, the approval card, the goal opening card and an upgrade
//     confirm all render UNDER the current reply card: inside #reply-notices, between the reply's top and its 阅读 · 朗读 row,
//     in the flow (not position:fixed), the composer uncovered and still reachable; the password field takes typing there
//   · the card names its class and the host's why-scope line when the host sends cls / why_scope; nothing extra without them
//   · something else in front (Settings): the card follows the owner over it; back on the chat it is under the reply again
//   · Settings → 安全 → 预算: hidden until budget_state; spent 「—」 until the host sends it; lowering = one signed budget_set
//     without Face ID; raising = passkey_required → the Face ID step → the SAME signed object again with `fa`
// Shots: 390 × 844 light / dark × zh / en (+ 1440 × 900 zh light / en dark) → reports/qa/p0269-cards/{after|before}/ + INDEX.html
// (P0269_CARDS_OUT overrides the folder). AJ_P0269C_BEFORE=1 AJ_P0269C_PUBLIC=<base public/> = the same scenes on the base build,
// shots only.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readdirSync } from 'node:fs';
import { generateKeyPairSync, randomBytes } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate as ev, navigate, waitFor, waitState, shoot, sleep, key } from './browser.mjs';

const BEFORE = process.env.AJ_P0269C_BEFORE === '1', CHECK = !BEFORE, tag = BEFORE ? 'before' : 'after';
const ROOT = process.env.P0269_CARDS_OUT ? pathToFileURL(process.env.P0269_CARDS_OUT.replace(/\/?$/, '/')) : new URL('../../../reports/qa/p0269-cards/', import.meta.url);
const OUT = new URL(tag + '/', ROOT); mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(process.env.AJ_P0269C_PUBLIC ? { publicDir: process.env.AJ_P0269C_PUBLIC } : {}) });
const fake = await startFakeHost(), B = await launch();
const J = JSON.stringify, hex = (n) => randomBytes(n).toString('hex');
const epk = () => generateKeyPairSync('x25519').publicKey.export({ format: 'jwk' }).x;
const FA = { fa: 'Y3JlZC0x', fa_ch: 'Y2hhbGxlbmdlLWNoYWxsZW5nZS0zMmJ5dGVz' };
const results = [];
const until = async (fn, ms = 8000) => { const t0 = Date.now(); while (!fn()) { if (Date.now() - t0 > ms) return false; await sleep(50); } return true; };
const check = (ok, what) => { results.push({ ok: !!ok, what }); if (CHECK) assert.ok(ok, what); };

// where a card sits: inside the reply's slot, between the reply's top and its button row, in the flow, composer free
const GEO = (sel) => `(()=>{const n=document.querySelector(${J(sel)});if(!n)return null;const r=n.getBoundingClientRect(),slot=document.getElementById('reply-notices'),
  rm=document.getElementById('rm').getBoundingClientRect(),bar=document.getElementById('rmbar').getBoundingClientRect(),comp=document.querySelector('.composer').getBoundingClientRect();
  const hit=document.elementFromPoint(comp.left+comp.width/2,comp.top+Math.min(comp.height/2,30));
  const q=slot.getBoundingClientRect(),acts=[...n.querySelectorAll('#apprBtns:not([hidden]),.confirm-row,.elev-acts,.gdlg-row,.aj-btn--primary:not([hidden]),button[type=submit]:not([hidden])')].filter(a=>a.getClientRects().length&&!a.closest('[hidden]'));
  const a=acts[0]?.getBoundingClientRect();
  return {inSlot:slot.contains(n),pos:getComputedStyle(n).position,top:r.top,bottom:r.bottom,h:r.height,rmTop:rm.top,barTop:bar.top,compTop:comp.top,slotTop:q.top,slotBottom:q.bottom,
    actionOnScreen:!a||(a.top>=q.top-1&&a.bottom<=q.bottom+1),
    composerFree:!!hit&&!!hit.closest('.composer'),slotScroll:slot.scrollHeight>slot.clientHeight}})()`;
async function under(p, sel, name) {
  const g = await ev(p, GEO(sel));
  check(g && g.inSlot, `${name}: inside the reply's notice slot ${J(g)}`);
  check(g && g.pos !== 'fixed', `${name}: in the flow, not a fixed overlay`);
  // the slot (the card's visible frame — a tall card scrolls inside it) lies between the reply's top and its button row
  check(g && g.h > 0 && g.slotTop >= g.rmTop - 1 && g.slotBottom <= g.barTop + 1 && g.bottom > g.slotTop && g.top < g.slotBottom, `${name}: under the reply card's top, above its button row ${J(g)}`);
  check(await ev(p, `(()=>{const n=document.querySelector(${J(sel)}),b=n&&n.querySelectorAll('.cp-host');return !!b&&b.length===1&&getComputedStyle(b[0]).display!=='none'&&b[0].textContent.length>0&&(()=>{const r=b[0].getBoundingClientRect(),q=document.getElementById('reply-notices').getBoundingClientRect();return r.height>0&&r.top>=q.top-1&&r.bottom<=q.bottom+1})()})()`), `${name}: carries the host-only badge, on screen at the top of the card (security r4 F8)`);
  check(g && g.actionOnScreen, `${name}: its decision button is on screen without scrolling ${J(g)}`);
  check(g && g.composerFree, `${name}: the composer is not covered`);
  return g;
}
async function snap(p, name, id) {
  const f = `${name}-${id}.png`;
  writeFileSync(new URL(f, OUT), await shoot(p, ''));
}
const ltCard = (lang) => {
  const id = hex(16), zh = lang === 'zh';
  const card = { schema_version: 1, card_id: id, task_id: 'weekly-brief', revision: 1, brief_digest: 'b'.repeat(64), registry_revision: 3, nonce: hex(16),
    expires_at: new Date(Date.now() + 1800e3).toISOString().replace(/\.\d+Z$/, 'Z'), status: 'needs_input',
    required: [{ id: 'cap-phone-delivery', capability_id: 'phone-delivery', why: { zh: '需要：主人的已配对手机', en: "Needed: the owner's paired phone" },
      how: { zh: '我会自动检查；没通过会告诉你下一步', en: 'I check it automatically and tell you the next step if it fails' },
      reuse: { zh: '配一次，以后同类任务自动复用', en: 'Set up once, reused by similar tasks' }, status: 'unverified', action_kind: 'verify' }], optional: [] };
  const brief = { goal: zh ? '每周一 09:00 生成一份公开资料简报，推到你手机。' : 'Every Monday 09:00, a public-sources brief sent to your phone.',
    acceptance: [zh ? '每条都有可追溯来源' : 'Every item has a traceable source'], red_lines: [zh ? '不付费、不外发' : 'No payments, nothing sent outside'],
    deliverables: [{ path: 'reports/weekly.md', format: 'markdown', audience: 'owner' }],
    plan: { kind: 'weekly', schedule_task_ref: 'weekly-brief/task.json', timezone: 'Asia/Tokyo', human_enable_required: true },
    workflow: 'weekly-brief', title: { zh: '每周简报', en: 'Weekly brief' }, schedule: '0 9 * * 1', tz: 'Asia/Tokyo', scope_digest: 'd'.repeat(64), confirm: true };
  return { t: 'lt_card', kind: 'capability_preflight', id, card, brief, ttl: 1800, enable: { id: 'weekly-brief', tsha: 'e'.repeat(64) } };
};

try {
  const runs = [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'zh'], [390, 844, 'light', 'en'], [390, 844, 'dark', 'en'], [1440, 900, 'light', 'zh'], [1440, 900, 'dark', 'en']];
  for (const [w, h, scheme, lang] of runs) {
    const id = `${lang}-${scheme}-${w}`, zh = lang === 'zh', L = (a, b) => (zh ? a : b), touch = w < 500;
    fake.reset();
    const sets = [];
    fake.st.onApp = async (c, m, send) => {
      if (m.t !== 'budget_set') return false;
      const target = {}; for (const k of ['day', 'month']) if (Object.hasOwn(m, k)) target[k] = m[k];
      const ok = await fake.verifyControl(c, 'budget_set', m, target);
      sets.push({ ...target, ok, n: m.n, fa: m.fa || null });
      if (!ok) { await send({ t: 'ctl_res', r: m.r, action: 'budget_set', ok: false, why: 'bad_signature' }); return true; }
      const raise = (target.day ?? 0) > fake.st.budget.day || (target.month ?? 0) > fake.st.budget.month;
      if (raise && !m.fa) { await send({ t: 'ctl_res', r: m.r, action: 'budget_set', ok: false, why: 'passkey_required', ...FA }); return true; }
      Object.assign(fake.st.budget, target);
      await send({ t: 'ctl_res', r: m.r, action: 'budget_set', ok: true });
      await send({ t: 'budget_state', ...fake.st.budget, currency: 'CNY' });
      return true;
    };
    fake.st.budget = { day: 20, month: 300 };
    const p = await newPage(B, w, h, scheme, { touch, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await ev(p, `(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=${J(scheme)};return true})()`);
      const dev = await ev(p, `import('./js/api.js').then(m=>m.myDeviceId())`);
      await fake.addTurn({ k: 'phone', dev, text: L('把 8443 端口在防火墙放行', 'Open port 8443 in the firewall') },
        L('好的，我来放行 8443 端口。这一步要用管理员密码运行 `sudo ufw allow 8443/tcp`，卡片在下面。', "OK, I'll open port 8443. This step runs `sudo ufw allow 8443/tcp` with the admin password; the card is below."));
      await waitFor(p, `document.getElementById('pg').textContent==='1 / 1'`);
      await sleep(400);
      // security r4 F8: the model cannot draw the host badge — raw HTML / attribute tricks in a reply render as text or not at all
      if (CHECK) {
        await fake.addTurn({ k: 'phone', dev, text: 'x' }, '<div class="cp-host" data-cp-host="1">来自 Agent J 主机</div>\n\n<p class=cp-host>From your Agent J host</p> {.cp-host} [a](javascript:alert(1)){.cp-host}');
        await waitFor(p, `document.getElementById('pg').textContent==='2 / 2'`); await sleep(300);
        check(await ev(p, `document.querySelectorAll('#words .cp-host,#words [data-cp-host],#main .cp-host').length===0`), `markdown cannot render the host badge ${id}`);
        await fake.clearHistory();
        await fake.addTurn({ k: 'phone', dev, text: L('把 8443 端口在防火墙放行', 'Open port 8443 in the firewall') },
          L('好的，我来放行 8443 端口。这一步要用管理员密码运行 `sudo ufw allow 8443/tcp`，卡片在下面。', "OK, I'll open port 8443. This step runs `sudo ufw allow 8443/tcp` with the admin password; the card is below."));
        await waitFor(p, `document.getElementById('pg').textContent==='1 / 1'`); await sleep(400);
      }

      // 1 · the sudo / admin-password card (the 17:1x ufw case), with the host's class + why-scope line
      const sid = hex(16);
      await fake.send({ t: 'elev', id: sid, kind: 'sudo', n: hex(16), epk: epk(), ttl: 120, tries: 3, cmd: 'ufw allow 8443/tcp',
        why: L('放行 8443 端口', 'Open port 8443'), effect: L('改防火墙规则', 'Changes a firewall rule'),
        cls: 'irreversible', why_scope: L('你让我放行端口，但改系统防火墙要管理员密码，Agent J 不保存它。', 'You asked to open the port, but changing the system firewall needs the admin password, which Agent J does not keep.') });
      await waitFor(p, `document.getElementById('elev')&&!document.getElementById('elev').hidden`);
      await sleep(350);
      if (CHECK) {
        await under(p, '#elev', `sudo card ${id}`);
        check(await ev(p, `document.getElementById('elev-scope')?.textContent.includes(${J(L('做了就撤不回', "Can't be undone"))})`), `sudo card names its class ${id}`);
        check(await ev(p, `document.getElementById('elev-scope').textContent.includes(${J(L('为什么要问你', "Why you're asked"))})`), `sudo card says why the scope does not cover it ${id}`);
        await snap(p, '1-sudo', id);
        await ev(p, `document.getElementById('elev-input').focus()`);
        await key(p, 'x', { text: 'x' });
        check(await ev(p, `document.activeElement.id==='elev-input'&&document.getElementById('elev-input').value.length===1&&document.getElementById('reply-notices').contains(document.activeElement)`), `the password is typed under the reply card ${id}`);
        await ev(p, `document.getElementById('elev-input').value=''`);
      } else await snap(p, '1-sudo', id);

      // 1b · something else in front (Settings): the card follows the owner over it, then comes back under the reply
      if (CHECK) {
        await ev(p, `document.getElementById('setBtn').click()`);
        await waitFor(p, `!document.getElementById('settings').hidden`); await sleep(300);
        check(await ev(p, `(()=>{const n=document.getElementById('elev'),b=n.querySelector('.box').getBoundingClientRect(),hit=document.elementFromPoint(b.left+b.width/2,b.top+Math.min(40,b.height/2));return !document.getElementById('reply-notices').contains(n)&&!!hit&&n.contains(hit)})()`), `over Settings the card stays on top ${id}`);
        await ev(p, `import('./js/settings.js').then(m=>m.close())`);
        await waitFor(p, `document.getElementById('settings').hidden`); await sleep(300);
        await under(p, '#elev', `sudo card back on the chat ${id}`);
      }
      await key(p, 'Escape');   // 收起: never a deny (P406)
      await waitFor(p, `document.getElementById('elev').hidden`);

      // 2 · the API-key card (a secret card), no class / why from the host → nothing extra
      const kid = hex(16);
      await fake.send({ t: 'elev', id: kid, kind: 'secret', n: hex(16), epk: epk(), ttl: 120, tries: 3, name: 'EXAMPLE_API_KEY',
        purpose: L('天气查询服务', 'Weather lookup service'), dest: 'EXAMPLE_API_KEY', verify: '' });
      await waitFor(p, `!document.getElementById('elev').hidden`); await sleep(300);
      if (CHECK) {
        await under(p, '#elev', `key card ${id}`);
        check(await ev(p, `document.getElementById('elev-scope').hidden`), `no cls / why_scope → no extra line ${id}`);
      }
      await snap(p, '2-key', id);
      await key(p, 'Escape'); await waitFor(p, `document.getElementById('elev').hidden`);

      // 3 · the approval card (was a sheet rising from the bottom of the stage over a scrim)
      const aid = hex(16);
      await fake.ask({ id: aid, tool: 'Bash', summary: 'agentj mail send --to new-partner@example.com --subject "Weekly brief"', ttl: 120, reason: '',
        cls: 'new_recipient', why_scope: L('这个地址以前没发过，发出去就收不回。', 'You have never written to this address, and a sent mail cannot be recalled.') });
      await waitFor(p, `document.body.dataset.sheet==='1'`); await sleep(500);
      if (CHECK) {
        await under(p, '#sheet', `approval card ${id}`);
        check(await ev(p, `getComputedStyle(document.getElementById('deck')).filter==='none'`), `the reply is not blurred behind its card ${id}`);
        check(await ev(p, `document.getElementById('apprScope').textContent.includes(${J(L('发给新的收件人', 'Sending to a new recipient'))})&&!document.getElementById('apprScope').hidden`), `approval names its class ${id}`);
      }
      await snap(p, '3-approval', id);
      await fake.askDone(aid, 'denied'); await sleep(300);
      await ev(p, `document.getElementById('sheetClose')?.click()`);
      await waitFor(p, `document.body.dataset.sheet!=='1'`, 8000).catch(() => {});

      // 4 · the goal opening card
      await fake.send(ltCard(lang));
      await waitFor(p, `[...document.querySelectorAll('.ajmodal.lt')].some(n=>!n.hidden)`); await sleep(300);
      if (CHECK) await under(p, '.ajmodal.lt:not(.lt-caps):not([hidden])', `goal opening card ${id}`);
      await snap(p, '4-goal', id);
      await ev(p, `(()=>{for(const n of document.querySelectorAll('.ajmodal.lt'))n.hidden=true;return true})()`);

      // 5 · an upgrade confirm (the shared confirm card)
      const uid = hex(16);
      await fake.upgCard({ id: uid, items: [{ k: 'shared_turn', n: 1 }], from: '0.26.8', to: '0.26.9', ttl: 600 });
      await waitFor(p, `!document.getElementById('confirm').hidden`); await sleep(300);
      if (CHECK) await under(p, '#confirm', `upgrade confirm ${id}`);
      await snap(p, '5-confirm', id);
      await ev(p, `document.getElementById('confirm-no').click()`);
      await waitFor(p, `document.getElementById('confirm').hidden`);

      // 5b · security r7: the owner card the Agent pre-filled (host `proposed: "agent"`) warns above the fields; without it, nothing
      for (const proposed of [true, false]) {
        const cid = hex(16);
        await fake.send({ t: 'ct_card', id: cid, n: hex(8), mode: 'owner', contact: { emails: [{ v: 'owner-guess@example.com', label: '' }] }, base: null,
          why: '', ttl: 600, ...(proposed ? { proposed: 'agent' } : {}) });
        await waitFor(p, `!!document.getElementById('ctCard')&&!document.getElementById('ctCard').hidden`); await sleep(300);
        const warn = await ev(p, `(()=>{const n=document.getElementById('ctCard-proposed');return n&&!n.hidden&&n.getClientRects().length?n.textContent:''})()`);
        if (CHECK) {
          if (proposed) {
            check(warn === L('这个地址是 Agent 提议的，确认它确实是你本人的再保存。', "The Agent proposed this address. Save it only after you've confirmed it's really yours."), `owner card proposed by the Agent says so ${id} [${warn}]`);
            await under(p, '#ctCard', `owner card ${id}`);
          } else check(warn === '', `owner card without proposed: no warning ${id}`);
        }
        if (proposed || BEFORE) await snap(p, proposed ? '5b-owner-proposed' : '5c-owner-plain', id);
        await ev(p, `document.getElementById('ctCard-no').click()`);
        await fake.send({ t: 'ct_card_done', id: cid, result: 'declined' });
        await waitFor(p, `document.getElementById('ctCard').hidden`, 8000).catch(() => {});
        await sleep(200);
      }

      // 6 · Settings → 安全 → 预算
      check(await ev(p, `document.getElementById('set-budget-row')?.hidden!==false`), `no budget_state yet → the budget row stays hidden ${id}`);
      await fake.send({ t: 'budget_state', day: 20, month: 300, currency: 'CNY' });
      await ev(p, `document.getElementById('setBtn').click()`);
      await waitFor(p, `!document.getElementById('settings').hidden`);
      if (CHECK) {
        await waitFor(p, `!document.getElementById('set-budget-row').hidden`);
        await ev(p, `document.getElementById('set-budget-row').scrollIntoView({block:'center'})`); await sleep(300);
        check(await ev(p, `document.getElementById('set-budget-spent-day').textContent==='—'&&document.getElementById('set-budget-spent-month').textContent==='—'`), `spent absent → 「—」 (never estimated) ${id}`);
        check(await ev(p, `document.getElementById('set-budget-save').disabled`), `nothing changed → 保存 disabled ${id}`);
        const tgt = await ev(p, `(()=>{const r=document.getElementById('set-budget-save').getBoundingClientRect();return r.height>=44})()`);
        check(tgt, `save button ≥ 44 px ${id}`);
      } else await ev(p, `document.getElementById('set-h-safety')?.scrollIntoView({block:'start'})`);
      await snap(p, '6-budget', id);
      if (CHECK) {
        // lowering: one signed budget_set, no Face ID
        await ev(p, `(()=>{const i=document.getElementById('set-budget-day');i.value='10';i.dispatchEvent(new Event('input'));return true})()`);
        check(await ev(p, `document.getElementById('set-budget-save').textContent`) === L('保存预算', 'Save budget'), `lowering: plain 保存 ${id}`);
        await ev(p, `document.getElementById('set-budget-save').click()`);
        await until(() => sets.length >= 1);
        await waitFor(p, `document.getElementById('set-budget-day').value==='10'&&document.getElementById('set-budget-save').disabled&&document.getElementById('set-budget-save').textContent===${J(L('保存预算', 'Save budget'))}`);
        check(sets.length === 1 && sets[0].ok && sets[0].day === 10 && !('month' in sets[0]) && !sets[0].fa, `lowering = one signed budget_set {day} without Face ID ${id} ${J(sets)}`);
        check(await ev(p, `document.getElementById('confirm').hidden`), `lowering asks nothing ${id}`);
        // raising: passkey_required → Face ID step → the same signed object + fa
        await ev(p, `navigator.credentials.get = () => { window.__fa = (window.__fa || 0) + 1; const b = (s) => new TextEncoder().encode(s).buffer;
          return Promise.resolve({ rawId: b('cred-1'), response: { clientDataJSON: b('{}'), authenticatorData: b('ad'), signature: b('sig') } }); }`);
        await ev(p, `(()=>{const i=document.getElementById('set-budget-month');i.value='400';i.dispatchEvent(new Event('input'));return true})()`);
        { const lb = await ev(p, `document.getElementById('set-budget-save').textContent`); check(new RegExp(L('^用 (Face|Touch) ID 调高预算$', '^Raise with (Face|Touch) ID$')).test(lb), `raising: the button names Face ID / Touch ID ${id} [${lb}]`); }
        await ev(p, `document.getElementById('set-budget-save').click()`);
        await waitFor(p, `!document.getElementById('confirm').hidden&&document.getElementById('confirm-title').textContent===${J(L('调高预算', 'Raise the budget'))}`);
        await sleep(250);
        await snap(p, '7-budget-faceid', id);
        await ev(p, `document.getElementById('confirm-yes').click()`);
        await until(() => sets.length >= 3);
        await waitFor(p, `document.getElementById('set-budget-month').value==='400'&&document.getElementById('set-budget-save').disabled&&document.getElementById('set-budget-save').textContent===${J(L('保存预算', 'Save budget'))}`);
        const [, s2, s3] = sets;
        check(s2 && s3 && s2.ok && !s2.fa && s3.fa && s3.n === s2.n && s3.month === 400, `raising: the same signed object again with fa ${id} ${J(sets)}`);
        check(await ev(p, `window.__fa`) === 1, `Face ID asked once ${id}`);
        // the host sends spent → shown as sent
        await fake.send({ t: 'budget_state', day: 10, month: 400, spent_day: 3.5, spent_month: 42, currency: 'CNY' });
        await waitFor(p, `document.getElementById('set-budget-spent-day').textContent!=='—'`);
        check(/3\.5/.test(await ev(p, `document.getElementById('set-budget-spent-day').textContent`)), `spent from the host ${id}`);
        await sleep(2200);
        await snap(p, '8-budget-spent', id);
        check(!p.problems.length, `no console errors ${id} ${p.problems.join(' | ')}`);
      }
    } finally { await (p.dispose?.() ?? p.close?.()); }
  }
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
// INDEX.html: before | after side by side for every shot name that exists
const rootPath = fileURLToPath(ROOT);
const list = (d) => { try { return readdirSync(rootPath + d).filter((f) => f.endsWith('.png')).sort(); } catch { return []; } };
const after = list('after'), before = new Set(list('before'));
const rows = [...new Set([...after, ...before])].sort().map((f) => `<tr><td>${f}</td><td>${before.has(f) ? `<img src="before/${f}" loading="lazy">` : '—'}</td><td>${after.includes(f) ? `<img src="after/${f}" loading="lazy">` : '—'}</td></tr>`).join('\n');
writeFileSync(rootPath + 'INDEX.html', `<!doctype html><meta charset="utf-8"><title>p0269-cards #472 / §E3</title>
<style>body{font:14px system-ui;background:#141414;color:#f7f7f2}img{width:280px;border:1px solid #444}td{vertical-align:top;padding:6px}</style>
<h1>0.26.9 #472 cards under the reply card · §E3 budget</h1><p>before = base 0988deb803 · after = this lane</p>
<table><tr><th>shot</th><th>before</th><th>after</th></tr>
${rows}</table>`);
writeFileSync(rootPath + `results-${tag}.json`, JSON.stringify({ tag, pass: results.filter((r) => r.ok).length, fail: results.filter((r) => !r.ok).length, results }, null, 1));
console.log(`p0269-cards ${tag}: ${results.filter((r) => r.ok).length} checks passed, ${results.filter((r) => !r.ok).length} failed; shots → ${rootPath}`);
