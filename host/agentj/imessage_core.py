"""iMessage assistant core — read-only chat.db reader + deterministic rule filter (stdlib only).

Shared byte-for-byte by the plaza package `imessage-assistant` (scripts/imsg_core.py) and the Agent J host
(agentj/imessage_core.py). Change it in one place and copy; both sides pin its sha256 in a test.

Hard rules (do not relax):
- The database is opened `mode=ro` + `PRAGMA query_only=1`, short-lived, never the `immutable` URI flag (that skips the WAL,
  where the newest messages are), never copied. There is no write statement anywhere in this file (a test greps for them).
- New messages are taken by `message.ROWID` cursor, never by time (iCloud sync inserts old dates with new ROWIDs).
- The body is `text`, and when that is empty the NSAttributedString typedstream in `attributedBody` (macOS 13+ clears
  `text` some time after arrival; a reader that trusts `text` silently drops messages after the Mac sleeps).
- Message text is untrusted data. Nothing here acts on it; the rule filter only labels it.
- Identity comes from the handle only (exact, or a unique last-10-digit match); never guessed from the text.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import urllib.parse

APPLE_EPOCH = 978307200          # 2001-01-01T00:00:00Z
EXIT_OK, EXIT_OTHER, EXIT_NO_FDA, EXIT_NO_DB, EXIT_SCHEMA, EXIT_ARGS = 0, 1, 2, 3, 4, 5
STATUS = {EXIT_OK: "ok", EXIT_NO_FDA: "no_fda", EXIT_NO_DB: "no_db", EXIT_SCHEMA: "schema", EXIT_OTHER: "error"}
REQUIRED = {"message": ("ROWID", "guid", "text", "attributedBody", "handle_id", "date", "is_from_me"),
            "handle": ("ROWID", "id"), "chat": ("ROWID", "chat_identifier"), "chat_message_join": ("chat_id", "message_id")}
OPTIONAL_MSG = ("date_read", "is_read", "service", "cache_has_attachments", "associated_message_type", "item_type",
                "date_edited", "date_retracted", "thread_originator_guid", "balloon_bundle_id", "is_spam",
                "is_audio_message")
OPTIONAL_CHAT = ("display_name", "style", "is_filtered")
BODY_MAX = 32 * 1024
STALE_S = 24 * 3600


class ReadError(Exception):
    def __init__(self, code: int, why: str):
        super().__init__(why)
        self.code, self.why = code, why


def default_db() -> str:
    return os.path.join(os.path.expanduser("~"), "Library", "Messages", "chat.db")


# ------------------------------------------------------------------ opening
def _classify_open_error(e: BaseException) -> ReadError:
    s = str(e).lower()
    if isinstance(e, PermissionError) or "authorization denied" in s or "not permitted" in s:
        return ReadError(EXIT_NO_FDA, "no_fda")
    if isinstance(e, FileNotFoundError):
        return ReadError(EXIT_NO_DB, "no_db")
    if "unable to open" in s:
        return ReadError(EXIT_NO_FDA, "no_fda")   # TCC refusals surface as this from sqlite
    if "no such table" in s or "no such column" in s:
        return ReadError(EXIT_SCHEMA, "schema")
    return ReadError(EXIT_OTHER, type(e).__name__)


def connect(path: str) -> sqlite3.Connection:
    """Read-only, WAL-aware, short-lived. Raises ReadError with an exit code."""
    try:
        os.stat(path)            # FileNotFoundError → no_db; PermissionError (TCC on ~/Library/Messages) → no_fda
    except OSError as e:
        raise _classify_open_error(e) from None
    uri = "file:" + urllib.parse.quote(os.path.abspath(path)) + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.execute("PRAGMA query_only = 1")
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    except (sqlite3.Error, OSError) as e:
        raise _classify_open_error(e) from None
    return conn


def columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}   # table names are our own constants


def _schema(conn) -> dict:
    cols = {t: columns(conn, t) for t in REQUIRED}
    cols.update({t: columns(conn, t) for t in ("attachment", "message_attachment_join")})
    missing = [f"{t}.{c}" for t, cs in REQUIRED.items() for c in cs if c not in cols[t]]
    if missing:
        raise ReadError(EXIT_SCHEMA, "schema:" + ",".join(missing[:6]))
    return cols


# ------------------------------------------------------------------ decoding
def decode_attributed_body(blob) -> str | None:
    """NSAttributedString typedstream → its string. 'NSString', then the '+' type tag, then a length:
    one byte (< 0x80), 0x81 + uint16 LE, or 0x82 + uint32 LE; then that many UTF-8 bytes."""
    if not blob:
        return None
    b = bytes(blob)
    i = b.find(b"NSString")
    if i < 0:
        return None
    j = b.find(b"+", i + 8)
    if j < 0 or j + 1 >= len(b):
        return None
    k = j + 1
    n = b[k]
    if n == 0x81:
        if k + 3 > len(b):
            return None
        n, k = int.from_bytes(b[k + 1:k + 3], "little"), k + 3
    elif n == 0x82:
        if k + 5 > len(b):
            return None
        n, k = int.from_bytes(b[k + 1:k + 5], "little"), k + 5
    elif n < 0x80:
        k += 1
    else:
        return None
    raw = b[k:k + n]
    if len(raw) < n:
        return None
    return raw.decode("utf-8", "replace")


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = s.replace("￼", "").replace("\x00", "")
    return s.strip()[:BODY_MAX]


def apple_time(v) -> float | None:
    """2001-based; nanoseconds since 10.13 (|v| > 1e11), seconds before. 0 / NULL → None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v == 0:
        return None
    if abs(v) > 1e11:
        v /= 1e9
    return v + APPLE_EPOCH


def iso(t: float | None) -> str | None:
    return None if t is None else time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))


def kind_of(r: dict) -> str:
    if r.get("is_from_me"):
        return "self"
    amt = r.get("associated_message_type") or 0
    if 2000 <= amt <= 3007:
        return "reaction"
    if (r.get("item_type") or 0) != 0:
        return "system"
    if (r.get("date_retracted") or 0) > 0:
        return "retracted"
    return "message"


# ------------------------------------------------------------------ reading
def _select(cols: dict) -> str:
    m = ["m.ROWID AS rowid", "m.guid", "m.text", "m.attributedBody", "m.date", "m.is_from_me"]
    m += [f"m.{c}" if c in cols["message"] else f"NULL AS {c}" for c in OPTIONAL_MSG]
    c = [f"c.{x} AS chat_{x}" if x in cols["chat"] else f"NULL AS chat_{x}" for x in OPTIONAL_CHAT]
    return ("SELECT " + ", ".join(m + ["h.id AS handle", "c.chat_identifier"] + c) +
            " FROM message m LEFT JOIN handle h ON h.ROWID = m.handle_id"
            " LEFT JOIN (SELECT message_id, MIN(chat_id) AS chat_id FROM chat_message_join GROUP BY message_id) j"
            " ON j.message_id = m.ROWID LEFT JOIN chat c ON c.ROWID = j.chat_id")


def _attachments(conn, cols: dict, rowid: int) -> list:
    if not cols.get("attachment") or not cols.get("message_attachment_join"):
        return []
    want = [x for x in ("mime_type", "transfer_name", "total_bytes") if x in cols["attachment"]]
    if not want:
        return []
    q = ("SELECT " + ", ".join("a." + x for x in want) + " FROM message_attachment_join maj "
         "JOIN attachment a ON a.ROWID = maj.attachment_id WHERE maj.message_id = ? LIMIT 20")
    try:
        return [dict(zip(want, r)) for r in conn.execute(q, (rowid,))]
    except sqlite3.Error:
        return []


def _shape(conn, cols, r: dict, body: bool) -> dict:
    text, src = clean_text(r.get("text")), "text"
    if not text:
        dec = clean_text(decode_attributed_body(r.get("attributedBody")))
        text, src = (dec, "attributedBody") if dec else ("", "none")
    t = apple_time(r.get("date"))
    style = r.get("chat_style")
    out = {"rowid": r["rowid"], "guid": r.get("guid"), "kind": kind_of(r), "date": t, "date_utc": iso(t),
           "is_read": bool(r.get("is_read")), "service": r.get("service"), "sender": r.get("handle"),
           "chat_identifier": r.get("chat_identifier"), "chat_name": r.get("chat_display_name") or None,
           "is_group": style == 43, "chat_is_filtered": r.get("chat_is_filtered"), "is_spam_flag": r.get("is_spam"),
           "is_edited": (r.get("date_edited") or 0) > 0, "is_reply": bool(r.get("thread_originator_guid")),
           "app_payload": r.get("balloon_bundle_id"), "is_audio": bool(r.get("is_audio_message")),
           "text_source": src, "attachments": _attachments(conn, cols, r["rowid"]) if r.get("cache_has_attachments") else []}
    if body:
        out["text"] = text
    else:
        out["text_len"] = len(text)
    return out


def max_rowid(path: str) -> int:
    conn = connect(path)
    try:
        _schema(conn)
        return int(conn.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])
    except sqlite3.Error as e:
        raise _classify_open_error(e) from None
    finally:
        conn.close()


def read(path: str, cursor: int, limit: int = 200, body: bool = True, kinds=("message", "retracted")) -> dict:
    """Rows with ROWID > cursor, oldest first. next_cursor = the last ROWID looked at (skipped kinds included), so a
    reaction never makes the cursor stall. Gaps in ROWID are normal (deleted messages)."""
    if not isinstance(cursor, int) or cursor < 0 or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise ReadError(EXIT_ARGS, "args")
    conn = connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cols = _schema(conn)
        rows = [dict(r) for r in conn.execute(_select(cols) + " WHERE m.ROWID > ? ORDER BY m.ROWID LIMIT ?",
                                              (cursor, limit))]
        msgs, stats, nxt = [], {"scanned": len(rows), "text": 0, "attributedBody": 0, "none": 0}, cursor
        for r in rows:
            nxt = max(nxt, int(r["rowid"]))
            m = _shape(conn, cols, r, body)
            stats[m["text_source"]] += 1
            if m["kind"] in kinds:
                msgs.append(m)
        return {"ok": True, "cursor_in": cursor, "next_cursor": nxt, "count": len(msgs), "more": len(rows) == limit,
                "stats": stats, "messages": msgs}
    except sqlite3.Error as e:
        raise _classify_open_error(e) from None
    finally:
        conn.close()


def read_rowids(path: str, ids, body: bool = True) -> dict:
    """The given ROWIDs again (a digest re-reads its bodies at send time, so no body is ever stored). Missing = deleted."""
    ids = sorted({int(i) for i in ids if isinstance(i, int) and i > 0})[:500]
    if not ids:
        return {}
    conn = connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cols = _schema(conn)
        q = _select(cols) + " WHERE m.ROWID IN (" + ",".join("?" * len(ids)) + ")"
        return {int(r["rowid"]): _shape(conn, cols, dict(r), body) for r in conn.execute(q, ids)}
    except sqlite3.Error as e:
        raise _classify_open_error(e) from None
    finally:
        conn.close()


def read_since(path: str, since: float, limit: int = 500, body: bool = True) -> dict:
    """One-off backfill: messages dated after `since` (unix s) among the newest `limit` rows. Does not move a cursor."""
    conn = connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cols = _schema(conn)
        rows = [dict(r) for r in conn.execute(_select(cols) + " ORDER BY m.ROWID DESC LIMIT ?", (int(limit),))]
        out = [m for m in (_shape(conn, cols, r, body) for r in reversed(rows))
               if m["kind"] in ("message", "retracted") and (m["date"] or 0) >= since]
        return {"ok": True, "count": len(out), "messages": out}
    except sqlite3.Error as e:
        raise _classify_open_error(e) from None
    finally:
        conn.close()


def doctor(path: str) -> dict:
    """Never prints a body. ok / no_fda / no_db / schema / error, plus counts that explain a silent reader."""
    try:
        conn = connect(path)
    except ReadError as e:
        return {"ok": False, "code": e.code, "status": STATUS.get(e.code, "error"), "why": e.why}
    try:
        conn.row_factory = sqlite3.Row
        cols = _schema(conn)
        rows = [dict(r) for r in conn.execute(_select(cols) + " ORDER BY m.ROWID DESC LIMIT 500")]
        text_null = sum(1 for r in rows if not clean_text(r.get("text")))
        decoded = sum(1 for r in rows if not clean_text(r.get("text")) and clean_text(decode_attributed_body(r.get("attributedBody"))))
        services: dict = {}
        for r in rows[:200]:
            services[r.get("service") or "?"] = services.get(r.get("service") or "?", 0) + 1
        mx = int(conn.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])
        hint = "check_sms_forwarding" if services and set(services) <= {"iMessage", "?"} else None
        return {"ok": True, "code": EXIT_OK, "status": "ok", "max_rowid": mx,
                "sample": {"n": len(rows), "text_null": text_null, "decoded": decoded}, "services": services,
                "missing_optional": [c for c in OPTIONAL_MSG if c not in cols["message"]], "hint": hint}
    except ReadError as e:
        return {"ok": False, "code": e.code, "status": STATUS.get(e.code, "error"), "why": e.why}
    except sqlite3.Error as e:
        err = _classify_open_error(e)
        return {"ok": False, "code": err.code, "status": STATUS.get(err.code, "error"), "why": err.why}
    finally:
        conn.close()


# ------------------------------------------------------------------ rule filter (layer 0 + 1)
CATEGORIES = ("code", "delivery", "bank", "travel", "bill", "personal", "group", "other", "spam")
_CODE = [re.compile(p, re.I) for p in (
    r"验证码\s*(?:是|为)?\s*[:：]?\s*([0-9]{4,8})", r"(?:verification|security|login|one[- ]time)\s+code\s*(?:is)?\s*[:：]?\s*([0-9]{4,8})",
    r"\bOTP\b\s*(?:is)?\s*[:：]?\s*([0-9]{4,8})", r"(?:認証|確認)(?:コード|番号)\s*(?:は)?\s*[:：]?\s*([0-9]{4,8})",
    r"(?:code|码)\s*(?:is|为|:|：)\s*([0-9]{4,8})", r"([0-9]{4,8})\s*(?:is your|是您的|为您的|為您的)[^\n]{0,20}(?:code|验证码|驗證碼)")]
_INJECTION = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|above) (?:instructions|prompts?)|disregard (?:the|your) (?:instructions|rules)"
    r"|you are (?:now )?(?:the |a )?(?:system|admin|administrator|developer mode)|system prompt|as an ai (?:assistant|agent)"
    r"|(?:forward|send) (?:the |your |all )?(?:recent )?(?:verification )?codes? to|reply (?:with )?y(?:es)? to confirm"
    r"|忽略(?:之前|以上|前面|上面)(?:的)?(?:所有)?(?:指令|指示|提示)|你(?:现在)?是(?:系统)?管理员|系统提示|把(?:最近的)?验证码(?:发|转发)给"
    r"|AI\s*助手(?:请|必须)|回复\s*Y\s*确认|以前の指示を無視", re.I)
_LINK = re.compile(r"https?://\S+|\b(?:bit\.ly|t\.cn|tinyurl\.com|goo\.gl|is\.gd|u\.nu|dwz\.cn)/\S*|\b[a-z0-9-]+\.(?:top|xyz|click|icu|cn|vip|shop)/\S*", re.I)
_SPAMWORD = re.compile(r"贷款|额度|中奖|领取|免费领|退订|回T|回复TD|优惠券|限时|特价|秒杀|账户异常请点击|点击链接|博彩|返利|加微信"
                       r"|loan|prize|winner|congratulations|claim (?:your|now)|free gift|unsubscribe|reply stop|limited offer|click (?:here|the link)"
                       r"|当選|無料|配信停止", re.I)
_TOPIC = {
    "delivery": re.compile(r"快递|包裹|派送|派件|取件|配送|驿站|丰巢|签收|配達|荷物|お届け"
                           r"|\b(?:delivery|delivered|package|parcel|shipment|out for delivery)\b", re.I),
    "bank": re.compile(r"银行|交易|消费|支出|入账|余额|信用卡|借记卡|异常登录|转账|銀行|ご利用|カード"
                       r"|\b(?:bank|card ending|transaction|debit|credit card|withdrawal|sign-?in attempt)\b", re.I),
    "travel": re.compile(r"航班|登机|值机|列车|车次|高铁|预约|酒店|入住|予約|搭乗|[0-9]+便"
                         r"|\b(?:flight|gate|boarding|check-?in|reservation|appointment|booking)\b", re.I),
    "bill": re.compile(r"账单|缴费|到期|还款|电费|水费|话费|請求|支払"
                       r"|\b(?:bill|due|payment|invoice|statement)\b", re.I),
}


def norm_handle(h: str | None) -> str:
    h = (h or "").strip().lower()
    if "@" in h:
        return h
    return re.sub(r"\D", "", h)


def short_code(h: str | None) -> bool:
    d = norm_handle(h)
    return "@" not in d and 0 < len(d) < 7


def handle_matches(sender: str | None, entries) -> bool:
    """Exact (digits / lower-cased email) or the last 10 digits when exactly one entry shares them. Short codes never
    match by suffix."""
    s = norm_handle(sender)
    if not s:
        return False
    normed = [norm_handle(e) for e in entries if norm_handle(e)]
    if s in normed:
        return True
    if "@" in s or len(s) < 7:
        return False
    tail = [e for e in normed if "@" not in e and len(e) >= 7 and e[-10:] == s[-10:]]
    return len(tail) == 1


def mask_handle(h: str | None) -> str:
    if not h:
        return "未知号码"
    if "@" in h:
        name, _, dom = h.partition("@")
        return (name[:1] + "•••@" + dom) if name else h
    d = re.sub(r"\D", "", h)
    if len(d) < 7:
        return h                     # a short code (bank / carrier / OTP service) is not personal
    return (("+" if h.strip().startswith("+") else "") + d[:-4][:3] + " ••• " + d[-4:]).strip()


def classify(m: dict, cfg: dict | None = None, now: float | None = None) -> dict:
    """→ {category, route: instant|digest|drop, reason, code?, stale}. cfg keys (all optional): strictness
    loose|standard|strict, groups bool, vip [handles], mute [handles or keywords], codes show|mask|off."""
    cfg = cfg or {}
    now = time.time() if now is None else now
    text = m.get("text") or ""
    sender = m.get("sender")
    strict = cfg.get("strictness", "standard")
    stale = bool(m.get("date")) and m["date"] < now - STALE_S
    out = {"category": "other", "route": "digest", "reason": "rule:default", "stale": stale}
    mute = [x for x in (cfg.get("mute") or []) if isinstance(x, str) and x.strip()]
    if handle_matches(sender, mute) or any(not re.search(r"\d{5,}|@", k) and k.lower() in text.lower() for k in mute):
        return {**out, "route": "drop", "reason": "muted"}
    if handle_matches(sender, cfg.get("vip") or []):
        return {**out, "category": "personal", "route": "digest" if stale else "instant", "reason": "vip"}
    if _INJECTION.search(text):
        return {**out, "category": "spam", "route": "drop", "reason": "rule:injection"}
    for rx in _CODE:
        hit = rx.search(text)
        if hit:
            if cfg.get("codes", "show") == "off":
                return {**out, "category": "code", "route": "drop", "reason": "rule:code_off"}
            return {**out, "category": "code", "route": "digest" if stale else "instant", "reason": "rule:code",
                    "code": hit.group(1)}
    links, spamw = bool(_LINK.search(text)), len(_SPAMWORD.findall(text))
    score = spamw * 2 + (1 if links else 0) + (1 if m.get("chat_is_filtered") not in (None, 0) else 0)
    if m.get("is_group"):
        if not cfg.get("groups", True) or strict == "strict":
            return {**out, "category": "group", "route": "drop", "reason": "rule:group_off"}
        return {**out, "category": "group", "route": "digest", "reason": "rule:group"}
    topic = next((k for k, rx in _TOPIC.items() if rx.search(text)), None)
    spam_line = 5 if strict == "loose" else 3
    if score >= spam_line + (2 if topic else 0):     # a real notice with a link is not spam: dropping needs more
        return {**out, "category": "spam", "route": "drop", "reason": f"rule:spam({score})"}
    if topic:
        route = "instant" if topic in ("bank", "travel") and strict != "loose" else "digest"
        if strict == "loose":
            route = "instant"
        return {**out, "category": topic, "route": "digest" if stale else route, "reason": "rule:" + topic}
    if m.get("chat_is_filtered") == 0 and not short_code(sender):
        return {**out, "category": "personal", "route": "digest" if stale else "instant", "reason": "rule:known_sender"}
    if strict == "strict":
        return {**out, "route": "drop", "reason": "rule:strict"}
    return out


def redact_for_model(text: str, limit: int = 1000) -> str:
    """For the optional cloud layer: numbers, e-mails, links and codes become placeholders; at most `limit` characters."""
    t = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<email>", text or "")
    t = _LINK.sub("<link>", t)
    t = re.sub(r"\+?\d[\d\s-]{5,}\d", "<number>", t)
    t = re.sub(r"\d{4,}", "<number>", t)
    return t[:limit]


# ------------------------------------------------------------------ what the owner sees
LABEL = {"zh": {"code": "验证码", "delivery": "快递", "bank": "银行与安全", "travel": "行程与预约", "bill": "账单",
                "personal": "个人消息", "group": "群聊", "other": "其他", "spam": "已拦截"},
         "en": {"code": "Code", "delivery": "Delivery", "bank": "Bank & security", "travel": "Travel & bookings",
                "bill": "Bill", "personal": "Personal", "group": "Group", "other": "Other", "spam": "Blocked"}}
WHY = {"zh": {"vip": "重要联系人", "rule:code": "验证码规则", "rule:known_sender": "熟人会话", "llm": "模型判断",
              "rule:group": "群聊", "rule:default": "未分类"},
       "en": {"vip": "VIP contact", "rule:code": "code rule", "rule:known_sender": "known conversation", "llm": "model",
              "rule:group": "group chat", "rule:default": "unclassified"}}


def _clock(t: float | None) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "--"


def snippet(text: str, n: int = 160) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[:n - 1] + "…"


def sender_label(m: dict, lang: str = "zh") -> str:
    who = mask_handle(m.get("sender"))
    if m.get("is_group"):
        who = (m.get("chat_name") or ("群聊" if lang == "zh" else "group")) + " · " + who
    return who


def format_alert(m: dict, v: dict, codes: str = "show", lang: str = "zh") -> str:
    """One instant alert. The message text is quoted data, marked as such; a code is shown, masked or not at all."""
    lang = "en" if lang == "en" else "zh"
    cat = LABEL[lang].get(v.get("category"), v.get("category"))
    head = f"【{cat}】{sender_label(m, lang)} · {_clock(m.get('date'))}" if lang == "zh" else \
        f"[{cat}] {sender_label(m, lang)} · {_clock(m.get('date'))}"
    if v.get("category") == "code":
        if codes == "mask":
            body = "收到一条验证码（已打码，到「信息」App 查看）" if lang == "zh" else "A code arrived (masked; open Messages)"
        else:
            body = (f"验证码 {v.get('code')}" if lang == "zh" else f"Code {v.get('code')}")
        return head + "\n" + body
    if m.get("kind") == "retracted":
        body = "对方撤回了一条消息" if lang == "zh" else "A message was unsent"
    elif m.get("text"):
        body = "“" + snippet(m["text"]) + "”"
    elif m.get("is_audio"):
        body = "收到一条语音" if lang == "zh" else "A voice message"
    elif m.get("attachments"):
        a = m["attachments"][0]
        body = ("附件：" if lang == "zh" else "Attachment: ") + str(a.get("mime_type") or a.get("transfer_name") or "?")
    else:
        body = "（没有文字）" if lang == "zh" else "(no text)"
    why = WHY[lang].get(v.get("reason"), v.get("reason", ""))
    return head + "\n" + body + ("\n" + ("依据：" if lang == "zh" else "Why: ") + why if why else "")


def format_digest(items: list, blocked: int, stale: int, since: float | None, codes: str = "show", lang: str = "zh") -> str:
    """items = [(message dict or None when deleted, verdict)]."""
    lang = "en" if lang == "en" else "zh"
    span = f"{_clock(since)}–{_clock(time.time())}"
    head = (f"iMessage 摘要（{span}）\n重要与普通 {len(items)} 条 · 已拦截垃圾 {blocked} 条" if lang == "zh" else
            f"iMessage digest ({span})\n{len(items)} messages · {blocked} blocked as spam")
    lines = [head]
    for i, (m, v) in enumerate(items[:30], 1):
        cat = LABEL[lang].get(v.get("category"), v.get("category"))
        if m is None:
            lines.append(f"{i}. 【{cat}】（这条已在「信息」App 里删除）" if lang == "zh" else f"{i}. [{cat}] (deleted in Messages)")
            continue
        if v.get("category") == "code":
            txt = ("验证码（已打码）" if lang == "zh" else "code (masked)") if codes != "show" else (
                ("验证码 " if lang == "zh" else "code ") + str(v.get("code")))
        else:
            txt = snippet(m.get("text") or "", 80) or ("（没有文字）" if lang == "zh" else "(no text)")
        lines.append(f"{i}. 【{cat}】{sender_label(m, lang)} · {_clock(m.get('date'))}：{txt}" if lang == "zh" else
                     f"{i}. [{cat}] {sender_label(m, lang)} · {_clock(m.get('date'))}: {txt}")
    if len(items) > 30:
        lines.append(f"…… 另有 {len(items) - 30} 条" if lang == "zh" else f"… {len(items) - 30} more")
    if stale:
        lines.append(f"— 另有 {stale} 条较早的消息刚同步到本机" if lang == "zh" else f"— {stale} older messages just synced to this Mac")
    return "\n".join(lines)


# ------------------------------------------------------------------ the reader as its own process
def _main(argv=None) -> int:
    """`python3 -I imsg_core.py <doctor|max|read|rowids|since> --db PATH [...]` → one JSON line. The Agent J host runs
    the reader this way, as a child process with a timeout: it holds no send capability and cannot hang the host."""
    import argparse
    import json
    import sys
    p = argparse.ArgumentParser(prog="imsg_core.py")
    p.add_argument("cmd", choices=["doctor", "max", "read", "rowids", "since"])
    p.add_argument("--db", default=default_db())
    p.add_argument("--cursor", type=int, default=0)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--ids", default="")
    p.add_argument("--since", type=float, default=0.0)
    try:
        a = p.parse_args(argv)
    except SystemExit:
        print(json.dumps({"ok": False, "code": EXIT_ARGS, "status": "error", "why": "args"}))
        return EXIT_ARGS
    try:
        if a.cmd == "doctor":
            out = doctor(a.db)
        elif a.cmd == "max":
            out = {"ok": True, "max_rowid": max_rowid(a.db)}
        elif a.cmd == "read":
            out = read(a.db, a.cursor, a.limit)
        elif a.cmd == "rowids":
            got = read_rowids(a.db, [int(x) for x in a.ids.split(",") if x.strip().isdigit()])
            out = {"ok": True, "messages": [got[k] for k in sorted(got)]}
        else:
            out = read_since(a.db, a.since, a.limit)
    except ReadError as e:
        out = {"ok": False, "code": e.code, "status": STATUS.get(e.code, "error"), "why": e.why}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0 if out.get("ok") else int(out.get("code") or EXIT_OTHER)


if __name__ == "__main__":
    raise SystemExit(_main())
