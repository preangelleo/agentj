"""Conservative Markdown → Telegram HTML; raw HTML is always text, never markup (P144, P201: relay ADR-051 parity —
fenced code + language, tables, merged quotes, rules, numbered / task lists, escapes; cuts prefer line ends)."""
import html
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urlsplit

from .text import drop_replacement

INLINE = re.compile(r'\\([\\`*_{}\[\]()#+\-.!|~>])|<(https?://[^\s<>]+)>|(?<![\w/])(https?://[^\s<>]+)|(`+)(.+?)\4'
                    r'|!?\[([^\]\n]+)\]\(<?([^\s)>]+)>?\)|(\*\*|(?<!\w)__|~~|\+\+|\*|(?<!\w)_)(?=\S)(.+?)(?<=\S)\8', re.S)
TAGS = {'**': 'b', '__': 'b', '*': 'i', '_': 'i', '~~': 's', '++': 'u'}
FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})\s*([^`\s]*)')
RULE = re.compile(r'^\s{0,3}([-*_])(\s*\1){2,}\s*$')
ITEM = re.compile(r'^(\s*)(?:([-+*])|(\d{1,9}[.)]))\s+(?:\[([ xX])\]\s+)?')
QUOTE = re.compile(r'^\s{0,3}>\s?')
TABLE_SEP = re.compile(r'^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$')
TABLE_PRE_MAX = 40          # wider than a phone's monospace line → one card per row instead of a padded <pre>
esc = lambda s: html.escape(s, quote=False)   # noqa: E731


def inline(text, depth=0):
    if depth >= 4:
        return esc(text)
    out, end = [], 0
    for m in INLINE.finditer(text):
        out.append(esc(text[end:m.start()]))
        if m[1] or m[3]:
            out.append(esc(m[1] or m[3]))      # a \-escape, or a bare URL (never parsed: a_b_c in a path)
        elif m[2]:
            out.append('<a href="' + html.escape(m[2], quote=True) + '">' + esc(m[2]) + '</a>')
        elif m[4]:
            out.append('<code>' + esc(m[5]) + '</code>')
        elif m[6]:
            try:
                valid = len(m[7]) <= 256 and urlsplit(m[7]).scheme.lower() in ('https', 'http', 'mailto', 'tg')
            except ValueError:
                valid = False
            label = re.sub(r'</?a\b[^>]*>', '', inline(m[6], depth + 1))
            out.append('<a href="' + html.escape(m[7], quote=True) + '">' + label + '</a>' if valid else label)
        elif m[8][0] == '_' and m.end() < len(text) and re.match(r'\w', text[m.end()]):
            out.append(esc(m[0]))
        else:
            tag = TAGS[m[8]]
            out.append('<' + tag + '>' + inline(m[9], depth + 1) + '</' + tag + '>')
        end = m.end()
    out.append(esc(text[end:]))
    return ''.join(out)


def _line(line):
    if RULE.match(line):
        return '──────────'
    m = ITEM.match(line)
    if not m:
        return inline(line)
    mark = ('☑' if m[4] in 'xX' else '☐') if m[4] else '•' if m[2] else m[3]
    return esc(m[1]) + mark + ' ' + inline(line[m.end():])


def _width(s):
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 0 if unicodedata.combining(c) else 1 for c in s)


def _cells(row):
    s = row.strip()
    s = s[1:] if s.startswith('|') else s
    s = s[:-1] if s.endswith('|') and not s.endswith('\\|') else s
    return [c.strip().replace('\\|', '|') for c in re.split(r'(?<!\\)\|', s)]


def visible(h):
    return html.unescape(re.sub(r'<[^>]*>', '', h))


def _table(rows):
    grid = [_cells(r) for r in rows]
    cols = max(map(len, grid))
    grid = [r + [''] * (cols - len(r)) for r in grid]
    shown = [[visible(inline(c)) for c in r] for r in grid]
    w = [max(_width(r[k]) for r in shown) for k in range(cols)]
    if sum(w) + 3 * (cols - 1) <= TABLE_PRE_MAX:
        lines = [' │ '.join(t + ' ' * (w[k] - _width(t)) for k, t in enumerate(r)).rstrip() for r in shown]
        lines.insert(1, '─┼─'.join('─' * x for x in w))
        return '<pre>' + esc('\n'.join(lines)) + '</pre>'
    cards = []                     # too wide for a phone: each row = its first cell in bold, then "header: cell" lines
    for r in grid[1:]:
        lines = ['▪ <b>' + re.sub(r'</?b>', '', inline(r[0])) + '</b>'] if r[0] else []
        lines += ['   ' + esc(shown[0][k]) + '：' + inline(c) for k, c in enumerate(r) if k and c]
        cards.append('\n'.join(lines))
    return '\n\n'.join(cards)


def markdown(text):
    lines = str(text).replace('\r\n', '\n').replace('\r', '\n').split('\n')
    out, i = [], 0
    while i < len(lines):
        line, f = lines[i], FENCE.match(lines[i])
        if f:
            j, mark = i + 1, f[1]
            while j < len(lines) and not (lines[j].strip().startswith(mark) and not lines[j].strip().strip(mark[0])):
                j += 1
            code, lang = esc('\n'.join(lines[i + 1:j])), re.sub(r'[^\w+#.-]', '', f[2])[:32]
            out.append('<pre><code class="language-' + lang + '">' + code + '</code></pre>' if lang else '<pre>' + code + '</pre>')
            i = j + 1
        elif '|' in line and i + 1 < len(lines) and '|' in lines[i + 1] and TABLE_SEP.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and '|' in lines[j] and lines[j].strip():
                j += 1
            out.append(_table([line] + lines[i + 2:j]))
            i = j
        elif QUOTE.match(line):
            j, inner = i, []
            while j < len(lines) and QUOTE.match(lines[j]):
                inner.append(_line(re.sub(r'^(\s{0,3}>\s?)+', '', lines[j])))   # Telegram has no nested quotes
                j += 1
            body = '\n'.join(inner).strip('\n')
            out.append('<blockquote>' + body + '</blockquote>' if body else '')
            i = j
        else:
            h = re.match(r'^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$', line)
            out.append('<b>' + re.sub(r'</?b>', '', inline(h[1])) + '</b>' if h else _line(line))
            i += 1
    return '\n'.join(out)


class _Parts(HTMLParser):
    """Cuts the HTML into ≤ limit UTF-16 parts that each parse alone: open tags close at a cut and reopen after it; a cut
    falls just after the last line end of the part when that keeps it at least a third full, else mid-line."""
    def __init__(self, limit):
        super().__init__(convert_charrefs=True)
        self.limit, self.stack, self.body, self.plain, self.parts, self.nl, self.href = limit, [], '', '', [], None, None

    def closers(self, stack=None):
        return ''.join('</' + tag + '>' for tag, _ in reversed(self.stack if stack is None else stack))

    def size(self, s):
        return len(s.encode('utf-16-le')) // 2

    def flush(self, final=False):
        if not final and self.nl and self.size(self.body[:self.nl[0]]) * 3 >= self.limit:
            at, kept, stack = self.nl
            head, self.body = self.body[:at] + self.closers(stack), ''.join(raw for _, raw in stack) + self.body[at:]
            self.parts.append((head, self.plain[:kept])) if self.plain[:kept].strip() else None
            self.plain, self.nl = self.plain[kept:], None
            return
        if self.plain.strip():
            self.parts.append((self.body + self.closers(), self.plain))
        self.body, self.plain, self.nl = ''.join(raw for _, raw in self.stack), '', None

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        while self.size(self.body + raw + '</' + tag + '>' + self.closers()) > self.limit and self.plain:
            self.flush()
        self.stack.append((tag, raw))
        self.body += raw
        self.href = (dict(attrs).get('href') or '', len(self.plain)) if tag == 'a' else self.href

    def handle_endtag(self, tag):
        self.stack.pop()
        self.body += '</' + tag + '>'
        url, at = self.href if tag == 'a' and self.href else ('', 0)
        if url and self.plain[at:] != url and self.size(self.plain) + len(url) < 4090:
            self.plain += ' (' + url + ')'     # the plain fallback keeps where a link pointed

    def handle_data(self, data):
        for char in data:
            escaped = esc(char)
            while self.size(self.body + escaped + self.closers()) > self.limit and self.plain:
                self.flush()
            self.body += escaped
            self.plain += char
            if char == '\n':
                self.nl = (len(self.body), len(self.plain), tuple(self.stack))


def parts(text, limit=3900):
    if limit < 1500:
        raise ValueError('Telegram HTML chunk budget must be at least 1500')
    parser = _Parts(limit)
    parser.feed(markdown(drop_replacement(text)))   # P281: no stray 「◆?」 in Telegram either (stored history too)
    parser.close()
    parser.flush(final=True)
    return parser.parts
