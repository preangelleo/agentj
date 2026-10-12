// md.js — relay's safe Markdown subset (pwa/assets/md.js), as an ES module for the Agent J web client (PROMPT-33).
// relay's logic: parse() → plain objects, toDOM() → DOM node by node (createElement / textContent, an allowlist of
// tags and attributes, never an HTML parser). Changes: the code block's button label is set by the page (LABELS, from the
// i18n dictionaries); the module exports RelayMD; and (PROMPT-33 security review) — P33-X12: tables are bounded (≤ 32
// columns, ≤ 300 rows, ≤ 4 000 cells per text; beyond that the lines stay plain text) and toDOM stops creating elements
// after 20 000 (the rest becomes one text node), so rendering cost stays linear in the input; P33-C08: links are https:,
// http: or mailto: only, open with rel="noopener noreferrer", and a link whose words are not its address shows the real
// host after them (small, .lhost): a link whose words say bank.com but which goes to evil.example reads "bank.com (evil.example)".
const MOD = { LABELS: { copy: "Copy", copyAria: "Copy code", copied: "Copied", image: "image" } };
// md.js — a small, safe Markdown subset for the agent's words (M1.5-pwa-md-ptt).
//
// The agent's text is untrusted input: it quotes web pages, files and tool
// output verbatim. So this file never hands a string to the HTML parser. It is
// two pure steps:
//
//   parse(text)        -> a tree of plain objects {tag, attrs, children} / {text}
//   toDOM(tree, doc)   -> DOM, built node by node with createElement/textContent
//
// toDOM re-checks every tag and attribute against an allowlist, so even a bug in
// parse() cannot produce a <script>, an on* handler or a javascript: href. Raw
// HTML in the source is not "sanitised" — it is simply never interpreted, and
// shows up as the literal characters the agent wrote.
//
// Supported: ATX headings, paragraphs (single newlines kept as line breaks),
// **strong** / __strong__, *em* / _em_, ~~del~~, `code`, fenced code (``` / ~~~,
// unclosed fences run to the end), ordered/unordered lists with nesting,
// blockquotes, [links](http..) / <http..> / bare http(s) URLs, horizontal rules,
// GFM pipe tables. Images are shown as a link, never fetched (F21: an image of a local
// file becomes a slot that media.js fills with the bytes the computer sent inside the session; P59: so does a
// [label](local file) link).
(function (root) {
  "use strict";
  const LABELS = root.LABELS;

  const MAX_INPUT = 200000;      // beyond this, the tail is shown as plain text
  const MAX_DEPTH = 12;          // nesting guard for blockquote/list/emphasis recursion
  const MAX_COLS = 32;           // a wider "table" is shown as its lines
  const MAX_ROWS = 300;          // rows past this (or past the cell budget) are shown as their lines
  const MAX_CELLS = 4000;        // table cells per parse(), shared by every table in the text
  const MAX_NODES = 20000;       // elements per toDOM(); the rest of the text is appended as one text node
  // P57 (render lane): md.js only MARKS code / math / diagrams — every node is still createElement + textContent.
  // render.js (lazy, after the page settles) upgrades the marks with the vendored highlight.js / KaTeX / mermaid.
  // Past these sizes a block is not marked at all, so it stays exactly the plain text it is today.
  const MAX_CODE_HL = 20000;     // a fenced block longer than this gets no data-lang (no highlighting)
  const MAX_TEX = 4000;          // longer "math" stays text
  const MAX_MERMAID = 10000;     // a longer ```mermaid block stays a code block
  let cellBudget = MAX_CELLS;    // reset by parse()
  const PUNCT = /[!-\/:-@\[-`{-~]/;
  const WORD = /[\p{L}\p{N}]/u;

  // ------------------------------------------------------------------ links
  // Only absolute http(s) and mailto:<one plain address>. Everything else (javascript:, data:, vbscript:,
  // file:, relative paths that would resolve against the secret base) is text.
  const MAILTO = /^mailto:[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/i;
  function safeHref(raw) {
    if (typeof raw !== "string") return null;
    const s = raw.replace(/[\u0000-\x20\u007f-\u009f]/g, "");
    if (MAILTO.test(s)) return "mailto:" + s.slice(7);
    if (!/^https?:\/\//i.test(s)) return null;
    let u;
    try { u = new URL(s); } catch (_) { return null; }
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return u.href;
  }
  // F21: a destination naming a file on the computer (absolute, ~/, ./, relative or file:) — the host may have sent it
  // with the page (media.js). Any other scheme (http(s) stays a link; data:, javascript:, mailto: …) and "//host" → null.
  function localRef(raw) {
    if (typeof raw !== "string") return null;
    const s = raw.trim();
    if (!s || s.length > 1024 || /[\u0000-\u001f\u007f]/.test(s) || s.startsWith("//")) return null;
    if (/^file:/i.test(s)) return s;
    return /^[A-Za-z][A-Za-z0-9+.-]*:/.test(s) ? null : s;
  }
  // The destination a reader should see next to the words: the host (the address for mailto:), or null when the words
  // already are that address (a bare URL, an autolink, a link whose words are its own address, a mailto: showing its address).
  function shownTarget(href, words) {
    const w = String(words || "").trim().toLowerCase().replace(/\/+$/, "");
    if (href.startsWith("mailto:")) {
      const addr = href.slice(7);
      return w === addr.toLowerCase() || w === href.toLowerCase() ? null : addr;
    }
    let u;
    try { u = new URL(href); } catch (_) { return null; }
    const full = href.toLowerCase().replace(/\/+$/, "");
    if (w === full || w === full.replace(/^https?:\/\//, "")) return null;
    let dw = null;
    try { dw = new URL(String(words).trim()).href.toLowerCase().replace(/\/+$/, ""); } catch (_) { /* words are not a URL */ }
    return dw === full ? null : u.host;
  }

  // ------------------------------------------------------------------ inline
  function text(t) { return { text: t }; }

  function runLen(s, i, c) { let n = 0; while (s[i + n] === c) n++; return n; }

  // Where does the code span that opens at i (with n backticks) close?
  // P33-X12: memoised per string — an answer for (i) is reused, and once a run of n finds no closer from i, it finds none
  // from any later position either — so "[[[ `` ` `` …" and runs of growing length stay linear.
  let ccStr = null, ccAt = null, ccFail = null;
  function codeClose(s, i, n) {
    if (s !== ccStr) { ccStr = s; ccAt = new Map(); ccFail = new Map(); }
    if (ccAt.has(i)) return ccAt.get(i);
    if (ccFail.has(n) && ccFail.get(n) <= i) return -1;
    let k = i + n, r = -1;
    while (k < s.length) {
      const j = s.indexOf("`", k);
      if (j < 0) break;
      const m = runLen(s, j, "`");
      if (m === n) { r = j; break; }
      k = j + m;
    }
    ccAt.set(i, r);
    if (r < 0) ccFail.set(n, Math.min(i, ccFail.has(n) ? ccFail.get(n) : i));
    return r;
  }
  // Total characters tryLink may scan per parse(): "[[[[…" costs ≤ 2 000 per bracket otherwise.
  const LINK_WORK = 4000000;
  let linkWork = 0;

  // Closing delimiter for emphasis: exactly `len` of c, preceded by non-space,
  // skipping escapes and code spans, and — for "_" — not followed by a letter.
  function findClose(s, from, c, len) {
    let k = from;
    while (k < s.length) {
      const ch = s[k];
      if (ch === "\\") { k += 2; continue; }
      if (ch === "`") {
        const n = runLen(s, k, "`"), e = codeClose(s, k, n);
        k = e < 0 ? k + n : e + n; continue;
      }
      if (ch === c) {
        const n = runLen(s, k, c);
        if (n === len && k > from && !/\s/.test(s[k - 1]) &&
            !(c === "_" && k + n < s.length && WORD.test(s[k + n]))) return k;
        k += n; continue;
      }
      k++;
    }
    return -1;
  }

  // [label](dest "title") starting at i (s[i] === "["). Returns {end, label, dest}.
  function tryLink(s, i) {
    if (linkWork > LINK_WORK) return null;           // budget spent: the rest of the brackets are text
    let depth = 0, k = i;
    for (; k < s.length && k - i < 2000; k++, linkWork++) {
      const ch = s[k];
      if (ch === "\\") { k++; continue; }
      if (ch === "`") {
        const n = runLen(s, k, "`"), e = codeClose(s, k, n);
        k = (e < 0 ? k + n : e + n) - 1; continue;
      }
      if (ch === "[") depth++;
      else if (ch === "]" && --depth === 0) break;
    }
    if (s[k] !== "]" || s[k + 1] !== "(") return null;
    const label = s.slice(i + 1, k);
    let p = k + 2;
    while (s[p] === " ") p++;
    let dest = "";
    if (s[p] === "<") {
      const e = s.indexOf(">", p);
      if (e < 0) return null;
      dest = s.slice(p + 1, e); p = e + 1;
    } else {
      let par = 0; const st = p;
      for (; p < s.length; p++) {
        const ch = s[p];
        if (ch === "\\") { p++; continue; }
        if (/\s/.test(ch)) break;
        if (ch === "(") par++;
        else if (ch === ")") { if (par === 0) break; par--; }
      }
      dest = s.slice(st, p);
    }
    while (s[p] === " ") p++;
    if (s[p] === '"' || s[p] === "'") {            // optional title, ignored
      const e = s.indexOf(s[p], p + 1);
      if (e < 0) return null;
      p = e + 1;
      while (s[p] === " ") p++;
    }
    if (s[p] !== ")") return null;
    return { end: p + 1, label, dest };
  }

  // A bare URL: printable ASCII only, so "见http(s)://x.io/a这里" stops at 这,
  // and trailing sentence punctuation / an unbalanced ")" is left outside.
  function bareUrl(s, i) {
    const m = /^https?:\/\/[\x21-\x7e]+/i.exec(s.slice(i, i + 4000));
    if (!m) return null;
    let u = m[0].replace(/[<>"'`].*$/, "");
    for (;;) {
      const last = u[u.length - 1];
      if (/[.,;:!?*_~]/.test(last)) { u = u.slice(0, -1); continue; }
      if (last === ")" && (u.match(/\(/g) || []).length < (u.match(/\)/g) || []).length) {
        u = u.slice(0, -1); continue;
      }
      break;
    }
    return /^https?:\/\/./i.test(u) ? u : null;
  }

  // ------------------------------------------------------------------ math (P57)
  // Delimiters: display $$…$$ and \[…\] (own lines, or inside a paragraph), inline \(…\) and $…$.
  // The $…$ rule is Pandoc's, so prices stay prices: the opening $ has a non-space right after it; the closing $ has a
  // non-space (not "\", not "$") right before it and is NOT followed by a digit or "$"; it is the next unescaped $ after
  // the opener (no $ inside inline math); nothing in between crosses a line
  // break or a backtick; ≤ MAX_TEX characters. So "$3 and $4", "$3/$4", "5$ or 6$" are text and "$x^2$" is math.
  // "\$" is always a literal dollar. \(…\) and \[…\] stay on one line inside a paragraph; $$…$$ may span its lines.
  // mc: one scan cache per parseInline call — "the next X at/after f is at k" — so a run of openers stays linear.
  function nextAt(s, tok, from, mc, test) {
    const c = mc[tok];
    if (c && c.f <= from && (c.k < 0 || c.k >= from)) return c.k;
    let k = from;
    for (;;) { k = s.indexOf(test ? "$" : tok, k); if (k < 0 || !test || test(s, k)) break; k++; }
    mc[tok] = { f: from, k };
    return k;
  }
  const dollarCloses = (s, k) => !/[\s\\$]/.test(s[k - 1]) && !/[0-9$]/.test(s[k + 1] || "");
  function mathAt(s, i, mc) {
    const two = s.slice(i, i + 2);
    const pair = two === "$$" ? ["$$", true] : two === "\\[" ? ["\\]", true] : two === "\\(" ? ["\\)", false] : null;
    let k, end;
    if (pair) {
      k = nextAt(s, pair[0], i + 2, mc);
      if (k < 0) return null;
      if (pair[0] !== "$$") { const nl = nextAt(s, "\n", i, mc); if (nl >= 0 && nl < k) return null; }
      end = k + 2;
    } else {
      if (s[i] !== "$" || i > 0 && s[i - 1] === "$" || !s[i + 1] || /\s/.test(s[i + 1])) return null;
      k = nextAt(s, "$c", i + 1, mc, (x, j) => x[j - 1] !== "\\");   // the NEXT unescaped $ must be the closer:
      if (k < 0 || !dollarCloses(s, k)) return null;                    // "$5，公式 $E=mc^2$" → only "$E=mc^2$" is math
      const nl = nextAt(s, "\n", i, mc), bt = nextAt(s, "`", i, mc);
      if (nl >= 0 && nl < k || bt >= 0 && bt < k) return null;
      end = k + 1;
    }
    const tex = s.slice(i + (pair ? 2 : 1), k);
    if (!tex.trim() || tex.length > MAX_TEX) return null;
    return { end, node: { tag: "math", tex, display: pair ? pair[1] : false, src: s.slice(i, end) } };
  }

  function parseInline(s, depth, inLink) {
    depth = depth || 0;
    const out = [];
    let buf = "";
    const flush = () => { if (buf) { out.push(text(buf)); buf = ""; } };
    if (depth > MAX_DEPTH) return [text(s)];
    const mc = {};
    // Once a closer search for (char, len) fails, every later one would scan the
    // same suffix and fail too: remembering that keeps "*a *b *c …" linear.
    const noClose = {};
    let i = 0;
    while (i < s.length) {
      const ch = s[i];
      if (ch === "$" || ch === "\\" && (s[i + 1] === "(" || s[i + 1] === "[")) {   // P57 math (before "\" escapes)
        const mt = mathAt(s, i, mc);
        if (mt) { flush(); out.push(mt.node); i = mt.end; continue; }
      }
      if (ch === "\\" && i + 1 < s.length && PUNCT.test(s[i + 1])) { buf += s[i + 1]; i += 2; continue; }
      if (ch === "\n") { flush(); out.push({ tag: "br" }); i++; continue; }
      if (ch === "`") {
        const n = runLen(s, i, "`"), e = codeClose(s, i, n);
        if (e < 0) { buf += s.slice(i, i + n); i += n; continue; }
        let c = s.slice(i + n, e).replace(/\n/g, " ");
        if (c.length > 2 && c[0] === " " && c[c.length - 1] === " " && c.trim()) c = c.slice(1, -1);
        flush(); out.push({ tag: "code", children: [text(c)] });
        i = e + n; continue;
      }
      if (ch === "[" || (ch === "!" && s[i + 1] === "[")) {
        const img = ch === "!";
        const L = tryLink(s, img ? i + 1 : i);
        if (!L || (inLink && !img)) { buf += ch; i++; continue; }
        const dest = L.dest.replace(/\\([!-\/:-@\[-`{-~])/g, "$1");
        const href = safeHref(dest);
        flush();
        // F21 (§13): an image of a local file → a slot media.js fills with the file the computer sent (the source text
        // stays inside it until then, and for good when nothing was sent); never fetched from the path itself
        if (!href && img && localRef(dest)) out.push({ tag: "mslot", ref: localRef(dest), children: [text(s.slice(i, L.end))] });
        // P59 (ADR-A164): a link to a local file too — media.js puts the file's card there (label kept as words); the
        // source text stays when the computer sent nothing for it
        else if (!href && !inLink && localRef(dest)) out.push({ tag: "mslot", ref: localRef(dest), link: true, label: flatText(parseInline(L.label, depth + 1, true)), children: [text(s.slice(i, L.end))] });
        else if (!href) out.push(text(s.slice(i, L.end)));      // unsafe: show the source
        else if (img) out.push({ tag: "a", attrs: { href }, children: [text(L.label || LABELS.image)] });
        else out.push({ tag: "a", attrs: { href }, children: parseInline(L.label, depth + 1, true) });
        i = L.end; continue;
      }
      if (ch === "<") {
        const m = /^<(https?:\/\/[^\s<>]+)>/i.exec(s.slice(i, i + 4000));
        const href = m && safeHref(m[1]);
        if (href && !inLink) { flush(); out.push({ tag: "a", attrs: { href }, children: [text(m[1])] }); i += m[0].length; continue; }
        buf += ch; i++; continue;
      }
      if ((ch === "h" || ch === "H") && !inLink && !(i > 0 && /[A-Za-z0-9]/.test(s[i - 1]))) {
        const u = bareUrl(s, i), href = u && safeHref(u);
        if (href) { flush(); out.push({ tag: "a", attrs: { href }, children: [text(u)] }); i += u.length; continue; }
      }
      if (ch === "*" || ch === "_" || ch === "~") {
        const n = runLen(s, i, ch);
        const next = s[i + n];
        const opens = next !== undefined && !/\s/.test(next) &&
                      !(ch === "_" && i > 0 && WORD.test(s[i - 1]));
        let tried = null;
        if (opens) {
          const plan = ch === "~" ? (n === 2 ? [[2, ["del"]]] : [])
                     : n >= 3 ? [[3, ["strong", "em"]], [2, ["strong"]], [1, ["em"]]]
                     : n === 2 ? [[2, ["strong"]]] : [[1, ["em"]]];
          for (const [len, tags] of plan) {
            const key = ch + len;
            if (noClose[key]) continue;
            const j = findClose(s, i + n, ch, len);
            if (j < 0) { noClose[key] = true; continue; }
            tried = { len, tags, j }; break;
          }
        }
        if (!tried) { buf += s.slice(i, i + n); i += n; continue; }
        const { len, tags, j } = tried;
        buf += s.slice(i, i + n - len);              // surplus opener chars stay literal
        flush();
        let node = parseInline(s.slice(i + n, j), depth + 1, inLink);
        for (let t = tags.length - 1; t >= 0; t--) node = [{ tag: tags[t], children: node }];
        out.push(node[0]);
        i = j + len; continue;
      }
      buf += ch; i++;
    }
    flush();
    return out;
  }

  // ------------------------------------------------------------------ blocks
  const RE = {
    blank: /^\s*$/,
    fence: /^( {0,3})(`{3,}|~{3,})\s*([^`\s]*)[^`]*$/,
    heading: /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$/,
    hr: /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/,
    quote: /^ {0,3}> ?/,
    item: /^( *)([-*+]|\d{1,9}[.)])( +|$)(.*)$/,
    tableSep: /^ *\|? *:?-+:? *(\| *:?-+:? *)*\|? *$/,
    math: /^ {0,3}(\$\$|\\\[)(.*)$/,               // P57: a display-math block opens
  };
  const indentOf = l => l.match(/^ */)[0].length;
  const detab = l => l.replace(/\t/g, "    ");

  function splitRow(line) {
    let t = line.trim();
    if (t.startsWith("|")) t = t.slice(1);
    if (t.endsWith("|") && !t.endsWith("\\|")) t = t.slice(0, -1);
    const cells = []; let cur = "";
    for (let k = 0; k < t.length; k++) {
      if (t[k] === "\\" && t[k + 1] === "|") { cur += "|"; k++; continue; }
      if (t[k] === "`") {                            // a pipe inside `code` is not a column
        const n = runLen(t, k, "`"), e = codeClose(t, k, n);
        if (e >= 0) { cur += t.slice(k, e + n); k = e + n - 1; continue; }
      }
      if (t[k] === "|") { cells.push(cur.trim()); cur = ""; continue; }
      cur += t[k];
    }
    cells.push(cur.trim());
    return cells;
  }

  function isTableStart(lines, i) {
    return lines[i].includes("|") && i + 1 < lines.length && RE.tableSep.test(lines[i + 1]) &&
           lines[i + 1].includes("-") && (lines[i + 1].includes("|") || lines[i].trim().startsWith("|"));
  }

  function startsBlock(lines, i) {
    const l = lines[i];
    return RE.fence.test(l) || RE.heading.test(l) || RE.hr.test(l) || RE.quote.test(l) ||
           RE.item.test(l) && !RE.blank.test(l.replace(RE.item, "$4")) || isTableStart(lines, i) || RE.math.test(l);
  }

  // P57: a display-math block — "$$ … $$" / "\[ … \]" on one line, or the opener's line … a line ending in the closer,
  // with no blank line in between and ≤ MAX_TEX characters; otherwise null (the lines stay a paragraph). The closer
  // line and the length come from per-call caches (mc), so a page of unclosed "$$" lines stays linear.
  function mathBlock(lines, i, mc) {
    const m = RE.math.exec(lines[i]), close = m[1] === "$$" ? "$$" : "\\]";
    const rest = m[2].replace(/\s+$/, "");
    const node = (tex, next) => tex.trim() && tex.length <= MAX_TEX
      ? { node: { tag: "math", tex, display: true, block: true, src: lines.slice(i, next).join("\n") }, next } : null;
    if (rest.endsWith(close)) return rest.length > close.length ? node(rest.slice(0, -close.length), i + 1) : null;
    const scan = (key, ok) => {
      const c = mc[key];
      if (c && c.f <= i + 1 && (c.k < 0 || c.k >= i + 1)) return c.k;
      let k = i + 1;
      while (k < lines.length && !ok(lines[k])) k++;
      if (k >= lines.length) k = -1;
      mc[key] = { f: i + 1, k };
      return k;
    };
    const k = scan(close, (l) => l.replace(/\s+$/, "").endsWith(close)), b = scan("blank", (l) => RE.blank.test(l));
    if (k < 0 || b >= 0 && b < k) return null;
    if (!mc.pre) { mc.pre = [0]; for (const l of lines) mc.pre.push(mc.pre[mc.pre.length - 1] + l.length + 1); }
    if (mc.pre[k + 1] - mc.pre[i] > MAX_TEX + 8) return null;
    const last = lines[k].replace(/\s+$/, "");
    return node([rest, ...lines.slice(i + 1, k), last.slice(0, -close.length)].join("\n"), k + 1);
  }

  function parseBlocks(lines, depth) {
    const out = [];
    const mc = {};                                   // P57: mathBlock's scan caches for these lines
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (RE.blank.test(line)) { i++; continue; }

      if (RE.math.test(line)) {                      // P57 display math; not a block → falls through to a paragraph
        const mb = mathBlock(lines, i, mc);
        if (mb) { out.push(mb.node); i = mb.next; continue; }
      }

      let m = RE.fence.exec(line);
      if (m) {
        const ind = m[1].length, fch = m[2][0], flen = m[2].length, lang = m[3] || "";
        const body = [];
        i++;
        while (i < lines.length) {
          const cm = /^( {0,3})(`{3,}|~{3,})\s*$/.exec(lines[i]);
          if (cm && cm[2][0] === fch && cm[2].length >= flen) { i++; break; }
          body.push(lines[i].replace(new RegExp("^ {0," + ind + "}"), ""));
          i++;
        }
        out.push({ tag: "pre", lang, code: body.join("\n") });
        continue;
      }

      m = RE.heading.exec(line);
      if (m) {
        out.push({ tag: "h" + m[1].length, children: parseInline((m[2] || "").trim(), depth) });
        i++; continue;
      }

      if (RE.hr.test(line)) { out.push({ tag: "hr" }); i++; continue; }

      if (RE.quote.test(line)) {
        const inner = [];
        while (i < lines.length && !RE.blank.test(lines[i])) {
          if (RE.quote.test(lines[i])) inner.push(lines[i].replace(RE.quote, ""));
          else if (inner.length && !startsBlock(lines, i)) inner.push(lines[i]);   // lazy continuation
          else break;
          i++;
        }
        out.push({ tag: "blockquote",
                   children: depth >= MAX_DEPTH ? [{ tag: "p", children: [text(inner.join("\n"))] }]
                                                : parseBlocks(inner, depth + 1) });
        continue;
      }

      m = RE.item.exec(line);
      if (m && !RE.blank.test(m[4])) {
        const res = parseList(lines, i, depth);
        out.push(res.node); i = res.next; continue;
      }

      if (isTableStart(lines, i)) {
        const head = splitRow(line);
        if (head.length > MAX_COLS || cellBudget < head.length) {      // too wide / over budget: its lines, as text
          const raw = [line, lines[i + 1]];
          i += 2;
          while (i < lines.length && !RE.blank.test(lines[i]) && lines[i].includes("|") && !RE.fence.test(lines[i])) raw.push(lines[i++]);
          out.push({ tag: "p", children: [text(raw.join("\n"))] });
          continue;
        }
        const aligns = splitRow(lines[i + 1]).map(c =>
          /^:-+:$/.test(c) ? "c" : /-+:$/.test(c) ? "r" : "");
        const cols = head.length;
        const cell = (tag, t, k) => {
          const n = { tag, children: parseInline(t || "", depth) };
          if (aligns[k]) n.attrs = { class: "al-" + aligns[k] };
          return n;
        };
        const rows = [], rest = [];
        cellBudget -= cols;
        i += 2;
        while (i < lines.length && !RE.blank.test(lines[i]) && lines[i].includes("|") &&
               !RE.fence.test(lines[i])) {
          if (rest.length || rows.length >= MAX_ROWS || cellBudget < cols) { rest.push(lines[i]); i++; continue; }
          cellBudget -= cols;
          const cells = splitRow(lines[i]);
          const tr = [];
          for (let k = 0; k < cols; k++) tr.push(cell("td", cells[k], k));
          rows.push({ tag: "tr", children: tr });
          i++;
        }
        out.push({ tag: "table", children: [
          { tag: "thead", children: [{ tag: "tr", children: head.map((t, k) => cell("th", t, k)) }] },
          { tag: "tbody", children: rows }] });
        if (rest.length) out.push({ tag: "p", children: [text(rest.join("\n"))] });
        continue;
      }

      // paragraph: until a blank line or anything that starts another block
      const para = [line.trim()];
      i++;
      while (i < lines.length && !RE.blank.test(lines[i]) && !startsBlock(lines, i)) {
        para.push(lines[i].trim()); i++;
      }
      out.push({ tag: "p", children: parseInline(para.join("\n"), depth) });
    }
    return out;
  }

  function parseList(lines, i, depth) {
    const first = RE.item.exec(lines[i]);
    const baseIndent = first[1].length;
    const ordered = /\d/.test(first[2]);
    const bulletChar = ordered ? first[2].slice(-1) : first[2];
    const list = { tag: ordered ? "ol" : "ul", children: [] };
    if (ordered) { const st = parseInt(first[2], 10); if (st !== 1) list.attrs = { start: String(st) }; }
    let loose = false;

    while (i < lines.length) {
      const m = RE.item.exec(lines[i]);
      if (!m || m[1].length !== baseIndent || RE.blank.test(m[4])) break;
      const sameKind = ordered ? /\d/.test(m[2]) && m[2].slice(-1) === bulletChar : m[2] === bulletChar;
      if (!sameKind) break;
      const contentIndent = baseIndent + m[2].length + Math.min(m[3].length || 1, 4);
      const body = [m[4]];
      i++;
      let sawBlank = false;
      while (i < lines.length) {
        const l = lines[i];
        if (RE.blank.test(l)) { body.push(""); sawBlank = true; i++; continue; }
        const ind = indentOf(l);
        const im = RE.item.exec(l);
        if (im && im[1].length <= baseIndent && !RE.blank.test(im[4])) break;   // sibling or outer item
        if (ind > baseIndent) {                                                // belongs to this item
          body.push(l.slice(Math.min(ind, contentIndent)));
          if (sawBlank) loose = true;
          sawBlank = false; i++; continue;
        }
        if (!sawBlank && !startsBlock(lines, i)) { body.push(l.trim()); i++; continue; }   // lazy line
        break;
      }
      while (body.length && body[body.length - 1] === "") body.pop();
      // trailing blank before the next sibling makes the list loose
      if (i < lines.length && RE.blank.test(lines[i - 1] || "x")) loose = loose || !!RE.item.exec(lines[i]);
      const kids = depth >= MAX_DEPTH ? [{ tag: "p", children: [text(body.join("\n"))] }]
                                      : parseBlocks(body, depth + 1);
      list.children.push({ tag: "li", children: kids });
      // skip blank lines between siblings
      let k = i;
      while (k < lines.length && RE.blank.test(lines[k])) k++;
      const nx = k < lines.length && RE.item.exec(lines[k]);
      if (k !== i && nx && nx[1].length === baseIndent) { loose = true; i = k; }
    }
    // Tight lists render their paragraphs inline (no paragraph margins).
    if (!loose) for (const li of list.children)
      li.children = li.children.flatMap(c => c.tag === "p" ? c.children.length ? [{ tag: "span", children: c.children }] : [] : [c]);
    return { node: list, next: i };
  }

  function parse(src) {
    if (src == null) return [];
    let s = String(src).replace(/\r\n?/g, "\n").replace(/\u0000/g, "�");
    let tail = "";
    if (s.length > MAX_INPUT) { tail = s.slice(MAX_INPUT); s = s.slice(0, MAX_INPUT); }
    cellBudget = MAX_CELLS; linkWork = 0; ccStr = null;
    const out = parseBlocks(s.split("\n").map(detab), 0);
    if (tail) out.push({ tag: "p", children: [text(tail)] });
    return out;
  }

  // ------------------------------------------------------------------ DOM
  // The second wall: whatever parse() produced, only these tags and these
  // attributes (with these values) can reach the document.
  const TAGS = new Set(["p", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del", "code",
    "pre", "blockquote", "ul", "ol", "li", "a", "hr", "br", "span",
    "table", "thead", "tbody", "tr", "th", "td"]);
  const ATTRS = {
    a: { href: v => safeHref(v) },
    ol: { start: v => /^\d{1,9}$/.test(v) ? v : null },
    th: { class: v => /^al-[cr]$/.test(v) ? v : null },
    td: { class: v => /^al-[cr]$/.test(v) ? v : null },
  };

  function codeBlock(node, doc) {
    const wrap = doc.createElement("div");
    wrap.className = "codeblock";
    const btn = doc.createElement("button");
    btn.type = "button";
    btn.className = "copy";
    btn.setAttribute("aria-label", LABELS.copyAria);
    btn.textContent = LABELS.copy;
    // The button lives in a bar above the code, never on top of it.
    const bar = doc.createElement("div");
    bar.className = "bar";
    if (node.lang && /^[\w+#.-]{1,32}$/.test(node.lang)) {
      const lab = doc.createElement("span");
      lab.className = "lang";
      lab.textContent = node.lang;
      bar.appendChild(lab);
    }
    bar.appendChild(btn);
    const pre = doc.createElement("pre");
    const code = doc.createElement("code");
    code.textContent = String(node.code == null ? "" : node.code);
    // P57 marks for render.js: ```mermaid → .mermaid-src (still a code block: copy, and the fallback); any other
    // named language → <pre data-lang>. Oversized blocks get no mark and stay plain.
    const lang = String(node.lang || "").toLowerCase(), len = String(node.code == null ? "" : node.code).length;
    if (lang === "mermaid") { if (len <= MAX_MERMAID) wrap.className = "codeblock mermaid-src"; }
    else if (/^[\w+#.-]{1,32}$/.test(lang) && len <= MAX_CODE_HL) pre.setAttribute("data-lang", lang);
    pre.appendChild(code);
    wrap.append(bar, pre);
    return wrap;
  }

  // P57: math → <span class="math"> (inline) / <div class="math display"> (its own block) whose text is the source
  // as written, delimiters included (so until render.js typesets it — or if it never does — the reader sees exactly
  // what the agent wrote), and data-tex = the TeX between the delimiters (an attribute value, never markup).
  function mathEl(node, doc) {
    const el = doc.createElement(node.block ? "div" : "span");
    el.className = node.display ? "math display" : "math";
    el.setAttribute("data-tex", String(node.tex));
    el.textContent = String(node.src);
    return el;
  }

  // The plain text of a subtree (iterative: no recursion limit, linear).
  function flatText(nodes) {
    const out = [], stack = [nodes];
    while (stack.length) {
      const top = stack.pop();
      if (Array.isArray(top)) { for (let k = top.length - 1; k >= 0; k--) stack.push(top[k]); continue; }
      if (!top || typeof top !== "object") continue;
      if (typeof top.text === "string") out.push(top.text);
      else if (top.tag === "pre") out.push("\n" + String(top.code == null ? "" : top.code) + "\n");
      else if (top.tag === "math") out.push(String(top.src));
      else if (top.tag === "br" || top.tag === "p" || top.tag === "tr" || top.tag === "li") { out.push("\n"); if (top.children) stack.push(top.children); }
      else if (top.children) stack.push(top.children);
    }
    return out.join("");
  }

  function toDOM(nodes, doc, parent, budget) {
    doc = doc || document;
    parent = parent || doc.createDocumentFragment();
    budget = budget || { left: MAX_NODES };
    const list = nodes || [];
    for (let idx = 0; idx < list.length; idx++) {
      const n = list[idx];
      if (!n || typeof n !== "object") continue;
      if (typeof n.text === "string") { parent.appendChild(doc.createTextNode(n.text)); continue; }
      if (budget.left <= 0) {                        // over the element budget: the rest of this level as one text node
        parent.appendChild(doc.createTextNode(flatText(list.slice(idx))));
        break;
      }
      budget.left--;
      if (n.tag === "pre") { parent.appendChild(codeBlock(n, doc)); continue; }
      if (n.tag === "math") { parent.appendChild(mathEl(n, doc)); continue; }
      if (n.tag === "mslot") {                       // F21: <span class="mslot" data-ref="…"> + the source text
        const sp = doc.createElement("span");
        sp.className = "mslot";
        sp.setAttribute("data-ref", String(n.ref == null ? "" : n.ref).slice(0, 1024));
        if (n.link === true) {                       // P59: a [label](local file) — the label as an attribute value (text)
          sp.setAttribute("data-link", "1");
          sp.setAttribute("data-label", String(n.label == null ? "" : n.label).slice(0, 200));
        }
        toDOM(n.children, doc, sp, budget);
        parent.appendChild(sp);
        continue;
      }
      if (!TAGS.has(n.tag)) continue;
      // An <a> whose href does not pass is never created: it becomes a <span>.
      const href = n.tag === "a" ? safeHref(String((n.attrs || {}).href)) : null;
      const tag = n.tag === "a" && !href ? "span" : n.tag;
      const el = doc.createElement(tag);
      const allowed = ATTRS[tag] || {};
      for (const [k, v] of Object.entries(n.attrs || {})) {
        const ok = allowed[k] && allowed[k](String(v));
        if (ok) el.setAttribute(k, ok);
      }
      if (tag === "a") { el.setAttribute("target", "_blank"); el.setAttribute("rel", "noopener noreferrer"); }
      if (n.tag === "table") {
        const scroll = doc.createElement("div");
        scroll.className = "tablewrap";
        scroll.appendChild(el);
        parent.appendChild(scroll);
      } else parent.appendChild(el);
      if (n.children) toDOM(n.children, doc, el, budget);
      if (tag === "a") {                             // P33-C08: the real destination, when the words say something else
        const where = shownTarget(href, el.textContent);
        if (where) {
          const h = doc.createElement("span");
          h.className = "lhost";
          h.textContent = " (" + where + ")";
          parent.appendChild(h);
        }
      }
    }
    return parent;
  }

  // ------------------------------------------------------------------ copy
  // navigator.clipboard only exists in a secure context: the tunnel (HTTPS) has
  // it, a phone on the LAN over plain http does not. The fallback is the old
  // hidden-textarea + execCommand("copy"), which still works on both engines
  // inside a user gesture. Resolves true/false; never throws.
  function legacyCopy(t, doc) {
    doc = doc || document;
    const ta = doc.createElement("textarea");
    ta.value = t;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;font-size:16px";
    doc.body.appendChild(ta);
    const sel = doc.getSelection && doc.getSelection();
    const prev = sel && sel.rangeCount ? sel.getRangeAt(0) : null;
    let ok = false;
    try {
      ta.focus({ preventScroll: true });
      ta.select();
      ta.setSelectionRange(0, t.length);            // iOS ignores select() alone
      ok = !!doc.execCommand("copy");
    } catch (_) { ok = false; }
    ta.remove();
    if (prev && sel) { try { sel.removeAllRanges(); sel.addRange(prev); } catch (_) {} }
    return ok;
  }

  async function copyText(t, nav, doc) {
    nav = nav || (typeof navigator !== "undefined" ? navigator : {});
    if (nav.clipboard && typeof nav.clipboard.writeText === "function") {
      try { await nav.clipboard.writeText(t); return true; } catch (_) { /* fall through */ }
    }
    return legacyCopy(t, doc);
  }

  root.RelayMD = { parse, parseInline: (s) => { linkWork = 0; cellBudget = MAX_CELLS; return parseInline(s); }, toDOM, safeHref, localRef, shownTarget, copyText, legacyCopy, LABELS,
    LIMITS: { MAX_INPUT, MAX_COLS, MAX_ROWS, MAX_CELLS, MAX_NODES, MAX_CODE_HL, MAX_TEX, MAX_MERMAID } };
})(MOD);
export const RelayMD = MOD.RelayMD;
export default RelayMD;
