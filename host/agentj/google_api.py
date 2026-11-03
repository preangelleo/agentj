"""#436: what the Agent does with a connected Google purpose — `agentj google youtube|search-console|gmail|drive …`.

The fenced Agent's CLI sends one fixed-shape request over elevate.sock (`{"t": "google_api", "op": …}`); serve runs it
with the purpose's own grant (gog bucket or google_oauth) — the token never reaches the Agent. Reads (youtube get, Search
Console sites / query, gmail search / get, drive ls) run at once. Every upload or send (youtube upload, gmail send, drive
upload) first raises one always-asked host card that names exactly what goes out (file, title, privacy — private unless the
card says otherwise — recipients, subject, body); only a paired device's signed allow lets it run, each time. Files: only
inside the Agent's folder, opened without following links, never key folders / credential files (media.py rules); text
holding a credential is refused (never mailed or published). Google's raw error bodies never leave this module.

Security round 1 (#436): every op — reads too — first passes the turn gate (`refusal`: a group / outside / OTG-scoped turn is
refused on the host, as contacts_serve / local_files); there is no in-process fallback when serve is not running. A card
shows the whole text that goes out (longer → refused, never cut) and nothing the local SecretIndex knows; it names where the
request came from (plaza_perms.VIA) and says when this turn has read mail (`mail_read`).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import stat
import sys
import urllib.parse

from . import google as g

OPS = {"youtube_upload": "youtube-publish", "youtube_get": "youtube-publish", "sc_sites": "search-console-read",
       "sc_query": "search-console-read", "gmail_search": "gmail-read", "gmail_get": "gmail-read",
       "gmail_send": "gmail-send", "drive_ls": "drive-file", "drive_upload": "drive-file"}
TOOLS = {"youtube_upload": "agentj google youtube upload", "gmail_send": "agentj google gmail send",
         "drive_upload": "agentj google drive upload"}          # policy_registry.HOST_TOOLS: always a card
PRIVACY = ("private", "unlisted", "public")
DIMENSIONS = ("query", "page", "country", "device", "date", "searchAppearance")
VIDEO = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
         ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}
VIDEO_MAX = 256 << 30                       # YouTube's own upload limit
_EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9][A-Za-z0-9.-]{0,189}\.[A-Za-z]{2,63}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})")
_VID = re.compile(r"[A-Za-z0-9_-]{6,20}")
_MID = re.compile(r"[A-Za-z0-9]{1,64}")
FIELDS = {"youtube_upload": {"file", "title", "description", "privacy", "publish_at"}, "youtube_get": {"id"},
          "sc_sites": set(), "sc_query": {"site", "start", "end", "dimensions", "rows"}, "gmail_search": {"query", "max"},
          "gmail_get": {"id"}, "gmail_send": {"to", "cc", "subject", "body"}, "drive_ls": {"max"},
          "drive_upload": {"file", "name"}}
NEED = {"youtube_upload": {"file", "title"}, "youtube_get": {"id"}, "sc_query": {"site", "start", "end"},
        "gmail_search": {"query"}, "gmail_get": {"id"}, "gmail_send": {"to", "subject", "body"}, "drive_upload": {"file"}}


def _str(v, lo: int, hi: int, line: bool = True) -> bool:
    return isinstance(v, str) and lo <= len(v) <= hi and "\0" not in v and not (line and ("\n" in v or "\r" in v))


def _int(v, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def shape(req: dict) -> str | None:
    """Fixed typed fields per op; anything else is refused before a card or a call."""
    op = req.get("op") if isinstance(req, dict) else None
    if op not in OPS:
        return "op: " + " | ".join(OPS)
    have = set(req) - {"t", "op"}
    if not have <= FIELDS[op] or not NEED.get(op, set()) <= have:
        return "fields: " + ", ".join(sorted(FIELDS[op]))
    v = req.get
    checks = {
        "file": lambda x: _str(x, 2, 4096) and x.startswith("/"),
        "title": lambda x: _str(x, 1, 100) and not set(x) & set("<>"),
        "description": lambda x: _str(x, 0, 5000, False) and not set(x) & set("<>"),
        "privacy": lambda x: x in PRIVACY, "publish_at": lambda x: isinstance(x, str) and bool(_AT.fullmatch(x)),
        "id": lambda x: isinstance(x, str) and bool((_VID if op == "youtube_get" else _MID).fullmatch(x)),
        "site": lambda x: _str(x, 3, 2048) and (x.startswith(("https://", "http://", "sc-domain:"))),
        "start": lambda x: isinstance(x, str) and bool(_DATE.fullmatch(x)),
        "end": lambda x: isinstance(x, str) and bool(_DATE.fullmatch(x)),
        "dimensions": lambda x: isinstance(x, list) and len(x) <= 4 and all(d in DIMENSIONS for d in x),
        "rows": lambda x: _int(x, 1, 25000), "max": lambda x: _int(x, 1, 100), "query": lambda x: _str(x, 1, 1000),
        "to": lambda x: isinstance(x, list) and 1 <= len(x) <= 10 and all(_str(a, 3, 254) and _EMAIL.fullmatch(a) for a in x),
        "cc": lambda x: isinstance(x, list) and len(x) <= 10 and all(_str(a, 3, 254) and _EMAIL.fullmatch(a) for a in x),
        "subject": lambda x: _str(x, 1, 998), "body": lambda x: _str(x, 1, 200_000, False),
        "name": lambda x: _str(x, 1, 255) and "/" not in x}
    for k in have:
        if not checks[k](v(k)):
            return k
    if v("publish_at") and v("privacy", "private") != "private":
        return "publish_at: only with privacy private (YouTube publishes it at that time)"
    return None


def ready(st, purpose: str) -> bool:
    from .google_connect import effective
    return ((effective(g.read(st).get("connections") or {}, purpose) or {}).get("step")) == "ready"


# ------------------------------------------------------------------ files (the Agent's folder only, no keys, no links)
def open_file(st, workdir, path: str, video: bool):
    """→ ((fd, name, size, mime), None) or (None, why). Video: any size up to YouTube's limit, typed by its leading bytes;
    other files: media.Media.check (its caps and secret scan), re-opened no-follow and matched to the checked inode."""
    from . import media as M
    from . import privacy
    if not workdir or not isinstance(path, str) or not os.path.isabs(path):
        return None, "outside"
    raw = os.path.normpath(path)
    if privacy.credentials(raw, entropy=False):
        return None, "secret"
    if not os.path.lexists(raw):
        return None, "gone"
    if not video:
        rec, why = M.Media(st, workdir).check(M.Ref(raw, raw, True), False)
        if why or not rec:
            return None, why or "type"
        root, parts, mime = rec["root"], rec["parts"], rec["mime"]
    else:
        real, rroot = os.path.realpath(raw), os.path.realpath(workdir)
        if M._inside(real, os.path.realpath(str(st.root))):
            return None, "private"
        if real == rroot or not M._inside(real, rroot) or not M._inside(raw, os.path.normpath(workdir)):
            return None, "outside"
        parts = os.path.relpath(real, rroot).split(os.sep)
        if M._denied(parts) or M._denied(os.path.relpath(raw, os.path.normpath(workdir)).split(os.sep)):
            return None, "secret"
        mime = VIDEO.get(os.path.splitext(parts[-1])[1].lower())
        if mime is None:
            return None, "type"
        root, rec = rroot, None
    try:
        fd = M.open_rel(root, list(parts))
    except M.Gone:
        return None, "gone"
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_size <= 0:
            raise ValueError("outside")
        if rec is not None and ((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) !=
                                (rec["dev"], rec["ino"], rec["bytes"], rec["mtime"])):
            raise ValueError("gone")
        if video:
            head = os.pread(fd, 16, 0)
            if s.st_size > VIDEO_MAX:
                raise ValueError("too_big")
            if not (head[4:8] == b"ftyp" or head[:4] == b"\x1aE\xdf\xa3" or head[:4] == b"RIFF" and head[8:12] == b"AVI "):
                raise ValueError("type")
    except (ValueError, OSError) as e:
        os.close(fd)
        return None, str(e) if isinstance(e, ValueError) else "gone"
    return (fd, parts[-1][:255], s.st_size, mime), None


def _secret(index, *texts) -> bool:
    """privacy's credential shapes, and this host's own secret values (SecretIndex, also with "/" removed: #428 rule)."""
    from . import privacy
    return any(t and (privacy.credentials(t) or index.hits(t) or index.hits(t.replace("/", ""))) for t in texts)


SHOWN = {"body": 1500, "description": 600}      # what the card shows in full; longer is refused, never cut (round 1, M2)
SRC = {"telegram": ("群聊或转发的消息", "a group or forwarded message"), "email": ("访客邮件", "a guest e-mail"),
       "automation": ("主人的自动化发来的数据", "data posted by the owner's automation"),
       "system": ("系统提示", "a system notice")}


def refusal(agent) -> dict | None:
    """Round 1 (H1 / L1): the turn gate for every Google op. A group / outside turn (not the owner, not a delegated task) or
    a delegated task whose grant needs the originating host → refused here, before Google, a card or a file."""
    from . import provenance
    if provenance.owner_grant_required(agent):
        return {"ok": False, "why": "owner_grant_scope",
                "detail": "授权单未覆盖这一步或已失效，请回发起主机补授权 / the grant does not cover this step; ask the originating host"}
    if not provenance.untrusted_turn(agent) or provenance.delegated_turn(agent):
        return None
    s = provenance.turn_source(agent)
    o = provenance._REQUEST.get()
    if o is not provenance._UNSET:
        s = provenance.stricter(s, o)
    zh, en = SRC.get(s["kind"], ("未核验来源（例如主机外自起的进程）", "an unverified source (e.g. a process Agent J did not start)"))
    return {"ok": False, "why": "group_source", "source": s["kind"],
            "detail": f"这一轮来自{zh}，不是主人本人或委托任务：Google 的读和写都不执行，也没有发卡片；不是主人拒绝 / this turn "
                      f"came from {en}, not the owner or a delegated task: no Google call runs and no card was sent; the owner "
                      "did not refuse it"}


def _turn(host) -> tuple:
    agent = getattr(host, "agent", None)
    return getattr(agent, "cur_send", None), getattr(host, "cur_turn", None)


def mail_mark(host) -> None:
    """Round 1 (L5): this turn has read mail (gmail search / get). Mail is outside text; later cards this turn say so."""
    if host is not None:
        host.__dict__["_aj_mail_turn"] = _turn(host)


def mail_read(host) -> bool:
    k = getattr(host, "__dict__", {}).get("_aj_mail_turn")
    now = _turn(host)
    return k is not None and k[0] is now[0] and k[1] == now[1]


def _mb(n: int) -> str:
    return f"{n / (1 << 20):.1f} MB"


PRIV = {"private": ("私享 private（只有你能看）", "private (only you)"),
        "unlisted": ("不公开列出 unlisted（有链接的人能看）", "unlisted (anyone with the link)"),
        "public": ("公开 public（任何人都能看）", "PUBLIC (anyone)")}


def summary(op: str, req: dict, f, zh: bool) -> str:
    """The card text: exactly what goes out, in full (serve refused anything longer than SHOWN); values come from the
    request, never Google."""
    if op == "youtube_upload":
        pv = PRIV[req.get("privacy") or "private"][0 if zh else 1]
        rows = [("上传到你的 YouTube 频道：" if zh else "Upload to your YouTube channel: ") + f"{f[1]}（{_mb(f[2])}）",
                ("标题：" if zh else "Title: ") + req["title"], ("可见性：" if zh else "Visibility: ") + pv]
        if req.get("publish_at"):
            rows.append(("定时公开：" if zh else "Goes public at: ") + req["publish_at"])
        if req.get("description"):
            rows.append(("简介：" if zh else "Description: ") + req["description"])
        return "\n".join(rows)
    if op == "gmail_send":
        rows = [("用你的 Gmail 发邮件给：" if zh else "Send from your Gmail to: ") + "、".join(req["to"])]
        if req.get("cc"):
            rows.append(("抄送：" if zh else "Cc: ") + "、".join(req["cc"]))
        rows += [("标题：" if zh else "Subject: ") + req["subject"], ("正文：" if zh else "Body: ") + req["body"]]
        return "\n".join(rows)
    return (("上传到你的 Google 云盘：" if zh else "Upload to your Google Drive: ") + f"{req.get('name') or f[1]}（{_mb(f[2])}）")


async def card(host, st, tool: str, text: str, zh: bool = True) -> dict:
    """One always-asked host card; only a paired device's signed allow (grant_info) counts. serve's turn gate (`refusal`)
    already refused group / outside / OTG turns; the card names a delegated / unattended source (L2) and a turn that read
    mail (L5)."""
    from . import policy_registry, provenance
    from .plaza_perms import MAIL, VIA
    agent = getattr(host, "agent", None)
    via = agent is not None and (provenance.unattended_turn(agent) or ("delegated" if provenance.delegated_turn(agent) else None)
                                 or ("owner_channel" if provenance.owner_channel_turn(agent) else None))
    if via in VIA:
        text += "\n" + ("来源：" if zh else "Asked during: ") + VIA[via][1 if zh else 0]
    if mail_read(host):
        text += "\n" + MAIL[1 if zh else 0]
    ans = await host.ask(tool, {"summary": text}, batch=False, grant_info=True, always_ask=True,
                         _company_proposal=policy_registry.HOST_CARD)
    ok = isinstance(ans, dict) and ans.get("behavior") == "allow" and isinstance(ans.get("grant_info"), dict)
    st.log("google_api_card", tool=tool, result="allow" if ok else "deny",
           reason=(ans or {}).get("reason") if isinstance(ans, dict) and not ok else None)
    return {"ok": ok}


async def serve(host, st, req: dict) -> dict:
    """elevate.sock `{"t": "google_api", …}` (serve side)."""
    bad = shape(req)
    if bad:
        return {"ok": False, "why": "shape", "detail": bad}
    op = req["op"]
    no = refusal(getattr(host, "agent", None))
    if no:
        st.log("google_api", kind=op, result=no["why"])
        return no
    purpose = OPS[op]
    if not ready(st, purpose):
        return {"ok": False, "why": "not_connected", "purpose": purpose,
                "next": f"agentj google connect --purpose {purpose}"}
    if op not in TOOLS:
        res = await asyncio.to_thread(run, st, req)
        if op in ("gmail_search", "gmail_get") and res.get("ok"):
            mail_mark(host)
        return res
    long = [k for k, n in SHOWN.items() if len(req.get(k) or "") > n]
    if long:
        return {"ok": False, "why": "too_long", "field": long[0], "limit": SHOWN[long[0]],
                "detail": f"太长，卡上放不下（{long[0]} 最多 {SHOWN[long[0]]} 字）：请缩短或分开发 / too long to show in full on "
                          f"the owner's card ({long[0]}: at most {SHOWN[long[0]]} characters): shorten it or split it"}
    from .peer_guard import SecretIndex
    workdir = (getattr(host, "agent_cfg", None) or {}).get("dir")
    index = await asyncio.to_thread(SecretIndex.build, workdir=workdir or None, fragment_size=8)
    if _secret(index, " ".join(req.get("to") or []), " ".join(req.get("cc") or []), req.get("title"), req.get("description"),
               req.get("subject"), req.get("body"), req.get("name")):
        return {"ok": False, "why": "secret", "detail": "a credential is never mailed or published"}
    f = None
    if "file" in req:
        f, why = await asyncio.to_thread(open_file, st, workdir, req["file"], op == "youtube_upload")
        if f is None:
            return {"ok": False, "why": why, "detail": "only a file in the Agent's folder (no keys, no links)"}
    try:
        if f is not None and not req.get("name") and _secret(index, f[1]):    # N-L1: Drive's default name is the basename
            return {"ok": False, "why": "secret", "detail": "a credential is never mailed or published"}
        zh = str(getattr(host, "lang", "zh")).startswith("zh")
        if not (await card(host, st, TOOLS[op], summary(op, req, f, zh), zh))["ok"]:
            return {"ok": False, "why": "denied", "detail": "the owner did not allow it on a paired device"}
        return await asyncio.to_thread(run, st, req, f)
    finally:
        if f is not None:
            with contextlib.suppress(OSError):
                os.close(f[0])


# ------------------------------------------------------------------ the calls (worker thread)
def _paused(st, purpose: str, why: str) -> dict:
    if why == "auth_expired":                  # only this purpose pauses; the owner reconnects it
        from .google_connect import _set
        _set(st, purpose, "oauth_consent_required", "auth_expired")
    return {"ok": False, "why": why, "purpose": purpose,
            **({"next": f"agentj google connect --purpose {purpose} --account EMAIL"} if why == "auth_expired" else {})}


def _gog(st, purpose: str, argv: list[str], **kw) -> dict:
    from .google_connect import gog_run
    code, body = gog_run(st, purpose, argv, timeout=kw.pop("timeout", 120), **kw)
    if code == 0:
        return {"ok": True, "result": body}
    if code == 4:
        return _paused(st, purpose, "auth_expired")
    from .google_oauth import disabled
    return {"ok": False, "why": "api_disabled" if disabled(body) else "failed", "code": code}


def run(st, req: dict, f=None) -> dict:
    op, purpose = req["op"], OPS[req["op"]]
    if op == "gmail_search":
        return _gog(st, purpose, ["gmail", "search", f"--max={req.get('max') or 10}", "--", req["query"]],
                    allow="gmail.search", flags=("--wrap-untrusted", "--gmail-no-send"))
    if op == "gmail_get":
        return _gog(st, purpose, ["gmail", "get", req["id"]], allow="gmail.get", flags=("--wrap-untrusted", "--gmail-no-send"))
    if op == "gmail_send":
        argv = ["gmail", "send", "--to=" + ",".join(req["to"]), *(["--cc=" + ",".join(req["cc"])] if req.get("cc") else []),
                "--subject=" + req["subject"], "--body-file=-"]
        res = _gog(st, purpose, argv, data=req["body"].encode())
        st.log("google_api", kind=op, result="sent" if res["ok"] else "failed", to=len(req["to"]) + len(req.get("cc") or []))
        return {"ok": True, "id": (res["result"] or {}).get("id")} if res["ok"] else res
    if op == "drive_ls":
        return _gog(st, purpose, ["drive", "ls", f"--max={req.get('max') or 20}"], allow="drive.ls", flags=("--gmail-no-send",))
    if op == "drive_upload":
        os.lseek(f[0], 0, os.SEEK_SET)
        res = _gog(st, purpose, ["drive", "upload", "/dev/stdin", "--name=" + (req.get("name") or f[1])], allow="drive.upload",
                   stdin=f[0], flags=("--gmail-no-send",), timeout=3600)
        st.log("google_api", kind=op, result="uploaded" if res["ok"] else "failed", bytes=f[2])
        body = res.get("result") or {}
        return {"ok": True, "id": body.get("id"), "name": body.get("name")} if res["ok"] else res
    from . import google_oauth as go
    if op == "youtube_upload":
        return _youtube_upload(st, req, f)
    if op == "youtube_get":
        status, rh, doc = go.api(st, purpose, "GET", "/youtube/v3/videos?" + urllib.parse.urlencode(
            {"id": req["id"], "part": "snippet,status,processingDetails"}))
        if status != 200:
            return _fail(st, purpose, status, rh, doc)
        items = (doc or {}).get("items") or []
        if not items:
            return {"ok": False, "why": "not_found"}
        v = items[0]
        sn, stt, pr = v.get("snippet") or {}, v.get("status") or {}, v.get("processingDetails") or {}
        return {"ok": True, "video": {"id": v.get("id"), "title": sn.get("title"), "privacy": stt.get("privacyStatus"),
                                      "publish_at": stt.get("publishAt"), "upload": stt.get("uploadStatus"),
                                      "failure": stt.get("failureReason"), "rejection": stt.get("rejectionReason"),
                                      "processing": pr.get("processingStatus")}}
    if op == "sc_sites":
        status, rh, doc = go.api(st, purpose, "GET", "/webmasters/v3/sites")
        if status != 200:
            return _fail(st, purpose, status, rh, doc)
        return {"ok": True, "sites": [{"site": e.get("siteUrl"), "permission": e.get("permissionLevel")}
                                      for e in (doc or {}).get("siteEntry") or [] if isinstance(e, dict)]}
    body = {"startDate": req["start"], "endDate": req["end"], "dimensions": req.get("dimensions") or ["query"],
            "rowLimit": req.get("rows") or 100}
    status, rh, doc = go.api(st, purpose, "POST", "/webmasters/v3/sites/" + urllib.parse.quote(req["site"], safe="")
                             + "/searchAnalytics/query", body=body)
    if status != 200:
        return _fail(st, purpose, status, rh, doc)
    return {"ok": True, "rows": (doc or {}).get("rows") or []}


def _fail(st, purpose, status, rh, doc) -> dict:
    if status is None:
        return _paused(st, purpose, rh)
    from .google_oauth import disabled
    return {"ok": False, "why": "api_disabled" if status == 403 and disabled(doc) else "failed", "http_status": status}


def _youtube_upload(st, req: dict, f) -> dict:
    """videos.insert, resumable: POST the metadata → session URL (must be Google's upload host) → PUT the bytes from the
    fd we opened; after a dropped connection, ask the session how far it got and send the rest (≤ 3 tries)."""
    from . import google_oauth as go
    fd, _name, size, mime = f
    status_ = {"privacyStatus": req.get("privacy") or "private"}     # "made for kids" stays the owner's own answer in Studio
    if req.get("publish_at"):
        status_["publishAt"] = req["publish_at"]
    meta = {"snippet": {"title": req["title"], "description": req.get("description") or ""}, "status": status_}
    s, rh, doc = go.api(st, "youtube-publish", "POST", "/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                        body=meta, headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": mime})
    if s != 200:
        return _fail(st, "youtube-publish", s, rh, doc)
    loc = rh.get("location") or ""
    if not loc.startswith(go.API + "/upload/"):
        return {"ok": False, "why": "failed"}
    done = 0
    for _try in range(3):
        tok, why = go.access(st, "youtube-publish")
        if tok is None:
            return _paused(st, "youtube-publish", why)
        os.lseek(fd, done, os.SEEK_SET)
        hdr = {"Authorization": "Bearer " + tok, "Content-Type": mime, "Content-Length": str(size - done)}
        if done:
            hdr["Content-Range"] = f"bytes {done}-{size - 1}/{size}"
        s, rh, raw = go.call("PUT", loc, data=_Reader(fd, size - done), headers=hdr, timeout=600)
        if s in (200, 201):
            v = go._json(raw) or {}
            st.log("google_api", kind="youtube_upload", result="uploaded", bytes=size)
            return {"ok": True, "id": v.get("id"), "privacy": (v.get("status") or {}).get("privacyStatus"),
                    "upload": (v.get("status") or {}).get("uploadStatus"),
                    "next": "agentj google youtube get " + str(v.get("id"))}
        if s not in (-1, 308, 500, 502, 503, 504):
            st.log("google_api", kind="youtube_upload", result="failed", http_status=s)
            return {"ok": False, "why": "failed", "http_status": s}
        s, rh, _raw = go.call("PUT", loc, data=b"", headers={"Authorization": "Bearer " + tok,
                                                            "Content-Range": f"bytes */{size}", "Content-Length": "0"})
        m = re.fullmatch(r"bytes=0-(\d+)", rh.get("range") or "")
        done = int(m.group(1)) + 1 if s == 308 and m else 0 if s == 308 else done
    return {"ok": False, "why": "failed", "detail": "upload interrupted three times; nothing was published"}


class _Reader:
    """A file-like view of `n` bytes from fd's current offset (urllib streams it; nothing is held in memory)."""

    def __init__(self, fd: int, n: int):
        self.fd, self.left = fd, n

    def read(self, k: int = 1 << 20) -> bytes:
        if self.left <= 0:
            return b""
        b = os.read(self.fd, min(k if k and k > 0 else 1 << 20, self.left, 1 << 20))
        self.left -= len(b)
        return b


# ------------------------------------------------------------------ the CLI (inside the fence: sends a request only)
SUBS = {"youtube": ("upload", "get"), "search-console": ("sites", "query"), "gmail": ("search", "get", "send"),
        "drive": ("ls", "upload")}


def add_args(p) -> None:
    p.add_argument("args", nargs="*", metavar="ARG",
                   help="youtube upload FILE | youtube get ID | search-console sites | search-console query SITE | gmail search "
                        "QUERY | gmail get ID | gmail send | drive ls | drive upload FILE")
    p.add_argument("--title", help="youtube upload：标题 / title")
    p.add_argument("--description", help="youtube upload：简介 / description")
    p.add_argument("--privacy", choices=["private", "unlisted", "public"],
                   help="youtube upload：默认 private；卡片上写明 / default private; shown on the owner's card")
    p.add_argument("--publish-at", metavar="ISO", help="youtube upload：定时公开（需 private）/ scheduled publish (private only)")
    p.add_argument("--start", metavar="YYYY-MM-DD", help="search-console query")
    p.add_argument("--end", metavar="YYYY-MM-DD", help="search-console query")
    p.add_argument("--dimension", action="append", choices=["query", "page", "country", "device", "date", "searchAppearance"],
                   help="search-console query（可重复 / repeatable）")
    p.add_argument("--rows", type=int, help="search-console query：行数 / row limit")
    p.add_argument("--max", type=int, help="gmail search / drive ls")
    p.add_argument("--to", action="append", metavar="EMAIL", help="gmail send（可重复 / repeatable）")
    p.add_argument("--cc", action="append", metavar="EMAIL", help="gmail send")
    p.add_argument("--subject", help="gmail send")
    p.add_argument("--body", help="gmail send")
    p.add_argument("--body-file", metavar="PATH", help="gmail send：正文文件，- 为标准输入 / body file, - = stdin")
    p.add_argument("--name", help="drive upload：云盘里的文件名 / name in Drive")


def cli_request(a) -> tuple[dict | None, dict]:
    args = list(a.args or [])
    sub = args[0] if args else None
    if sub not in SUBS.get(a.action, ()):
        return {"ok": False, "why": "shape", "detail": f"agentj google {a.action} " + " | ".join(SUBS[a.action])}, {}
    rest = args[1:]
    op = {"youtube": {"upload": "youtube_upload", "get": "youtube_get"}, "search-console": {"sites": "sc_sites",
          "query": "sc_query"}, "gmail": {"search": "gmail_search", "get": "gmail_get", "send": "gmail_send"},
          "drive": {"ls": "drive_ls", "upload": "drive_upload"}}[a.action][sub]
    req: dict = {"t": "google_api", "op": op}
    one = {"youtube_upload": "file", "drive_upload": "file", "youtube_get": "id", "gmail_get": "id", "sc_query": "site",
           "gmail_search": "query"}.get(op)
    if (one is None and rest) or (one is not None and not rest) or (one not in (None, "query") and len(rest) != 1):
        return {"ok": False, "why": "shape", "detail": f"agentj google {a.action} {sub}" + (f" {one.upper()}" if one else "")}, {}
    if one == "file":
        req["file"] = os.path.abspath(os.path.expanduser(rest[0]))     # the host checks it against the Agent's folder
    elif one == "query":
        req["query"] = " ".join(rest)
    elif one:
        req[one] = rest[0]
    body = a.body
    if op == "gmail_send" and a.body_file:
        try:
            body = sys.stdin.read() if a.body_file == "-" else open(os.path.expanduser(a.body_file), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            return {"ok": False, "why": "body_file"}, {}
    vals = {"title": a.title, "description": a.description, "privacy": a.privacy, "publish_at": a.publish_at,
            "start": a.start, "end": a.end, "dimensions": a.dimension, "rows": a.rows, "max": a.max, "to": a.to,
            "cc": a.cc, "subject": a.subject, "body": body, "name": a.name}
    extra = [k for k, v in vals.items() if v is not None and k not in FIELDS[op]]
    if extra:
        return {"ok": False, "why": "shape", "detail": "not for this command: " + ", ".join(extra)}, {}
    req.update({k: v for k, v in vals.items() if v is not None})
    return None, req


def cmd(a) -> int:
    from . import elevate
    from .state import State
    res, req = cli_request(a)
    if res is None:
        st = State()
        long = req["op"] in ("youtube_upload", "drive_upload")
        res = elevate.client_request(st, req, timeout=6 * 3600 if long else 900)
        if res.get("result") == "unavailable":     # round 1 (H1): no in-process fallback — only serve checks the turn
            res = {"ok": False, "why": "not_running", "detail": "Agent J is not running (agentj service status)"}
    print(json.dumps(res, ensure_ascii=False, indent=None if a.json else 2))
    return 0 if res.get("ok") else 1
