"""Long tasks (0.18, ADR-A193): the capability inventory, the one opening card, the task brief and the CEO report.

The main Agent's `agentj-capability` skill drives everything here; the owner never types a command and the phone has no new
button. Four pieces, all on this computer only (never the account dashboard, D1 or a log in clear — PR1):

- **Registry** `<state>/capabilities/registry.json` (0700 / 0600, `ltschema` "registry"): what this Agent can use — the
  configured harness, a short allowlist of CLIs, the packaged and working-root skills, `.mcp.json` servers, public web
  reading, local files, the paired phone, credentials saved through a secret card (name only) and every workflow CEO.
  `sync` only looks at those configured places (never a home-directory crawl, browser history or a secret file). Every
  entry is checked by a trusted adapter with structured arguments (the registry never stores an executable string) that has
  no side effect: no payment, sending, credential write or deletion. A new entry is never ready before its check passes;
  TTL browser 15 min · credential 24 h · CLI / skill / MCP 7 days or a changed source · CEO on every dispatch. A TTL is a
  cache, not a reason to ask the owner again: expired entries are re-checked automatically.
- **Opening card** (`ltschema` "preflight", kind=capability_preflight): at most five required items (the owner's real
  choices + required capabilities that are not ready), optional items never block. More than five → refused, the Agent
  narrows the deliverable. The answer is an Ed25519 signature of the paired device over canonical JSON with domain
  `agentj.preflight.v1` (channel, device, card, task, revision, brief digest, registry revision, nonce, expiry, action,
  picks). Card TTL 30 min, nonce once; expired / replayed / unpaired / changed-brief answers are refused and grant nothing.
  A valid submit goes to *verifying* (re-check), never straight to ready. Secrets are never in the card: they use the
  existing secret card.
- **Brief** `<workflow>/brief.json` (`ltschema` "brief"): the dispatch contract next to the unchanged task.json v1. Its
  *scope digest* (goal, acceptance, required capabilities, red lines, deliverables, plan) joins the workflow's contract hash
  (tasks.contract_sha), so a change the owner must decide voids an earlier enable; a wording / technical change does not.
  A new workflow or a schedule needs the owner's signed confirmation (domain `agentj.brief.v1`), shown on the SAME card;
  enabling the schedule is a second, ordinary `task_on` control signature checked by controls.py — each verified and
  logged on its own, any failure enables nothing. Receipts live in the state directory; a `confirmed: true` written by the
  Agent means nothing.
- **Report** `<workflow>/reports/report.json` (`ltschema` "report"): read after a run of a workflow that has a brief. It
  must match the brief (task, revision, digest, acceptance ids), its status must equal the VERDICT line and every artifact
  must be a readable file; otherwise the run is not reported as ok.

Dispatch (`agentj capability dispatch`) checks the brief, its receipt, the required capabilities and that the workflow is
not already queued or running (system idempotency, not a lock on the owner), then hands it to the existing Scheduler.
Nothing here widens the five dangerous categories (danger.py): spend / delete / send / credentials / price stay per action.

The fenced Agent reaches serve through `<state>/agentperm/capability.sock` (like recall.sock); without serve the read-only
commands (list / sync / check / digest / report-check) work locally.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time

from . import ltschema

SOCK_NAME = "capability.sock"
ENV = "AGENTJ_CAPABILITY_SOCK"
DIR_NAME = "capabilities"
PREFLIGHT_DOMAIN = "agentj.preflight.v1"
BRIEF_DOMAIN = "agentj.brief.v1"
TTL = {"browser": 900, "credential": 86400, "cli": 7 * 86400, "skill": 7 * 86400, "mcp": 7 * 86400, "ceo": 300}
CARD_TTL = 1800
END_TURN = ("End your turn now with one line to the owner: the run starts only after your turn ends (one user of the Agent "
            "at a time), and the host sends you a note with the result to read back. Do not wait or poll in this turn.")
MAX_REQUIRED = 5
MAX_OPTIONAL = 10
MAX_ASK_TEXT = 500
REQ_MAX = 256 * 1024
CARDS_KEEP = 50
CLI_ALLOW = {"git": ["code.git"], "python3": ["code.python"], "node": ["code.node"], "curl": ["web.fetch"],
             "ffmpeg": ["media.convert"], "pandoc": ["docs.convert"]}
HARNESS_TITLE = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
PROBE_RESULTS = ("ok", "auth_required", "not_found", "permission_denied", "offline", "timeout", "unsupported")
STATUS_OF = {"ok": "ready", "not_found": "missing", "auth_required": "unavailable", "permission_denied": "unavailable",
             "offline": "unavailable", "timeout": "unavailable", "unsupported": "unverified"}
_ID = re.compile(r"[a-z0-9][a-z0-9.-]{0,79}")
_CARD = re.compile(r"[0-9a-f]{32}")
_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,79}")
_REL = re.compile(r"(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9][A-Za-z0-9_./-]{0,200}")
WEB_PROBE_URL = "https://www.example.com/"


class CapError(Exception):
    """why: shape | unknown | invalid | too_many | no_serve | no_agent | no_device | changed | busy | stopped | ..."""

    def __init__(self, why: str, detail: str = "", **extra):
        super().__init__(why)
        self.why, self.detail, self.extra = why, detail, extra

    def out(self) -> dict:
        return {"result": "refused", "why": self.why, "detail": self.detail, **self.extra}


# ------------------------------------------------------------------ small helpers
def iso(t: float | None = None) -> str:
    return dt.datetime.fromtimestamp(time.time() if t is None else t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s) -> float | None:
    if not isinstance(s, str):
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def sha(b: bytes | str) -> str:
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


def slug(s: str, n: int = 60) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n].strip("-")
    return out or "x"


def cap_dir(st) -> pathlib.Path:
    d = st.root / DIR_NAME
    d.mkdir(mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    return d


@contextlib.contextmanager
def _locked(st):
    fd = os.open(cap_dir(st) / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read(st, name: str, default):
    try:
        d = json.loads((cap_dir(st) / name).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return default
    return d if isinstance(d, type(default)) else default


def _write(st, name: str, obj) -> None:
    st.write_private(cap_dir(st) / name, json.dumps(obj, ensure_ascii=False, indent=1).encode())


def audit(st, ev: str, **kw) -> None:
    """capabilities/audit.log (0600): ids, hashes and results only — never a pick, goal text or a name of a service."""
    rec = {"ts": int(time.time()), "ev": ev, **{k: v for k, v in kw.items() if v is not None}}
    st.append_private(cap_dir(st) / "audit.log", json.dumps(rec, ensure_ascii=False))


def host_id(st) -> str:
    try:
        return "h-" + sha(st.signing_pub())[:16]
    except Exception:  # noqa: BLE001 — no host key yet (tests, a fresh state)
        return "h-local"


def working_root(cfg: dict | None) -> pathlib.Path | None:
    if not cfg:
        return None
    from .main_identity import working_root as wr
    try:
        return wr(cfg)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ discovery (configured places only)
def _skill_title(path: pathlib.Path, fallback: str) -> dict:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        head = ""
    m = re.search(r"^description:\s*(.+)$", head, re.M)
    text = (m.group(1).strip() if m else fallback)[:120] or fallback
    return {"zh": text, "en": text}


def _file_digest(p: pathlib.Path) -> str | None:
    try:
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 1024 * 1024:
            return None
        return sha(p.read_bytes())
    except OSError:
        return None


def _cand(cid, kind, zh, en, ref, provides, adapter, args=None, digest=None, cred=None, scope=None) -> dict:
    return {"id": cid, "kind": kind, "title": {"zh": zh[:500] or cid, "en": en[:500] or cid}, "source_ref": ref,
            "scope": scope or [], "provides": provides, "depends_on": [], "credential_name": cred,
            "_adapter": adapter, "_args": args or {}, "_digest": digest}


def discover(st, cfg: dict | None) -> list[dict]:
    """Candidate entries (with private `_adapter` / `_args` / `_digest`, never saved)."""
    out: list[dict] = []
    root = working_root(cfg)
    kind = (cfg or {}).get("kind")
    # the harness that runs this Agent
    if kind in HARNESS_TITLE:
        exe = None
        with contextlib.suppress(Exception):
            from . import binaries
            exe = (binaries.resolve(kind) or {}).get("path")
        exe = exe if isinstance(exe, str) else shutil.which(kind)
        out.append(_cand(f"harness-{kind}", "cli", f"主 Agent（{HARNESS_TITLE[kind]}）", f"Main Agent ({HARNESS_TITLE[kind]})",
                         f"harness/{kind}", ["agent.run"], "harness-bin", {"exe": exe}, _exe_digest(exe)))
    for name, provides in CLI_ALLOW.items():
        exe = shutil.which(name)
        if exe:
            out.append(_cand(f"cli-{name}", "cli", f"命令行工具 {name}", f"Command-line tool {name}", f"cli/{name}", provides,
                             "cli-version", {"exe": exe}, _exe_digest(exe)))
    # skills: the ones this package ships, then the working root's own (three harness folders)
    pkg = pathlib.Path(__file__).resolve().parent
    seen: set[str] = set()
    skill_files = sorted((pkg / "skills").glob("*/SKILL.md")) + [pkg / "wizard" / "skill" / "SKILL.md"]
    if root:
        for d in (".claude/skills", ".agents/skills", ".opencode/skills"):
            base = root / d
            if base.is_dir() and not base.is_symlink():
                skill_files += sorted(base.glob("*/SKILL.md"))[:100]
    for f in skill_files[:200]:
        name = "agentj-workflow-wizard" if f.parent.name == "skill" else f.parent.name
        cid = "skill-" + slug(name)
        if cid in seen:
            continue
        seen.add(cid)
        ref = f"skills/{slug(name)}/SKILL.md"
        provides = ["skill." + slug(name)]
        if name == "agentj-workflow-wizard":
            provides.append("workflow.create")
        if name == "agentj-capability":
            provides.append("capability.manage")
        t = _skill_title(f, name)
        out.append(_cand(cid, "skill", t["zh"], t["en"], ref, provides, "skill-file", {"path": str(f)}, _file_digest(f)))
    # MCP servers named by the working root's .mcp.json (never started here: unverified until a harness uses them)
    if root:
        try:
            mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
            names = sorted((mcp.get("mcpServers") or {}).keys())[:30] if isinstance(mcp, dict) else []
        except (OSError, ValueError, AttributeError):
            names = []
        for n in names:
            if isinstance(n, str):
                out.append(_cand("mcp-" + slug(n), "mcp", f"连接器 {n[:60]}", f"Connector {n[:60]}", f"mcp/{slug(n)}",
                                 ["mcp." + slug(n)], "mcp-config", {}, sha(n)))
    # public web reading, local files, the owner's paired phone
    out.append(_cand("web-public", "browser", "公开网页读取", "Public web reading", "browser/public-web",
                     ["research.public", "web.read"], "web-public", {}, None, scope=["public.read"]))
    if root:
        out.append(_cand("local-files", "cli", "本机文件交付", "Local file delivery", "workspace/local",
                         ["files.write", "deliver.local"], "local-write", {"root": str(root)}, sha(str(root))))
    out.append(_cand("phone-delivery", "cli", "主人的已配对手机", "The owner's paired phone", "devices/paired",
                     ["deliver.phone"], "phone-paired", {}, None))
    # credentials saved through a secret card: the name only (elevate.secret-results.json), never a value
    try:
        from . import elevate
        results = elevate.read_results(st)
    except Exception:  # noqa: BLE001
        results = {}
    creds: dict[str, dict] = {}
    for r in results.values() if isinstance(results, dict) else []:
        rc = r.get("receipt") if isinstance(r, dict) else None
        name = (rc or {}).get("name") if isinstance(rc, dict) else None
        if r.get("result") == "saved" and isinstance(name, str) and _NAME.fullmatch(name):
            if name not in creds or (r.get("at") or 0) >= (creds[name].get("at") or 0):
                creds[name] = {"at": r.get("at") or 0, "verify": rc.get("verify")}
    for name, c in sorted(creds.items()):
        out.append(_cand("cred-" + slug(name), "credential", f"凭据 {name}", f"Credential {name}", f"credentials/{name}",
                         ["credential." + slug(name)], "credential-receipt", c, sha(f"{name}\n{c['at']}"), cred=name))
    # workflow CEOs (tasks.discover: root/<id> and legacy workflows/<id>)
    if root:
        from . import tasks
        for e in tasks.discover(str(root)):
            if not _ID.fullmatch(e["id"]):
                continue
            t = (e.get("task") or {}).get("title")
            zh, en = (t.get("zh"), t.get("en")) if isinstance(t, dict) else (e["id"], e["id"])
            out.append(_cand("ceo-" + e["id"], "ceo", str(zh or e["id"]), str(en or e["id"]), e["relative_dir"],
                             ["workflow." + e["id"]], "ceo-entry", {"dir": e["dir"], "problems": e["problems"]},
                             e.get("tsha") or sha("|".join(e["problems"]))))
    return out


def _exe_digest(exe: str | None) -> str | None:
    if not exe:
        return None
    try:
        s = os.stat(exe)
        return sha(f"{os.path.realpath(exe)}\n{s.st_size}\n{int(s.st_mtime)}")
    except OSError:
        return None


# ------------------------------------------------------------------ trusted adapters (no side effect)
def _probe_cli(args, ctx) -> str:
    exe = args.get("exe")
    if not exe:
        return "not_found"
    try:
        p = subprocess.run([exe, "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=8, env={k: v for k, v in os.environ.items() if not k.endswith(("_KEY", "_TOKEN", "_SECRET"))})
    except subprocess.TimeoutExpired:
        return "timeout"
    except PermissionError:
        return "permission_denied"
    except OSError:
        return "not_found"
    return "ok" if p.returncode == 0 else "unsupported"


def _probe_harness(args, ctx) -> str:
    """The configured harness: its resolved executable is a runnable file (never started here — serve starts it; its login
    is checked by doctor / the first turn)."""
    exe = args.get("exe")
    if not exe or not os.path.isfile(exe):
        return "not_found"
    return "ok" if os.access(exe, os.X_OK) else "permission_denied"


def _probe_skill(args, ctx) -> str:
    p = pathlib.Path(args.get("path") or "")
    try:
        head = p.read_text(encoding="utf-8")[:4000]
    except PermissionError:
        return "permission_denied"
    except (OSError, UnicodeDecodeError):
        return "not_found"
    return "ok" if head.startswith("---") and re.search(r"^name:\s*\S", head, re.M) else "unsupported"


def _probe_web(args, ctx) -> str:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(WEB_PROBE_URL, method="HEAD", headers={"User-Agent": "agentj-capability-check"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return "ok" if 200 <= r.status < 400 else "offline"
    except urllib.error.HTTPError as e:
        return "ok" if e.code in (403, 405) else "offline"
    except TimeoutError:
        return "timeout"
    except (urllib.error.URLError, OSError):
        return "offline"


def _probe_local(args, ctx) -> str:
    """A temporary file under <root>/.agentj (created, read back, removed): local and reversible, never the owner's files."""
    root = pathlib.Path(args.get("root") or "")
    try:
        d = root / ".agentj"
        if d.is_symlink():
            return "permission_denied"
        d.mkdir(mode=0o700, exist_ok=True)
        p = d / f"capability-probe-{secrets.token_hex(6)}"
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, b"ok")
        finally:
            os.close(fd)
        ok = p.read_bytes() == b"ok"
        p.unlink()
        return "ok" if ok else "permission_denied"
    except PermissionError:
        return "permission_denied"
    except OSError:
        return "not_found"


def _probe_phone(args, ctx) -> str:
    st = ctx["st"]
    try:
        return "ok" if any(v.get("sk") for v in st.devices().values()) else "not_found"
    except Exception:  # noqa: BLE001
        return "not_found"


def _probe_credential(args, ctx) -> str:
    """Only the business adapter can check a key; here: saved and verified (ok) within 24 h → ok, else re-check needed."""
    if args.get("verify") == "ok" and time.time() - float(args.get("at") or 0) < TTL["credential"]:
        return "ok"
    return "auth_required" if args.get("verify") == "fail" else "unsupported"


def _probe_ceo(args, ctx) -> str:
    if args.get("problems"):
        return "unsupported"
    d = pathlib.Path(args.get("dir") or "")
    if not any((d / n).is_file() for n in ("CLAUDE.md", "AGENTS.md", "RUN.md")):
        return "not_found"
    b = d / "brief.json"
    if b.exists():
        _, probs = load_brief(d)
        if probs:
            return "unsupported"
    return "ok"


def _probe_mcp(args, ctx) -> str:
    return "unsupported"


ADAPTERS = {"cli-version": _probe_cli, "harness-bin": _probe_harness, "skill-file": _probe_skill, "web-public": _probe_web, "local-write": _probe_local,
            "phone-paired": _probe_phone, "credential-receipt": _probe_credential, "ceo-entry": _probe_ceo,
            "mcp-config": _probe_mcp}


# ------------------------------------------------------------------ the registry
def empty_registry(st) -> dict:
    return {"schema_version": 1, "host_id": host_id(st), "revision": 1, "generated_at": iso(), "entries": []}


def read_registry(st) -> dict:
    """The saved registry; a missing or broken file reads as empty (rebuilt by the next sync). An unknown schema_version is
    refused (CapError unknown_version): a newer Agent J wrote it, we do not guess."""
    try:
        d = json.loads((cap_dir(st) / "registry.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return empty_registry(st)
    if isinstance(d, dict) and d.get("schema_version") not in (None, 1):
        raise CapError("unknown_version", "registry.json was written by a newer Agent J")
    if ltschema.problems("registry", d):
        reg = empty_registry(st)
        if isinstance(d, dict) and isinstance(d.get("revision"), int) and not isinstance(d.get("revision"), bool):
            reg["revision"] = max(1, d["revision"])     # monotonic even across a broken file
        return reg
    return d


def _expired(e: dict, now: float) -> bool:
    exp = parse_iso((e.get("check") or {}).get("expires_at"))
    return exp is None or exp <= now


def _entry(c: dict, result: str, now: float, old: dict | None) -> dict:
    status = STATUS_OF.get(result, "unverified")
    ok = result == "ok"
    last = iso(now) if ok else ((old or {}).get("check") or {}).get("last_verified_at")
    return {"id": c["id"], "kind": c["kind"], "title": c["title"], "source_ref": c["source_ref"], "scope": c["scope"],
            "provides": c["provides"], "depends_on": c["depends_on"], "status": status,
            "credential_name": c["credential_name"],
            "check": {"status": result, "adapter_id": c["_adapter"], "last_verified_at": last,
                      "expires_at": iso(now + TTL[c["kind"]]) if ok else None, "source_digest": c["_digest"]}}


def sync(st, cfg: dict | None, *, probe: bool = True, ids: list[str] | None = None, force: bool = False,
         adapters: dict | None = None, now: float | None = None) -> dict:
    """Discover + re-check what needs it (new, changed source, expired TTL, not ready, `force`, or every CEO named in `ids`).
    Atomic, locked; the revision moves only when something changed (a refresh with nothing to do is idempotent)."""
    adapters = {**ADAPTERS, **(adapters or {})}
    now = time.time() if now is None else now
    with _locked(st):
        old = read_registry(st)
        prev = {e["id"]: e for e in old["entries"]}
        entries: list[dict] = []
        cands = discover(st, cfg)
        seen = set()
        for c in cands:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            o = prev.get(c["id"])
            want = ids is None or c["id"] in ids
            changed = o is not None and (o.get("check") or {}).get("source_digest") != c["_digest"]
            need = probe and want and (force or o is None or changed or o.get("status") != "ready" or _expired(o, now)
                                       or (c["kind"] == "ceo" and ids is not None))
            if need:
                try:
                    result = adapters[c["_adapter"]](c["_args"], {"st": st, "cfg": cfg})
                except Exception:  # noqa: BLE001 — an adapter bug is "unsupported", never a crash or an error text
                    result = "unsupported"
                result = result if result in PROBE_RESULTS else "unsupported"
                e = _entry(c, result, now, o)
            elif o is not None:
                e = dict(o, title=c["title"], source_ref=c["source_ref"], provides=c["provides"], scope=c["scope"],
                         credential_name=c["credential_name"])
                if changed or (o.get("status") == "ready" and _expired(o, now)):
                    e["status"] = "stale"
            else:
                e = _entry(c, "unsupported", now, None)
                e["status"] = "discovered"
            if c["kind"] == "ceo" and any("duplicate" in p for p in c["_args"].get("problems") or []):
                e["status"] = "conflict"
            entries.append(e)
        # P125: business adapters own their verification; project their closed-schema evidence into this one registry.
        # No site login or Google authorization is attempted here. Revocation and stale sign-ins apply on every sync.
        from . import browser_sites, google
        if probe:
            for sid, rec in browser_sites.load(st)["sites"].items():
                if (ids is None or "browser-" + sid in ids) and (force or browser_sites._stale(rec, now)):
                    browser_sites.check(st, [sid], if_stale=not force, reason="capability")
            gs = google.status(st)
            if (ids is None or "google-cli" in ids) and gs["tool"]["state"] == "installed" and (force or not gs["tool"]["fresh"]):
                google.check(st)   # own pinned CLI only; no authorization, installation or external API request
        projected = browser_sites.registry(st, now=now) + google.registry_entries(st)
        probs = ltschema.problems("registry", {"schema_version": 1, "host_id": host_id(st), "revision": 1,
                    "generated_at": iso(now), "entries": projected})
        if probs:raise CapError("invalid", probs[0])
        for e in projected:
            if e["id"] in seen:raise CapError("invalid", "duplicate trusted capability id")
            o = prev.get(e["id"])
            if e["id"] == "browser-runtime" and e["status"] == "ready" and o and o["status"] == "ready" \
                    and o["check"]["source_digest"] == e["check"]["source_digest"] and not _expired(o, now) and not force:
                e["check"] = dict(o["check"])   # unchanged local runtime: stable inventory revision until TTL refresh
            if e["status"] == "ready" and _expired(e, now):e["status"] = "stale"
            seen.add(e["id"])
            entries.append(e)
        for oid, o in prev.items():           # gone (uninstalled, revoked, workflow removed): kept as missing, never ready
            if oid not in seen:
                e = dict(o, status="missing")
                e["check"] = dict(o["check"], status="not_found", expires_at=None)
                entries.append(e)
        entries.sort(key=lambda e: (e["kind"], e["id"]))
        reg = {"schema_version": 1, "host_id": host_id(st), "revision": old["revision"], "generated_at": old["generated_at"],
               "entries": entries[:1000]}
        if ltschema.canonical(entries) != ltschema.canonical(old["entries"]) or reg["host_id"] != old["host_id"]:
            reg["revision"] = old["revision"] + 1
            reg["generated_at"] = iso(now)
            probs = ltschema.problems("registry", reg)
            if probs:
                raise CapError("invalid", probs[0])
            _write(st, "registry.json", reg)
            audit(st, "registry", revision=reg["revision"], n=len(entries))
        return reg


def summary(reg: dict, lang: str = "zh") -> list[dict]:
    """What the phone's 「我的 Agent 会什么」 sheet and `list` show (no paths of the owner's files)."""
    out = []
    for e in reg["entries"]:
        c = e["check"]
        out.append({"id": e["id"], "kind": e["kind"], "title": e["title"], "status": e["status"],
                    "provides": e["provides"], "verified": c.get("last_verified_at"), "expires": c.get("expires_at"),
                    "credential": e.get("credential_name")})
    return out


# ------------------------------------------------------------------ brief / report contracts
def scope_of(brief: dict) -> dict:
    """What the owner decides (a change here needs a new confirmation; everything else is a technical detail)."""
    return {"goal": brief.get("goal"), "acceptance": [{"id": a.get("id"), "criterion": a.get("criterion")}
                                                      for a in brief.get("acceptance") or [] if isinstance(a, dict)],
            "capabilities": sorted(c.get("id") for c in brief.get("capabilities") or [] if isinstance(c, dict) and c.get("required")),
            "red_lines": brief.get("red_lines"), "deliverables": brief.get("deliverables"), "plan": brief.get("plan"),
            "workflow_id": brief.get("workflow_id"), "task_id": brief.get("task_id")}


def scope_digest(brief: dict) -> str:
    return sha(ltschema.canonical(scope_of(brief)))


def brief_digest(raw: bytes) -> str:
    return sha(raw)


def brief_problems(brief, folder_id: str | None = None) -> list[str]:
    out = ltschema.problems("brief", brief)
    if out:
        return out
    if folder_id is not None and brief["workflow_id"] != folder_id:
        out.append("workflow_id does not match its folder")
    if brief["confirmation"]["scope_digest"] != scope_digest(brief):
        out.append("confirmation.scope_digest is not the scope digest (agentj capability digest prints it)")
    ids = [a["id"] for a in brief["acceptance"]]
    if len(set(ids)) != len(ids):
        out.append("acceptance ids must be unique")
    caps = [c["id"] for c in brief["capabilities"]]
    if len(set(caps)) != len(caps):
        out.append("capability ids must be unique")
    from .taskspec import secret_like
    if secret_like(json.dumps(brief, ensure_ascii=False)):
        out.append("brief.json looks like it holds a credential: name the service only")
    return out


def load_brief(wf_dir) -> tuple[dict | None, list[str]]:
    p = pathlib.Path(wf_dir) / "brief.json"
    raw = _regular(p)
    if raw is None:
        return None, ["brief.json is missing (or not a regular file ≤ 256 KiB)"]
    try:
        b = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, ["brief.json is not valid UTF-8 JSON"]
    probs = brief_problems(b, pathlib.Path(wf_dir).name)
    return (b, probs)


def brief_raw(wf_dir) -> bytes | None:
    return _regular(pathlib.Path(wf_dir) / "brief.json")


def _regular(p: pathlib.Path, limit: int = 256 * 1024) -> bytes | None:
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_size > limit:
            return None
        return os.read(fd, limit + 1)
    finally:
        os.close(fd)


def contract_part(wf_dir) -> tuple[bytes, list[str]]:
    """What a workflow's brief adds to its contract hash (tasks.contract_sha): b"" without a brief (old workflows keep
    their hash), the scope digest with a valid one, problems with a broken one (such a workflow never runs)."""
    if not os.path.lexists(os.path.join(wf_dir, "brief.json")):
        return b"", []
    b, probs = load_brief(wf_dir)
    if probs:
        return b"", ["brief.json: " + probs[0]]
    return ("\0brief\0" + scope_digest(b)).encode(), []


def report_check(wf_dir, brief: dict, raw_brief: bytes, verdict: str | None, since: float = 0) -> tuple[str, str]:
    """('ok', '') or (problem code, detail) for <workflow>/reports/report.json after a run that started at `since`."""
    wf = pathlib.Path(wf_dir)
    p = wf / "reports" / "report.json"
    raw = _regular(p)
    if raw is None:
        return "missing", "reports/report.json 不存在 / missing"
    try:
        if os.stat(p).st_mtime + 1 < since:
            return "old", "reports/report.json 不是这次运行写的 / not written by this run"
        rep = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        return "invalid", "reports/report.json 不是 JSON / not JSON"
    probs = ltschema.problems("report", rep)
    if probs:
        return "invalid", probs[0]
    if rep["task_id"] != brief["task_id"] or rep["brief_revision"] != brief["revision"] \
            or rep["brief_digest"] != brief_digest(raw_brief):
        return "mismatch", "报告与任务书不一致 / report is not for this brief"
    if {a["id"] for a in rep["acceptance"]} != {a["id"] for a in brief["acceptance"]}:
        return "mismatch", "验收项与任务书不一致 / acceptance items differ from the brief"
    if verdict is not None and rep["status"] != verdict:
        return "verdict", f"报告状态 {rep['status']} 与 VERDICT {verdict} 冲突 / report status conflicts with VERDICT"
    for a in rep["artifacts"]:
        q = wf / a
        b = _regular(q, 64 * 1024 * 1024)
        if not b:
            return "artifact", f"产物不可读 / artifact not readable: {a}"
    return "ok", ""


def brief_block(e: dict) -> str:
    """0.18 (ADR-A193 §5): a workflow with brief.json gets its contract and the report it must leave."""
    raw = brief_raw(e["dir"])
    if raw is None:
        return ""
    b, probs = load_brief(e["dir"])
    if b is None or probs:
        return ""
    return ("\n\n---\n[任务书 / brief] brief.json（revision " + str(b["revision"]) + "，digest " + brief_digest(raw)
            + "）是这次派单的合同：目标、验收、红线、交付和汇报节点都以它为准；主人的开工选择在 inputs/owner-choices.json（如有）。"
            "交付到主人已配对手机由 Agent J 主机在运行结束后完成（推送 audience=owner 的可读交付物），你不发送任何东西；这类验收以本机文件可读为准。"
            "执行中自己解决技术和可逆细节，不为小问题求许可；付款、删除、对外发送、改凭据、改价仍逐条在手机上申请。"
            "真正缺少主人决定或外部授权时，只停依赖它的部分，独立部分继续。\n"
            "结束前写 reports/report.json（schema_version 1；task_id、brief_revision、brief_digest 与任务书一致；status 与 VERDICT "
            "一致；acceptance 逐项给 pass/fail/unverified/skipped 和 evidence_refs；artifacts 是可读的产物相对路径；"
            "unprocessed/blockers/assumptions/recoveries/approval_refs/next_step）。可用 `agentj capability report-check "
            "--verdict <ok|attention|fail>` 自查。缺报告、未验收或产物不可读都不算成功。\n"
            "/ brief.json is this run's contract (goal, acceptance, red lines, deliverables, reporting); the owner's opening choices "
            "are in inputs/owner-choices.json if present. Delivery to the owner's paired phone is done by the Agent J host after the run "
            "(it pushes the readable owner deliverables): never send anything yourself; judge that acceptance by the local file. "
            "Solve technical and reversible details yourself; spend / delete / send / "
            "credentials / price still ask on the phone per action. A genuinely missing owner decision pauses only what depends "
            "on it. Before you finish write reports/report.json (report schema v1, matching task_id / brief_revision / "
            "brief_digest, status equal to your VERDICT, per-criterion status and evidence, readable artifacts). No report, no "
            "verified acceptance or no readable artifact is not success.")


def check_run_report(e: dict, res: dict, since: float) -> None:
    """0.18: with a brief, an ok VERDICT also needs a matching reports/report.json and readable artifacts — otherwise the
    run is reported as attention with the reason (never as success)."""
    raw = brief_raw(e["dir"])
    if raw is None:
        return
    b, probs = load_brief(e["dir"])
    if b is None or probs:
        res["report_check"] = "brief"
        if res["verdict"] == "ok":
            res["verdict"], res["line"] = "attention", ("任务书无效 / brief invalid: " + (probs[0] if probs else ""))[:300]
        return
    code, detail = report_check(e["dir"], b, raw, res["verdict"], since)
    res["report_check"] = code
    # the owner's deliverables go to the paired phones as files on the run's page (the host delivers, never the CEO)
    res["deliver"] = [str(p) for d in b["deliverables"] if d["audience"] == "owner"
                      for p in [os.path.join(e["dir"], d["path"])] if _regular(pathlib.Path(p), 64 * 1024 * 1024)]
    if code != "ok" and res["verdict"] == "ok":
        res["verdict"] = "attention"
        res["line"] = ("报告未通过检查：" + detail + " — " + res["line"])[:300]


# ------------------------------------------------------------------ the opening card
def norm_asks(asks) -> list[dict]:
    """The Agent's owner questions (≤ 5): [{id, why{zh,en}, how{zh,en}, reuse{zh,en}, input: {type: text|single|multi,
    options: [≤ 8 strings]}}]. Never a secret: a value that looks like a key is refused."""
    if asks is None:
        return []
    if not isinstance(asks, list) or len(asks) > MAX_REQUIRED:
        raise CapError("too_many" if isinstance(asks, list) else "shape", f"at most {MAX_REQUIRED} owner questions")
    out = []
    for a in asks:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not _ID.fullmatch(a["id"]):
            raise CapError("shape", "ask.id: [a-z0-9][a-z0-9.-]{0,79}")
        item = {"id": a["id"]}
        for k in ("why", "how", "reuse"):
            v = a.get(k)
            if not (isinstance(v, dict) and all(isinstance(v.get(x), str) and 0 < len(v[x]) <= 500 for x in ("zh", "en"))):
                raise CapError("shape", f"ask.{k}: {{zh, en}} 1–500 characters")
            item[k] = {"zh": v["zh"], "en": v["en"]}
        inp = a.get("input") or {"type": "text"}
        typ = inp.get("type") if isinstance(inp, dict) else None
        if typ not in ("text", "single", "multi"):
            raise CapError("shape", "ask.input.type: text | single | multi")
        opts = inp.get("options") or []
        if typ != "text" and not (isinstance(opts, list) and 1 <= len(opts) <= 8
                                  and all(isinstance(o, str) and 0 < len(o) <= 120 for o in opts)):
            raise CapError("shape", "ask.input.options: 1–8 strings ≤ 120 characters")
        default = inp.get("default")
        item["input"] = {"type": typ, "options": opts if typ != "text" else [],
                         **({"default": default} if isinstance(default, (str, list)) else {})}
        from .taskspec import secret_like
        if secret_like(json.dumps(item, ensure_ascii=False)):
            raise CapError("shape", "an owner question must not hold a credential")
        out.append(item)
    if len({a["id"] for a in out}) != len(out):
        raise CapError("shape", "ask ids must be unique")
    return out


def _action_kind(kind: str) -> str:
    return {"credential": "secret_card", "browser": "login"}.get(kind, "verify")


def _cap_item(cid: str, e: dict | None, required: bool) -> dict:
    k = (e or {}).get("kind", "cli")
    t = (e or {}).get("title") or {"zh": cid, "en": cid}
    how = {"secret_card": ("用手机上的专用密钥卡填一次，Agent 看不到值", "Fill in once on the phone's secret card; the Agent never sees the value"),
           "login": ("在这台电脑的浏览器里登录一次", "Sign in once in this computer's browser"),
           "verify": ("我会自动检查；没通过会告诉你下一步", "I check it automatically and tell you the next step if it fails")}[_action_kind(k)]
    st = (e or {}).get("status")
    item_status = {"ready": "ready", "missing": "missing", "unavailable": "failed", "conflict": "failed"}.get(st, "unverified")
    return {"id": "cap-" + cid[:75], "capability_id": cid,
            "why": {"zh": f"需要：{t['zh']}"[:500], "en": f"Needed: {t['en']}"[:500]} if required else
                   {"zh": f"可选：{t['zh']}，没有也能完成"[:500], "en": f"Optional: {t['en']}; the task works without it"[:500]},
            "how": {"zh": how[0], "en": how[1]},
            "reuse": {"zh": "配一次，以后同类任务自动复用", "en": "Set up once, reused by similar tasks"},
            "status": item_status, "action_kind": _action_kind(k)}


def plan_card(st, cfg: dict | None, wf: str, asks: list[dict], *, adapters: dict | None = None,
              now: float | None = None) -> dict:
    """Everything the host knows before showing a card: {need_card, preflight, asks, brief, enable, missing, optional}.
    Refuses (CapError) a broken brief, an unknown workflow and more than five required items."""
    from . import tasks
    root = working_root(cfg)
    if not root:
        raise CapError("no_agent", "no Agent working folder configured")
    try:
        e = tasks.find(str(root), wf)
    except tasks.TaskError:
        raise CapError("unknown", f"no workflow {wf}") from None
    raw = brief_raw(e["dir"])
    b, probs = load_brief(e["dir"])
    if b is None or probs:
        raise CapError("invalid", probs[0] if probs else "brief.json")
    if e["problems"]:
        raise CapError("invalid", e["problems"][0])
    if (e["task"] or {}).get("mode") == "research":
        raise CapError("invalid", "task.json mode research is read-only: it cannot write the brief's deliverables or "
                                  "reports/report.json — use mode normal (dangerous actions still ask per action)")
    now = time.time() if now is None else now
    ids = [c["id"] for c in b["capabilities"]] + ["ceo-" + wf]
    reg = sync(st, cfg, ids=ids, adapters=adapters, now=now)
    by = {x["id"]: x for x in reg["entries"]}
    required, optional, missing = [], [], []
    for a in asks:
        required.append({"id": a["id"], "capability_id": "ceo-" + wf if ("ceo-" + wf) in by else (b["capabilities"][0]["id"] if b["capabilities"] else "ceo-" + wf),
                         "why": a["why"], "how": a["how"], "reuse": a["reuse"], "status": "missing", "action_kind": "choose"})
    for c in b["capabilities"]:
        ent = by.get(c["id"])
        if ent and ent["status"] == "ready":
            continue
        item = _cap_item(c["id"], ent, c["required"])
        (required if c["required"] else optional).append(item)
        if c["required"]:
            missing.append(c["id"])
    ceo = by.get("ceo-" + wf)
    if not ceo or ceo["status"] != "ready":
        missing.append("ceo-" + wf)
    if len(required) > MAX_REQUIRED:
        raise CapError("too_many", f"{len(required)} required items: narrow the deliverable or split the goal (≤ {MAX_REQUIRED})",
                       required=[r["id"] for r in required])
    rcpt = receipt_for(st, wf, b)
    need_brief = b["confirmation"]["mode"] == "signed" and rcpt is None
    enable = None
    if b["plan"]["kind"] == "weekly" and b["plan"]["human_enable_required"]:
        on = tasks.load(st)["on"].get(wf)
        if not on or on.get("tsha") != e["tsha"]:
            enable = {"id": wf, "tsha": e["tsha"]}
    pf = {"schema_version": 1, "card_id": secrets.token_hex(16), "task_id": b["task_id"], "revision": b["revision"],
          "brief_digest": brief_digest(raw), "registry_revision": reg["revision"], "nonce": secrets.token_hex(16),
          "expires_at": iso(now + CARD_TTL), "status": "needs_input", "required": required, "optional": optional[:MAX_OPTIONAL]}
    if not required and not need_brief and not enable:
        pf["status"] = "ready"
    probs = ltschema.problems("preflight", pf)
    if probs:
        raise CapError("invalid", probs[0])
    brief_view = {"goal": b["goal"], "acceptance": [a["criterion"] for a in b["acceptance"]], "red_lines": b["red_lines"],
                  "deliverables": [{"path": d["path"], "format": d["format"], "audience": d["audience"]} for d in b["deliverables"]],
                  "plan": b["plan"], "workflow": wf, "title": (e["task"] or {}).get("title"),
                  "schedule": (e["task"] or {}).get("schedule"), "tz": (e["task"] or {}).get("tz"),
                  "scope_digest": scope_digest(b), "confirm": need_brief, "full": b}
    return {"need_card": bool(required or need_brief or enable), "preflight": pf, "asks": {a["id"]: a["input"] for a in asks},
            "brief": brief_view, "enable": enable, "missing": missing, "wf": wf, "dir": e["dir"]}


def preflight_binding(channel: str, device: str, card: dict, action: str, picks) -> dict:
    return {"domain": PREFLIGHT_DOMAIN, "channel": channel, "device": device, "card_id": card["card_id"],
            "task_id": card["task_id"], "revision": card["revision"], "brief_digest": card["brief_digest"],
            "registry_revision": card["registry_revision"], "nonce": card["nonce"], "expires_at": card["expires_at"],
            "action": action, "picks": picks if action == "submit" else {}}


def brief_binding(channel: str, device: str, card: dict, scope: str, action: str) -> dict:
    return {"domain": BRIEF_DOMAIN, "channel": channel, "device": device, "card_id": card["card_id"],
            "task_id": card["task_id"], "revision": card["revision"], "brief_digest": card["brief_digest"],
            "scope_digest": scope, "nonce": card["nonce"], "expires_at": card["expires_at"], "action": action}


def signed_bytes(binding: dict) -> bytes:
    return ltschema.canonical(binding)


def verify_sig(sign_pub: bytes | None, sig_b64: str, binding: dict) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from . import wire
    if not sign_pub or len(sign_pub) != 32 or not isinstance(sig_b64, str) or len(sig_b64) > 100:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(sign_pub).verify(wire.unb64u(sig_b64), signed_bytes(binding))
        return True
    except (InvalidSignature, ValueError):
        return False


def check_picks(asks: dict, picks) -> str | None:
    if not isinstance(picks, dict) or set(picks) != set(asks):
        return "picks"
    from .taskspec import secret_like
    for k, spec in asks.items():
        v = picks[k]
        if spec["type"] == "text":
            if not isinstance(v, str) or not v.strip() or len(v) > MAX_ASK_TEXT:
                return "picks"
        elif spec["type"] == "single":
            if v not in spec["options"]:
                return "picks"
        elif not (isinstance(v, list) and v and len(set(v)) == len(v) and all(x in spec["options"] for x in v)):
            return "picks"
    if secret_like(json.dumps(picks, ensure_ascii=False)):
        return "secret"                    # a key typed into a choice: refused, never stored (secret card instead)
    return None


# ------------------------------------------------------------------ receipts (state directory; the Agent cannot write them)
def receipts(st) -> dict:
    d = _read(st, "receipts.json", {})
    return {k: v for k, v in d.items() if isinstance(v, list)}


def receipt_for(st, wf: str, brief: dict) -> dict | None:
    """The newest signed confirmation of THIS scope (goal, acceptance, capabilities, red lines, deliverables, plan)."""
    sd = scope_digest(brief)
    for r in reversed(receipts(st).get(wf, [])):
        if isinstance(r, dict) and r.get("scope_digest") == sd and r.get("mode") == "signed":
            return r
    return None


def add_receipt(st, wf: str, rec: dict) -> None:
    with _locked(st):
        d = receipts(st)
        d.setdefault(wf, []).append(rec)
        d[wf] = d[wf][-20:]
        _write(st, "receipts.json", d)


def existing_workflow(st, wf: str) -> bool:
    """An existing, owner-known workflow (requested-mode briefs are allowed for it): the owner enabled it before, it has
    run before, or a signed brief of it was confirmed. A brand-new workflow always needs the signed card."""
    from . import tasks
    d = tasks.load(st)
    return wf in d["on"] or wf in d["last"] or bool(receipts(st).get(wf))


# ------------------------------------------------------------------ unattended runs inside the confirmed brief
BRIEF_WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
BRIEF_READ_TOOLS = ("WebFetch", "WebSearch")
BRIEF_FIXED = {"inputs", "documentation", "task.json", "brief.json", "CLAUDE.md", "AGENTS.md"}


def brief_scope_for(st, e: dict) -> dict | None:
    """The scope a run of workflow `e` may act in without a phone card: only when it has a valid brief whose scope the owner
    confirmed (signed receipt) or that is an owner-known workflow on a requested brief. None = the usual cards."""
    raw = brief_raw(e["dir"])
    if raw is None:
        return None
    b, probs = load_brief(e["dir"])
    if b is None or probs:
        return None
    if receipt_for(st, e["id"], b) is None and not (b["confirmation"]["mode"] == "requested" and existing_workflow(st, e["id"])):
        return None
    task = e.get("task") or {}
    fixed = set(BRIEF_FIXED) | {task.get("prompt_file"), task.get("dry_run_prompt_file")}
    return {"dir": os.path.realpath(e["dir"]), "wf": e["id"], "fixed": {x for x in fixed if isinstance(x, str)}}


def brief_allows(scope: dict | None, tool: str, tool_input, dangerous: bool) -> bool:
    """ADR-A194 §6 table, first row: inside a confirmed brief, reading public pages and writing the workflow's own output
    files happen without a card. Never a dangerous category, never Bash / MCP, never a file outside the workflow folder, its
    contract files (task.json, brief.json, RUN.md, entry files), the owner's choices (inputs/) or a top-level file."""
    if not scope or dangerous or not isinstance(tool_input, dict):
        return False
    if tool in BRIEF_READ_TOOLS:
        return True
    if tool not in BRIEF_WRITE_TOOLS:
        return False
    fp = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(fp, str) or not fp or str(tool_input.get("new_string") or "").startswith("files: "):
        return False
    real = os.path.realpath(fp if os.path.isabs(fp) else os.path.join(scope["dir"], fp))
    root = scope["dir"]
    if not real.startswith(root.rstrip(os.sep) + os.sep):
        return False
    parts = os.path.relpath(real, root).split(os.sep)
    if len(parts) < 2 or parts[0] in scope["fixed"] or parts[0].startswith("."):
        return False
    return True


_HOST_MEDIATED = re.compile(r"(?:cd [A-Za-z0-9_./~-]{1,300} ?(?:;|&&) ?)?"
                            r"agentj capability (?:list|sync|check|show|prepare|result|dispatch|digest|report-check)"
                            r"(?: [A-Za-z0-9_./=:@-]{1,200}){0,8}" + r"(?: 2>&1)?(?: \| grep -v [^\s|;&$`'\"<>()\\]{1,40})?")


_WIZARD_READ = re.compile(r"(?:cd [A-Za-z0-9_./~-]{1,300} ?(?:;|&&) ?)?agentj wizard (?:doctor|diff)(?: [A-Za-z0-9_./=:@-]{1,200}){0,6}"
                          r"(?: 2>&1)?(?: \| grep -v [^\s|;&$`'\"<>()\\]{1,40})?")


def staging_write(root: str | None, tool: str, tool_input) -> bool:
    """A main-Agent write into `<root>/.agentj/wizard-staging/` is inert (nothing reads it until `agentj wizard apply`, which
    validates every file and never overwrites what the owner changed): no card for it."""
    if not root or tool not in BRIEF_WRITE_TOOLS or not isinstance(tool_input, dict):
        return False
    fp = tool_input.get("file_path")
    if not isinstance(fp, str) or not fp or str(tool_input.get("new_string") or "").startswith("files: "):
        return False
    base = os.path.realpath(os.path.join(root, ".agentj", "wizard-staging"))
    real = os.path.realpath(fp if os.path.isabs(fp) else os.path.join(root, fp))
    return real.startswith(base + os.sep)


def host_mediated(tool: str, tool_input) -> bool:
    """`agentj capability …` itself needs no permission card: every effect it has is gated by the host (the opening card is
    the owner's signed decision; dispatch checks receipts; the rest reads). Exactly one plain command — no pipe, redirect,
    chain, substitution or quote (anything else goes through the usual card)."""
    if tool != "Bash" or not isinstance(tool_input, dict):
        return False
    cmd = tool_input.get("command")
    return isinstance(cmd, str) and bool(_HOST_MEDIATED.fullmatch(cmd.strip()) or _WIZARD_READ.fullmatch(cmd.strip()))


# ------------------------------------------------------------------ serve side
class Capabilities:
    """Owned by serve.Host: capability.sock, the open cards (≤ 4), answers from phones, dispatch."""

    MAX_OPEN = 4

    def __init__(self, host):
        self.host = host
        self.cards: dict[str, dict] = {}
        self.server = None
        self.adapters: dict = {}                # tests: stand-in adapters (offline CI)

    @property
    def st(self):
        return self.host.st

    @property
    def path(self) -> pathlib.Path:
        return self.st.perm_dir / SOCK_NAME

    def cfg(self) -> dict | None:
        return self.host.agent_cfg

    async def start(self) -> None:
        st = self.st
        # a card shown by a previous serve died with it (its nonce lived in memory): remembered as gone
        with _locked(st):
            d = _read(st, "cards.json", {})
            for k, v in d.items():
                if isinstance(v, dict) and v.get("status") in ("needs_input", "verifying"):
                    v["status"], v["result"] = "expired", "restart"
            _write(st, "cards.json", d)
        st.perm_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(st.perm_dir, 0o700)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        old = os.umask(0o077)
        try:
            self.server = await asyncio.start_unix_server(self.on_client, path=str(self.path), limit=REQ_MAX)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        os.environ[ENV] = str(self.path)
        self.host_task = asyncio.create_task(self._initial_sync())

    async def _initial_sync(self) -> None:
        """Discovery only at start (no network, no process): the checks run when a card, dispatch or `show` needs them."""
        with contextlib.suppress(Exception):
            await asyncio.to_thread(sync, self.st, self.cfg(), probe=False)

    async def stop(self) -> None:
        if os.environ.get(ENV) == str(self.path):
            os.environ.pop(ENV, None)
        for c in list(self.cards.values()):
            self._remember(c, status="expired", result="gone")
        self.cards.clear()
        if self.server:
            self.server.close()
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()

    # ---------------------------------------------------------- remembered card outcomes (cards.json, no picks / text)
    def _remember(self, c: dict, **kw) -> None:
        with _locked(self.st):
            d = _read(self.st, "cards.json", {})
            rec = d.get(c["id"], {}) if isinstance(d.get(c["id"]), dict) else {}
            rec.update({"wf": c["wf"], "task": c["pf"]["task_id"], "at": int(time.time()),
                        "expires": c["pf"]["expires_at"], "brief_digest": c["pf"]["brief_digest"]}, **kw)
            d[c["id"]] = rec
            if len(d) > CARDS_KEEP:
                for k in sorted(d, key=lambda k: d[k].get("at", 0))[:len(d) - CARDS_KEEP]:
                    d.pop(k, None)
            _write(self.st, "cards.json", d)

    def card_state(self, cid: str) -> dict | None:
        r = _read(self.st, "cards.json", {}).get(cid)
        return r if isinstance(r, dict) else None

    # ---------------------------------------------------------- phone frames
    def card_msg(self, c: dict) -> dict:
        pf = c["pf"]
        b = c["brief"]
        return {"t": "lt_card", "kind": "capability_preflight", "id": c["id"], "card": pf, "asks": c["asks"],
                "brief": {k: b[k] for k in ("goal", "acceptance", "red_lines", "deliverables", "plan", "workflow", "title",
                                            "schedule", "tz", "scope_digest", "confirm")},
                "enable": c["enable"], "ttl": max(0, int((parse_iso(pf["expires_at"]) or 0) - time.time()))}

    def _lt_sessions(self):
        return [s for s in self.host.sessions.values()
                if s.state == "ready" and getattr(s, "lt1", False) and self.st.is_allowed(s.pub)]

    async def _show(self, c: dict) -> int:
        n = 0
        for s in self._lt_sessions():
            if await self.host.send_app(s, self.card_msg(c)):
                n += 1
        old = [s for s in self.host.sessions.values() if s.state == "ready" and not getattr(s, "lt1", False)
               and self.st.is_allowed(s.pub)]
        for s in old:                     # an older phone page: one plain line, never a silent confirmation
            await self.host.send_app(s, {"t": "msg", "id": secrets.token_hex(8), "ts": int(time.time() * 1000),
                                         "seq": getattr(self.host, "seq", 0), "from": "notice",
                                         "text": "电脑上有一张开工确认卡：请刷新手机页面（更新到 0.18）后确认。/ A start card is "
                                                 "waiting: reload this page (0.18) to confirm it."})
        return n

    async def on_ready(self, s) -> None:
        if getattr(s, "lt1", False):
            for c in list(self.cards.values()):
                if not c["done"]:
                    await self.host.send_app(s, self.card_msg(c))

    async def show_capabilities(self, lang: str = "zh") -> dict:
        reg = await asyncio.to_thread(sync, self.st, self.cfg(), adapters=self.adapters)
        items = summary(reg, lang)
        msg = {"t": "lt_caps", "kind": "capability_snapshot", "revision": reg["revision"], "items": items}
        n = 0
        for s in self._lt_sessions():
            if await self.host.send_app(s, msg):
                n += 1
        return {"result": "shown" if n else "no_phone", "phones": n, "revision": reg["revision"], "items": items}

    # ---------------------------------------------------------- prepare / answer
    async def prepare(self, wf: str, asks) -> dict:
        if self.host.stopped():
            raise CapError("stopped", "Stop everything is on")
        if not isinstance(wf, str) or not _ID.fullmatch(wf):
            raise CapError("shape", "workflow id")
        asks = norm_asks(asks)
        plan = await asyncio.to_thread(plan_card, self.st, self.cfg(), wf, asks, adapters=self.adapters)
        pf = plan["preflight"]
        if not plan["need_card"]:
            audit(self.st, "preflight", wf_sha=sha(wf)[:16], result="ready_no_card")
            return {"result": "ready", "missing": plan["missing"], "card": None}
        if not self.host._approvers():
            raise CapError("no_device", "no paired phone that can sign")
        for c in list(self.cards.values()):          # a newer card for the same workflow replaces the open one
            if c["wf"] == wf and not c["done"]:
                c["done"] = True
                self.cards.pop(c["id"], None)
                self._remember(c, status="cancelled", result="replaced")
                await self.host._send_ready(lambda s, c=c: {"t": "lt_done", "id": c["id"], "status": "cancelled"})
        if len([c for c in self.cards.values() if not c["done"]]) >= self.MAX_OPEN:
            raise CapError("busy", "too many open cards")
        c = {"id": pf["card_id"], "wf": wf, "pf": pf, "asks": plan["asks"], "brief": plan["brief"], "enable": plan["enable"],
             "dir": plan["dir"], "done": False, "missing": plan["missing"]}
        self.cards[c["id"]] = c
        self._remember(c, status="needs_input", result="pending", missing=plan["missing"])
        audit(self.st, "preflight_card", card=c["id"], required=len(pf["required"]), optional=len(pf["optional"]),
              confirm=plan["brief"]["confirm"], enable=bool(plan["enable"]))
        self.host.activity("lt_card", id=wf, title=(plan["brief"].get("title") or {}).get("zh") if isinstance(plan["brief"].get("title"), dict) else wf)
        shown = await self._show(c)
        self.host.push_notify("ask")
        return {"result": "shown" if shown else "waiting_phone", "card": c["id"], "ttl": CARD_TTL,
                "required": [r["id"] for r in pf["required"]], "optional": [r["id"] for r in pf["optional"]],
                "confirm": plan["brief"]["confirm"], "enable": bool(plan["enable"]), "missing": plan["missing"]}

    def _finish_card(self, c: dict) -> None:
        c["done"] = True
        self.cards.pop(c["id"], None)

    async def on_phone(self, s, obj: dict) -> None:
        cid = obj.get("id")
        c = self.cards.get(cid) if isinstance(cid, str) and _CARD.fullmatch(cid) else None
        if c is None or c["done"]:
            audit(self.st, "preflight_refused", card=cid if isinstance(cid, str) and len(cid) <= 64 else None,
                  device=s.device, why="unknown")
            return await self.host.send_app(s, {"t": "lt_res", "id": cid if isinstance(cid, str) else "", "ok": False, "why": "unknown"})
        why = self._check(s, c, obj)
        if why:
            audit(self.st, "preflight_refused", card=c["id"], device=s.device, why=why)
            self.st.log("lt_refused", id=c["id"], device=s.device, reason=why)
            return await self.host.send_app(s, {"t": "lt_res", "id": c["id"], "ok": False, "why": why})
        c["pf"]["nonce"] = None                        # one answer per nonce: a replay finds no match
        sig_sha = sha(obj["sig"])[:32]
        if obj["action"] == "cancel":
            self._finish_card(c)
            self._remember(c, status="cancelled", result="cancelled", device=s.device)
            audit(self.st, "preflight_answer", card=c["id"], device=s.device, action="cancel", sig=sig_sha)
            self.host.activity("lt_cancel", id=c["wf"], by=s.name or s.device)
            await self.host._send_ready(lambda x: {"t": "lt_done", "id": c["id"], "status": "cancelled"})
            return await self.host.send_app(s, {"t": "lt_res", "id": c["id"], "ok": True, "status": "cancelled"})
        # submit: write the owner's choices for the CEO (local, 0600), receipts, then verify again
        self._finish_card(c)
        picks = obj.get("picks") or {}
        audit(self.st, "preflight_answer", card=c["id"], device=s.device, action="submit", sig=sig_sha,
              picks_sha=sha(ltschema.canonical(picks))[:32])
        if picks:
            await asyncio.to_thread(self._write_picks, c, picks)
        if c["brief"]["confirm"]:
            rec = {"mode": "signed", "card_id": c["id"], "device": s.device, "at": iso(), "revision": c["pf"]["revision"],
                   "brief_digest": c["pf"]["brief_digest"], "scope_digest": c["brief"]["scope_digest"],
                   "sig_sha256": sha(obj["bsig"])}
            add_receipt(self.st, c["wf"], rec)
            audit(self.st, "brief_receipt", card=c["id"], device=s.device, scope=c["brief"]["scope_digest"][:32])
        self._remember(c, status="verifying", result="submitted", device=s.device)
        status, missing, enabled = await self._verify_after(c, s, obj)
        self._remember(c, status=status, result="submitted", missing=missing, enabled=enabled)
        self.host.activity("lt_submit", id=c["wf"], by=s.name or s.device, status=status, enabled=enabled)
        done = {"t": "lt_done", "id": c["id"], "status": status, "missing": missing, "enabled": enabled}
        await self.host._send_ready(lambda x: done)
        await self.host.send_app(s, {"t": "lt_res", "id": c["id"], "ok": True, "status": status, "enabled": enabled})

    def _write_picks(self, c: dict, picks: dict) -> None:
        d = pathlib.Path(c["dir"]) / "inputs"
        if d.is_symlink():
            return
        d.mkdir(mode=0o700, exist_ok=True)
        data = json.dumps({"v": 1, "card": c["id"], "at": iso(), "brief_digest": c["pf"]["brief_digest"], "choices": picks},
                          ensure_ascii=False, indent=1).encode()
        tmp = d / f".owner-choices.{secrets.token_hex(4)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, d / "owner-choices.json")

    async def _verify_after(self, c: dict, s, obj: dict) -> tuple[str, list[str], bool]:
        """verifying → ready | needs_input. The schedule is enabled only when every required capability is ready and both
        signatures (brief + task_on) were valid; anything else enables nothing."""
        try:
            plan = await asyncio.to_thread(plan_card, self.st, self.cfg(), c["wf"], [], adapters=self.adapters)
        except CapError as e:
            return "blocked", [e.why], False
        missing = plan["missing"]
        if missing:
            return "needs_input", missing, False
        enabled = False
        if c["enable"]:
            en = obj.get("en")
            from . import controls, tasks
            why = "shape"
            if isinstance(en, dict):
                why = controls.check(self.st, self.host.nonces, self.host.channel, s.device, en, "task_on",
                                     {"id": c["enable"]["id"], "tsha": c["enable"]["tsha"]})
            if why:
                controls.log(self.st, action="task_on", device=s.device, result="refused:" + why)
                audit(self.st, "enable_refused", card=c["id"], device=s.device, why=why)
                return "needs_input", ["enable:" + why], False
            try:
                wd = str(working_root(self.cfg()))
                await asyncio.to_thread(tasks.set_enabled, self.st, wd, c["enable"]["id"], True, "phone:" + s.device,
                                        c["enable"]["tsha"])
                enabled = True
                controls.log(self.st, action="task_on", device=s.device, result="ok", obj=c["enable"], sig=en.get("sig"),
                             n=en.get("n"), ts=en.get("ts"))
                self.host.activity("task_on", by=s.name or s.device, id=c["enable"]["id"])
                self.host.scheduler.wake.set()
            except tasks.TaskError as e:
                controls.log(self.st, action="task_on", device=s.device, result=e.reason)
                return "needs_input", ["enable:" + e.reason], False
        return "ready", [], enabled

    def _check(self, s, c: dict, obj: dict) -> str | None:
        action, sig = obj.get("action"), obj.get("sig")
        if action not in ("submit", "cancel") or not isinstance(sig, str) or len(sig) > 100:
            return "shape"
        if obj.get("n") != c["pf"]["nonce"] or c["pf"]["nonce"] is None:
            return "replay"
        if (parse_iso(c["pf"]["expires_at"]) or 0) <= time.time():
            self._finish_card(c)
            self._remember(c, status="expired", result="timeout")
            return "expired"
        sk = self.st.sign_key(s.device)
        if not sk:
            return "no_key"
        picks = obj.get("picks") if action == "submit" else {}
        if action == "submit" and c["asks"] and (why := check_picks(c["asks"], picks)):
            return why
        if action == "submit" and not c["asks"]:
            picks = {}
            if obj.get("picks") not in (None, {}):
                return "picks"
        if not verify_sig(sk, sig, preflight_binding(self.host.channel, s.device, c["pf"], action, picks)):
            return "bad_signature"
        if action == "submit" and c["brief"]["confirm"]:
            if not verify_sig(sk, obj.get("bsig"), brief_binding(self.host.channel, s.device, c["pf"],
                                                                  c["brief"]["scope_digest"], "submit")):
                return "bad_signature"
        # the brief on disk must still be the one shown
        raw = brief_raw(c["dir"])
        if raw is None or brief_digest(raw) != c["pf"]["brief_digest"]:
            return "changed"
        return None

    async def result_of(self, cid, wait: bool) -> dict:
        if not isinstance(cid, str) or not _CARD.fullmatch(cid):
            raise CapError("shape", "card id: 32 hex characters")
        stop_at = time.monotonic() + CARD_TTL + 30
        while True:
            r = self.card_state(cid)
            if r is None:
                return {"result": "unknown", "card": cid}
            if r.get("status") not in ("needs_input", "verifying") or not wait or time.monotonic() > stop_at:
                return {"result": r.get("status"), "card": cid, **{k: r[k] for k in ("missing", "enabled") if k in r}}
            await asyncio.sleep(0.5)

    # ---------------------------------------------------------- dispatch
    async def dispatch(self, wf: str) -> dict:
        """Brief valid + confirmed + required capabilities ready + not already queued / running → the Scheduler runs it."""
        if self.host.stopped():
            raise CapError("stopped", "Stop everything is on")
        if not isinstance(wf, str) or not _ID.fullmatch(wf):
            raise CapError("shape", "workflow id")
        sch = self.host.scheduler
        if sch.current_id == wf or any(t == wf for t, _ in sch.waiting):
            audit(self.st, "dispatch", wf_sha=sha(wf)[:16], result="already_running")
            return {"result": "already_running", "workflow": wf, "running": sch.current_id == wf, "next": "end_turn",
                    "detail": END_TURN}
        plan = await asyncio.to_thread(plan_card, self.st, self.cfg(), wf, [], adapters=self.adapters)
        b = plan["brief"]["full"]
        if b["confirmation"]["mode"] == "signed" and plan["brief"]["confirm"]:
            raise CapError("unconfirmed", "the owner has not confirmed this brief: prepare a card")
        if b["confirmation"]["mode"] == "requested" and not existing_workflow(self.st, wf):
            raise CapError("unconfirmed", "a new workflow needs the owner's signed confirmation (confirmation.mode=signed)")
        if plan["missing"]:
            raise CapError("not_ready", "required capabilities are not ready", missing=plan["missing"])
        pending = [c for c in self.cards.values() if c["wf"] == wf and not c["done"]]
        if pending:
            raise CapError("not_ready", "an opening card for this workflow is still open", card=pending[0]["id"])
        with _locked(self.st):
            d = _read(self.st, "dispatch.json", {})
            d[wf] = {"at": iso(), "brief_digest": plan["preflight"]["brief_digest"], "pid": os.getpid(), "status": "queued"}
            _write(self.st, "dispatch.json", d)
        if b["confirmation"]["mode"] == "requested":
            add_receipt(self.st, wf, {"mode": "requested", "request_id": b["confirmation"]["request_id"], "at": iso(),
                                      "revision": b["revision"], "brief_digest": plan["preflight"]["brief_digest"],
                                      "scope_digest": scope_digest(b)})
        sch.request(wf, "dispatch")
        audit(self.st, "dispatch", wf_sha=sha(wf)[:16], result="queued")
        self.host.activity("lt_dispatch", id=wf)
        return {"result": "queued", "workflow": wf, "next": "end_turn", "detail": END_TURN}

    def run_finished(self, wf: str, res: dict) -> None:
        with contextlib.suppress(Exception), _locked(self.st):
            d = _read(self.st, "dispatch.json", {})
            if wf in d:
                d[wf].update(status="done", verdict=res.get("verdict"), report_check=res.get("report_check"))
                _write(self.st, "dispatch.json", d)
        # the owner is waiting in this conversation: the main Agent reads the report back and answers (design: 「主 Agent 读回后
        # 手机交付」); a host-originated turn like the welcome, never shown as the owner's words
        host = self.host
        if getattr(host, "agent", None) is not None and not host.stopped() and not res.get("stopped"):
            with contextlib.suppress(Exception):
                lang = host.onboarding_lang() if hasattr(host, "onboarding_lang") else "zh"
                host.agent.submit(ReadBackSend(readback_prompt(wf, res, lang)))


    # ---------------------------------------------------------- the Agent's socket
    async def on_client(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            sock = w.get_extra_info("socket")
            if sock is not None and hasattr(socket, "SO_PEERCRED"):
                _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    w.close()
                    return
            req = json.loads(await asyncio.wait_for(r.readline(), 10))
            if not isinstance(req, dict):
                raise CapError("shape", "one JSON object")
            res = await self.handle(req)
        except CapError as e:
            res = e.out()
        except (ValueError, asyncio.TimeoutError, asyncio.LimitOverrunError):
            res = {"result": "refused", "why": "shape", "detail": "one JSON line"}
        except (OSError, ConnectionError):
            return
        with contextlib.suppress(OSError, ConnectionError):
            w.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
            await w.drain()
        w.close()

    async def handle(self, req: dict) -> dict:
        t = req.get("t")
        if t == "list":
            reg = await asyncio.to_thread(sync, self.st, self.cfg(), probe=False)
            return {"result": "list", "revision": reg["revision"], "items": summary(reg)}
        if t == "sync":
            reg = await asyncio.to_thread(sync, self.st, self.cfg(), force=bool(req.get("force")), adapters=self.adapters)
            return {"result": "list", "revision": reg["revision"], "items": summary(reg)}
        if t == "check":
            ids = req.get("ids")
            if ids is not None and not (isinstance(ids, list) and all(isinstance(x, str) and _ID.fullmatch(x) for x in ids)):
                raise CapError("shape", "ids")
            reg = await asyncio.to_thread(sync, self.st, self.cfg(), ids=ids, force=bool(ids), adapters=self.adapters)
            return {"result": "list", "revision": reg["revision"], "items": summary(reg)}
        if t == "show":
            return await self.show_capabilities()
        if t == "prepare":
            return await self.prepare(req.get("workflow"), req.get("asks"))
        if t == "result":
            return await self.result_of(req.get("card"), bool(req.get("wait")))
        if t == "dispatch":
            return await self.dispatch(req.get("workflow"))
        raise CapError("shape", "t: list | sync | check | show | prepare | result | dispatch")


def readback_prompt(wf: str, res: dict, lang: str = "zh") -> str:
    v, line, rc = res.get("verdict"), str(res.get("line") or "")[:300], res.get("report_check") or "—"
    if lang == "en":
        return (f"[Agent J system note, not from the owner; do not repeat it] The run you dispatched to workflow {wf} ended: "
                f"VERDICT {v} — {line}; host report check: {rc}. Read {wf}/reports/report.json and the artifacts it lists, "
                "then tell the owner the result in one or two lines (the host already put the owner deliverables on the phone). "
                "If it did not pass, say exactly what is missing and the next step; never call it done without a passing report.")
    return (f"〔Agent J 系统提示，不是主人说的话，不要复述〕你派给工作流 {wf} 的这次运行结束了：VERDICT {v} — {line}；主机报告检查：{rc}。"
            f"读回 {wf}/reports/report.json 和它列出的产物，用一两句话告诉主人结果（交付文件主机已推到手机）。没通过就说清缺什么、下一步；"
            "没有通过的报告不要说完成。")


def _readback_cls():
    from .compose import Send

    class _ReadBack(Send):
        """Through the Agent's ordinary queue, outside any phone page (turn 0), like onboarding.WelcomeSend."""

        def __init__(self, text: str):
            super().__init__("longtask", "readback", 0, text=text, by="Agent J")
    return _ReadBack


def ReadBackSend(text: str):  # noqa: N802 — a Send built lazily (compose imports nothing of ours)
    return _readback_cls()(text)

# ------------------------------------------------------------------ CLI (runs as the Agent, usually inside the fence)
def client(req: dict, timeout: float = CARD_TTL + 120) -> dict | None:
    """One request on capability.sock → the answer; None when serve is not reachable."""
    path = os.environ.get(ENV)
    if not path or not os.path.exists(path):        # next to the other Agent sockets serve announced (same agentperm/)
        for other in ("AGENTJ_ELEVATE_SOCK", "AGENTJ_RECALL_SOCK", "AGENTJ_FRIENDS_SOCK"):
            sib = os.environ.get(other)
            if sib and os.path.exists(os.path.join(os.path.dirname(sib), SOCK_NAME)):
                path = os.path.join(os.path.dirname(sib), SOCK_NAME)
                break
    if not path:
        with contextlib.suppress(Exception):
            from .state import State
            path = str(State().perm_dir / SOCK_NAME)
    if not path or not os.path.exists(path):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf)
    except (OSError, ValueError):
        return None


def _local(req: dict) -> dict:
    from .state import State
    st = State()
    cfg = st.agent_config()
    if req["t"] == "list":
        reg = sync(st, cfg, probe=False)
    elif req["t"] in ("sync", "check"):
        reg = sync(st, cfg, ids=req.get("ids"), force=bool(req.get("force") or req.get("ids")))
    else:
        raise CapError("no_serve", "Agent J is not running (agentj service status); this step needs it")
    return {"result": "list", "revision": reg["revision"], "items": summary(reg)}


def _call(req: dict) -> dict:
    res = client(req)
    if res is not None:
        return res
    try:
        return _local(req)
    except CapError as e:
        return e.out()


def _print(res: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif res.get("result") == "list":
        print(f"capability registry r{res['revision']} — {len(res['items'])} entries")
        for it in res["items"]:
            mark = {"ready": "✓", "stale": "~", "unverified": "?", "discovered": "?"}.get(it["status"], "✗")
            print(f" {mark} {it['id']:<32} {it['kind']:<10} {it['status']:<11} {it['title']['zh']} / {it['title']['en']}")
    else:
        print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("result") not in ("refused", "unknown") else 2


def cmd_capability(a) -> int:
    sub = a.cap_cmd
    if sub == "digest":
        d = pathlib.Path(a.dir or ".").expanduser()
        wf = d / a.workflow if a.workflow else d
        raw = brief_raw(wf)
        if raw is None:
            return _print({"result": "refused", "why": "missing", "detail": "brief.json"}, a.json)
        try:
            b = json.loads(raw)
        except ValueError:
            return _print({"result": "refused", "why": "invalid", "detail": "not JSON"}, a.json)
        probs = ltschema.problems("brief", b) or brief_problems(b, wf.name)
        return _print({"result": "digest", "brief_digest": brief_digest(raw),
                       "scope_digest": scope_digest(b) if isinstance(b, dict) else None, "problems": probs}, a.json)
    if sub == "report-check":
        d = pathlib.Path(a.dir or ".").expanduser()
        wf = d / a.workflow if a.workflow else d
        b, probs = load_brief(wf)
        if b is None or probs:
            return _print({"result": "refused", "why": "invalid", "detail": probs[0] if probs else "brief.json"}, a.json)
        code, detail = report_check(wf, b, brief_raw(wf), a.verdict)
        return _print({"result": code, "detail": detail}, a.json)
    if sub == "list":
        return _print(_call({"t": "list"}), a.json)
    if sub == "sync":
        return _print(_call({"t": "sync", "force": a.force}), a.json)
    if sub == "check":
        return _print(_call({"t": "check", "ids": a.id or None}), a.json)
    if sub == "show":
        res = client({"t": "show"}) or {"result": "refused", "why": "no_serve", "detail": "Agent J is not running"}
        return _print(res, a.json)
    if sub == "prepare":
        asks = None
        if a.asks:
            try:
                asks = json.loads(pathlib.Path(a.asks).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return _print({"result": "refused", "why": "shape", "detail": "--asks: a JSON file"}, a.json)
        res = client({"t": "prepare", "workflow": a.workflow, "asks": asks}, timeout=120) or \
            {"result": "refused", "why": "no_serve", "detail": "Agent J is not running"}
        return _print(res, a.json)
    if sub == "result":
        res = client({"t": "result", "card": a.card, "wait": a.wait}) or \
            {"result": "refused", "why": "no_serve", "detail": "Agent J is not running"}
        return _print(res, a.json)
    if sub == "dispatch":
        res = client({"t": "dispatch", "workflow": a.workflow}, timeout=120) or \
            {"result": "refused", "why": "no_serve", "detail": "Agent J is not running"}
        return _print(res, a.json)
    return 2


def add_parser(sub) -> None:
    p = sub.add_parser("capability", help="长程任务：能力清单 list · sync · check · show，开工卡 prepare · result，派单 dispatch，"
                                         "digest · report-check / long tasks: capability inventory, opening card, dispatch")
    ps = p.add_subparsers(dest="cap_cmd", required=True)
    x = ps.add_parser("list", help="能力清单（不重新检查）/ the inventory")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("sync", help="发现并检查到期的能力 / discover and re-check")
    x.add_argument("--force", action="store_true")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("check", help="重新检查指定能力 / re-check entries")
    x.add_argument("--id", action="append", help="能力 id（可多次）")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("show", help="把能力页发到主人手机 / show it on the phone")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("prepare", help="开工卡：预检任务书并在手机上集中确认 / one opening card on the phone")
    x.add_argument("--workflow", required=True)
    x.add_argument("--asks", help="主人需要做的选择（JSON 文件，≤5 项）/ the owner's choices, a JSON file")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("result", help="开工卡结果 / the card's outcome")
    x.add_argument("card")
    x.add_argument("--wait", action="store_true")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("dispatch", help="把任务书派给工作流 CEO / hand the brief to its CEO")
    x.add_argument("--workflow", required=True)
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("digest", help="任务书摘要 / brief digests")
    x.add_argument("--workflow", help="工作流文件夹名（相对 --dir）")
    x.add_argument("--dir", help="工作根目录或工作流目录（默认当前目录）")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("report-check", help="检查 CEO 报告 / check reports/report.json")
    x.add_argument("--workflow", help="工作流文件夹名（相对 --dir）")
    x.add_argument("--dir", help="工作根目录或工作流目录（默认当前目录）")
    x.add_argument("--verdict", choices=["ok", "attention", "fail"])
    x.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_capability)
