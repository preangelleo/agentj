"""Use the owner-installed Ego Lite CLI only on Agent J task Pages. QR crops stay in memory."""
from __future__ import annotations

import base64
import contextlib
import csv
import json
import math
import os
import pathlib
import re
import secrets
import selectors
import subprocess
import time

from . import instance
from .browser_cdp import CDP, CDPError

try:
    from .browser_cdp import CDPTimeout, MAX_SIDE
except ImportError:
    # A pre-restart upgrade check can still have the predecessor module loaded.
    class CDPTimeout(CDPError):
        pass

    MAX_SIDE = 640  # Preserve the signed browser crop limit in that old process.

BACKEND = "ego-lite"
DOWNLOAD = "https://lite.ego.app/"
HINTS = {
    "ego_install": (
        "可以直接让 Agent 代装 Ego Lite。Agent 会从 https://lite.ego.app/ 下载官方安装包，在你同意后协助安装和准备浏览器资料，"
        "再运行 agentj browser connect，请你在已配对的手机上确认。 / "
        "Ask your Agent to install Ego Lite. It will download the official installer from https://lite.ego.app/, "
        "help with installation and profile setup after you agree, then run agentj browser connect for you to confirm on your paired phone."
    ),
    "ego_not_running": (
        "请在 Agent J 主机上打开 Ego Lite，再让 Agent 运行 agentj browser check。 / "
        "Open Ego Lite on the computer and ask your Agent to run agentj browser check again."
    ),
    "ego_instance": (
        "Ego Lite 目前只供本机第一个 Agent J 席位使用。 / "
        "Ego Lite is available to the first Agent J instance on this computer."
    ),
    "ego_profile_required": (
        "请在 Ego Lite 中准备名为 Agent J 的浏览器资料，再让 Agent 运行 agentj browser connect。请在已配对的手机上确认。 / "
        "Prepare a profile named Agent J in Ego Lite, then ask your Agent to run agentj browser connect. Confirm on your paired phone."
    ),
    "ego_profile_unknown": (
        "Ego Lite 需要一份唯一名为 Agent J 的浏览器资料。准备好后，让 Agent 重试 agentj browser connect。 / "
        "Ego Lite needs exactly one profile named Agent J. When it is ready, ask your Agent to retry agentj browser connect."
    ),
    "ego_wsl_interop": (
        "可以直接让 Agent 代装 Ego Lite 测试版（https://lite.ego.app/），并协助检查 WSL 互操作和 Program Files 中的厂商签名。"
        "准备好后 Agent 会运行 agentj browser connect，请你在已配对的手机上确认。 / "
        "Ask your Agent to install the Ego Lite beta (https://lite.ego.app/) and help check WSL interop and the signed CLI under Program Files. "
        "Once ready, your Agent will run agentj browser connect for you to confirm on your paired phone."
    ),
}
_REPLY = b"AGENTJ_EGO "
_MAX_OUTPUT = 1024 * 1024
_PAGE = re.compile(r"([1-9][0-9]{0,15}):(Agent J [0-9a-f]{16}):(p[1-9][0-9]{0,7}):([A-Za-z0-9_-]{1,128})")
_METHODS = frozenset({
    "Runtime.evaluate", "Network.setCacheDisabled", "Network.setBypassServiceWorker", "Page.navigate",
    "Page.getFrameTree", "Page.reload", "Page.bringToFront", "Page.createIsolatedWorld", "DOM.getFrameOwner",
    "DOM.getBoxModel", "Page.captureScreenshot",
})
_ENV = frozenset({
    "HOME", "USER", "LOGNAME", "TMPDIR", "XDG_RUNTIME_DIR", "SystemRoot",
    "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "WSL_INTEROP", "LANG",
})


def _environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in _ENV}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    return env


def _output(argv: list[str], *, timeout: int = 2) -> str:
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=timeout, check=False, env=_environment())
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.decode("utf-8", "replace").strip() if result.returncode == 0 else ""


MAC_APP = pathlib.Path("/Applications/ego lite.app")
MAC_HELPER = "Contents/Frameworks/ego Framework.framework/Versions/Current/Helpers/ego-browser"
MAC_REQUIREMENT = ('anchor apple generic and identifier "com.citrolabs.ego.ego-browser" '
                   'and certificate leaf[subject.OU] = "JGQLC6YQYJ"')
WINDOWS_TOOLS = "/mnt/c/Windows/System32/"
WINDOWS_CLI = re.compile(r"C:\\Program Files(?: \(x86\))?\\[^\r\n]+\\ego-browser\.exe", re.IGNORECASE)


def _mac_executable() -> str | None:
    try:
        candidate = (MAC_APP / MAC_HELPER).resolve(strict=True)
        if not candidate.is_relative_to(MAC_APP) or not os.access(candidate, os.X_OK):
            return None
        result = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", f"-R={MAC_REQUIREMENT}", str(candidate)],
            capture_output=True, timeout=4, env=_environment(),
        )
        return str(candidate) if result.returncode == 0 else None
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None


def _windows_executable() -> str | None:
    # Read the Windows PATH through the system binary. The candidate must be
    # under Program Files and carry a valid Citro Labs publisher signature.
    for native in _output([WINDOWS_TOOLS + "where.exe", "ego-browser.exe"]).splitlines():
        if not WINDOWS_CLI.fullmatch(native) or any(part in (".", "..") for part in native.split("\\")):
            continue
        path = pathlib.Path(_output(["/usr/bin/wslpath", "-u", native]))
        try:
            real = path.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if real != path or not real.is_file() or not real.is_relative_to("/mnt/c"):
            continue
        literal = native.replace("'", "''")
        script = (
            f"$s = Microsoft.PowerShell.Security\\Get-AuthenticodeSignature -LiteralPath '{literal}'; "
            "if ($s.Status -eq 'Valid' -and "
            "$s.SignerCertificate.GetNameInfo('SimpleName', $false) -eq 'CITRO LABS PTE. LIMITED') { 'valid' }"
        )
        command = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        powershell = WINDOWS_TOOLS + "WindowsPowerShell/v1.0/powershell.exe"
        if _output([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", command], timeout=8) == "valid":
            return str(real)
    return None


def _executable(platform: str) -> str | None:
    if platform == "macos":
        return _mac_executable()
    if platform == "wsl2":
        return _windows_executable()
    return None


def _running_identity(platform: str) -> tuple[str, ...]:
    """Check the already-open desktop app before contacting the vendor CLI."""
    if platform == "macos":
        from .browser import proc_start_time
        pids = _output(["/usr/bin/pgrep", "-u", str(os.geteuid()), "-x", "ego lite"]).splitlines()
        return tuple(sorted(f"{pid}:{started}" for pid in pids if pid.isdigit()
                            and (started := proc_start_time(int(pid)))))
    tasklist = WINDOWS_TOOLS + "tasklist.exe"
    raw = _output([tasklist, "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq ego lite.exe"])
    return tuple(sorted(row[1] for row in csv.reader(raw.splitlines()) if len(row) >= 2
                        and row[0].casefold() == "ego lite.exe" and row[1].isdigit()))


def _left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise CDPTimeout("Ego Lite timed out")
    return left


def _exchange(proc: subprocess.Popen, script: bytes, deadline: float) -> bytes:
    """Cap the vendor's merged stdout/stderr in memory without blocking on either pipe."""
    stdout, stdin = proc.stdout, proc.stdin
    pending = memoryview(script)
    reply = bytearray()
    with selectors.DefaultSelector() as sel:
        sel.register(stdout, selectors.EVENT_READ)
        sel.register(stdin, selectors.EVENT_WRITE)
        for stream in (stdout, stdin):
            os.set_blocking(stream.fileno(), False)
        while sel.get_map():
            for key, _ in sel.select(_left(deadline)):
                if key.fileobj is stdin:
                    pending = pending[os.write(key.fd, pending[:4096]):]
                    if not pending:
                        sel.unregister(stdin)
                        stdin.close()
                else:
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        sel.unregister(stdout)
                    elif len(reply) + len(chunk) > _MAX_OUTPUT:
                        raise CDPError("Ego Lite reply too large")
                    else:
                        reply.extend(chunk)
    return bytes(reply)


def _invoke(cli: str, body: str, *, timeout: float = 4):
    script = ("async function agentjOperation() {\n" + body + "\n}\n"
              "const agentjResult = await agentjOperation();\n"
              "console.log('AGENTJ_EGO ' + JSON.stringify(agentjResult));\n").encode("utf-8")
    deadline = time.monotonic() + timeout
    try:
        with subprocess.Popen([cli, "nodejs"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, env=_environment()) as proc:
            try:
                raw = _exchange(proc, script, deadline)
                code = proc.wait(timeout=_left(deadline))
            except (CDPError, OSError, subprocess.TimeoutExpired):
                proc.kill()
                raise
    except subprocess.TimeoutExpired:
        raise CDPTimeout("Ego Lite timed out") from None
    except OSError:
        raise CDPError("Ego Lite did not answer") from None
    if code == 0:
        replies = [line[len(_REPLY):] for line in raw.splitlines() if line.startswith(_REPLY)]
        if len(replies) == 1:
            with contextlib.suppress(ValueError, UnicodeError):
                return json.loads(replies[0])
    raise CDPError("Ego Lite did not answer")


def _transport(st) -> tuple[dict | None, str | None, bool]:
    from .browser import settings, system
    if not settings(st)["enabled"]:
        return None, "disabled", False
    if instance.current() != 1:
        return None, "ego_instance", False
    platform = system()
    exe = _executable(platform)
    if not exe:
        return None, "ego_wsl_interop" if platform == "wsl2" else "ego_install", False
    running = _running_identity(platform)
    if not running:
        return None, "ego_not_running", True
    return {"backend": BACKEND, "source": BACKEND, "headless": False, "cli": exe,
            "runtime_id": (str(pathlib.Path(exe).resolve()), running)}, None, True


def _profile_file(st) -> pathlib.Path:
    from .browser import bdir
    return bdir(st) / "ego-profile.json"


def _valid_id(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 256 and value.isprintable() and bool(value.strip())


def binding_id(st) -> str | None:
    from .browser import _read_json
    record = _read_json(_profile_file(st))
    ident = record.get("profile_id") if isinstance(record, dict) and record.get("v") == 1 else None
    return ident if _valid_id(ident) else None


def resolve_profile(st, profile_name: str) -> tuple[str | None, str | None]:
    """Resolve the exact profile name without returning any other profile metadata."""
    rt, reason, _ = _transport(st)
    if not rt:
        return None, reason
    try:
        ident = _invoke(rt["cli"], f"""
            const matches = (await profiles()).filter(p => p.name === {_json(profile_name)});
            return matches.length === 1 ? matches[0].id : null;
        """)
    except CDPError:
        return None, "ego_not_running"
    if not _valid_id(ident):
        return None, "ego_profile_unknown"
    return ident, None


def bind_profile(st, ident: str) -> None:
    from .browser import _write_json
    if not _valid_id(ident):
        raise ValueError("invalid Ego Lite profile ID")
    _write_json(st, _profile_file(st), {"v": 1, "profile_id": ident})


def disconnect(st) -> None:
    _profile_file(st).unlink(missing_ok=True)


def _state(st) -> tuple[dict | None, str | None, bool]:
    rt, reason, installed = _transport(st)
    if not rt:
        return None, reason, installed
    profile = binding_id(st)
    if not profile:
        return None, "ego_profile_required", installed
    try:
        exists = _invoke(rt["cli"], f"return (await profiles()).filter(p => p.id === {_json(profile)}).length === 1;")
    except CDPError:
        return None, "ego_not_running", installed
    if exists is not True:
        return None, "ego_profile_required", installed
    return {**rt, "profile_id": profile, "runtime_id": (*rt["runtime_id"], profile)}, None, installed


def runtime(st) -> dict | None:
    return _state(st)[0]


def status(st) -> dict:
    from .browser import system
    rt, reason, installed = _state(st)
    state = "disabled" if reason == "disabled" else "ready" if rt else "attention"
    result = {"ok": state != "attention", "state": state, "enabled": state != "disabled",
              "system": system(), "backend": BACKEND, "source": {"source": BACKEND},
              "installed": {"provider": BACKEND} if installed else None,
              "service": {"manager": "vendor", "installed": False, "running": rt is not None},
              "attention": [reason] if state == "attention" else [],
              "hints": [HINTS[reason]] if reason in HINTS else []}
    if rt:
        result["runtime"] = {"source": BACKEND, "headless": False, "host_login": True}
    return result


def setup(st) -> dict:
    rt, reason, _ = _state(st)
    if rt:
        return {"ok": True, "result": "ready", "source": BACKEND, "backend": BACKEND, "headless": False}
    result = {"ok": False, "result": "attention", "reason": reason, "source": BACKEND, "backend": BACKEND}
    if reason in HINTS:
        result.update(hint=HINTS[reason], download=DOWNLOAD)
    return result


def wake(st) -> dict | None:
    _, reason, _ = _state(st)
    return {"reason": reason, "next": HINTS[reason]} if reason in HINTS else None


def _json(value) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=True)


def _parse(target: str) -> re.Match:
    parsed = _PAGE.fullmatch(target) if isinstance(target, str) else None
    if not parsed:
        raise CDPError("Ego Lite Page unavailable")
    return parsed


_ROOT_FRAME = """
async () => {
  const tree = await page.cdp("Page.getFrameTree", {}, {timeout: 5000});
  const frame = tree.frameTree.frame;
  return {origin: frame.securityOrigin, loader: frame.loaderId, frame: frame.id};
}
"""


def _is_clip(params: dict) -> bool:
    clip = params.get("clip")
    if not isinstance(clip, dict) or set(clip) != {"x", "y", "width", "height", "scale"}:
        return False
    if not all(type(n) in (int, float) and math.isfinite(n) for n in clip.values()):
        return False
    return (params == {"format": "png", "clip": clip, "captureBeyondViewport": False, "fromSurface": True}
            and 0 <= clip["x"] <= 100_000 and 0 <= clip["y"] <= 100_000
            and 80 <= clip["width"] <= MAX_SIDE + 16 and 80 <= clip["height"] <= MAX_SIDE + 16
            and clip["scale"] == 2)


class EgoCDP:
    def __init__(self, rt: dict, *, qr_origins=()):
        self.cli = rt["cli"]
        self.profile_id = rt["profile_id"]
        self._created = set()
        self._qr_origins = frozenset(qr_origins)
        self._checkpoints = {}
        self._fresh_tabs = set()
        self._login_page = None

    def _page(self, target: str, script: str, *, timeout: float = 10, missing_ok=False):
        sid, name, label, page_id = _parse(target).groups()
        prelude = f"""
          const found = (await listTaskSpaces()).find(s => s.id === {sid} && s.name === {_json(name)}
            && s.profileId === {_json(self.profile_id)} && s.ownership === "agent");
          if (!found) {'return false;' if missing_ok else 'throw Error("Space unavailable");'}
          const task = await taskSpace({sid});
          if (task.name !== {_json(name)} || task.ownership !== "agent") throw Error("Space unavailable");
          const page = (await task.pages()).find(p => p.label === {_json(label)} &&
            p.targetId === {_json(page_id)} && p.openedBy === "agent");
          if (!page) {'return false;' if missing_ok else 'throw Error("Page unavailable");'}
        """
        return _invoke(self.cli, prelude + script, timeout=timeout)

    def new_tab(self, url: str, background: bool = True) -> str:
        return self._fresh_login_probe(url) if self._login_page else self._new_task_page(url)

    def _new_task_page(self, url: str) -> str:
        name = "Agent J " + secrets.token_hex(8)
        script = f"""
          const task = await taskSpace({_json(name)}, {{profileId: {_json(self.profile_id)}}});
          if (task.name !== {_json(name)} || task.ownership !== "agent") throw Error("Space unavailable");
          try {{
            const info = (await listTaskSpaces()).find(s => s.id === task.spaceId && s.name === {_json(name)});
            if (info?.profileId !== {_json(self.profile_id)}) throw Error("Profile unavailable");
            const page = (await task.pages()).find(p => p.label === "p1" && p.openedBy === "agent");
            if (!page?.targetId) throw Error("Page unavailable");
            if ({_json(url)} !== "about:blank") {{
              const result = await page.cdp("Page.navigate", {{url: {_json(url)}}}, {{timeout: 10000}});
              if (result.errorText || result.isDownload || !result.loaderId) throw Error("Navigation failed");
            }}
            return [task.spaceId, page.targetId];
          }} catch (e) {{
            await task.finish({{keep: []}});
            throw e;
          }}
        """
        reply = _invoke(self.cli, script, timeout=14)
        if not isinstance(reply, list) or len(reply) != 2:
            raise CDPError("Ego Lite Page unavailable")
        return self._remember(f"{reply[0]}:{name}:p1:{reply[1]}")

    def bind_login(self, target: str) -> None:
        """Fresh confirmation gets another Page in this exact login Space and browser Profile."""
        _parse(target)
        self._login_page = target

    def _fresh_login_probe(self, url: str) -> str:
        sid, name, original, _ = _parse(self._login_page).groups()
        if url != "about:blank":
            raise CDPError("Login check must start in a fresh Ego Lite Page")
        script = """
          const probe = await task.newPage();
          try {
            const created = (await task.pages()).find(p => p.label === probe.label && p.openedBy === "agent");
            if (!created?.targetId || created.label === page.label) throw Error("Probe Page unavailable");
            return [created.label, created.targetId];
          } catch (e) {
            if (probe.label !== page.label) await probe.close();
            throw e;
          }
        """
        reply = self._page(self._login_page, script)
        if not isinstance(reply, list) or len(reply) != 2 or reply[0] == original:
            raise CDPError("Ego Lite probe Page unavailable")
        return self._remember(f"{sid}:{name}:{reply[0]}:{reply[1]}")

    def _remember(self, target: str) -> str:
        _parse(target)
        self._created.add(target)
        return target

    def attach(self, target: str) -> str:
        _parse(target)
        return target

    def tab_alive(self, target: str) -> bool:
        return self._page(target, "return true;", missing_ok=True) is True

    def location(self, session: str) -> tuple[str, str]:
        return CDP.location(self, session)

    def begin_qr(self, session: str) -> bool:
        self._checkpoints.pop(session, None)
        frame = self._page(session, "return await (" + _ROOT_FRAME + ")();")
        if not isinstance(frame, dict) or frame.get("origin") not in self._qr_origins:
            return False
        if not all(isinstance(frame.get(k), str) and frame[k] for k in ("loader", "frame")):
            return False
        self._checkpoints[session] = frame
        return True

    def _qr_call(self, session, action, output, seconds):
        expected = self._checkpoints.get(session)
        if not expected:
            raise CDPError("Ego Lite QR document unavailable")
        # Pin the approved document throughout this pixel extraction.
        script = f"""
            const rootFrame = {_ROOT_FRAME};
            const expected = {_json(expected)};
            const sameDocument = async () => {{
              const frame = await rootFrame();
              return frame.origin === expected.origin && frame.loader === expected.loader && frame.frame === expected.frame;
            }};
            if (!await sameDocument()) return {{}};
            const reply = {action};
            if (!await sameDocument()) return {{}};
            {output}
        """
        return self._page(session, script, timeout=seconds + 14)

    def qr_evaluate(self, session, params):
        action = f'await page.cdp("Runtime.evaluate", {_json(params)}, {{timeout: 5000}})'
        output = """
            const data = reply?.result?.value;
            return typeof data === "string" && data.length <= 699052 &&
              data.length % 4 === 0 && /^[A-Za-z0-9+/]+={0,2}$/.test(data) ? {result: {value: data}} : {};
        """
        result = self._qr_call(session, action, output, 5)
        return result if isinstance(result, dict) else {}

    def call(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float | None = None) -> dict:
        if method not in _METHODS or not session:
            raise CDPError("Ego Lite command outside Page scope")
        params = params or {}
        seconds = timeout or 10
        action = f"await page.cdp({_json(method)}, {_json(params)}, {{timeout: {int(seconds * 1000)}}})"
        script = "return " + action + ";"
        if method in ("Page.navigate", "Page.reload") and session in self._fresh_tabs:
            # Reapply bypass settings to the session that actually navigates.
            script = """
              await page.cdp("Network.setCacheDisabled", {cacheDisabled: true}, {timeout: 5000});
              await page.cdp("Network.setBypassServiceWorker", {bypass: true}, {timeout: 5000});
            """ + script
            seconds += 10
        if method == "Page.captureScreenshot":
            if not _is_clip(params):
                raise CDPError("Ego Lite QR clip unavailable")
            output = """
                const data = reply?.data;
                return typeof data === "string" && data.length <= 700000 &&
                  data.length % 4 === 0 && /^[A-Za-z0-9+/]+={0,2}$/.test(data) ? {data} : {};
            """
            reply = self._qr_call(session, action, output, seconds)
            return reply if isinstance(reply, dict) else {}
        reply = self._page(session, script, timeout=seconds + 4)
        if method == "Network.setBypassServiceWorker" and params == {"bypass": True}:
            self._fresh_tabs.add(session)
        return reply if isinstance(reply, dict) else {}

    def hand_off(self, target: str) -> None:
        self._page(target, "await task.handOff(); return true;")

    def detach(self, session: str) -> None:
        self._checkpoints.pop(session, None)
        self._fresh_tabs.discard(session)

    def close_tab(self, target: str) -> None:
        if target in self._created:
            original = _parse(target).group(3) == "p1"
            cleanup = "await task.finish({keep: []});" if original else "await page.close();"
            with contextlib.suppress(CDPError):
                self._page(target, cleanup + "return true;", missing_ok=True)
            self._created.discard(target)
            self.detach(target)

    def close(self) -> None:
        self._checkpoints.clear()
        self._fresh_tabs.clear()


def host_open(rt: dict, url: str) -> None:
    cdp = EgoCDP(rt)
    target = cdp.new_tab(url, background=False)
    try:
        cdp.hand_off(target)
    except CDPError:
        cdp.close_tab(target)
        raise
