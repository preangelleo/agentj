"""The toolbox (AGENTJ-CORE-SPEC 2.1): the only process of an Agent J Harness session that touches files or runs commands.
`python -P -m agentj.core.toolbox <root>` speaks JSON lines on stdio — {"id", "tool", "args"} → {"id", "ok", "out"} — and
is started inside the fence (fence.wrap, root = the session's project folder) whenever the session is fenced.

Its environment is built from an allow-list (ENV_KEEP), never inherited: the model key, Agent J's sockets and tokens and every
name that looks like a secret stay with the loop in the host process (spec 2.1 "附带的安全收益"; P359 asserts it). Each
session — the main Agent, a CEO, every sub-agent — has its own toolbox, so parallel sub-agents run commands in parallel.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading

ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TZ", "TMPDIR", "XDG_RUNTIME_DIR", "AGENTJ_ORDER_ID")
SECRETISH = re.compile(r"KEY|TOKEN|SECRET|PASS|CRED|AUTH|COOKIE|SESSION", re.I)


def environment(base=None) -> dict:
    env = os.environ if base is None else base
    out = {k: env[k] for k in ENV_KEEP if env.get(k)}
    out.update({k: v for k, v in env.items() if k.startswith("LC_")})
    out.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    out["AGENTJ_CORE_TOOLBOX"] = "1"
    out["GIT_TERMINAL_PROMPT"] = "0"
    return {k: v for k, v in out.items() if not (SECRETISH.search(k) and k not in ENV_KEEP)}


def serve(root: str) -> int:
    from .tools import ToolError, run
    for line in sys.stdin:
        try:
            req = json.loads(line)
            rid = req.get("id")
        except ValueError:
            continue
        try:
            out, ok = run(root, req.get("tool"), req.get("args") or {}), True
        except ToolError as e:
            out, ok = str(e), False
        except TypeError as e:
            out, ok = f"bad arguments: {e}", False
        except Exception as e:  # noqa: BLE001 — a tool bug is the model's error message, never the toolbox's death
            out, ok = f"{type(e).__name__}: {e}", False
        sys.stdout.write(json.dumps({"id": rid, "ok": ok, "out": out}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


class Toolbox:
    """Client side, in the host process. fenced=True wraps the child with fence.wrap (refusing to start if it cannot)."""

    def __init__(self, root: str, *, st=None, fenced: bool = False, environ=None):
        self.root = os.path.realpath(root)
        self.lock = threading.Lock()
        self.next = 0
        argv = [sys.executable, "-P", "-m", "agentj.core.toolbox", self.root]
        env = environment(environ)
        from ..agent import source_root
        src = source_root()
        if src:
            env["PYTHONPATH"] = src
        if fenced:
            from .. import fence
            why = fence.problem(st, self.root)
            if why:
                raise RuntimeError("fence:" + why)
            argv = fence.wrap(st, argv, self.root)
        self.env = env
        self.proc = subprocess.Popen(argv, cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", start_new_session=True)

    def call(self, tool: str, args: dict) -> tuple[bool, str]:
        with self.lock:
            if self.proc.poll() is not None:
                return False, "the toolbox process ended; start a new session"
            self.next += 1
            rid = self.next
            self.proc.stdin.write(json.dumps({"id": rid, "tool": tool, "args": args}, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    return False, "the toolbox process ended"
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("id") == rid:
                    return bool(r.get("ok")), str(r.get("out", ""))

    def close(self) -> None:
        p = self.proc
        if p.poll() is None:
            try:
                os.killpg(p.pid, 9)
            except OSError:
                p.kill()
            p.wait(timeout=5)
        for f in (p.stdin, p.stdout):
            try:
                f.close()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(serve(sys.argv[1]))
