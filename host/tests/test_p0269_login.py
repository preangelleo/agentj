"""0.26.9 #464 / #462: a login failure in a chat reply gets 「重新登录」; the button signs the MAIN session in and proves it there.

Chairman 11-04: 「用户根本不知道要回到设置里边做这些操作…把 login 这样的 slash command 变成可以点击的…在弹窗里边完成 authentication」.
Proven by hand the same day: `claude auth login` → link → authorise in the phone's browser → paste the code → Login successful →
main conversation back. #462: the old rescue said 「已恢复」 from a side process while the main conversation still failed.

Only stand-in `claude` / `codex` (single-file executables written below), fake codes and tokens, an isolated HOME / XDG / state;
never this machine's real login. The owner's terminal session is a `sleep`-like child whose environment we choose.
"""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from agentj import auth_rescue as ar, claude_live, controls, history, login_flow, wire
from agentj.state import State

GOOD = "sk-ant-oat01-" + "G" * 95 + "Q"        # the token the stand-in setup-token prints (and its -p accepts)
OLD = "sk-ant-oat01-" + "O" * 95 + "Z"         # a token that no longer works
CODE = "Zq7RkP2vN8xLmT4wYb6sHc9dFj3gKe5a#st4te-0269"   # the stand-in's one valid authorization code (OAuth shape)

FAKE = r'''
import json, os, sys, time
args = sys.argv[1:]
KIND = os.path.basename(sys.argv[0])
GOOD = os.environ.get("FAKE0269_TOKEN", "")
CODE = os.environ.get("FAKE0269_CODE", "")
LOG = os.environ.get("FAKE0269_LOG")


def log(x):
    if LOG:
        with open(LOG, "a") as f:
            f.write(x + "\n")


def out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def cfgdir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.environ.get("HOME", "/"), ".claude")


def stored():
    try:
        with open(os.path.join(cfgdir(), ".credentials.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_until_cr(limit=60):
    buf = b""
    end = time.time() + limit
    while time.time() < end:
        c = os.read(0, 1)
        if not c:
            break
        if c in (b"\r", b"\n"):
            return buf.decode(errors="replace")
        buf += c
    return buf.decode(errors="replace")


def claude():
    if args[:1] == ["setup-token"] or args[:2] == ["auth", "login"]:
        import tty
        tty.setraw(0)
        log("login_env cfg=%s" % cfgdir())
        out("Browser didn't open? Use the url below to sign in (c to copy)\r\n")
        out("https://claude.com/cai/oauth/authorize?code=true&client_id=x&response_type=code&state=s1\r\n")
        out("Paste code here if prompted > ")
        code = read_until_cr()
        log("code_len %d" % len(code))
        if code.strip() != CODE:
            out("\r\nOAuth error: Invalid code\r\n")
            sys.exit(1)
        if args[0] == "setup-token":
            out("\r\nYour OAuth token (valid for 1 year):\r\n\r\n" + GOOD + "\r\n")
            time.sleep(.5)
            return
        out("\r\nLogin successful. Press Enter to continue\r\n")
        read_until_cr(10)
        os.makedirs(cfgdir(), exist_ok=True)
        with open(os.path.join(cfgdir(), ".credentials.json"), "w") as f:
            json.dump({"acct": os.environ.get("FAKE0269_ACCOUNT", "new")}, f)
        return
    e = os.environ
    if args[:2] == ["auth", "status"]:
        if e.get("ANTHROPIC_AUTH_TOKEN") or e.get("CLAUDE_CODE_OAUTH_TOKEN"):
            d = {"loggedIn": True, "authMethod": "oauth_token"}
        elif e.get("ANTHROPIC_API_KEY"):
            d = {"loggedIn": True, "authMethod": "api_key", "apiKeySource": "ANTHROPIC_API_KEY"}
        else:
            d = {"loggedIn": True, "authMethod": "claude.ai"} if stored() else {"loggedIn": False, "authMethod": "none"}
        print(json.dumps(d))
        return
    if "-p" in args:
        if not sys.stdin.isatty():
            sys.stdin.read()
        tok = e.get("CLAUDE_CODE_OAUTH_TOKEN")
        ok = tok == GOOD if tok else bool(stored()) and not e.get("FAKE0269_ORG")
        log("probe " + ("ok" if ok else "fail"))
        line = ("Your organization has disabled Claude subscription access for Claude Code" if e.get("FAKE0269_ORG")
                else "Not logged in · Please run /login")
        if "--output-format" not in args:                  # auth_rescue.live_claude: plain text
            print("PONG" if ok else line)
            sys.exit(0 if ok else 1)
        print(json.dumps({"type": "result", "result": "Hello!"} if ok else {"type": "result", "is_error": True, "result": line}))
        sys.exit(0 if ok else 1)
    if args[:1] == ["--version"]:
        print("2.1.316 (Claude Code)")
        return
    time.sleep(60)


def codex():
    if args[:2] == ["login", "--device-auth"]:
        print("1. Open this link in your browser and sign in to your account\n   https://auth.openai.com/codex/device\n")
        print("2. Enter this one-time code (expires in 15 minutes)\n   AB12-CD34E\n", flush=True)
        if os.environ.get("FAKE0269_DEVICE") == "hang":
            time.sleep(120)
            return
        time.sleep(.4)
        home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
        with open(os.path.join(home, "auth.json"), "w") as f:
            f.write('{"new": true}')
        print("Successfully logged in")
        return
    if args[:1] == ["exec"]:
        sys.stdin.read()
        for ev in ({"type": "thread.started"}, {"type": "turn.started"},
                   {"type": "item.completed", "item": {"type": "agent_message", "text": "Hello!"}},
                   {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}):
            print(json.dumps(ev))
        return
    if args[:1] == ["--version"]:
        print("codex-cli 0.166.0")
        return
    time.sleep(60)


claude() if KIND == "claude" else codex()
'''


# ------------------------------------------------------------------ ① detection
class Detect(unittest.TestCase):
    POS = [
        ("claude", "Not logged in · Please run /login", "not_logged_in"),
        ("claude", "● Not logged in · Please run /login", "not_logged_in"),
        ("claude", "API Error: 403 {\"type\":\"error\",\"error\":{\"type\":\"permission_error\",\"message\":\"Your organization has "
                   "disabled Claude subscription access for Claude Code\"}}", "org_disabled"),
        ("claude", "Your organization has disabled Claude subscription access for Claude Code. Please contact your admin.",
         "org_disabled"),
        ("claude", "OAuth token has expired. Please obtain a new token or refresh your existing token.", "expired"),
        ("claude", "Invalid API key · Please run /login", "expired"),
        ("claude", "API Error: 401 {\"type\":\"error\",\"error\":{\"type\":\"authentication_error\",\"message\":\"Invalid bearer token\"}}",
         "expired"),
        ("claude", "Login expired · Please run /login", "expired"),
        ("codex", "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header", "expired"),
        ("codex", "stream error: exceeded retry limit, last status: 401 Unauthorized, request id: abc", "expired"),
        ("codex", "Your access token could not be refreshed because your refresh token was already used. Please log out and "
                  "sign in again.", "expired"),
        ("codex", "Not logged in. Run `codex login` first.", "not_logged_in"),
        ("codex", "Error: device code login is not enabled for this workspace.", "device"),
        ("opencode", "ProviderAuthError: No API key found for provider anthropic", "not_logged_in"),
        ("opencode", "No API key found for provider deepseek. Run opencode auth login.", "not_logged_in"),
        ("claude", "登录失效了，请运行 /login 重新登录。", "not_logged_in"),
        ("codex", "Welcome to Codex\n › 1. Sign in with ChatGPT\n   2. Sign in with Device Code\n   3. Provide your own API key",
         "not_logged_in"),                                                          # its own sign-in screen (BLOCKERS)         # item 3: the /login slash text
    ]
    NEG = [
        ("claude", "如果你看到 Not logged in · Please run /login，打开 设置 → 紧急修复 就能修好。"),       # the Agent explaining it
        ("claude", "我查了日志：昨天的 host.log 里有一行 Not logged in · Please run /login，那是切换账号前的旧记录，现在已经正常。"),
        ("claude", "Here is the error you asked about:\n```\nNot logged in · Please run /login\n```"),
        ("claude", "The route https://example.com/login returns 401 when the cookie is missing; I added a redirect."),
        ("claude", "GET /login returns 401 in our Express app until the session middleware runs."),
        ("claude", "src/routes/login.ts now validates the form; /login.html is unchanged."),
        ("claude", "Claude AI usage limit reached|1760000000"),
        ("claude", "Credit balance is too low · Please run /login"),               # balance first (P258): never a re-login
        ("claude", ("Long explanation. " * 40) + "\nNot logged in · Please run /login"),
        ("codex", "The API returned 401 Unauthorized when I called it, so I'll retry with the right header."),
        ("codex", "You've hit your usage limit. Upgrade to Pro or try again later; please log in to see your plan."),
        ("codex", "I fixed the login page: users who are not logged in now see a sign in button."),
        ("opencode", "I configured the provider; the auth flow docs say to run the login command once."),
        ("codex", "I added a Sign in with ChatGPT button to the settings page."),
        ("kimi", "Not logged in · Please run /login"),
        (None, "Not logged in · Please run /login"),
    ]

    def test_matrix(self):
        for kind, text, why in self.POS:
            with self.subTest(text=text[:50]):
                self.assertEqual(login_flow.detect(kind, text), {"agent": kind, "why": why})
        for kind, text in self.NEG:
            with self.subTest(text=text[:50]):
                self.assertIsNone(login_flow.detect(kind, text))

    def test_history_keeps_the_attachment(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-0269-h-", dir=os.environ.get("TEST_TMP") or None))
        self.addCleanup(shutil.rmtree, tmp, True)
        st = State(tmp / "st")
        st.init(relay="ws://127.0.0.1:1")
        h = history.History(st)
        t = h.add({"k": "agent", "text": ""}, "Not logged in · Please run /login", "done")
        h.update(t["id"], login={"agent": "claude", "why": "not_logged_in"})
        self.assertEqual(h.get(t["id"])["login"], {"agent": "claude", "why": "not_logged_in"})


# ------------------------------------------------------------------ fixtures for the flow
class _Host:
    def __init__(self, st, work):
        self.st, self.lang, self.agent_cfg = st, "zh", {"dir": str(work)}
        self.agent = None
        self.notices, self.recovery, self.sent, self.pages, self.tasks = [], [], [], [], []
        self.channel, self.nonces = "CH", controls.Nonces()
        self.auth_rescue = ar.Rescue(self)

    def agent_notice(self, t, **kw):
        self.notices.append(t)
        login_flow.mark(self, len(self.notices), login_flow.take_pending(self))

    def agent_recovery_notice(self, t):
        self.recovery.append(t)

    def hist_update(self, tid, **kw):
        self.pages.append((tid, kw))
        return []

    async def ask(self, tool, tool_input, batch=True, **kw):
        return {"behavior": "allow"}

    async def _send_device(self, device, obj):
        self.sent.append((device, json.loads(json.dumps(obj))))

    def _bg(self, coro, what="bg"):
        t = asyncio.ensure_future(coro)
        self.tasks.append(t)
        return t

    def _paired_live(self, s):
        return True


class _Owned:
    """An Agent J-owned Claude Code (stream-json child): the next spawn reads serve's environment."""
    kind = "claude"

    def __init__(self, work):
        self.cfg = {"dir": str(work), "fence": False}
        self.restarts = []

    def owns_harness(self):
        return True

    def request_restart(self, why):
        self.restarts.append(why)
        return "next_turn"


def _proc_start(pid):
    """The registry's `procStart` of a live process (field 22 of /proc/<pid>/stat), as Claude Code records it."""
    stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    return stat[stat.rfind(")") + 2:].split()[19]


class _Shared:
    kind = "claude"

    def __init__(self, pid, work, registered=False):
        self.cfg = {"session_mode": "shared", "dir": str(work)}
        # registered: the attached session row carries the process start time (merge ruling 2: provably that process)
        self.session = {"pid": pid, **({"procStart": _proc_start(pid)} if registered else {})}
        self.native, self.master = None, None

    def owns_harness(self):
        return False


class _Codex:
    """The main Codex agent: `call` answers like its app-server would from the auth.json it read when it started."""
    kind = "codex"

    def __init__(self, owned=True, cached=False):
        self.owned, self.cached, self.proc, self.halting, self.spawns, self.calls = owned, cached, object(), False, 0, []
        self.seen = self._read()

    def _read(self):
        try:
            return json.loads((pathlib.Path(os.environ["CODEX_HOME"]) / "auth.json").read_text())
        except (OSError, ValueError):
            return None

    def owns_harness(self):
        return self.owned

    async def _end_proc(self):
        self.proc = None

    async def _spawn(self):
        self.spawns += 1
        self.proc = object()
        if not self.cached:
            self.seen = self._read()
        return True

    async def call(self, method, params, timeout=0):
        self.calls.append(method)
        if method == "account/read":
            return {"account": {"type": "chatgpt", "email": "owner@example.com"} if (self.seen or {}).get("new") else None}
        if not (self.seen or {}).get("new"):
            raise ConnectionError("401 Unauthorized")
        return {"rateLimits": {}}


class _Sess:
    cid, state, pub = "c1", "ready", "pk"

    def __init__(self, device):
        self.device = device


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-0269-", dir=os.environ.get("TEST_TMP") or None))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for d in ("work", "cc", "cx", "config", "bin"):
            (self.tmp / d).mkdir()
        self.log = self.tmp / "fake.log"
        bins = {}
        for kind in ("claude", "codex"):
            exe = self.tmp / "bin" / kind
            exe.write_text(f"#!{sys.executable}\n" + FAKE)
            exe.chmod(0o700)
            bins[kind] = str(exe)
        env = {"AGENTJ_CLAUDE_BIN": bins["claude"], "AGENTJ_CODEX_BIN": bins["codex"], "FAKE0269_LOG": str(self.log),
               "FAKE0269_TOKEN": GOOD, "FAKE0269_CODE": CODE, "HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp / "config"),
               "XDG_DATA_HOME": str(self.tmp / "data"), "XDG_STATE_HOME": str(self.tmp / "xstate"),
               "XDG_CACHE_HOME": str(self.tmp / "cache"), "CLAUDE_CONFIG_DIR": str(self.tmp / "cc"),
               "CODEX_HOME": str(self.tmp / "cx"), "AGENTJ_STATE_DIR": str(self.tmp / "st")}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FAKE0269_ORG", "FAKE0269_DEVICE"):
            os.environ.pop(k, None)
        for target in ("agentj.keys_found.manager_env",):          # per uid, not per HOME: never the real user manager
            m = mock.patch(target, return_value={})
            m.start()
            self.addCleanup(m.stop)
        m = mock.patch("agentj.harness_setup.enabled", return_value=False)
        m.start()
        self.addCleanup(m.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = _Host(self.st, self.tmp / "work")
        self.kids = []
        self.addCleanup(self._reap)
        self.keys = {}
        for dev in ("phoneA", "phoneB"):
            sk = Ed25519PrivateKey.generate()
            pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            did = self.st.add_device(secrets.token_bytes(32), dev, pub)
            self.keys[dev] = (did, sk)

    def _reap(self):
        for p in self.kids:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()

    def terminal(self, **env):
        base = {k: v for k, v in os.environ.items() if k not in claude_live.AUTH}
        p = subprocess.Popen([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"],
                             env={**base, **env}, stdout=subprocess.PIPE, text=True)
        self.kids.append(p)
        p.stdout.readline()
        return p

    def signed(self, dev, obj, *, forge=False):
        did, sk = self.keys[dev]
        n, ts = secrets.token_hex(16), int(time.time() * 1000)
        target = login_flow.Login.target(obj)
        digest = controls.object_digest("auth_rescue", target)
        msg = controls.signed_message(self.host.channel, did, "auth_rescue", n, ts, digest)
        sig = (Ed25519PrivateKey.generate() if forge else sk).sign(msg)
        return {"t": "auth_rescue", "r": secrets.token_hex(4), **obj, "n": n, "ts": ts, "sig": wire.b64u(sig)}

    async def phone(self, dev, obj, **kw):
        await self.host.auth_rescue.login.on_phone(_Sess(self.keys[dev][0]), self.signed(dev, obj, **kw))

    def views(self, dev=None):
        did = self.keys[dev][0] if dev else None
        return [m["view"] for d, m in self.host.sent if did is None or d == did]

    async def until(self, step, dev="phoneA", secs=20):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            got = [v for v in self.views(dev) if v.get("step") == step]
            if got:
                return got[-1]
            await asyncio.sleep(.05)
        self.fail(f"no {step}: {self.views()}")

    async def drain(self):
        while self.host.tasks:
            await self.host.tasks.pop(0)

    def assert_code_nowhere(self):
        for p in pathlib.Path(self.st.root).rglob("*"):
            if p.is_file() and not p.is_symlink():
                self.assertNotIn(CODE.split("#")[0], p.read_text(errors="replace"), p)
        self.assertNotIn(CODE.split("#")[0], json.dumps(self.host.notices + self.host.recovery, ensure_ascii=False))
        self.assertNotIn(CODE.split("#")[0], self.log.read_text() if self.log.exists() else "")


# ------------------------------------------------------------------ ② Claude: `claude auth login` lands where the main session reads
class ClaudeLogin(Base):
    @unittest.skipUnless(sys.platform.startswith("linux"), "procStart is read from /proc")
    async def test_owner_terminal_login_lands_in_its_config_and_is_proven_there(self):
        """Merge ruling 2 (provable case): the owner's terminal uses its own config dir; the attached session row is that
        very process (procStart matches) → the login lands there and is proven there."""
        owner_cc = self.tmp / "owner-cc"
        owner_cc.mkdir(mode=0o700)
        term = self.terminal(CLAUDE_CONFIG_DIR=str(owner_cc))
        self.host.agent = _Shared(term.pid, self.tmp / "work", registered=True)
        await self.phone("phoneA", {"action": "login", "agent": "claude"})
        url = await self.until("url")
        self.assertEqual((url["agent"], url["route"]), ("claude", "login"))
        self.assertTrue(url["url"].startswith("https://claude.com/cai/oauth/authorize?"))
        self.assertFalse(self.views("phoneB"), "the link goes to the asking phone only")
        flow = self.host.auth_rescue.view()["flow"]
        self.assertFalse({"url", "code"} & set(flow), flow)                          # Settings' broadcast row: no link
        # the code from ANOTHER paired phone is refused and never typed
        await self.phone("phoneB", {"action": "code", "code": CODE})
        self.assertEqual(self.views("phoneB")[-1], {"step": "done", "ok": False, "why": "not_requester"})
        # a code with a control byte / outside the charset is refused before the PTY
        await self.phone("phoneA", {"action": "code", "code": "abc\x1b[Adef-ghijk"})
        self.assertEqual(self.views("phoneA")[-1].get("again"), "bad_code")
        await self.phone("phoneA", {"action": "code", "code": CODE})
        done = await self.until("done")
        await self.drain()
        self.assertEqual(done["ok"], True, done)
        self.assertTrue((owner_cc / ".credentials.json").exists())                   # where the main session reads it
        self.assertFalse((self.tmp / "cc" / ".credentials.json").exists())            # not serve's own config
        self.assertIn("probe ok", self.log.read_text())                               # one real call in the main env
        self.assertIsNone(term.poll())
        # one-shot: the same code again is refused
        await self.phone("phoneA", {"action": "code", "code": CODE})
        self.assertEqual(self.views("phoneA")[-1]["why"], "no_flow")
        self.assert_code_nowhere()
        ctl = pathlib.Path(self.st.controls_path).read_text()
        self.assertIn('"step": "code"', ctl)                                          # audited, without the code or its hash
        self.assertEqual(self.host.auth_rescue.last["claude"]["result"], "recovered")

    async def _refused(self, agent, cc):
        self.host.agent = agent
        del self.host.sent[:]                       # until() must see this flow's own 「done」
        await self.phone("phoneA", {"action": "login", "agent": "claude"})
        done = await self.until("done")
        await self.drain()
        self.assertEqual((done["ok"], done["why"]), (False, "context"), done)
        self.assertIn("无法确认", done.get("text", ""))
        self.assertFalse([v for v in self.views("phoneA") if v.get("step") == "url"], "no login link was even opened")
        self.assertFalse((cc / ".credentials.json").exists(), "nothing written into that config dir")
        self.assertFalse((self.tmp / "cc" / ".credentials.json").exists())
        self.assertNotIn("login_env", self.log.read_text() if self.log.exists() else "", "the login CLI never started")

    async def test_unproven_session_config_dir_is_refused(self):
        """Negative control: a session row without the process identity (any pid an Agent or a reply could name) whose
        process has another config dir → refused with why=context; no login runs anywhere."""
        other_cc = self.tmp / "other-cc"
        other_cc.mkdir(mode=0o700)
        await self._refused(_Shared(self.terminal(CLAUDE_CONFIG_DIR=str(other_cc)).pid, self.tmp / "work"), other_cc)

    @unittest.skipUnless(sys.platform.startswith("linux"), "procStart is read from /proc")
    async def test_registered_session_with_a_workspace_config_dir_is_refused(self):
        """Negative control: even the real main session is refused when its config dir sits in the Agent-writable
        workspace (an Agent could have planted settings there) or is writable by others."""
        ws_cc = self.tmp / "work" / ".cc"
        ws_cc.mkdir(mode=0o700)
        await self._refused(_Shared(self.terminal(CLAUDE_CONFIG_DIR=str(ws_cc)).pid, self.tmp / "work", registered=True), ws_cc)
        open_cc = self.tmp / "open-cc"
        open_cc.mkdir()
        open_cc.chmod(0o777)
        await self._refused(_Shared(self.terminal(CLAUDE_CONFIG_DIR=str(open_cc)).pid, self.tmp / "work", registered=True), open_cc)

    async def test_side_success_but_main_still_failing_is_never_ok(self):
        """#462: the login itself works (auth status loggedIn) but every message in the main session's own environment still
        gets the organization error → ok:false, never 「已恢复」."""
        os.environ["FAKE0269_ORG"] = "1"
        self.host.agent = _Owned(self.tmp / "work")
        await self.phone("phoneA", {"action": "login", "agent": "claude"})
        await self.until("url")
        await self.phone("phoneA", {"action": "code", "code": CODE})
        done = await self.until("done")
        await self.drain()
        self.assertEqual((done["ok"], done["why"], done["live"]), (False, "main_session", "org_disabled"), done)
        self.assertEqual(self.host.agent.restarts, ["chat_login"])
        self.assertNotIn("已重新登录", " ".join(self.host.notices))
        self.assertEqual(self.host.auth_rescue.last["claude"]["result"], "partial")
        self.assert_code_nowhere()

    async def test_env_token_session_mints_a_token_and_says_why(self):
        """ADR-462 precedence: an env token in the main process masks any stored login → setup-token instead; an owner
        terminal still holding the old token is told to reopen (signed P435 card), not reported ok, and never signalled."""
        term = self.terminal(CLAUDE_CODE_OAUTH_TOKEN=OLD)
        self.host.agent = _Shared(term.pid, self.tmp / "work")
        await self.phone("phoneA", {"action": "login", "agent": "claude"})
        url = await self.until("url")
        self.assertEqual((url["route"], url["masked"]), ("setup_token", "env_token"))
        self.assertIn("token", url["note"])
        await self.phone("phoneA", {"action": "code", "code": CODE})
        done = await self.until("done")
        await self.drain()
        self.assertEqual((done["ok"], done["why"], done.get("live")), (False, "main_session", "stale"), done)
        self.assertEqual(len(self.host.recovery), 1)
        self.assertIsNone(term.poll())
        self.assertEqual(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), GOOD)            # stored for serve / the next reopen
        blob = json.dumps(self.host.sent) + " ".join(self.host.notices + self.host.recovery)
        self.assertNotIn(GOOD, blob)
        self.assert_code_nowhere()

    async def test_outranking_key_refuses_by_name(self):
        term = self.terminal(ANTHROPIC_API_KEY="sk-ant-api03-fake")
        self.host.agent = _Shared(term.pid, self.tmp / "work")
        await self.phone("phoneA", {"action": "login", "agent": "claude"})
        done = await self.until("done")
        await self.drain()
        self.assertEqual((done["ok"], done["why"]), (False, "conflict"))
        self.assertIn("ANTHROPIC_API_KEY", done["text"])
        self.assertNotIn("sk-ant-api03-fake", json.dumps(self.host.sent))


# ------------------------------------------------------------------ ③ gates: signature, e-stop, one at a time, cancel, timeout
class Gates(Base):
    async def test_unsigned_or_forged_never_starts(self):
        self.host.agent = _Owned(self.tmp / "work")
        await self.phone("phoneA", {"action": "login", "agent": "claude"}, forge=True)
        self.assertEqual(self.views("phoneA")[-1], {"step": "done", "ok": False, "why": "bad_signature"})
        await self.host.auth_rescue.login.on_phone(_Sess(self.keys["phoneA"][0]),
                                                   {"t": "auth_rescue", "r": "r1", "action": "login", "agent": "claude"})
        self.assertEqual(self.views("phoneA")[-1]["why"], "shape")
        self.assertIsNone(self.host.auth_rescue.login.active)

    async def test_estop_busy_not_main(self):
        self.host.agent = _Owned(self.tmp / "work")
        controls_state = {"on": True, "at": 1, "by": "terminal"}
        with mock.patch("agentj.controls.estop_state", return_value=controls_state):
            await self.phone("phoneA", {"action": "login", "agent": "claude"})
        self.assertEqual(self.views("phoneA")[-1]["why"], "estop")
        await self.phone("phoneA", {"action": "login", "agent": "codex"})
        self.assertEqual(self.views("phoneA")[-1]["why"], "not_main")
        await self.phone("phoneA", {"action": "login", "agent": "claude"})
        await self.until("url")
        await self.phone("phoneB", {"action": "login", "agent": "claude"})
        self.assertEqual(self.views("phoneB")[-1]["why"], "busy")
        start = await self.host.auth_rescue.start("claude", "login")                 # Settings' rescue is blocked too
        self.assertEqual(start, {"ok": False, "why": "busy"})
        await self.phone("phoneB", {"action": "cancel"})
        self.assertEqual(self.views("phoneB")[-1]["why"], "not_requester")
        self.host.sent.clear()
        await self.phone("phoneA", {"action": "cancel"})
        done = await self.until("done")
        await self.drain()
        self.assertEqual((done["ok"], done["why"]), (False, "cancelled"))
        self.assertIsNone(self.host.auth_rescue.login.active)

    async def test_estop_during_wait_and_timeout(self):
        self.host.agent = _Owned(self.tmp / "work")
        await self.phone("phoneA", {"action": "login", "agent": "claude"})
        await self.until("url")
        with mock.patch("agentj.controls.estop_state", return_value={"on": True}):
            done = await self.until("done")
            await self.drain()
        self.assertEqual(done["why"], "estop")
        self.host.sent.clear()
        with mock.patch.object(login_flow, "WAIT", 1.5):
            await self.phone("phoneA", {"action": "login", "agent": "claude"})
            done = await self.until("done")
            await self.drain()
        self.assertEqual(done["why"], "timeout")


# ------------------------------------------------------------------ ④ Codex: device auth, proven on the main agent's own connection
class CodexLogin(Base):
    async def test_device_auth_then_main_session_account(self):
        self.host.agent = _Codex(owned=True)
        await self.phone("phoneA", {"action": "login", "agent": "codex"})
        url = await self.until("url")
        self.assertEqual((url["url"], url["code"]), ("https://auth.openai.com/codex/device", "AB12-CD34E"))
        self.assertFalse(self.views("phoneB"))
        done = await self.until("done")
        await self.drain()
        self.assertEqual(done["ok"], True, done)
        self.assertEqual(self.host.agent.spawns, 1)                                  # restarted to read the new auth.json
        self.assertEqual(self.host.agent.calls, ["account/read", "account/rateLimits/read"])

    async def test_shared_daemon_still_on_old_login_is_not_ok(self):
        self.host.agent = _Codex(owned=False, cached=True)                          # a daemon that kept the old login
        await self.phone("phoneA", {"action": "login", "agent": "codex"})
        done = await self.until("done")
        await self.drain()
        self.assertEqual((done["ok"], done["why"], done["live"]), (False, "main_session", "not_logged_in"), done)
        self.assertTrue((pathlib.Path(os.environ["CODEX_HOME"]) / "auth.json").exists())   # the valid new login is kept


# ------------------------------------------------------------------ ⑤ an adapter's failure signal puts the button on its notice
class Signal(Base):
    async def test_failure_notice_gets_the_button(self):
        self.host.agent = _Owned(self.tmp / "work")
        self.host.auth_rescue.text_signal("claude", "Your organization has disabled Claude subscription access for Claude Code")
        self.assertEqual(self.host.pages[-1][1], {"login": {"agent": "claude", "why": "org_disabled"}})
        self.host.auth_rescue.text_signal("claude", "Credit balance is too low")
        self.host.agent_notice("unrelated")
        self.assertEqual(len(self.host.pages), 1)


if __name__ == "__main__":
    unittest.main()
