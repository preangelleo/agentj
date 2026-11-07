"""Existing P224 fixed CLI PTY login, no model probe."""
import os, re, sys, subprocess, contextlib, urllib.parse
_URL = re.compile(r"https://[^\s\x1b\"'<>]+")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")
_DEVCODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,5}\b")
LOGIN_HOSTS = ("anthropic.com", "claude.ai", "claude.com", "openai.com", "chatgpt.com")
LOGIN_TTL = 15 * 60
LOGIN_ARGV = {"claude": ["auth", "login"], "codex": ["login", "--device-auth"]}


def login_url(text: str) -> str | None:
    for m in _URL.finditer(_ANSI.sub("", text)):
        u = m.group(0).rstrip(".,);]")
        try:
            parsed = urllib.parse.urlsplit(u)
            if parsed.username or parsed.password or parsed.port not in (None, 443) or len(u)>2000:
                continue
            host = parsed.hostname or ""
        except ValueError:
            continue
        if any(host == h or host.endswith("." + h) for h in LOGIN_HOSTS):
            return u[:2000]
    return None


class LoginProcess:
    """A CLI login flow in a pseudo-terminal (the CLIs expect one). Output stays in memory, never logged."""

    def __init__(self, argv: list[str], env: dict):
        import pty
        self.master, slave = pty.openpty()
        try:
            self.p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True,
                                      cwd=os.path.expanduser("~"))
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(self.master, False)
        self.buf = ""

    def read(self) -> str:
        while True:
            try:
                chunk = os.read(self.master, 65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            if not chunk:
                break
            self.buf = (self.buf + chunk.decode("utf-8", "replace"))[-65536:]
        return self.buf

    def write(self, data: bytes) -> None:
        os.write(self.master, data)

    def done(self) -> int | None:
        return self.p.poll()

    def close(self) -> None:
        if self.p.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.p.pid, 15)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.p.wait(1)
        if self.p.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.p.pid, 9)
            self.p.wait()
        with contextlib.suppress(OSError):
            os.close(self.master)
        self.buf = ""

