// P569 (#370) shared scene for p569_bars.mjs (assertions) and p569-shots.mjs (before / after shots): a real paired page
// (fake host + Noise) with every surface that carries a left accent bar, and a probe that reads the bar the way the eye
// sees it — whatever technique draws it (border-left, inset shadow, ::before strip, 3 px gradient stripe).
// The same calls run on the base tree, so the expected colours in p569_bars.mjs come from the before state.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

export const REPLY = {
  zh: '部署方案有三步：先备份数据库，再切换流量，最后观察十分钟。\n\n> 引用：这一段是主人上一轮的原话，回复里原样引用。\n\n结束。',
  en: 'The rollout has three steps: back up the database, switch traffic, then watch for ten minutes.\n\n> Quoted: these are the owner\'s words from the last turn, quoted as they were.\n\nDone.',
};
const TXT = {
  zh: { share: '截图已收到，点「粘贴截图」放进输入框', grant: '已批准 10 分钟内同类命令（还剩 9 分钟）', run: '正在执行：systemctl --user restart agentj',
    who: 'Agent J', quote: '部署方案有三步：先备份数据库', act: '在电脑上点「允许」，然后回到这里。', toast: '已发送到 Agent J' },
  en: { share: 'Screenshot received — tap Paste screenshot to put it in the field', grant: 'Same-class commands approved for 10 minutes (9 left)',
    run: 'Running: systemctl --user restart agentj', who: 'Agent J', quote: 'The rollout has three steps', act: 'Click Allow on the computer, then come back here.',
    toast: 'Sent to Agent J' },
};

// pair, set idle, add one reply with a markdown quote; scheme also sent as the owner preference
export async function boot(page, fake, base, lang, scheme) {
  fake.reset(); fake.st.autoReply = false; fake.st.extraCaps = ['mq5'];
  fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
  await page.goto(fake.newPairing(base + '/?lang=' + lang));
  await page.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 20000 });
  await fake.approve();
  await page.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 20000 });
  await page.evaluate(() => document.querySelector('#phone-env-dismiss')?.click());
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await fake.addTurn({ k: 'phone', dev: 'other', text: lang === 'zh' ? '部署怎么做？' : 'How do we deploy?' }, REPLY[lang]);
  await page.waitForFunction(() => !!document.querySelector('#words blockquote'), null, { timeout: 15000 });
  await page.waitForTimeout(300);
}

// a real selection in the reply → the composer's quote row (n = 1: one row; n = 2: two chips)
export async function quote(page, n) {
  for (let i = 0; i < n; i++) {
    await page.evaluate(([a, b]) => {
      const p = document.querySelector('#words p'); const t = p.firstChild; const r = document.createRange();
      r.setStart(t, a); r.setEnd(t, b); const s = getSelection(); s.removeAllRanges(); s.addRange(r);
      document.dispatchEvent(new Event('selectionchange'));
    }, i ? [8, 14] : [0, 6]);
    await page.waitForFunction((k) => { const b = document.getElementById('qbar'); return !b.hidden && (b.dataset.n ? +b.dataset.n : 1) === k; }, i + 1, { timeout: 5000 });
    await page.evaluate(() => getSelection().removeAllRanges());
  }
}
export async function unquote(page) {
  await page.evaluate(() => document.getElementById('qbX')?.click());
  await page.waitForFunction(() => document.getElementById('qbar').hidden, null, { timeout: 5000 });
}

// the reply's notice stack: a rect notification (real ui.notify), the share notice, the approval bar, a running command
export async function notices(page, lang, kind = 'info') {
  await page.evaluate(async ([t, kind]) => {
    const ui = await import('./js/ui.js');
    const sn = document.getElementById('share-notice'); document.getElementById('share-status').textContent = t.share; sn.hidden = false;
    if (!document.getElementById('p569-grant')) {
      const g = document.createElement('div'); g.className = 'elev-grant'; g.id = 'p569-grant'; g.setAttribute('role', 'status');
      const gt = document.createElement('span'); gt.className = 'elev-grant-text'; gt.textContent = t.grant; g.append(gt); ui.replyNotice(g);
      const r = document.createElement('div'); r.className = 'elev-run'; r.id = 'p569-run'; r.dataset.state = 'ask';
      const row = document.createElement('div'); row.className = 'elev-run-row';
      const main = document.createElement('button'); main.type = 'button'; main.className = 'elev-run-main';
      const dot = document.createElement('span'); dot.className = 'elev-run-dot';
      const tx = document.createElement('span'); tx.className = 'elev-run-text'; tx.textContent = t.run;
      main.append(dot, tx); row.append(main); r.append(row); ui.replyNotice(r);
    }
    ui.notify({ kind, text: t.toast, shape: 'rect', ms: 600000 });
  }, [TXT[lang], kind]);
  await page.waitForTimeout(350);
}
export async function clearNotices(page) {
  await page.evaluate(async () => {
    (await import('./js/ui.js')).toastOff();
    document.getElementById('share-notice').hidden = true;
    for (const id of ['p569-grant', 'p569-run']) document.getElementById(id)?.remove();
  });
}

// the sent message's quote block (#omQuote) and the folded row (#omQuoteFold), filled the way relay paints them
export async function sentQuote(page, lang) {
  await page.evaluate((t) => {
    document.getElementById('omqWho').textContent = t.who; document.getElementById('omqMeta').textContent = ' · 10:24';
    document.getElementById('omqText').textContent = t.quote; document.getElementById('omQuote').hidden = false;
    document.getElementById('omqfHead').textContent = t.who; document.getElementById('omqfText').textContent = t.quote;
    document.getElementById('omQuoteFold').hidden = false;
  }, TXT[lang]);
  await page.waitForTimeout(200);
}

// the rest, in their own containers (these appear only deep in other flows: a danger approval, a to-do card, the contact
// form, the five pages, type-ahead lists, the first-run checklist, the terminal sheet, rescue mode in Settings)
export async function extras(page, lang) {
  await page.evaluate((t) => {
    if (document.getElementById('p569-extras')) return;
    if (!document.querySelector('link[href="terminal.css"]')) { const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = 'terminal.css'; document.head.append(l); }
    const h = (html) => { const d = document.createElement('div'); d.innerHTML = html; return d.firstElementChild; };
    const box = document.createElement('div'); box.id = 'p569-extras'; box.style.cssText = 'position:absolute;left:0;top:0;width:360px;z-index:1;pointer-events:none;visibility:hidden';
    box.append(
      h(`<div class="sheet danger"><div class="cmd" id="p569-cmd">rm -rf build/</div></div>`),
      h(`<p class="setup-act" id="p569-setup-act">${t.act}</p>`),
      h(`<div class="fr-gmentions"><button class="fp-mention" id="p569-frm" aria-selected="true">Sammi</button></div>`),
      h(`<div><button class="fp-mention" id="p569-fpm" aria-selected="true">Sammi</button></div>`),
      h(`<div class="fp-prose"><blockquote id="p569-fpq">${t.quote}</blockquote></div>`),
      h(`<div><div class="jc-filter"></div><ul class="jc-list"><li class="jc-item" id="p569-jci" data-mark="overdue"><span class="jc-text">${t.quote}</span></li></ul></div>`),
      h(`<div class="ct-dlg"><label class="ct-field fresh"><span>Mail</span><input id="p569-ctf" value="sammi@example.com"></label></div>`),
      h(`<div class="term-sheet"><p class="term-origin" id="p569-tor">${t.run}</p><p class="term-warn" id="p569-twa">${t.run}</p></div>`),
    );
    document.body.append(box);
    box.querySelector('.ct-dlg').style.setProperty('--dfg', 'var(--dlg-fg)');   // the dialog's .gdlg scope gives its fields this
    const sug = document.getElementById('sug');
    sug.innerHTML = '<button type="button" role="option" aria-selected="true" id="p569-sug1"><b>/compact</b><span>x</span></button><button type="button" role="option"><b>/clear</b><span>y</span></button>';
    const rs = document.createElement('p'); rs.className = 'rsmode'; rs.id = 'p569-rsmode'; rs.textContent = t.act;
    document.getElementById('settings')?.append(rs);
  }, TXT[lang]);
}

// Settings at 1440: the side nav's current section
export async function openSettings(page) {
  await page.evaluate(() => document.getElementById('setBtn').click());
  await page.waitForFunction(() => { const s = document.getElementById('settings'); return s && !s.hidden; }, null, { timeout: 8000 });
  await page.evaluate(() => document.querySelector('#settings .setside button:not([hidden])')?.click());
  await page.waitForTimeout(400);
}

// read the bar: which technique, its colour, the box's left border width and left corner radii, any pseudo strip
export const PROBE = `(sel) => {
  const el = document.querySelector(sel); if (!el) return { missing: true };
  const cs = getComputedStyle(el);
  const top = (s) => { const out = []; let d = 0, cur = ''; for (const ch of s) { if (ch === '(') d++; if (ch === ')') d--; if (ch === ',' && !d) { out.push(cur.trim()); cur = ''; } else cur += ch; } if (cur.trim()) out.push(cur.trim()); return out; };
  const colourIn = (s) => { const m = /(rgba?\\([^)]*\\)|color\\([^)]*\\)|oklab\\([^)]*\\)|oklch\\([^)]*\\)|#[0-9a-f]{3,8})/i.exec(s); return m ? m[1] : null; };
  let pseudo = null;
  for (const ps of ['::before', '::after']) {
    const p = getComputedStyle(el, ps);
    if (p.content === 'none' || p.content === 'normal' || p.display === 'none') continue;
    if (p.position === 'absolute' && parseFloat(p.width) <= 6 && parseFloat(p.height) >= 12) pseudo = { ps, colour: p.backgroundColor };
  }
  const inset = top(cs.boxShadow === 'none' ? '' : cs.boxShadow).find((s) => /inset/.test(s) && /(^|\\s)-?[1-9][\\d.]*px 0px 0px/.test(s.replace(/^(rgba?\\([^)]*\\)|color\\([^)]*\\))\\s*/, '')));
  const grad = /linear-gradient\\(90deg, *(.*?) 3px/.exec(cs.backgroundImage);
  const blw = parseFloat(cs.borderLeftWidth) || 0;
  const technique = pseudo ? 'pseudo' : inset ? 'inset' : grad ? 'gradient' : blw ? 'border' : 'none';
  const colour = pseudo ? pseudo.colour : inset ? colourIn(inset) : grad ? colourIn(grad[1]) : blw ? cs.borderLeftColor : null;
  return { technique, colour, blw, blc: cs.borderLeftColor, tl: parseFloat(cs.borderTopLeftRadius) || 0, bl: parseFloat(cs.borderBottomLeftRadius) || 0,
    pseudo: !!pseudo, shadow: cs.boxShadow, display: cs.display };
}`;
export const probe = (page, sel) => page.evaluate(`(${PROBE})(${JSON.stringify(sel)})`);

// colours in any computed syntax → [r,g,b,a] (0-255, a 0-1)
export function rgba(c) {
  if (!c) return null;
  let m = /color\(srgb ([\d.e-]+) ([\d.e-]+) ([\d.e-]+)(?: \/ ([\d.]+))?\)/.exec(c);
  if (m) return [+m[1] * 255, +m[2] * 255, +m[3] * 255, m[4] === undefined ? 1 : +m[4]];
  m = /rgba?\(([\d.]+),? ([\d.]+),? ([\d.]+)(?:[,/] ?([\d.]+))?\)/.exec(c);
  if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
  return null;
}
export const sameColour = (a, b, tol = 1.6) => { const x = rgba(a), y = rgba(b); return !!x && !!y && x.slice(0, 3).every((v, i) => Math.abs(v - y[i]) <= tol) && Math.abs(x[3] - y[3]) <= 0.02; };

// the dashboard's own sheet: a static page with its bars, served from a public dir (no host involved)
export function dashboardServer(dashDir) {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/account/brand/palette.css"><link rel="stylesheet" href="/account/brand/base.css"><link rel="stylesheet" href="/account/app.css"></head>
<body class="aj"><main class="dash-page" style="padding:16px;display:grid;gap:16px">
<p class="msg" id="d-msg" style="position:static;transform:none">Saved.</p>
<p class="msg" id="d-msgerr" data-kind="error" style="position:static;transform:none">Could not save.</p>
<div class="warn" id="d-warn"><p>Your seat expires in 3 days.</p></div>
<div class="callout-info" id="d-info"><p>Pair a phone from Settings.</p></div>
<div class="pending" id="d-pending"><p>Waiting for the host.</p></div>
<div class="sentence" id="d-sentence"><p class="sentence-label">Say this to your Agent</p></div>
<div class="plaza-replies"><div class="plaza-reply admin" id="d-admin"><p>Admin reply</p></div><div class="plaza-reply pinned" id="d-pinned"><p>Pinned reply</p></div></div>
<p class="approve-note" id="d-approve">Approved on this phone.</p>
<nav class="side-nav" style="display:flex;flex-direction:column"><div class="side-group" style="display:flex;flex-direction:column"><button class="side-link" id="d-side" aria-current="true">Billing</button></div></nav>
</main></body></html>`;
  const root = normalize(dashDir);
  const srv = createServer(async (req, res) => {
    const u = new URL(req.url, 'http://x');
    if (u.pathname === '/p569.html') { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); return res.end(html); }
    const rel = u.pathname.replace(/^\/account\//, '');
    const f = normalize(join(root, rel));
    if (!f.startsWith(root)) { res.writeHead(403); return res.end(); }
    try { const b = await readFile(f); res.writeHead(200, { 'content-type': { '.css': 'text/css', '.woff2': 'font/woff2' }[extname(f)] || 'application/octet-stream' }); res.end(b); }
    catch { res.writeHead(404); res.end(); }
  });
  return new Promise((r) => srv.listen(0, '127.0.0.1', () => r({ url: `http://127.0.0.1:${srv.address().port}`, stop: () => new Promise((q) => srv.close(q)) })));
}
export const DASH = ['#d-msg', '#d-msgerr', '#d-warn', '#d-info', '#d-pending', '#d-sentence', '#d-admin', '#d-pinned', '#d-approve', '#d-side'];
export const HERE = fileURLToPath(new URL('.', import.meta.url));
