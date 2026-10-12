"""P184: the owner's address book — kept by the Agent, on this computer only (`<state>/contacts/contacts.json`, 0600).

Leo (10-12): the Agent remembers every address its owner had it mail, imports a whole CSV / vCard export once, and finds
「张珊」 when the owner says 「张三」. Fields follow vCard 4.0 (RFC 6350) and the Google / Outlook CSV exports:
name (FN), nicknames (NICKNAME), several e-mails / phones / addresses with a label (TYPE), organisation, title, birthday,
note — plus what only we know: source (owner · agent · phone · import:<format>), last_sent_at, send_count.

Search (`find`) is a plain table walk with a pinyin index, no vector store: an address book holds hundreds of names,
the questions are names and spellings, and what a voice transcript gets wrong is the characters, not the meaning — an
embedding model would not know 「李思」 and 「李四」 sound the same, pinyin does. Scoring (highest wins, one per contact):
exact name / nickname / address → prefix / substring → same pinyin (every reading of a polyphone: 单 dan/shan, 曾 zeng/ceng)
→ fuzzy pinyin (zh=z ch=c sh=s, n=l, r=l, f=h, -ng=-n: the southern merges an ASR also makes) → initials (zs → 张三)
→ edit distance on the fuzzy pinyin / Latin name. pypinyin is already a dependency (wake words).

Dedupe on import and add: the same e-mail or the same phone number (digits; a +86 / 0086 prefix ignored) = the same
person → fields are merged (union of e-mails, phones, addresses, nicknames; an empty name / org / title is filled; notes
are appended once). Same name and nothing that disagrees (no two different e-mails, no two different phones) → merged; same
name with different e-mails → two contacts (two 张伟 are common).

Nothing here leaves the computer: no cloud, no relay copy; the phone reads it inside the Noise session (contacts_serve).
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import pathlib
import re
import secrets
import time
import unicodedata

DIR = "contacts"
FILE = "contacts.json"
LOCK = "contacts.lock"
MAX_CONTACTS = 5000
MAX_EACH = 10                       # e-mails / phones / nicknames per contact
MAX_ADDR = 5
TEXT = {"name": 120, "org": 120, "title": 120, "note": 2000, "bday": 20, "label": 30, "nick": 60, "addr": 300}
SOURCES = ("owner", "phone", "agent", "import", "mail")
SORTS = ("recent", "frequent", "name")
IMPORT_MAX = 20 * 1024 * 1024
EMAIL = re.compile(r"[A-Za-z0-9._%+'=-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+")
CJK = re.compile(r"[㐀-鿿豈-﫿]")
ID = re.compile(r"c[0-9a-f]{12}")


class ContactError(ValueError):
    def __init__(self, why: str, detail: str = ""):
        super().__init__(why)
        self.why, self.detail = why, detail


# ------------------------------------------------------------------ normalisation
def _s(v, n: int) -> str:
    v = unicodedata.normalize("NFC", str(v or "")).replace("\x00", "")
    v = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v).strip()
    return v[:n]


def norm_email(a) -> str:
    a = _s(a, 254).strip("<>\"' ;,").lower()
    if a.startswith("mailto:"):
        a = a[7:]
    return a if EMAIL.fullmatch(a) else ""


def norm_phone(p) -> str:
    """As written, minus noise (keeps a leading +, digits, spaces, dashes, brackets, `ext`)."""
    p = _s(p, 40)
    if p.lower().startswith("tel:"):
        p = p[4:]
    p = re.sub(r"[^\d+()\-\s.x#*]", "", p).strip()
    return p if len(re.sub(r"\D", "", p)) >= 3 else ""


def phone_key(p: str) -> str:
    d = re.sub(r"\D", "", p or "")
    if d.startswith("0086"):
        d = d[4:]
    elif d.startswith("86") and len(d) == 13:
        d = d[2:]
    return d


def name_key(n: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", n or "")).lower()


def _labeled(items, norm, limit: int) -> list[dict]:
    out, seen = [], set()
    for it in items or []:
        if isinstance(it, str):
            it = {"v": it}
        if not isinstance(it, dict):
            continue
        v = norm(it.get("v") if "v" in it else it.get("value", it.get("addr", it.get("num", ""))))
        k = v.lower() if norm is not norm_phone else phone_key(v)
        if not v or k in seen:
            continue
        seen.add(k)
        out.append({"v": v, "label": _s(it.get("label") or it.get("type") or "", TEXT["label"]).lower()})
        if len(out) >= limit:
            break
    return out


def clean(raw: dict, *, partial: bool = False) -> dict:
    """Any contact-shaped dict (ours, or a simple {name, email, phone}) → our record's fields. Raises ContactError."""
    if not isinstance(raw, dict):
        raise ContactError("shape")
    emails = raw.get("emails", [])
    phones = raw.get("phones", [])
    for k in ("email", "e-mail", "mail"):
        if raw.get(k):
            emails = list(emails) + (raw[k] if isinstance(raw[k], list) else re.split(r"[;,\s]+", str(raw[k])))
    for k in ("phone", "tel", "mobile"):
        if raw.get(k):
            phones = list(phones) + (raw[k] if isinstance(raw[k], list) else re.split(r"[;,/]+", str(raw[k])))
    nicks = raw.get("nicks", raw.get("nicknames", []))
    if raw.get("nick") or raw.get("nickname"):
        nicks = list(nicks if isinstance(nicks, list) else [nicks]) + re.split(r"[,，;；、]+", str(raw.get("nick") or raw.get("nickname")))
    if isinstance(nicks, str):
        nicks = re.split(r"[,，;；、]+", nicks)
    addrs = raw.get("addresses", [])
    if raw.get("address"):
        addrs = list(addrs) + [raw["address"]]
    c = {"name": _s(raw.get("name") or raw.get("fn"), TEXT["name"]),
         "nicks": list(dict.fromkeys(n for n in (_s(x, TEXT["nick"]) for x in nicks or []) if n))[:MAX_EACH],
         "emails": _labeled(emails, norm_email, MAX_EACH), "phones": _labeled(phones, norm_phone, MAX_EACH),
         "addresses": _labeled(addrs, lambda v: _s(v, TEXT["addr"]), MAX_ADDR),
         "org": _s(raw.get("org") or raw.get("company"), TEXT["org"]), "title": _s(raw.get("title"), TEXT["title"]),
         "bday": _s(raw.get("bday") or raw.get("birthday"), TEXT["bday"]), "note": _s(raw.get("note") or raw.get("notes"), TEXT["note"])}
    if not c["name"]:
        c["name"] = c["nicks"][0] if c["nicks"] else (c["emails"][0]["v"].split("@")[0] if c["emails"] else "")
    if not partial and not c["emails"] and not c["phones"]:
        raise ContactError("no_contact", "at least one e-mail or phone / 至少一个邮箱或电话")
    if not partial and not c["name"]:
        raise ContactError("no_name")
    return c


# ------------------------------------------------------------------ storage
def root(st) -> pathlib.Path:
    d = pathlib.Path(st.root) / DIR
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(d, 0o700)
    return d


def _blank() -> dict:
    return {"v": 1, "contacts": []}


def read(st) -> dict:
    try:
        book = json.loads((root(st) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank()
    if not isinstance(book, dict) or not isinstance(book.get("contacts"), list):
        return _blank()
    book["contacts"] = [c for c in book["contacts"] if isinstance(c, dict) and isinstance(c.get("id"), str)]
    return book


def write(st, book: dict) -> None:
    d = root(st)
    tmp = d / f".{FILE}.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(book, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, d / FILE)


@contextlib.contextmanager
def edit_book(st):
    """Read-modify-write under an exclusive lock (the CLI, serve and the phone may write at the same time)."""
    import fcntl
    fd = os.open(root(st) / LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        book = read(st)
        yield book
        write(st, book)
    finally:
        os.close(fd)


def get(book: dict, cid: str) -> dict | None:
    return next((c for c in book["contacts"] if c["id"] == cid), None)


def by_email(book: dict, addr: str) -> dict | None:
    a = norm_email(addr)
    return next((c for c in book["contacts"] if any(e["v"] == a for e in c.get("emails", []))), None) if a else None


def _match(book: dict, c: dict) -> dict | None:
    em = {e["v"] for e in c["emails"]}
    ph = {phone_key(p["v"]) for p in c["phones"]} - {""}
    for o in book["contacts"]:
        if em & {e["v"] for e in o.get("emails", [])}:
            return o
        if ph & {phone_key(p["v"]) for p in o.get("phones", [])}:
            return o
    nk = name_key(c["name"])
    for o in book["contacts"]:
        if not nk or name_key(o.get("name", "")) != nk:
            continue
        oem = {e["v"] for e in o.get("emails", [])}
        oph = {phone_key(p["v"]) for p in o.get("phones", [])} - {""}
        if not (em and oem) and not (ph and oph):       # same name, no e-mail vs e-mail or phone vs phone that disagree
            return o
    return None


def merge_into(o: dict, c: dict) -> list[str]:
    """Union c into o; → the fields that changed."""
    changed = []
    for k in ("name", "org", "title", "bday"):
        if c.get(k) and not o.get(k):
            o[k] = c[k]
            changed.append(k)
    if c.get("note") and c["note"] not in (o.get("note") or ""):
        o["note"] = ((o.get("note") + "\n") if o.get("note") else "") + c["note"]
        o["note"] = o["note"][:TEXT["note"]]
        changed.append("note")
    if c.get("name") and o.get("name") and name_key(c["name"]) != name_key(o["name"]) and c["name"] not in o.get("nicks", []):
        o.setdefault("nicks", []).append(c["name"])        # another spelling the owner used → a nickname, so `find` hits it
        changed.append("nicks")
    for k, keyf, limit in (("emails", lambda v: v.lower(), MAX_EACH), ("phones", phone_key, MAX_EACH),
                           ("addresses", lambda v: v.lower(), MAX_ADDR)):
        have = {keyf(x["v"]) for x in o.get(k, [])}
        for x in c.get(k, []):
            if keyf(x["v"]) not in have and len(o.get(k, [])) < limit:
                o.setdefault(k, []).append(x)
                have.add(keyf(x["v"]))
                changed.append(k)
    for n in c.get("nicks", []):
        if n not in o.get("nicks", []) and n != o.get("name") and len(o.get("nicks", [])) < MAX_EACH:
            o.setdefault("nicks", []).append(n)
            changed.append("nicks")
    if changed:
        o["updated"] = int(time.time())
    return list(dict.fromkeys(changed))


def add(book: dict, raw: dict, source: str = "owner", merge: bool = True) -> tuple[dict, str]:
    """→ (the contact, "added" | "merged" | "same")."""
    c = clean(raw)
    o = _match(book, c) if merge else None
    if o is not None:
        return o, "merged" if merge_into(o, c) else "same"
    if len(book["contacts"]) >= MAX_CONTACTS:
        raise ContactError("full", f"≤ {MAX_CONTACTS}")
    now = int(time.time())
    rec = {"id": "c" + secrets.token_hex(6), **c, "source": source if source in SOURCES else source[:20],
           "created": now, "updated": now, "last_sent_at": None, "send_count": 0}
    book["contacts"].append(rec)
    return rec, "added"


def update(book: dict, cid: str, changes: dict) -> dict:
    """Replace the given fields (a whole list replaces the list); `add_*` / `rm_*` keys add or remove single values."""
    o = get(book, cid)
    if o is None:
        raise ContactError("not_found")
    new = dict(o)
    for k in ("name", "org", "title", "bday", "note", "nicks", "emails", "phones", "addresses"):
        if k in changes:
            new[k] = changes[k]
    for k, field in (("add_email", "emails"), ("add_phone", "phones"), ("add_address", "addresses"), ("add_nick", "nicks")):
        for v in changes.get(k) or []:
            new[field] = list(new.get(field) or []) + [v]
    for k, field, keyf in (("rm_email", "emails", norm_email), ("rm_phone", "phones", phone_key), ("rm_nick", "nicks", str)):
        for v in changes.get(k) or []:
            kv = keyf(v)
            new[field] = [x for x in new.get(field) or [] if (keyf(x["v"]) if isinstance(x, dict) else x) != kv]
    c = clean(new)
    o.update(c)
    o["updated"] = int(time.time())
    return o


def remove(book: dict, cid: str) -> dict:
    o = get(book, cid)
    if o is None:
        raise ContactError("not_found")
    book["contacts"].remove(o)
    return o


def record_sent(st, recipients: list, source: str = "agent") -> list[dict]:
    """After a mail went out: each address is remembered (a new contact, marked with its source) and counted.
    recipients: [addr | {addr, name}]. → [{addr, id, added}]."""
    out, now = [], int(time.time())
    with edit_book(st) as book:
        for r in recipients:
            addr = norm_email(r.get("addr") if isinstance(r, dict) else r)
            if not addr:
                continue
            name = _s(r.get("name"), TEXT["name"]) if isinstance(r, dict) else ""
            o, added = by_email(book, addr), False
            if o is None:
                if len(book["contacts"]) >= MAX_CONTACTS:
                    continue
                o, _ = add(book, {"name": name or addr.split("@")[0], "emails": [addr]}, source=source, merge=False)
                added = True
            elif name and name_key(name) != name_key(o.get("name", "")) and name not in o.get("nicks", []):
                o.setdefault("nicks", []).append(name)
            o["last_sent_at"], o["send_count"] = now, int(o.get("send_count") or 0) + 1
            out.append({"addr": addr, "id": o["id"], "added": added})
    return out


# ------------------------------------------------------------------ pinyin + search
_PY_CACHE: dict = {}
# Toneless Mandarin syllables (from pypinyin's table): a Latin run such as "zhangsan" is cut into them, so the fuzzy rules
# apply per syllable whether the owner typed pinyin or the ASR wrote characters.
_SYL = frozenset((
    "a ai an ang ao ba bai ban bang bao bei ben beng bi bian biang biao bie bin bing bo bong bu ca cai can cang cao "
    "ce cei cen ceng cha chai chan chang chao che chen cheng chi chong chou chu chua chuai chuan chuang chui chun "
    "chuo ci cong cou cu cuan cui cun cuo da dai dan dang dao de dei den deng di dia dian diao die din ding diu "
    "dong dou du duan dui dun duo e ei en eng er fa fan fang fei fen feng fiao fo fou fu ga gai gan gang gao ge gei "
    "gen geng gong gou gu gua guai guan guang gui gun guo ha hai han hang hao he hei hen heng hong hou hu hua huai "
    "huan huang hui hun huo ji jia jian jiang jiao jie jin jing jiong jiu ju juan jue jun ka kai kan kang kao ke "
    "kei ken keng kong kou ku kua kuai kuan kuang kui kun kuo la lai lan lang lao le lei len leng li lia lian liang "
    "liao lie lin ling liu lo long lou lu luan lun luo lv lve ma mai man mang mao me mei men meng mi mian miao mie "
    "min ming miu mo mou mu na nai nan nang nao ne nei nen neng ni nia nian niang niao nie nin ning niu nong nou nu "
    "nuan nun nuo nv nve o ou pa pai pan pang pao pei pen peng pi pian piao pie pin ping po pou pu qi qia qian "
    "qiang qiao qie qin qing qiong qiu qu quan que qun ran rang rao re ren reng ri rong rou ru rua ruan rui run ruo "
    "sa sai san sang sao se sen seng sha shai shan shang shao she shei shen sheng shi shou shu shua shuai shuan "
    "shuang shui shun shuo si song sou su suan sui sun suo ta tai tan tang tao te tei teng ti tian tiao tie ting "
    "tong tou tu tuan tui tun tuo wa wai wan wang wei wen weng wo wong wu xi xia xian xiang xiao xie xin xing xiong "
    "xiu xu xuan xue xun ya yan yang yao ye yi yin ying yo yong you yu yuan yue yun za zai zan zang zao ze zei zen "
    "zeng zha zhai zhan zhang zhao zhe zhei zhen zheng zhi zhong zhou zhu zhua zhuai zhuan zhuang zhui zhun zhuo zi "
    "zong zou zu zuan zui zun zuo "
).split())
# The surname reading where the first reading of the character is not the surname one (sorting / the letter index).
SURNAME = {"曾": "zeng", "单": "shan", "解": "xie", "区": "ou", "仇": "qiu", "朴": "piao", "查": "zha", "乐": "yue", "尉": "yu",
           "覃": "qin", "翟": "zhai", "召": "shao", "盖": "ge", "繁": "po", "员": "yun", "种": "chong", "缪": "miao", "重": "chong",
           "长": "chang", "秘": "bi", "万": "wan", "能": "nai", "都": "du", "沈": "shen", "柏": "bai", "薄": "bo", "任": "ren",
           "宿": "su", "车": "che", "贾": "jia", "石": "shi", "华": "hua", "纪": "ji", "藏": "zang", "句": "gou", "朝": "chao"}


def segment(run: str) -> list[str]:
    """"zhangsan" → ["zhang", "san"] (fewest syllables); a run that is not all pinyin stays one word."""
    n = len(run)
    if n < 2 or n > 40 or not run.isalpha():
        return [run]
    best: list = [None] * (n + 1)
    best[0] = []
    for i in range(n):
        if best[i] is None:
            continue
        for j in range(min(n, i + 6), i, -1):
            if run[i:j] in _SYL and (best[j] is None or len(best[j]) > len(best[i]) + 1):
                best[j] = best[i] + [run[i:j]]
    return best[n] or [run]


def syllables(text: str, cap: int = 16) -> list[list[str]]:
    """Every reading of `text` as a list of syllables (CJK → pinyin, other runs → lower-case words), ≤ cap variants."""
    key = (text, cap)
    if key in _PY_CACHE:
        return _PY_CACHE[key]
    try:
        from pypinyin import Style, pinyin
    except ImportError:                     # the package declares it; a broken install only loses the pinyin rungs
        pinyin = None
    variants: list[list[str]] = [[]]
    for run in re.findall(r"[㐀-鿿豈-﫿]+|[A-Za-z0-9]+", unicodedata.normalize("NFKC", text or "")):
        if CJK.match(run) and pinyin is not None:
            for readings in pinyin(run, style=Style.NORMAL, heteronym=True, errors="ignore"):
                rs = list(dict.fromkeys(r.replace("ü", "v") for r in readings if r))[:3] or [""]
                variants = [v + [r] for v in variants for r in rs][:cap]
        else:
            variants = [v + segment(run.lower()) for v in variants]
    _PY_CACHE[key] = variants
    if len(_PY_CACHE) > 4096:
        _PY_CACHE.clear()
    return variants


def fuzzy(s: str) -> str:
    """One syllable → its fuzzy class (the merges a southern speaker and an ASR make)."""
    if s[:2] in ("zh", "ch", "sh"):
        s = s[0] + s[2:]
    elif s[:1] == "n" and len(s) > 1:
        s = "l" + s[1:]
    elif s[:1] == "r":
        s = "l" + s[1:]
    elif s[:1] == "f":
        s = "h" + s[1:]
    if s.endswith(("ang", "eng", "ing")):
        s = s[:-1]
    return s


def initials(sy: list[str]) -> str:
    return "".join(s[0] for s in sy if s)


def lev(a: str, b: str, cap: int = 3) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _keys(c: dict) -> list[str]:
    return [x for x in [c.get("name", "")] + list(c.get("nicks", [])) if x]


def score(c: dict, q: str) -> int:
    ql = name_key(q)
    if not ql:
        return 0
    best = 0
    for e in c.get("emails", []):
        v, local = e["v"], e["v"].split("@")[0]
        if ql == v:
            return 100
        if ql == local:
            best = max(best, 92)
        elif v.startswith(ql):
            best = max(best, 85)
        elif len(ql) >= 3 and ql in v:
            best = max(best, 70)
    if any(re.sub(r"\D", "", ql) and len(re.sub(r"\D", "", ql)) >= 4 and re.sub(r"\D", "", ql) in phone_key(p["v"])
           for p in c.get("phones", [])) and re.fullmatch(r"[\d+\-\s()]+", q.strip()):
        best = max(best, 80)
    qsy = syllables(q)
    qlatin = not CJK.search(q)
    qjoin = [("".join(v), "".join(fuzzy(s) for s in v)) for v in qsy]
    for key in _keys(c):
        k = name_key(key)
        if k == ql:
            return 100
        if k.startswith(ql):
            best = max(best, 88)
        elif ql in k:
            best = max(best, 80 if CJK.search(ql) or len(ql) >= 3 else 60)
        if not CJK.search(key + q) and len(ql) >= 3:     # Latin names: one word off by a letter or two (Jon → John)
            for w in re.findall(r"[a-z0-9]+", key.lower()):
                d = lev(ql, w, 2)
                if d == 1 or d == 2 and len(ql) >= 6:
                    best = max(best, 44 if d == 1 else 34)
        for sy in syllables(key):
            full, fz = "".join(sy), "".join(fuzzy(s) for s in sy)
            for qf, qz in qjoin:
                if not qf:
                    continue
                if qf == full:
                    best = max(best, 76)
                elif qz == fz:
                    best = max(best, 68)
                elif len(qf) >= 3 and full.startswith(qf):
                    best = max(best, 58)
                elif len(qf) >= 4 and (qf in full or qz in fz):
                    best = max(best, 52)
                else:
                    d = lev(qz, fz)
                    if d == 1 and len(qz) >= 4 or d == 2 and len(qz) >= 7:
                        best = max(best, 46 if d == 1 else 36)
            if qlatin and len(ql) >= 2 and ql.isalpha():
                ini = initials(sy)
                if ql == ini:
                    best = max(best, 62)
                elif len(ql) >= 2 and ini.startswith(ql) and CJK.search(key):
                    best = max(best, 48)
    return best


def find(book: dict, q: str, limit: int = 10, floor: int = 36) -> list[tuple[int, dict]]:
    hits = [(score(c, q), c) for c in book["contacts"]]
    hits = [h for h in hits if h[0] >= floor]
    hits.sort(key=lambda h: (-h[0], -(h[1].get("send_count") or 0), -(h[1].get("last_sent_at") or 0), sort_key(h[1])))
    return hits[:limit]


def sort_key(c: dict) -> str:
    name = c.get("name") or ""
    sy = syllables(name)
    s = " ".join(sy[0]) if sy and sy[0] else ""
    if name[:1] in SURNAME and s:
        s = SURNAME[name[0]] + s[len(s.split(" ")[0]):]
    return (s or name_key(c.get("name", ""))) + "\x00" + name_key(c.get("name", ""))


def letter(c: dict) -> str:
    k = sort_key(c)[:1].upper()
    return k if "A" <= k <= "Z" else "#"


def ordered(book: dict, how: str = "recent") -> list[dict]:
    cs = list(book["contacts"])
    if how == "name":
        return sorted(cs, key=lambda c: ("#" == letter(c), sort_key(c)))
    if how == "frequent":
        return sorted(cs, key=lambda c: (-(c.get("send_count") or 0), -(c.get("last_sent_at") or 0), sort_key(c)))
    return sorted(cs, key=lambda c: (-(c.get("last_sent_at") or 0), -(c.get("send_count") or 0), -(c.get("updated") or 0), sort_key(c)))


def view(c: dict) -> dict:
    """What the phone / the Agent sees of one contact (everything but internal bookkeeping)."""
    return {k: c.get(k) for k in ("id", "name", "nicks", "emails", "phones", "addresses", "org", "title", "bday", "note", "source",
                                  "last_sent_at", "send_count")} | {"letter": letter(c)}


def missing(c: dict) -> list[str]:
    return [k for k, f in (("emails", "emails"), ("phones", "phones"), ("nicks", "nicks"), ("org", "org"), ("addresses", "addresses"))
            if not c.get(f)]


# ------------------------------------------------------------------ import
def decode(data: bytes) -> str:
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    if len(data) > 3 and data[1:2] == b"\x00" and data[3:4] == b"\x00":
        return data.decode("utf-16-le", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gb18030", "replace")       # Chinese Outlook / Foxmail / QQ exports


def sniff(name: str, text: str) -> str:
    n = (name or "").lower()
    head = text.lstrip()[:200].upper()
    if n.endswith((".vcf", ".vcard")) or head.startswith("BEGIN:VCARD"):
        return "vcf"
    if n.endswith(".json") or head[:1] in ("[", "{"):
        return "json"
    return "csv"


def _unescape(v: str) -> str:
    return re.sub(r"\\([nN,;:\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _qp(v: str, charset: str) -> str:
    import quopri
    try:
        return quopri.decodestring(v.encode("latin-1", "replace")).decode(charset or "utf-8", "replace")
    except LookupError:
        return quopri.decodestring(v.encode("latin-1", "replace")).decode("utf-8", "replace")


def _split(v: str, sep: str) -> list[str]:
    return [_unescape(x) for x in re.split(r"(?<!\\)" + re.escape(sep), v)]


def parse_vcard(text: str) -> list[dict]:
    """vCard 2.1 / 3.0 / 4.0 (Apple, Google, Outlook, Android, QQ / 163 exports) → simple dicts for `clean`."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"=\n", "=\x01", text)                       # 2.1 quoted-printable soft breaks (joined below)
    text = re.sub(r"\n[ \t]", "", text)                       # RFC 6350 §3.2 unfolding
    out, cur = [], None
    for line in text.split("\n"):
        line = line.replace("=\x01", "")
        if not line.strip():
            continue
        m = re.match(r"(?:[A-Za-z0-9-]+\.)?([A-Za-z0-9-]+)((?:;[^:]*)?):(.*)$", line)
        if not m:
            continue
        prop, params, value = m.group(1).upper(), m.group(2), m.group(3)
        if prop == "BEGIN" and value.strip().upper() == "VCARD":
            cur = {"emails": [], "phones": [], "addresses": [], "nicks": []}
            continue
        if cur is None:
            continue
        if prop == "END":
            out.append(cur)
            cur = None
            continue
        ps = [p for p in params.split(";") if p]
        types, charset, qp = [], "", False
        for p in ps:
            k, _, v = p.partition("=")
            k = k.upper()
            if not _:
                types.append(k.lower())                       # 2.1 bare TEL;CELL;PREF
            elif k == "TYPE":
                types += [t.strip('"').lower() for t in v.split(",")]
            elif k == "CHARSET":
                charset = v
            elif k == "ENCODING" and v.upper() == "QUOTED-PRINTABLE":
                qp = True
        if qp:
            value = _qp(value, charset)
        label = next((t for t in types if t not in ("pref", "internet", "voice", "x400")), "")
        if prop == "FN":
            cur["name"] = _unescape(value)
        elif prop == "N" and not cur.get("n"):
            parts = _split(value, ";") + [""] * 5
            fam, giv, mid = parts[0], parts[1], parts[2]
            cur["n"] = (fam + giv) if CJK.search(fam + giv) else " ".join(x for x in (giv, mid, fam) if x)
        elif prop == "NICKNAME":
            cur["nicks"] += [x for x in _split(value, ",") if x]
        elif prop == "EMAIL":
            cur["emails"].append({"v": _unescape(value), "label": label})
        elif prop == "TEL":
            cur["phones"].append({"v": _unescape(value), "label": label})
        elif prop == "ADR":
            parts = _split(value, ";")
            cur["addresses"].append({"v": ", ".join(x.replace("\n", " ") for x in parts if x.strip()), "label": label})
        elif prop == "ORG":
            cur["org"] = " ".join(x for x in _split(value, ";") if x)
        elif prop == "TITLE":
            cur["title"] = _unescape(value)
        elif prop == "NOTE":
            cur["note"] = _unescape(value)
        elif prop == "BDAY":
            cur["bday"] = value.strip()
    for c in out:
        if not c.get("name") and c.get("n"):
            c["name"] = c["n"]
    return out


def _hkey(h: str) -> str:
    return re.sub(r"[\s_\-:.()/]+", "", (h or "").lower())


NAME_H = {"name", "fullname", "displayname", "姓名", "名字", "联系人", "联系人姓名", "全名"}
GIVEN_H = {"firstname", "givenname", "first", "名"}
FAMILY_H = {"lastname", "familyname", "surname", "last", "姓"}
MIDDLE_H = {"middlename", "additionalname"}
NICK_H = {"nickname", "nick", "昵称", "别名", "alias"}
ORG_H = {"company", "organization", "organizationname", "organization1name", "公司", "单位", "工作单位", "公司名称"}
TITLE_H = {"jobtitle", "title", "organizationtitle", "organization1title", "职位", "职务", "头衔"}
NOTE_H = {"notes", "note", "备注", "说明"}
BDAY_H = {"birthday", "生日"}


def _col_kind(h: str) -> str | None:
    k = _hkey(h)
    if not k:
        return None
    if k in NAME_H:
        return "name"
    if k in GIVEN_H:
        return "given"
    if k in FAMILY_H:
        return "family"
    if k in MIDDLE_H:
        return "middle"
    if k in NICK_H:
        return "nick"
    if k in ORG_H:
        return "org"
    if k in TITLE_H:
        return "title"
    if k in NOTE_H:
        return "note"
    if k in BDAY_H:
        return "bday"
    if any(x in k for x in ("type", "label", "displayname", "类型")):
        return "label"
    if any(x in k for x in ("email", "mail", "邮箱", "邮件", "電郵", "郵件")):
        return "email"
    if "fax" in k or "传真" in k:
        return None
    if any(x in k for x in ("phone", "mobile", "tel", "cell", "手机", "电话", "電話", "移动", "pager")):
        return "phone"
    if "formatted" in k or k in ("address", "地址", "住址", "联系地址", "通讯地址"):
        return "address"
    for part in ("street", "city", "state", "postalcode", "zip", "country", "region", "pobox"):
        if part in k:
            return "addrpart"
    return None


def _label_of(h: str) -> str:
    k = _hkey(h)
    for word, label in (("mobile", "cell"), ("cell", "cell"), ("手机", "cell"), ("移动", "cell"), ("home", "home"), ("住宅", "home"),
                        ("家", "home"), ("business", "work"), ("work", "work"), ("办公", "work"), ("工作", "work"), ("other", "other")):
        if word in k:
            return label
    return ""


def parse_csv(text: str) -> list[dict]:
    """Google Contacts (old and new), Outlook / Outlook.com, Apple-tool, QQ / 163 / Foxmail CSV → simple dicts.
    The columns are recognised by name (English and Chinese), not by position."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return []
    head = rows[0]
    kinds = [_col_kind(h) for h in head]
    out = []
    for row in rows[1:]:
        if not any(x.strip() for x in row):
            continue
        c: dict = {"emails": [], "phones": [], "addresses": [], "nicks": []}
        given = family = middle = ""
        addr_parts: dict = {}
        for i, v in enumerate(row[:len(head)]):
            v = v.strip()
            if not v:
                continue
            k, h = kinds[i], head[i]
            if k == "name":
                c["name"] = v
            elif k == "given":
                given = v
            elif k == "family":
                family = v
            elif k == "middle":
                middle = v
            elif k == "nick":
                c["nicks"] += [x for x in re.split(r"[,，;；、]+", v) if x.strip()]
            elif k in ("org", "title", "note", "bday"):
                c[k] = v
            elif k == "email":
                label = _label_of(h)
                nxt = head[i + 1] if i + 1 < len(head) else ""
                if not label and i + 1 < len(row) and kinds[i + 1] == "label" and "value" not in _hkey(nxt):
                    label = row[i + 1].strip().strip("* ").lower()
                if i > 0 and kinds[i - 1] == "label" and re.search(r"\d", _hkey(head[i - 1])):
                    label = row[i - 1].strip().strip("* ").lower() or label
                for a in re.split(r"\s*:::\s*|[;,\s]+", v):        # Google joins several values with " ::: "
                    if a:
                        c["emails"].append({"v": a, "label": label})
            elif k == "phone":
                label = _label_of(h)
                if i > 0 and kinds[i - 1] == "label" and re.search(r"\d", _hkey(head[i - 1])):
                    label = row[i - 1].strip().strip("* ").lower() or label
                for p in re.split(r"\s*:::\s*|[;/]+", v):
                    if p.strip():
                        c["phones"].append({"v": p, "label": label})
            elif k == "address":
                c["addresses"].append({"v": v.replace("\n", ", "), "label": _label_of(h)})
            elif k == "addrpart":
                grp = _label_of(h) or "other"
                addr_parts.setdefault(grp, []).append(v)
        if not c.get("name") and (given or family):
            c["name"] = (family + given) if CJK.search(family + given) else " ".join(x for x in (given, middle, family) if x)
        for grp, parts in addr_parts.items():
            if not any(a["label"] == grp for a in c["addresses"]):
                c["addresses"].append({"v": ", ".join(parts), "label": grp if grp != "other" else ""})
        out.append(c)
    return out


def parse_json(text: str) -> list[dict]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("contacts", [])
    if not isinstance(data, list):
        raise ContactError("shape", "a list, or {contacts: [...]}")
    return [d for d in data if isinstance(d, dict)]


def import_data(st, data: bytes, name: str = "", fmt: str = "auto", dry_run: bool = False, source: str = "") -> dict:
    """Bytes of a .vcf / .csv / .json export → counts {added, merged, same, skipped, total, format}."""
    if len(data) > IMPORT_MAX:
        raise ContactError("too_big", f"≤ {IMPORT_MAX // (1024 * 1024)} MB")
    text = decode(data)
    fmt = sniff(name, text) if fmt in ("auto", "", None) else fmt
    try:
        rows = parse_vcard(text) if fmt == "vcf" else parse_json(text) if fmt == "json" else parse_csv(text)
    except (ValueError, csv.Error) as e:
        raise ContactError("parse", str(e)[:120]) from None
    res = {"format": fmt, "total": len(rows), "added": 0, "merged": 0, "same": 0, "skipped": 0, "skipped_rows": []}
    src = source or "import"
    with edit_book(st) as book:
        work = book if not dry_run else json.loads(json.dumps(book))
        for i, r in enumerate(rows):
            try:
                _, how = add(work, r, source=src)
            except ContactError as e:
                res["skipped"] += 1
                if len(res["skipped_rows"]) < 20:
                    res["skipped_rows"].append({"row": i + 1, "why": e.why})
                continue
            res[how] += 1
        if dry_run:
            return {**res, "dry_run": True}
    return res


# ------------------------------------------------------------------ export
def _esc(v: str) -> str:
    return (v or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _fold(line: str) -> str:
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


VC_TYPES = {"cell": "CELL", "mobile": "CELL", "home": "HOME", "work": "WORK", "other": "OTHER", "main": "MAIN", "fax": "FAX"}


def to_vcard(cs: list[dict], version: str = "3.0") -> str:
    """vCard 3.0 by default: what Apple Contacts, Google Contacts and Outlook all import; 4.0 on request."""
    lines = []
    for c in cs:
        name = c.get("name", "")
        if CJK.search(name) and len(name) >= 2:
            fam, giv = name[:1], name[1:]
        else:
            ws = name.split()
            fam, giv = (ws[-1], " ".join(ws[:-1])) if len(ws) > 1 else (name, "")
        L = ["BEGIN:VCARD", f"VERSION:{version}", "FN:" + _esc(name), f"N:{_esc(fam)};{_esc(giv)};;;"]
        if c.get("nicks"):
            L.append("NICKNAME:" + ",".join(_esc(n) for n in c["nicks"]))
        for e in c.get("emails", []):
            t = VC_TYPES.get(e.get("label") or "", "")
            L.append("EMAIL" + (f";TYPE={'INTERNET,' if version == '3.0' else ''}{t}" if t else (";TYPE=INTERNET" if version == "3.0" else "")) + ":" + e["v"])
        for p in c.get("phones", []):
            t = VC_TYPES.get(p.get("label") or "", "")
            L.append("TEL" + (f";TYPE={t}" if t else "") + ":" + p["v"])
        for a in c.get("addresses", []):
            t = VC_TYPES.get(a.get("label") or "", "")
            L.append("ADR" + (f";TYPE={t}" if t else "") + ":;;" + _esc(a["v"]) + ";;;;")
        for k, prop in (("org", "ORG"), ("title", "TITLE"), ("note", "NOTE"), ("bday", "BDAY")):
            if c.get(k):
                L.append(prop + ":" + (_esc(c[k]) if k != "bday" else c[k]))
        L.append("END:VCARD")
        lines += [_fold(x) for x in L]
    return "\r\n".join(lines) + ("\r\n" if lines else "")


GOOGLE_HEAD = ["Name", "Nickname", "Birthday", "Notes", "Organization 1 - Name", "Organization 1 - Title"]


def to_csv(cs: list[dict]) -> str:
    """Google Contacts CSV (Google and Outlook import it; Excel opens it — UTF-8 with BOM)."""
    ne = max([len(c.get("emails", [])) for c in cs] + [1])
    np_ = max([len(c.get("phones", [])) for c in cs] + [1])
    na = max([len(c.get("addresses", [])) for c in cs] + [1])
    head = list(GOOGLE_HEAD)
    for i in range(1, ne + 1):
        head += [f"E-mail {i} - Type", f"E-mail {i} - Value"]
    for i in range(1, np_ + 1):
        head += [f"Phone {i} - Type", f"Phone {i} - Value"]
    for i in range(1, na + 1):
        head += [f"Address {i} - Type", f"Address {i} - Formatted"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(head)
    cap = {"home": "Home", "work": "Work", "cell": "Mobile", "mobile": "Mobile", "other": "Other"}
    for c in cs:
        row = [c.get("name", ""), " ::: ".join(c.get("nicks", [])), c.get("bday", ""), c.get("note", ""), c.get("org", ""), c.get("title", "")]
        for k, n in (("emails", ne), ("phones", np_), ("addresses", na)):
            xs = c.get(k, [])
            for i in range(n):
                x = xs[i] if i < len(xs) else None
                row += [cap.get((x or {}).get("label") or "", (x or {}).get("label") or "") if x else "", x["v"] if x else ""]
        w.writerow(row)
    return "﻿" + buf.getvalue()


def export_text(book: dict, fmt: str = "vcf", version: str = "3.0") -> str:
    cs = ordered(book, "name")
    if fmt == "csv":
        return to_csv(cs)
    if fmt == "json":
        return json.dumps({"v": 1, "contacts": [view(c) for c in cs]}, ensure_ascii=False, indent=1)
    return to_vcard(cs, version)


# ------------------------------------------------------------------ CLI
def _call(st, req: dict, timeout: float = 30) -> dict | None:
    """Through serve (elevate.sock, visible inside the Agent's fence); None when serve is not running."""
    from .elevate import client_request
    res = client_request(st, {"t": "contacts", **req}, timeout=timeout)
    return None if res.get("result") == "unavailable" else res


def local(st, req: dict) -> dict:
    """The same operations on the file itself (serve calls this; the CLI too when serve is not running)."""
    op = req.get("op")
    try:
        if op == "list":
            book = read(st)
            cs = ordered(book, req.get("sort") if req.get("sort") in SORTS else "recent")
            lim = max(1, min(int(req.get("limit") or 200), MAX_CONTACTS))
            return {"ok": True, "count": len(book["contacts"]), "contacts": [view(c) for c in cs[:lim]]}
        if op == "find":
            hits = find(read(st), str(req.get("q") or ""), max(1, min(int(req.get("limit") or 10), 200)))
            return {"ok": True, "q": req.get("q"), "contacts": [{**view(c), "score": s} for s, c in hits]}
        if op == "get":
            c = get(read(st), str(req.get("id") or ""))
            return {"ok": True, "contact": view(c)} if c else {"ok": False, "why": "not_found"}
        if op == "add":
            with edit_book(st) as book:
                c, how = add(book, req.get("contact") or {}, source=req.get("source") or "agent", merge=not req.get("no_merge"))
            return {"ok": True, "result": how, "contact": view(c)}
        if op == "edit":
            with edit_book(st) as book:
                c = update(book, str(req.get("id") or ""), req.get("changes") or {})
            return {"ok": True, "result": "updated", "contact": view(c)}
        if op == "rm":
            with edit_book(st) as book:
                c = remove(book, str(req.get("id") or ""))
            return {"ok": True, "result": "removed", "contact": {"id": c["id"], "name": c.get("name")}}
        if op == "import":
            import base64
            data = base64.b64decode(req.get("data") or "")
            return {"ok": True, **import_data(st, data, req.get("name") or "", req.get("format") or "auto", bool(req.get("dry_run")),
                                              req.get("source") or "")}
        if op == "export":
            return {"ok": True, "format": req.get("format") or "vcf",
                    "text": export_text(read(st), req.get("format") or "vcf", req.get("version") or "3.0")}
        if op == "sent":
            return {"ok": True, "recorded": record_sent(st, req.get("recipients") or [], req.get("source") or "agent")}
    except ContactError as e:
        return {"ok": False, "why": e.why, **({"detail": e.detail} if e.detail else {})}
    except (TypeError, ValueError) as e:
        return {"ok": False, "why": "shape", "detail": str(e)[:120]}
    return {"ok": False, "why": "shape", "detail": "op"}


def _resolve(st, ref: str, served: bool) -> str | None:
    if ID.fullmatch(ref or ""):
        return ref
    req = {"op": "find", "q": ref, "limit": 2}
    res = (_call(st, req) if served else None) or local(st, req)
    hits = res.get("contacts") or []
    if len(hits) == 1 or (hits and hits[0]["score"] >= 92 and (len(hits) < 2 or hits[1]["score"] < hits[0]["score"])):
        return hits[0]["id"]
    return None


def cmd(a) -> int:
    import base64
    import sys
    from .state import State
    st = State()
    zh = not getattr(a, "en", False)

    def out(res: dict) -> int:
        if a.json or a.action not in ("list", "find") or not res.get("ok"):
            print(json.dumps(res, ensure_ascii=False, indent=None if a.json else 2))
        else:
            rows = res.get("contacts") or []
            if not rows:
                print(("没找到叫「{}」的人" if zh else "Nobody called “{}” in the address book").format(a.query) if a.action == "find"
                      else ("通讯录是空的" if zh else "The address book is empty"))
            for c in rows:
                em = ", ".join(e["v"] for e in c.get("emails") or [])
                ph = ", ".join(p["v"] for p in c.get("phones") or [])
                nk = ("（" + "、".join(c["nicks"]) + "）") if c.get("nicks") else ""
                sc = f"  [{c['score']}]" if "score" in c else ""
                print(f"{c['id']}  {c['name']}{nk}  {em}  {ph}  ×{c.get('send_count') or 0}{sc}")
        return 0 if res.get("ok") else 1

    def run(req: dict, timeout: float = 30) -> dict:
        res = _call(st, req, timeout)
        return res if res is not None else local(st, req)

    if a.action == "list":
        return out(run({"op": "list", "sort": a.sort, "limit": a.limit}))
    if a.action == "find":
        return out(run({"op": "find", "q": a.query or "", "limit": a.limit or 10}))
    contact = {"name": a.name or "", "nicks": a.nick or [], "emails": a.email or [], "phones": a.phone or [],
               "addresses": a.address or [], "org": a.org or "", "title": a.title or "", "note": a.note or ""}
    if a.action == "add":
        return out(run({"op": "add", "contact": contact, "source": a.source or "agent"}))
    if a.action == "card":
        served = _call(st, {"op": "ping"}, 5) is not None
        cid = _resolve(st, a.query, served) if a.query else None
        if a.query and not cid:
            return out({"ok": False, "why": "not_found", "detail": f"no single contact matches {a.query!r}; "
                        "omit it to create a new contact / 没有唯一匹配的联系人；不带它就新建"})
        req = {"op": "card", "id": cid, "contact": {k: v for k, v in contact.items() if v}, "why": a.why or ""}

        def early(m):
            print(f"CONTACT_CARD: {m.get('id')}", flush=True)
        from .elevate import client_request
        res = client_request(st, {"t": "contacts", **req}, timeout=700, on_card=early)
        if res.get("result") == "unavailable":
            res = {"ok": False, "why": "not_running", "detail": "联系人卡片要 Agent J 在运行（agentj service status）"
                   if zh else "the contact card needs Agent J running (agentj service status)"}
        return out(res)
    if a.action in ("edit", "rm"):
        served = _call(st, {"op": "ping"}, 5) is not None
        cid = _resolve(st, a.query or "", served)
        if not cid:
            return out({"ok": False, "why": "not_found", "detail": f"{a.query!r}: not exactly one contact (use the id from list/find)"})
        if a.action == "rm":
            return out(run({"op": "rm", "id": cid}))
        changes = {k: v for k, v in (("name", a.name), ("org", a.org), ("title", a.title), ("note", a.note)) if v is not None}
        changes.update(add_email=a.email or [], add_phone=a.phone or [], add_address=a.address or [], add_nick=a.nick or [],
                       rm_email=a.rm_email or [], rm_phone=a.rm_phone or [], rm_nick=a.rm_nick or [])
        return out(run({"op": "edit", "id": cid, "changes": changes}))
    if a.action == "import":
        if not a.query:
            return out({"ok": False, "why": "shape", "detail": "agentj contacts import <file>"})
        try:
            data = sys.stdin.buffer.read(IMPORT_MAX + 1) if a.query == "-" else pathlib.Path(a.query).expanduser().read_bytes()
        except OSError as e:
            return out({"ok": False, "why": "read", "detail": type(e).__name__})
        return out(run({"op": "import", "data": base64.b64encode(data).decode(), "name": os.path.basename(a.query),
                        "format": a.format or "auto", "dry_run": a.dry_run, "source": "import"}, 120))
    if a.action == "export":
        fmt = a.format if a.format in ("vcf", "csv", "json") else (
            "csv" if (a.query or "").lower().endswith(".csv") else "json" if (a.query or "").lower().endswith(".json") else "vcf")
        res = run({"op": "export", "format": fmt, "version": a.vcard_version or "3.0"}, 60)
        if not res.get("ok"):
            return out(res)
        if not a.query or a.query == "-":
            sys.stdout.write(res["text"])
            return 0
        p = pathlib.Path(a.query).expanduser()
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(res["text"])
        return out({"ok": True, "format": fmt, "path": str(p.resolve()), "bytes": p.stat().st_size})
    return out({"ok": False, "why": "shape"})


def add_parser(sub) -> None:
    import sys
    p = sub.add_parser("contacts", help="通讯录：list · find · add · edit · rm · import · export · card / the owner's address book",
                       description="主人的通讯录，只存在这台电脑上。find 按姓名、昵称、拼音（含谐音、声母）、邮箱模糊查找；"
                                   "import 吃 Google / Outlook / Apple / QQ / 163 导出的 .vcf .csv .json；card 在手机上弹联系人卡片让主人确认保存。"
                                   " / The owner's address book, on this computer only: fuzzy find by name, nickname, pinyin, e-mail; "
                                   "import vCard / CSV / JSON exports; `card` asks the owner on the phone.")
    p.add_argument("action", choices=["list", "find", "add", "edit", "rm", "import", "export", "card"])
    p.add_argument("query", nargs="?", help="find：要找的人 · edit/rm/card：id 或名字 · import/export：文件（- = 标准输入/输出）")
    p.add_argument("--name")
    p.add_argument("--nick", action="append", help="昵称 / 别名（可多次）")
    p.add_argument("--email", action="append", help="邮箱（可多次）")
    p.add_argument("--phone", action="append", help="电话（可多次）")
    p.add_argument("--address", action="append", help="地址（可多次）")
    p.add_argument("--org")
    p.add_argument("--title")
    p.add_argument("--note")
    p.add_argument("--rm-email", action="append")
    p.add_argument("--rm-phone", action="append")
    p.add_argument("--rm-nick", action="append")
    p.add_argument("--source", choices=SOURCES, help="add：来源（默认 agent）")
    p.add_argument("--why", help="card：一句话说明为什么要存 / why (shown on the card)")
    p.add_argument("--sort", choices=SORTS, default="recent", help="list：recent 最近发送 · frequent 最常用 · name 按名字字母")
    p.add_argument("--limit", type=int)
    p.add_argument("--format", choices=["auto", "vcf", "csv", "json"])
    p.add_argument("--vcard-version", choices=["3.0", "4.0"])
    p.add_argument("--dry-run", action="store_true", help="import：只数不写 / count only")
    p.add_argument("--en", action="store_true", help="English messages")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: sys.exit(cmd(a)))
