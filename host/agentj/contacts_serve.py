"""P184 inside serve: the address book on the phone, the contact card, 「转发到邮箱」 and the Agent's own mail (PROTOCOL §25).

Phone (approved devices only, inside the Noise session; routed through elevate.PHONE_TYPES):
- `ct_list {r, sort?, q?, limit?}` → `ct_res {r, ok, contacts, count, me, mail}` — `me` = the owner's own addresses (the
  e-mail channel's allowlist rows with role owner), `mail` = can this computer send (P170 SMTP + app password).
- `ct_save {r, contact, id?}` → `ct_res` — the forward dialog's 「新增」 (address + nickname) or an edit.
- `mail_fwd {r, to: [{addr, name?}], turn?, mid?, input?, again?}` → `mail_fwd_res {r, ok, why?, sent?, files?, skipped?,
  skips?, limit?, refused?, ago?}` — a reply
  page (P189: the input that started it — sender remote, time, quote — unless `input: false`, then the reply, as the
  mail_template HTML + plain text; its files attached within FILES_MAX, the rest listed 「文件太大未附上」) or one file of it, mailed through the owner's SMTP to exactly the addresses the owner picked in the dialog. Then every
  address is remembered (new ones as contacts, source `phone`) and counted (last_sent_at, send_count).
  P295: `skips` = [{name, bytes, why: too_big | secret | gone}] — the owner's phone hears the real reason (the outsider's
  mail only says 「未附上」), `limit` = FILES_MAX; `refused` = recipients the SMTP server turned down (the rest went);
  the same page / file to the same recipients again within DUP_WINDOW → why `dup` + `ago` (seconds) until the phone
  resends with `again: true`. A reply that opens with the Agent's short English work notes before its first tool call
  (「Need to create worktree first …」 in a Chinese reply) goes without them.
- `ct_card_answer {id, n, ok, contact?}` — the owner's answer to a contact card.
- P207 `ct_rm {r, id}` → `ct_res {r, ok, result?, contact?, why?}` — delete one contact (slot 0 / the owner: why `owner`).
- P207 `ct_export {r, fmt}` (vcf | csv, default vcf) → `ct_res {r, ok, fmt, text, count}`; a whole answer past the §10.1
  frag ceiling (2 MiB) → why `too_big`.
- P207 `ct_import {r, name, text, fmt?}` (UTF-8 text ≤ IMPORT_TEXT_MAX, fmt auto | vcf | csv | json; name = the file name
  for sniffing) → `ct_res {r, ok, format, total, added, merged, same, skipped, skipped_rows, why?}` (source `phone`).

Agent (elevate.sock, visible inside the fence, `agentj contacts` / `agentj mail send`):
- `{t: contacts, op: list|find|get|add|edit|rm|import|export|ping}` → contacts.local (the file is outside the fence);
- `{t: contacts, op: card, id?, contact, why}` → a contact card on the paired phones (new, pre-filled with what the owner
  said; or completing an existing contact). The owner edits and saves (≥ one e-mail or phone) or cancels. → saved | merged |
  cancelled | timeout | gone. Nothing is saved without the owner's tap;
- `{t: mail_send, to, cc?, bcc?, format?, to_owner?, subject, body, files}` (P355: cc / bcc like to, every recipient counts
  below; format markdown (default, mail_template) | html (mail_html) | text) → `to_owner: true` adds the owner's own addresses (slot 0 + the
  「我本人」 allowlist rows; none → `no_owner_address`) to `to` (union, deduped, ≤ 10 in all); names are looked up in the address book (no unique match → `not_found`, the
  Agent says so and offers a card); files must be the Agent's own (media.check: inside its folder, no secrets); then an
  approval card shows recipients, subject and attachments — mail goes out only when the owner allows. P296: when every
  recipient is the owner's own address (reason `owner_direct`) or on the mail allowlist (P170 rows, any role; reason
  `allowlist`) there is no card; one other recipient → the card as before, which offers each recipient not yet on the
  allowlist as an unticked box 「以后发给 X 不用再确认」 — ticked ones are signed with the allow and join as guests. A group member's / guest's turn never sends (provenance check above the card). Sent addresses are
  remembered with source `agent`.
Logs carry counts and reason words, never an address, a name or a body.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import email.policy
import email.utils
import re
import secrets
import time
from email.message import EmailMessage

from . import contacts as C

TTL = 600
MAX_TO = 10
FWD_PER_HOUR = 30                     # mails per device per hour from the forward dialog
FILES_MAX = 20 * 1024 * 1024          # attachments of one mail (most providers stop at 20–25 MB)
BODY_HTML_MAX = 400_000               # P355: an HTML body (a web report); elevate.MAX_FRAME is 512 KiB a line
FMT_LINE = {"zh": {"markdown": "格式：带格式（Markdown 排版）", "html": "格式：HTML 网页原样（去掉脚本和表单）"},
            "en": {"markdown": "Format: formatted (Markdown)", "html": "Format: the HTML page as is (scripts and forms removed)"}}
DUP_WINDOW = 600                      # P295: the same page to the same recipients within 10 min asks first
_NOTE = re.compile(r"[A-Z][^\n`|#>*\[\]]{8,200}[.:…!]")   # P295: one English sentence of work notes
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_ID = re.compile(r"[0-9a-f]{32}")
PHONE_TYPES = ("ct_list", "ct_save", "ct_card_answer", "mail_fwd")
PHONE_TYPES += ("ct_rm", "ct_export", "ct_import")   # P207: delete one contact, export the book, import a file's text
IMPORT_TEXT_MAX = 1024 * 1024         # P207: ct_import text, UTF-8 bytes
EXPORT_FMTS = ("vcf", "csv")
IMPORT_FMTS = ("auto", "vcf", "csv", "json")
T = {
    "zh": {"file_body": "{agent} 转来一个文件：{name}", "file_subj": "{agent} 转来文件：{name}",
           "why_new": "存成新联系人", "why_update": "补全联系人",
           "why_owner": "通讯录 0 号位是你本人：填上主邮箱（手机号可选），以后「发给我自己」的邮件、报告都发到这里。可以跳过，之后在设置里补。"},
    "en": {"file_body": "{agent} forwarded a file: {name}", "file_subj": "{agent} forwarded: {name}",
           "why_new": "Save as a new contact", "why_update": "Complete this contact",
           "why_owner": "Slot 0 of the address book is you: your main e-mail (phone optional) is where everything \"send it to "
                        "me\" goes — mail, reports. You can skip this and add it later in Settings."},
}


DENIED = {"zh": "主人没在手机上确认，邮件没有发出", "en": "The owner did not confirm on the phone; the mail was not sent"}


class ContactService:
    def __init__(self, elev):
        self.elev = elev
        self.host = elev.host
        self.st = elev.st
        self.cards: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.bg: set = set()
        self.rate: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self.recent: dict[tuple, float] = {}  # P295: (what, recipients) → when it last went out

    def lang(self) -> str:
        return "en" if getattr(self.host, "lang", "zh") == "en" else "zh"

    def agent_name(self) -> str:
        with contextlib.suppress(Exception):
            return self.st.agent_name() or "Agent J"
        return "Agent J"

    # ------------------------------------------------------------------ mail plumbing (P170's account)
    def mail_state(self) -> tuple[dict | None, str | None, str | None]:
        """(config, app password, why-not). Sending needs an address, an SMTP server and the stored app password."""
        from . import mail as M
        cfg = M.read_config(self.st)
        if not cfg.get("address") or not (cfg.get("smtp") or {}).get("host"):
            return None, None, "mail_not_configured"
        pw = M.load_secret(self.st, cfg["address"])
        if not pw:
            return None, None, "no_password"
        return cfg, pw, None

    def owner_addresses(self) -> list[str]:
        """P194: slot 0's e-mails first, then the e-mail channel's 「我本人」 rows (P170)."""
        return C.owner_emails(self.st)

    def allowlisted(self, addrs) -> bool:
        """P296: every address is on the e-mail channel's allowlist (P170, any role) — the same rows that let their mail in."""
        from . import mail as M
        rows = {str(r.get("addr", "")).lower() for r in M.read_config(self.st).get("allow") or []}
        return bool(addrs) and all(str(a).lower() in rows for a in addrs)

    def allow_add(self, addrs: list[str]) -> list[str]:
        """P296: the recipients the owner ticked on the signed mail card join the allowlist as guests (no second card: the
        card's signature covers them, serve._answer checked they were offered). → the addresses actually added."""
        from . import mail as M
        if not addrs:
            return []
        cfg = M.read_config(self.st)
        have = {str(r.get("addr", "")).lower() for r in cfg.get("allow") or []}
        new = [a for a in addrs if a.lower() not in have]
        if not new:
            return []
        try:
            nxt = dict(cfg, allow=[*cfg.get("allow", []), *({"addr": M.norm_addr(a), "role": "guest"} for a in new)])
            mail = getattr(self.host, "mail", None)
            if mail is not None:
                mail.set_config(nxt)
            else:
                M.save_config(self.st, M.validate(nxt))
        except M.MailError as e:
            self.st.log("mail_allow", device="card", result=e.why)
            return []
        self.st.log("mail_allow", device="card", role="guest", result="ok", count=len(new))
        with contextlib.suppress(Exception):
            self.host.activity("decision", result="allow", tool="邮件白名单" if self.lang() == "zh" else "Mail allowlist",
                               text="guest")
        return new

    def build(self, cfg: dict, to: list[str], subject: str, body: str, files=(), kind: str = "forward",
              html: str | None = None, cc=()) -> EmailMessage:
        from . import mail as M
        m = EmailMessage(policy=email.policy.SMTP)
        m["From"] = email.utils.formataddr((self.agent_name(), cfg["address"]))
        m["To"] = ", ".join(to)
        if cc:                         # P355: Cc is a header; Bcc never is (only the SMTP envelope carries it)
            m["Cc"] = ", ".join(cc)
        m["Subject"] = subject[:200] or "Agent J"
        m["Date"] = email.utils.formatdate(localtime=True)
        m["Message-ID"] = email.utils.make_msgid(domain=cfg["address"].rpartition("@")[2])
        m[M.MARK] = kind               # if it comes back to the Agent's own inbox it is never a task (no loops)
        m.set_content(body or " ")
        if html:                       # P189: multipart/alternative — clients without HTML show the plain text
            m.add_alternative(html, subtype="html")
        for name, mime, data in files:
            main, _, sub = (mime or "application/octet-stream").partition("/")
            m.add_attachment(data, maintype=main or "application", subtype=sub or "octet-stream", filename=name)
        return m

    def smtp_send(self, cfg: dict, pw: str, msg: EmailMessage, to: list[str]) -> tuple[int, str]:
        """→ (recipients the server refused, its queue id from the 250 after DATA — `OK 1791991713 …-gsmtp` → `…-gsmtp`)."""
        from . import mail as M
        c = M.smtp_open(cfg, pw)
        got = {}
        data = c.data

        def keep(m):                   # sendmail drops DATA's reply; keep it as the receipt
            code, repl = data(m)
            got["r"] = repl
            return code, repl
        c.data = keep
        try:
            refused = c.send_message(msg, from_addr=cfg["address"], to_addrs=to) or {}
            words = re.findall(r"[A-Za-z0-9._@<>-]{6,}", (got.get("r") or b"").decode("ascii", "replace"))
            return len(refused), (words[-1][:64] if words else "")
        except M.MailError:
            raise
        except Exception as e:   # noqa: BLE001
            raise M._fail(e, "smtp") from None
        finally:
            with contextlib.suppress(Exception):
                c.quit()

    # ------------------------------------------------------------------ the phone
    async def on_phone(self, s, obj: dict) -> None:
        if not self.st.sign_key(s.device):            # only an approved device (same rule as the other cards)
            self.st.log("contacts_refused", device=s.device, reason="no_key")
            return
        t = obj.get("t")
        if t == "ct_card_answer":
            return await self.card_answer(s, obj)
        r = obj.get("r")
        from . import wire
        if not wire.is_rid(r):
            return
        if t == "ct_list":
            res = await asyncio.to_thread(self._list, obj)
            return await self.host.send_app(s, {"t": "ct_res", "r": r, **res})
        if t == "ct_save":
            raw = obj.get("contact")
            cid = obj.get("id")
            req = {"op": "owner_set", "contact": raw, "owner_ok": True} if cid == C.OWNER_ID else \
                {"op": "edit", "id": cid, "changes": raw} if isinstance(cid, str) and cid else \
                {"op": "add", "contact": raw, "source": "phone"}
            res = await asyncio.to_thread(C.local, self.st, req)
            self.st.log("contacts_save", device=s.device, result=res.get("result") or res.get("why"))
            return await self.host.send_app(s, {"t": "ct_res", "r": r, **res})
        if t == "ct_rm":                              # P207
            cid = obj.get("id")
            if not isinstance(cid, str) or not cid or len(cid) > 64:
                res = {"ok": False, "why": "shape"}
            elif cid == C.OWNER_ID:
                res = {"ok": False, "why": "owner"}
            else:
                res = await asyncio.to_thread(C.local, self.st, {"op": "rm", "id": cid})
            self.st.log("contacts_rm", device=s.device, result=res.get("result") or res.get("why"))
            return await self.host.send_app(s, {"t": "ct_res", "r": r, **res})
        if t == "ct_export":                          # P207
            fmt = obj.get("fmt") if obj.get("fmt") is not None else "vcf"
            res = await asyncio.to_thread(self._export, fmt)
            self.st.log("contacts_export", device=s.device, result="ok" if res.get("ok") else res.get("why"),
                        count=res.get("count", 0))
            return await self.host.send_app(s, {"t": "ct_res", "r": r, **res})
        if t == "ct_import":                          # P207
            res = await asyncio.to_thread(self._import, obj)
            self.st.log("contacts_import", device=s.device, result="ok" if res.get("ok") else res.get("why"),
                        added=res.get("added", 0), merged=res.get("merged", 0), skipped=res.get("skipped", 0))
            return await self.host.send_app(s, {"t": "ct_res", "r": r, **res})
        if t == "mail_fwd":
            res = await self.forward(s, obj)
            log = res.pop("_log", {})
            self.st.log("mail_fwd", device=s.device, result="ok" if res.get("ok") else res.get("why"), to=res.get("sent", 0),
                        files=res.get("files", 0), skipped=res.get("skipped", 0), refused=res.get("refused", 0), **log)
            return await self.host.send_app(s, {"t": "mail_fwd_res", "r": r, **res})

    def _list(self, obj: dict) -> dict:
        q = obj.get("q") if isinstance(obj.get("q"), str) else ""
        lim = obj.get("limit") if type(obj.get("limit")) is int else 500
        res = C.local(self.st, {"op": "find", "q": q[:80], "limit": min(lim, 200)} if q.strip() else
                      {"op": "list", "sort": obj.get("sort"), "limit": min(lim, C.MAX_CONTACTS)})
        _, _, why = self.mail_state()
        res["me"] = self.owner_addresses()
        res["owner"] = (C.local(self.st, {"op": "owner_get"}) or {}).get("owner")      # P194: slot 0 (None until filled)
        res["mail"] = {"ready": why is None, **({"why": why} if why else {})}
        res["count"] = res.get("count", len(res.get("contacts") or []))
        return res

    def _export(self, fmt) -> dict:
        """P207 ct_export: the whole book as vCard 3.0 or CSV text; too big for one (fragmented) answer → too_big."""
        if fmt not in EXPORT_FMTS:
            return {"ok": False, "why": "fmt"}
        book = C.read(self.st)
        text = C.export_text(book, fmt)
        res = {"ok": True, "fmt": fmt, "text": text, "count": len(book.get("contacts") or [])}
        from . import wire
        if len(wire.dumps({"t": "ct_res", "r": "x" * 32, **res})) > wire.FRAG_MAX_TOTAL - 1024:
            return {"ok": False, "why": "too_big", "count": res["count"]}
        return res

    def _import(self, obj: dict) -> dict:
        """P207 ct_import: a picked file's text → contacts.import_data (dedupe / merge), source `phone`."""
        text, name = obj.get("text"), obj.get("name")
        fmt = obj.get("fmt") if obj.get("fmt") is not None else "auto"
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "why": "empty"}
        if fmt not in IMPORT_FMTS or (name is not None and not isinstance(name, str)):
            return {"ok": False, "why": "shape"}
        data = text.encode("utf-8", "surrogatepass")
        if len(data) > IMPORT_TEXT_MAX:
            return {"ok": False, "why": "too_big"}
        try:
            return {"ok": True, **C.import_data(self.st, data, (name or "")[:128], fmt, source="phone")}
        except C.ContactError as e:
            return {"ok": False, "why": e.why}

    async def forward(self, s, obj: dict) -> dict:
        to_raw = obj.get("to")
        if not isinstance(to_raw, list) or not 1 <= len(to_raw) <= MAX_TO:
            return {"ok": False, "why": "to"}
        recips, seen = [], set()
        for x in to_raw:
            x = x if isinstance(x, dict) else {"addr": x}
            a = C.norm_email(x.get("addr"))
            if not a:
                return {"ok": False, "why": "address"}
            if a not in seen:
                seen.add(a)
                recips.append({"addr": a, "name": C._s(x.get("name"), C.TEXT["name"])})
        turn, mid = obj.get("turn"), obj.get("mid")
        if type(turn) is not int and not isinstance(mid, str):
            return {"ok": False, "why": "what"}
        cfg, pw, why = self.mail_state()
        if why:
            return {"ok": False, "why": why}
        now = time.monotonic()
        q = self.rate[s.device]
        while q and now - q[0] >= 3600:
            q.popleft()
        if len(q) >= FWD_PER_HOUR:
            return {"ok": False, "why": "rate_limited"}
        key = (turn if type(turn) is int else None, mid if isinstance(mid, str) else None, obj.get("input") is not False,
               tuple(sorted(r["addr"] for r in recips)))
        for k, at in list(self.recent.items()):
            if now - at >= DUP_WINDOW:
                del self.recent[k]
        if key in self.recent and obj.get("again") is not True:
            return {"ok": False, "why": "dup", "ago": int(now - self.recent[key])}
        from . import owner_route
        mine = await asyncio.to_thread(owner_route.all_owner, self.st, [r["addr"] for r in recips])
        content = await asyncio.to_thread(self._content, turn, mid, obj.get("input") is not False, mine)
        if content is None:
            return {"ok": False, "why": "not_found"}
        if content == "secret":       # P211: a file checked for the owner's page, refused by the strict rules for others
            return {"ok": False, "why": "secret"}
        subject, body, files, html, skips = content
        msg = self.build(cfg, [r["addr"] for r in recips], subject, body, files, html=html)
        q.append(now)
        from . import mail as M
        log = {"domains": sorted({r["addr"].rpartition("@")[2] for r in recips})[:MAX_TO],   # never a whole address
               "msgid": str(msg["Message-ID"]).strip("<>")[:120]}
        try:
            refused, smtp_id = await asyncio.to_thread(self.smtp_send, cfg, pw, msg, [r["addr"] for r in recips])
        except M.MailError as e:
            return {"ok": False, "why": "send_failed", "detail": e.why, "_log": log}
        log["smtp"] = smtp_id
        self.recent[key] = now
        await asyncio.to_thread(C.record_sent, self.st, recips, "phone")
        return {"ok": True, "sent": len(recips) - refused, "files": len(files), "_log": log,
                **({"refused": refused} if refused else {}),
                **({"skipped": len(skips), "skips": skips, "limit": FILES_MAX} if skips else {})}

    def _content(self, turn, mid, with_input: bool = True, mine: bool = False) -> tuple[str, str, list, str | None, list] | None:
        """(subject, text, [(name, mime, bytes)], html, [{name, bytes, why}] not attached) of what the owner forwards, or None
        when gone.
        P194: to the owner's own addresses only (`mine`) the owner mode — contacts, links and codes as written, credentials
        still redacted; with anyone else on the list the strict rules."""
        from . import mail_template
        from . import tg_media
        from .privacy import owner_text, redact
        if mine:
            redact = owner_text   # noqa: F811
        L = T[self.lang()]
        agent = self.agent_name()
        media = getattr(self.host, "media", None)
        table = getattr(media, "table", {}) if media is not None else {}

        def read(m: str) -> tuple[str, str, bytes] | None:
            lock = getattr(media, "lock", None)
            with (lock or contextlib.nullcontext()):
                rec = dict(table.get(m) or {})
            if not rec:
                return None
            if not mine and media.strict(rec):   # P211: page files were checked for the owner; others get the strict scan
                return "secret"
            try:
                return rec["name"], rec["mime"], tg_media.read_checked(rec)
            except Exception:   # noqa: BLE001 — vanished / changed since the page: `gone`
                return None
        if isinstance(mid, str):
            one = read(mid)
            if one is None or one == "secret":
                return one
            return L["file_subj"].format(agent=agent, name=one[0]), L["file_body"].format(agent=agent, name=one[0]), [one], None, []
        hist = getattr(self.host, "hist", None)
        page = hist.get(turn) if hist is not None else None
        text = ((page or {}).get("reply") or {}).get("text", "") if page and page.get("end") in ("done", "stopped", "failed") else ""
        if not text.strip():
            return None
        text = self.drop_notes(text)
        files, skipped, total = [], [], 0
        for item in page.get("media") or []:
            if not isinstance(item, dict) or not isinstance(item.get("mid"), str):
                continue
            with (getattr(media, "lock", None) or contextlib.nullcontext()):
                rec = dict(table.get(item["mid"]) or {})
            name, size = str(rec.get("name") or item.get("name") or "file")[:128], int(rec.get("bytes") or item.get("bytes") or 0)
            if rec and total + size > FILES_MAX:          # checked before reading: a 2 GB file is never loaded
                skipped.append((name, size, "too_big"))
                continue
            got = read(item["mid"])
            if got is None or got == "secret":       # an outsider's mail never says why a file stayed home (P295: 「未附上」)
                skipped.append((name, size, "secret" if got else "gone"))
                continue
            total += len(got[2])
            files.append(got)
        src = None
        if with_input and isinstance(page.get("src"), dict) and (page["src"].get("text") or "").strip():
            raw = page["src"]
            src = {"k": raw.get("k"), "name": str(raw.get("name") or "")[:64], "text": redact(str(raw["text"])),
                   "ts": page.get("ts"), "att": [a for a in raw.get("att") or [] if isinstance(a, dict)][:20]}
            if isinstance(raw.get("quote"), dict) and raw["quote"].get("text"):
                src["quote"] = {"who": str(raw["quote"].get("who") or "")[:64], "text": redact(str(raw["quote"]["text"]))[:300]}
        subject, body, html = mail_template.render(lang=self.lang(), agent=agent, src=src, reply=redact(text),
                                                   files=[(n, len(b)) for n, _, b in files],
                                                   skipped=[(n, b, "held" if w == "secret" else w) for n, b, w in skipped])
        return subject, body, files, html, [{"name": n, "bytes": b, "why": w} for n, b, w in skipped]

    def drop_notes(self, text: str) -> str:
        """P295: a Chinese reply's leading English work notes — one short sentence per paragraph, written before the first
        tool call and joined to the page by history.SEP — stay out of the mail. Never the whole reply, never in English."""
        if self.lang() == "en" or not _CJK.search(text):
            return text
        paras = text.split("\n\n")
        n = 0
        while n < len(paras) - 1 and n < 3 and _NOTE.fullmatch(paras[n].strip()) and paras[n].count(" ") >= 3 \
                and "://" not in paras[n] and not _CJK.search(paras[n]):
            n += 1
        rest = "\n\n".join(paras[n:])
        return rest if n and _CJK.search(rest) else text

    # ------------------------------------------------------------------ the Agent
    async def agent(self, req: dict) -> dict:
        op = req.get("op")
        if op == "ping":
            return {"ok": True}
        if op == "card":
            return await self.card(req)
        req = {k: v for k, v in req.items() if k != "owner_ok"}     # P194: only the owner's own paths may change slot 0
        if op in ("list", "find", "get", "add", "edit", "rm", "import", "export", "owner_get", "owner_set"):
            if op == "add":
                req = {**req, "source": req.get("source") if req.get("source") in C.SOURCES else "agent"}
            res = await asyncio.to_thread(C.local, self.st, req)
            if op in ("add", "edit", "rm", "import"):
                self.st.log("contacts_" + op, result=res.get("result") or ("ok" if res.get("ok") else res.get("why")),
                            **({"added": res.get("added"), "merged": res.get("merged")} if op == "import" else {}))
            return res
        return {"ok": False, "why": "shape", "detail": "op: list | find | get | add | edit | rm | import | export | card"}

    def resolve(self, who: str) -> tuple[str | None, dict | None]:
        """A name / nickname / pinyin / address → (address, contact). Only an exact address or one clear match counts."""
        a = C.norm_email(who)
        if a:
            return a, C.by_email(C.read(self.st), a)
        hits = C.find(C.read(self.st), who, 3, floor=60)
        good = [(sc, c) for sc, c in hits if c.get("emails")]
        if not good or (len(good) > 1 and good[1][0] == good[0][0]):
            return None, None
        return good[0][1]["emails"][0]["v"], good[0][1]

    async def agent_send(self, req: dict) -> dict:
        """`agentj mail send`: only the addresses shown on the owner's approval card — or, P296, no card when every
        recipient is the owner's own or on the mail allowlist."""
        from . import media as media_mod
        from . import provenance, tg_media
        from . import mail_template as MT
        lists = {}
        for key in ("to", "cc", "bcc"):      # P355: --cc / --bcc take the same addresses or address-book names as --to
            raw = req.get(key)
            raw = [] if raw is None else [raw] if isinstance(raw, str) else raw
            if not isinstance(raw, list) or len(raw) > MAX_TO or not all(isinstance(x, str) for x in raw):
                return {"ok": False, "why": "to", "detail": f"1–{MAX_TO} recipients"}
            lists[key] = raw
        to_raw = lists["to"]
        fmt = req.get("format") or "markdown"
        if fmt not in MT.FORMATS:
            return {"ok": False, "why": "format", "detail": "markdown | html | text"}
        if req.get("to_owner") is True:      # P205: `--to-owner` = the owner's own addresses, added to any --to (union, deduped)
            owners = await asyncio.to_thread(C.owner_emails, self.st)
            if not owners:
                return {"ok": False, "why": "no_owner_address",
                        "detail": "还没有主人本人的邮箱：请主人在手机「设置 → 通讯录」填 0 号位（我本人）的邮箱，或用 agentj mail allow <地址> "
                                  "把自己的地址加进白名单 / no owner address yet: ask the owner to fill slot 0 (me) of the address "
                                  "book on the phone, or add their address with `agentj mail allow <address>`"}
            to_raw = to_raw + [a for a in owners if a not in to_raw]
        if not to_raw:
            return {"ok": False, "why": "to", "detail": f"1–{MAX_TO} recipients"}
        subject = C._s(req.get("subject"), 200)
        body = str(req.get("body") or "")[:BODY_HTML_MAX if fmt == "html" else 100_000]
        if not subject and not body.strip():
            return {"ok": False, "why": "empty"}
        cfg, pw, why = self.mail_state()
        if why:
            return {"ok": False, "why": why}
        if provenance.untrusted_turn(getattr(self.host, "agent", None)):
            return {"ok": False, "why": "group_source"}
        recips, missing = [], []
        for key, who in [("to", w) for w in to_raw] + [(k, w) for k in ("cc", "bcc") for w in lists[k]]:
            addr, c = await asyncio.to_thread(self.resolve, who)
            if addr is None:
                missing.append(who[:60])
            elif addr not in [r["addr"] for r in recips]:   # to before cc before bcc: one address, one place
                recips.append({"addr": addr, "name": (c or {}).get("name", ""), "who": who, "as": key})
        if len(recips) > MAX_TO:
            return {"ok": False, "why": "to", "detail": f"1–{MAX_TO} recipients"}
        if missing:
            return {"ok": False, "why": "not_found", "who": missing,
                    "detail": "没找到这些人，请告诉主人「没找到叫 X 的人」，并用 agentj contacts card 请主人保存 / "
                              "not in the address book: tell the owner and offer `agentj contacts card`"}
        from . import owner_route, privacy
        mine = await asyncio.to_thread(owner_route.all_owner, self.st, [r["addr"] for r in recips])
        files, names, total = [], [], 0
        m = media_mod.Media(self.st, (getattr(self.host, "agent_cfg", None) or {}).get("dir"))
        for p in req.get("files") or []:
            if not isinstance(p, str):
                return {"ok": False, "why": "file"}
            # P211: to the owner's own address only the deterministic floor (credential files); to anyone else the full scan
            rec, fwhy = await asyncio.to_thread(m.check, media_mod.Ref(p, p, True), mine)
            if fwhy in ("key_file", "key_content"):
                return {"ok": False, "why": "file", "detail": f"{p}: {fwhy} — a credential file never goes as a mail "
                        "attachment; send it with `agentj secret send --file` (a pickup card on the owner's phone)"}
            if rec is None or fwhy:
                return {"ok": False, "why": "file", "detail": f"{p}: {fwhy or 'gone'} (only files in the Agent's folder, no secrets)"}
            try:
                data = await asyncio.to_thread(tg_media.read_checked, rec)
            except Exception:   # noqa: BLE001
                return {"ok": False, "why": "file", "detail": f"{p}: changed"}
            total += len(data)
            if total > FILES_MAX:
                return {"ok": False, "why": "too_big", "detail": f"≤ {FILES_MAX // (1024 * 1024)} MB per mail"}
            files.append((rec["name"], rec["mime"], data))
            names.append(rec["name"])
        zh = self.lang() == "zh"
        keys = len(privacy.credentials(body)) if mine else 0
        def who(kind):
            return "、".join(f"{r['name']} <{r['addr']}>" if r["name"] else r["addr"] for r in recips if r["as"] == kind)
        lines = [("发邮件给：" if zh else "Mail to: ") + who("to")]
        lines += [lab + who(k) for k, lab in (("cc", "抄送：" if zh else "Cc: "), ("bcc", "密送：" if zh else "Bcc: "))
                  if any(r["as"] == k for r in recips)]
        lines.append(("标题：" if zh else "Subject: ") + subject)
        if names:
            lines.append(("附件：" if zh else "Attachments: ") + "、".join(names))
        if fmt != "text":
            lines.append(FMT_LINE["zh" if zh else "en"][fmt])
        if fmt == "html":
            from .mail import html_text
            shown = html_text(body)
        else:
            shown = body
        lines.append(("正文：" if zh else "Body: ") + shown[:600] + (" …" if len(shown) > 600 else ""))
        # P194: say which filter the mail will get — owner-directed (as written; keys → pickup cards) or outward (masked)
        lines.append(("发给你自己：原样发出，不打码" if zh else "To yourself: sent as written, nothing masked") +
                     ((f"；其中 {keys} 个密钥不随邮件发出，改成手机上的密钥领取卡" if zh else
                       f"; {keys} key(s) stay out of the mail and come as secret pickup cards") if keys else "")
                     if mine else ("发给外人：邮箱、电话、密钥等会打码" if zh else "To others: e-mail, phone numbers and keys are masked"))
        listed = mine or await asyncio.to_thread(self.allowlisted, [r["addr"] for r in recips])
        if listed:      # P296: only the owner's own / allowlisted addresses — no card; logged, not asked
            if self.host.stopped():
                return {"ok": False, "why": "stopped", "detail": "已急停，邮件没有发出 / Agent J is stopped; the mail was not sent"}
            from . import approvals
            tool, summary, by = "agentj mail send", "\n".join(lines), "owner_direct" if mine else "allowlist"
            rid = secrets.token_hex(16)
            approvals.record(self.st, rid=rid, agent=getattr(getattr(self.host, "agent", None), "kind", "?"), tool=tool,
                             input_sha256=approvals.input_digest({"summary": summary}),
                             shown_sha256=approvals.shown_digest(tool, summary), decision="allow", reason=by, cats=[])
            with contextlib.suppress(Exception):
                self.host.activity("auto", id=rid, tool=tool, summary=summary, by=by)
            ans = {"behavior": "allow"}
        else:           # P296: the card offers each recipient not yet on the allowlist as an unticked box
            mine_set = set(C.owner_emails(self.st))
            offer = [r["addr"] for r in recips if r["addr"] not in mine_set and not self.allowlisted([r["addr"]])]
            ans = await self.host.ask("agentj mail send", {"summary": "\n".join(lines)}, batch=False, allow_offer=offer)
        if ans.get("behavior") != "allow":
            self.st.log("mail_send", result="denied", to=len(recips))
            return {"ok": False, "why": "denied", "detail": DENIED[self.lang()]}
        added = await asyncio.to_thread(self.allow_add, ans.get("allow_add") or [])
        from .privacy import redact
        rep = {}
        if mine:        # P194: owner-directed — as written, credentials diverted to secret pickup cards
            out, rep = await owner_route.divert(self.host, body, "mail", self.lang(), subject or "邮件")
            subj = privacy.owner_text(subject)
            fn = None
        else:           # P355: an HTML body is masked text node by text node (tags and CSS stay intact)
            out, subj = (body if fmt == "html" else redact(body)), redact(subject)
            fn = redact
        # P355: the Agent's own mail goes out formatted (mail_template, the forward's look) with a plain-text twin
        text, page, info = MT.render_send(lang=self.lang(), agent=self.agent_name(), subject=subj, body=out, fmt=fmt,
                                          text_fn=fn)
        addr_of = {k: [r["addr"] for r in recips if r["as"] == k] for k in ("to", "cc", "bcc")}
        msg = self.build(cfg, addr_of["to"], subj, text, files, kind="agent", html=page, cc=addr_of["cc"])
        from . import mail as M
        try:
            await asyncio.to_thread(self.smtp_send, cfg, pw, msg, [r["addr"] for r in recips])
        except M.MailError as e:
            self.st.log("mail_send", result="failed", why=e.why)
            return {"ok": False, "why": "send_failed", "detail": e.why, **({"allowlisted": added} if added else {})}
        rec = await asyncio.to_thread(C.record_sent, self.st, [{"addr": r["addr"], "name": r["name"]} for r in recips], "agent")
        self.st.log("mail_send", result="sent", to=len(recips), files=len(files), owner=mine, fmt=fmt,
                    cc=len(addr_of["cc"]), bcc=len(addr_of["bcc"]))
        return {"ok": True, "sent": [r["addr"] for r in recips], "files": names, "format": fmt,
                **({"cc": addr_of["cc"]} if addr_of["cc"] else {}), **({"bcc": addr_of["bcc"]} if addr_of["bcc"] else {}),
                **({"local_images": info["local_images"]} if info.get("local_images") else {}),
                **({"removed": info["removed"]} if info.get("removed") else {}), "new_contacts": [x["addr"] for x in rec if x["added"]],
                "owner_direct": mine, "no_card": bool(listed), **({"allowlisted": added} if added else {}), **({"secret_cards": rep["cards"], "withheld": rep["withheld"]} if rep.get("cards") or
                                          rep.get("withheld") else {})}

    # ------------------------------------------------------------------ the contact card
    def card_msg(self, c: dict) -> dict:
        return {"t": "ct_card", "id": c["id"], "n": c["nonce"], "mode": c["mode"], "contact": c["contact"], "base": c.get("base"),
                "why": c["why"], "ttl": max(0, int(c["deadline"] - time.monotonic()))}

    def open_msgs(self, s) -> list[dict]:
        return [self.card_msg(c) for c in list(self.cards.values()) if not c["fut"].done()]   # P203: shared with cards_get

    async def on_ready(self, s) -> None:
        for m in self.open_msgs(s):
            await self.host.send_app(s, m)

    def end_all(self, result: str) -> None:
        for c in list(self.cards.values()):
            if not c["fut"].done():
                c["fut"].set_result({"result": result})

    async def card(self, req: dict, on_card=None) -> dict:
        from . import provenance
        if self.host.stopped():
            return {"result": "stopped"}
        if provenance.untrusted_turn(getattr(self.host, "agent", None)):
            return {"result": "group_source"}
        base = None
        own = req.get("owner") is True or (req.get("id") == C.OWNER_ID and C.owner(C.read(self.st)) is not None)
        if own:                                 # P194: the owner card — slot 0, saved only by the owner's tap
            base = C.owner(C.read(self.st))
        elif req.get("id"):
            base = C.get(C.read(self.st), str(req["id"]))
            if base is None:
                return {"ok": False, "result": "not_found"}
        try:
            pre = C.clean(req.get("contact") or {}, partial=True)
        except C.ContactError as e:
            return {"ok": False, "result": "refused", "why": e.why}
        if own and not pre["emails"] and not (base or {}).get("emails"):
            pre["emails"] = [{"v": a, "label": ""} for a in C.owner_emails(self.st)[:1]]   # a suggestion; the owner confirms
        if len(self.cards) >= 4:
            return {"ok": False, "result": "busy"}
        cid = secrets.token_hex(16)
        L = T[self.lang()]
        why = C._s(req.get("why"), 200) or (L["why_owner"] if own else L["why_update"] if base else L["why_new"])
        c = {"id": cid, "nonce": secrets.token_hex(8), "mode": "owner" if own else "update" if base else "new",
             "contact": {k: v for k, v in pre.items() if v}, "base": C.view(base) if base else None, "why": why,
             "deadline": time.monotonic() + TTL, "fut": asyncio.get_running_loop().create_future()}
        self.cards[cid] = c
        self.st.log("contact_card", id=cid, mode=c["mode"])
        if on_card is not None:
            with contextlib.suppress(Exception):
                await on_card(c)
        await self.host._send_ready(lambda s: self.card_msg(c))
        try:
            res = await asyncio.wait_for(asyncio.shield(c["fut"]), TTL)
        except asyncio.TimeoutError:
            res = {"result": "timeout"}
        finally:
            self.cards.pop(cid, None)
        await self.host._send_ready(lambda s: {"t": "ct_card_done", "id": cid, "result": res.get("result")})
        self.st.log("contact_card_done", id=cid, result=res.get("result"))
        self.results[cid] = res
        while len(self.results) > 50:
            self.results.pop(next(iter(self.results)))
        return {"ok": res.get("result") in ("saved", "merged", "same"), "id": cid, **res}

    async def card_answer(self, s, obj: dict) -> None:
        cid = obj.get("id")
        c = self.cards.get(cid) if isinstance(cid, str) and _ID.fullmatch(cid) else None
        if c is None or c["fut"].done() or obj.get("n") != c["nonce"]:
            return await self.host.send_app(s, {"t": "ct_card_err", "id": cid if isinstance(cid, str) else "", "why": "gone"})
        if obj.get("ok") is not True:
            c["fut"].set_result({"result": "cancelled", "device": s.device})
            return
        raw = obj.get("contact")
        if c["mode"] == "owner":
            req = {"op": "owner_set", "contact": raw if isinstance(raw, dict) else {}, "owner_ok": True}
        elif c["mode"] == "update":
            req = {"op": "edit", "id": c["base"]["id"], "changes": raw if isinstance(raw, dict) else {}}
        else:
            req = {"op": "add", "contact": raw, "source": "owner"}
        res = await asyncio.to_thread(C.local, self.st, req)
        if not res.get("ok"):
            return await self.host.send_app(s, {"t": "ct_card_err", "id": cid, "why": res.get("why") or "shape"})
        c["fut"].set_result({"result": "saved" if res.get("result") in ("added", "updated") else res.get("result"),
                             "contact": res.get("contact")})
