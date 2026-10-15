"""P258 stand-in for `claude` / `codex` login flows and their interactive first screens (tests only, never a real login).

FAKE258_KIND=claude|codex (which program this wrapper pretends to be).
claude setup-token | auth login --claudeai: banner + authorize URL, then the pasted code (raw TTY, ends with CR):
    GOODCODE → token / "Login successful" (+ Enter) · anything else → "OAuth error: Invalid code" exit 1 · FAKE258_NOURL=1: no URL.
claude -p …: PONG when CLAUDE_CODE_OAUTH_TOKEN == FAKE258_GOOD_TOKEN (or FAKE258_PROBE=ok), else the expired-login line.
claude / codex (no args): FAKE258_SCREENS=a,b,… shown in order, each waiting for its key; "ready" = the prompt; "unknown" =
    a screen nobody catalogued; "exit" = quits. A wrong key on trust (Enter = 「No, exit」) quits with status 1.
codex login --device-auth: link + code; FAKE258_DEVICE=ok|fail|disabled|hang (ok writes $CODEX_HOME/auth.json).
P288 health checks (the bots' no-tool command): claude -p … --output-format json → JSON result; FAKE258_PROBE=ok|balance|
rate|down overrides the token rule. codex exec … → JSONL events; FAKE258_CODEX_PROBE=ok (default)|login|down.
"""
import json
import os
import sys
import time

KIND = os.environ.get("FAKE258_KIND", "claude")
GOOD = os.environ.get("FAKE258_GOOD_TOKEN", "sk-ant-oat01-" + "A" * 95 + "Q")
LOG = os.environ.get("FAKE258_LOG")
args = sys.argv[1:]


def log(x):
    if LOG:
        with open(LOG, "a") as f:
            f.write(x + "\n")


def out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def raw():
    try:
        import tty
        tty.setraw(0)
    except Exception:  # noqa: BLE001
        pass


def read_until_cr(limit=30):
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


def read_key(limit=30):
    """One key press: an escape sequence, a lone ESC, CR or a character."""
    import select
    end = time.time() + limit
    r, _, _ = select.select([0], [], [], max(0.0, end - time.time()))
    if not r:
        return ""
    c = os.read(0, 1)
    if c == b"\x1b":
        r, _, _ = select.select([0], [], [], .15)
        if r:
            c += os.read(0, 2)
            if c == b"\x1b[B":           # Down, then the confirming CR
                r, _, _ = select.select([0], [], [], 2)
                if r:
                    c += os.read(0, 1)
    return c.decode(errors="replace")


SCREENS = {
    "claude": {
        "trust": ("Accessing workspace:\r\n Quick safety check: Is this a project you created or one you trust?\r\n"
                  " ❯ No, exit\r\n   Yes, I trust this folder\r\n Enter to confirm · Esc to cancel\r\n", "\x1b[B\r"),
        "theme": ("Let's get started.\r\n Choose the text style that looks best with your terminal\r\n ❯ Dark mode\r\n", "\r"),
        "security_notes": ("Security notes:\r\n Claude can make mistakes\r\n Press Enter to continue\r\n", "\r"),
        "api_key": ("Detected a custom API key in your environment\r\n Do you want to use this API key?\r\n ❯ Yes\r\n   No (recommended)\r\n", "\x1b[B\r"),
        "bypass": ("WARNING: Claude Code running in Bypass Permissions mode\r\n ❯ No, exit\r\n   Yes, I accept\r\n", "\x1b[B\r"),
        "mcp": ("New MCP server found in .mcp.json: foo\r\n Use this and all future MCP servers in this project\r\n", "\x1b"),
        "login_method": ("Select login method:\r\n ❯ Claude account with subscription\r\n   Anthropic Console account\r\n", None),
        "login_expired": ("Login expired · Please run /login\r\n", None),
        "update": ("Update available! Run: claude update\r\n", ""),
        "ready": ("❯ Try \"fix lint errors\"\r\n ? for shortcuts   ⏵⏵ auto mode on (shift+tab to cycle)\r\n", None),
        "unknown": ("Something brand new is on this screen and waits\r\n", None),
    },
    "codex": {
        "trust": ("You are running Codex in /x\r\n Do you trust the contents of this directory?\r\n › 1. Yes, continue\r\n", "\r"),
        "update": ("✨ Update available! 0.162.1 -> 0.170.0\r\n › 1. Update now\r\n   2. Skip\r\n   3. Skip until next version\r\n", "\x1b"),
        "login": ("Welcome to Codex\r\n › 1. Sign in with ChatGPT\r\n   2. Sign in with Device Code\r\n   3. Provide your own API key\r\n", None),
        "approval": ("Choose a permission mode\r\n › Allow Codex to work without asking for approval\r\n", "\r"),
        "ready": ("› Ask Codex to do anything\r\n ? for shortcuts\r\n", None),
        "unknown": ("A screen nobody knows\r\n", None),
    },
}


def interactive():
    raw()
    seq = [x for x in os.environ.get("FAKE258_SCREENS", "ready").split(",") if x]
    log("interactive " + ",".join(seq))
    for name in seq:
        if name == "exit":
            sys.exit(0)
        text, key = SCREENS[KIND][name]
        out(text)
        if key is None:                    # stays here (ready / unknown / a login screen)
            time.sleep(60)
            sys.exit(0)
        if key == "":
            continue                       # informational, no key needed
        got = read_key()
        log(f"key {name} {got!r}")
        if got != key:
            out("Exiting.\r\n")
            sys.exit(1)
    time.sleep(60)


def claude():
    if args[:1] == ["setup-token"] or args[:2] == ["auth", "login"]:
        raw()
        out("Welcome to Claude Code v2.1.296\r\n")
        if os.environ.get("FAKE258_NOURL"):
            time.sleep(60)
            return
        out("Browser didn't open? Use the url below to sign in (c to copy)\r\n")
        out("https://claude.com/cai/oauth/authorize?code=true&client_id=x&response_type=code&scope=user%3Ainference&state=s1\r\n")
        out("Paste code here if prompted > ")
        code = read_until_cr(60)
        log("code_len %d" % len(code))
        if code.strip() != "GOODCODE":
            out("\r\nOAuth error: Invalid code\r\n")
            sys.exit(1)
        if args[0] == "setup-token":
            out("\r\n✓ Long-lived authentication token created successfully!\r\n\r\nYour OAuth token (valid for 1 year):\r\n\r\n")
            out(GOOD + "\r\n\r\nStore this token securely. You won't be able to see it again.\r\n")
            time.sleep(.5)
            return
        out("\r\nLogin successful. Press Enter to continue…\r\n")
        read_until_cr(10)
        cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        if cfg:
            with open(os.path.join(cfg, ".credentials.json"), "w") as f:
                f.write("{}")
        return
    if "-p" in args:
        mode = os.environ.get("FAKE258_PROBE", "")
        ok = mode == "ok" or (not mode and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == GOOD)
        log("probe token=%s" % ("good" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == GOOD else "other"))
        if "--output-format" in args:
            if not sys.stdin.isatty():
                sys.stdin.read()
            if mode == "down":
                sys.stderr.write("Error: connect ECONNREFUSED\n")
                sys.exit(1)
            line = {"balance": "Credit balance is too low", "rate": "Claude AI usage limit reached|1760000000"}.get(
                mode, "Invalid API key · Please run /login")
            print(json.dumps({"type": "result", "result": "Hello!"} if ok else {"type": "result", "is_error": True, "result": line}))
            sys.exit(0 if ok else 1)
        if ok:
            print("PONG")
            return
        print("Invalid API key · Please run /login")
        sys.exit(1)
    if args and args[0] == "--version":
        print("2.1.296 (Claude Code)")
        return
    interactive()


def codex():
    if args[:2] == ["login", "--device-auth"]:
        mode = os.environ.get("FAKE258_DEVICE", "ok")
        if mode == "disabled":
            print("Error: device code login is not enabled for this workspace. Enable it in ChatGPT security settings.")
            sys.exit(1)
        print("Follow these steps to sign in with ChatGPT using device code authorization:\n")
        print("1. Open this link in your browser and sign in to your account\n   https://auth.openai.com/codex/device\n")
        print("2. Enter this one-time code (expires in 15 minutes)\n   AB12-CD34E\n", flush=True)
        if mode == "hang":
            time.sleep(120)
            return
        time.sleep(float(os.environ.get("FAKE258_DELAY", "0.5")))
        if mode == "ok":
            home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
            os.makedirs(home, exist_ok=True)
            with open(os.path.join(home, "auth.json"), "w") as f:
                f.write('{"new": true}')
            print("Successfully logged in")
            return
        os.remove(os.path.join(os.environ.get("CODEX_HOME"), "auth.json")) if os.environ.get("FAKE258_WIPE") else None
        print("Error: device authorization failed")
        sys.exit(1)
    if args and args[0] == "--version":
        print("codex-cli 0.162.1")
        return
    if args and args[0] == "exec":
        sys.stdin.read()
        mode = os.environ.get("FAKE258_CODEX_PROBE", "ok")
        log("codex_exec " + mode)
        if mode == "login":
            sys.stderr.write("ERROR: unexpected status 401 Unauthorized: refresh token was revoked; sign in again\n")
            sys.exit(1)
        if mode == "down":
            sys.stderr.write("ERROR: stream disconnected before completion\n")
            sys.exit(1)
        for ev in ({"type": "thread.started"}, {"type": "turn.started"},
                   {"type": "item.completed", "item": {"type": "agent_message", "text": "Hello!"}},
                   {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}):
            print(json.dumps(ev))
        return
    interactive()


claude() if KIND == "claude" else codex()
