"""P317: why a relay leg (this host's channel, or the friends mailbox) is not up — one short slug per failed attempt, shared by
serve's journal line, `agentj status --json` (`relay_last`, `friends.mbox_last`) and `agentj doctor`. Bounded metadata only:
never an exception's message (it may carry proxy credentials), never a key, a certificate or a URL path."""
from __future__ import annotations

import asyncio
import socket
import ssl

CLOSE_WHY = {4001: "replaced", 4002: "seat", 4003: "auth_refused", 4029: "limit"}   # relay/src/wire.ts close codes

TEXT = {   # why → (中文, English); "timeout" is split by phase below
    "dns": ("解析不到中继域名（DNS）", "the relay host name does not resolve (DNS)"),
    "timeout_connect": ("打开中继连接超时（TCP / TLS / WebSocket 握手没在时限内完成）",
                        "opening the relay connection timed out (TCP / TLS / WebSocket upgrade)"),
    "timeout_auth": ("中继没在时限内回应主机认证（本机进程或磁盘卡顿时也会这样）",
                     "the relay's auth answer did not arrive in time (also happens when this computer's process or disk stalls)"),
    "timeout_seat": ("读取本机席位证书超时（磁盘卡住？）", "reading this computer's seat certificate timed out (disk stalled?)"),
    "tls": ("TLS / 证书校验失败（抓包软件、代理或系统证书有问题）", "TLS / certificate check failed (an intercepting proxy or system certificates)"),
    "refused": ("中继地址拒绝连接", "the relay address refused the connection"),
    "network": ("网络不通", "network error"),
    "proxy": ("代理连接失败", "proxy connection failed"),
    "http": ("中继拒绝了 WebSocket 升级", "the relay refused the WebSocket upgrade"),
    "limit": ("中继按出口 IP 限流（同一出口 IP 的连接太多）", "the relay rate-limits this public IP (too many connections from it)"),
    "auth_refused": ("中继拒绝了这台电脑的签名（本机身份文件与频道对不上，或密钥损坏）",
                     "the relay refused this computer's signature (identity files do not match the channel, or a damaged key)"),
    "identity": ("本机身份文件不一致：签名密钥算出的频道和 config.json 里的不同", "identity files disagree: the signing key does not "
                 "belong to the channel in config.json"),
    "replaced": ("另一个 Agent J 正用同一身份连中继（开了两个 agentj serve，或状态目录被复制到了别处）",
                 "another Agent J is using this identity (two agentj serve running, or the state directory was copied elsewhere)"),
    "seat": ("席位证书无效或缺失", "no valid seat certificate"),
    "bad_challenge": ("中继答复不是预期的认证挑战", "the relay did not send the expected auth challenge"),
    "closed": ("中继关闭了连接", "the relay closed the connection"),
    "error": ("连接出错", "connection error"),
}


def close_code(e: BaseException) -> int | None:
    """The WebSocket close code a relay link ended with (4003 = relay auth deadline / refusal, 4001 = replaced), or None."""
    for attr in ("rcvd", "sent"):
        code = getattr(getattr(e, attr, None), "code", None)
        if type(code) is int:
            return code
    return None


def _http(e: BaseException) -> int | None:
    code = getattr(getattr(e, "response", None), "status_code", None)
    if type(code) is not int:
        code = getattr(e, "status_code", None)   # websockets' legacy InvalidStatusCode
    return code if type(code) is int else None


def classify(e: BaseException, phase: str) -> dict:
    """{phase, why, code, http, kind} for one failed attempt. `phase`: seat · connect · auth · up."""
    code, http, kind = close_code(e), _http(e), type(e).__name__
    if code in CLOSE_WHY:
        why = CLOSE_WHY[code]
    elif code is not None:
        why = "closed"
    elif http is not None:
        why = "limit" if http == 429 else "http"
    elif isinstance(e, socket.gaierror):
        why = "dns"
    elif isinstance(e, (TimeoutError, asyncio.TimeoutError)):
        why = "timeout"
    elif isinstance(e, ssl.SSLError) or "SSL" in kind or "Certificate" in kind:
        why = "tls"
    elif "proxy" in kind.lower() or (isinstance(e, ImportError) and "socks" in str(e).lower()):
        why = "proxy"
    elif isinstance(e, ConnectionRefusedError):
        why = "refused"
    elif isinstance(e, ValueError) and str(e) in ("relay refused auth", "bad challenge"):   # our own two messages (serve._auth)
        why = "auth_refused" if str(e) == "relay refused auth" else "bad_challenge"
    elif isinstance(e, OSError):
        why = "network"
    else:
        why = "error"
    return {"phase": phase, "why": why, "code": code, "http": http, "kind": kind}


def text(d: dict | None, lang: str = "zh") -> str:
    """One line for a person: the reason in words + the machine tokens (phase / close code / HTTP status) to quote back."""
    if not isinstance(d, dict):
        return ""
    why = d.get("why") or "error"
    pair = TEXT.get(f"{why}_{d.get('phase')}") or TEXT.get(why) or TEXT["error"]
    words = pair[0] if lang == "zh" else pair[1]
    tokens = [f"phase={d.get('phase')}", f"why={why}"]
    tokens += [f"code={d['code']}"] if type(d.get("code")) is int else []
    tokens += [f"http={d['http']}"] if type(d.get("http")) is int else []
    return f"{words} ({' '.join(tokens)})"


def text2(d: dict | None) -> str:
    """Both languages, for the journal and doctor rows that are already bilingual."""
    zh, en = text(d, "zh"), text(d, "en")
    return zh if not zh else f"{zh.rsplit(' (', 1)[0]} / {en}"
