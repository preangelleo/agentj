"""F32 (P73, ADR-A180, PROTOCOL §18): the outbound secret pickup card — `agentj secret send`.

The owner asks their own Agent for something that holds a secret (a Shadowrocket `ss://` link, a proxy YAML with a password).
Ordinary replies keep redacting it and Telegram / group chats / friends / the relay keep refusing it; this is the one way out,
and it goes only to the owner's own paired devices:

- The Agent runs `agentj secret send --name … (--file PATH | --value-from env:NAME|file:PATH)`. The CLI reads the value itself
  (with the Agent's own permissions — the bridge never reads more than the session could) and hands it to `serve` over the
  same-user `agentperm/elevate.sock`. Its output is the card's state only, never the value.
- `serve` keeps the value in memory (a bytearray, wiped when the card ends) and shows every ready paired session a card that
  carries NO value: name, purpose, text/file, file name, size, time left, a one-time nonce and — for a device whose record holds a
  passkey (F20) — that device's own credential id (`fa`).
- The phone opens it with Face ID: a WebAuthn assertion (UP + UV) over `passkey.elevate_challenge(channel, device, id,
  "secret_out", n, shown digest)` — the F17 verifier, its own kind so an assertion for one never fits the other. Only then does
  the value leave the host, to THAT session only, inside the Noise session (the relay sees ciphertext). A device without a
  passkey cannot open it (it is told to set Face ID up first, `no_passkey`); there is no password-only fallback.
- One pickup: after delivering, the card ends everywhere (`secret_out_done` picked + time), the value is wiped, history gets one
  `sys` line 「已领取「name」 HH:MM」. Default 10 minutes (30–600 s), then it expires and is wiped. 「不要了」 declines it.
  Stop everything voids open cards. Nothing of the value is logged: `elevate.log` gets who / when / which card (+ size).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import pathlib
import secrets
import sys
import time

from . import elevate, passkey, wire

CTX = "agentjarvis-secret-out-v1"
KIND = "secret_out"
TTL_DEFAULT, TTL_MIN, TTL_MAX = 600, 30, 600
MAX_TEXT = 64 * 1024            # bytes of a text value
MAX_FILE = 256 * 1024           # bytes of a file
MAX_OPEN = 8
NAME_MAX, PURPOSE_MAX, FILE_NAME_MAX = 80, 300, 128


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def shown_fields(c: dict) -> list[str]:
    """What the phone shows before Face ID, in a fixed order: name, purpose, text|file, file name, size (bytes)."""
    return [c["name"], c.get("purpose") or "", c["kind"], c.get("filename") or "", str(c["size"])]


def shown_digest(fields: list[str]) -> str:
    """hex SHA-256 of the context, the kind, then one line per shown field `sha256(field)` (web/public/js/secretout.js)."""
    return _h("\n".join([CTX, KIND] + [_h(f) for f in fields]))


# ---------------------------------------------------------------- the Agent's request
def norm(req: dict) -> dict:
    """{"t":"secret_out","name","purpose"?,"kind":"text"|"file","filename"?,"value"?|"data"?(b64),"ttl"?} → a card (value as a
    bytearray). Raises elevate.Refused."""
    def text(v, limit, field, required):
        if v in (None, ""):
            if required:
                raise elevate.Refused("shape", f"{field} is required")
            return ""
        if not isinstance(v, str) or wire.text_problem(v, limit) or "\n" in v:
            raise elevate.Refused("shape", f"{field}: one line up to {limit} characters")
        return v
    name = text(req.get("name"), NAME_MAX, "name", True)
    purpose = text(req.get("purpose"), PURPOSE_MAX, "purpose", False)
    kind = req.get("kind")
    if kind == "text":
        v = req.get("value")
        if not isinstance(v, str) or not v:
            raise elevate.Refused("shape", "an empty value")
        value = bytearray(v.encode("utf-8"))
        if len(value) > MAX_TEXT:
            elevate.wipe(value)
            raise elevate.Refused("too_big", f"text up to {MAX_TEXT // 1024} KiB; send a file instead")
        filename = ""
    elif kind == "file":
        filename = text(req.get("filename"), FILE_NAME_MAX, "filename", True)
        if "/" in filename or "\\" in filename or filename in (".", ".."):
            raise elevate.Refused("shape", "filename: a plain file name")
        d = req.get("data")
        if not isinstance(d, str) or not d or len(d) > (MAX_FILE * 4) // 3 + 8:
            raise elevate.Refused("too_big" if isinstance(d, str) and d else "shape", f"a file up to {MAX_FILE // 1024} KiB")
        try:
            value = bytearray(base64.b64decode(d, validate=True))
        except ValueError:
            raise elevate.Refused("shape", "data: base64") from None
        if not value or len(value) > MAX_FILE:
            raise elevate.Refused("too_big", f"a file up to {MAX_FILE // 1024} KiB")
    else:
        raise elevate.Refused("shape", "kind: text | file")
    ttl = req.get("ttl", TTL_DEFAULT)
    if ttl is None:
        ttl = TTL_DEFAULT
    if type(ttl) is not int or not TTL_MIN <= ttl <= TTL_MAX:
        elevate.wipe(value)
        raise elevate.Refused("shape", f"ttl: {TTL_MIN}–{TTL_MAX} s")
    return {"name": name, "purpose": purpose, "kind": kind, "filename": filename, "size": len(value), "value": value, "ttl": ttl}


# ---------------------------------------------------------------- serve side
class Outbox:
    """Owned by elevate.Elevator (same socket, same phone dispatch)."""

    def __init__(self, elev):
        self.elev = elev
        self.host = elev.host
        self.st = elev.st
        self.cards: dict[str, dict] = {}

    # ---------------------------------------------------------- audit / results (never the value)
    def _audit(self, c: dict, result: str, **kw) -> None:
        rec = {"ts": int(time.time()), "kind": KIND, "id": c["id"], "name": c["name"], "size": c["size"],
               "shown_sha256": c["digest"], "result": result, "channel": self.host.channel}
        rec.update({k: v for k, v in kw.items() if v is not None})
        self.st.append_private(elevate.log_path(self.st), json.dumps(rec, ensure_ascii=False))

    def _remember(self, c: dict, result: str, **kw) -> None:
        elevate.remember(self.st, c["id"], {"kind": KIND, "name": c["name"], "result": result, **kw})

    # ---------------------------------------------------------- the Agent asks
    async def send(self, req: dict) -> dict:
        try:
            c = norm(req)
        except elevate.Refused as e:
            return {"result": "refused", "why": e.why, "detail": e.detail}
        if self.host.stopped():
            elevate.wipe(c["value"])
            return {"result": "stopped"}
        approvers = self.host._approvers()
        if not approvers:
            elevate.wipe(c["value"])
            return {"result": "no_device"}
        if len(self.cards) >= MAX_OPEN:
            elevate.wipe(c["value"])
            return {"result": "busy"}
        c["id"] = secrets.token_hex(16)
        c["nonce"] = secrets.token_hex(16)
        c["digest"] = shown_digest(shown_fields(c))
        c["deadline"] = time.monotonic() + c["ttl"]
        c["until"] = int(time.time() + c["ttl"])
        c["timer"] = asyncio.get_running_loop().call_later(c["ttl"], lambda: self._end(c, "expired"))
        self.cards[c["id"]] = c
        faceid = sum(1 for d in approvers if self.st.passkey_of_device(d))
        self._audit(c, "sent")
        self._remember(c, "pending", until=c["until"], size=c["size"], type=c["kind"])
        self.st.log("sout_card", id=c["id"], kind=c["kind"])
        self.host.push_notify("ask")
        await self.host._send_ready(lambda s: self.card_msg(c, s))
        return {"result": "sent", "id": c["id"], "ttl": c["ttl"], "devices": len(approvers), "faceid": faceid,
                "size": c["size"], "type": c["kind"]}

    def card_msg(self, c: dict, s=None) -> dict:
        m = {"t": "secret_out", "id": c["id"], "name": c["name"], "purpose": c["purpose"], "kind": c["kind"],
             "filename": c["filename"], "size": c["size"], "n": c["nonce"],
             "ttl": max(0, int(c["deadline"] - time.monotonic()))}
        pk = self.st.passkey_of_device(s.device) if s is not None and getattr(s, "device", None) else None
        if pk:
            m["fa"] = pk["id"]        # this device's own credential id only
        return m

    async def on_ready(self, s) -> None:
        for c in list(self.cards.values()):
            await self.host.send_app(s, self.card_msg(c, s))

    # ---------------------------------------------------------- the phone answers
    async def on_phone(self, s, obj: dict) -> None:
        rid = obj.get("id")
        c = self.cards.get(rid) if isinstance(rid, str) and elevate._ID.fullmatch(rid) else None
        if c is None or time.monotonic() > c["deadline"]:
            self.st.log("sout_refused", id=rid if isinstance(rid, str) else None, device=s.device, reason="unknown")
            return await self.host.send_app(s, {"t": "secret_out_err", "id": rid if isinstance(rid, str) else "", "why": "gone"})
        if not self.st.sign_key(s.device):          # only an approved device with an approval key (same as F17)
            self.st.log("sout_refused", id=rid, device=s.device, reason="no_key")
            return
        if obj["t"] == "secret_out_decline":
            return self._end(c, "declined", device=s.device)
        why = self._check(s, c, obj)
        if why:
            self.st.log("sout_refused", id=rid, device=s.device, reason=why)
            self._audit(c, "refused", reason=why, device=s.device)
            return await self.host.send_app(s, {"t": "secret_out_err", "id": rid,
                                                "why": "no_passkey" if why == "no_passkey" else "passkey"})
        nonce, c["nonce"] = c["nonce"], None        # one attempt per nonce while this delivery is under way
        try:
            v = c["value"]
            msg = {"t": "secret_out_val", "id": rid, "kind": c["kind"], "filename": c["filename"]}
            if c["kind"] == "text":
                msg["value"] = v.decode("utf-8")
            else:
                msg["data"] = base64.b64encode(bytes(v)).decode()
            ok = await self.host.send_app(s, msg)
        finally:
            msg = None  # noqa: F841 — drop the reference; str cannot be wiped
        if not ok:                                   # not delivered (closed, too big for an old session): stays open
            c["nonce"] = secrets.token_hex(16)
            self.st.log("sout_refused", id=rid, device=s.device, reason="send")
            await self.host.send_app(s, {"t": "secret_out_err", "id": rid, "why": "send"})
            await self.host._send_ready(lambda x: self.card_msg(c, x))
            return
        del nonce
        self._end(c, "picked", device=s.device, passkey="uv")

    def _check(self, s, c: dict, obj: dict) -> str | None:
        if obj.get("n") != c["nonce"] or not c["nonce"]:
            return "replay"
        pk = self.st.passkey_of_device(s.device)
        if not pk:
            return "no_passkey"
        if "fa" not in obj:
            return "passkey_missing"
        try:
            f = passkey.approval_fields(obj["fa"])
            if f["id"] != pk["id"]:
                raise passkey.PasskeyError("bad", "credential")
            ch = passkey.elevate_challenge(self.host.channel, s.device, c["id"], KIND, c["nonce"], c["digest"])
            count = passkey.verify_assertion(pk, f, ch)
        except (passkey.PasskeyError, ValueError) as e:
            self.st.log("sout_passkey", id=c["id"], device=s.device, reason=getattr(e, "detail", "bad"))
            return "passkey_bad"
        self.st.passkey_count(s.device, pk["id"], count)
        return None

    # ---------------------------------------------------------- the end of a card
    def _end(self, c: dict, result: str, device: str | None = None, passkey: str | None = None) -> None:
        if self.cards.pop(c["id"], None) is None:
            return
        with contextlib.suppress(Exception):
            c["timer"].cancel()
        elevate.wipe(c["value"])
        c["value"] = bytearray()
        at = int(time.time())
        self._audit(c, result, device=device, passkey=passkey)
        self._remember(c, result, until=None, **({"device": device} if device else {}))
        self.st.log("sout_done", id=c["id"], result=result, device=device)
        done = {"t": "secret_out_done", "id": c["id"], "result": result, "at": at * 1000}
        try:
            asyncio.get_running_loop().create_task(self.host._send_ready(lambda s: done))
        except RuntimeError:                         # no loop (a test calling end_all directly): nobody to tell
            pass
        if result == "picked":
            lang = getattr(self.host, "lang", "zh")
            hm = time.strftime("%H:%M", time.localtime(at))
            note = (f"已领取「{c['name']}」 {hm}（密钥领取卡，值没有留在聊天里）" if lang != "en"
                    else f"Picked up “{c['name']}” at {hm} (secret pickup card; the value is not kept in the chat)")
            with contextlib.suppress(Exception):
                self.host.hist_add({"k": "sys", "text": ""}, note, "done")

    def end_all(self, result: str) -> None:
        for c in list(self.cards.values()):
            self._end(c, result)


# ---------------------------------------------------------------- the CLI side (runs as the Agent)
def _read_value(a) -> dict:
    """--file / --value-from → the request fields. Raises SystemExit(2) with a message (never the value)."""
    if bool(a.file) == bool(a.value_from):
        raise SystemExit("用法 / usage: agentj secret send --name '<名字>' (--file <路径> | --value-from env:<变量>|file:<路径>)")
    if a.file:
        path = pathlib.Path(os.path.expanduser(a.file))
        try:
            st = path.stat()
            if not path.is_file():
                raise SystemExit(f"不是普通文件 / not a regular file: {path}")
            if st.st_size > MAX_FILE:
                raise SystemExit(f"文件太大（上限 {MAX_FILE // 1024} KiB）/ file too big (≤ {MAX_FILE // 1024} KiB)")
            data = path.read_bytes()
        except OSError as e:
            raise SystemExit(f"读不了这个文件 / cannot read the file: {e.strerror}") from None
        return {"kind": "file", "filename": path.name[:FILE_NAME_MAX], "data": base64.b64encode(data).decode()}
    src = a.value_from
    if src.startswith("env:"):
        v = os.environ.get(src[4:])
        if not v:
            raise SystemExit(f"环境变量 {src[4:]} 没有值 / {src[4:]} is empty or unset")
    elif src.startswith("file:"):
        try:
            v = pathlib.Path(os.path.expanduser(src[5:])).read_text("utf-8").rstrip("\r\n")
        except (OSError, UnicodeDecodeError):
            raise SystemExit("读不了这个文件（要 UTF-8 文本）/ cannot read the file as UTF-8 text") from None
        if not v:
            raise SystemExit("文件是空的 / the file is empty")
    else:
        raise SystemExit("--value-from 只认 env:<变量名> 或 file:<路径> / --value-from takes env:<NAME> or file:<path>")
    return {"kind": "text", "value": v}


MSG = {
    "sent": "已发到主人的手机：「{name}」，{secs} 秒内可领（Face ID 验证后才显示）。不要在回复里重复这个值；告诉主人去手机上领取。"
            "/ Sent to the owner's phone; it can be picked up for {secs} s after Face ID. Do not repeat the value in your reply.",
    "picked": "主人已在手机上领取。/ The owner picked it up on the phone.",
    "expired": "没人领取，已过期并从电脑内存里清除。需要的话重新发一张。/ Not picked up; it expired and was wiped. Send a new one if needed.",
    "declined": "主人在手机上点了「不要了」，卡片已作废。/ The owner declined it on the phone; the card is void.",
    "gone": "Agent J 停止或重启，卡片作废（值已清除）。/ Agent J stopped or restarted; the card is void.",
    "stopped": "已急停，卡片作废（值已清除）。/ Everything was stopped; the card is void.",
    "no_device": "没有已配对的手机。/ No paired phone.",
    "busy": "已有太多张领取卡没被领取。/ Too many pickup cards are already open.",
    "unavailable": "Agent J 没在运行（agentj service status）。/ Agent J is not running.",
    "refused": "请求不合格：{detail}。/ Request refused: {detail}.",
    "pending": "还没被领取（还剩 {secs} 秒）。/ Not picked up yet ({secs} s left).",
    "unknown": "没有这张卡的记录。/ No record of that card.",
}


def _say(res: dict) -> str:
    return MSG.get(res.get("result"), str(res.get("result"))).format(
        name=res.get("name", ""), secs=res.get("secs", res.get("ttl", "")), detail=res.get("detail") or res.get("why") or "")


def report(res: dict, as_json: bool) -> int:
    """Print a pickup card's state → exit: 0 sent / picked · 75 pending · 125 otherwise."""
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    elif res.get("result") in ("sent", "picked", "pending"):
        print(f"SECRET_OUT: {res.get('result')} {res.get('id', '')} — {_say(res)}")
    else:
        print(f"SECRET_OUT: {res.get('result')} — {_say(res)}", file=sys.stderr)
    return {"sent": 0, "picked": 0, "pending": elevate.EXIT_PENDING}.get(res.get("result"), elevate.EXIT_CARD)


def cmd_send(a) -> int:
    from .state import State
    if not a.name:
        raise SystemExit("--name is required (shown on the phone)")
    req = {"t": "secret_out", "name": a.name, "purpose": a.purpose or "", "ttl": a.ttl, **_read_value(a)}
    st = State()
    res = elevate.client_request(st, req, 30)
    req = None  # noqa: F841 — drop the value
    if res.get("result") == "sent":
        res["name"] = a.name
        if not a.json:
            kind = "文本 / text" if res.get("type") == "text" else f"文件 / file {os.path.basename(a.file or '')}"
            print(f"SECRET_OUT: sent {res['id']} · 「{a.name}」 · {kind} · {res.get('size')} bytes · {res['ttl']} s")
            print(f"  {_say(res)}")
            print(f"  已配对手机 {res.get('devices')} 台，其中 {res.get('faceid')} 台设了 Face ID / {res.get('devices')} paired, "
                  f"{res.get('faceid')} with Face ID" + ("" if res.get("faceid") else
                  "。没设 Face ID 的手机要先在卡片上点「设置 Face ID」才能领取 / a phone without Face ID must set it up on the card first"))
            print(f"  领取情况 / status: agentj secret result {res['id']}  (--wait)")
            if not a.wait:
                return 0
        if a.wait:
            res = elevate.client_request(st, {"t": "secret_result", "id": res["id"], "wait": True})
            return report(res, a.json)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    return report(res, a.json)
