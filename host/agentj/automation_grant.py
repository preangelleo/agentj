"""0304-frozen A3: automation outbound-send grant (chairman 2026-11-09 19:30).

One owner fingerprint card issues a grant bound to ONE automation — a webhook hook (id + digest of its auth method,
source-IP allow list and target/handling config) or a scheduled task (id + tsha) — that lets turns of that automation
mail the listed recipients / domains without a card. Pattern = workflow_mail (core verbatim on the card, Ed25519 +
stored passkey assertion re-verified on every use, per-target revoke epoch). Out-of-scope → refused with the exact
command that extends it. Store: <state>/automation-grants.json (0600). See reports/qa/0304-frozen/THREAT-MODEL.md §A3.

Identity is host-held only: a hook turn = the current Send's `hook_notice` (set by hook_service._to_agent) AND a caller
process born inside this non-owner turn under the live Agent session; a task = workflow_mail.source (the peer descends
from the pinned task root of the live run). Turn text never names the automation.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shlex
import threading
import time
import unicodedata

from . import approvals, contacts, passkey, provenance, wire
from .workflow_mail import canonical, digest

TOOL = 'agentj automation grant'
FILE = 'automation-grants.json'
ACTIONS = ('mail', 'mail+files')        # F8: attachments are a separate action, off unless the card names it
KINDS = ('hook', 'task')
CARD_TTL, WAIT = 1800, 600
MAX_TO, MAX_DOMAINS, MAX_USES, MAX_DAYS = 50, 20, 100_000, 365
STATES = ('live', 'expired', 'stale', 'revoked', 'used_up')
ID = re.compile(r'[0-9a-f]{32}')
REF = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')           # task ids (workflow_mail.ID)
HOOK_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')                    # hooks.new_id = token_urlsafe: may start with _ or -
DOMAIN = re.compile(r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
_LOCK = threading.Lock()


# ------------------------------------------------------------------ normalization + scope
# security impl r1 M2: Python's codec is IDNA 2003 (nameprep). It folds where UTS-46 non-transitional (what browsers and
# mail servers resolve) does not: the four deviation characters (ß → ss, ς → σ, ZWJ / ZWNJ removed) and the NFKC
# compatibility mappings (full-width / ligature / superscript forms) plus the code points nameprep maps to nothing. Such an
# input would be granted and shown as a DIFFERENT ASCII domain than the one the owner typed, so it is refused: type the
# exact punycode (xn--…). A refusal only ever means "type it exactly".
_IDNA_DEVIATION = frozenset('\u00df\u03c2\u200d\u200c')
_IDNA_NOTHING = frozenset('\u00ad\u034f\u1806\u180b\u180c\u180d\u200b\u2060\ufeff'
                          + ''.join(chr(c) for c in range(0xfe00, 0xfe10)))


def idna_refusal(value) -> str | None:
    """None = IDNA 2003 and UTS-46 non-transitional agree on `value` (a domain or an address; ASCII always agrees).
    'idna_deviation' (ß ς ZWJ ZWNJ, also after lower-casing: 'Σ' at a label end lowers to 'ς') | 'nfkc_folding'
    (NFKC(x) != NFC(x), or a code point nameprep silently drops)."""
    if not isinstance(value, str) or value.isascii():
        return None
    nfc = unicodedata.normalize('NFC', value)
    if any(c in _IDNA_DEVIATION for c in nfc + nfc.lower()):
        return 'idna_deviation'
    if unicodedata.normalize('NFKC', nfc) != nfc or any(c in _IDNA_NOTHING for c in nfc):
        return 'nfkc_folding'
    return None


def norm_domain(value) -> str:
    """'@Example.COM.' → 'example.com'; NFC, then IDNA (non-ASCII → punycode 'xn--…'); at least two labels ('com' alone
    is never a grant); '' when invalid or when IDNA 2003 would fold it differently from UTS-46 (idna_refusal).
    Shape only: domain_refusal decides whether it may be a DOMAIN grant."""
    if not isinstance(value, str) or idna_refusal(value):
        return ''
    d = unicodedata.normalize('NFC', value).strip().lower().lstrip('@').strip('.')
    try:
        d = d.encode('idna').decode('ascii') if d and not d.isascii() else d
    except UnicodeError:
        return ''
    return d if DOMAIN.fullmatch(d) else ''


def norm_addr(value) -> str:
    """One normalization for BOTH sides (grant and recipient): NFC, an IDN domain → punycode, then contacts.norm_email
    (lower case, ASCII-only shape). Exact string equality after this; '' when invalid."""
    if not isinstance(value, str) or idna_refusal(value):
        return ''
    a = unicodedata.normalize('NFC', value).strip()
    local, at, dom = a.rpartition('@')
    if at and dom and not dom.isascii():
        dom = norm_domain(dom)
        if not dom:
            return ''
        a = local + '@' + dom
    return contacts.norm_email(a)


# security r1 F2: a domain grant on a public suffix or a consumer mailbox domain = "send to anyone". Closed, offline subset
# of the Public Suffix List (ICANN second levels of the common ccTLDs, Japanese prefectures, Chinese provinces, US states,
# the registry-style and hosting private suffixes anyone can get a name under) + a closed consumer-mailbox list (the mail
# PRESETS domains included). Those are only ever exact addresses. Fail-closed: a false hit only means "type the address".
_CC_SLD = frozenset('com net org edu gov ac co mil gob nic or ne go gen biz info ltd plc sch nom mobi int idv gouv web '
                    'firm ind res nhs police govt ad ed gr lg'.split())
_JP_PREF = frozenset('aichi akita aomori chiba ehime fukui fukuoka fukushima gifu gunma hiroshima hokkaido hyogo ibaraki '
                     'ishikawa iwate kagawa kagoshima kanagawa kochi kumamoto kyoto mie miyagi miyazaki nagano nagasaki '
                     'nara niigata oita okayama okinawa osaka saga saitama shiga shimane shizuoka tochigi tokushima tokyo '
                     'tottori toyama wakayama yamagata yamaguchi yamanashi'.split())
_CN_PROV = frozenset('ah bj cq fj gd gs gx gz ha hb he hi hk hl hn jl js jx ln mo nm nx qh sc sd sh sn sx tj tw xj xz yn zj'
                     .split())
_US_ST = frozenset('ak al ar as az ca co ct dc de fl ga gu hi ia id il in ks ky la ma md me mi mn mo ms mt nc nd ne nh nj nm '
                   'nv ny oh ok or pa pr ri sc sd tn tx ut va vi vt wa wi wv wy'.split())
_SUFFIXES = frozenset((
    # ICANN multi-label suffixes outside the generic <sld>.<cc> rule
    'me.uk', 'ltd.uk', 'plc.uk', 'net.uk', 'sch.uk', 'nhs.uk', 'police.uk', 'ac.jp', 'ad.jp', 'co.jp', 'ed.jp', 'go.jp',
    'gr.jp', 'lg.jp', 'ne.jp', 'or.jp', 'co.kr', 'or.kr', 'ne.kr', 're.kr', 'pe.kr', 'go.kr', 'ac.kr', 'hs.kr', 'blog.br',
    'eco.br', 'art.br', 'tv.br', 'k12.tr', 'av.tr', 'bel.tr', 'pol.tr', 'tsk.tr', 'asn.au', 'id.au', 'csiro.au', 'act.au',
    'nsw.au', 'qld.au', 'vic.au', 'wa.au', 'sa.au', 'tas.au', 'nt.au', 'geek.nz', 'kiwi.nz', 'maori.nz', 'school.nz',
    'cri.nz', 'iwi.nz', 'parliament.nz', 'qc.ca', 'on.ca', 'bc.ca', 'ab.ca', 'mb.ca', 'nb.ca', 'nl.ca', 'ns.ca',
    'pe.ca', 'sk.ca', 'yk.ca', 'nt.ca', 'nu.ca', 'gc.ca', 'in.th', 'mi.th', 'or.th', 'co.th', 'ac.th', 'go.th', 'co.id',
    'or.id', 'web.id', 'my.id', 'biz.id', 'sch.id', 'ac.id', 'go.id', 'co.in', 'firm.in', 'gen.in', 'ind.in', 'net.in',
    'org.in', 'co.za', 'org.za', 'web.za', 'gov.za', 'ac.za', 'nom.za', 'co.il', 'org.il', 'net.il', 'ac.il', 'gov.il',
    'muni.il', 'co.ke', 'or.ke', 'ne.ke', 'go.ke', 'ac.ke', 'sc.ke', 'co.tz', 'or.tz', 'ne.tz', 'go.tz', 'ac.tz',
    'co.ug', 'or.ug', 'ne.ug', 'go.ug', 'ac.ug', 'co.zw', 'co.mz', 'co.bw', 'co.ls', 'co.na', 'co.zm', 'com.de',
    'priv.at', 'gv.at', 'co.at', 'or.at', 'ac.at', 'co.hu', 'org.hu', 'priv.no', 'co.no', 'gs.no', 'e164.arpa',
    # registry-style private suffixes (anyone can register <name>.<suffix>)
    'eu.org', 'uk.com', 'us.com', 'cn.com', 'gb.net', 'uk.net', 'us.org', 'za.com', 'jpn.com', 'de.com', 'eu.com',
    'br.com', 'ru.com', 'sa.com', 'se.net', 'hu.net', 'qc.com', 'kr.com', 'ae.org', 'it.com', 'in.net', 'africa.com',
    'co.com', 'mex.com', 'gr.com', 'co.nl', 'co.ca', 'co.place',
    # hosting / dynamic-DNS private suffixes
    'github.io', 'gitlab.io', 'herokuapp.com', 'vercel.app', 'netlify.app', 'pages.dev', 'workers.dev', 'web.app',
    'firebaseapp.com', 'appspot.com', 'blogspot.com', 'azurewebsites.net', 'cloudapp.net', 'cloudfront.net',
    'amazonaws.com', 'elasticbeanstalk.com', 'onrender.com', 'fly.dev', 'glitch.me', 'repl.co', 'ngrok.io', 'ngrok.app',
    'ngrok-free.app', 'trycloudflare.com', 'duckdns.org', 'no-ip.org', 'ddns.net', 'dyndns.org', 'now.sh', 'surge.sh',
    'wixsite.com', 'weebly.com', 'wordpress.com', 'tumblr.com', 'myshopify.com', 'notion.site', 'deno.dev',
    'railway.app', 'up.railway.app', 'azurestaticapps.net', 'cloudflareaccess.com', 'r2.dev', 'supabase.co',
    # security impl r1 L6: Russian regional private suffixes (PSL)
    'spb.ru', 'msk.ru',
))
_FREEMAIL = frozenset((
    'gmail.com', 'googlemail.com', 'qq.com', 'foxmail.com', 'vip.qq.com', '163.com', '126.com', 'yeah.net', '188.com',
    'vip.163.com', 'vip.126.com', 'sina.com', 'sina.cn', 'vip.sina.com', 'sohu.com', 'tom.com', '21cn.com', '139.com',
    '189.cn', 'wo.cn', 'aliyun.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com', 'passport.com', 'icloud.com',
    'me.com', 'mac.com', 'yahoo.com', 'yahoo.co.jp', 'ymail.com', 'rocketmail.com', 'aol.com', 'aim.com', 'proton.me',
    'protonmail.com', 'protonmail.ch', 'pm.me', 'tutanota.com', 'tutanota.de', 'tuta.io', 'tuta.com', 'mail.com',
    'email.com', 'gmx.net', 'web.de', 'freenet.de', 't-online.de', 'orange.fr', 'free.fr', 'laposte.net', 'sfr.fr',
    'libero.it', 'virgilio.it', 'mail.ru', 'bk.ru', 'inbox.ru', 'list.ru', 'internet.ru', 'rambler.ru', 'ya.ru',
    'naver.com', 'daum.net', 'hanmail.net', 'kakao.com', 'nate.com', 'zoho.com', 'zohomail.com', 'fastmail.com',
    'fastmail.fm', 'hey.com', 'rediffmail.com', 'comcast.net', 'verizon.net', 'att.net', 'sbcglobal.net', 'cox.net',
    'btinternet.com', 'sky.com', 'seznam.cz', 'wp.pl', 'o2.pl', 'onet.pl', 'interia.pl', 'ukr.net', 'hinet.net',
    'netvigator.com', 'docomo.ne.jp', 'ezweb.ne.jp', 'softbank.ne.jp', 'i.softbank.jp', 'nifty.com', 'excite.co.jp',
    # security impl r1 L6: more consumer mailboxes and the forwarding / alias relays (anyone can get an address there)
    'duck.com', 'tutamail.com', 'tuta.io', 'tutanota.com', 'mailfence.com', 'posteo.de', 'posteo.net', 'mailbox.org',
    'zoho.in', 'zoho.eu', 'email.cn', '163.net', 'sohu.net', 'riseup.net', 'disroot.org', 'gmx.com', 'gmx.de',
    'addy.io', 'anonaddy.me', 'anonaddy.com', 'simplelogin.com', 'simplelogin.co', 'slmail.me', 'aleeas.com',
    'mozmail.com', 'privaterelay.appleid.com', 'hey.com', 'pm.me', 'fastmail.com', 'fastmail.fm', 'web.de',
    't-online.de', 'laposte.net', 'orange.fr', 'libero.it', 'rambler.ru', 'seznam.cz', 'naver.com', 'daum.net',
    'hanmail.net',
))
# "<base>.<any public suffix>": gmx.de, yandex.ru, yahoo.co.uk, hotmail.fr, outlook.jp, live.cn, …
_FREEMAIL_BASES = frozenset('gmx yandex yahoo hotmail outlook live msn windowslive gmail'.split())


def _preset_domains() -> frozenset:
    with contextlib.suppress(Exception):
        from .mail import PRESETS        # read-only: the mail presets' consumer domains (qq, 163, gmail, icloud, …)
        return frozenset(d for row in PRESETS.values() for d in row[8])
    return frozenset()


def public_suffix(d: str) -> bool:
    """d (normalized) is itself a public suffix in the shipped subset: anyone can hold a name directly under it."""
    labels = d.split('.')
    n, tld = len(labels), labels[-1]
    if n == 1 or d in _SUFFIXES:
        return True
    if n == 2 and len(tld) == 2 and tld.isalpha() and labels[0] in _CC_SLD:
        return True                                       # com.cn, co.uk, org.au, gov.br, ac.jp, …
    if tld == 'jp' and labels[-2] in _JP_PREF and n <= 3:
        return True                                       # tokyo.jp, <city>.osaka.jp
    if tld == 'cn' and n == 2 and labels[0] in _CN_PROV:
        return True                                       # bj.cn, sh.cn
    if tld == 'us' and labels[-2] in _US_ST and n <= 3:
        return True                                       # ny.us, k12.ny.us
    return n == 3 and '.'.join(labels[1:]) in ('sch.uk', 'kawasaki.jp', 'kitakyushu.jp', 'kobe.jp', 'nagoya.jp',
                                               'sapporo.jp', 'sendai.jp', 'yokohama.jp')   # PSL wildcard rows


def freemail(d: str) -> bool:
    """d is (or is under) a consumer mailbox domain: anyone can open an address there."""
    labels = d.split('.')
    if labels[0] in _FREEMAIL_BASES and len(labels) >= 2 and public_suffix('.'.join(labels[1:])):
        return True
    free = _FREEMAIL | _preset_domains()
    return any('.'.join(labels[i:]) in free for i in range(len(labels) - 1))


def domain_refusal(d: str) -> str | None:
    """None = d may be a DOMAIN grant. 'public_suffix' | 'freemail' = only exact addresses there. A listed mailbox wins
    the label (web.de also matches the generic <sld>.<cc> suffix rule)."""
    if freemail(d):
        return 'freemail'
    if public_suffix(d):
        return 'public_suffix'
    return None


def _unicode_domain(d: str) -> str:
    try:
        return d.encode('ascii').decode('idna')
    except UnicodeError:
        return d


def _script(ch: str) -> str:
    try:
        name = unicodedata.name(ch).split()[0]
    except ValueError:
        return 'UNKNOWN'
    return 'CJK' if name in ('CJK', 'HIRAGANA', 'KATAKANA', 'HANGUL', 'BOPOMOFO') else name


def mixed_script(d: str) -> bool:
    """A label mixing writing systems (Cyrillic а in 'аpple') — the lookalike pattern. Latin + CJK is the ordinary
    mix (UTS #39 highly-restrictive), not flagged."""
    for label in _unicode_domain(d).split('.'):
        s = {_script(c) for c in label if c.isalpha()}
        if 'CJK' in s:
            s.discard('LATIN')
        if len(s) > 1:
            return True
    return False


def addr_domain(addr: str) -> str:
    return addr.rpartition('@')[2]


def covers(core: dict, addr: str) -> bool:
    """Exact normalized address (norm_addr on both sides), or a domain that is the address's whole domain or a parent of
    it, label by label (after '@'). 'evil-example.com' never matches 'example.com'; '+tag' is not folded; display names
    never reach here."""
    a = norm_addr(addr)
    if not a:
        return False
    if a in core.get('to', []):
        return True
    dom = addr_domain(a)
    return any(dom == d or dom.endswith('.' + d) for d in core.get('domains', []))


def addr_hash(addrs) -> str:
    return hashlib.sha256('\n'.join(sorted(addrs)).encode()).hexdigest()[:16]


def target_key(kind: str, tid: str) -> str:
    return kind + ':' + tid


# ------------------------------------------------------------------ the card (core verbatim)
def local_zone(ts: int) -> list:
    """[zone name, UTC offset seconds] of this host at instant `ts` — frozen into the core at issuance, so the card
    text (which proof_ok rebuilds on every use) never changes when the host's time zone setting does."""
    from datetime import datetime
    d = datetime.fromtimestamp(ts).astimezone()
    name = (d.tzname() or '').strip()
    name = name if name.isascii() and name.isprintable() and 0 < len(name) <= 16 else ''
    return [name, int(d.utcoffset().total_seconds())]


def when(ts: int, zone) -> str:
    """'2026-12-10 09:00 JST (UTC+09:00)' in the zone frozen at issuance; ISO UTC for a core without one."""
    from datetime import datetime, timedelta, timezone
    if not (isinstance(zone, list) and len(zone) == 2):
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()
    off = zone[1]
    d = datetime.fromtimestamp(ts, timezone(timedelta(seconds=off)))
    sign, a = ('+' if off >= 0 else '-'), abs(off)
    utc = f'UTC{sign}{a // 3600:02d}:{a % 3600 // 60:02d}'
    return f"{d:%Y-%m-%d %H:%M} {zone[0]} ({utc})" if zone[0] else f"{d:%Y-%m-%d %H:%M} ({utc})"


def zone_ok(zone) -> bool:
    return (isinstance(zone, list) and len(zone) == 2 and isinstance(zone[0], str) and len(zone[0]) <= 16
            and zone[0].isascii() and (zone[0].isprintable()) and type(zone[1]) is int and abs(zone[1]) <= 14 * 3600)


# Security r2 N1: a hook's name is the Agent's to choose (it can rename the hook before asking). Wherever the card shows
# it — the signed text, the front, refusals — it is reduced to plain words: no quote / bracket that could close the
# 「…」 around it and make the rest read as the card's own wording, no line breaks or invisible characters, ≤ 40 chars.
# Security r3 R3-1: by Unicode category, not a hand list — every opening / closing / initial / final quote or bracket
# (Ps Pe Pi Pf), combining marks (Mn Me), control / format / unassigned (C*) and separators (Z*), judged on the NFKC
# form of each character so half-width ｢｣, ﹁﹂﹃﹄ and the like fold to what they imitate. The quote-like characters
# outside those categories (ASCII ' " ` < >, primes ′″, ornament quotes ❛❜❝❞, ditto 〃) are listed explicitly.
_NAME_CATS = frozenset(('Ps', 'Pe', 'Pi', 'Pf', 'Mn', 'Me'))
_NAME_EXTRA = frozenset('\'"`<>\u2032\u2033\u2034\u2035\u2036\u2037\u2057\u275b\u275c\u275d\u275e\u275f\u2760\u3003'
                        '\U0001f676\U0001f677\U0001f678')


def _name_mark(c: str) -> bool:
    for x in {c, *unicodedata.normalize('NFKC', c)}:
        cat = unicodedata.category(x)
        if x in _NAME_EXTRA or cat in _NAME_CATS or cat[0] in 'CZ':
            return True
    return False


def label(auto: dict) -> str:
    """The display-safe name of an automation target (core or identity dict with 'name' and 'id' / 'target')."""
    tid = (auto.get('target') or {}).get('id') if 'target' in auto else auto.get('id')
    raw = str(auto.get('name') or tid or '')
    text = ' '.join(''.join(' ' if _name_mark(c) else c for c in raw).split())
    if len(text) > 40:
        text = text[:39] + '…'
    return text or str(tid or '')


def shown(core: dict) -> str:
    en = core.get('lang') == 'en'
    t = core['target']
    what = ((f"Webhook 「{label(core)}」 ({t['id']})" if en else f"Webhook「{label(core)}」（{t['id']}）")
            if t['kind'] == 'hook' else (f"Scheduled task {t['id']}" if en else f"定时任务 {t['id']}"))
    until = (when(core['expires'], core.get('tz')) if core.get('expires')
             else ('no expiry (until you revoke it)' if en else '长期有效（直到你撤销）'))
    uses = str(core['max_uses']) if core.get('max_uses') else ('no limit' if en else '不限')
    none = 'none' if en else '无'
    files = 'mail+files' in core['actions']

    def dom(d):     # F2: the punycode form is what matches; the Unicode form and a mixed-script warning beside it
        u = _unicode_domain(d)
        out = d if u == d else (f'{d} (displays as {u})' if en else f'{d}（显示为 {u}）')
        if mixed_script(d):
            out += (' [warning: mixes writing systems, may imitate another domain]' if en
                    else '【注意：混用多种文字，可能在仿冒别的域名】')
        return out
    if en:
        lines = ['Authorize an automation to send to third parties', f'Automation: {what}',
                 f"Automation fingerprint: {t['digest']}",
                 'Action: send e-mail' + (' with attachments' if files else ' (no attachments)'),
                 account_line(core),
                 'Allowed recipients: ' + (', '.join(core['to']) or none),
                 'Allowed domains (and their subdomains): ' + (', '.join(dom(d) for d in core['domains']) or none),
                 f'Expires: {until}', f'Use limit: {uses}',
                 ('Anyone who can trigger this hook can make the Agent mail these recipients; you do not control what '
                  'the mail says.' if t['kind'] == 'hook' else
                  'Every run of this task can make the Agent mail these recipients; you do not check each mail.'),
                 'Changing this automation (webhook auth method, source IPs, delivery target, note, or the task '
                 'definition), the sending account, or Stop everything voids this grant; it must be approved again.',
                 'Any other recipient is still refused.',
                 'Revoke: Settings → Safety → Automation send grants, or agentj automation revoke ' + core['id']]
    else:
        lines = ['授权自动化对外发信', f'自动化：{what}', f"自动化指纹：{t['digest']}",
                 '动作：发邮件' + ('（可带附件）' if files else '（不带附件）'),
                 account_line(core),
                 '允许的收件人：' + ('、'.join(core['to']) or none),
                 '允许的域名（含子域名）：' + ('、'.join(dom(d) for d in core['domains']) or none),
                 f'到期时间：{until}', f'次数上限：{uses}',
                 ('能触发这个 hook 的人都能让 Agent 给这些收件人发信，信的内容不受你控制。' if t['kind'] == 'hook' else
                  '这个定时任务每次运行都能让 Agent 给这些收件人发信，你不会逐封确认。'),
                 '这个自动化一改（Webhook 验签方式、来源 IP、投递目标、说明，或任务定义）、发信账号一换，或按了「全部停下」，'
                 '本授权即失效，需要重新批准。',
                 '范围外的收件人照样拦下。',
                 '撤销：设置 → 安全 → 自动化外发授权，或 agentj automation revoke ' + core['id']]
    # QA scene 3: zh labels. The id and the digest stay on the signed text (proof_ok rebuilds exactly this).
    tail = (['Grant: ' + core['id'], 'SHA256: ' + digest(core)] if en
            else ['授权编号：' + core['id'], '校验摘要 SHA256：' + digest(core)])
    return '\n'.join(lines + tail)


def account_line(core: dict) -> str:
    acct = core['account']
    return (f"Sending account: {acct['address']} via {acct['smtp']}" if core.get('lang') == 'en'
            else f"发信账号：{acct['address']}（{acct['smtp']}）")


def _masked(signed: str, raw: str) -> bool:
    """`signed` is `raw` with one or more non-empty spans replaced by privacy.R (the card display projection masks a
    value it finds in a local .env — e.g. the owner's own mailbox / SMTP host). R works as a non-empty wildcard."""
    from .privacy import R
    parts = signed.split(R)
    if len(parts) < 2 or not raw.startswith(parts[0]) or not raw.endswith(parts[-1]):
        return False
    pos, end = len(parts[0]), len(raw) - len(parts[-1])
    for p in parts[1:-1]:
        i = raw.find(p, pos + 1)
        if i < 0 or i + len(p) > end:
            return False
        pos = i + len(p)
    return end > pos


def signed_text_ok(core: dict, summary) -> bool:
    """0.30.6 preview stale: the phone shows (and signs) the host's display projection of shown(core). That projection
    may mask the sending-account line only — its binding is enforced by core['account'] (account_of on issue and on
    every use) and by the SHA256 line, which commits to the account. Every other line (target, fingerprint,
    recipients, domains, expiry, limit, grant id, SHA256) must match byte for byte."""
    raw = shown(core)
    if not isinstance(summary, str):
        return False
    if summary == raw:
        return True
    rl, sl = raw.split('\n'), summary.split('\n')
    acct = account_line(core)
    if len(rl) != len(sl) or rl.count(acct) != 1:
        return False
    i = rl.index(acct)
    return all(a == b for j, (a, b) in enumerate(zip(rl, sl)) if j != i) and _masked(sl[i], rl[i])


def _short_list(items, en: bool, n: int = 3) -> str:
    items = list(items)
    head = (', ' if en else '、').join(items[:n])
    if len(items) > n:
        head += (f' and {len(items) - n} more' if en else f' 等 {len(items)} 个')
    return head


def front(core: dict) -> dict:
    """QA scene 3: what the card's FRONT says (approval envelope `title` / `reason`, zh + en; approval-layout0281.js
    stepTitle / stepReason show them). One line: to whom, how long, then which automation (security r2 N1: recipients
    lead; the Agent-chosen hook name comes after 「来源」, through label()), who can trigger it, and a short fingerprint. The full signed text stays in `details` (shown(core)). Host-built from the core only."""
    t = core['target']
    name = label(core)
    fp = str(t.get('digest') or '')[:12]
    files = 'mail+files' in core.get('actions', [])
    out = {}
    for lang in ('zh', 'en'):
        en = lang == 'en'
        whom = _short_list(list(core.get('to') or []) + ['@' + d for d in core.get('domains') or []], en)
        life = (('until ' if en else '到 ') + when(core['expires'], core.get('tz'))) if core.get('expires') else (
            'no expiry' if en else '长期有效')
        if core.get('max_uses'):
            life += (f', at most {core["max_uses"]} sends' if en else f'，最多 {core["max_uses"]} 次')
        if en:
            who = (f'webhook “{name}”' if t['kind'] == 'hook' else f'scheduled task {t["id"]}')
            risk = ('anyone who can trigger this hook can make the Agent send' if t['kind'] == 'hook'
                    else 'every run can send; you will not confirm each mail')
            reason = (f'Mail {whom} automatically{" with attachments" if files else ""} ({life}) · from {who} · {risk}'
                      f' · fingerprint {fp}')
            title = 'Allow automated mail'
        else:
            who = (f'hook「{name}」' if t['kind'] == 'hook' else f'定时任务 {t["id"]}')
            risk = '能触发这个 hook 的人都能让 Agent 发信' if t['kind'] == 'hook' else '每次运行都能发信，你不会逐封确认'
            reason = f'自动发邮件给 {whom}{"（可带附件）" if files else ""}（{life}）· 来源 {who} · {risk} · 指纹 {fp}'
            title = '授权自动化对外发信'
        out['title_en' if en else 'title'] = title
        out['reason_en' if en else 'reason'] = reason
    return out


def proof_ok(st, core: dict, proof: dict) -> bool:
    """workflow_mail.proof_ok for this tool: the signed text is shown(core) on this host; Ed25519 of the device still
    paired; the stored WebAuthn assertion verifies against its current passkey (no live-counter advance)."""
    try:
        if not signed_text_ok(core, proof['summary']) or proof['channel'] != core['host']:
            return False
        sk, pk = st.sign_key(proof['device']), st.passkey_of_device(proof['device'])
        if not sk or not pk or proof['kind'] not in ('sandbox', 'perm'):
            return False
        version = proof.get('approval_v')
        if version is None:
            d = approvals.shown_digest(TOOL, proof['summary'])
            if not approvals.verify(sk, wire.unb64u(proof['sig']), proof['channel'], proof['device'], proof['rid'], 'allow', d):
                return False
        elif type(version) is int and version == 2:
            from . import approval_review
            env = proof.get('envelope')
            if (not isinstance(env, dict) or env.get('v') != 2 or env.get('channel') != core['host']
                    or env.get('request_id') != proof['rid'] or env.get('action') != TOOL
                    or env.get('details') != approval_review.safe_text(proof['summary'], 24000)
                    or env.get('input_sha256') != approvals.input_digest({'summary': shown(core)})
                    or env.get('plan') is not None):
                return False
            f = front(core)         # security r2 N3: the signed front is exactly the host-built one for this core
            if any(env.get(k) != approval_review.safe_text(f[k], n) for k, n in
                   (('title', 160), ('title_en', 160), ('reason', 600), ('reason_en', 600))):
                return False
            d = approval_review.digest(env)
            if not approvals.verify(sk, wire.unb64u(proof['sig']), proof['channel'], proof['device'], proof['rid'], 'allow', d, approval_v=2):
                return False
        else:
            return False
        f = passkey.approval_fields(proof['fa'])
        if f['id'] != pk['id']:
            return False
        ch = passkey.elevate_challenge(proof['channel'], proof['device'], proof['rid'], proof['kind'], proof['rid'], d)
        passkey.verify_assertion({k: pk.get(k) for k in ('alg', 'spki', 'rp')}, f, ch)
        return True
    except (KeyError, TypeError, ValueError, passkey.PasskeyError):
        return False


def core_ok(core) -> bool:
    try:
        t = core['target']
        return (isinstance(core, dict) and ID.fullmatch(core['id']) is not None and t['kind'] in KINDS
                and isinstance(t['id'], str) and (HOOK_ID if t['kind'] == 'hook' else REF).fullmatch(t['id']) is not None
                and isinstance(t['digest'], str)
                and core['actions'] in (['mail'], ['mail', 'mail+files'])
                and isinstance(core['to'], list) and isinstance(core['domains'], list)
                and all(norm_addr(a) == a for a in core['to'])
                and all(norm_domain(d) == d and domain_refusal(d) is None for d in core['domains'])
                and (core['to'] or core['domains'])
                and (core['expires'] is None or type(core['expires']) is int)
                and (core['max_uses'] is None or type(core['max_uses']) is int and core['max_uses'] >= 1)
                and type(core['stop']) is int and isinstance(core['account'], dict)
                and ('tz' not in core or zone_ok(core['tz']))
                and set(core['account']) == {'address', 'smtp', 'user'}
                and all(isinstance(v, str) and v for v in core['account'].values()))
    except (KeyError, TypeError, AttributeError):
        return False


def account_of(cfg) -> dict | None:
    """F8: the SMTP account a grant is bound to (address + server shown on the card; the login name only as a hash)."""
    try:
        smtp = cfg.get('smtp') or {}
        addr = contacts.norm_email(cfg.get('address'))
        host = str(smtp.get('host') or '').strip().lower().rstrip('.')
        if not addr or not host:
            return None
        return {'address': addr, 'smtp': f"{host}:{int(smtp.get('port') or 0)}",
                'user': hashlib.sha256(str(cfg.get('username') or addr).encode()).hexdigest()[:16]}
    except (AttributeError, TypeError, ValueError):
        return None


def stop_mark(st) -> int | None:
    """F8: the Stop-everything record's time (0 = never stopped); None while it is on (or unreadable = on). Any stop or
    resume after issuance changes it, so the grant dies and the owner must sign again."""
    if not hasattr(st, 'estop_path'):
        return 0
    from . import controls
    e = controls.estop_state(st)
    if e.get('on'):
        return None
    return int(e.get('at') or 0)


# ------------------------------------------------------------------ the store
def _path(st):
    return st.root / FILE


def load(st) -> dict:
    try:
        d = json.loads(_path(st).read_bytes())
    except FileNotFoundError:
        return {'v': 1, 'grants': {}, 'epochs': {}}
    if not isinstance(d, dict) or d.get('v') != 1 or not isinstance(d.get('grants'), dict) or not isinstance(d.get('epochs'), dict):
        raise ValueError('ledger')
    return d


def save(st, d: dict) -> None:
    st.write_private(_path(st), canonical(d))     # 0600, atomic (state._write_private)


_SKIP = object()


def why(st, row, current_digest=_SKIP, data=None, account=_SKIP) -> str | None:
    """None = usable. Else stale (the automation changed, or is gone / deleted: current_digest None) | estop (Stop
    everything was used after issuance) | account (the SMTP account changed) | invalid (record or proof tampered, or the
    signing remote unpaired / its passkey replaced) | revoked | expired | used_up. _SKIP skips a comparison."""
    if not isinstance(row, dict):
        return 'invalid'
    core = row.get('core')
    if not core_ok(core):
        return 'invalid'
    if row.get('revoked'):
        return 'revoked'
    data = data if data is not None else load(st)
    if row.get('epoch') != data['epochs'].get(target_key(core['target']['kind'], core['target']['id']), 0):
        return 'revoked'
    if stop_mark(st) != core['stop']:
        return 'estop'
    if core['expires'] is not None and core['expires'] <= time.time():
        return 'expired'
    if core['max_uses'] is not None and int(row.get('uses') or 0) >= core['max_uses']:
        return 'used_up'
    if current_digest is not _SKIP and current_digest != core['target']['digest']:
        return 'stale'
    if account is not _SKIP and account != core['account']:
        return 'account'
    if not proof_ok(st, core, row.get('proof') or {}):
        return 'invalid'
    return None


def target_gone(st, kind: str, tid: str, why_: str = 'deleted') -> int:
    """F8: the hook / task was deleted or ended → every grant of it is revoked now (the stale check already refuses it;
    this makes the Settings list and the ledger say so). → number of grants revoked."""
    n = 0
    with _LOCK:
        try:
            data = load(st)
        except (ValueError, OSError):
            return 0
        for gid, row in data['grants'].items():
            t = ((row or {}).get('core') or {}).get('target') or {}
            if t.get('kind') == kind and t.get('id') == tid and not row.get('revoked'):
                row.update(revoked=True, revoked_at=int(time.time()), revoked_why=why_)
                n += 1
        if n:
            key = target_key(kind, tid)
            data['epochs'][key] = data['epochs'].get(key, 0) + 1
            save(st, data)
    if n:
        st.log('automation_grant_revoke', kind=kind, task=tid[:16], why=why_, count=n)
    return n


def task_gone(host, tid: str) -> bool:
    """security impl r1 L3: the scheduled task's folder no longer exists (tasks.find → TaskError 'unknown'), read from a
    listable workdir. A task that is present but invalid / mid-edit is NOT gone (it is only `stale` until fixed)."""
    try:
        from . import tasks
        wd = host.scheduler.workdir()
        if not wd or not os.path.isdir(wd):
            return False
        os.listdir(wd)
        tasks.find(wd, tid)
        return False
    except Exception as e:  # noqa: BLE001
        return type(e).__name__ == 'TaskError' and getattr(e, 'reason', None) == 'unknown'


def reap_task(host, kind: str, tid: str) -> int:
    """A deleted task's grants are revoked for good (epoch bumped): re-creating an identical task (same tsha) never
    revives them. Called before every check / use / list and from the scheduler's sweep. Blocking (disk)."""
    if kind != 'task' or not task_gone(host, tid):
        return 0
    return target_gone(host.st, 'task', tid, 'deleted')


def sweep_tasks(host, present) -> int:
    """Scheduler tick: every live task grant whose task id is no longer among `present` (the ids the scheduler just
    discovered) is revoked. Only when the workdir itself is listable — an unreadable workdir revokes nothing."""
    try:
        wd = host.scheduler.workdir()
        if not wd or not os.path.isdir(wd):
            return 0
        os.listdir(wd)
        if not _path(host.st).exists():
            return 0
        data = load(host.st)
    except (ValueError, OSError, AttributeError):
        return 0
    gone = {c['target']['id'] for row in data['grants'].values()
            if isinstance(row, dict) and not row.get('revoked') and isinstance(c := row.get('core'), dict)
            and (c.get('target') or {}).get('kind') == 'task' and c['target'].get('id') not in present}
    return sum(reap_task(host, 'task', tid) for tid in sorted(gone))


def live_for(data: dict, kind: str, tid: str):
    for gid, row in data['grants'].items():
        c = (row or {}).get('core') or {}
        if (c.get('target') or {}).get('kind') == kind and c['target'].get('id') == tid and not row.get('revoked'):
            return gid, row
    return None, None


# ------------------------------------------------------------------ host-held automation identity
def hook_digest(h: dict) -> str:
    from .hook_service import grant_digest
    return grant_digest(h)


def _hook(host, hid):
    """Read-only hook lookup, safe off the loop (Store.get may end an expired hook and save: never from a worker)."""
    store = getattr(getattr(host, 'hooks', None), 'store', None)
    doc = getattr(store, 'doc', None)
    h = (doc.get('hooks') or {}).get(hid) if isinstance(doc, dict) else (store.get(hid) if store is not None else None)
    if not isinstance(h, dict) or h.get('state') != 'active' or (h.get('expires') and h['expires'] <= time.time()):
        return None
    return h


def current_digest(host, kind: str, tid: str) -> str | None:
    """The automation's digest right now, or None when it is gone / not runnable (→ stale)."""
    try:
        if kind == 'hook':
            h = _hook(host, tid)
            return hook_digest(h) if h else None
        from . import tasks
        e = tasks.find(host.scheduler.workdir(), tid)
        return None if e['problems'] else e['tsha']
    except Exception:  # noqa: BLE001 — unknown → stale (never usable)
        return None


def hook_caller(host, peer) -> bool:
    """The peer is a process of THIS hook turn: it descends from the live Agent session process and every hop below it
    was born inside the currently open non-owner span (application_ingress.note_turn opens it for a hook Send). An
    older background job — the owner's or another turn's — cannot borrow a hook's grant."""
    from .application_ingress import SPAN_MARGIN
    from .core import process_evidence as pe
    agent = getattr(host, 'agent', None)
    if agent is None or type(peer) is not int or peer <= 1:
        return False
    spans = host.__dict__.get('_nonowner_spans') or []
    if not spans or spans[-1][1] is not None:
        return False
    # security impl r1 L2: the clock-granularity margin narrows the span (a process born up to SPAN_MARGIN after the
    # span opened is refused too) — never widens it toward an older job (application_ingress: "always toward refusing").
    floor = spans[-1][0] + SPAN_MARGIN
    session = getattr(agent, 'session', None)
    anchor = session.get('pid') if isinstance(session, dict) else getattr(getattr(agent, 'proc', None), 'pid', None)
    if type(anchor) is not int or anchor <= 1 or not pe.row(anchor):
        return False
    current, seen = peer, set()
    while current != anchor:
        if type(current) is not int or current <= 1 or current in seen or len(seen) >= 64:
            return False
        seen.add(current)
        hop = pe.row(current)
        if not hop or hop[1] < floor:
            return False
        current = hop[0]
    return bool(seen)


def identity(host, peer) -> dict | None:
    """{'kind','id','digest','name'} from host-held facts only, else None (not an automation turn)."""
    from . import workflow_mail
    from .background_done import chain_turn
    if provenance._paired_source() is not provenance._UNSET:
        return None                                  # a paired (phone) request is never an automation
    req = provenance._REQUEST.get()
    try:
        c = workflow_mail.source(host, peer)
    except Exception:  # noqa: BLE001
        c = None
    if c:
        # F3: the run lineage (peer under the live run's pinned root) is the identity; a request source stricter than
        # the task (a friend / same-owner / guest request around it) never borrows it.
        if req is not provenance._UNSET and req['kind'] != 'scheduled_task':
            return None
        return {'kind': 'task', 'id': c['workflow_id'], 'digest': c['tsha'], 'name': c['workflow_id']}
    agent = getattr(host, 'agent', None)
    send = getattr(agent, 'cur_send', None)
    hid = getattr(send, 'hook_notice', None)
    if (not isinstance(hid, str) or not hid or provenance.normalize(getattr(send, 'source', None))['kind'] != 'system'
            or provenance.task_scope() is not None
            or (req is not provenance._UNSET and req['kind'] != 'system')):          # F3: request not stricter than the turn
        return None
    # F3: same_owner / friend_task / wechat_task / official_assist / chain turns never use a grant
    try:
        if provenance.same_owner_source(agent) or provenance.delegated_turn(agent) or chain_turn(agent):
            return None
    except Exception:  # noqa: BLE001 — unknown provenance is not an automation
        return None
    h = _hook(host, hid)
    if not h or not hook_caller(host, peer):
        return None
    return {'kind': 'hook', 'id': hid, 'digest': hook_digest(h), 'name': h.get('name') or hid}


def _what(auto: dict, en: bool) -> str:
    if auto['kind'] == 'hook':
        return f"webhook 「{label(auto)}」" if en else f"Webhook「{label(auto)}」"
    return f"scheduled task {auto['id']}" if en else f"定时任务 {auto['id']}"


_SAFE_ARG = re.compile(r'[A-Za-z0-9._-]{1,128}')           # shell-inert: no quoting needed


def _extend_cmd(auto: dict, core, missing, files=False) -> str:
    # Security r3 R3-2: the hook's id, never its Agent-chosen name (spaces, ';', '$(…)' would make a pasteable command
    # run something else). `--hook=<id>`: an id may start with '-', which argparse would read as an option.
    tid = auto['id'] if _SAFE_ARG.fullmatch(auto['id']) else shlex.quote(auto['id'])
    ref = ('--hook=' + tid) if auto['kind'] == 'hook' else '--task ' + tid
    keep = (core or {}).get('to', []) if core else []
    doms = (core or {}).get('domains', []) if core else []
    parts = [f'--to {a}' for a in list(dict.fromkeys(keep + list(missing)))] + [f'--domain {d}' for d in doms]
    if files or 'mail+files' in ((core or {}).get('actions') or []):
        parts.append('--files')
    return f"agentj automation grant {ref} " + ' '.join(parts)


def refusal(code: str, auto: dict, lang: str, missing=(), core=None, reason=None, files=False) -> dict:
    en = lang == 'en'
    w = _what(auto, en)
    cmd = _extend_cmd(auto, core, missing, files)
    if code == 'automation_grant_required' and reason == 'mail+files':
        zh = (f"{w}的发信授权不含附件：带附件发信是另一项动作，默认关闭。没有发出，也没有发卡片；不是主人拒绝。"
              f"可以去掉附件再发；或请主人在自己的对话里重新授权并写明可带附件（一次指纹）：{cmd}")
        en_ = (f"The {w}'s send grant does not include attachments (a separate action, off by default). Nothing was sent "
               f"and no card was shown; the owner did not refuse. Send without the files, or ask the owner to approve it "
               f"again with attachments (one fingerprint): {cmd}")
    elif code == 'automation_grant_required':
        zh = (f"{w}没有覆盖这些收件人的对外发信授权：{'、'.join(missing)}。没有发出，也没有发卡片；不是主人拒绝。"
              f"请告诉主人：在自己的对话（手机网页 / 本机终端 / 已认证的主人渠道）里说「给这个自动化补授权发信给 …」，"
              f"主人会收到一张指纹卡；或主人在本机终端运行 {cmd}")
        en_ = (f"The {w} has no send grant covering: {', '.join(missing)}. Nothing was sent and no card was shown; the owner "
               f"did not refuse. Tell the owner: in their own conversation (Web App / local terminal / verified owner "
               f"channel) ask to extend this automation's send grant — one fingerprint card — or run {cmd}")
    elif code == 'automation_grant_stale' and reason == 'estop':
        zh = (f"主人用过「全部停下」，{w}之前的对外发信授权已随之作废，没有发出。恢复后要继续自动发信，"
              f"主人必须在自己的对话里重新授权（一次指纹）：{cmd}")
        en_ = (f"The owner used Stop everything, which voided the {w}'s send grant; nothing was sent. To send again "
               f"unattended the owner must approve it again in their own conversation (one fingerprint): {cmd}")
    elif code == 'automation_grant_stale' and reason == 'account':
        zh = (f"发信邮箱账号在授权之后换过，{w}的对外发信授权只绑定原账号，已失效，没有发出。"
              f"请主人在自己的对话里重新授权（一次指纹）：{cmd}")
        en_ = (f"The sending mail account changed after the {w}'s send grant, which is bound to the old account; nothing "
               f"was sent. Ask the owner to approve it again (one fingerprint): {cmd}")
    elif code == 'automation_grant_stale':
        zh = (f"{w}在授权之后被修改或删除过（验签方式、来源 IP、投递目标、说明或任务定义变了），原授权已失效，没有发出。"
              f"请主人在自己的对话里重新授权（一次指纹）：{cmd}")
        en_ = (f"The {w} changed or was deleted after its send grant (auth method, source IPs, target, note or task "
               f"definition); the grant is void and nothing was sent. Ask the owner to approve it again (one "
               f"fingerprint): {cmd}")
    else:   # automation_grant_invalid
        zh = (f"{w}的对外发信授权记录校验失败（文件被改动，或批准它的遥控器已移除 / 通行密钥已变），没有发出。"
              f"请主人在设置 → 安全 → 自动化外发授权里撤销后重新授权：{cmd}")
        en_ = (f"The {w}'s send grant failed verification (the record changed, or the approving remote was removed / its "
               f"passkey changed); nothing was sent. Ask the owner to revoke it in Settings → Safety → Automation send "
               f"grants and grant again: {cmd}")
    return {'ok': False, 'why': code, 'automation': {'kind': auto['kind'], 'id': auto['id']},
            **({'missing': list(missing)} if missing else {}), **({'reason': reason} if reason else {}),
            'detail': zh + ' / ' + en_}


_CODE = {'stale': 'automation_grant_stale', 'estop': 'automation_grant_stale', 'account': 'automation_grant_stale',
         'invalid': 'automation_grant_invalid'}


def _others(st, addrs) -> list[str]:
    own = {norm_addr(a) for a in contacts.owner_emails(st)}
    return [a for a in addrs if norm_addr(a) not in own]


def _verdict(core, w, others, action):
    """(code, reason, missing) for a non-usable state or a scope miss; None = covered."""
    if w in _CODE:
        return _CODE[w], w, list(others)
    if w is not None:
        return 'automation_grant_required', w, list(others)
    if action not in core['actions']:
        return 'automation_grant_required', action, []
    missing = [a for a in others if not covers(core, a)]
    return ('automation_grant_required', None, missing) if missing or not others else None


def check(host, auto: dict, addrs, action: str = 'mail', log: bool = True, account=_SKIP) -> dict:
    """Does a live grant for this automation cover every NON-owner recipient for `action` ('mail', or 'mail+files' when
    the mail has attachments) on this SMTP `account`? Owner addresses in a mixed list need no coverage (0304-small decides
    an all-owner list; callers do not reach here for it).
    → {'ok': True, 'grant': id} | refusal dict (automation_grant_required / _stale / _invalid)."""
    st, lang = host.st, getattr(host, 'lang', 'zh')
    others = _others(st, addrs)
    files = action == 'mail+files'
    reap_task(host, auto['kind'], auto['id'])      # L3: deleted → revoked before anything reads the ledger
    with _LOCK:
        try:
            data = load(st)
        except (ValueError, OSError):
            log and _deny(st, auto, 'automation_grant_invalid', others)
            return refusal('automation_grant_invalid', auto, lang, others, files=files)
        gid, row = live_for(data, auto['kind'], auto['id'])
        if row is None:
            log and _deny(st, auto, 'automation_grant_required', others)
            return refusal('automation_grant_required', auto, lang, others, files=files)
        w = why(st, row, auto['digest'], data, account)
    core = row.get('core') if isinstance(row, dict) else None
    v = _verdict(core, w, others, action)
    if v is None:
        return {'ok': True, 'grant': gid, 'core': core}
    code, reason, missing = v
    log and _deny(st, auto, code, missing, gid, state=reason)
    out = refusal(code, auto, lang, missing, core if code != 'automation_grant_invalid' else None, reason, files)
    if reason and code == 'automation_grant_required' and reason != action:
        out['grant_state'] = reason
    return out


def consume(host, auto: dict, gid: str, addrs, action: str = 'mail', account=_SKIP, rid: str | None = None) -> str | None:
    """F8: reserve one use FIRST, atomically, right before SMTP and after every await: re-verify (proof, expiry, count,
    revoke epoch, Stop everything, target digest, SMTP account, action) against the FINAL resolved to/cc/bcc. → None
    (counted) or the refusal code. A counted use is never refunded (an SMTP outcome may be uncertain)."""
    st = host.st
    others = _others(st, addrs)
    reap_task(host, auto['kind'], auto['id'])
    with _LOCK:
        try:
            data = load(st)
            row = data['grants'].get(gid)
            w = why(st, row, current_digest(host, auto['kind'], auto['id']), data, account)
            if w is None and row['core']['target']['digest'] != auto['digest']:
                w = 'stale'
            v = _verdict(row['core'] if w != 'invalid' else None, w, others, action)
            if v is not None:
                _deny(st, auto, v[0], others, gid, state=v[1])
                return v[0]
            row['uses'] = int(row.get('uses') or 0) + 1
            row['last_used'] = int(time.time())
            save(st, data)
        except (ValueError, OSError, KeyError, TypeError):
            return 'automation_grant_invalid'
    st.log('automation_grant_use', id=gid, request=rid, kind=auto['kind'], task=auto['id'][:16], to=len(others),
           sha=addr_hash(others), count=row['uses'], action=action)
    return None


def _deny(st, auto, code, addrs, gid=None, state=None):
    st.log('automation_grant_deny', id=gid, kind=auto['kind'], task=auto['id'][:16], why=code, reason=state,
           to=len(addrs), sha=addr_hash(addrs))


# ------------------------------------------------------------------ issuance (owner turn + card)
def owner_facts(host, peer) -> tuple[bool, bool]:
    """Blocking process facts (worker thread): (the peer is the owner's local terminal, the peer is a task process)."""
    from . import ctl_auth, workflow_mail
    terminal = task = False
    with contextlib.suppress(Exception):
        terminal = bool(type(peer) is int and ctl_auth.refusal(peer, os.getpid(), host._agent_pids()) is None
                        and ctl_auth.has_tty(peer))
    try:
        task = bool(workflow_mail.source(host, peer))
    except Exception:  # noqa: BLE001 — unknown → treated as a task process (refused)
        task = True
    return terminal, task


def owner_turn(host, peer, facts=None) -> bool:
    """Only the owner's own turn may ask for (or widen) a grant: the local owner terminal, or the paired phone / an owner
    channel turn (provenance.owner_speaking) whose caller is the live owner request frame. Never a webhook, task, friend,
    guest, group or unknown turn — so an injected automation can never request its own wider card.
    Runs on the event loop (application_ingress.allowed reads the current task); `facts` from owner_facts."""
    from . import application_ingress
    terminal, task = facts if facts is not None else owner_facts(host, peer)
    agent = getattr(host, 'agent', None)
    send = getattr(agent, 'cur_send', None)
    if task or getattr(send, 'hook_notice', None) or provenance.task_scope() is not None:
        return False
    if terminal:
        return True
    from .background_done import chain_turn
    try:
        if provenance.delegated_turn(agent) or provenance.same_owner_source(agent) or chain_turn(agent):
            return False                         # F13: same_owner / friend / wechat / assist / chain turns never issue
        return provenance.owner_speaking(agent) and application_ingress.allowed(host)
    except Exception:  # noqa: BLE001 — unknown provenance is not the owner's
        return False


def context_refusal(host) -> str | None:
    """F13: a grant is never issued inside a context that approves on the owner's behalf — a live goal grant, a live
    batch approval, a bounded-plan continuation / approval-explanation turn. (Computer-Use preauthorization cannot
    reach this card: preauth.GRANTABLE is the closed {'computer.control'} and the card is always_ask + passkey on the
    list path.) Event loop (reads host dicts; goal.json is one small read). → reason or None."""
    send = getattr(getattr(host, 'agent', None), 'cur_send', None)
    if getattr(send, 'bounded_plan_resume', None) is not None or getattr(send, 'approval_review', None):
        return 'bounded_plan'
    try:
        live = getattr(host, '_grant_live', None)
        if any(live(g) if callable(live) else True for g in (getattr(host, 'grants', None) or {}).values()):
            return 'batch'
        goals = getattr(host, 'goals', None)
        if goals is not None and goals.current():
            return 'goal'
    except Exception:  # noqa: BLE001 — unknown → refused (fail closed)
        return 'unknown'
    return None


def parse_expires(value) -> int | None:
    """'30d' / '12h' / '90m' / plain days → seconds; None = until revoked."""
    if value in (None, '', 0):
        return None
    m = re.fullmatch(r'(\d{1,6})([dhm]?)', str(value).strip().lower())
    if not m:
        raise ValueError('expires')
    n = int(m.group(1)) * {'d': 86400, '': 86400, 'h': 3600, 'm': 60}[m.group(2)]
    if not 60 <= n <= MAX_DAYS * 86400:
        raise ValueError('expires')
    return n


def _existing(st, kind, tid, tdig, to, domains, mx, secs, actions=('mail',), account=_SKIP):
    """(current revoke epoch of the target, id of an identical live grant or None). Worker thread."""
    with _LOCK:
        try:
            data = load(st)
        except (ValueError, OSError):
            data = {'v': 1, 'grants': {}, 'epochs': {}}
        old_id, old = live_for(data, kind, tid)
        epoch = data['epochs'].get(target_key(kind, tid), 0)
        if old is not None and why(st, old, tdig, data, account) is None:
            oc = old['core']
            if (oc['to'] == to and oc['domains'] == domains and oc['max_uses'] == mx and oc['actions'] == list(actions)
                    and (secs is None) == (oc['expires'] is None)):
                return epoch, old_id
        return epoch, None


def _store(st, core, proof, key, epoch):
    """Save the signed grant, replacing this target's older one; None when the target was revoked meanwhile. Worker."""
    t = core['target']
    with _LOCK:
        try:
            data = load(st)
        except (ValueError, OSError):
            data = {'v': 1, 'grants': {}, 'epochs': {}}    # an unreadable ledger verifies nothing; the new signed grant starts it
        if data['epochs'].get(key, 0) != epoch:
            return None
        replaced = [gid for gid, row in data['grants'].items()
                    if ((row or {}).get('core') or {}).get('target', {}).get('kind') == t['kind']
                    and row['core']['target'].get('id') == t['id']]
        for gid in replaced:
            data['grants'].pop(gid, None)
        data['grants'][core['id']] = {'core': core, 'proof': proof, 'uses': 0, 'epoch': epoch}
        save(st, data)
    return replaced


def _pending(host) -> dict:
    return host.__dict__.setdefault('_automation_grant_cards', {})


async def issue(host, req: dict, peer) -> dict:
    lang = getattr(host, 'lang', 'zh')
    st = host.st
    if not owner_turn(host, peer, await asyncio.to_thread(owner_facts, host, peer)):
        st.log('automation_grant_deny', why='automation_grant_owner_turn')
        return {'ok': False, 'why': 'automation_grant_owner_turn',
                'detail': ('只有主人自己的回合才能发起或扩大自动化外发授权（手机网页、本机终端，或已认证的主人渠道）；'
                           'Webhook、定时任务、访客、群聊、好友回合都不行。没有发卡片，也不是主人拒绝。'
                           '请告诉主人：在自己的对话里说要给这个自动化补授权 / Only the owner\'s own turn (Web App, local '
                           'terminal or a verified owner channel) can request or widen an automation send grant; a webhook, '
                           'task, visitor, group or friend turn cannot. No card was shown and the owner did not refuse. Tell '
                           'the owner to ask for it in their own conversation.')}
    ctx = await asyncio.to_thread(context_refusal, host)
    if ctx:
        st.log('automation_grant_deny', why='automation_grant_context', reason=ctx)
        return {'ok': False, 'why': 'automation_grant_context', 'reason': ctx,
                'detail': ('自动化外发授权不能在目标授权、批量批准或预先批准的计划执行中发起；没有发卡片，也不是主人拒绝。'
                           '请主人等这件事结束（或先结束目标）后，在自己的对话里再说一次 / An automation send grant cannot be '
                           'requested inside a goal grant, a batch approval or a pre-approved plan; no card was shown and the '
                           'owner did not refuse. Ask the owner again in their own conversation once that has ended.')}
    kind, ref = req.get('kind'), req.get('ref')
    to_raw, dom_raw = req.get('to') or [], req.get('domains') or []
    try:
        if kind not in KINDS or not isinstance(ref, str) or not ref or len(ref) > 128:
            raise ValueError('target')
        if (not isinstance(to_raw, list) or not isinstance(dom_raw, list) or len(to_raw) > MAX_TO
                or len(dom_raw) > MAX_DOMAINS or not (to_raw or dom_raw)):
            raise ValueError('scope')
        to = sorted({norm_addr(a) for a in to_raw})
        domains = sorted({norm_domain(d) for d in dom_raw})
        if '' in to or '' in domains:
            raise ValueError('address')
        if req.get('files') not in (None, False, True):
            raise ValueError('files')
        secs = parse_expires(req.get('expires'))
        mx = req.get('max_uses')
        if mx is not None and (type(mx) is not int or not 1 <= mx <= MAX_USES):
            raise ValueError('max_uses')
    except ValueError as e:
        bad = next((x for x in list(to_raw) + list(dom_raw) if idna_refusal(x)), None) if str(e) == 'address' else None
        if bad is not None:      # security impl r1 M2: never grant a folded domain the owner did not type
            r = idna_refusal(bad)
            st.log('automation_grant_deny', why='automation_grant_idn_ambiguous', reason=r)
            return {'ok': False, 'why': 'automation_grant_idn_ambiguous', 'reason': r,
                    'detail': ('这个域名含有会被不同规则换成别的域名的字符（如 ß、ς、零宽连接符或全角字母），不能直接授权；'
                               '请改写成它的精确 punycode 形式（xn--…）再授权 / This domain contains characters that different '
                               'IDNA rules turn into different domains (ß, ς, zero-width joiners, full-width letters); type '
                               'its exact punycode form (xn--…) instead.')}
        return {'ok': False, 'why': 'shape', 'detail': str(e)}
    for d in domains:       # F2: public suffixes and consumer mailboxes only as exact addresses
        r = domain_refusal(d)
        if r:
            st.log('automation_grant_deny', why='automation_grant_domain_refused', reason=r)
            return {'ok': False, 'why': 'automation_grant_domain_refused', 'domain': d, 'reason': r,
                    'detail': ((f'{d} 是公共后缀（任何人都能在它下面注册名字）' if r == 'public_suffix' else
                                f'{d} 是公共邮箱（任何人都能在那里开邮箱）') + '，不能按域名授权，只能写精确地址：--to 名字@' + d
                               + ' / ' + (f'{d} is a public suffix (anyone can register a name under it)' if r == 'public_suffix'
                                          else f'{d} is a public mailbox provider (anyone can open an address there)')
                               + '; it cannot be a domain grant, only exact addresses: --to name@' + d)}
    from . import mail as M
    account = account_of(await asyncio.to_thread(M.read_config, st))
    if account is None:
        return {'ok': False, 'why': 'mail_not_configured'}
    stop = await asyncio.to_thread(stop_mark, st)
    if stop is None:
        return {'ok': False, 'why': 'estop'}
    actions = ['mail', 'mail+files'] if req.get('files') is True else ['mail']
    if kind == 'hook':
        hooks = getattr(host, 'hooks', None)
        h = hooks.store.find(ref) if hooks is not None else None
        if not h or h.get('state') != 'active':
            return {'ok': False, 'why': 'no_target', 'detail': 'no active webhook with that name / id'}
        tid, name, tdig = h['id'], h.get('name') or h['id'], hook_digest(h)
    else:
        tid = ref if REF.fullmatch(ref) else None
        tdig = await asyncio.to_thread(current_digest, host, 'task', tid) if tid else None
        if not tdig:
            return {'ok': False, 'why': 'no_target', 'detail': 'no valid scheduled task with that id'}
        name = tid
    channel = st.config()['channel']
    now = int(time.time())
    core = {'id': secrets.token_hex(16), 'host': channel, 'target': {'kind': kind, 'id': tid, 'digest': tdig},
            'name': name, 'actions': actions, 'to': to, 'domains': domains, 'account': account, 'stop': stop,
            'expires': now + secs if secs else None, 'max_uses': mx, 'created': now, 'lang': 'en' if lang == 'en' else 'zh',
            **({'tz': local_zone(now + secs)} if secs else {})}
    key = target_key(kind, tid)
    epoch, same = await asyncio.to_thread(_existing, st, kind, tid, tdig, to, domains, mx, secs, actions, account)
    if same:
        return {'ok': True, 'unchanged': True, 'grant': same}
    p = _pending(host).get(key)
    if p is not None and not p.done():
        return {'ok': False, 'why': 'card_pending'}
    task = asyncio.ensure_future(_card(host, core, key, epoch))
    _pending(host)[key] = task
    done, _ = await asyncio.wait({task}, timeout=WAIT)
    return task.result() if task in done else {'ok': False, 'why': 'card_pending'}


async def _card(host, core: dict, key: str, epoch: int) -> dict:
    from . import policy_registry
    st = host.st
    text = shown(core)
    ans = await host.ask(TOOL, {'summary': text}, batch=False, grant_info=True, always_ask=True,
                         require_passkey=True, ttl=CARD_TTL, _company_proposal=policy_registry.HOST_CARD,
                         _front=front(core))
    proof = {k: v for k, v in (ans.get('grant_info') or {}).items() if k in
             ('rid', 'device', 'sig', 'fa', 'summary', 'kind', 'channel', 'approval_v', 'envelope')}
    t = core['target']
    if ans.get('behavior') != 'allow' or host.stopped():
        st.log('automation_grant', id=core['id'], kind=t['kind'], result='denied')
        return {'ok': False, 'why': 'denied', 'detail': ans.get('message') or 'not approved'}
    from . import mail as M
    if (await asyncio.to_thread(current_digest, host, t['kind'], t['id']) != t['digest']
            or await asyncio.to_thread(stop_mark, st) != core['stop']
            or account_of(await asyncio.to_thread(M.read_config, st)) != core['account']
            or not await asyncio.to_thread(proof_ok, st, core, proof)):
        st.log('automation_grant', id=core['id'], kind=t['kind'], result='stale')
        return {'ok': False, 'why': 'automation_grant_stale'}
    replaced = await asyncio.to_thread(_store, st, core, proof, key, epoch)
    if replaced is None:
        return {'ok': False, 'why': 'automation_grant_stale'}     # revoked while the card was open
    st.log('automation_grant', id=core['id'], kind=t['kind'], task=t['id'][:16], sha=digest(core)[:16],
           device=proof.get('device'), request=proof.get('rid'), to=len(core['to']), domains=len(core['domains']),
           files='mail+files' in core['actions'], masked=proof.get('summary') != text, result='ok', count=len(replaced))
    await push(host)
    return {'ok': True, 'grant': core['id'], 'expires': core['expires'], 'max_uses': core['max_uses']}


# ------------------------------------------------------------------ list / revoke (Settings + CLI)
def items(host) -> list[dict]:
    st = host.st
    with contextlib.suppress(Exception):
        from . import tasks
        sweep_tasks(host, {e['id'] for e in tasks.discover(host.scheduler.workdir())})
    with _LOCK:
        try:
            data = load(st)
        except (ValueError, OSError):
            return []
        rows = list(data['grants'].items())
    from . import mail as M
    try:
        account = account_of(M.read_config(st))
    except Exception:  # noqa: BLE001 — no readable mail config: no grant is usable
        account = None
    out = []
    for gid, row in rows:
        c = (row or {}).get('core') or {}
        t = c.get('target') or {}
        if not core_ok(c):
            continue
        w = why(st, row, current_digest(host, t['kind'], t['id']), data, account)
        state = 'live' if w is None else 'stale' if w in ('stale', 'invalid', 'estop', 'account') else w
        out.append({'id': gid, 'kind': t['kind'], 'target': c.get('name') if t['kind'] == 'hook' else t['id'],
                    'actions': list(c['actions']), 'to': list(c['to']), 'domains': list(c['domains']),
                    'expires': c['expires'], 'permanent': c['expires'] is None, 'max_uses': c['max_uses'],
                    'uses': int(row.get('uses') or 0),
                    'created': c['created'], 'state': state})
    out.sort(key=lambda x: (x['state'] != 'live', -x['created']))
    return out[:100]


def revoke(st, gid, by=None) -> bool:
    if not isinstance(gid, str) or not ID.fullmatch(gid):
        return False
    with _LOCK:
        try:
            data = load(st)
        except (ValueError, OSError):
            return False
        row = data['grants'].get(gid)
        if not isinstance(row, dict) or not isinstance(row.get('core'), dict):
            return False
        t = row['core'].get('target') or {}
        key = target_key(str(t.get('kind')), str(t.get('id')))
        row['revoked'] = True
        row['revoked_at'] = int(time.time())
        data['epochs'][key] = data['epochs'].get(key, 0) + 1
        save(st, data)
    st.log('automation_grant_revoke', id=gid, kind=t.get('kind'), device=by, generation=data['epochs'][key])
    return True


async def push(host, s=None) -> None:
    """The Settings list: `{t:'automation_grants', items}` to one remote, or to every P33 remote after a change."""
    msg = {'t': 'automation_grants', 'items': await asyncio.to_thread(items, host)}
    if s is not None:
        await host.send_app(s, msg)
    elif callable(getattr(host, '_post', None)) and callable(getattr(host, '_send_p33', None)):
        host._post(host._send_p33, lambda x: msg)


async def on_phone(host, s, t: str, obj: dict) -> None:
    """Settings → Safety: list, or revoke one grant. Any paired remote may revoke (narrowing: signature, no UV)."""
    if not host.st.sign_key(s.device):
        return
    if t == 'automation_grant_revoke':
        gid = obj.get('id')
        if await asyncio.to_thread(revoke, host.st, gid, s.device):
            with contextlib.suppress(Exception):
                host.activity('automation_grant_revoke', id=gid, by=host._dname(s.device))
            await push(host)
            if not (callable(getattr(host, '_post', None)) and callable(getattr(host, '_send_p33', None))):
                await push(host, s)
            return
    await push(host, s)


async def control(host, req: dict, peer) -> dict:
    """elevate.sock `{t:'automation_grant', op: grant | list | revoke}` (the CLI)."""
    op = req.get('op')
    if op == 'grant':
        return await issue(host, req, peer)
    if op not in ('list', 'revoke'):
        return {'ok': False, 'why': 'shape', 'detail': 'op: grant | list | revoke'}
    # security impl r1 L1: the CLI reaches serve from any Agent turn. Recipient addresses and revoking (a webhook turn
    # could switch off the owner's automation) are the owner's own turn only; any other turn sees counts. The phone's
    # Settings → Safety list / revoke (on_phone: a paired remote's signed control) is unchanged.
    owner = owner_turn(host, peer, await asyncio.to_thread(owner_facts, host, peer))
    if op == 'list':
        rows = await asyncio.to_thread(items, host)
        if owner:
            return {'ok': True, 'items': rows}
        st_ = {}
        for it in rows:
            st_[it['state']] = st_.get(it['state'], 0) + 1
        return {'ok': True, 'count': len(rows), 'states': st_, 'items': [], 'counts_only': True}
    if not owner:
        host.st.log('automation_grant_deny', why='automation_grant_owner_turn', reason='revoke')
        return {'ok': False, 'why': 'automation_grant_owner_turn',
                'detail': ('只有主人自己的回合（手机网页、本机终端，或已认证的主人渠道）才能用命令撤销自动化外发授权；'
                           '主人也可以在 设置 → 安全 → 自动化外发授权 里直接撤销 / Only the owner\'s own turn (Web App, local '
                           'terminal or a verified owner channel) can revoke an automation send grant from the command '
                           'line; the owner can also revoke it in Settings → Safety → Automation send grants.')}
    if op == 'revoke':
        ok = await asyncio.to_thread(revoke, host.st, req.get('id'), 'cli')
        if ok:
            await push(host)
        return {'ok': ok, **({} if ok else {'why': 'not_found'}), 'items': await asyncio.to_thread(items, host)}


# ------------------------------------------------------------------ CLI (under the existing `agentj automation`)
def add_parsers(ps) -> None:
    x = ps.add_parser('grant', help='授权一个自动化对外发信（一次指纹卡，仅主人回合）/ let one automation mail listed recipients (one fingerprint card, owner turn only)')
    g = x.add_mutually_exclusive_group(required=True)
    g.add_argument('--hook', help='Webhook 名称或 id / webhook name or id')
    g.add_argument('--task', help='定时任务 id / scheduled task id')
    x.add_argument('--to', action='append', default=[], help='允许的收件人地址（可重复）/ allowed recipient address (repeatable)')
    x.add_argument('--domain', action='append', default=[], help='允许的域名，含子域名（可重复；内置清单上的公共邮箱域名和公共后缀（如 gmail.com、co.uk）只能写精确地址；清单并不完整——请核对卡片上的域名）/ allowed domain incl. subdomains (repeatable; public mailbox domains and public suffixes on the built-in list (e.g. gmail.com, co.uk) only as exact addresses; the list is not complete — check the domain on the card)')
    x.add_argument('--expires', help='有效期，如 30d / 12h（默认直到撤销）/ lifetime, e.g. 30d or 12h (default: until revoked)')
    x.add_argument('--max', type=int, help='最多使用次数 / maximum number of sends')
    x.add_argument('--files', action='store_true', help='也允许带附件（另一项动作，默认不允许）/ also allow attachments (a separate action, off by default)')
    x.add_argument('--json', action='store_true')
    x = ps.add_parser('grants', help='列出自动化外发授权 / list automation send grants')
    x.add_argument('--json', action='store_true')
    x = ps.add_parser('revoke', help='撤销一个自动化外发授权 / revoke an automation send grant')
    x.add_argument('id')
    x.add_argument('--json', action='store_true')


def cmd(a) -> None:
    import sys
    from .elevate import client_request
    from .state import State
    st = State()
    if a.automation_cmd == 'grant':
        req = {'t': 'automation_grant', 'op': 'grant', 'kind': 'hook' if a.hook else 'task', 'ref': a.hook or a.task,
               'to': a.to, 'domains': a.domain, 'expires': a.expires, 'max_uses': a.max, 'files': a.files}
    elif a.automation_cmd == 'grants':
        req = {'t': 'automation_grant', 'op': 'list'}
    else:
        req = {'t': 'automation_grant', 'op': 'revoke', 'id': a.id}
    res = client_request(st, req, timeout=WAIT + 100)
    if a.json or a.automation_cmd != 'grants':
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif res.get('counts_only'):
        print(f"{res.get('count', 0)} grant(s): " + ', '.join(f'{k}={v}' for k, v in sorted((res.get('states') or {}).items()))
              + ' — addresses are listed only in the owner\'s own turn / 只有主人自己的回合才列出地址')
    else:
        for it in res.get('items') or []:
            scope = ', '.join(it['to'] + ['*@' + d for d in it['domains']])
            print(f"{it['id'][:12]}  {it['state']:<8} {it['kind']}:{it['target']}  {scope}  uses={it['uses']}"
                  + (f"/{it['max_uses']}" if it['max_uses'] else ''))
    if not res.get('ok'):
        sys.exit(2)
