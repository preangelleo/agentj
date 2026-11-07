// 0.26.9 #433 / #447 (chairman 11-03 23:1x: 「系统提示里的 /recover-background 不可点……一般的小白也不知道要在输入框里输入这个
// slash command」; 「[查看待核对的后台结果](reply:/recover-background) 没渲染」) · Agent J's own slash commands in a reply, a system
// page or a command result become internal links (.fp-link, Banana Republic like every internal link):
//   · a bare `/name` in the words (or an inline `code` that is exactly one command), and
//   · md.js's <span class="rcmd" data-cmd="/name arg"> — the Agent's `[label](reply:/name arg)` — the label stays the words.
// Only names the phone knows (KNOWN, the host's slash.PHONE_COMMANDS). A tap on a look-only command (SEND: lists, status,
// a background report) sends it at once through the same path as typing it; anything that changes or needs words
// (/compact, /clear, /model, /goal …) is put after the composer's words for the owner to send — one tap never clears or switches anything.
// Never inside a fenced code block, a link, a button, or a quote of someone else's words; a path (/home/x, ~/a/status, a URL)
// is never a command. Everything is createElement + textContent.

export const KNOWN = new Set(['clear', 'new', 'compact', 'model', 'context', 'cost', 'usage', 'status', 'help', 'stop', 'my-agent-id',
  'add-friend', 'telegram-pair', 'update', 'reload', 'reset', 'switch-harness', 'goal', 'recover-background']);
// security C-1: only commands that change nothing go at once (/compact rewrites the session's context → fill only)
export const SEND = new Set(['recover-background', 'context', 'cost', 'usage', 'status', 'help', 'my-agent-id']);
const HEX64 = /^[a-f0-9]{64}$/;
// inside `sel` between the node and `box` (the reply's own box #words is itself a <blockquote>: only what lies inside counts)
const inside = (n, sel, box) => { const m = n && n.closest(sel); return !!m && m !== box && box.contains(m); };
const SKIP = 'pre, a, button, blockquote, .mslot, .math, details, .rcmd';
// a command: start / space / CJK punctuation before it, nothing path-like after it; recover-background may carry its 64-hex id
const CMD = /(^|[\s　，。！？；：、（(【\[「『"'“‘])\/([a-z][a-z0-9-]{1,39})(?: ([a-f0-9]{64}))?(?![A-Za-z0-9_/.~-])/g;

/** "/name arg" → {cmd, arg} when it is a phone command (arg only for recover-background, a 64-hex id), else null. */
export function parseCmd(s) {
  const m = /^\/([a-z][a-z0-9-]{1,39})(?: (\S+))?$/.exec(String(s || '').trim());
  if (!m || !KNOWN.has(m[1])) return null;
  if (m[2] && !(m[1] === 'recover-background' && HEX64.test(m[2]))) return null;
  return { cmd: m[1], arg: m[2] || '' };
}
/** What a tap does: 'send' (look-only) or 'fill' (into the composer). */
export const tapKind = (cmd) => (SEND.has(cmd) ? 'send' : 'fill');

function link(label, c, h, t) {
  const b = document.createElement('button');
  const full = '/' + c.cmd + (c.arg ? ' ' + c.arg : '');
  b.setAttribute('data-aj-owner','aj-action');b.setAttribute('data-aj-action-context','dialog');b.type = 'button'; b.className = 'aj-action aj-action--quiet slash-command-link'; b.dataset.cmd = full;
  b.textContent = label;
  const tip = t(tapKind(c.cmd) === 'send' ? 'p0269w4.cmdSend' : 'p0269w4.cmdFill', { cmd: '/' + c.cmd });
  b.title = tip; if (label !== full) b.setAttribute('aria-label', label + ' · ' + tip);
  b.addEventListener('click', (e) => {
    e.stopPropagation(); e.preventDefault();
    if (tapKind(c.cmd) === 'send') h.run(c.cmd, c.arg); else h.fill(full + (c.cmd === 'goal' ? ' ' : ''));
  });
  return b;
}

/** Links the commands inside `box`. h = {run(cmd, arg), fill(text)}; t = the i18n function. → how many links it made.
    Safe to call on every paint: what is already a link is skipped. */
export function linkSlash(box, h, t) {
  if (!box || !h) return 0;
  let n = 0;
  // the Agent's [label](reply:/cmd)
  for (const sp of [...box.querySelectorAll('span.rcmd[data-cmd]')]) {
    const c = parseCmd(sp.dataset.cmd);
    const label = sp.textContent.trim() || sp.dataset.cmd;
    if (c && !inside(sp.parentElement, 'pre, a, button, blockquote', box)) { sp.replaceWith(link(label, c, h, t)); n++; }
    else sp.replaceWith(document.createTextNode(label));            // not a phone command: the label as words
  }
  // `\/compact` written as inline code: the code becomes the link
  for (const code of [...box.querySelectorAll('code')]) {
    if (inside(code, SKIP, box)) continue;
    const c = parseCmd(code.textContent);
    if (c) { code.replaceWith(link(code.textContent.trim(), c, h, t)); n++; }
  }
  const walk = document.createTreeWalker(box, NodeFilter.SHOW_TEXT, {
    acceptNode: (x) => (x.parentElement && inside(x.parentElement, SKIP + ', code', box) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const hits = [];
  for (let x = walk.nextNode(); x; x = walk.nextNode()) if (x.nodeValue.includes('/')) hits.push(x);
  for (const x of hits) {
    const s = x.nodeValue, f = document.createDocumentFragment();
    let at = 0, hit = false;
    for (const m of s.matchAll(CMD)) {
      if (!KNOWN.has(m[2]) || n >= 16) continue;
      const arg = m[3] && m[2] === 'recover-background' ? m[3] : '';
      const start = m.index + m[1].length, text = '/' + m[2] + (arg ? ' ' + arg : '');
      // an id after another command stays words (only the command is linked)
      f.append(s.slice(at, start), link(text, { cmd: m[2], arg }, h, t));
      at = start + text.length; hit = true; n++;
    }
    if (!hit) continue;
    f.append(s.slice(at));
    x.replaceWith(f);
  }
  return n;
}

// 0.26.9 #452: an AuthRescue card (sign in again / add another account / remove an account) shows the host's own sentence —
// the "summary" string inside the host's approval text — never the tool input JSON or the request hash. kind: add / del / null.
export function rescueCard(sum){
  let text = typeof sum === "string" ? sum : sum && typeof sum.summary === "string" ? sum.summary : "";
  const m = /"summary"\s*:\s*("(?:[^"\\]|\\.)*")/.exec(text);
  if (m){ try { text = JSON.parse(m[1]); } catch (_) { /* keep the whole text */ } }
  else text = text.replace(/\n?Request SHA-256:.*$/s, "");
  text = text.trim();
  const add = /^(?:添加另一个|登记新的) ?(Claude Code|Codex) ?账号|^Add (?:another|a) (Claude Code|Codex) account/.exec(text);
  const del = /^从这台 ?Agent J ?主机删除 ?(Claude Code|Codex) ?账号|^Remove the (Claude Code|Codex) account/.exec(text);
  const hit = add || del;
  return {text, kind: add ? "add" : del ? "del" : null, h: hit ? hit[1] || hit[2] : ""};
}
