"""P547 (PROTOCOL "P547 §B", ADR-P547): move this host's seat to another Agent J host from a remote (or the terminal).

This host (H1) makes the transfer code itself (`ajh_` + 43 base64url, 256 bit — `ajh_` = host-issued; the account page only
ever mints `ajt_`) and sends only its SHA-256 to the Dashboard,
which keeps H1 bound until the new host (H2) redeems the code (then H1 is unbound in the same write). The code is kept only
in <state>/seat/transfer.json (0600) until the move is settled, and shown only to the asking remote / the terminal.

Two HMACs under the code, which the Dashboard never sees, keep it out of the trust path:
- handover = {b, mac}: b = b64url(nonce ‖ AES-256-GCM(HKDF-SHA256(code, info "agentj-seat-handover-key-v1"), nonce,
  canonical JSON {"v":1,"from":channel,"devices":[{"pub","sign","name","pk"?}]})) — the Dashboard stores it opaque and cannot
  read the remotes' names — and mac = HMAC-SHA256(code, "agentj-seat-handover-v1\\n" + b). H2 adds those remotes (source
  "transfer") ONLY when the code it bound with is host-issued (`ajh_`) AND the mac verifies AND b decrypts (apply_handover) —
  the one way a Dashboard answer can lead to an allowlist entry. A handover next to an account-page `ajt_` code is ignored:
  the Dashboard minted that code itself, so it could have made a valid mac (security review B1).
- arrival = {x25519, relay, mac}: H2's static key and "<relay URL>#c=<H2 channel>", mac = HMAC-SHA256(code,
  "agentj-seat-arrival-v1\\n" + channel + "\\n" + x25519 + "\\n" + relay URL). H1 tells its remotes `host_moved` ONLY
  after this verifies (Desk.tick, every TICK s while issued).
Issuing needs, on a remote: a signed control + that remote's own Face ID (two steps, like approval_set); at the terminal: the
local administrator password (`sudo -k`, a real terminal; tests replace `admin_check` in-process — there is no environment
switch). Cancelling only narrows: signed, no Face ID."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse

from . import cloud, controls, passkey, wire
from .text import clean_label, pick

TYPES = ("seat_transfer", "seat_transfer_cancel", "seat_xfer_state")
TTL = 24 * 3600
TICK = 30
HANDOVER_LABEL = "agentj-seat-handover-v1\n"
HANDOVER_KEY_INFO = b"agentj-seat-handover-key-v1"
PREFIX = "ajh_"         # host-issued: the only codes whose handover / arrival this host trusts (review B1)
ARRIVAL_LABEL = "agentj-seat-arrival-v1\n"
INSTALL_MD = "https://agentj.app/install.md"
MAX_BLOB = 12000        # plaintext; b64url(12 + 12000 + 16) ≤ 16 038 < the Dashboard's 16 384, and under cloud.MAX_ENVELOPE
MAX_B = 16384
_SLUG = re.compile(r"[a-z0-9-]{1,30}")


class TransferError(Exception):
    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


# ------------------------------------------------------------------ transfer.json
def path(st) -> pathlib.Path:
    return pathlib.Path(st.root) / "seat" / "transfer.json"


def read(st) -> dict:
    try:
        d = json.loads(path(st).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def write(st, d: dict) -> None:
    p = path(st)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.replace(tmp, p)


def view(st, now: float | None = None) -> dict:
    """What the Settings row shows: {"state": none | issued | expired | bound | moved | cancelled | revoked, "expires_at"}."""
    d, now = read(st), time.time() if now is None else now
    state = d.get("state") if d.get("state") in ("issued", "bound", "moved", "cancelled", "revoked", "expired") else "none"
    exp = d.get("expires_at") if type(d.get("expires_at")) is int else None
    if state == "issued" and (exp is None or exp <= now):
        state = "expired"
    return {"state": state, "expires_at": exp}


# ------------------------------------------------------------------ the code and its two HMACs
def new_token() -> str:
    return PREFIX + wire.b64u(secrets.token_bytes(32))


def host_issued(token) -> bool:
    """A code this kind of host made (`ajh_`): only its handover / arrival can be trusted — the Dashboard never knew it."""
    return cloud.seat_code_ok(token) and token.startswith(PREFIX)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mac(token: str, label: str, data: str) -> str:
    return wire.b64u(hmac.new(token.encode(), (label + data).encode(), hashlib.sha256).digest())


def mask(token: str) -> str:
    return token[:4] + "••••…" + token[-4:]


def handover_blob(st) -> str:
    """The remotes as public keys + names (+ the public part of each Face ID key), oldest pairing first, ≤ MAX_BLOB."""
    devs = sorted(((k, v) for k, v in st.devices().items() if isinstance(v, dict) and isinstance(v.get("pub"), str)
                   and isinstance(v.get("sk"), str)), key=lambda kv: kv[1].get("paired_at") or 0)[:10]
    rows = []
    for _, v in devs:
        row = {"pub": v["pub"], "sign": v["sk"], "name": clean_label(str(v.get("name", "")))[:64] or "remote"}
        pk = v.get("pk")
        if isinstance(pk, dict) and all(isinstance(pk.get(k), str) for k in ("id", "spki", "rp")) and pk.get("alg") in passkey.ALGS:
            row["pk"] = {"id": pk["id"], "alg": pk["alg"], "spki": pk["spki"], "rp": pk["rp"]}
        rows.append(row)

    def dump(r):
        return json.dumps({"v": 1, "from": cloud.channel_of(st), "devices": r}, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
    b = dump(rows)
    if len(b) > MAX_BLOB:
        rows = [{k: x[k] for k in ("pub", "sign", "name")} for x in rows]
        while rows and len(b := dump(rows)) > MAX_BLOB:
            rows.pop()
    return b


def _aead(token: str):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HANDOVER_KEY_INFO).derive(token.encode()))


def seal(token: str, plain: str) -> str:
    nonce = secrets.token_bytes(12)
    return wire.b64u(nonce + _aead(token).encrypt(nonce, plain.encode(), HANDOVER_LABEL.encode()))


def unseal(token: str, b: str) -> str | None:
    from cryptography.exceptions import InvalidTag
    try:
        raw = wire.unb64u(b)
        if len(raw) < 12 + 16:
            return None
        return _aead(token).decrypt(raw[:12], raw[12:], HANDOVER_LABEL.encode()).decode()
    except (ValueError, InvalidTag, UnicodeDecodeError):
        return None


def handover(st, token: str) -> dict:
    b = seal(token, handover_blob(st))
    return {"b": b, "mac": mac(token, HANDOVER_LABEL, b)}


def _key32(v) -> bytes | None:
    try:
        k = wire.unb64u(v) if isinstance(v, str) and len(v) == 43 else b""
    except ValueError:
        return None
    return k if len(k) == 32 else None


def verify_handover(token: str, ho) -> list[dict] | None:
    """The remotes of a handover whose mac verifies under `token` (strictly shaped), else None."""
    if not host_issued(token) or not isinstance(ho, dict) or not isinstance(ho.get("b"), str) \
            or not isinstance(ho.get("mac"), str) or len(ho["b"]) > MAX_B:
        return None
    if not hmac.compare_digest(mac(token, HANDOVER_LABEL, ho["b"]).encode(), ho["mac"].encode()):
        return None
    plain = unseal(token, ho["b"])
    try:
        d = json.loads(plain) if plain is not None else None
    except ValueError:
        return None
    devs = d.get("devices") if isinstance(d, dict) and d.get("v") == 1 else None
    if not isinstance(devs, list) or len(devs) > 10:
        return None
    out = []
    for x in devs:
        if not isinstance(x, dict) or not _key32(x.get("pub")) or not _key32(x.get("sign")) or not isinstance(x.get("name"), str):
            return None
        row = {"pub": _key32(x["pub"]), "sign": _key32(x["sign"]), "name": clean_label(x["name"])[:64] or "remote"}
        pk = x.get("pk")
        if isinstance(pk, dict):
            try:
                if not (isinstance(pk.get("id"), str) and len(pk["id"]) <= 1400 and isinstance(pk.get("rp"), str)
                        and len(pk["rp"]) <= 253 and isinstance(pk.get("spki"), str)):
                    raise passkey.PasskeyError("bad", "pk")
                passkey.load_spki(passkey.b64(pk["spki"], passkey.MAX_SPKI, "spki"), pk.get("alg"))
                row["pk"] = {"id": pk["id"], "alg": pk["alg"], "spki": pk["spki"], "rp": pk["rp"], "at": int(time.time())}
            except passkey.PasskeyError:
                pass                     # the remote still moves; its Face ID is set up again on the new host
        out.append(row)
    return out


def apply_handover(st, token: str, ho) -> int:
    """New host side, after a bound seat-bind with a host-issued code: add the handed-over remotes (source "transfer") — only
    when the code is host-issued (`ajh_`) and the mac verifies under it. → how many were added (0 otherwise)."""
    from .state import DeviceLimit
    if not host_issued(token):      # review B1: the Dashboard minted this code — a "valid" mac proves nothing
        st.log("seat_handover", result="ignored")
        return 0
    rows = verify_handover(token, ho)
    if not rows:
        st.log("seat_handover", result="refused" if rows is None else "empty")
        return 0
    n = 0
    for r in rows:
        try:
            st.pair_device(r["pub"], r["name"], r["sign"], source="transfer")
        except DeviceLimit:
            break
        if r.get("pk"):
            st.set_passkey(r["pub"], r["pk"])
        n += 1
    st.log("seat_handover", result="ok", count=n)
    return n


def arrival(st, token: str) -> dict:
    """This (new) host's arrival for the old one: its X25519 key, "<relay>#c=<channel>", the mac over both."""
    ch, relay, x = cloud.channel_of(st), str(st.config().get("relay", "")), wire.b64u(st.host_keypair().pub)
    return {"x25519": x, "relay": f"{relay}#c={ch}", "mac": mac(token, ARRIVAL_LABEL, f"{ch}\n{x}\n{relay}")}


def relay_ok(url) -> bool:
    """The relay a remote may be sent to (same rule as dashboard seat_transfer.ts): wss://, or ws:// only to exactly
    127.0.0.1 / localhost; a host name, no userinfo."""
    if not isinstance(url, str) or len(url) > 200 or any(c.isspace() or ord(c) < 0x20 for c in url):
        return False
    try:
        u = urllib.parse.urlsplit(url)
        host = u.hostname
        u.port                                  # noqa: B018 — a malformed port raises
    except ValueError:
        return False
    if not host or "@" in u.netloc or u.username is not None or u.password is not None:
        return False
    return u.scheme == "wss" or (u.scheme == "ws" and host in ("127.0.0.1", "localhost"))


def verify_arrival(token: str, a) -> dict | None:
    """→ {"relay", "channel", "hostPub"} for host_moved, only when the arrival's mac verifies under `token`."""
    if not isinstance(token, str) or not isinstance(a, dict) or not all(isinstance(a.get(k), str) for k in ("x25519", "relay", "mac")):
        return None
    relay, _, ch = a["relay"].rpartition("#c=")
    if not relay or not re.fullmatch(r"[A-Za-z0-9_-]{22}", ch) or not _key32(a["x25519"]) or not relay_ok(relay):
        return None
    if not hmac.compare_digest(mac(token, ARRIVAL_LABEL, f"{ch}\n{a['x25519']}\n{relay}").encode(), a["mac"].encode()):
        return None
    return {"relay": relay, "channel": ch, "hostPub": a["x25519"]}


def prompt(slug: str, token: str) -> tuple[str, str]:
    """The account page's sentence (dashboard sentenceZh / sentenceEn), valid 24 hours instead of 7 days."""
    slug = slug if isinstance(slug, str) and _SLUG.fullmatch(slug) else "—"
    zh = (f"请用 curl 把 {INSTALL_MD} 下载到本地，用读文件的工具完整读完，然后一步一步照着做（每一步开始前重读那一节），把 Agent J 装好。"
          f"这台Agent J 主机用的是 Agent J 账号「{slug}」的一个席位，设置码是：{token}（24 小时内有效，只能用一次；只在 agentj login "
          f"--seat-file 那一步用，别贴到别的地方）。需要我动手的时候跟我说。")
    en = (f"Please download {INSTALL_MD} with curl, read the whole file with your file-reading tool, then follow it step by step "
          f"(re-read each section before you do it) to set up Agent J. The Agent J host uses a seat on the Agent J account "
          f"\"{slug}\". The seat setup code is {token} (valid 24 hours, single use; use it only in the agentj login --seat-file "
          f"step, nowhere else). Tell me when you need me to do something.")
    return zh, en


# ------------------------------------------------------------------ the three Dashboard calls, with transfer.json
def issue(st, by: str, *, post=None, now=time.time) -> dict:
    """→ {"token", "expires_at", "slug", "prompt_zh", "prompt_en"}; raises TransferError(unbound | not_found | billing_busy |
    rate_limited | offline | error)."""
    link = cloud.read_cloud(st)
    if not link:
        raise TransferError("unbound")
    token = new_token()
    res = cloud.seat_transfer_issue(st, link["api"], token_hash(token), handover(st, token), post=post or cloud.post_json, now=now)
    if res["status"] != "issued":
        raise TransferError(res["status"])
    slug = res["tenant"]["slug"]
    with st.file_lock("seat_transfer"):
        write(st, {"v": 1, "state": "issued", "token": token, "setup_id": res["setup_id"], "expires_at": res["expires_at"],
                   "issued_at": int(now()), "by": clean_label(by)[:64], "slug": slug, "told": None})
    zh, en = prompt(slug, token)
    return {"token": token, "expires_at": res["expires_at"], "slug": slug, "prompt_zh": zh, "prompt_en": en}


def settle(st, state: str, **extra) -> dict:
    """A final state: the code is forgotten (nothing left to verify with it)."""
    with st.file_lock("seat_transfer"):
        d = read(st)
        d.pop("token", None)
        d.update(state=state, told=None, settled_at=int(time.time()), **extra)
        write(st, d)
    return d


def cancel(st, *, post=None, now=time.time) -> str:
    """→ cancelled | none | bound | a failure (unbound, offline, …)."""
    link = cloud.read_cloud(st)
    base = link["api"] if link else None
    if base is None:
        with contextlib.suppress(cloud.CloudError):
            base = cloud.api_url(st)
    if not base:
        return "unbound"
    res = cloud.seat_transfer_cancel(st, base, post=post or cloud.post_json, now=now)["status"]
    if res in ("cancelled", "none") and read(st).get("state") == "issued":
        settle(st, "cancelled")
    return res


def poll(st, *, post=None, now=time.time) -> str | None:
    """One status look while issued. → the new state when it changed (moved | bound | revoked | expired | cancelled)."""
    d = read(st)
    token = d.get("token")
    if d.get("state") != "issued" or not isinstance(token, str):
        return None
    link = cloud.read_cloud(st)
    try:
        base = link["api"] if link else cloud.api_url(st)
    except cloud.CloudError:
        return None
    res = cloud.seat_transfer_status(st, base, post=post or cloud.post_json, now=now)
    s = res.get("status")
    if s == "bound":
        to = verify_arrival(token, res.get("arrival"))
        if to:
            settle(st, "moved", moved={**to, "name": res.get("agent_name") or "", "at": int(now())})
            st.log("seat_transfer", result="moved")
            return "moved"
        # review minor 3: unverified — nothing the Dashboard said about where it went is repeated to the remotes
        settle(st, "bound")
        st.log("seat_transfer", result="bound_unverified")
        return "bound"
    if s in ("revoked", "expired") or (s == "none" and d.get("expires_at", 0) < now()):
        settle(st, "cancelled" if s == "none" else s)
        return s
    return None


# ------------------------------------------------------------------ serve side
class Desk:
    """Wired into serve.Host as `self.seat_xfer`."""

    def __init__(self, host):
        self.host = host
        self.wake: asyncio.Event | None = None
        self.busy = False

    def _ready(self):
        h = self.host
        return [x for x in list(h.sessions.values()) if x.state == "ready" and x.p33 and h.st.is_allowed(x.pub)]

    def moved_msg(self) -> dict | None:
        m = read(self.host.st).get("moved")
        if isinstance(m, dict) and all(isinstance(m.get(k), str) for k in ("relay", "channel", "hostPub")):
            return {"t": "host_moved", "relay": m["relay"], "channel": m["channel"], "hostPub": m["hostPub"],
                    "name": clean_label(str(m.get("name") or ""))[:64], "at": m["at"] if type(m.get("at")) is int else 0}
        return None

    async def on_ready(self, s) -> None:
        if s.p33:
            msg = await asyncio.to_thread(self.moved_msg)
            if msg:
                await self.host.send_app(s, msg)

    async def announce(self) -> None:
        """Tell every remote the state transfer.json is in, once per state (the file says what was already told)."""
        h = self.host
        d = await asyncio.to_thread(read, h.st)
        state = d.get("state")
        if not state or d.get("told") == state:
            return
        by, name = str(d.get("by") or ""), clean_label(str((d.get("moved") or {}).get("name") or ""))
        text = {
            "issued": pick(h.lang, f"席位转移码已由{f'遥控器「{by}」' if by else '这台Agent J 主机的终端'}签发，24 小时内有效。"
                                   "不是你本人？在 设置 → Agent 与遥控器 里点「撤销」。",
                           f"A seat transfer code was issued {f'from remote “{by}”' if by else 'at the terminal of this Agent J host'}, "
                           "valid 24 hours. Not you? Tap Revoke in Settings → Agent and remotes."),
            "cancelled": pick(h.lang, "席位转移码已作废，这台Agent J 主机的席位不变。",
                              "The seat transfer code was revoked; this Agent J host keeps its seat."),
            "revoked": pick(h.lang, "席位转移码已在账户页被收回，这台Agent J 主机的席位不变。",
                            "The seat transfer code was revoked on the account page; this Agent J host keeps its seat."),
            "moved": pick(h.lang, f"席位已转到新主机「{name}」，遥控器会自动切过去。",
                          f"The seat moved to the new host “{name}”; the remotes switch over automatically."),
            "bound": pick(h.lang, "席位转移码已被使用。如果不是你本人，请到账户页确认。",
                          "The seat transfer code was used. If that was not you, check your account page."),
        }.get(state)
        with h.st.file_lock("seat_transfer"):
            cur = read(h.st)
            if cur.get("state") == state:
                cur["told"] = state
                write(h.st, cur)
        st, msg = view(h.st), self.moved_msg() if state == "moved" else None
        for x in self._ready():
            if msg:
                await h.send_app(x, msg)
            await h.send_app(x, {"t": "seat_xfer_state", **st})
        if text:
            await h.broadcast(text, frm="notice")
            h.push_notify("security")

    async def from_terminal(self) -> None:
        """`agentj seat transfer` while serve runs: announce what the CLI wrote and watch it."""
        await self.announce()
        if self.wake:
            self.wake.set()

    async def tick(self) -> None:
        h = self.host
        if await asyncio.to_thread(poll, h.st):
            await self.announce()

    async def run(self) -> None:
        self.wake = asyncio.Event()
        while True:
            try:
                if (await asyncio.to_thread(read, self.host.st)).get("state") == "issued":
                    await self.tick()
            except Exception as e:  # noqa: BLE001 — the watcher never stops serve
                self.host.st.log("seat_transfer", result="watch_failed", exception_type=type(e).__name__)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.wake.wait(), TICK)
            self.wake.clear()

    # ------------------------------------------------------------ the phone
    async def on_phone(self, s, t: str, obj: dict) -> None:
        h = self.host
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        if t == "seat_xfer_state":
            return await h.send_app(s, {"t": "seat_xfer_state", "r": r, **await asyncio.to_thread(view, h.st)})
        target: dict = {}
        pk = ch = None
        if t == "seat_transfer":
            pk = h.st.passkey_of_device(s.device)
            if not pk:
                return await h._ctl_res(s, r, t, False, why="passkey_needed")
            n = obj.get("n")
            if not isinstance(n, str) or not re.fullmatch(r"[0-9a-f]{32}", n):
                return await h._ctl_res(s, r, t, False, why="shape")
            ch = passkey.elevate_challenge(h.channel, s.device, n, t, n, controls.object_digest(t, target))
            if obj.get("fa") is None:
                return await h._ctl_res(s, r, t, False, why="passkey_required", fa=pk["id"], fa_ch=wire.b64u(ch))
        why = controls.check(h.st, h.nonces, h.channel, s.device, obj, t, target)
        if not why and pk:
            try:
                f = passkey.approval_fields(obj.get("fa"))
                if f["id"] != pk["id"]:
                    raise passkey.PasskeyError("bad", "credential")
                h.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(pk, f, ch))
            except (passkey.PasskeyError, ValueError):
                why = "passkey_failed"
        if not why and self.busy:
            why = "busy"
        if why:
            h.st.log("control_refused", device=s.device, action=t, reason=why)
            controls.log(h.st, action=t, device=s.device, result="refused:" + why)
            return await h._ctl_res(s, r, t, False, why=why)
        by = s.name or h._dname(s.device) or s.device
        self.busy = True
        try:
            if t == "seat_transfer":
                try:
                    res, why = await asyncio.to_thread(issue, h.st, by), None
                except TransferError as e:
                    res, why = None, e.why
            else:
                status = await asyncio.to_thread(cancel, h.st)
                res, why = ({"status": status}, None) if status in ("cancelled", "none", "bound") else (None, status)
        finally:
            self.busy = False
        controls.log(h.st, action=t, device=s.device, result="ok" if res else why, obj=target, sig=obj.get("sig"),
                     n=obj.get("n"), ts=obj.get("ts"))
        if res:
            self._record(s, t, target, obj)
            h.activity("decision", by=by, result="allow", text="",
                       tool=pick(h.lang, "签发席位转移码" if t == "seat_transfer" else "撤销席位转移码",
                                 "Seat transfer code issued" if t == "seat_transfer" else "Seat transfer code revoked"))
        if not res:
            return await h._ctl_res(s, r, t, False, why=why)
        if t == "seat_transfer":   # the code only to this remote, inside its Noise session; everyone else gets the notice
            await h._ctl_res(s, r, t, True, prompt_zh=res["prompt_zh"], prompt_en=res["prompt_en"], token=res["token"],
                             expires_at=res["expires_at"])
        else:
            await h._ctl_res(s, r, t, True, status=res["status"])
        await self.from_terminal()

    def _record(self, s, tool: str, target: dict, obj: dict) -> None:
        from . import approvals
        st = self.host.st
        digest = hashlib.sha256(controls.object_text(tool, target).encode()).hexdigest()
        try:
            approvals.record(st, rid=secrets.token_hex(8), agent="phone", tool=tool, input_sha256=digest, shown_sha256=digest,
                             decision="allow", reason="device", device=s.device, sign_pub=st.sign_key(s.device),
                             sig=wire.unb64u(obj["sig"]), control={"n": obj["n"], "sig_ts": obj["ts"],
                                                                   "object_sha256": controls.object_digest(tool, target)})
        except Exception:  # noqa: BLE001 — controls.log already has it
            st.log("approvals_log", status="failed")


# ------------------------------------------------------------------ the terminal: agentj seat transfer | setup-code
def admin_check(prompt_text: str) -> bool:
    """The local administrator password, typed by a human at a real terminal into sudo itself (never read or kept here).
    No test switch: tests replace this function in-process. Limits (ADR-P547): NOPASSWD sudo, or an Agent driving a pty,
    passes `sudo -k … true`; same-uid code that can read the state needs no code at all."""
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise TransferError("no_tty")
    sudo = shutil.which("sudo")
    if not sudo:
        raise TransferError("no_sudo")
    try:
        return subprocess.run([sudo, "-k", "-p", prompt_text, "true"], timeout=180).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


MSG = {
    "unbound": "这台Agent J 主机没有绑定席位 / this Agent J host is not bound to a seat",
    "no_tty": "要在真正的终端里由你本人输入 Agent J 主机的管理员密码，什么都没做 / run this in a real terminal: you type the Agent J "
              "host's administrator password; nothing was done",
    "no_sudo": "这台 Agent J 主机没有 sudo，请在遥控器里转移（设置 → Agent 与遥控器）/ no sudo here: transfer from a remote instead",
    "bad_password": "管理员密码不对，什么都没做 / wrong administrator password; nothing was done",
    "not_found": "账号后台说这台Agent J 主机没有绑定席位 / the account dashboard says this host holds no seat",
    "billing_busy": "账号的账单正在处理，几分钟后再试 / a billing change is in progress: try again in a few minutes",
    "rate_limited": "一小时内签发太多次，稍后再试 / too many codes this hour: try again later",
    "offline": "连不上 Agent J 服务器 / cannot reach the Agent J server",
    "out_exists": "--out 指向的文件已存在：换一个新文件名（不会覆盖），什么都没做 / the --out file already exists: name a new one "
                  "(it is never overwritten); nothing was done",
}


def cmd(a) -> None:
    from .state import State
    st = State()
    if not st.exists():
        sys.exit("还没初始化：先运行 agentj init / not initialised: run agentj init first")
    expire_due(st)
    if a.seat_cmd == "transfer" and (a.status or a.cancel):
        if a.cancel:
            res = cancel(st)
            print({"cancelled": "✓ 已作废席位转移码，这台Agent J 主机的席位不变 / the transfer code was revoked; this host keeps its seat",
                   "none": "没有待用的席位转移码 / no transfer code is waiting",
                   "bound": "码已经被新主机用掉了，这里撤销不了：请在新主机上再转回来 / the code was already used by the new host; "
                            "transfer back from there"}.get(res) or "✗ " + MSG.get(res, res))
            _tell_serve(st)
            return
        v = view(st)
        print(json.dumps(v) if getattr(a, "json", False) else f"{v['state']}" + (
            f" · {time.strftime('%Y-%m-%d %H:%M', time.localtime(v['expires_at']))}" if v["expires_at"] else ""))
        return
    if not cloud.read_cloud(st):
        sys.exit("✗ " + MSG["unbound"])
    d = read(st)
    reuse = a.seat_cmd == "setup-code" and d.get("state") == "issued" and isinstance(d.get("token"), str) \
        and isinstance(d.get("expires_at"), int) and d["expires_at"] > time.time() + 60
    out_fd = _open_out(a.out) if getattr(a, "out", None) else None    # refused before anything is issued
    try:
        if not admin_check("[agentj seat] 本机管理员密码 / administrator password: "):
            raise TransferError("bad_password")
        res = {"token": d["token"], "expires_at": d["expires_at"], **dict(zip(("prompt_zh", "prompt_en"), prompt(d.get("slug"), d["token"])))} \
            if reuse else issue(st, "")
    except TransferError as e:
        if out_fd is not None:
            os.close(out_fd)
            with contextlib.suppress(OSError):
                os.unlink(a.out)
        sys.exit("✗ " + MSG.get(e.why, e.why))
    token = res["token"]
    shown = token if a.show else mask(token)
    if a.seat_cmd == "setup-code":
        print(shown)
    else:
        print(res["prompt_zh"].replace(token, shown) + "\n\n" + res["prompt_en"].replace(token, shown))
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(res["expires_at"]))
        print(f"\n有效到 / valid until {when}。撤销 / revoke: agentj seat transfer --cancel")
    if out_fd is not None:
        with os.fdopen(out_fd, "w") as f:
            f.write(res["prompt_zh"] + "\n\n" + res["prompt_en"] + "\n")
        print(f"整段提示词（含完整的码）已写到 / the whole prompt (full code) is in {a.out} (0600)")
    if not reuse:
        _tell_serve(st)


def expire_due(st, now: float | None = None) -> bool:
    """CLI (review minor 5): an issued code past its expiry is settled `expired` — the code is dropped from transfer.json."""
    d = read(st)
    exp = d.get("expires_at")
    if d.get("state") == "issued" and (type(exp) is not int or exp <= (time.time() if now is None else now)):
        settle(st, "expired")
        return True
    return False


def _open_out(name: str) -> int:
    """--out: a NEW file, 0600, never through a symlink, never over an existing file (review minor 6)."""
    try:
        return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        sys.exit("✗ " + MSG["out_exists"])
    except OSError as e:
        sys.exit(f"✗ --out：写不了 / cannot create it ({e.strerror})")


def _tell_serve(st) -> None:
    from . import names
    with contextlib.suppress(Exception):
        names.ctl_call(st, {"cmd": "seat_transfer_notice"}, 10)


def add_parser(sub) -> None:
    p = sub.add_parser("seat", help="席位：transfer 把席位转到另一台 Agent J 主机（本机管理员密码）· setup-code 只打印码 / "
                                    "seat: transfer it to another Agent J host (administrator password) · setup-code prints the code only")
    ss = p.add_subparsers(dest="seat_cmd", required=True)
    t = ss.add_parser("transfer", help="签发 24 小时有效的转移码并打印整段安装提示词 / issue a 24-hour transfer code and print the prompt")
    t.add_argument("--show", action="store_true", help="显示完整的码（默认遮挡）/ show the whole code (masked by default)")
    t.add_argument("--out", metavar="FILE", help="把整段提示词（含完整的码）写进这个文件（0600）/ write the whole prompt to FILE (0600)")
    t.add_argument("--cancel", action="store_true", help="作废待用的转移码 / revoke the waiting code")
    t.add_argument("--status", action="store_true", help="查看转移状态 / show the transfer state")
    t.set_defaults(fn=cmd)
    c = ss.add_parser("setup-code", help="只打印转移码（有待用的就用它，否则新签发）/ print only the transfer code")
    c.add_argument("--show", action="store_true", help="显示完整的码（默认遮挡）/ show the whole code (masked by default)")
    c.set_defaults(fn=cmd)
