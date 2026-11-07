// P212 (PROTOCOL §29): forms in the reply card on the real page + a local fake host.
//   a) Quick Pick, one button question: one tap submits (say + form), the block turns read-only 「已提交」.
//   b) Quick Pick, several questions: 提交 stays off until the required one is answered; show_if questions appear / vanish
//      and hidden ones are never sent.
//   c) Deep Dive: the entry opens the full-screen page (form_get), recommendations pre-selected, a required question blocks
//      「下一节」, ESC closes and the draft goes to the computer (form_draft), reopening restores it, the review screen, submit.
//   d) Deep Dive with an attachment: the file uploads with the submission (blob channel) and the answer names its blob.
//   e) expired / cancelled forms take no answers.
// Screenshots (zh / en × light / dark, 390 phone + 1280 desktop) → reports/qa/p212/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key } from './browser.mjs';

const SPECS = JSON.parse(readFileSync(new URL('./fixtures/p212-specs.json', import.meta.url), 'utf8'));
const $ = (id) => `document.getElementById('${id}')`;
const q = (sel) => `document.querySelector(${JSON.stringify(sel)})`;
const click = (p, sel) => evaluate(p, `(()=>{const e=${q(sel)};if(!e)throw new Error('no '+${JSON.stringify(sel)});e.scrollIntoView({block:'center'});e.click();return true})()`);
const view = (spec, id, extra = {}) => ({ id, mode: spec.mode, title: spec.title, n: spec.n, state: 'open', exp: Date.now() + 7 * 864e5,
  secs: spec.sections.length, ...(spec.mode === 'light' ? { spec } : {}), ...(spec.tap ? { tap: true } : {}), ...extra });
const FID = (n) => 'f_' + String(n).padStart(16, '0');
// a) Quick Pick: one tap submits, the block turns read-only · b) required gate, show_if questions appear and hidden ones are
// never sent · c) Deep Dive: full-screen page, preselected recommendations, required gate, draft on the computer, review,
// submit · d) a Deep Dive attachment uploads with the submission · e) expired / cancelled forms take no answers
export const CASES = { a: 'quick-tap', b: 'quick-multi', c: 'deep-draft', d: 'deep-attach', e: 'closed' };
const L = {
  zh: { ask: '首页先做哪个版本？', reply: '三个方向都能在周五前上线，你挑一个：', sent: /已提交/, deep: '开工前把关键的几件事问清楚，大概 10 分钟。', missing: /还差/, expired: /已过期/, cancelled: /撤回/ },
  en: { ask: 'Which homepage version first?', reply: 'All three can ship by Friday — pick one:', sent: /Submitted/, deep: 'Before we start, let’s settle the key decisions — about 10 minutes.', missing: /required left/, expired: /expired/, cancelled: /withdrew/ },
};

export async function runP212({ B, web, fake, out = 'reports/qa/p212', rec = parityRecorder('web/test/p212.mjs') }) {
  mkdirSync(out, { recursive: true });
  const results = {};
  const step = async (c, f) => { try { await f(); rec.record(CASES[c], true); } catch (e) { rec.record(CASES[c], false); throw e; } };
  for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
    const s = L[lang], first = lang === 'zh' && theme === 'light';
    fake.reset();
    const drafts = new Map(), specs = new Map(), gets = [];
    fake.st.onApp = async (c, m, send) => {
      if (m.t === 'form_get') { gets.push(m.id); const sp = specs.get(m.id); await send(sp ? { t: 'form_res', r: m.r, ok: true, id: m.id, state: 'open', spec: sp, exp: Date.now() + 864e5, ...(drafts.has(m.id) ? { draft: drafts.get(m.id) } : {}) } : { t: 'form_res', r: m.r, ok: false, why: 'gone' }); return true; }
      if (m.t === 'form_draft') { drafts.set(m.id, { answers: m.answers, other: m.other || {}, sec: m.sec, at: Date.now(), by: 'Pixel' }); await send({ t: 'form_draft_res', id: m.id, ok: true, at: Date.now() }); return true; }
      return false;
    };
    const p = await newPage(B, 390, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
    const save = async (name, pg = p) => writeFileSync(resolve(out, `${lang}-${theme}-${name}.png`), await shoot(pg, ''));
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
      const dev = await evaluate(p, `import('./js/api.js').then(api=>api.myDeviceId())`);
      const lastSay = () => fake.st.says[fake.st.says.length - 1];

      await step('a', async () => {
        const sp = SPECS['decide-3.' + lang];
        const t = await fake.addTurn({ k: 'phone', dev, text: s.ask }, s.reply, 'done');
        await fake.updateTurn(t.id, { form: view(sp, FID(1)) });
        await waitFor(p, `!!${q('#fstrip .fopt')}`);
        assert.equal(await evaluate(p, `document.querySelectorAll('#fstrip .fopt').length`), 3);
        assert.equal(await evaluate(p, `!!${q('#fstrip .fgo')}`), false, 'a lone button question has no 提交');
        assert.equal(await evaluate(p, `${q('#fstrip .fopt.on')}`), null, 'Quick Pick never pre-selects');
        assert.equal(await evaluate(p, `${q('#fstrip .fopt .frec')}.closest('.fopt').dataset.v`), 'a', 'the recommendation is marked');
        const tgt = await evaluate(p, `(()=>{const r=${q('#fstrip .fopt')}.getBoundingClientRect();return Math.min(r.width,r.height)})()`);
        assert.ok(tgt >= 44, 'option ≥ 44 px: ' + tgt);
        if (first || theme === 'dark') await save('quick-tap');
        const n0 = fake.st.says.length;
        await click(p, '#fstrip .fopt[data-v="b"]');
        await waitFor(p, `true`, 200);
        for (let i = 0; i < 40 && fake.st.says.length === n0; i++) await sleep(50);
        const say = lastSay();
        assert.deepEqual(say.form, { id: FID(1), answers: { choice: 'b' } }, 'one tap = the answer, as a say with form');
        assert.ok(say.text.includes(sp.title));
        await fake.updateTurn(t.id, { form: { ...view(sp, FID(1)), state: 'answered', at: Date.now(), by: 'iPhone', rows: [[sp.sections[0].questions[0].label, sp.sections[0].questions[0].options[1].label, false]] } });
        await waitFor(p, `!!${q('#fstrip .fdone')} && document.querySelectorAll('#fstrip .fopt').length===0`);
        assert.match(await evaluate(p, `${q('#fstrip .fdone')}.textContent`), s.sent);
        await waitFor(p, `${$('pg')}.textContent.startsWith('2 /')`, 4000).catch(() => {});   // the answer's own page follows
        await evaluate(p, `${$('pgPrev')}.click()`); await sleep(150);
        if (first || theme === 'dark') await save('quick-answered');
        await evaluate(p, `${$('pgNext')}.click()`); await sleep(150);      // back to the newest page: new pages follow again
      });

      await step('b', async () => {
        const sp = SPECS['event-signup.' + lang];
        const t = await fake.addTurn({ k: 'agent', text: '' }, lang === 'zh' ? '帮你报名，还差这几项：' : 'Signing you up — just these:', 'done');
        await fake.updateTurn(t.id, { form: view(sp, FID(2)) });
        await waitFor(p, `!!${q('#fstrip .fgo')}`);
        assert.equal(await evaluate(p, `${q('#fstrip .fgo')}.disabled`), true, 'required 参加方式 not answered → 提交 off');
        assert.match(await evaluate(p, `${q('#fstrip .fsubnote')}.textContent`), s.missing);
        const hidden = () => evaluate(p, `[...document.querySelectorAll('#fstrip .fq')].filter(x=>x.hidden).map(x=>x.dataset.q)`);
        assert.deepEqual(await hidden(), ['talks', 'people', 'diet'], 'show_if questions wait for 参加方式');
        await click(p, '#fstrip .fq[data-q="mode"] .fopt[data-v="onsite"]');
        assert.deepEqual(await hidden(), []);
        await click(p, '#fstrip .fq[data-q="talks"] .fopt[data-v="t1"]');
        await evaluate(p, `(()=>{const i=${q('#fstrip .fq[data-q="diet"] input')};i.value='${lang === 'zh' ? '不吃香菜' : 'No coriander'}';i.dispatchEvent(new Event('input'))})()`);
        if (first || theme === 'dark') await save('quick-multi');
        await click(p, '#fstrip .fq[data-q="mode"] .fopt[data-v="online"]');
        assert.deepEqual(await hidden(), ['people', 'diet'], 'online → 人数 / 饮食 hidden again');
        assert.equal(await evaluate(p, `${q('#fstrip .fgo')}.disabled`), false);
        const n0 = fake.st.says.length;
        await click(p, '#fstrip .fgo');
        for (let i = 0; i < 40 && fake.st.says.length === n0; i++) await sleep(50);
        assert.deepEqual(lastSay().form.answers, { mode: 'online', talks: ['t1'] }, 'hidden answers (people default, diet) never go');
      });

      await step('c', async () => {
        const sp = SPECS['grill-startup.' + lang];
        specs.set(FID(3), sp);
        const t = await fake.addTurn({ k: 'phone', dev, text: lang === 'zh' ? '我想做个宠物店预约的小程序' : 'I want to build pet-shop booking' }, s.deep, 'done');
        await fake.updateTurn(t.id, { form: view(sp, FID(3)) });
        await waitFor(p, `!!${q('#fstrip .fdeepin .fgo')}`);
        if (first || theme === 'dark') await save('deep-entry');
        await click(p, '#fstrip .fdeepin .fgo');
        await waitFor(p, `!${$('fdeep')}.hidden && !!${q('#fdeep .fq')}`);
        assert.deepEqual(gets, [FID(3)], 'the spec comes with form_get');
        assert.equal(await evaluate(p, `${q('#fdeep .fq[data-q="who"] .fopt.on')}.dataset.v`), 'u1', 'Grill me: the recommendation is pre-selected');
        assert.ok(await evaluate(p, `!!${q('#fdeep .fq[data-q="who"] .frecline')}`), 'and says so (黄底 我的推荐)');
        await click(p, '#fdeep .fdnext');
        assert.equal(await evaluate(p, `${q('#fdeep .fdstep.on')}.textContent`), sp.sections[0].title, 'a required question keeps 下一节');
        assert.ok(await evaluate(p, `${q('#fdeep .fq[data-q="now"]')}.classList.contains('bad')`));
        await evaluate(p, `(()=>{const i=${q('#fdeep .fq[data-q="now"] textarea')};i.value='${lang === 'zh' ? '微信聊天加手写本' : 'WeChat and a paper notebook'}';i.dispatchEvent(new Event('input'))})()`);
        if (first || theme === 'dark') await save('deep-page');
        await key(p, 'Escape', { code: 'Escape' });
        await waitFor(p, `${$('fdeep')}.hidden`);
        await waitFor(p, `true`, 100);
        for (let i = 0; i < 40 && !drafts.has(FID(3)); i++) await sleep(50);
        assert.equal(drafts.get(FID(3))?.answers?.now?.length > 3, true, 'closing sends the draft to the computer');
        // another remote would start from the computer's draft: forget this page's memory, reopen
        await evaluate(p, `import('./js/forms.js').then(f=>f.forgetAll())`);
        await click(p, '#fstrip .fdeepin .fgo');
        await waitFor(p, `!${$('fdeep')}.hidden && !!${q('#fdeep .fq[data-q="now"] textarea')}`);
        assert.ok((await evaluate(p, `${q('#fdeep .fq[data-q="now"] textarea')}.value`)).length > 3, 'the draft is back');
        // fill every required question section by section
        const fill = async () => evaluate(p, `(()=>{for(const w of document.querySelectorAll('#fdeep .fq:not([hidden])')){
          const t=w.querySelector('textarea, input.fin[type=text]'); if(t&&!t.value&&w.querySelector('.freq')){t.value='ok';t.dispatchEvent(new Event('input'));}
          if(w.querySelector('.freq')&&w.querySelector('.fopt')&&!w.querySelector('.fopt.on'))w.querySelector('.fopt').click();}})()`);
        for (let k = 0; k < sp.sections.length; k++) { await fill(); await click(p, '#fdeep .fdnext'); await sleep(60); }
        await waitFor(p, `!!${q('#fdeep .fdrev')}`);
        if (first) await save('deep-review');
        const n0 = fake.st.says.length;
        await click(p, '#fdeep .fdnext');
        for (let i = 0; i < 60 && fake.st.says.length === n0; i++) await sleep(50);
        const f = lastSay().form;
        assert.equal(f.id, FID(3));
        assert.equal(f.answers.who, 'u1');
        assert.ok(f.answers.now && f.answers.promise && f.answers.core && f.answers.kill, 'every required answer goes');
        await waitFor(p, `${$('fdeep')}.hidden`);
      });

      await step('d', async () => {
        const sp = SPECS['bug-report.' + lang];
        specs.set(FID(4), sp);
        const t = await fake.addTurn({ k: 'agent', text: '' }, lang === 'zh' ? '把这个 bug 描述一下，截图一起传上来：' : 'Describe the bug and attach a screenshot:', 'done');
        await fake.updateTurn(t.id, { form: view(sp, FID(4)) });
        await waitFor(p, `!!${q('#fstrip .fdeepin .fgo')}`);
        await click(p, '#fstrip .fdeepin .fgo');
        await waitFor(p, `!${$('fdeep')}.hidden && !!${q('#fdeep .fq[data-q="steps"]')}`);
        await evaluate(p, `(()=>{for(const [id,v] of [['steps','1. open\\n2. tap'],['expected','works'],['actual','spins']]){const i=document.querySelector('#fdeep .fq[data-q="'+id+'"] textarea, #fdeep .fq[data-q="'+id+'"] input');i.value=v;i.dispatchEvent(new Event('input'));}})()`);
        await click(p, '#fdeep .fdnext'); await click(p, '#fdeep .fdnext');
        await waitFor(p, `!!${q('#fdeep .fq[data-q="shots"] input[type=file]')}`);
        await evaluate(p, `(()=>{const i=${q('#fdeep .fq[data-q="shots"] input[type=file]')},d=new DataTransfer();
          d.items.add(new File([new Uint8Array([137,80,78,71,13,10,26,10,1,2,3,4])],'screen.png',{type:'image/png'}));i.files=d.files;i.dispatchEvent(new Event('change'))})()`);
        await waitFor(p, `!!${q('#fdeep .fchip')}`);
        if (first || lang === 'en' && theme === 'dark') await save('deep-attach');
        await click(p, '#fdeep .fdnext');
        await waitFor(p, `!!${q('#fdeep .fdrev')}`);
        const n0 = fake.st.says.length;
        await click(p, '#fdeep .fdnext');
        for (let i = 0; i < 100 && fake.st.says.length === n0; i++) await sleep(50);
        const say = lastSay();
        assert.equal(say.att.length, 1, 'the file went with the submission');
        assert.deepEqual(say.form.answers.shots, say.att, 'the answer names the uploaded blob');
        assert.equal(fake.st.opens.filter(Boolean).at(-1).name, 'screen.png');
      });

      await step('e', async () => {
        const sp = SPECS['decide-3.' + lang];
        const t1 = await fake.addTurn({ k: 'agent', text: '' }, 'old', 'done');
        await fake.updateTurn(t1.id, { form: view(sp, FID(5), { exp: Date.now() - 1000 }) });
        await waitFor(p, `!!${q('#fstrip .fclosed')}`);
        assert.match(await evaluate(p, `${q('#fstrip .fclosed')}.textContent`), s.expired);
        const t2 = await fake.addTurn({ k: 'agent', text: '' }, 'withdrawn', 'done');
        await fake.updateTurn(t2.id, { form: view(sp, FID(6), { state: 'cancelled' }) });
        await waitFor(p, `!!${q('#fstrip .fclosed')} && /${s.cancelled.source}/.test(${q('#fstrip .fclosed')}.textContent)`);
        assert.equal(await evaluate(p, `document.querySelectorAll('#fstrip .fopt').length`), 0);
      });

      // desktop: the Deep Dive page at 1280
      if (theme === 'light') {
        const d = await newPage(B, 1280, 860, theme, { allow: [web.url, fake.relay + '/'] });
        try {
          await navigate(d, fake.newPairing(web.url + '?lang=' + lang)); await waitState(d, 'awaiting-approval'); await fake.approve(); await waitState(d, 'ready');
          await evaluate(d, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
          await waitFor(d, `!!${q('#fstrip .fdeepin .fgo')} || true`);
          const t = await fake.addTurn({ k: 'agent', text: '' }, s.deep, 'done');
          await fake.updateTurn(t.id, { form: view(SPECS['grill-startup.' + lang], FID(3)) });
          await waitFor(d, `!!${q('#fstrip .fdeepin .fgo')}`);
          await save('desktop-entry', d);
          await click(d, '#fstrip .fdeepin .fgo');
          await waitFor(d, `!${$('fdeep')}.hidden && !!${q('#fdeep .fq')}`);
          await save('desktop-deep', d);
          await key(d, 'ArrowRight', { code: 'ArrowRight' });
          await sleep(100);
          assert.deepEqual(d.problems, []);
        } finally { await d.dispose(); }
      }
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
      results[lang + '-' + theme] = { result: 'pass' }; console.log('PASS P212 ' + lang + '-' + theme);
    } finally { fake.st.onApp = null; await p.dispose(); }
  }
  writeFileSync(resolve(out, 'results.json'), JSON.stringify(results, null, 2) + '\n');
  console.log('parity results → ' + rec.write());
  return results;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
  try { await runP212({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
