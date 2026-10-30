"""P426 #170 ③: 「我的设备」 — the owner's own devices as one group chat, with @ to hand a message to a device's main Agent.

Nobody creates this group: its members are exactly the friends whose Same owner relationship is active on BOTH sides
(same_owner.SameOwner.active) and not blocked. Anybody else is not in it, cannot post to it and never sees it.

A group message comes from the owner's paired phone (signed `fr_gsay`, PROTOCOL §17.7) or from the main Agent
(`agentj friends say …`). Then, per member:

- **@-mentioned** (`@Mac M1`, several names, `@all` / `@所有设备`): the message travels as an ordinary friend message (`pmsg`)
  with a `grp` field, so it takes the existing Same owner delivery path unchanged — P410 chain ID / hops / per-friend rate /
  lease, the receiver's 「先压着等我批」 switch, the inbound secret filter, Stop everything, and delivery to the receiver's
  **main Agent** as same-owner request data (provenance kind `same_owner`: never the owner, never an approval). The only
  difference is the label: 「来自本人设备 X（「我的设备」群）」.
- **Not mentioned**: a `grp` record (app message `{"t": "grp", …}`) — it shows in that device's group history and wakes no
  Agent, starts no turn.

The mentioned device's main Agent answers with the usual `agentj friends tell <device> --reply-to <message-id> <result>`. The
host sees that the message it answers came from the group: the reply carries `grp.re` (the group message it answers), is
recorded in the group here, and a `grp` record of it goes to every other member. On the sender's side a reply to a message the
owner typed on the phone stays in the group (the phone is told; no Agent turn); a reply to a message the main Agent sent with
`friends say` also reaches that Agent as the usual informational result, so it learns the outcome.

The history is this host's own file `peer/my-devices.json` (atomic writes under the friends lock, newest 500). Every text is
checked like a friend message both ways (outbound filter before sending; inbound filter on arrival — a hit is kept as
「（疑似密钥，已拦下）」, never shown). Records from a member are limited to 60 a minute.
"""
from __future__ import annotations

import re
import secrets

from . import peer_guard, wire
from .text import clean

FILE = "my-devices.json"
KEEP = 500
RECORDS_PER_MINUTE = 60
_HEX16 = re.compile(r"[0-9a-f]{16}")
ALL_WORDS = ("all", "everyone", "所有设备", "全部设备", "全部", "所有人")
BY = ("phone", "agent")
MAX_TO = 32

T = {
    "zh": {"label": "来自本人设备 {name}（「我的设备」群）", "replied": "「我的设备」群：{name} 回复了，打开「好友」→「我的设备」查看。",
           "note": "这条来自「我的设备」群（主人自己的设备之间的群聊），点名了你这台。回复照常用下面的命令，结果会回到群里，所有设备都能看到。",
           "withheld": "（疑似密钥，已拦下，原文没有保存）"},
    "en": {"label": "From your own device {name} (My devices group)",
           "replied": "My devices group: {name} replied. Open Friends → My devices to read it.",
           "note": "This came from the My devices group (a group chat between the owner's own devices) and mentions this device. "
                   "Reply with the command below as usual; the result goes back to the group for every device.",
           "withheld": "(looked like a secret; stopped, not kept)"},
}


def _word(c: str) -> bool:
    return c.isascii() and (c.isalnum() or c in "_.")


def mentions(text: str, members: list[tuple[str, str]]) -> tuple[list[str], bool]:
    """`@Mac M1 跑测试 @Huawei` → ([ids], all?). members = [(id, name)]. Longest names first, case-insensitive; an Agent ID
    works too; `@all` / `@所有设备` means every member. An @ right after a Latin letter or digit (an e-mail address) is not a
    mention; anything after @ that names no member is just text."""
    low = (text or "").casefold()
    names = [(n.casefold(), fid) for fid, n in members if n] + [(fid.casefold(), fid) for fid, _ in members]
    names += [(w.casefold(), "*") for w in ALL_WORDS]
    names.sort(key=lambda x: -len(x[0]))
    found, every = [], False
    for i in [m.start() for m in re.finditer("@", low)]:
        if i > 0 and _word(low[i - 1]):
            continue
        for name, fid in names:
            end = i + 1 + len(name)
            if low.startswith(name, i + 1) and not (end < len(low) and _word(low[end]) and _word(name[-1])):
                if fid == "*":
                    every = True
                elif fid not in found:
                    found.append(fid)
                break
    return ([fid for fid, _ in members] if every else found), every


class OwnerGroup:
    def __init__(self, svc):
        self.svc = svc

    # ------------------------------------------------------------ membership and the file
    @property
    def tx(self) -> dict:
        return T[self.svc.lang]

    def members(self) -> list[str]:
        s = self.svc.store
        if not s.on:
            return []
        return [fid for fid in s.friends() if not s.is_blocked(fid) and self.svc.same_owner.active(fid)]

    def _read(self) -> dict:
        d = self.svc.store._read(FILE, {})
        d.setdefault("items", [])
        return d

    def _add(self, item: dict, unread: bool = False) -> bool:
        """Append one item (dedupe by origin + gid). False when it was already there."""
        with self.svc.store._edit(FILE, {}) as d:
            items = d.setdefault("items", [])
            if any(x.get("gid") == item["gid"] and x.get("from") == item["from"] for x in items[-KEEP:]):
                return False
            items.append(item)
            d["items"] = items[-KEEP:]
            if unread:
                d["unread"] = int(d.get("unread") or 0) + 1
        return True

    def _find(self, **kw) -> dict | None:
        for x in reversed(self._read()["items"]):
            if all(x.get(k) == v for k, v in kw.items()):
                return x
        return None

    def _item(self, gid, sender, by, text, to, re_=None, mid=None, kind="msg") -> dict:
        return {"gid": gid, "from": sender, "by": by, "text": text, "to": list(to), "re": re_, "mid": mid, "kind": kind,
                "ts": int(self.svc.clock() * 1000)}

    def summary(self) -> dict:
        """What fr_list carries for the 「我的设备」 entry."""
        d = self._read()
        last = d["items"][-1] if d["items"] else None
        return {"n": len(self.members()), "unread": int(d.get("unread") or 0),
                "last": {"ts": last["ts"], "text": clean(str(last.get("text") or ""), 120)} if last else None}

    def log_view(self, before=None, mark_read=True) -> dict:
        """fr_glog / `agentj friends devices`: members and the newest 50 messages (oldest first)."""
        svc = self.svc
        me = svc.keys.id if svc.keys else ""
        names = {fid: svc.store.name_of(fid) for fid in self.members()}
        items = [x for x in self._read()["items"] if before is None or x.get("ts", 0) < before]
        more = len(items) > 50
        items = items[-50:]
        if mark_read and before is None:
            with svc.store._edit(FILE, {}) as d:
                d["unread"] = 0

        def name(i):
            return svc.st.agent_name() or "Agent J" if i == me else names.get(i) or svc.store.name_of(i) or i
        out = [{"gid": x["gid"], "from": x["from"], "self": x["from"] == me, "name": name(x["from"]), "by": x.get("by"),
                "text": x.get("text", ""), "to": [{"id": i, "name": name(i)} for i in x.get("to", [])], "re": x.get("re"),
                "kind": x.get("kind", "msg"), "ts": x.get("ts", 0), "mid": x.get("mid")} for x in items]
        return {"me": {"id": me, "name": svc.st.agent_name() or "Agent J"},
                "members": [{"id": fid, "name": n} for fid, n in names.items()], "items": out, "more": more}

    # ------------------------------------------------------------ sending (phone or main Agent)
    async def say(self, text, by="agent") -> dict:
        svc = self.svc
        if by not in BY or not isinstance(text, str) or not text.strip() or wire.text_problem(text, wire.MAX_TEXT_P33):
            return {"ok": False, "why": "bad_text"}
        if svc.host.stopped():
            return {"ok": False, "why": "stopped"}
        refusal = svc.seat_refusal()
        if refusal:
            return {"ok": False, "why": "seat", "message": refusal}
        if not svc.store.on:
            return {"ok": False, "why": "off"}
        members = self.members()
        if not members:
            return {"ok": False, "why": "no_devices"}
        v = await svc._outbound(text)
        if v.ok:
            v = peer_guard.inbound(text)      # what every receiver would refuse anyway
        if not v.ok:
            svc.log("peer_group", result="outbound_gate", reason=v.rule)
            return {"ok": False, "why": "gate", "rule": v.rule}
        if svc.net is None:
            svc._ensure_net(run=False)
        members = [fid for fid in members if fid in self.members()]      # re-check after the awaits
        to, every = mentions(text, [(fid, svc.store.name_of(fid)) for fid in members])
        gid = secrets.token_hex(8)
        me = svc.keys.id
        self._add(self._item(gid, me, by, text, to))
        sent = []
        for fid in members:
            if fid in to:
                grp = {"gid": gid, "to": to, "by": by, "re": None}
                mid = svc._send_pmsg(fid, text, None, auto=False, extra={"grp": grp}, fresh=by == "phone")
                why = None if mid else (getattr(svc, "last_relay_refusal", None) or "not_sent")
                sent.append({"id": fid, "name": svc.store.name_of(fid), "task": True, "mid": mid, "ok": bool(mid),
                             **({"why": why} if why else {})})
                if mid:
                    svc.store.auto_round(fid, reset=True)
            else:
                ok = svc._send(fid, {"t": "grp", "gid": gid, "to": to, "by": by, "text": text,
                                     "ts": int(svc.clock() * 1000), "re": None})
                sent.append({"id": fid, "name": svc.store.name_of(fid), "task": False, "ok": ok})
        svc.log("peer_group", result="sent", by=by, tasks=sum(1 for x in sent if x["task"] and x["ok"]), n=len(sent))
        svc.changed(None)
        return {"ok": True, "gid": gid, "all": every, "devices": sent}

    async def write(self, target: dict, device=None) -> tuple[bool, str | None, str | None]:
        """The phone's signed `fr_gsay` (serve already checked the signature) → peer_service.write's answer shape."""
        res = await self.say(target.get("text"), by="phone")
        return res["ok"], None if res["ok"] else res.get("why"), None

    # ------------------------------------------------------------ replies by this host's main Agent
    def reply_extra(self, fid: str, reply_to) -> dict | None:
        """`friends tell <device> --reply-to <mid>`: the task came from the group → the `grp` field of the reply."""
        if not isinstance(reply_to, str):
            return None
        task = self._find(**{"from": fid, "mid": reply_to})
        if not task:
            return None
        return {"grp": {"gid": secrets.token_hex(8), "to": [fid], "by": "agent", "re": task["gid"]}}

    def after_reply(self, fid: str, text: str, extra: dict) -> None:
        """Record this host's reply in the group and send a record of it to every other member."""
        svc, g = self.svc, extra["grp"]
        me = svc.keys.id
        self._add(self._item(g["gid"], me, "agent", text, g["to"], re_=g["re"], kind="reply"))
        for other in self.members():
            if other != fid:
                svc._send(other, {"t": "grp", "gid": g["gid"], "to": g["to"], "by": "agent", "text": text,
                                  "ts": int(svc.clock() * 1000), "re": g["re"]})
        svc.changed(None)

    # ------------------------------------------------------------ receiving
    @staticmethod
    def _grp_ok(g) -> bool:
        return (isinstance(g, dict) and isinstance(g.get("gid"), str) and _HEX16.fullmatch(g["gid"]) is not None
                and isinstance(g.get("to"), list) and len(g["to"]) <= MAX_TO
                and all(isinstance(x, str) and 0 < len(x) <= 64 for x in g["to"]) and g.get("by") in BY
                and (g.get("re") is None or (isinstance(g["re"], str) and _HEX16.fullmatch(g["re"]) is not None)))

    def on_task(self, fid: str, m: dict) -> bool:
        """A same-owner pmsg (already through P410 accept, limits, Stop everything and the inbound filter) that carries `grp`.
        Records it. True = handled here (a group reply to something the owner typed on the phone: no Agent turn)."""
        g = m.get("grp")
        if g is None or not self._grp_ok(g):
            m.pop("grp", None)                # malformed: an ordinary same-owner message, nothing in the group
            return False
        svc = self.svc
        is_reply = bool(m.get("irt") or m.get("end"))
        self._add(self._item(g["gid"], fid, g["by"], m["text"], g["to"], re_=g.get("re"), mid=m["mid"],
                             kind="reply" if is_reply else "msg"), unread=True)
        svc.changed(None)
        if not is_reply:
            return False
        asked = self._find(gid=g.get("re"), **{"from": svc.keys.id}) if g.get("re") else None
        if asked and asked.get("by") == "agent":
            return False                      # the main Agent asked with `friends say`: it gets the usual result too
        svc._seen_put((fid, m["mid"]), "got")
        svc._send(fid, {"t": "pack", "mid": m["mid"], "s": "got"})
        svc.store.hist_set_status(fid, m["mid"], "got")
        svc.notice(self.tx["replied"].format(name=svc.store.name_of(fid)))
        svc.log("peer_group", result="reply_recorded")
        return True

    def on_record(self, fid: str, obj: dict) -> None:
        """`{"t": "grp", …}` from a member: history only — no Agent, no turn, no card."""
        svc = self.svc
        if fid not in self.members() or not self._grp_ok(obj) or not isinstance(obj.get("text"), str) \
                or wire.text_problem(obj["text"], wire.MAX_TEXT_P33):
            return
        now = svc.clock()
        with svc.store._edit(FILE, {}) as d:
            rate = d.setdefault("rate", {})
            times = [t for t in rate.get(fid, []) if t > now - 60]
            if len(times) >= RECORDS_PER_MINUTE:
                svc.log("peer_group", result="limited")
                return
            rate[fid] = times + [now]
        text = obj["text"] if peer_guard.inbound(obj["text"]).ok else self.tx["withheld"]
        if self._add(self._item(obj["gid"], fid, obj["by"], text, obj["to"], re_=obj.get("re"),
                                kind="reply" if obj.get("re") else "msg"), unread=True):
            svc.log("peer_group", result="recorded")
            svc.changed(None)

    # ------------------------------------------------------------ how the main Agent sees a group task (same_owner.forward)
    def label(self, name: str) -> str:
        return self.tx["label"].format(name=name)

    def note(self) -> str:
        return self.tx["note"]
