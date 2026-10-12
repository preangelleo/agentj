"""P184 inside serve: the address book on the phone, the contact card, 「转发到邮箱」 and the Agent's own mail (PROTOCOL §25).

Phone (approved devices only, inside the Noise session; routed through elevate.PHONE_TYPES):
- `ct_list {r, sort?, q?, limit?}` → `ct_res {r, ok, contacts, count, me, mail}` — `me` = the owner's own addresses (the
  e-mail channel's allowlist rows with role owner), `mail` = can this computer send (P170 SMTP + app password).
- `ct_save {r, contact, id?}` → `ct_res` — the forward dialog's 「新增」 (address + nickname) or an edit.
- `mail_fwd {r, to: [{addr, name?}], turn?, mid?, input?}` → `mail_fwd_res {r, ok, why?, sent?, files?, skipped?}` — a reply
  page (P189: the input that started it — sender remote, time, quote — unless `input: false`, then the reply, as the
  mail_template HTML + plain text; its files attached within FILES_MAX, the rest listed 「文件太大未附上」) or one file of it, mailed through the owner's SMTP to exactly the addresses the owner picked in the dialog. Then every
  address is remembered (new ones as contacts, source `phone`) and counted (last_sent_at, send_count).
- `ct_card_answer {id, n, ok, contact?}` — the owner's answer to a contact card.

Agent (elevate.sock, visible inside the fence, `agentj contacts` / `agentj mail send`):
- `{t: contacts, op: list|find|get|add|edit|rm|import|export|ping}` → contacts.local (the file is outside the fence);
- `{t: contacts, op: card, id?, contact, why}` → a contact card on the paired phones (new, pre-filled with what the owner
  said; or completing an existing contact). The owner edits and saves (≥ one e-mail or phone) or cancels. → saved | merged |
  cancelled | timeout | gone. Nothing is saved without the owner's tap;
- `{t: mail_send, to, subject, body, files}` → names are looked up in the address book (no unique match → `not_found`, the
  Agent says so and offers a card); files must be the Agent's own (media.check: inside its folder, no secrets); then an
  approval card shows recipients, subject and attachments — mail goes out only when the owner allows. A group member's /
  guest's turn never reaches that card (serve.ask). Sent addresses are remembered with source `agent`.
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
_ID = re.compile(r"[0-9a-f]{32}")
PHONE_TYPES = ("ct_list", "ct_save", "ct_card_answer", "mail_fwd")
T = {
    "zh": {"file_body": "{agent} 转来一个文件：{name}", "file_subj": "{agent} 转来文件：{name}",
           "why_new": "存成新联系人", "why_update": "补全联系人"},
    "en": {"file_body": "{agent} forwarded a file: {name}", "file_subj": "{agent} forwarded: {name}",
           "why_new": "Save as a new contact", "why_update": "Complete this contact"},
}


class ContactService:
    def __init__(self, elev):
        self.elev = elev
        self.host = elev.host
        self.st = elev.st
        self.cards: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.bg: set = set()
        self.rate: dict[str, collections.deque] = collections.defaultdict(collections.deque)

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
        from . import mail as M
        return [r["addr"] for r in M.read_config(self.st).get("allow", []) if r.get("role") == "owner"]

    def build(self, cfg: dict, to: list[str], subject: str, body: str, files=(), kind: str = "forward",
              html: str | None = None) -> EmailMessage:
        from . import mail as M
        m = EmailMessage(policy=email.policy.SMTP)
        m["From"] = email.utils.formataddr((self.agent_name(), cfg["address"]))
        m["To"] = ", ".join(to)
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

    def smtp_send(self, cfg: dict, pw: str, msg: EmailMessage, to: list[str]) -> None:
        from . import mail as M
        c = M.smtp_open(cfg, pw)
        try:
            c.send_message(msg, from_addr=cfg["address"], to_addrs=to)
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
            req = {"op": "edit", "id": cid, "changes": raw} if isinstance(cid, str) and cid else \
                {"op": "add", "contact": raw, "source": "phone"}
            res = await asyncio.to_thread(C.local, self.st, req)
            self.st.log("contacts_save", device=s.device, result=res.get("result") or res.get("why"))
            return await self.host.send_app(s, {"t": "ct_res", "r": r, **res})
        if t == "mail_fwd":
            res = await self.forward(s, obj)
            self.st.log("mail_fwd", device=s.device, result="ok" if res.get("ok") else res.get("why"), to=res.get("sent", 0),
                        files=res.get("files", 0))
            return await self.host.send_app(s, {"t": "mail_fwd_res", "r": r, **res})

    def _list(self, obj: dict) -> dict:
        q = obj.get("q") if isinstance(obj.get("q"), str) else ""
        lim = obj.get("limit") if type(obj.get("limit")) is int else 500
        res = C.local(self.st, {"op": "find", "q": q[:80], "limit": min(lim, 200)} if q.strip() else
                      {"op": "list", "sort": obj.get("sort"), "limit": min(lim, C.MAX_CONTACTS)})
        _, _, why = self.mail_state()
        res["me"] = self.owner_addresses()
        res["mail"] = {"ready": why is None, **({"why": why} if why else {})}
        res["count"] = res.get("count", len(res.get("contacts") or []))
        return res

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
        content = await asyncio.to_thread(self._content, turn, mid, obj.get("input") is not False)
        if content is None:
            return {"ok": False, "why": "not_found"}
        subject, body, files, html, skipped = content
        msg = self.build(cfg, [r["addr"] for r in recips], subject, body, files, html=html)
        q.append(now)
        from . import mail as M
        try:
            await asyncio.to_thread(self.smtp_send, cfg, pw, msg, [r["addr"] for r in recips])
        except M.MailError as e:
            return {"ok": False, "why": "send_failed", "detail": e.why}
        await asyncio.to_thread(C.record_sent, self.st, recips, "phone")
        return {"ok": True, "sent": len(recips), "files": len(files), **({"skipped": skipped} if skipped else {})}

    def _content(self, turn, mid, with_input: bool = True) -> tuple[str, str, list, str | None, int] | None:
        """(subject, text, [(name, mime, bytes)], html, files not attached) of what the owner forwards, or None when gone."""
        from . import mail_template
        from . import tg_media
        from .privacy import redact
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
            try:
                return rec["name"], rec["mime"], tg_media.read_checked(rec)
            except Exception:   # noqa: BLE001 — vanished / changed since the page: `gone`
                return None
        if isinstance(mid, str):
            one = read(mid)
            if one is None:
                return None
            return L["file_subj"].format(agent=agent, name=one[0]), L["file_body"].format(agent=agent, name=one[0]), [one], None, 0
        hist = getattr(self.host, "hist", None)
        page = hist.get(turn) if hist is not None else None
        text = ((page or {}).get("reply") or {}).get("text", "") if page and page.get("end") in ("done", "stopped", "failed") else ""
        if not text.strip():
            return None
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
            if got is None:
                skipped.append((name, size, "gone"))
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
                                                   files=[(n, len(b)) for n, _, b in files], skipped=skipped)
        return subject, body, files, html, len(skipped)

    # ------------------------------------------------------------------ the Agent
    async def agent(self, req: dict) -> dict:
        op = req.get("op")
        if op == "ping":
            return {"ok": True}
        if op == "card":
            return await self.card(req)
        if op in ("list", "find", "get", "add", "edit", "rm", "import", "export"):
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
        """`agentj mail send`: always the owner's approval card first; only the addresses shown there."""
        from . import media as media_mod
        from . import provenance, tg_media
        to_raw = req.get("to")
        if isinstance(to_raw, str):
            to_raw = [to_raw]
        if not isinstance(to_raw, list) or not 1 <= len(to_raw) <= MAX_TO or not all(isinstance(x, str) for x in to_raw):
            return {"ok": False, "why": "to", "detail": f"1–{MAX_TO} recipients"}
        subject = C._s(req.get("subject"), 200)
        body = str(req.get("body") or "")[:100_000]
        if not subject and not body.strip():
            return {"ok": False, "why": "empty"}
        cfg, pw, why = self.mail_state()
        if why:
            return {"ok": False, "why": why}
        if provenance.untrusted_turn(getattr(self.host, "agent", None)):
            return {"ok": False, "why": "group_source"}
        recips, missing = [], []
        for who in to_raw:
            addr, c = await asyncio.to_thread(self.resolve, who)
            if addr is None:
                missing.append(who[:60])
            elif addr not in [r["addr"] for r in recips]:
                recips.append({"addr": addr, "name": (c or {}).get("name", ""), "who": who})
        if missing:
            return {"ok": False, "why": "not_found", "who": missing,
                    "detail": "没找到这些人，请告诉主人「没找到叫 X 的人」，并用 agentj contacts card 请主人保存 / "
                              "not in the address book: tell the owner and offer `agentj contacts card`"}
        files, names, total = [], [], 0
        m = media_mod.Media(self.st, (getattr(self.host, "agent_cfg", None) or {}).get("dir"))
        for p in req.get("files") or []:
            if not isinstance(p, str):
                return {"ok": False, "why": "file"}
            rec, fwhy = await asyncio.to_thread(m.check, media_mod.Ref(p, p, True))
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
        lines = [("发邮件给：" if zh else "Mail to: ") + "、".join(
                    f"{r['name']} <{r['addr']}>" if r["name"] else r["addr"] for r in recips),
                 ("标题：" if zh else "Subject: ") + subject]
        if names:
            lines.append(("附件：" if zh else "Attachments: ") + "、".join(names))
        lines.append(("正文：" if zh else "Body: ") + body[:600] + (" …" if len(body) > 600 else ""))
        ans = await self.host.ask("agentj mail send", {"summary": "\n".join(lines)}, batch=False)
        if ans.get("behavior") != "allow":
            self.st.log("mail_send", result="denied", to=len(recips))
            return {"ok": False, "why": "denied", "detail": "主人在手机上拒绝了 / declined on the phone"}
        from .privacy import redact
        msg = self.build(cfg, [r["addr"] for r in recips], subject, redact(body), files, kind="agent")
        from . import mail as M
        try:
            await asyncio.to_thread(self.smtp_send, cfg, pw, msg, [r["addr"] for r in recips])
        except M.MailError as e:
            self.st.log("mail_send", result="failed", why=e.why)
            return {"ok": False, "why": "send_failed", "detail": e.why}
        rec = await asyncio.to_thread(C.record_sent, self.st, [{"addr": r["addr"], "name": r["name"]} for r in recips], "agent")
        self.st.log("mail_send", result="sent", to=len(recips), files=len(files))
        return {"ok": True, "sent": [r["addr"] for r in recips], "files": names, "new_contacts": [x["addr"] for x in rec if x["added"]]}

    # ------------------------------------------------------------------ the contact card
    def card_msg(self, c: dict) -> dict:
        return {"t": "ct_card", "id": c["id"], "n": c["nonce"], "mode": c["mode"], "contact": c["contact"], "base": c.get("base"),
                "why": c["why"], "ttl": max(0, int(c["deadline"] - time.monotonic()))}

    async def on_ready(self, s) -> None:
        for c in list(self.cards.values()):
            if not c["fut"].done():
                await self.host.send_app(s, self.card_msg(c))

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
        if req.get("id"):
            base = C.get(C.read(self.st), str(req["id"]))
            if base is None:
                return {"ok": False, "result": "not_found"}
        try:
            pre = C.clean(req.get("contact") or {}, partial=True)
        except C.ContactError as e:
            return {"ok": False, "result": "refused", "why": e.why}
        if len(self.cards) >= 4:
            return {"ok": False, "result": "busy"}
        cid = secrets.token_hex(16)
        L = T[self.lang()]
        why = C._s(req.get("why"), 200) or (L["why_update"] if base else L["why_new"])
        c = {"id": cid, "nonce": secrets.token_hex(8), "mode": "update" if base else "new",
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
        if c["mode"] == "update":
            req = {"op": "edit", "id": c["base"]["id"], "changes": raw if isinstance(raw, dict) else {}}
        else:
            req = {"op": "add", "contact": raw, "source": "owner"}
        res = await asyncio.to_thread(C.local, self.st, req)
        if not res.get("ok"):
            return await self.host.send_app(s, {"t": "ct_card_err", "id": cid, "why": res.get("why") or "shape"})
        c["fut"].set_result({"result": "saved" if res.get("result") in ("added", "updated") else res.get("result"),
                             "contact": res.get("contact")})
