// P339: a goal card in plain words (snap.js keeps goal_who / goal_what / goal_add), 「批准，并加进目标授权」 signs exactly like the
// P296 mail allowlist (api.allowPicks → wire.mailAllowMessage over [goal_add], answer.allow_add = [goal_add]), and md.js turns a
// ```details fence (the goal result notice's raw commands) into a closed <details> with the page's label — text only.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import * as snap from '../public/js/snap.js';
import { allowOffer, allowPicks, cleanText } from '../public/js/api.js';
import * as wire from '../../protocol/wire.js';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const dec = new TextDecoder();
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
const ADD = '对外发送：scp 到 olares 的文件 · 最多 20 次 · 到目标结束';
snap.configure({ onChange() {}, onTurn() {} });
const ask = (m) => { const id = hex(16); snap.addAsk({ id, tool: 'Bash', summary: 'scp -q a olares:b', ttl: 120, ...m }); return snap.S.asks.get(id); };

test('P339 snap: goal_who / goal_what kept as one line (≤ 60 / ≤ 160), goal_add kept byte-exact on a hold card', () => {
  const a = ask({ goal: 'hold', goal_why: '不在授权范围内，且无法撤回', goal_who: 'Claude Code', goal_what: '把 pwone.py 复制到 olares', goal_add: ADD, cat: ['send'] });
  assert.equal(a.goal_who, 'Claude Code');
  assert.equal(a.goal_what, '把 pwone.py 复制到 olares');
  assert.equal(a.goal_add, ADD);
  assert.equal(a.goal_why, '不在授权范围内，且无法撤回');
  const b = ask({ goal: 'auto', goal_who: 'x'.repeat(90) + '\n<b>', goal_what: 'copy\u0000 a\n\nfile ' + 'y'.repeat(300) });
  assert.equal(b.goal_who.length, 60);
  assert.ok(!/[\u0000-\u001f]/.test(b.goal_who + b.goal_what), 'no control characters / newlines');
  assert.ok(b.goal_what.startsWith('copy a file ') && b.goal_what.length === 160);
});

test('P339 snap: goal_add only on a hold card, never cut (too long / control characters / not a string → dropped)', () => {
  assert.equal(ask({ goal: 'auto', goal_what: 'x', goal_add: ADD }).goal_add, '', 'auto: no offer');
  assert.equal(ask({ goal: 'attack', goal_what: 'x', goal_add: ADD }).goal_add, '', 'attack: no offer');
  assert.equal(ask({ goal: 'grant', goal_add: ADD }).goal_add, '', 'grant: no offer');
  assert.equal(ask({ goal_add: ADD }).goal_add, '', 'not a goal card');
  assert.equal(ask({ goal: 'hold', goal_add: 'a'.repeat(301) }).goal_add, '', '> 300: dropped, not sliced (it is signed)');
  assert.equal(ask({ goal: 'hold', goal_add: 'a'.repeat(300) }).goal_add, 'a'.repeat(300));
  assert.equal(ask({ goal: 'hold', goal_add: 'a\nb' }).goal_add, '');
  assert.equal(ask({ goal: 'hold', goal_add: 42 }).goal_add, '');
  for (const k of ['goal_who', 'goal_what']) assert.equal(ask({ goal: 'hold', [k]: { x: 1 } })[k], '');
  const old = ask({ goal: 'hold', goal_why: 'w' });        // an older host: nothing new, the card renders as before
  assert.deepEqual([old.goal_who, old.goal_what, old.goal_add], ['', '', '']);
});

test('P339 api: a goal hold card offers [goal_add]; the mail card keeps its own offer; nothing else is ever added', () => {
  const goal = { goal: 'hold', goal_add: ADD, allow_offer: [] };
  assert.deepEqual(allowOffer(goal), [ADD]);
  assert.deepEqual(allowPicks(goal, [ADD]), [ADD]);
  assert.deepEqual(allowPicks(goal, ['something else']), [], 'only what the card offered');
  assert.deepEqual(allowPicks(goal, undefined), []);
  const mail = { tool: 'agentj mail send', allow_offer: ['a@x.example', 'b@x.example'] };
  assert.deepEqual(allowPicks(mail, ['b@x.example', 'a@x.example']), ['a@x.example', 'b@x.example'], 'card order');
  assert.deepEqual(allowPicks({ goal: 'auto', goal_add: ADD }, [ADD]), [], 'auto never adds');
  assert.deepEqual(allowPicks({ goal: 'hold', goal_add: '' }, ['']), []);
  assert.equal(typeof cleanText, 'function');
});

test('P339 api: the goal_add allow is signed with mailAllowMessage over [goal_add] — the allow text + one allow_add line', async () => {
  const args = ['CH', 'DEV', 'r'.repeat(32), 'Bash', 'scp -q a olares:b'];
  const base = dec.decode(await wire.approveMessage(args[0], args[1], args[2], 'allow', args[3], args[4]));
  const signed = dec.decode(await wire.mailAllowMessage(...args, allowPicks({ goal: 'hold', goal_add: ADD }, [ADD])));
  const line = Array.from(new Uint8Array(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode('allow:' + ADD))), (b) => b.toString(16).padStart(2, '0')).join('');
  assert.equal(signed, `${base}\n${line}`);
});

// ---------------------------------------------------------------- md.js: ```details
class FNode {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.className = ''; this.data = null; }
  appendChild(n) { if (n.tag === '#frag') this.kids.push(...n.kids); else this.kids.push(n); return n; }
  append(...ns) { for (const n of ns) this.appendChild(n); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  get textContent() { return this.data !== null ? this.data : this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; this.data = null; if (this.tag === '#text') this.data = String(v); else this.kids.push(fdoc.createTextNode(v)); }
  all() { const out = []; const st = [...this.kids]; while (st.length) { const n = st.pop(); if (n.tag !== '#text') { out.push(n); st.push(...n.kids); } } return out; }
}
const fdoc = {
  createElement: (t) => new FNode(t),
  createTextNode: (s) => { const n = new FNode('#text'); n.data = String(s); return n; },
  createDocumentFragment: () => new FNode('#frag'),
};
const { RelayMD } = await import('../public/js/md.js');
const render = (src) => { const root = new FNode('div'); RelayMD.toDOM(RelayMD.parse(src), fdoc, root); return root; };
const NOTICE = '🎯 目标结束 · 到期：在 olares 上跑基准测试\n卡在这里，等你决定（1 件）：\n第 1 件：Claude Code 想把 pwone.py 复制到 olares\n'
  + '~~~~details\n第 1 件：scp -q /srv/u/bench-jarvis/pwone.py olares:bench-jarvis/pwone.py && echo copied\n<img src=x onerror=alert(1)> <script>x()</script>\n~~~~\n';

test('P339 md: a ```details / ~~~~details fence → <details> (closed) with the page label, the code block inside, text only', () => {
  const was = RelayMD.LABELS.details;
  RelayMD.LABELS.details = '详情';
  try {
    for (const src of [NOTICE, NOTICE.replace(/~~~~/g, '```'), NOTICE.replace(/~~~~/g, '~~~~~~')]) {
      const root = render(src);
      const d = root.all().filter((n) => n.tag === 'details');
      assert.equal(d.length, 1, 'one <details>');
      assert.equal(d[0].hasAttribute('open'), false, 'closed by default');
      const sum = d[0].kids[0];
      assert.equal(sum.tag, 'summary');
      assert.equal(sum.textContent, '详情');
      const code = d[0].all().find((n) => n.tag === 'code');
      assert.ok(code.textContent.includes('scp -q /srv/u/bench-jarvis/pwone.py') && code.textContent.includes('<img src=x onerror=alert(1)>'), 'the body is text');
      assert.ok(!root.all().some((n) => ['img', 'script'].includes(n.tag)), 'no element from the body');
      assert.equal(d[0].all().find((n) => n.tag === 'pre').getAttribute('data-lang'), null, 'no highlight mark');
      assert.ok(root.textContent.includes('第 1 件：Claude Code 想把 pwone.py 复制到 olares'), 'the plain words stay outside');
    }
    RelayMD.LABELS.details = 'Details';
    assert.equal(render('```details\nx\n```').all().find((n) => n.tag === 'summary').textContent, 'Details');
  } finally { RelayMD.LABELS.details = was; }
});

test('P339 md: other fences are unchanged (no <details> for js / text / Details / details-x / none)', () => {
  for (const lang of ['js', 'text', 'Details', 'details-x', 'detailsx', '']) {
    const root = render('```' + lang + '\nconsole.log(1)\n```');
    assert.ok(!root.all().some((n) => n.tag === 'details' || n.tag === 'summary'), lang);
    assert.equal(root.all().filter((n) => n.tag === 'pre').length, 1, lang);
  }
  assert.ok(!render('<details><summary>x</summary>y</details>').all().some((n) => n.tag === 'details'), 'raw HTML stays text');
});
