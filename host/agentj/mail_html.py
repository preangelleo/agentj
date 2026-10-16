"""P355: an HTML page (a web report, a workflow's HTML output, a hand-written page) made fit to be a mail body.

`prepare(html, text_fn)` → (html, info). Two passes, pure, no I/O:
1. **Safe**: script / iframe / object / embed / applet / frameset / template / textarea / select go with their content;
   form, button and noscript lose the tag and keep the words; input / link / base / meta http-equiv / comments go;
   every on* attribute, form action / formaction, and any href / src / background / poster that is not http(s), mailto,
   tel, a #fragment or (src only) a data:image goes. A relative <img> (a report's local assets/) cannot travel in a
   mail: it becomes its alt text (info `local_images`). Styles with expression( / javascript: / behavior go.
   `text_fn` (redaction for outsiders) runs on every text node and kept href, never on tags or CSS.
2. **Inline**: mail clients drop or ignore <style> in different ways and Gmail ignores CSS custom properties, so every
   var(--x) is resolved from the page's :root / html rules, clamp(a, b, c) becomes a, and each rule whose selector is a
   plain chain of tag / .class / #id compounds (descendant or child) is written into the matching elements' style
   attributes by specificity and order; the element's own style wins. <body>'s style is also put on a wrapping <div>
   (Gmail drops the body element) and HTML5 sectioning tags (main, header, nav, section …, which Gmail also drops with
   their style) are written as <div>. The resolved sheet stays in <head> for clients that honour it (@media, :hover).
"""
from __future__ import annotations

import html as H
import html.parser
import re

DROP_ALL = {"script", "iframe", "object", "embed", "applet", "frameset", "frame", "template", "textarea", "select"}
DROP_TAG = {"form", "button", "noscript"}
DROP_VOID = {"input", "link", "base"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SECTION = {"main", "header", "footer", "nav", "section", "article", "aside", "figure", "figcaption", "hgroup", "address"}
URL_ATTRS = {"href", "src", "background", "poster", "xlink:href", "cite", "longdesc"}
_SAFE_URL = re.compile(r"^(https?:|mailto:|tel:|#)", re.I)
_DATA_IMG = re.compile(r"^data:image/(png|jpe?g|gif|webp);", re.I)
_BAD_CSS = re.compile(r"expression\s*\(|javascript:|vbscript:|behavior\s*:|-moz-binding", re.I)


def _attr(k: str, v) -> str:
    return f" {k}" if v is None else f' {k}="{H.escape(v, quote=True)}"'


class _Safe(html.parser.HTMLParser):
    def __init__(self, text_fn):
        super().__init__(convert_charrefs=False)
        self.out, self.drop, self.fn = [], [], text_fn or (lambda s: s)
        self.info = {"removed": 0, "local_images": 0}

    def handle_decl(self, decl):
        if not self.drop:
            self.out.append(f"<!{decl}>")

    def _keep(self, tag, attrs) -> list | None:
        keep = []
        for k, v in attrs:
            k = k.lower()
            if k.startswith("on") or k in ("action", "formaction", "srcset", "srcdoc", "ping"):
                self.info["removed"] += 1
                continue
            if k in URL_ATTRS and v is not None:
                u = H.unescape(v).strip()
                if tag == "img" and k == "src" and not (_SAFE_URL.match(u) and not u.startswith("#") or _DATA_IMG.match(u)):
                    return None                      # a local / odd image cannot travel
                if not (_SAFE_URL.match(u) or (k == "src" and _DATA_IMG.match(u))):
                    self.info["removed"] += 1
                    continue
                v = self.fn(u) if k == "href" else u
            if k == "style" and v and _BAD_CSS.search(H.unescape(v)):
                self.info["removed"] += 1
                continue
            keep.append((k, v))
        return keep

    def handle_starttag(self, tag, attrs, end=""):
        if self.drop:
            if tag in DROP_ALL and tag not in VOID:
                self.drop.append(tag)
            return
        if tag in DROP_ALL:
            self.info["removed"] += 1
            if tag not in VOID and not end:
                self.drop.append(tag)
            return
        if tag in DROP_TAG or tag in DROP_VOID or (tag == "meta" and any(k.lower() == "http-equiv" for k, _ in attrs)):
            self.info["removed"] += tag not in ("noscript",)
            return
        keep = self._keep(tag, attrs)
        if keep is None:
            self.info["local_images"] += 1
            alt = next((v for k, v in attrs if k.lower() == "alt" and v), "")
            if alt:
                self.out.append(f"<span>[{H.escape(self.fn(alt))}]</span>")
            return
        self.out.append(f"<{tag}{''.join(_attr(k, v) for k, v in keep)}{end}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs, " /")

    def handle_endtag(self, tag):
        if self.drop:
            if tag == self.drop[-1]:
                self.drop.pop()
            return
        if tag in DROP_ALL or tag in DROP_TAG or tag in DROP_VOID or tag in VOID:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.drop:
            return
        last = self.out[-1] if self.out else ""
        raw = last.startswith("<style")       # CSS is not text: never redacted, never escaped
        self.out.append(data if raw else H.escape(self.fn(H.unescape(data)), quote=False))

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")

    def handle_comment(self, data):
        pass

    def handle_pi(self, data):
        pass


def sanitize(src: str, text_fn=None) -> tuple[str, dict]:
    p = _Safe(text_fn)
    p.feed(str(src or ""))
    p.close()
    return "".join(p.out), p.info


# ---------------------------------------------------------------- CSS → inline
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_COMPOUND = re.compile(r"^([a-z][a-z0-9]*)?((?:[.#][A-Za-z_][\w-]*)*)$")
_ROOTSEL = re.compile(r"^(:root|html)(\[[^\]]*\])*$|^\[data-[\w-]+(=[^\]]*)?\]$")


def _rules(css: str):
    """Top-level (selector, body) pairs and @-blocks as (None, raw)."""
    css, out, i = _COMMENT.sub("", css), [], 0
    while i < len(css):
        j = css.find("{", i)
        if j < 0:
            break
        sel = css[i:j].strip()
        depth, k = 1, j + 1
        while k < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        body = css[j + 1:k - 1]
        out.append((None, f"{sel}{{{body}}}") if sel.startswith("@") else (sel, body))
        i = k
    return out


def _decls(body: str) -> list[tuple[str, str]]:
    out = []
    for d in re.split(r";(?![^(]*\))", body):
        k, sep, v = d.partition(":")
        if sep and k.strip() and v.strip():
            out.append((k.strip().lower(), v.strip()))
    return out


def _resolve(v: str, vars_: dict, depth: int = 0) -> str | None:
    if depth > 8:
        return None
    for _ in range(50):
        m = re.search(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\)[^()]*)*))?\)", v)
        if not m:
            break
        rep = vars_.get(m[1], m[2])
        if rep is None:
            return None
        rep = _resolve(rep.strip(), vars_, depth + 1)
        if rep is None:
            return None
        v = v[:m.start()] + rep + v[m.end():]
    v = re.sub(r"clamp\(\s*([^,()]+),[^()]*(?:\([^()]*\))?[^()]*\)", lambda m: m[1].strip(), v)
    return None if "var(" in v or _BAD_CSS.search(v) else v


def _selector(sel: str):
    """→ [(tag, classes, id, combinator)] right to left, or None when not a plain chain."""
    parts = re.split(r"\s*(>)\s*|\s+", sel.strip())
    parts = [p for p in parts if p]
    chain, comb = [], " "
    for p in parts:
        if p == ">":
            comb = ">"
            continue
        m = _COMPOUND.match(p)
        if not m or p in ("",):
            return None
        tag = m[1]
        classes = set(re.findall(r"\.([\w-]+)", m[2]))
        ids = re.findall(r"#([\w-]+)", m[2])
        if not tag and not classes and not ids:
            return None
        chain.append((tag, classes, ids[0] if ids else None, comb))
        comb = " "
    return chain[::-1] if chain else None


def _spec(chain) -> tuple[int, int, int]:
    return (sum(1 for c in chain if c[2]), sum(len(c[1]) for c in chain), sum(1 for c in chain if c[0]))


def _hit(c, el) -> bool:
    tag, classes, id_, _ = c
    return (not tag or tag == el[0]) and classes <= el[1] and (not id_ or id_ == el[2])


def _match(chain, stack) -> bool:
    if not stack or not _hit(chain[0], stack[-1]):
        return False
    i, pos = 1, len(stack) - 2
    while i < len(chain):
        child_only = chain[i - 1][3] == ">"
        while pos >= 0 and not _hit(chain[i], stack[pos]):
            if child_only:
                return False
            pos -= 1
        if pos < 0:
            return False
        i, pos = i + 1, pos - 1
    return True


class _Inline(html.parser.HTMLParser):
    def __init__(self, rules):
        super().__init__(convert_charrefs=False)
        self.rules, self.out, self.stack, self.css, self.in_style, self.body = rules, [], [], None, False, None

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def _style(self, tag, attrs):
        cls = set((dict(attrs).get("class") or "").split())
        el = (tag, cls, dict(attrs).get("id"))
        self.stack.append(el)
        got: dict[str, str] = {}
        for _spec_, _order, chain, decls in self.rules:
            if _match(chain, self.stack):
                for k, v in decls:
                    got.pop(k, None)
                    got[k] = v
        if tag in VOID:
            self.stack.pop()
        own = dict(attrs).get("style")
        merged = "".join(f"{k}:{v};" for k, v in got.items()).replace('"', "'") + (own or "")
        return [(k, v) for k, v in attrs if k != "style"] + ([("style", merged)] if merged else [])

    def handle_starttag(self, tag, attrs, end=""):
        if tag == "style":
            self.in_style = True
            self.out.append("<style>")
            return
        if tag in ("html", "head", "meta", "title"):
            if tag in ("html", "head"):
                self.stack.append((tag, set((dict(attrs).get("class") or "").split()), dict(attrs).get("id")))
            self.out.append(f"<{tag}{''.join(_attr(k, v) for k, v in attrs)}{end}>")
            return
        attrs = self._style(tag, attrs)
        out = "div" if tag in SECTION else tag     # Gmail drops HTML5 sectioning tags and their style with them
        self.out.append(f"<{out}{''.join(_attr(k, v) for k, v in attrs)}{end}>")
        if tag == "body":
            self.body = dict(attrs).get("style") or ""
            self.out.append(f'<div class="aj-body" style="{H.escape(self.body, quote=True)}">')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs, " /")

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False
            self.out.append(f"{self.css or ''}</style>")
            self.css = ""
            return
        if tag == "body" and self.body is not None:
            self.out.append("</div>")
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag not in VOID:
            self.out.append(f"</{'div' if tag in SECTION else tag}>")

    def handle_data(self, data):
        if not self.in_style:
            self.out.append(data)

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")


def inline_css(src: str) -> str:
    sheets = re.findall(r"<style[^>]*>(.*?)</style>", src, re.S | re.I)
    pairs = [r for css in sheets for r in _rules(css)]
    vars_ = {}
    for sel, body in pairs:
        if sel and any(_ROOTSEL.match(s.strip()) for s in sel.split(",")):
            vars_.update({k: v for k, v in _decls(body) if k.startswith("--")})
    rules, rest = [], []
    for n, (sel, body) in enumerate(pairs):
        if sel is None:
            res = re.sub(r"var\([^()]*(?:\([^()]*\))?[^()]*\)", lambda m: _resolve(m[0], vars_) or "inherit", body)
            rest.append(res)
            continue
        decls = [(k, r) for k, v in _decls(body) if not k.startswith("--") for r in [_resolve(v, vars_)] if r is not None]
        if not decls:
            continue
        rest.append(f"{sel}{{{''.join(f'{k}:{v};' for k, v in decls)}}}")
        for s in sel.split(","):
            chain = _selector(s)
            if chain:
                rules.append((_spec(chain), n, chain, decls))
    rules.sort(key=lambda r: (r[0], r[1]))
    p = _Inline(rules)
    p.css = "".join(rest)
    first = [True]

    def once(m):                                  # every sheet folds into the first <style>
        if first[0]:
            first[0] = False
            return m[0]
        return ""
    src = re.sub(r"<style[^>]*>.*?</style>", once, src, flags=re.S | re.I)
    p.feed(src)
    p.close()
    return "".join(p.out)


def prepare(src: str, text_fn=None) -> tuple[str, dict]:
    safe, info = sanitize(src, text_fn)
    return inline_css(safe), info


def looks_html(s: str) -> bool:
    return bool(re.match(r"\s*(<!doctype\s+html|<html[\s>])", str(s or ""), re.I))
