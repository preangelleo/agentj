"""Conservative Markdown → Telegram HTML; raw HTML is always text, never markup."""
import html
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

INLINE = re.compile(r'(`+)(.+?)\1|\[([^\]\n]+)\]\(([^\s)]+)\)|(\*\*|(?<!\w)__|~~|\+\+|\*|(?<!\w)_)(?=\S)(.+?)(?<=\S)\5', re.S)
TAGS = {'**': 'b', '__': 'b', '*': 'i', '_': 'i', '~~': 's', '++': 'u'}


def inline(text, depth=0):
    if depth >= 4:
        return html.escape(text, quote=False)
    out, end = [], 0
    for m in INLINE.finditer(text):
        out.append(html.escape(text[end:m.start()], quote=False))
        if m[1]:
            out.append('<code>' + html.escape(m[2], quote=False) + '</code>')
        elif m[3]:
            try:
                valid = len(m[4]) <= 256 and urlsplit(m[4]).scheme.lower() in ('https', 'http', 'mailto', 'tg')
            except ValueError:
                valid = False
            label = inline(m[3], depth + 1)
            out.append('<a href="' + html.escape(m[4], quote=True) + '">' + label + '</a>' if valid else label)
        else:
            if m[5][0] == '_' and m.end() < len(text) and re.match(r'\w', text[m.end()]):
                out.append(html.escape(m[0], quote=False))
            else:
                tag = TAGS[m[5]]
                out.append('<' + tag + '>' + inline(m[6], depth + 1) + '</' + tag + '>')
        end = m.end()
    out.append(html.escape(text[end:], quote=False))
    return ''.join(out)


def markdown(text):
    out, code, fence = [], [], None
    for line in str(text).splitlines(keepends=True):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                out.append('<pre>' + html.escape(''.join(code), quote=False) + '</pre>\n')
                code, fence = [], None
            else:
                code.append(line)
            continue
        if marker:
            fence = marker[1]
            continue
        heading = re.match(r'^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$', line)
        quote = re.match(r'^\s{0,3}>\s?(.*)', line)
        if heading:
            out.append('<b>' + inline(heading[1]) + '</b>\n')
        elif quote:
            out.append('<blockquote>' + inline(quote[1]) + '</blockquote>\n')
        else:
            out.append(inline(re.sub(r'^([ \t]*)(?:[-+*]|\d+[.)])\s+', r'\1• ', line)))
    if fence:
        out.append('<pre>' + html.escape(''.join(code), quote=False) + '</pre>')
    return ''.join(out)


class _Parts(HTMLParser):
    def __init__(self, limit):
        super().__init__(convert_charrefs=True)
        self.limit, self.stack, self.body, self.plain, self.parts = limit, [], '', '', []

    def closers(self):
        return ''.join('</' + tag + '>' for tag, _ in reversed(self.stack))

    def size(self, s):
        return len(s.encode('utf-16-le')) // 2

    def flush(self):
        if self.plain:
            self.parts.append((self.body + self.closers(), self.plain))
        self.body = ''.join(raw for _, raw in self.stack)
        self.plain = ''

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        if self.size(self.body + raw + '</' + tag + '>' + self.closers()) > self.limit:
            self.flush()
        self.stack.append((tag, raw))
        self.body += raw

    def handle_endtag(self, tag):
        self.stack.pop()
        self.body += '</' + tag + '>'

    def handle_data(self, data):
        for char in data:
            escaped = html.escape(char, quote=False)
            if self.size(self.body + escaped + self.closers()) > self.limit:
                self.flush()
            self.body += escaped
            self.plain += char


def parts(text, limit=3900):
    if limit < 1500:
        raise ValueError('Telegram HTML chunk budget must be at least 1500')
    parser = _Parts(limit)
    parser.feed(markdown(text))
    parser.close()
    parser.flush()
    return parser.parts
