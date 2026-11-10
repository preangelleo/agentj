"""ADR-0305-plaza-python: a plaza package declares Python ≥ 3.N, the self-check PATH has none → after the owner's signed card the
host (outside every sandbox) prepares one with uv in Agent J's own state folder; the self-check sandbox only reads it.

Layout (all 0700, created here, never a link): <state>/plaza/python/{interpreters, cache, home, envs/<name>-<sha16>/venv}.
uv runs with a clean environment (no credential, UV_NO_CONFIG, its cache / config / bin folders inside this tree), stdin
closed, a time limit, output kept only as a redacted tail. Wheels only (`--only-binary :all:`), and only for an officially
signed package whose requirements file is plain `name[extras] <specifiers> [; marker]` lines."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

PREFERRED = 12              # the minor asked of uv when the package's floor is lower (a current, well-supported Python)
MAX_MINOR = 20              # the highest floor uv is asked for (min_python reads at most two digits)
BUDGET = 240.0              # s, one preparation: the install request's CLI must still be waiting when it ends
STEP = {"python": 180.0, "venv": 60.0, "pip": 180.0}
KEEP = 2                    # prepared environments kept per package name
NAME = re.compile(r"Python ≥ 3\.(\d{1,2})")
_KEY = re.compile(r"[a-z0-9][a-z0-9-]{1,39}-[0-9a-f]{16}")
_CPY = re.compile(r"cpython-3\.(\d{1,2})\.(\d{1,3})[^/]*")
_REQ = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}(?:\[[A-Za-z0-9._, -]{1,100}\])?\s*"
                  r"(?:(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]{1,40}\s*(?:,\s*(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]{1,40}\s*)*)?"
                  r"(?:;(?!\s*-)(?![^\n]*\s-)\s*[A-Za-z0-9_.'\" <>=!~(),-]{1,200})?")      # F5: no ` -option` hidden in a marker
_PKG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_FILE = re.compile(r"[A-Za-z0-9._/-]{1,120}")
_PASS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
         "SSL_CERT_FILE", "SSL_CERT_DIR")

# owner-facing words (card, preview, CLI receipt): zh / en pairs
CARD = ("将由 Agent J 用 uv 为这个包准备 Python ≥ 3.{n}（需要联网下载，放在 Agent J 自己的目录，不改系统 Python）",
        "Agent J will prepare Python ≥ 3.{n} for this package with uv (downloaded from the internet, kept in Agent J's own "
        "folder; the system Python is not changed)")
CARD_REQ = ("，并从 PyPI 下载安装包声明的 Python 依赖（{f}）：{names}（只装现成的 wheel，不从源码构建）",
            ", and download from PyPI and install the Python dependencies the package declares ({f}): {names} (ready-made "
            "wheels only, nothing built from source)")
CARD_MORE = ("…等 {n} 个", "… {n} in all")
CARD_NAMES = 5              # F4: package names shown on the card (each ≤ 40 chars)
CARD_REQ_NO = ("；包声明的 Python 依赖（{f}）不自动安装：只有 Agent J 官方签名的包才自动装",
               "; the package's declared Python dependencies ({f}) are not installed automatically: only for packages with "
               "the Agent J official signature")
AGENT = ("这个{k}请用 {p} 运行 python3 脚本（Agent J 准备的 Python {v}）",
         "Run this {k}'s python3 scripts with {p} (the Python {v} Agent J prepared)")
REQ_STATE = {"uncertified": ("包声明的 Python 依赖没有安装：只有 Agent J 官方签名的包才自动装",
                             "the package's declared Python dependencies were not installed: only for officially signed packages"),
             "refused": ("包声明的 Python 依赖没有安装：依赖文件里有不是「包名 版本」的行（网址、路径或选项）",
                         "the package's declared Python dependencies were not installed: the file has lines that are not "
                         "plain 'name version' requirements (a URL, a path or an option)"),
             "missing": ("包声明的 Python 依赖没有安装：包里没有这个文件", "the declared Python dependencies file is not in the package"),
             "not_prepared": ("包声明的 Python 依赖没有安装：Agent J 没能用 uv 准备 Python 环境（重跑 agentj plaza verify 会再试）",
                              "the package's declared Python dependencies were not installed: Agent J could not prepare a Python "
                              "environment with uv (agentj plaza verify tries again)")}


def label(want) -> str:
    return f"Python ≥ {want[0]}.{want[1]}"


def file_label(f) -> str:
    return f if isinstance(f, str) and _FILE.fullmatch(f) and ".." not in f.split("/") else "requirements"


def enabled() -> bool:
    return os.environ.get("AGENTJ_PLAZA_UV", "on") != "off"          # tests: off (tests/_hermetic.py); a test turns it on


def uv_binary() -> str | None:
    """update.installer_binary('uv') (the same uv the upgrade uses), only as a real file owned by root or this user and not
    writable by group / others."""
    if not enabled():
        return None
    from .update import installer_binary
    p = installer_binary("uv")
    p = p if os.path.isabs(p) else shutil.which(p)
    if not p:
        return None
    try:
        r = os.path.realpath(p)
        s = os.stat(r)
    except OSError:
        return None
    if not (os.path.isfile(r) and os.access(r, os.X_OK) and s.st_uid in (0, os.getuid()) and not s.st_mode & 0o022):
        return None
    return r


def base(st):
    return st.root / "plaza" / "python"


def _private_dir(p) -> bool:
    """mkdir 0700 (parents too); then it must be a real directory of this user, nobody else may write or read it."""
    try:
        os.makedirs(p, mode=0o700, exist_ok=True)
        s = os.lstat(p)
        if not (os.path.isdir(p) and not os.path.islink(p) and s.st_uid == os.getuid()):
            return False
        if s.st_mode & 0o077:
            os.chmod(p, 0o700)
        return True
    except OSError:
        return False


def _under(p: str, root: str) -> bool:
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def redact(text: str, st) -> str:
    t = (text or "")[-1200:]
    for real, short in ((os.path.realpath(str(base(st))), "<agentj>/plaza/python"), (str(base(st)), "<agentj>/plaza/python"),
                        (os.path.realpath(os.path.expanduser("~")), "~"), (os.path.expanduser("~"), "~")):
        if real and real != "/":
            t = t.replace(real, short)
    t = re.sub(r"[^\S\n]+", " ", re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", t))
    return "\n".join(t.splitlines()[-6:])[-400:]


def uv_env(st) -> dict:
    b = os.path.realpath(str(base(st)))
    home = os.path.join(b, "home")
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "LANG": "C.UTF-8", "UV_NO_CONFIG": "1", "UV_NO_PROGRESS": "1",
           "UV_CACHE_DIR": os.path.join(b, "cache"), "UV_PYTHON_INSTALL_DIR": os.path.join(b, "interpreters"),
           "UV_PYTHON_BIN_DIR": os.path.join(b, "interpreters", "bin"), "UV_PYTHON_INSTALL_BIN": "0",
           "UV_TOOL_DIR": os.path.join(home, "tools"), "UV_TOOL_BIN_DIR": os.path.join(home, "bin"),
           "XDG_CONFIG_HOME": os.path.join(home, "config"), "XDG_CACHE_HOME": os.path.join(home, "cache"),
           "XDG_DATA_HOME": os.path.join(home, "data"), "XDG_BIN_HOME": os.path.join(home, "bin"), "TMPDIR": os.path.join(home, "tmp")}
    for k in _PASS:          # a proxy / CA bundle the network needs; never one that carries a user:password
        v = os.environ.get(k)
        if v and len(v) <= 500 and "@" not in v and "\n" not in v:
            env[k] = v
    return env


def _run(argv, env, timeout) -> tuple[str, str]:
    """→ ("ok" | "failed" | "timeout", raw output tail)."""
    if timeout <= 0:
        return "timeout", ""
    try:
        p = subprocess.Popen(argv, env=env, cwd=env["HOME"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as e:
        return "failed", f"cannot run uv ({e.strerror})"
    try:
        o, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, 9)
        except OSError:
            pass
        o, _ = p.communicate()
        return "timeout", (o or b"")[-2000:].decode("utf-8", "replace")
    return ("ok" if p.returncode == 0 else "failed"), (o or b"")[-2000:].decode("utf-8", "replace")


def interpreters(st, want) -> list[tuple[tuple, str]]:
    """uv-installed CPython ≥ want in our interpreters folder → [((3, minor, patch), python3 path)], newest first."""
    root = os.path.realpath(os.path.join(str(base(st)), "interpreters"))
    out = []
    for d in glob.glob(os.path.join(root, "cpython-3.*")):
        m = _CPY.fullmatch(os.path.basename(d))
        if not m or os.path.islink(d) or (3, int(m.group(1))) < tuple(want):
            continue
        exe = os.path.join(d, "bin", "python3")
        r = os.path.realpath(exe)
        if os.path.isfile(r) and os.access(r, os.X_OK) and _under(r, os.path.realpath(d)):
            out.append(((3, int(m.group(1)), int(m.group(2))), exe))
    return sorted(out, reverse=True)


def req_lines(data: bytes) -> list[str] | None:
    """Plain requirement lines (comments / blanks dropped); None when any line is an option, a URL, a path or unreadable."""
    try:
        text = data.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return None
    out = []
    for raw in text.splitlines():
        s = raw.split(" #", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not s:
            continue
        if not _REQ.fullmatch(s) or "://" in s or s.startswith(("-", ".", "/")):
            return None
        out.append(s)
    return out[:200]


def interp_dir(b: str, d) -> str | None:
    """F2: a uv CPython folder we may run / expose — the real `<base>/interpreters/cpython-3.N.P-…` directory itself (never
    the `cpython-3.N-…` minor-version link uv also makes, never a link, ours) → its path, else None."""
    if not isinstance(d, str) or not os.path.isabs(d):
        return None
    root = os.path.join(b, "interpreters")
    try:
        st = os.lstat(d)
    except OSError:
        return None
    if (os.path.islink(d) or not os.path.isdir(d) or st.st_uid != os.getuid() or os.path.realpath(d) != os.path.normpath(d)
            or os.path.dirname(os.path.normpath(d)) != root or not _CPY.fullmatch(os.path.basename(d))):
        return None
    return os.path.normpath(d)


def _chain(path: str, roots) -> str | None:
    """P1: follow `path` link by link; every name on the way (and every folder holding it) must already be a real path inside
    one of `roots` — the sandbox only shows those, so a hop through uv's minor-version link would be ENOENT there → the final
    file, else None."""
    p = os.path.normpath(path)
    for _ in range(16):
        parent = os.path.dirname(p)
        if os.path.realpath(parent) != parent or not any(_under(p, r) for r in roots):
            return None
        if not os.path.islink(p):
            return p if os.path.isfile(p) and os.access(p, os.X_OK) else None
        try:
            p = os.path.normpath(os.path.join(parent, os.readlink(p)))
        except OSError:
            return None
    return None


def venv_ok(env_dir: str, interp: str) -> bool:
    """P1: the venv's python3 and pyvenv.cfg `home` resolve inside the two exposed folders without passing a link outside them."""
    if not _chain(os.path.join(env_dir, "venv", "bin", "python3"), (env_dir, interp)):
        return False
    try:
        with open(os.path.join(env_dir, "venv", "pyvenv.cfg"), encoding="utf-8") as f:
            cfg = f.read(8192)
    except (OSError, UnicodeDecodeError):
        return False
    home = [ln.split("=", 1)[1].strip() for ln in cfg.splitlines() if ln.split("=", 1)[0].strip() == "home" and "=" in ln]
    return len(home) == 1 and os.path.isabs(home[0]) and os.path.realpath(home[0]) == os.path.normpath(home[0]) \
        and _under(os.path.normpath(home[0]), interp)


def exposure(b: str, env_dir: str) -> dict | None:
    """The one finished environment folder `<base>/envs/<key>` (ready.json written last) and the one interpreter folder it
    uses, both checked (F2, P1) → {expose: [env_dir, interp], record}, else None. The self-check sandbox and the Agent's fence
    mount exactly these two, read-only."""
    env_dir = os.path.normpath(env_dir)
    try:
        if (os.path.dirname(env_dir) != os.path.join(b, "envs") or not _KEY.fullmatch(os.path.basename(env_dir))
                or os.path.islink(env_dir) or not os.path.isdir(env_dir) or os.lstat(env_dir).st_uid != os.getuid()):
            return None
        p = os.path.join(env_dir, "ready.json")
        st = os.lstat(p)
        if not os.path.isfile(p) or os.path.islink(p) or st.st_uid != os.getuid() or st.st_size > 8192:
            return None
        with open(p, encoding="utf-8") as f:
            r = json.loads(f.read())
    except (OSError, ValueError):
        return None
    interp = interp_dir(b, r.get("interpreter_dir")) if isinstance(r, dict) else None
    if not interp or not isinstance(r.get("version"), str) or not venv_ok(env_dir, interp):
        return None
    return {"expose": [env_dir, interp], "record": r}


def fence_dirs(st, limit: int = 32) -> list[str]:
    """GAP-0305-1: the folders the Agent's fence mounts read-only — for each installed.json record whose self-check ran with
    the Python Agent J prepared (verify.python.source == agentj_uv), its env folder and interpreter folder (exposure())."""
    try:
        from .market import read_installed
        recs = read_installed(st)
        b = os.path.realpath(str(base(st)))
    except Exception:  # noqa: BLE001 - the fence never fails on plaza state
        return []
    out = []
    for r in recs:
        py = (r.get("verify") or {}).get("python") if isinstance(r.get("verify"), dict) else None
        path = py.get("path") if isinstance(py, dict) and py.get("source") == "agentj_uv" else None
        if not isinstance(path, str) or not os.path.isabs(path):
            continue
        env_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.normpath(path))))
        if os.path.join(env_dir, "venv", "bin", "python3") != os.path.normpath(path):
            continue
        ex = exposure(b, env_dir)
        if ex:
            out += ex["expose"]
    return list(dict.fromkeys(out))[:limit]


def _ready(env_dir: str, want, req_sha) -> dict | None:
    """A finished environment (ready.json written last) that still matches: same floor, same requirements, checked exposure."""
    b = os.path.dirname(os.path.dirname(env_dir))
    ex = exposure(b, env_dir)
    r = ex["record"] if ex else None
    if not r or r.get("want") != list(want) or r.get("requirements") != req_sha:
        return None
    return {"ok": True, "path": os.path.join(env_dir, "venv", "bin", "python3"), "version": r["version"], "expose": ex["expose"],
            "requirements": r.get("req_state"), "reused": True}


def prepare(st, name: str, sha: str, want, *, req=None, certified=False, budget=BUDGET, probe=None) -> dict:
    """→ {ok: True, path, version, expose: [dirs], requirements: installed|uncertified|refused|missing|None, reused}
    | {ok: False, why: off|no_uv|failed|timeout|unsafe|too_new, step, tail (redacted)}. `req` = (file name, bytes | None).
    `probe(result) → bool` (P1): the python asked inside the very sandbox prefix that will run it; a reused environment
    that fails it is removed and built again, a new one that fails it is removed and never marked ready."""
    if not enabled():
        return {"ok": False, "why": "off", "tail": ""}
    key = f"{name}-{(sha or '')[:16]}"
    if not _KEY.fullmatch(key) or not (isinstance(want, tuple) and len(want) == 2 and want[0] == 3):
        return {"ok": False, "why": "unsafe", "tail": ""}
    if want[1] > MAX_MINOR:
        return {"ok": False, "why": "too_new", "tail": ""}
    lines, req_state = None, None
    if req:
        if not certified:
            req_state = "uncertified"
        elif req[1] is None:
            req_state = "missing"
        else:
            lines = req_lines(req[1])
            req_state = "refused" if lines is None else "installed"
    req_sha = hashlib.sha256("\n".join(lines).encode()).hexdigest() if lines else None
    b = os.path.realpath(str(base(st)))
    env_dir = os.path.join(b, "envs", key)
    good = lambda h: h if h and (probe is None or probe(h)) else None  # noqa: E731
    hit = good(_ready(env_dir, want, req_sha))
    if hit:
        return {**hit, "requirements": req_state}
    uv = uv_binary()
    if not uv:
        return {"ok": False, "why": "no_uv", "tail": ""}
    if not all(_private_dir(os.path.join(b, d)) for d in ("", "interpreters", "cache", "home", "home/tmp", "envs")):
        return {"ok": False, "why": "unsafe", "tail": ""}
    deadline = time.monotonic() + budget
    import fcntl
    try:
        lk = os.open(os.path.join(b, ".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        return {"ok": False, "why": "unsafe", "tail": ""}
    try:
        while True:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    return {"ok": False, "why": "timeout", "tail": ""}
                time.sleep(0.5)
        hit = good(_ready(env_dir, want, req_sha))       # a parallel preparation finished it meanwhile
        if hit:
            return {**hit, "requirements": req_state}
        res = _build(st, uv, env_dir, want, lines, req_sha, req_state, deadline, probe)
    finally:
        os.close(lk)
    st.log("plaza_python", name=name, result="ok" if res["ok"] else res["why"], reason=res.get("step"))   # step: an allowed field
    if res["ok"]:
        _prune(b, name, env_dir)
    return res


def _build(st, uv, env_dir, want, lines, req_sha, req_state, deadline, probe=None) -> dict:
    env = uv_env(st)
    b = os.path.realpath(str(base(st)))
    left = lambda step: min(STEP[step], deadline - time.monotonic())  # noqa: E731
    fail = lambda why, step, tail="": {"ok": False, "why": why, "step": step, "tail": redact(tail, st)}  # noqa: E731
    shutil.rmtree(env_dir, ignore_errors=True)          # never finished (no ready.json), stale or broken: built again
    if not _private_dir(env_dir):
        return fail("unsafe", "dir")
    have = interpreters(st, want)
    if not have:
        why, tail = _run([uv, "python", "install", "--install-dir", env["UV_PYTHON_INSTALL_DIR"], f"3.{max(want[1], PREFERRED)}"],
                         env, left("python"))
        have = interpreters(st, want)
        if why != "ok" or not have:
            return fail(why if why != "ok" else "failed", "python", tail)
    ver, exe = have[0]
    idir = interp_dir(b, os.path.dirname(os.path.dirname(exe)))
    if not idir:
        return fail("unsafe", "python")
    venv = os.path.join(env_dir, "venv")
    # P1: asked by its full key (cpython-3.N.P-<platform>, the real folder's name) and managed-only, uv links the venv to that
    # folder; a path or a bare 3.N would go through uv's cpython-3.N-<platform> minor-version link, which no sandbox shows.
    why, tail = _run([uv, "venv", "--python", os.path.basename(idir), venv],
                     {**env, "UV_PYTHON_DOWNLOADS": "never", "UV_PYTHON_PREFERENCE": "only-managed"}, left("venv"))
    py = os.path.join(venv, "bin", "python3")
    if why != "ok" or not os.path.isfile(os.path.realpath(py)):
        return fail(why if why != "ok" else "failed", "venv", tail)
    if not venv_ok(env_dir, idir):
        shutil.rmtree(env_dir, ignore_errors=True)
        return fail("failed", "venv", "the environment's python is outside the prepared folders")
    if lines:
        rq = os.path.join(env_dir, "requirements.txt")
        fd = os.open(rq, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        why, tail = _run([uv, "pip", "install", "--python", py, "--only-binary", ":all:", "-r", rq],
                         {**env, "UV_PYTHON_DOWNLOADS": "never"}, left("pip"))
        if why != "ok":
            return fail(why, "pip", tail)
    res = {"ok": True, "path": py, "version": ".".join(map(str, ver)), "expose": [env_dir, idir], "requirements": req_state,
           "reused": False}
    if probe is not None and not probe(res):            # P1 (a): it must run in the sandbox before it is called ready
        shutil.rmtree(env_dir, ignore_errors=True)
        return fail("failed", "probe")
    rec = {"v": 1, "want": list(want), "version": res["version"], "interpreter_dir": idir, "requirements": req_sha,
           "req_state": req_state, "at": int(time.time())}
    tmp = os.path.join(env_dir, ".ready.json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    os.replace(tmp, os.path.join(env_dir, "ready.json"))
    return res


def _prune(b, name, keep_dir) -> None:
    seen = []
    for d in glob.glob(os.path.join(b, "envs", f"{name}-*")):
        if _KEY.fullmatch(os.path.basename(d)) and os.path.basename(d)[:-17] == name and not os.path.islink(d):
            try:
                seen.append((os.lstat(d).st_mtime, d))
            except OSError:
                pass
    for _, d in sorted(seen)[:-KEEP]:
        if d != keep_dir:
            shutil.rmtree(d, ignore_errors=True)


def card_line(plan: dict, en: bool) -> str:
    i = 1 if en else 0
    s = CARD[i].format(n=plan["want"][1])
    if plan.get("req") and plan.get("req_ok"):
        names = [n for n in (plan.get("names") or ()) if isinstance(n, str) and _PKG.fullmatch(n)][:CARD_NAMES]
        count = plan.get("count") if isinstance(plan.get("count"), int) else len(names)
        shown = ("、" if not en else ", ").join(n[:40] for n in names) + (CARD_MORE[i].format(n=count) if count > len(names) else "")
        s += CARD_REQ[i].format(f=file_label(plan["req"]), names=shown or "-")
    elif plan.get("req"):
        s += CARD_REQ_NO[i].format(f=file_label(plan["req"]))
    return s


def req_names(lines) -> tuple[list[str], int]:
    """F4: the package-name part of the first CARD_NAMES plain lines (already _REQ-checked), each ≤ 40 chars, redacted."""
    from .privacy import redact
    from .text import clean_line
    out = []
    for ln in (lines or [])[:CARD_NAMES]:
        m = _PKG.match(ln)
        n = clean_line(redact(m.group(0)), 40) if m else ""
        if n and _PKG.fullmatch(n):
            out.append(n)
    return out, len(lines or [])


def consent(want, req, req_ok, lines) -> dict:
    """F1: what the owner's card showed about the uv preparation, kept in installed.json verify.python_prep; `agentj plaza
    verify` prepares only while the current plan is this same one."""
    return {"want": f"{want[0]}.{want[1]}", "req": req or None, "req_ok": bool(req_ok),
            "req_sha": hashlib.sha256("\n".join(lines).encode()).hexdigest() if (req_ok and lines) else None}


def agent_line(py: dict, kind: str, en: bool) -> str:
    k = {"skill": ("技能", "skill"), "workflow": ("工作流", "workflow")}.get(kind, ("包", "package"))[1 if en else 0]
    return AGENT[1 if en else 0].format(k=k, p=py["path"], v=py["version"])


def fix_text(name: str, want, found, prep, en: bool, consent: bool = False) -> str:
    """plaza_deps.task_text: the one line for a missing declared Python (what is there, and one step that adds it). F1: the
    「verify tries again」 step only when the owner's card agreed to the uv preparation (`consent`, installed.json
    verify.python_prep); without it a new card is the way (`agentj plaza request <name> --replace`)."""
    n = f"3.{want[1]}"
    has = (f"the system has {found}" if en else f"系统里找到的是 {found}") if found else ("no Python 3 found" if en else "系统里没找到 Python 3")
    retry = prep in ("failed", "timeout") and consent
    card = prep in ("no_consent", "host_only")
    head = (f"- {label(want)}: not on the self-check PATH ({has})." if en else f"- {label(want)}：自检的系统 PATH 里没有（{has}）。")
    why = {"no_consent": ("the owner's install card did not agree to preparing it with uv", "主人的安装卡没有同意过用 uv 准备它"),
           "host_only": ("it cannot be prepared from inside the Agent's sandbox", "Agent 的沙箱里不能自动准备")}.get(prep, ("", ""))
    if en:
        step = (f"First run `agentj plaza verify {name}`: it tries once more to prepare it automatically with uv (needs the "
                "internet). If that still fails, " if retry else
                f"Agent J can prepare it with uv, but {why[0]}: run `agentj plaza request {name} --replace` (a new card on "
                "the owner's phone; after the yes Agent J prepares it). Or " if card else
                "There is no uv here, so Agent J cannot prepare it: " if prep == "no_uv" else "")
    else:
        step = (f"先运行 `agentj plaza verify {name}`：它会再试一次由 Agent J 用 uv 自动准备（需要联网）。还不行再" if retry else
                f"Agent J 可以用 uv 准备，但{why[1]}：运行 `agentj plaza request {name} --replace`（主人手机上重新弹卡，同意后由 "
                "Agent J 准备）。或者" if card else
                "本机没有 uv，Agent J 没法自动准备：" if prep == "no_uv" else "")
    if sys.platform == "darwin":
        how = (f"download the official installer for Python {n} or newer from python.org (it installs into /usr/local/bin), "
               f"then run `agentj plaza verify {name}`." if en else
               f"从 python.org 下载 Python {n} 或更新版本的官方安装包（装到 /usr/local/bin），然后运行 `agentj plaza verify {name}`。")
    else:
        how = (f"install python{n} (or newer) with the system package manager (e.g. `sudo apt install python{n}`), then run "
               f"`agentj plaza verify {name}`." if en else
               f"用系统包管理器装 python{n} 或更新版本（例如 `sudo apt install python{n}`），然后运行 `agentj plaza verify {name}`。")
    if en and not step:
        how = how[0].upper() + how[1:]
    return head + (" " if en else "") + step + how
