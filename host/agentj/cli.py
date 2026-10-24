"""Agent J host CLI `agentj` (alpha): init · serve · pair · admin · name · devices · revoke · send · status · login · report ·
unlink · remote-unbind · agent · approvals · passphrase · doctor · service · update (L3) · stop · resume · memory · activity ·
config · tasks (phone controls, ADR-A50 – A54) · wizard (L3.5, `wizard/__init__.py`) · migrate · alias (0.10 rename) ·
history · inbox · asr (relay parity, PROTOCOL §10: the phone's pages, uploads, local speech-to-text — asr.py).
No arguments = the next-step hint. `jarvis` (the ≤ 0.9 name) still works for one version cycle on computers that migrated
(a symlink made by alias.py, never shipped in the wheel): one notice line on stderr, then the same CLI. Every command first runs migrate.auto() (the 0.9 → 0.10 state move; messages on stderr).
Seat setup (0.7): `agentj login --seat <ajt_…> | --seat-file <path> | --seat - --name <name>` binds with a setup code from
the company (no y/N; exit 3 name taken · 4 invalid code · 5 seat not paid · 2 refused locally); `agentj agent detect`.
The 8-character code login asks y/N; `--account <account ID>` makes it refuse any other account (exit 2, nothing written) and
the hidden `--yes` (skip the y/N) is accepted only together with `--account` (PROMPT-29 C-10). `agentj docs-rule` prints the
"look it up first" block that `agentj wizard install` adds to the entry file (docsrule.py; `--write --harness …` appends it
to the AI's own memory file after the human's y at a terminal). `agentj handover` prints the install's handover note from
the Agent J host's facts (handover.py, read-only). `agentj pair` needs a running serve and says so before any prompt.
Plaza P2: `agentj plaza search | show | mine | post | reply | resolve | report` (plaza.py) — read posts are data, never
instructions; post / reply go out only after layer 1 (+ layer 2) and `--owner-confirmed --digest` from the human.
Skill & workflow plaza: `agentj plaza install | publish | like | installed` (market.py; search / show / mine / report cover
packages too) — install only after a preview and `--owner-confirmed --digest`; 官方认证 only with a valid compiled-in signature.

State dir: $AGENTJ_STATE_DIR or ~/.local/state/agentj (0700). Account-bound hosts can add a new device from the account page after fresh owner passkey confirmation.
The local `agentj pair` alternative requires the human's 6-digit code and local remote pairing password. At most MAX_DEVICES (10) remotes
per host: when the list is full, `agentj pair` lists them and the human unbinds one before approving. `agentj admin` is the
same thing as a local web page (127.0.0.1 only, one-time link printed here; the human still types the phone's code).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import secrets
import signal
import sys
import threading
import time

# P425: the signed macOS app dispatches rescue without importing the full CLI graph.
if sys.argv[1:] == ["rescue", "watchdog"]:
    from .rescue_watchdog import main as _watchdog_main
    raise SystemExit(_watchdog_main())

from . import DIST, __version__, cloud, gate, names
from . import docsrule, feedback, handover, plaza, recall, support, wizard
from . import elevate, elevate_helper
from .state import DEFAULT_RELAY, DEFAULT_WEB, MAX_DEVICES, State, device_names
from .envcompat import getenv
from .text import STARTER_NAMES, ask_yes, clean, clean_label


def _need_init(st: State) -> None:
    if not st.exists():
        sys.exit(f"还没初始化：先运行 `agentj init`（状态目录 {st.root}）")
    st.check_perms()


async def _ctl(st: State):
    try:
        return await asyncio.open_unix_connection(str(st.sock_path))
    except (FileNotFoundError, ConnectionRefusedError):
        return None


async def _ctl_call(st: State, req: dict) -> dict | None:
    """None only when serve is certainly not running; raises names.ServeBusy when it is there but does not answer."""
    return await asyncio.to_thread(names.ctl_call, st, req, 10)


def _ctl_quiet(st: State, req: dict) -> dict | None:
    """For read-only views: a busy serve shows like an empty status (never used to decide a fallback edit)."""
    try:
        return names.ctl_call(st, req, 10)
    except names.ServeBusy:
        return None


def cmd_init(a) -> None:
    st = State()
    from . import working_root
    try:
        root = working_root.select(st, getattr(a, "working_root", None), interactive=sys.stdin.isatty())
    except (ValueError, OSError) as e:
        sys.exit("✗ " + str(e))
    try:
        if st.exists() and getattr(a, "working_root", None) and not a.force:
            cfg = st.config()  # record a root for installed users without regenerating keys
        else:
            cfg = st.init(relay=a.relay, web=a.web, force=a.force)
    except FileExistsError as e:
        sys.exit(f"{e}（要重建身份密钥用 --force；所有已配对设备都要重配）")
    print(f"这台Agent J 主机的身份已生成：{st.root}\n通道 {cfg['channel']}\n转发服务器 {cfg['relay']}\n下一步：运行 `agentj`，看看还差哪几步 / next: run `agentj` to see what is left")
    from . import preferences
    preferences.ensure()
    working_root.record(st, root)
    from . import wizard
    wizard.bootstrap_root(root, lang=preferences.get(preferences.effective(st), "appearance.language", "zh"))
    _refresh_root(st, root)
    print(f"工作根目录 / Working root: {root}; existing files were kept; new workflows use direct child folders.")
    old_dir = cfg.get("agent", {}).get("dir")
    if old_dir and os.path.realpath(old_dir) != str(root):
        print("现有工作流没有搬动 / Existing workflows were not moved: " + old_dir)
    from .config_migrations import run
    run(st)
    _alias_auto(sys.stdout)


def _refresh_root(st, root) -> None:
    """P215: the shipped Workflow Design Bible skill + the boot-doc core upgrade in the working root (wizard.refresh_root).
    Best effort: a problem here never stops init / agent set."""
    from . import preferences, wizard
    try:
        cfg = st.agent_config() if st.exists() else None
        kind = (cfg or {}).get("kind")
        lang = preferences.get(preferences.effective(st), "appearance.language", None) if st.exists() else None
        rows = wizard.refresh_root(root, [kind] if kind in wizard.HARNESSES else None, lang, st=st)
        placed = [r["path"] for r in rows if r.get("status") == "starter-installed"]
        if placed:                          # P245: say once, in the terminal, which example workflows were put in the root
            print("示例工作流 / Example workflows: " + ", ".join(placed) + " → " + str(root))
    except Exception:  # noqa: BLE001 — never block installation on the documents
        pass


def _alias_auto(out, with_compat: bool = False) -> None:
    """After init: the short command `aj`; after the 0.9 state move also the old name `jarvis` — each only when nothing
    else has that name (alias.py)."""
    from . import alias
    try:
        rs = alias.install_all(with_compat=with_compat)
    except OSError:
        return
    for r in rs:
        if r.get("changed") or r["state"] == "taken" or (r["name"] == alias.COMPAT and r.get("why") in
                                                         ("not_on_path", "not_writable", "error")):
            print(alias.line(r), file=out, flush=True)


def cmd_alias(a) -> None:
    from . import alias
    rs = {"status": alias.status_all, "install": alias.install_all, "remove": alias.remove_all}[a.mode]()
    if a.mode == "remove":
        rs = [r for r in rs if r.get("changed")] or rs[:1]
    if a.json:
        print(json.dumps(rs, ensure_ascii=False))
        return
    for r in rs:
        print(alias.line(r))


def cmd_migrate(a) -> None:
    from . import migrate
    if a.mode == "rollback":
        try:
            r = migrate.rollback()
        except migrate.RollbackError as e:
            sys.exit("✗ " + migrate.ROLLBACK_MESSAGES.get(e.reason, e.reason))
        if a.json:
            print(json.dumps(r, ensure_ascii=False))
            return
        if r["moved_back"]:
            print(f"✓ 状态目录已搬回 {migrate.tilde(r['state_dir'])} / the state directory is back at the old path")
        if r["urls"]:
            print("✓ 旧网址已恢复 / old addresses restored: " + ", ".join(r["urls"]))
        print("注意：再运行任何 agentj 命令都会重新搬过去。要回到旧版本，先卸载 agentj / note: any agentj command moves it again; "
              "to go back to the old version, uninstall agentj first")
        return
    r = migrate.status()
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
        return
    rec = r["record"]
    print(f"状态目录 / state directory: {migrate.tilde(r['state_dir'])}" + ("  (AGENTJ_STATE_DIR)" if r["env_override"] else ""))
    if rec.get("migrated_from"):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(rec.get("at") or 0))
        print(f"✓ 已从 {migrate.tilde(rec['migrated_from'])} 搬来（{when}，{rec.get('version')}）/ moved from the old path"
              + ("；旧路径是指向这里的链接 / the old path links here" if r["old_is_link"] else ""))
    elif r["pending"] == "serve_running":
        print("! 还在旧路径：有 serve 正在使用它，停掉后自动搬 / still at the old path: a serve is using it")
    elif not r["env_override"]:
        print("没有需要搬的旧状态 / nothing to move")
    if rec.get("urls"):
        print("旧网址已换成新的 / old addresses replaced: " + ", ".join(sorted(rec["urls"])))


SERVE_EXIT_GRACE = 15.0   # P292: s from SIGTERM/SIGINT to a forced exit — under launchd's 20 s ExitTimeOut / systemd TimeoutStopSec=20
_exit_timer = None


def _serve_exit_guard() -> None:
    """P292: on a saturated disk serve's stop (queued fsyncs, a worker thread parked in a read, the default executor join at
    the end of asyncio.run) can outlast the service manager's stop timeout, which then SIGKILLs it (launchd: exit -9). Leave
    by ourselves first; everything renamed is already on disk, a line still queued would be lost to SIGKILL just the same."""
    global _exit_timer
    if _exit_timer is None:
        _exit_timer = threading.Timer(SERVE_EXIT_GRACE, os._exit, (0,))
        _exit_timer.daemon = True
        _exit_timer.start()


def cmd_serve(a) -> None:
    from .serve import Host
    st = State()
    _need_init(st)
    from .config_migrations import run
    from . import preferences
    try:
        run(st)  # stopped-host, locked, idempotent model migration before activation
    except (preferences.ConfigError, OSError):
        # Preserve Host's existing last-good recovery for a manually broken JSON5 file.
        # Migration rollback already restored disk; doctor reports the pending step.
        st.log("config_migration_failed")
    host = Host(st, events=a.events, read_stdin=not a.no_stdin)

    async def main():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: (host.stopping.set(), _serve_exit_guard()))
        if a.events == "quiet":     # service mode: metadata only (no message text ever reaches the journal / log)
            print(f"agentj serve {__version__} · 通道 {host.channel} · service mode (--events quiet)", flush=True)
            print(_agent_line(st), flush=True)
        if a.events == "text":
            print(f"agentj serve · 通道 {host.channel} · 输入一行回车 = 发给所有已批准设备；Ctrl-C 退出", flush=True)
            print(f"Agent：{_name_or_unset(st)} · Agent 管理页：运行 `agentj admin`", flush=True)
            print(_agent_line(st), flush=True)
        await host.run()

    asyncio.run(main())


UTF8_TERMINALS = ("Apple_Terminal", "iTerm.app", "vscode", "WezTerm", "ghostty")


def _qr_mode(out=None, environ=None) -> str:
    """How to draw the pairing QR on this terminal: "compact" (Unicode half blocks, needs a UTF-8 terminal), "ansi" (colour
    blocks, any ANSI terminal — also a plain SSH session with LANG=C), "ascii" (no colours either: TERM=dumb / not a tty)."""
    out = out or sys.stdout
    e = os.environ if environ is None else environ
    enc = (getattr(out, "encoding", None) or "ascii").lower()
    try:
        "▀▄█".encode(enc)
        utf = True
    except (LookupError, UnicodeEncodeError):
        utf = False
    # Python itself writes UTF-8 under the C locale (PEP 538/540), so the stream says nothing about the terminal: a server
    # SSH session without a UTF-8 locale (LANG unset / C / POSIX) gets the colour-block QR, which needs no Unicode at all
    ctype = e.get("LC_CTYPE") or ""
    if ctype.lower().replace("-", "") == "c.utf8":   # what Python's own C-locale coercion puts here: not the terminal's word
        ctype = ""
    loc = (e.get("LC_ALL") or ctype or e.get("LANG") or "").lower().replace("-", "")
    # a terminal program that always speaks UTF-8 (macOS Terminal / iTerm2 often run with no LANG at all — the ANSI
    # fallback is twice as wide and wraps in an 80-column window)
    if "utf8" not in loc and e.get("TERM_PROGRAM") not in UTF8_TERMINALS:
        utf = False
    if utf and not getenv("AGENTJ_QR_ASCII", environ=e):
        return "compact"
    if e.get("TERM", "") not in ("", "dumb") and not getenv("AGENTJ_QR_ASCII", environ=e) and getattr(out, "isatty", lambda: False)():
        return "ansi"
    return "ascii"


IPHONE_LINE_ZH = "iPhone：请把链接粘贴到主屏幕图标打开的页面里，不要用 Safari 打开。"
IPHONE_LINE_EN = "iPhone: paste the link into the page opened from the home-screen icon, not into Safari."


def link_text(link: str, mins: int) -> str:
    """The same one-use capability as the QR, always visible in the owner's terminal."""
    return (f"\n方法二：把链接粘到电脑浏览器地址栏直接打开（{mins} 分钟内有效，只能用一次；它就是配对密钥，别发给别人）：\n"
            f"Way 2: paste the link into a computer browser's address bar (valid {mins} min, works once; it is the pairing key, never share it):\n"
            f"{link}\n{IPHONE_LINE_ZH}\n{IPHONE_LINE_EN}\n")


QR_MAX_WIDTH = 80   # columns: a QR wider than the terminal wraps and cannot be scanned


def qr_half_blocks(qr, border: int | None = None) -> list[str]:
    """The QR in half-height blocks: one text line = two module rows, one column = one module, so a pairing link (QR
    version 8, 49 modules since ADR-A177; an old-format link was version 13, 69) is ≤ 80 columns. Light modules are drawn (█ ▀ ▄), dark ones are spaces — right on a dark
    terminal, the usual one (the light quiet zone is drawn too, so the code has its white frame). The quiet zone is 4
    modules when that still fits in QR_MAX_WIDTH, else 2. An odd last row is paired with one more light row."""
    if border is None:
        border = 4 if qr.symbol_size(scale=1, border=4)[0] <= QR_MAX_WIDTH else 2
    rows = [list(r) for r in qr.matrix_iter(scale=1, border=border)]
    if len(rows) % 2:
        rows.append([False] * len(rows[0]))      # False = light
    glyph = {(False, False): "█", (False, True): "▀", (True, False): "▄", (True, True): " "}
    return ["".join(glyph[(bool(t), bool(b))] for t, b in zip(top, bot)) for top, bot in zip(rows[::2], rows[1::2])]


def qr_from_half_blocks(lines: list[str]) -> list[list[bool]]:
    """The inverse of qr_half_blocks (tests): text lines → module rows, True = dark."""
    top = {"█": False, "▀": False, "▄": True, " ": True}
    bot = {"█": False, "▀": True, "▄": False, " ": True}
    out = []
    for ln in lines:
        out.append([top[c] for c in ln])
        out.append([bot[c] for c in ln])
    return out


def _qr_ascii(qr, border: int = 2) -> str:
    """Plain characters only: '##' = dark module, two spaces = light (reads like a printed code on a light background;
    on a dark terminal some phones still read it inverted — otherwise use --link)."""
    rows = []
    for row in qr.matrix_iter(scale=1, border=border):
        rows.append("".join("##" if dark else "  " for dark in row))
    return "\n".join(rows)


def _print_qr(link: str) -> None:
    from . import wire
    qr = wire.pairing_qr(link)   # ADR-A177: digits in a numeric segment (version 8, 49 modules)
    mode = _qr_mode()
    if mode == "compact":
        lines = qr_half_blocks(qr)
        if sys.stdout.isatty():   # white on black, whatever the terminal's own colours (a light theme would invert it)
            lines = [f"\x1b[97;40m{ln}\x1b[0m" for ln in lines]
        print("\n".join(lines), flush=True)
        return
    if mode == "ansi":
        qr.terminal(compact=False, border=2)
    else:
        print(_qr_ascii(qr), flush=True)
    import shutil
    width = 2 * qr.symbol_size(scale=1, border=2)[0]
    if width > shutil.get_terminal_size((QR_MAX_WIDTH, 24)).columns:
        print(f"（这个终端不能显示紧凑的二维码，它有 {width} 列宽：把窗口拉宽到能完整显示，或者用 `agentj pair --link`。"
              f"/ This terminal cannot show the compact QR code; it is {width} columns wide: widen the window until it "
              "fits, or use `agentj pair --link`.）", flush=True)


def _remote_session(environ=None) -> bool:
    """A terminal over SSH without a desktop (a cloud server): no browser, a QR may not render well."""
    e = os.environ if environ is None else environ
    return bool(e.get("SSH_CONNECTION") or e.get("SSH_TTY")) and not (e.get("DISPLAY") or e.get("WAYLAND_DISPLAY"))


SERVE_NOT_RUNNING = ("✗ Agent J 现在没在这台Agent J 主机上运行，所以没法配对手机。先运行 `agentj service start` 让它一直在后台运行"
                     "（想看它在不在跑：`agentj service status`），然后再运行一次 `agentj pair`。/ Agent J is not running on this "
                     "computer, so a phone cannot be paired. Run `agentj service install` to keep it running in the background "
                     "(check with `agentj service status`), then run `agentj pair` again.")
SERVE_BUSY = ("✗ Agent J 在运行，但没有响应。运行 `agentj service status` 看看，或者重启它（`agentj service install`），再配对。"
              "/ Agent J is running but not answering: check `agentj service status` or restart it with `agentj service install`, "
              "then pair again.")


def _serve_or_exit(st: State) -> dict:
    """S-10: pairing goes through the running serve. Fail fast, before any prompt, with what to do. → serve's status."""
    try:
        up = names.ctl_call(st, {"cmd": "status"}, 5)
    except names.ServeBusy:
        sys.exit(SERVE_BUSY)
    if up is None:
        sys.exit(SERVE_NOT_RUNNING)
    return up


def _pair_check(st: State, status: dict | None) -> dict:
    from . import pairprep
    return pairprep.precheck(st, (status.get("relay_up") is True) if isinstance(status, dict) else None)


CODE_PROMPT = "输入手机上显示的 6 位安全码并回车（120 秒内；直接回车 = 拒绝）/ Type the 6-digit code shown on the phone, Enter: "
PAIR_AGAIN = ("重来：再运行一次 `agentj pair`（SSH 里用 `agentj pair --link`），在手机上打开新的链接。/ To retry, run `agentj pair` "
              "again (over SSH: `agentj pair --link`) and open the new link on the phone.")
PAIR_NO_TTY = ("（这里不是交互终端，没法输安全码。想在终端里批准，请在真正的终端里运行 `agentj pair`。/ Not an interactive terminal, so "
               "the safety code cannot be typed here; run `agentj pair` in a real terminal to approve from it.）")
PAIR_NOT_TERMINAL = ("✗ 这个终端不能直接批准：它不是交互终端，或者是在 Agent 里运行的。设备还在等——到账户页用通行密钥批准，或者自己"
                     "在真正的终端里重新运行 `agentj pair`。/ This terminal cannot approve (not interactive, or run from an Agent); "
                     "the phone keeps waiting — approve it on the account page.")


PAIR_SET_FIRST = ("在终端里配对，要先设一个「遥控器配对密码」（至少 8 位，只存在这台Agent J 主机上）。以后在这里、或者在别的Agent J 主机的浏览器上"
                  "批准新遥控器，都可以用它证明是你。只有你自己知道，别让 Agent 替你设。\nPairing at the terminal needs a remote "
                  "pairing password first (at least 8 characters, kept on the Agent J host only). It proves it's you when you approve "
                  "a new remote here or from a browser on another computer. Set it yourself, never through an Agent.")
PAIR_NEED_PASS = ("✗ 这台Agent J 主机还没设遥控器配对密码，所以不能在这里配对。请你自己在这台Agent J 主机的终端里运行 `agentj pair`（它会先请你设密码），"
                  "或者到账户页 {url} 这台Agent J 主机的席位卡点「添加遥控器」。/ the Agent J host has no remote pairing password yet, so it "
                  "can't pair from here. Run `agentj pair` yourself in a terminal on the Agent J host (it asks you to set one "
                  "first), or choose Add a remote on the Agent J host's seat card at {url}.")


def cmd_pair(a) -> None:
    from . import pairprep
    st = State()
    _need_init(st)
    if getattr(a, "check", False):   # P127: the shared pre-check only (install helper, agentj-pair skill) — no pairing starts
        try:
            status = names.ctl_call(st, {"cmd": "status"}, 3)
        except names.ServeBusy:
            status = None
        chk = _pair_check(st, status)
        print(json.dumps({**chk, "serve": status is not None}) if getattr(a, "json", False) else pairprep.both(chk))
        return
    chk = _pair_check(st, _serve_or_exit(st))
    if chk["route"] == "seat":          # P185: no usable seat → no pairing; say where to bind / renew
        sys.exit("✗ " + pairprep.both(chk))
    if not chk["passphrase_set"]:
        # P221 (Leo 10-13): pairing at the terminal always needs the remote pairing password — set it first, then pair
        # (replaces P176's "a bound host's keyboard is enough"). A pipe / an Agent's shell cannot set it: say where to go.
        url = chk.get("account_url") or cloud.DEFAULT_APP
        if not sys.stdin.isatty():
            sys.exit(PAIR_NEED_PASS.format(url=url))
        print(PAIR_SET_FIRST, flush=True)
        _set_passphrase_interactive(st, change=False)
        chk = {**chk, "passphrase_set": True, "route": "account" if chk["route"] == "account" else "passphrase"}
    if chk["route"] == "account":
        print(pairprep.both(chk) + "\n", flush=True)
    passkey_only = chk["route"] == "account" and not chk["passphrase_set"]

    async def main():
        c = await _ctl(st)
        if not c:
            sys.exit(SERVE_NOT_RUNNING)
        r, w = c
        w.write(b'{"cmd":"pair"}\n')
        await w.drain()
        first = json.loads(await r.readline() or b"{}")
        if first.get("ev") != "link":
            sys.exit(f"无法开始配对：{first.get('reason', first)}")
        if not a.no_qr:
            print("方法一：用手机扫码 / Way 1: scan with your phone", flush=True)
            _print_qr(first["link"])
        mins = max(1, (first['expires'] - int(time.time())) // 60)
        print(link_text(first["link"], mins), flush=True)
        if st.device_full():
            print(f"注意：本机已有 {MAX_DEVICES} 台遥控器（上限）。批准新设备时会自动解绑最久没用的那台（离线的先走）。\n", flush=True)

        reading: asyncio.Future | None = None   # the one outstanding readline on serve's stream

        def next_line() -> asyncio.Future:
            nonlocal reading
            if reading is None:
                reading = asyncio.ensure_future(r.readline())
            return reading

        async def take() -> dict:
            nonlocal reading
            f, reading = next_line(), None
            line = await f
            if not line:
                sys.exit("agentj serve 断开了")
            return json.loads(line)

        async def race(prompt: str) -> str | None:
            """Ask the human; None when serve speaks first (timeout / device gone) — then don't wait for the keyboard."""
            ask, f = _ask_async(prompt), next_line()
            done, _ = await asyncio.wait({ask, f}, return_when=asyncio.FIRST_COMPLETED)
            if f in done:
                print()
                return None
            return ask.result()

        async def send(obj: dict) -> None:
            w.write((json.dumps(obj) + "\n").encode())
            await w.drain()

        async def answer(code_only: bool) -> dict:
            """P176: ask for the phone's 6-digit code (and the passphrase when one is set) until serve answers; serve may
            speak first at any prompt (account-page approval, timeout, phone gone) — then that event is returned."""
            pw = None
            while True:
                code = await race(CODE_PROMPT)
                if code is None:
                    return await take()
                code = code.strip()
                if not code:
                    await send({"cmd": "code", "code": ""})
                    return await take()
                if not (len(code) == 6 and code.isdigit()):
                    print("✗ 安全码是手机上显示的 6 位数字，请再输一次（这次不算错）。/ The safety code is the 6 digits on the "
                          "phone; type it again (this did not count).", flush=True)
                    continue
                if code_only:
                    await send({"cmd": "code", "code": code, "terminal": True, "retry": True})
                    ev = await take()
                else:
                    while True:
                        if pw is None:
                            ask, f = _secret_async("遥控器配对密码 / remote pairing password："), next_line()
                            done, _ = await asyncio.wait({ask, f}, return_when=asyncio.FIRST_COMPLETED)
                            if f in done:
                                print()
                                return await take()
                            pw = ask.result()
                        await send({"cmd": "code", "code": code, "pass": pw, "retry": True})
                        ev = await take()
                        if ev.get("ev") != "pass_wrong":
                            break
                        pw = None
                        print(f"✗ 遥控器配对密码不对（再错 {ev.get('left', 0)} 次会锁定）。设备还在等，请再输一次。", flush=True)
                if ev.get("ev") == "code_wrong":
                    print(f"✗ 安全码不对，还能再试 {ev.get('left', 0)} 次：看清手机上的 6 位数字再输。/ Wrong safety code, "
                          f"{ev.get('left', 0)} tries left: check the 6 digits on the phone.", flush=True)
                    continue
                if ev.get("ev") == "code_refused":
                    print(PAIR_NOT_TERMINAL, flush=True)
                    return await take()
                return ev

        ev = await take()
        while True:
            kind = ev.get("ev")
            if kind == "pending":
                print(f"一台设备已连上（它自称「{ev['name']}」，名字由设备自己填，不可信）。", flush=True)
                if isinstance(ev.get("replaces"), dict):
                    print(f"它是之前配对过的同一个浏览器：批准后替换旧记录「{_dev_line(ev['replaces'])}」，不多占一台。", flush=True)
                elif isinstance(ev.get("evict"), dict):
                    print(f"已满 {ev.get('limit', MAX_DEVICES)} 台：批准后会自动解绑最早的遥控器「{_dev_line(ev['evict'])}」。"
                          "不想让它走，就直接回车拒绝，先用 `agentj revoke <设备>` 解绑别的。", flush=True)
                if st.devices():   # P161: the P153 request card also pops up on every paired remote
                    print("也可以在任一已配对的遥控器上确认（那边会弹出申请卡片）。/ "
                          "You can also confirm on any paired remote (a request card pops up there).", flush=True)
                url = chk.get("account_url") or cloud.DEFAULT_APP
                if passkey_only and not sys.stdin.isatty():   # P127 as before: no terminal to type into → the account page
                    print(f"到账户页 {url} 这台Agent J 主机的席位卡上批准这台等待中的手机（通行密钥确认）；这里会自动显示结果。"
                          f"不想批准就按 Ctrl-C。\nApprove this waiting phone on the Agent J host's seat card at {url} (passkey); "
                          "the result shows here. Ctrl-C to refuse.", flush=True)
                    print(PAIR_NO_TTY, flush=True)
                    ev = await take()
                    continue
                if passkey_only:   # P176: both ways at once — the account page's passkey, or the phone's code typed here
                    print(f"两条路任选其一，先完成的算数：① 到账户页 {url} 这台Agent J 主机的席位卡上批准（通行密钥）；"
                          f"② 直接在这里输入手机上显示的 6 位安全码。\nEither way, whichever comes first: approve on this "
                          f"computer's seat card at {url} (passkey), or type the 6-digit safety code shown on the phone here.",
                          flush=True)
                else:
                    print("安全码只显示在手机屏幕上——这个终端永远不会显示它。", flush=True)
                    if chk["route"] == "account":
                        print("（也可以直接到账户页批准，用通行密钥。/ Or approve on the account page with your passkey.）", flush=True)
                ev = await answer(passkey_only)
                continue
            if kind == "unbound":
                ev = await take()
                continue
            if kind == "approved":
                if isinstance(ev.get("replaced"), dict):
                    print(f"已替换同一浏览器的旧记录：{_dev_line(ev['replaced'])}", flush=True)
                if isinstance(ev.get("evicted"), dict):
                    print(f"已自动解绑最早的遥控器：{_dev_line(ev['evicted'])}", flush=True)
                print(f"✓ 已批准：{ev['name']}（设备 {ev['device']}）。在 serve 终端里打字就能发给它。")
                return
            reasons = {"code_mismatch": "安全码不一致，已拒绝。" + PAIR_AGAIN, "denied": "已拒绝",
                       "timeout": "120 秒内没有批准，已拒绝。" + PAIR_AGAIN,
                       "abandoned": "已取消", "bad_handshake": "握手失败", "gone": "手机断开了", "hs_timeout": "手机没有完成握手，已拒绝",
                       "device_limit": f"已达 {MAX_DEVICES} 台上限，没有批准：先解绑一台（`agentj revoke <设备>`）再配对",
                       "pass_locked": gate.MESSAGES["locked"], "pass_not_set": gate.MESSAGES["not_set"],
                       "account_cancelled": "已在账户页取消（可以重新运行 agentj pair）"}
            msg = {"expired": "二维码已过期（5 分钟），请重新运行 agentj pair", "gone": "手机断开了",
                   "replaced": "已被新的 agentj pair 取代"}.get(kind, reasons.get(ev.get("reason"), str(ev)))
            if kind == "gone" or ev.get("reason") in ("gone", "hs_timeout"):
                msg += "。这次配对已结束，链接作废，请重新运行 agentj pair / the device left: this link is spent, run agentj pair again"
            sys.exit(f"✗ {msg}")

    _run_prompting(main())


def _dev_line(d: dict) -> str:
    """「<名称> 配对于 <时间>」 for one remote in a serve event (names are device-chosen: one cleaned line)."""
    pa = d.get("paired_at")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(pa if isinstance(pa, (int, float)) and not isinstance(pa, bool) else 0))
    return f"{clean_label(str(d.get('name') or '')) or '（没有名字）'} 配对于 {when}" + ("（在线）" if d.get("online") is True else "")


_PROMPTS: list[threading.Thread] = []   # keyboard prompts started by _ask_async / _secret_async


def _prompt_thread(run) -> None:
    t = threading.Thread(target=run, daemon=True)
    _PROMPTS.append(t)
    t.start()


def _run_prompting(coro) -> None:
    """asyncio.run for a command that races keyboard prompts against serve (P153). A daemon thread still blocked in
    input() / getpass on a terminal keeps Python from finishing its exit: `agentj pair` printed 「✗ 手机断开了」 and then
    hung until Ctrl-C. When a prompt is still waiting at the end: put the terminal back (getpass turns echo off), flush,
    and leave with os._exit and the same status sys.exit would have given."""
    saved = None
    try:
        import termios
        saved = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        termios = None
    code: object = 0
    try:
        asyncio.run(coro)
    except (SystemExit, KeyboardInterrupt) as e:
        if not any(t.is_alive() for t in _PROMPTS):
            raise
        code = e.code if isinstance(e, SystemExit) else 130
    else:
        if not any(t.is_alive() for t in _PROMPTS):
            return
    if isinstance(code, str):
        print(code, file=sys.stderr)
        code = 1
    elif not isinstance(code, int):
        code = 0 if code is None else 1
    if saved is not None:
        with contextlib.suppress(Exception):
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
    with contextlib.suppress(Exception):
        sys.stdout.flush()
        sys.stderr.flush()
    os._exit(code)


def _ask_async(prompt: str) -> asyncio.Future:
    """input() on a daemon thread, so an unanswered prompt never blocks exit (with _run_prompting)."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def run():
        try:
            v = input(prompt)
        except EOFError:
            v = ""
        loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(v))

    _prompt_thread(run)
    return fut


def _secret(prompt: str) -> str:
    """The remote pairing password: never echoed on a terminal; a pipe (scripts, tests) gives one line."""
    import getpass
    if sys.stdin.isatty():
        try:
            return getpass.getpass(prompt)
        except EOFError:
            return ""
    print(prompt, end="", flush=True)
    return (sys.stdin.readline() or "").rstrip("\r\n")


def _secret_async(prompt: str) -> asyncio.Future:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def run():
        v = _secret(prompt)
        loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(v))

    _prompt_thread(run)
    return fut


def _gate_msg(e: gate.GateError) -> str:
    if e.reason == "locked":
        return f"遥控器配对密码连续输错，锁定中（还要 {max(1, e.info.get('seconds', 60) // 60)} 分钟）。"
    if e.reason == "wrong":
        return f"遥控器配对密码不对（再错 {e.info.get('left', 0)} 次会锁定）。"
    return gate.MESSAGES.get(e.reason, e.reason)


def _set_passphrase_interactive(st: State, change: bool) -> None:
    old = _secret("现在的遥控器配对密码：") if change else None
    print("遥控器配对密码：在这台Agent J 主机的终端配对，或者在别的Agent J 主机的浏览器上批准新遥控器时，用它证明是你。只有你知道——别告诉 Agent，"
          "也别让 Agent 替你设。/ The remote pairing password proves it's you when pairing at this terminal or approving a new "
          "remote from a browser on another computer. Only you know it: never tell an Agent, never let one set it.", flush=True)
    print(PASS_WHERE, flush=True)
    new = _secret(f"新的遥控器配对密码（至少 {gate.MIN_LEN} 个字符）：")
    if _secret("再输一次：") != new:
        sys.exit("✗ " + gate.MESSAGES["mismatch"])
    try:
        gate.set_passphrase(st, new, old)
    except gate.GateError as e:
        sys.exit("✗ " + _gate_msg(e))
    print("✓ 遥控器配对密码已保存。/ remote pairing password saved.")


PASS_WHERE = ("它只存在这台Agent J 主机上（我们的服务器上没有），请自己记好；已绑定账号的Agent J 主机在账户页席位卡上加手机，用不到它；忘了就在这台Agent J 主机上"
              "重设（`agentj passphrase reset --phone`：已配对手机确认，所有手机保持配对）。\nIt is kept only on this Agent J host (never on "
              "our server), so note it somewhere safe. An Agent J host bound to the account adds phones from the account-page seat card "
              "without it. Forgot it? Reset it on this Agent J host (`agentj passphrase reset --phone`: a paired phone confirms, every "
              "phone stays paired).")
PASS_OFFER = ("要不要现在设一个遥控器配对密码？（可选，推荐）它用于在别的Agent J 主机或浏览器上批准新遥控器的最后一步，也是在这台Agent J 主机的终端里配对时"
              "必须的。不设也能用这台Agent J 主机的浏览器完成首次配对（账户页 → 添加遥控器 → 通行密钥），但将来所有遥控器都失效时，可能要回到这台"
              "Agent J 主机的终端设它。它只存在这台Agent J 主机上。\nSet a remote pairing password now? (Optional, recommended.) It is the last step when approving a new "
              "remote from another computer or browser, and it is required to pair at the Agent J host's terminal. You can skip it "
              "and pair the first remote in the Agent J host's browser (account page → Add a remote → passkey), but if every remote "
              "is ever lost you may have to come back to this terminal to set it. It is kept only on this Agent J host.")
PASS_OFFER_SKIPPED = ("已跳过。以后随时在这台Agent J 主机的终端运行 `agentj passphrase set` 设置。/ Skipped. Set it any time with "
                      "`agentj passphrase set` in a terminal on the Agent J host.")


def _passphrase_offer(st: State) -> str:
    """P221 item 5 (install end): ask once; Enter skips. → 'set' | 'skipped' | 'already' | 'no_terminal'."""
    if gate.is_set(st):
        print("✓ 遥控器配对密码已经设置过。/ A remote pairing password is already set.", flush=True)
        return "already"
    if not sys.stdin.isatty():
        print(PASS_OFFER + "\n" + PASS_OFFER_SKIPPED, flush=True)
        return "no_terminal"
    print(PASS_OFFER, flush=True)
    try:
        yes = input("现在设置？输入 y 回车设置，直接回车跳过 / Set it now? y + Enter to set, Enter to skip: ").strip().lower()
    except EOFError:
        yes = ""
    if yes not in ("y", "yes", "是", "好"):
        print(PASS_OFFER_SKIPPED, flush=True)
        return "skipped"
    _set_passphrase_interactive(st, change=False)
    return "set"


def cmd_passphrase(a) -> None:
    st = State()
    _need_init(st)
    if a.mode == "offer":
        res = _passphrase_offer(st)
        if getattr(a, "json", False):
            print(json.dumps({"result": res, "set": gate.is_set(st)}))
        return
    if a.mode == "set":
        if gate.is_set(st):
            sys.exit(gate.MESSAGES["exists"])
        _set_passphrase_interactive(st, change=False)
    elif a.mode == "change":
        if not gate.is_set(st):
            sys.exit(gate.MESSAGES["not_set"])
        _set_passphrase_interactive(st, change=True)
    elif a.mode == "reset" and getattr(a, "phone", False):
        _passphrase_reset_phone(st)
    elif a.mode == "reset":
        n = len(st.devices())
        if n:                    # #118: with a paired remote the owner can keep them all
            print("想保留所有遥控器：改用 `agentj passphrase reset --phone`，在已配对手机上确认即可。/ To keep every remote, run "
                  "`agentj passphrase reset --phone` and confirm on a paired phone.", flush=True)
        print(f"忘了遥控器配对密码：重置会删除它，并吊销本机全部 {n} 台遥控器（之后用新密码重新配对）。", flush=True)
        if input("确认重置？输入 RESET 回车：").strip() != "RESET":
            sys.exit("没有重置。")
        for did in list(st.devices()):
            try:
                res = names.ctl_call(st, {"cmd": "revoke", "device": did}, 10)
            except names.ServeBusy:
                sys.exit("agentj serve 在运行但没有响应，没有重置")
            if res is not None and res.get("error") in ("claim_required", "claim_refused"):
                sys.exit("✗ " + _ctl_refused_msg(res) + "（没有重置 / nothing reset）")
            if res is None:
                st.remove_device(did)
        gate.reset(st)
        print("✓ 已重置，所有手机都已解除配对。运行 `agentj pair` 重新配对：它会先请你设一个新的遥控器配对密码。/ Reset: every "
              "phone is unpaired. Run `agentj pair` to pair again; it asks you to choose a new remote pairing password first.")
    else:
        left = gate.lock_left(st)
        print(("已设置" + (f"（锁定中，还要 {max(1, left // 60)} 分钟）" if left else "")) if gate.is_set(st)
              else "还没设置：运行 `agentj passphrase set`")


PASS_PHONE_ERR = {
    "no_device": "没有已配对的遥控器可以确认：改用 `agentj passphrase reset`（会吊销全部遥控器）。/ No paired remote can confirm: "
                 "use `agentj passphrase reset` (unpairs every remote).",
    "not_set": gate.MESSAGES["not_set"],
    "denied": "手机上拒绝了：遥控器配对密码没变。/ Denied on the phone: nothing changed.",
    "timeout": "5 分钟内没人在手机上确认：遥控器配对密码没变。/ Nobody confirmed on a phone in 5 minutes: nothing changed.",
    "stopped": "已急停（全部停下）：先恢复再重设。/ Everything is stopped: resume first.",
    "busy": "已经有一张重设卡片在等手机确认。/ A reset card is already waiting on the phones.",
    "ticket": "确认已过期：重新运行 `agentj passphrase reset --phone`。/ The confirmation expired: run it again.",
}


def _passphrase_reset_phone(st: State) -> None:
    """#118 (pass_reset.py): a paired remote confirms, the owner types the new password HERE, every remote stays paired."""
    from . import ctl_auth, pass_reset
    if set(os.environ) & set(ctl_auth.AGENT_ENV) or not sys.stdin.isatty():
        sys.exit("✗ 只有主人能在自己的终端里重设遥控器配对密码，Agent 和脚本做不了。/ Only the owner, typing in their own "
                 "terminal, can reset the remote pairing password — not an Agent or a script.")
    if not gate.is_set(st):
        sys.exit("✗ " + gate.MESSAGES["not_set"])
    if not st.devices():
        sys.exit("✗ " + PASS_PHONE_ERR["no_device"])
    code = f"{secrets.randbelow(10 ** 6):06d}"
    print(f"已在已配对的手机上发出确认卡片，对照码 {code[:3]} {code[3:]}。请在手机上核对后确认（5 分钟内；有通行密钥的手机要验证本人）。\n"
          f"A card went to your paired phones with match code {code[:3]} {code[3:]}. Check it and confirm there (within 5 "
          f"minutes; a phone with a passkey asks you to verify it's you).", flush=True)
    try:
        res = names.ctl_call(st, {"cmd": "passphrase_consent", "code": code}, pass_reset.CARD_TTL + 30)
    except names.ServeBusy:
        sys.exit("✗ agentj serve 在运行但没有响应：没有重设。/ agentj serve is running but not answering: nothing changed.")
    if res is None:
        sys.exit("✗ agentj serve 没在运行，手机收不到卡片。先启动服务（`agentj service start`），或改用 `agentj passphrase reset`"
                 "（会吊销全部遥控器）。/ agentj serve is not running, so no phone can confirm.")
    if not res.get("ok"):
        if res.get("error") in ("claim_required", "claim_refused"):
            sys.exit("✗ " + _ctl_refused_msg(res))
        sys.exit("✗ " + PASS_PHONE_ERR.get(res.get("error"), "没有重设 / nothing changed"))
    print(f"✓ 「{res.get('by') or '?'}」已确认。现在在这里输入新的遥控器配对密码（只存在这台Agent J 主机上）。/ Confirmed on the phone. "
          "Type the new remote pairing password here (it is kept only on this Agent J host).", flush=True)
    for _ in range(3):
        new = _secret(f"新的遥控器配对密码（至少 {gate.MIN_LEN} 个字符）：")
        if _secret("再输一次：") != new:
            print("✗ " + gate.MESSAGES["mismatch"], flush=True)
            continue
        try:
            gate.check_new(new)
        except gate.GateError as e:
            print("✗ " + _gate_msg(e), flush=True)
            continue
        try:
            out = names.ctl_call(st, {"cmd": "passphrase_replace", "ticket": res["ticket"], "new": new}, 30)
        except names.ServeBusy:
            sys.exit("✗ agentj serve 没有响应：没有重设。/ agentj serve is not answering: nothing changed.")
        if out and out.get("ok"):
            print(f"✓ 遥控器配对密码已重设，{out.get('kept', 0)} 台遥控器都保持配对。/ Remote pairing password reset; every "
                  "remote stays paired.")
            return
        if out and out.get("error") == "passphrase":
            print("✗ " + gate.MESSAGES.get(out.get("reason"), "没有重设"), flush=True)
            continue
        sys.exit("✗ " + PASS_PHONE_ERR.get((out or {}).get("error"), "没有重设 / nothing changed"))
    sys.exit("✗ 没有重设：遥控器配对密码没变。/ Nothing changed.")


def _name_or_unset(st: State) -> str:
    n = st.agent_name()
    return f"「{n}」" if n else "还没起名（`agentj name <名字>`）"


def cmd_name(a) -> None:
    st = State()
    _need_init(st)
    if a.name is None:
        n = st.agent_name()
        print(f"Agent 名：「{n}」" if n else "Agent 名：还没起名。")
        if not n:
            print("起个名字：`agentj name <名字>`。可以参考：" + "、".join(f"「{x}」" for x in STARTER_NAMES) + "，或者自己起一个。")
        return
    res = names.rename(st, a.name)
    if res["ok"]:
        where = "账号后台和这台Agent J 主机都改好了" if res["linked"] else "这台Agent J 主机没加到 Agent J 账号里，只改了这台Agent J 主机上的"
        print(f"✓ Agent 名改为「{res['name']}」（{where}）")
        return
    msg = names.RENAME_MESSAGES.get(res["error"], "名字没改")
    if res["error"] == "name_too_long":
        msg = names.too_long_message(a.name)                 # P393: how long it is against the limit
    if res["error"] == "name_taken" and res["suggestions"]:
        msg += "。可以试试：" + "、".join(f"「{x}」" for x in res["suggestions"])
    elif res["error"] == "name_taken":
        msg += "，换一个吧"
    sys.exit("✗ " + msg)


def ssh_tunnel_hint(port: int, environ=None) -> str | None:
    """`agentj admin` over SSH: the page listens on 127.0.0.1 of the server only — reach it through an SSH tunnel."""
    e = os.environ if environ is None else environ
    conn = (e.get("SSH_CONNECTION") or "").split()
    if len(conn) < 4:
        return None
    import getpass
    server = conn[2]
    host = f"[{server}]" if ":" in server else server
    return (f"你是用 SSH 登录的：管理页只在这台服务器的 127.0.0.1 上。在你自己的Agent J 主机上另开一个终端运行\n"
            f"  ssh -N -L {port}:127.0.0.1:{port} {getpass.getuser()}@{host}\n"
            f"再在那台Agent J 主机的浏览器里打开上面的链接。/ Over SSH: run the line above on your own computer, then open the link there.")


def _desktop_env(environ=None) -> dict | None:
    """`--open` needs a desktop browser on the Agent J host → the environment to open it with, or None. Never over SSH without
    a desktop. On Linux/BSD the Agent usually runs under the background service, whose environment may lack the display:
    find the owner's own Wayland socket / X display (without one Python's webbrowser falls back to a console browser,
    which would hang a detached process)."""
    e = dict(os.environ if environ is None else environ)
    if _remote_session(e):
        return None
    if not sys.platform.startswith(("linux", "freebsd", "openbsd", "netbsd")) or e.get("DISPLAY") or e.get("WAYLAND_DISPLAY"):
        return e
    import glob
    import stat
    run = e.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    for sock in sorted(glob.glob(os.path.join(run, "wayland-[0-9]*"))):
        try:
            st = os.stat(sock)
        except OSError:
            continue
        if stat.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid():
            return {**e, "XDG_RUNTIME_DIR": run, "WAYLAND_DISPLAY": os.path.basename(sock)}
    xs = sorted(glob.glob("/tmp/.X11-unix/X[0-9]*"))
    if xs:
        return {**e, "DISPLAY": ":" + os.path.basename(xs[0])[1:]}
    return None


ADMIN_OPEN_POLL = 5.0   # s between "is the opened page still in use?" checks


def admin_open_serve(srv, report, opener=None, stop=None, poll=ADMIN_OPEN_POLL) -> bool:
    """P163 `agentj admin --open`, the detached half: open one fresh link in the default browser (the link never reaches
    stdout, so it never lands in an Agent transcript), `report` one event dict, then keep the page up while it is
    in use — the link not yet opened (≤ 10 min) or a logged-in page (30 min idle / 8 h max, admin.py). → opened?"""
    import webbrowser
    try:
        opened = bool((opener or webbrowser.open)(srv.new_link()))
    except Exception:
        opened = False
    report({"ev": "opened" if opened else "no_browser", "port": srv.port, "pid": os.getpid()})
    if opened:
        stop = stop or threading.Event()
        while not stop.wait(poll) and srv.in_use():
            pass
    return opened


ADMIN_OPEN_MSG = {
    "opened": "已在这台Agent J 主机的默认浏览器打开 Agent 管理页（只在 127.0.0.1:{port}；链接只用一次，页面 30 分钟不用会自动关闭，"
              "要再开就再运行 agentj admin --open）/ Opened the Agent admin page in the Agent J host's default browser "
              "(127.0.0.1:{port} only; one-time link, closes after 30 minutes unused — run agentj admin --open again to reopen)",
    "no_browser": "这里没法自动打开浏览器（SSH 登录、没有桌面或浏览器打不开）：请主人在自己的终端运行 agentj admin，把它打印的链接"
                  "粘到这台Agent J 主机的浏览器里 / Cannot open a browser here (SSH, no desktop or no browser): the owner runs "
                  "agentj admin in their own terminal and pastes the printed link into the Agent J host's browser",
}


def spawn_admin_open(port: int = 0, environ=None):
    """Start the detached `agentj admin --open-child` and wait for its one line → (event dict, Popen | None).
    Used by the CLI on the owner's own computer and by serve for a fenced Agent (elevate.py `admin_open`)."""
    import subprocess
    env = _desktop_env(environ)
    if env is None:
        return {"ev": "no_browser", "port": None}, None
    cmd = [os.path.abspath(sys.executable), "-P", "-m", "agentj.cli", "admin", "--open-child", "--port", str(int(port))]
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True, text=True, env=env)
    except OSError:
        return {"ev": "no_browser", "port": None}, None
    box: list = []
    t = threading.Thread(target=lambda: box.append(p.stdout.readline()), daemon=True)
    t.start()
    t.join(60)
    try:
        ev = json.loads(box[0]) if box and box[0] else {}
    except ValueError:
        ev = {}
    if ev.get("ev") != "opened":
        if p.poll() is None:
            p.terminate()
        return {"ev": "no_browser", "port": None}, None
    return {k: ev[k] for k in ("ev", "port", "pid") if k in ev}, p


def _admin_open(a) -> None:
    """`agentj admin --open`: returns at once (an Agent's shell call must not block); the page runs in a detached child.
    Inside the Agent's fence (state hidden, own pid namespace) the host opens it instead, through the Agent socket."""
    st = State()
    if not st.exists():
        from . import elevate
        import pathlib
        if pathlib.Path(os.environ.get(elevate.ENV) or st.perm_dir / elevate.SOCK_NAME).exists():
            res = elevate.client_request(st, {"t": "admin_open"}, timeout=90)
            ev = res if isinstance(res, dict) and res.get("ev") in ("opened", "no_browser") else {"ev": "no_browser", "port": None}
            _admin_open_report(a, ev)
            sys.exit(0 if ev["ev"] == "opened" else 1)
    _need_init(st)
    ev, _ = spawn_admin_open(a.port)
    _admin_open_report(a, ev)
    if ev["ev"] != "opened":
        sys.exit(1)


def _admin_open_report(a, ev: dict) -> None:
    if a.events == "jsonl":
        print(json.dumps(ev), flush=True)
    else:
        print(ADMIN_OPEN_MSG[ev["ev"]].format(port=ev.get("port")), file=sys.stdout if ev["ev"] == "opened" else sys.stderr,
              flush=True)


def _admin_open_child(a) -> None:
    from .admin import AdminServer
    st = State()
    _need_init(st)

    def report(ev: dict) -> None:   # the parent reads exactly one line; afterwards stdout goes nowhere (its pipe closes)
        print(json.dumps(ev), flush=True)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.close(devnull)
    try:
        srv = AdminServer(st, port=a.port).start()
    except OSError:
        report({"ev": "no_browser", "port": None})
        return
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        admin_open_serve(srv, report, stop=stop)
    finally:
        srv.stop()


def cmd_admin(a) -> None:
    if getattr(a, "open_child", False):
        return _admin_open_child(a)
    if getattr(a, "open", False):
        if a.url_file:
            _refuse("--open 和 --url-file 只能选一个 / --open and --url-file cannot be combined")
        return _admin_open(a)
    from .admin import AdminServer
    st = State()
    _need_init(st)
    try:
        srv = AdminServer(st, port=a.port).start()
    except OSError as e:
        sys.exit(f"Agent 管理页启动失败：端口 {a.port} 用不了（{e.strerror}）")
    url_fd = None
    if a.url_file:
        import os
        try:   # exclusive create, 0600: never follow / reuse an existing file (another user's symlink, a stale secret)
            url_fd = os.open(a.url_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError as e:
            srv.stop()
            sys.exit(f"--url-file {a.url_file}：没法新建（{e.strerror}；它必须是一个还不存在的文件）")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    def show() -> None:
        url = srv.new_link()
        ev = {"ev": "admin", "url": url, "port": srv.port, "expires_in": 600}
        if url_fd is not None:
            import os
            os.write(url_fd, (json.dumps(ev) + "\n").encode())
            if a.events == "jsonl":
                print(json.dumps({"ev": "admin", "port": srv.port, "url_file": a.url_file, "expires_in": 600}), flush=True)
            else:
                print(f"Agent 管理页：新链接已写入 {a.url_file}（10 分钟内有效、只能用一次；之前打开的页面都已退出）", flush=True)
        elif a.events == "jsonl":
            print(json.dumps(ev), flush=True)
        else:
            print(f"Agent 管理页：{url}", flush=True)
            print("（只在本机打开；链接 10 分钟内有效、只能用一次。回车 = 打印一个新链接，旧链接和已打开的页面都作废；Ctrl-C 退出）", flush=True)
            tunnel = ssh_tunnel_hint(srv.port)
            if tunnel:
                print(tunnel, flush=True)
    show()

    def stdin_loop() -> None:
        for _ in iter(sys.stdin.readline, ""):
            if stop.is_set():
                return
            show()
    if not a.no_stdin:
        threading.Thread(target=stdin_loop, daemon=True).start()
    try:
        while not stop.wait(0.5):
            pass
    finally:
        srv.stop()
        if url_fd is not None:
            import os
            os.close(url_fd)
        if a.events == "jsonl":
            print(json.dumps({"ev": "stopped"}), flush=True)


def cmd_devices(a) -> None:
    st = State()
    _need_init(st)
    try:   # P179: pre-P179 records → self_name / owner_name (same audit-based rule as serve's start)
        st.migrate_device_names()
    except (OSError, ValueError):
        pass
    from .devnames import public_device
    devs = st.devices()
    status = _ctl_quiet(st, {"cmd": "status"}) or {}
    online = {s["device"] for s in status.get("sessions", []) if s.get("state") == "ready"}
    devs = {d: public_device(d, v, d in online) for d, v in devs.items()}
    if a.json:
        print(json.dumps(devs,
                         ensure_ascii=False, indent=1))
        return
    print(f"Agent：{_name_or_unset(st)}")
    print(_link_line(st))
    print(f"遥控器 {len(devs)}/{MAX_DEVICES}" + ("（已满：再批准新设备会自动解绑最久没用的那台；想自己挑就先 `agentj revoke <设备>`）" if len(devs) >= MAX_DEVICES else ""))
    if not devs:
        print("还没有已批准的设备。用 `agentj pair` 配对。")
    for did, v in devs.items():
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(v.get("paired_at", 0)))
        fid = "  Face ID ✓" if v.get("has_passkey") else ""   # F20: this phone can reconnect with its passkey
        print(f"{did}  {'在线' if did in online else '离线'}  {v.get('name', '')}{_name_note(v)}  配对于 {when}{fid}{' · 经账户页添加 / Added from account page' if v.get('source') == 'account' else ''}")


def _name_note(v) -> str:
    """P179: who gave a remote its name, the same words as the account page and the phone's settings."""
    n = device_names(v)
    if n["named_by"] != "owner":
        return "（名字是设备自己填的 / name set by the device）"
    own = f"；设备自己填的原名：{n['self_name']}" if n["self_name"] else ""
    return f"（主人改的名字 / name set by the owner{own}）"


def cmd_revoke(a) -> None:
    st = State()
    _need_init(st)
    try:
        res = names.ctl_call(st, {"cmd": "revoke", "device": a.device}, 10)
    except names.ServeBusy:
        sys.exit("agentj serve 在运行但没有响应：没有吊销（不会绕过它直接改准许名单）。稍后再试，或先停掉 serve。")
    if res is not None and res.get("error") in ("claim_required", "claim_refused"):
        sys.exit("✗ " + _ctl_refused_msg(res))
    if res is None:  # serve certainly not running (no socket / refused): edit the allowlist directly, under its lock
        ok = st.remove_device(a.device)
        if ok:
            st.log("revoked", device=a.device)
            print("（Agent J 现在没在运行：已经把这台设备删掉了，以后它连不上。）")
        res = {"ok": ok, "closed": 0}
    if not res.get("ok"):
        sys.exit(f"没有这台设备：{a.device}（`agentj devices` 查看）")
    print(f"已吊销 {a.device}，断开了 {res.get('closed', 0)} 个连接。")


def cmd_send(a) -> None:
    st = State()
    _need_init(st)
    try:
        res = names.ctl_call(st, {"cmd": "send", "text": a.text}, 10)
    except names.ServeBusy:
        sys.exit("agentj serve 没有响应，没有发送")
    if res is None:
        sys.exit("Agent J 现在没在运行，没有发送（`agentj service status` 看看）/ Agent J is not running: nothing sent")
    if res.get("error") in ("claim_required", "claim_refused"):
        sys.exit("✗ " + _ctl_refused_msg(res))
    if not res.get("ok"):
        sys.exit("太长了（上限 4000 字），没有发送" if res.get("error") == "too_long" else f"发送失败：{res}")
    print(f"已发给 {res.get('delivered', 0)} 台设备")


NOTIFY_WHY = {   # P365: `agentj notify` refusals — what happened and the one thing that fixes it
    "off": "主人关掉了「允许自动化往手机发消息」（手机设置或 `agentj config set notify.enabled true`）/ automation messages are off",
    "unknown": "这个自动化还没登记。主人在自己的终端运行 `agentj notify --allow {name}`（或手机设置里打开）后再发 / "
               "not registered: the owner runs `agentj notify --allow {name}` first",
    "denied": "这个自动化的开关是关的。打开：`agentj notify --allow {name}`（或手机设置）/ switched off: `agentj notify --allow {name}`",
    "agent": "Agent 不能用这个通道（只给主人自己的脚本 / 定时器用）。Agent J 定时任务请在 task.json 写 \"notify_webapp\": true，"
             "它写进 reports/ 的文件会附在任务结果上 / Agents cannot post here; a task declares notify_webapp in task.json",
    "rate": "发得太快了（每个自动化每分钟最多 6 条），没有发送 / too many posts (6 a minute per automation)",
    "shape": "--from 名字只能用字母、数字和 . _ -（不能以 task. 开头），--file 最多 4 个 / bad --from name or too many files",
    "too_long": "文字或标题太长（文字 4000 字、标题 64 字以内）/ text ≤ 4000, title ≤ 64",
    "empty": "没有内容（--text、--title、--file 至少一个），或文件是空的 / nothing to send",
    "type": "文件类型不支持（只收 png jpg gif webp pdf txt md，内容要和扩展名一致）/ only png jpg gif webp pdf txt md",
    "too_big": "文件超过 20 MB / file over 20 MB",
    "gone": "文件读不到（不存在、是链接、不是你的，或读的时候被改了）/ file missing, a link, not yours, or changed while read",
    "secret": "这个文件像是凭据（.env / 密钥文件，或文本里有密钥），不会发 / looks like a credential: not sent",
    "private": "Agent J 自己的状态目录里的文件不能发 / files in Agent J's state directory are not sent",
    "busy": "已急停：恢复之前不发 / stopped: nothing is sent until resume",
}


def _notify_switch(st, name: str, allow) -> None:
    from . import notify, preferences
    st.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(st.root / "preferences.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        preferences.ensure()
        raw, doc = preferences.read()
        rows = notify.with_switch(preferences.validate(doc), name, allow)
        res = preferences.transact(st, preferences.edit(raw, "notify.automations", rows))
    except preferences.ConfigError as e:
        sys.exit("✗ " + str(e.detail))
    finally:
        os.close(fd)
    if not res.get("ok", True):
        sys.exit("✗ " + str(res.get("error")))


def cmd_notify(a) -> None:
    """P365: the owner's own automation posts a message / files to the phone (notify.py). Never an Agent."""
    from . import fence, notify, preferences
    st = State()
    _need_init(st)
    for flag, allow in (("allow", True), ("deny", False), ("forget", None)):
        name = getattr(a, flag)
        if name:
            try:
                notify.check_name(name)
            except notify.Refused:
                sys.exit("✗ " + NOTIFY_WHY["shape"])
            _notify_switch(st, name, allow)
            print({True: f"✓ 已允许「{name}」往手机发消息（`agentj notify --from {name} …`）",
                   False: f"✓ 已关掉「{name}」：它再发会被拒绝",
                   None: f"✓ 已删除「{name}」的登记"}[allow])
            return
    if a.list:
        prefs = preferences.effective(st)
        rows = notify.rows(prefs)
        if a.json:
            print(json.dumps({"enabled": notify.enabled(prefs), "automations": rows}, ensure_ascii=False))
            return
        print("自动化消息：" + ("开" if notify.enabled(prefs) else "关（手机设置或 `agentj config set notify.enabled true`）"))
        for r in rows:
            print(f"  {'✓' if r['allow'] else '·'} {r['id']}")
        if not rows:
            print("  （还没有登记的自动化：`agentj notify --allow <名字>`）")
        return
    if not a.frm:
        sys.exit("要用 --from <自动化名字>（先由主人 `agentj notify --allow <名字>` 登记）/ --from <name> is required")
    if fence.inside():
        sys.exit("✗ " + NOTIFY_WHY["agent"])
    text = a.text or ""
    if text == "-":
        text = sys.stdin.read(64 * 1024)
    files = [os.path.abspath(os.path.expanduser(f)) for f in (a.file or [])]
    req = {"cmd": "notify", "from": a.frm, "title": a.title or "", "text": text, "files": files}
    try:
        notify.check_request(req)
    except notify.Refused as e:
        sys.exit("✗ " + NOTIFY_WHY.get(e.why, e.why).format(name=a.frm))
    try:
        res = names.ctl_call(st, req, 60)
    except names.ServeBusy:
        sys.exit("agentj serve 没有响应，没有发送 / serve did not answer: nothing sent")
    if res is None:
        sys.exit("Agent J 现在没在运行，没有发送（`agentj service status` 看看）/ Agent J is not running: nothing sent")
    if res.get("error") in ("claim_required", "claim_refused"):
        res = {"ok": False, "error": "agent"}
    if not res.get("ok"):
        why = NOTIFY_WHY.get(res.get("error"), str(res.get("error"))).format(name=a.frm)
        sys.exit("✗ " + (f"{res['file']}：" if res.get("file") else "") + why)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
        return
    print(f"已发到手机（{res.get('delivered', 0)} 台在线；不在线的打开就能看到）" + (f"，附 {res['files']} 个文件" if res.get("files") else ""))


def cmd_status(a) -> None:
    from . import host_diagnostics
    rc = host_diagnostics.request("status")
    if rc is not None:
        sys.exit(rc)
    st = State()
    _need_init(st)
    try:
        res = names.ctl_call(st, {"cmd": "status"}, 10)
    except names.ServeBusy:
        sys.exit("agentj serve 在运行但没有响应")
    if res is None and getattr(a, "json", False):
        print(json.dumps({"ok": False, "running": False, "agent_status": "down", "hint": RECONNECT_HINT}, ensure_ascii=False))
        return
    if res is None:
        print("要让手机重新连上：agentj doctor --reconnect / to bring the phone back: agentj doctor --reconnect")
        print(f"Agent J 现在没在运行（要让它一直在后台运行：`agentj service install`）· 状态目录 {st.root} · 通道 {st.config()['channel']}"
              " / Agent J is not running (keep it running: `agentj service install`)")
        print(f"Agent：{_name_or_unset(st)}")
        print(_link_line(st))
        print("遥控器配对密码：" + ("已设置" if gate.is_set(st) else "未设置（账户页用通行密钥添加遥控器；终端配对时 `agentj pair` 会先请你设）"))
        print(_estop_line(st))
        return
    link = cloud.read_cloud(st)
    res["dashboard"] = link["tenant"]["slug"] if link else "没加到 Agent J 账号"
    res["linked_via"] = link["via"] if link else None   # "seat" (seat setup code) | "code" (8-character agentj login)
    res["agent_name"] = st.agent_name()
    from .text import agent_label
    res["agent_display_name"] = agent_label(res["agent_name"])
    res["passphrase"] = gate.is_set(st)
    if res.get("agent_status") == "down" or res.get("relay_state") == "reconnecting":
        res["hint"] = RECONNECT_HINT          # P147: grey phone → one command
    if res.get("relay_last"):                 # P317: the host leg's stuck point in words (relay_last keeps the tokens)
        from . import relaydiag
        res["relay_stuck"] = relaydiag.text2(res["relay_last"])
    print(json.dumps(res, ensure_ascii=False, indent=1))


RECONNECT_HINT = "agentj doctor --reconnect"


def _link_line(st: State) -> str:
    link = cloud.read_cloud(st)
    if not link:
        return "Agent J 账号：还没加入（`agentj login`）/ not in an Agent J account yet"
    via = "用设置码加入 / joined with a setup code" if link["via"] == "seat" else "用Agent J 主机绑定码加入 / joined with a host binding code"
    return f"Agent J 账号：{link['tenant']['slug']}（{link['tenant']['name']}）· {via}"


def _report_now(st: State) -> cloud.ReportResult:
    """One report: live view from a running serve (control socket), else devices.json with everything offline."""
    view = _ctl_quiet(st, {"cmd": "report_view"})
    if view and view.get("ok"):
        online, pending = set(view.get("online") or []), view.get("pending") or {}
    else:
        online, pending = set(), {"count": 0, "since": None}
    res = cloud.send_report(st, online, pending)
    if res.kind == "ok":
        st.log("report_ok", seq=res.seq, trigger="cli")
        from . import preferences
        preferences.adopt_account_language(st, res.account)    # A1: a newer account language (serve broadcasts it)
    elif res.kind == "unbound":
        st.log("report_unbound", trigger="cli")
    elif res.kind == "fail":
        st.log("report_fail", status=res.status, trigger="cli")
    return res


_POLL_NOTES = {"slow_down": "服务器要求放慢，轮询间隔 +5 秒", "timeout": "网络超时，继续等", "network": "网络不通，继续等",
               "http_5xx": "服务器暂时出错，继续等"}


SEAT_FILE_MAX = 256
# exit codes of `agentj login --seat` (seat setup §4.1); everything else is 1 like `agentj login`, 2 = refused locally
SEAT_EXIT = {"name_taken": 3, "invalid_setup": 4, "payment_required": 5, "billing_busy": 6, "bad_code": 2, "bad_name": 2, "name_required": 2}


def _read_seat_code(a) -> str:
    """The setup code from --seat <code>, --seat - (one line on stdin) or --seat-file <path> (a regular file the human /
    agent wrote with mode 0600; refused when group / others may read it). Never printed, never logged."""
    if a.seat_file or getattr(a, "install_code_file", None):
        a.seat_file = a.seat_file or a.install_code_file
        import os
        import stat as _stat
        try:
            fd = os.open(a.seat_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as e:
            sys.exit(f"✗ --seat-file：读不了 / cannot open it ({e.strerror})")
        try:
            s = os.fstat(fd)
            if not _stat.S_ISREG(s.st_mode):
                sys.exit("✗ --seat-file 必须是普通文件 / must be a regular file")
            if s.st_mode & 0o077:
                sys.exit("✗ --seat-file 别人也能读：先 `chmod 600` 它 / group or others can read it: `chmod 600` it first")
            raw = os.read(fd, SEAT_FILE_MAX + 1)
        finally:
            os.close(fd)
        if len(raw) > SEAT_FILE_MAX:
            sys.exit("✗ --seat-file 太大，里面应该只有设置码 / too large: it should hold only the setup code")
        text = raw.decode("utf-8", "replace")
    elif a.seat == "-":
        text = sys.stdin.readline(SEAT_FILE_MAX + 1)
    else:
        text = a.seat
    return _pasted_code(text)


# P214: what reaches `--seat` / `--install-code-file` is often the whole prompt or install command, with a CRLF or a
# full-width hyphen. Keep only the first AJI installation code (else the first ajt_ setup code) of it; never print it.
_DASHES = {ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d\u30fc"}


def _pasted_code(text: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKC", text or "").translate(_DASHES)
    for t in (s, re.sub(r"\s+", "", s)):
        m = re.search(r"AJI-([0-9a-f]{32})(?![0-9a-f])", t, re.I)
        if m:
            return "AJI-" + m.group(1).lower()
    m = re.search(r"(?<![A-Za-z0-9_-])ajt_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", s)
    return m.group(0) if m else s.strip()


def _read_paste(prompt: str) -> str:
    """P214 (Leo 10-13): `agentj login` at a terminal asks for the code first. Hidden input; a pasted prompt spans lines, so
    take everything that arrives within a moment (else the rest would answer the next question). Never echoed or logged."""
    import select
    fd, old = sys.stdin.fileno(), None
    try:
        import termios
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
    except (ImportError, OSError, ValueError):
        old = None
    try:
        print(prompt, end="", flush=True)
        text = sys.stdin.readline(SEAT_FILE_MAX * 16)
        while select.select([sys.stdin], [], [], 0.5)[0]:
            more = sys.stdin.readline(SEAT_FILE_MAX * 16)
            if not more:
                break
            text += more
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print(flush=True)
    return text


PASTE_PROMPT = ("有账户页给的设置码（ajt_…）或安装码（AJI-…）就粘贴在这里回车（不显示；整段提示词也行，会自动取出码）；\n"
                "没有就直接回车，改用Agent J 主机绑定码。 / Paste a setup code (ajt_…) or installation code (AJI-…) and press Enter "
                "(hidden; a whole prompt is fine). No code? Just press Enter to use a host binding code instead.\n> ")


def _paste_login(st: State, a, forced: bool) -> bool:
    """Interactive `agentj login`: a pasted code → the seat login (no --seat-file to write); Enter → False (binding code)."""
    text = _read_paste(PASTE_PROMPT)
    if not text.strip() and not forced:
        return False
    code = _pasted_code(text)
    if not re.fullmatch(r"AJI-[a-f0-9]{32}|ajt_[A-Za-z0-9_-]{43}", code):
        _refuse("没找到设置码或安装码：请在账户页点「复制设置码」或「复制安装码」，再粘贴 / No setup code or installation code "
                "found: on the account page choose Copy setup code or Copy installation code, then paste it")
    if a.name is None and code.startswith("AJI-"):
        a.name = st.agent_name() or "Agent J"
    a.seat = code
    _seat_login(st, a)
    return True


def _seat_login(st: State, a) -> None:
    """`agentj login --seat …` (seat setup §4.1): no y/N — the human gave the code to the Agent J host's Agent, that is the
    decision. Prints the company it joined (the human must see it); never the code."""
    given = a.name is not None
    if a.name is None and getattr(a, "install_code_file", None):
        a.name = st.agent_name() or "Agent J"
    if given:
        from .text import new_agent_name_problem
        if new_agent_name_problem(a.name) == "name_too_long":   # P154: a new name must be short; the kept one is not re-checked
            _refuse(names.too_long_message(a.name))             # P393: says how long the given name is against the limit
    # P214 (Leo 10-13): no --name is no longer an error — the code is read first (it may be the stdin of `--seat -`), then the
    # name: asked at a terminal (Enter = the kept name, else the Agent J host's host name), the default otherwise.
    code = _read_seat_code(a)
    auto = _choose_name(st, a, ask=getattr(a, "paste", False) or sys.stdin.isatty())
    if getattr(a, "install_code_file", None) and not re.fullmatch(r"AJI-[a-f0-9]{32}", code):
        _refuse("没找到安装码：请只粘贴 AJI- 开头的那串码，在账户页点「复制安装码」 / No installation code found: paste only the "
                "code that starts with AJI- (account page → Copy installation code)")
    try:
        api = cloud.api_url(st, override=a.api)
    except cloud.CloudError:
        sys.exit("拒绝：API 地址必须是 https://（http:// 只允许 127.0.0.1 / localhost）")
    base = a.name
    for n in range(2, 11):
        try:
            res = cloud.seat_bind(st, api, code, a.name)
        except cloud.CloudError as e:
            sys.exit(f"连不上 Agent J 服务器（{e.kind}）：{api} / cannot reach the Agent J server")
        if res["status"] != "name_taken" or not auto:
            break
        # a name nobody typed: take the next free one the server offers, else number it — never stop to ask
        offered = [x for x in res.get("suggestions") or [] if isinstance(x, str) and x != a.name]
        a.name = offered[0] if offered else _numbered(base, n)
    s = res["status"]
    if s == "bound":
        t, name = res["tenant"], res["agent_name"]
        st.set_agent_name(name)
        st.log("agent_name_set", kind="seat")
        _ctl_quiet(st, {"cmd": "agent_name_changed"})
        print(f"✓ 已加到 Agent J 账号 {t['slug']}（{t['name']}）的一个席位，Agent 名「{name}」")
        print(f"✓ Added to a seat of the Agent J account {t['slug']} ({t['name']}); Agent name \"{name}\". "
              "Not the account you expected? Run `agentj unlink`: it also takes the Agent J host out of that account.")
        r = _report_now(st)
        print("首次上报：成功 / first report: ok" if r.kind == "ok"
              else f"首次上报失败（{r.status}），serve 启动后会自动重试 / first report failed, serve retries")
        if not getattr(a, "install_code_file", None):      # the installer starts the service and pairs on its own
            _after_bind(st)
        return
    msgs = {
        "bad_code": "设置码格式不对（应是 ajt_ 加 43 个字符）：从那句话里原样复制 / not a setup code (ajt_ + 43 characters): copy it exactly",
        "bad_name": "Agent 名不合规（不能有控制字符、不可见字符或换行）/ bad Agent name (no control or invisible characters, no line breaks)",
        "name_required": "Agent 名不能为空 / the Agent name is required",
        "name_taken": "这个账号里已经有叫这个名字的 Agent 了，换一个 / this account already has an Agent with that name",
        "invalid_setup": "设置码无效、已用过、已过期或已被作废——请向管理员要一个新的 / "
                         "the setup code is invalid, used, expired or revoked: ask the account owner for a new one",
        "payment_required": "这个席位没在付费了，请找账号的管理员 / this seat is no longer paid: contact the account owner",
        "already_bound": "账号后台里这台Agent J 主机还挂在别的地方：先请账号的管理员在账号后台把它移除 / "
                         "the Agent J host is still listed in an account dashboard: the owner removes it there first",
        "rate_limited": "尝试太频繁，过一小时再试 / too many attempts: try again within the hour",
        # P393: these two used to read as already_bound ("remove it in the dashboard first") — wrong advice for both
        "billing_busy": "账单正在处理中（比如刚在账号后台加了席位），这时不能绑定新的 Agent J 主机。设置码没有用掉，请几分钟后再运行一次；"
                        "一个多小时后还是这样，请联系账号的管理员 / a billing change is being processed for this account (for example, "
                        "seats were just added), so no Agent J host can be added right now. The setup code is still unused: run this "
                        "again in a few minutes; if it still says this after an hour or so, contact the account owner",
        "server_error": "Agent J 服务器这次出错了，没有绑定，设置码也没有用掉，请稍后再试 / the Agent J server hit an error: nothing "
                        "was bound and the setup code is still unused. Try again later",
    }
    msg = "✗ " + msgs.get(s, f"绑定失败 / bind failed（{res.get('http')}{' ' + res['error'] if res.get('error') else ''}）")
    if s == "name_taken" and res.get("suggestions"):
        msg += "\n  可以试试 / try: " + "、".join(f"「{x}」" for x in res["suggestions"])
    print(msg, file=sys.stderr)
    sys.exit(SEAT_EXIT.get(s, 1))


def _default_name(st: State) -> str:
    """The kept Agent name, else the Agent J host's host name cut to the new-name length, else 「Agent J」."""
    kept = st.agent_name()
    if kept:
        return kept
    from .text import machine_name, new_agent_name_problem
    m = (machine_name() or "").strip()
    while m and new_agent_name_problem(m):
        m = m[:-1].rstrip(" -_.")
    return m or "Agent J"


def _numbered(base: str, n: int) -> str:
    from .text import new_agent_name_problem
    stem = base
    while stem and new_agent_name_problem(f"{stem} {n}"):
        stem = stem[:-1].rstrip(" -_.")
    return f"{stem or 'Agent'} {n}"


def _choose_name(st: State, a, *, ask: bool) -> bool:
    """Fill a.name when --name was not given. True = a default nobody typed (a clash gets numbered, not refused)."""
    if a.name is not None:
        return False
    d = _default_name(st)
    if ask:
        try:
            typed = input(f"给这台Agent J 主机上的 Agent 起个名字（直接回车用「{d}」）/ Name this Agent (Enter = \"{d}\"): ").strip()
        except EOFError:
            typed = ""
        if typed:
            a.name = typed
            return False
    a.name = d
    return True


def _after_bind(st: State) -> None:
    """P214 (Leo 10-13): right after a seat bind, bring the Agent online (the account page's 「添加遥控器」 needs a running
    serve) and say the one next step. Only for the Agent J host's own install: a custom state directory without its own
    service name (tests, a second instance) never touches the service."""
    from . import service
    url = "https://agentj.app/account/"
    try:
        url = cloud.app_url(st).rstrip("/") + "/"
    except Exception:
        pass
    own = not os.environ.get("AGENTJ_STATE_DIR") or os.environ.get("AGENTJ_SERVICE_NAME")
    started = False
    if own:
        try:
            s = service.status()
            if s["kind"] != "none":
                service.restart() if s.get("installed") and s.get("active") == "active" else service.start(st)
                started = True
                print("✓ 后台服务已启动，这台Agent J 主机上线了 / the background service is running: the Agent J host is online")
        except Exception as e:      # ServiceError, a missing user manager …: say the command, never fail the bind
            print(f"后台服务没能自动启动（{getattr(e, 'args', [''])[0] or type(e).__name__}）：运行 agentj service install"
                  " / could not start the background service: run agentj service install")
    if own and not started:
        print("让它上线：运行 agentj service install（或 agentj serve）/ bring it online: agentj service install (or agentj serve)")
    print(f"下一步：添加遥控器——在账户页 {url} 这台Agent J 主机的席位卡点「添加遥控器」，或在这台Agent J 主机运行 agentj pair 扫二维码。")
    print(f"Next: add a remote — on {url} choose Add a remote on the Agent J host's seat card, or run agentj pair here and scan.")


def _refuse(msg: str) -> None:
    """Refused locally: nothing sent, nothing written — exit 2."""
    print("✗ " + msg, file=sys.stderr)
    sys.exit(2)


def _account_arg(a) -> str | None:
    """`--yes` only together with `--account <account ID>` (PROMPT-29 C-10): the agent may skip the y/N only when the
    human said yes AND named the account — the host then checks that the account dashboard reports exactly that account."""
    acct = (a.account or "").strip().lower() or None
    if a.yes and not acct:
        _refuse("--yes 只能和 --account <账号 ID> 一起用，什么都没做。只有你的主人明确说了「可以」并告诉了你账号 ID 时才这样用；"
                "否则去掉 --yes，让主人自己回答 y/N。/ --yes works only together with --account <account ID>; nothing was "
                "done. Use it only when your human explicitly said yes and told you the account ID; otherwise drop --yes and "
                "let your human answer the y/N.")
    if acct and (a.seat is not None or a.seat_file or getattr(a, "install_code_file", None)):
        _refuse("--yes / --account 只用于Agent J 主机绑定码的登录，设置码登录不需要 / --yes and --account are for the Agent J host binding code "
                "login only; the setup code login does not ask")
    if acct and not cloud._SLUG.fullmatch(acct):
        _refuse(f"「{acct}」不是一个账号 ID（3–30 位小写字母、数字和连字符），什么都没做 / not an account ID (3–30 lowercase "
                "letters, digits and hyphens); nothing was done")
    return acct


def cmd_login(a) -> None:
    st = State()
    _need_init(st)
    acct = _account_arg(a)
    link = cloud.read_cloud(st)
    if link:
        sys.exit(f"这台Agent J 主机已经加到 Agent J 账号 {link['tenant']['slug']} 里了。要换账号，先运行 `agentj unlink`（再到账号后台把这台Agent J 主机移除）。"
                 f" / the Agent J host is already in the Agent J account {link['tenant']['slug']}: run `agentj unlink` first.")
    if a.seat is not None or a.seat_file or getattr(a, "install_code_file", None):
        _seat_login(st, a)
        return
    # P214: at a terminal (or with --paste) ask for a pasted code first — no device code needed when the owner has one
    if not acct and (a.paste or (sys.stdin.isatty() and sys.stdout.isatty())) and _paste_login(st, a, a.paste):
        return
    if a.name is not None:
        sys.exit("--name 只和 --seat / --seat-file 一起用（用Agent J 主机绑定码的话，名字在账户页里起）/ --name goes with --seat only")
    try:
        api = cloud.api_url(st, override=a.api)
        app = cloud.app_url(st)
    except cloud.CloudError:
        sys.exit("拒绝：服务器地址必须是 https://（http:// 只允许 127.0.0.1 / localhost）/ refused: the server address must be https://")

    def show(lg: dict) -> None:
        mins = max(1, lg["expires_in"] // 60)
        print(f"这台Agent J 主机的通道号：{cloud.channel_of(st)}（账号后台里显示的应该一样）", flush=True)
        print(_hostname_notice(st), flush=True)
        # F7: the configured Dashboard; the server's verification_uri only when it is on that same origin.
        # P214 (Leo 10-13): name the code and the exact place — an owner put it into the passkey box (「通行密钥转移码」).
        page = cloud.dashboard_uri(app, lg["verification_uri"])
        page = page if "#" in page else page.rstrip("/") + "/#add-agent"
        print(f"Agent J 主机绑定码：{lg['user_code']}（{mins} 分钟内有效，只能用一次） / Host binding code", flush=True)
        # P389 (#110): the code fills one seat; its box is in each empty seat card of the account page
        print(f"到账户页一张空席位卡的「已经装好 Agent J？用 Agent J 主机绑定码」里填它，这台Agent J 主机就绑到那个席位：{page}", flush=True)
        print("  / On the account page open Agent (seats) → Add an Agent and enter it there."
              " 它不是通行密钥转移码，不要填到「登录与安全」里 / It is not a passkey transfer code.", flush=True)
        print("看一下账户页显示的通道号和上面一样，再点添加。等待中……（Ctrl-C 取消）", flush=True)

    def on_poll(kind: str) -> None:
        print(f"· {_POLL_NOTES.get(kind, kind)}", flush=True)

    mismatch: list[str] = []

    def confirm(t: dict, agent_name: str | None = None) -> bool:
        """F5: the human at this terminal confirms the tenant the Dashboard bound this host to, before anything is written.
        A3.2: the prompt also shows the Agent name the Dashboard gave this host; the same y sets it locally.
        C-10: with --account, any other account is refused here (nothing written; the login is undone on the Dashboard)."""
        if acct and t["slug"] != acct:
            mismatch.append(t["slug"])
            return False
        q = (f"加到 Agent J 账号 {t['slug']}（{t['name']}），Agent 名「{agent_name}」？[y/N] " if agent_name
             else f"加到 Agent J 账号 {t['slug']}（{t['name']}）？[y/N] ")
        if a.yes:
            print(q + "y（--yes --account）", flush=True)
            return True
        return ask_yes(q)

    try:
        res = cloud.login(st, api, show=show, confirm=confirm, on_poll=on_poll)
    except KeyboardInterrupt:
        print("\n已取消，本机没有绑定。")
        sys.exit(130)
    except cloud.CloudError as e:
        sys.exit(f"连不上 Agent J 服务器（{e.kind}）：{api} / cannot reach the Agent J server")
    st_ = res["status"]
    if st_ == "bound":
        t = res["tenant"]
        if res.get("agent_name"):
            st.set_agent_name(res["agent_name"])   # after the human's y, like cloud.json (contract §3)
            st.log("agent_name_set", kind="login")
            _ctl_quiet(st, {"cmd": "agent_name_changed"})
        print(f"✓ 已加到 Agent J 账号 {t['slug']}（{t['name']}）。/ Added to the Agent J account {t['slug']}.")
        if res.get("agent_name"):
            print(f"这台Agent J 主机的 Agent 名：「{res['agent_name']}」（在账号后台或用 `agentj name` 可以改）")
        r = _report_now(st)
        print("首次上报：成功" if r.kind == "ok" else f"首次上报失败（{r.status}），serve 启动后会自动重试")
        return
    if st_ == "declined" and mismatch:
        undone = ("已通知账号后台撤销这次添加 / the account dashboard was told to undo it" if res.get("undone") == "undone"
                  else f"没能通知账号后台撤销（{res.get('undone')}），请到账号后台把这台Agent J 主机移除 / could not tell the account "
                       "dashboard to undo it: remove the Agent J host there")
        _refuse(f"没有加入：账号后台报回来的账号是 {mismatch[0]}，和 --account {acct} 不一样。这台Agent J 主机什么都没写；{undone}。"
                f"请和主人核对账号 ID。/ Not added: the account dashboard reported the account {mismatch[0]}, not {acct}. "
                "Nothing was written on the Agent J host. Check the account ID with your human.")
    if st_ == "declined":
        t = res["tenant"]
        if res.get("undone") == "undone":
            sys.exit(f"✗ 没有加入：这台Agent J 主机什么都没写，也已经通知账号后台撤销这次添加（账号 {t['slug']} 里不会有这台Agent J 主机）。\n"
                     "  如果这不是你自己输入的代码，说明有人看到了这个终端上的代码——别再让别人看到。")
        sys.exit(f"✗ 没有加入：这台Agent J 主机什么都没写。但没能通知账号后台撤销（{res.get('undone')}），"
                 f"那边可能还把这台Agent J 主机记在账号 {t['slug']}（{t['name']}）下：\n"
                 f"  如果这是你的账号，到账号后台把这台Agent J 主机移除；如果不是你的，说明有人用了这个终端上显示的代码。"
                 f"别再让别人看到代码，并请对方（或我们）在账号后台把这台Agent J 主机移除。")
    msgs = {"already_bound": "账号后台里这台Agent J 主机还挂在别的地方：先在账号后台把它移除，再运行 `agentj login`",
            "rate_limited": "登录请求太频繁，稍后再试", "rejected": "账号后台没有接受这次添加",
            "expired": "代码已过期（10 分钟），请重新运行 `agentj login`"}
    sys.exit("✗ " + msgs.get(st_, f"登录失败（{res.get('http')}{' ' + res['error'] if res.get('error') else ''}）"))


def _hostname_notice(st: State) -> str:
    from .text import machine_name
    m = machine_name()
    if st.report_machine() and m:
        return f"加入后，账号后台会显示这台Agent J 主机的名字「{m}」（「运行在 … 上」）；不想显示：`agentj report-hostname off`"
    return "账号后台不显示这台Agent J 主机的名字（要显示：`agentj report-hostname on`）"


def cmd_report_hostname(a) -> None:
    st = State()
    _need_init(st)
    if a.mode in ("on", "off"):
        st.set_report_machine(a.mode == "on")
        st.log("report_machine_switch", status=a.mode)
        if cloud.read_cloud(st):
            _ctl_quiet(st, {"cmd": "agent_name_changed"})   # = "send a report soon", so the Dashboard sees the change
    print(_hostname_notice(st) if st.report_machine() else "关：账号后台不显示这台Agent J 主机的名字。")


NEWS_PROBLEM = {   # P374: why `agentj news` did not work — (zh, en)
    "unlinked": ("这台电脑还没加到 Agent J 账号：先运行 `agentj login`",
                 "This computer is not in an Agent J account yet: run `agentj login` first"),
    "not_bound": ("账号后台那边已经把这台电脑移除了：`agentj unlink` 后重新 `agentj login`",
                  "The account dashboard no longer has this computer: `agentj unlink`, then `agentj login` again"),
    "rate_limited": ("改得太频繁了，过几分钟再试", "Too many changes; try again in a few minutes"),
    "unreachable": ("连不上 Agent J 账号后台，稍后再试", "Could not reach the Agent J account dashboard; try again later"),
    "unsupported": ("账号后台还不支持这个设置，请在账号后台的设置里改", "The account dashboard does not offer this here yet; change it in the dashboard's settings"),
    "fail": ("没办成", "That did not work"),
    # P393 #128: the digest goes to this Agent J host's own person; a seat with no e-mail yet confirms one with a code
    "need_email": ("这台 Agent J 的新闻发到你自己的邮箱，但还没有邮箱：`agentj news email <你的邮箱>` 收验证码，"
                   "再 `agentj news verify <你的邮箱> <验证码>`",
                   "This Agent J host's news goes to your own email, and there is none yet: `agentj news email <your email>` "
                   "sends a code, then `agentj news verify <your email> <code>`"),
    "account_email": ("这台 Agent J 用的是你账号的邮箱：要换邮箱请在账号后台的设置里改",
                      "This Agent J host uses your account's email: change it in the account dashboard's settings"),
    "bad_email": ("这个邮箱地址不对，检查一下再试", "That email address does not look right; check it and try again"),
    "invalid_code": ("验证码不对或已过期：重新 `agentj news email <你的邮箱>` 收一个新的",
                     "The code is wrong or expired: run `agentj news email <your email>` for a new one"),
    "mail_failed": ("验证码邮件没发出去，过一会儿再试", "The code email could not be sent; try again in a while"),
}


def cmd_news(a) -> None:
    """P374 `agentj news [status|on|off] [--json]`: this Agent J host's Daily AI News (the daily 「Musk + AI 24h」 digest, in
    Chinese; default off) — the same signed call as the phone's switch. P393 #128: the digest goes to the seat's own person;
    `agentj news email <address>` mails a code to confirm one when the seat has none, `agentj news verify <address> <code>`
    confirms it. The e-mail and the code are never logged."""
    st = State()
    _need_init(st)
    en = _lang(st) == "en"
    args = [str(x).strip() for x in (getattr(a, "args", None) or [])]
    want = {"email": 1, "verify": 2}.get(a.mode, 0)
    if len(args) != want:
        usage = {"email": "agentj news email <address>", "verify": "agentj news verify <address> <code>"}.get(
            a.mode, f"agentj news {a.mode}")
        print(("usage: " if en else "用法：") + usage, file=sys.stderr)
        raise SystemExit(2)
    try:
        if a.mode == "email":
            res = cloud.news_email(st, args[0], "en" if en else "zh")
        elif a.mode == "verify":
            res = cloud.news_verify(st, args[0], args[1])
        else:
            res = cloud.news(st, {"on": True, "off": False}.get(a.mode))
    except Exception:  # noqa: BLE001 — a bad key / state read: one line, never a traceback
        res = cloud.NewsResult("fail", status="error")
    st.log("news_cli", mode=a.mode, status=res.kind, http=res.status, **({"on": res.on} if res.kind == "ok" else {}))
    if a.json:
        if res.kind == "sent":
            out = {"ok": True, "sent": True, "expires_in": res.expires_in}
        elif cloud.news_is_state(res):
            out = {"ok": True, "on": res.on, "email": res.email, "sample_url": res.sample_url, "queued": res.queued,
                   "seats": res.seats, **({"need_email": True} if res.kind == "need_email" else {})}
        else:
            out = {"ok": False, "problem": cloud.news_problem(res.kind), "status": res.status,
                   **({"retry_after": res.retry_after} if res.retry_after else {})}
        print(json.dumps(out, ensure_ascii=False))
        if not out["ok"]:
            raise SystemExit(1)
        return
    if res.kind == "sent":
        mins = max(1, (res.expires_in or 600) // 60)
        print(f"Code sent to {args[0]} (good for {mins} minutes). Then: agentj news verify {args[0]} <code>" if en else
              f"验证码已发到 {args[0]}（{mins} 分钟内有效）。收到后运行：agentj news verify {args[0]} <验证码>")
        return
    if res.kind == "need_email" and cloud.news_is_state(res):
        print("Daily AI News: off — " + NEWS_PROBLEM["need_email"][1] if en else
              "Daily AI News：关 — " + NEWS_PROBLEM["need_email"][0])
        if res.sample_url:
            print(f"See a sample: {res.sample_url}" if en else f"看一封样例：{res.sample_url}")
        return
    if res.kind != "ok":
        zh, enm = NEWS_PROBLEM.get(res.kind, NEWS_PROBLEM["fail"])
        sys.exit("✗ " + (enm if en else zh) + (f" ({res.status})" if res.status and res.kind in ("unreachable", "fail") else ""))
    if a.mode == "verify":
        print(f"Confirmed: this Agent J host's Daily AI News goes to {res.email}." if en else
              f"已确认：这台 Agent J 的 Daily AI News 发到 {res.email}。")
    seats = res.seats or 500
    if res.on and res.queued:
        print(f"Daily AI News: on — all {seats} seats are taken; you are number {res.queued} on the waiting list. When someone "
              f"unsubscribes you move up automatically and get an email at {res.email}." if en else
              f"Daily AI News：开 — 名额 {seats} 个已满，你在候补第 {res.queued} 位。有人退订会自动补上，并发邮件到 {res.email} 告诉你。")
    elif en:
        print(f"Daily AI News: on — sent to {res.email}" if res.on else
              f"Daily AI News: off. `agentj news on` sends the \"Musk + AI 24h\" digest our Daily Agent writes (in Chinese) "
              f"to {res.email} every morning.")
        if res.sample_url:
            print(f"See a sample: {res.sample_url}")
    else:
        print(f"Daily AI News：开 — 发到 {res.email}" if res.on else
              f"Daily AI News：关。`agentj news on` 每天早上把 Agent J 的 Daily Agent 整理的《Musk + AI 24h》（中文）发到 {res.email}。")
        if res.sample_url:
            print(f"看一封样例：{res.sample_url}")


def cmd_report(a) -> None:
    st = State()
    _need_init(st)
    link = cloud.read_cloud(st)
    if not link:
        sys.exit("这台Agent J 主机还没加到 Agent J 账号：先运行 `agentj login`")
    res = _report_now(st)
    if res.kind == "ok":
        print(f"已上报到 Agent J 账号 {link['tenant']['slug']}（seq {res.seq}）")
    elif res.kind == "unbound":
        sys.exit("账号后台那边已经把这台Agent J 主机移除了。这台Agent J 主机上的记录还在：`agentj unlink` 清掉后，可以重新 `agentj login`")
    elif res.kind == "unlinked":
        sys.exit("这台Agent J 主机还没加到 Agent J 账号：先运行 `agentj login`")
    else:
        sys.exit(f"上报失败（{res.status}）")


def cmd_unlink(a) -> None:
    st = State()
    _need_init(st)
    link = cloud.read_cloud(st)
    slug = link["tenant"]["slug"] if link else "?"
    left = False
    if link and link["via"] == "seat":
        # review SS-02: a seat link is also undone on the Dashboard (signed seat-leave), best effort — cloud.json goes anyway
        try:
            r = cloud.seat_leave(st, link["api"])
        except Exception:                                    # never let the notice keep the local unlink from happening
            r = {"status": "fail", "http": "error"}
        left = r["status"] == "left"
        if left:
            print(f"已经把这台Agent J 主机移出账号 {slug} / the Agent J host was taken out of the account {slug}.")
        elif r["status"] == "not_found":
            print(f"这台Agent J 主机已经不在账号 {slug} 的席位里了 / the Agent J host is no longer in a seat of the account {slug}.")
        else:
            why = {"rate_limited": "太频繁，稍后再试 / rate limited"}.get(r["status"], f"连不上或出错（{r.get('http', '')}）")
            print(f"没能通知账号后台把这台Agent J 主机移出账号 {slug}：{why}。请账号的管理员在账号后台收回这个席位。"
                  f" / Could not reach the account dashboard; ask the account owner to take the seat back there.")
    removed = cloud.delete_cloud(st)
    if not removed:
        print("这台Agent J 主机没加到 Agent J 账号里。")
        return
    st.log("cloud_unlinked")
    if left:
        print(f"已删掉这台Agent J 主机上的账号记录（账号 {slug}）。")
    else:
        print(f"已删掉这台Agent J 主机上的账号记录（账号 {slug}）。账号后台那边，还要在账号后台里把这台Agent J 主机移除。")


AGENT_LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode", "kimi": "Kimi Code", "qwen": "Qwen Code", "dsh": "DeepSeek Harness"}


def _route_lang() -> str:
    """Before `agentj init` there is no language setting: the locale decides (zh_* → zh)."""
    loc = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "zh" if loc.lower().startswith("zh") else "en"


def _lang(st: State) -> str:
    try:
        from . import preferences
        return "en" if preferences.get(preferences.effective(st), "appearance.language", "zh") == "en" else "zh"
    except Exception:  # noqa: BLE001 — a display language, never a failure
        return "zh"


def _agent_line(st: State, lang: str | None = None) -> str:
    """`agentj agent` and the serve start-up log: the copy follows the session mode in effect (P127) — a shared session is
    the owner's own harness process, never described as fenced."""
    lang = lang or _lang(st)
    en = lang == "en"
    c = st.agent_config()
    if not c:
        return ("Agent: none (phone messages only show in this terminal; `agentj agent claude --dir <folder>` connects Claude Code)"
                if en else "接的 Agent：无（手机消息只显示在这个终端；`agentj agent claude --dir <目录>` 接上 Claude Code）")
    label = AGENT_LABEL[c["kind"]]
    extra = ("; anything that needs your OK pops up on the phone" if en else "；需要你点头的环节，它会在手机上弹窗问你") if c["kind"] in AGENT_LABEL else ""
    if c.get("session_mode") == "shared":
        fz = (f" · shared session (your own {label} on the Agent J host, with its own permissions; outside Agent J's fence)" if en
              else f" · 共享会话（接你Agent J 主机上自己的 {label}，按它自己的权限运行，不在 Agent J 的隔离里）")
        pin = c.get("shared_session_id")
        if c["kind"] == "codex" and pin:         # P328: a phone /clear moved Codex off the owner's pin (per-harness state)
            from . import shared_codex
            pin = shared_codex.effective_pin(st, pin) or ("new conversation at the next message" if en else "下一条消息开新对话")
        if pin:
            fz += (" · pinned " if en else " · 指定会话 ") + str(pin)
    elif c.get("fence", True):
        fz = " · independent session · fenced" if en else " · 独立会话 · 隔离运行"
        if c.get("docker"):
            fz += " · ⚠ docker allowed (--allow-docker)" if en else " · ⚠ 允许用 docker（--allow-docker）"
    else:
        fz = " · independent session · ⚠ not fenced (--unfenced)" if en else " · 独立会话 · ⚠ 不隔离运行（--unfenced）"
    if en:
        return f"Agent: {label} · folder {c['dir']}" + (f" · model {c['model']}" if c["model"] else "") + fz + extra
    return (f"接的 Agent：{label} · 目录 {c['dir']}" + (f" · 模型 {c['model']}" if c["model"] else "")
            + fz + extra)


def cmd_agent(a) -> None:
    if a.mode == "detect":   # works before `agentj init`: only looks at PATH and whether login files exist
        from . import harness
        # P220: `detect <harness> --probe --model M` = one real call to that harness with that model (the installer's
        # fallback when Claude Code's own default is a model the owner's relay does not serve)
        target = getattr(a, "target", None)
        sys.exit(harness.main(as_json=a.json, call_probe=getattr(a, "probe", False),
                              only=(target,) if target else None, model=a.model if target else None))
    if a.mode == "route":    # P279: which AI route to recommend here (works before `agentj init`; never reads a key)
        from . import ai_route
        st = State()
        sys.exit(ai_route.main(st, as_json=a.json, lang=_lang(st) if st.exists() else _route_lang()))
    if a.mode == "switch":   # P155: gate (headless probe) → a job outside the service switches, verifies, rolls back
        from . import switch_harness
        st = State()
        if not _first_connect(st, a):
            sys.exit(switch_harness.cli(a, st, _lang(st)))
    st = State()
    _need_init(st)
    if a.mode in ("claude", "codex", "opencode", "kimi", "qwen", "dsh"):
        if a.mode in ("kimi", "qwen", "dsh") and getattr(a, "session_mode", None) == "shared":
            sys.exit(f"✗ {AGENT_LABEL[a.mode]} 只支持独立会话（--independent）/ {AGENT_LABEL[a.mode]} runs independent sessions only")
        if a.mode in ("kimi", "qwen", "dsh"):
            a.session_mode = "independent"
        if a.mode == "dsh" and getattr(a, "base_url", None):    # P329: an OpenAI-compatible route (Olares Router …)
            from . import agent_dsh
            if not a.model:
                sys.exit("✗ --base-url 要配 --model（Router 列出的模型 id）/ --base-url needs --model (the id the server lists)")
            try:
                why = agent_dsh.check_url(a.base_url)
                ctx, needs_key = (None, False) if why else agent_dsh.server_probe(
                    a.base_url, a.model, agent_dsh.fresh_keys(dict(os.environ)).get(agent_dsh.ROUTE_KEY_ENV))
                p = agent_dsh.write_route(a.base_url, a.model, context=ctx, needs_key=needs_key)
            except ValueError as e:
                sys.exit({"bad_url": "✗ 地址不对：写成 https://<服务>/v1 / not a usable address (https://<server>/v1)",
                          "http_not_local": "✗ 明文 http 只允许本机或局域网地址；外网请用 https / plain http only for this "
                                            "computer or the LAN",
                          "bad_model": "✗ 模型 id 不对 / not a model id"}.get(str(e), "✗ " + str(e)))
            from .service import tilde
            print(f"✓ 路线已写入 {tilde(str(p))}（地址 + 模型{f' · 上下文 {ctx}' if ctx else ''}；key 只按名字 "
                  f"{agent_dsh.ROUTE_KEY_ENV} 读，服务要 key 时让 Agent 发密钥卡）/ route written; the key is read by name only",
                  flush=True)
            if needs_key:   # P329 live: the Olares Router answers 401 without a key
                print(f"! 这个服务要 key：配对（第 11 步）后运行下面这行，主人只在手机密钥卡里贴 key / this server wants a key: "
                      f"after pairing (Step 11) run this line; the human pastes the key only on the phone's key card:\n"
                      f"  {agent_dsh.key_card(a.base_url)}", flush=True)
        if a.mode == "opencode" and a.model and not re.fullmatch(r"[A-Za-z0-9._-]+/[^\s/][^\s]*", a.model):
            sys.exit("✗ OpenCode 的模型写成「服务商/模型」，例如 zhipuai/glm-5.3 或 deepseek/deepseek-flash（`opencode models` 列出可用的）")
        if a.unfenced:   # F14: the owner's (or, at the owner's request, the Agent's) switch — no passphrase
            print("⚠ 不隔离运行：Agent 按底层 harness 自己的权限运行，能看到 Agent J 的状态目录。随时可以去掉 --unfenced 改回来。", flush=True)
        elif a.allow_docker:
            print("⚠ 允许 Agent 用 docker / podman：容器能挂载这台Agent J 主机上的任何文件（包括 Agent J 的状态目录）。", flush=True)
        try:
            from . import working_root, preferences, session_choice
            lang = _lang(st)
            # P127 / P174: --shared / --independent is the owner's choice; without a flag the mode stays as it is
            # (factory default independent; an install from before P174 keeps shared)
            from . import instance
            if instance.current() > 1:   # P180: a second seat on the Agent J host never attaches to the owner's desktop session
                if getattr(a, "session_mode", None) == "shared":
                    sys.exit("✗ 同一台Agent J 主机上的第 2 个及以后的席位只能用独立会话（--independent）/ "
                             "instances 2–10 run independent sessions only")
                a.session_mode = "independent"
            flag = getattr(a, "session_mode", None)
            before = session_choice.mode_of(st)
            mode = session_choice.resolve(st, a.mode, flag, lang)
            if flag and flag != before:
                print(session_choice.explain(mode, before, lang)["text"], flush=True)
            if a.mode == "claude":
                from . import claude_auth, preferences
                if mode == "independent" and not claude_auth.available():
                    from . import service
                    sys.exit("✗ " + service.token_hint(service.name()))
            root = working_root.select(st, a.dir)
            from . import harness_setup
            prev = st.agent_config() or {}
            same = prev.get("kind") == a.mode
            model = a.model or (prev.get("model") if same else None)     # P219: the owner's earlier choice is kept
            effort = prev.get("effort") if same else None
            if a.mode == "claude":                # P219: never Claude Code's own pick (a relay may not serve it: 400)
                model, effort = model or harness_setup.DEFAULT_CLAUDE_MODEL, effort or harness_setup.DEFAULT_CLAUDE_EFFORT
            st.set_agent_config(a.mode, str(root), model, fence=not a.unfenced, docker=bool(a.allow_docker and not a.unfenced),
                                effort=effort)
            if a.mode == "kimi":                 # P305: kimi.com (mainland-cn, default) or kimi.ai (global) — the sign-in site
                st.set_agent_region(getattr(a, "region", None) or (prev.get("region") if same else None))
            back = st.agent_config() or {}          # P298: read back what was written — never trust anyone's account of it
            if back.get("kind") != a.mode or (back.get("model") or None) != (model or None):
                sys.exit(f"✗ 读回核对不一致：写入 {a.mode} · {model or '默认模型'}，读回 {back.get('kind')} · "
                         f"{back.get('model') or '默认模型'} / read-back mismatch")
            print(f"✓ 读回核对：{AGENT_LABEL[a.mode]} · 模型 {back.get('model') or '默认'} / read back: {a.mode} · "
                  f"model {back.get('model') or 'default'}", flush=True)
            if a.mode == "opencode":
                from .opencode_free import privacy_note
                note = privacy_note(model, lang)
                if note:
                    print("⚠ " + note, flush=True)
                from .bailian import model_note       # P310: Token Plan terms (data used for training); P348: tasks OK, bots never
                from .ark import model_note as ark_note   # P315: Ark Coding Plan terms + image input
                note = model_note(model, "en" if lang == "en" else "zh") or ark_note(model, "en" if lang == "en" else "zh")
                if note:
                    print("⚠ " + note, flush=True)
            if harness_setup.enabled():           # P219: the harness's one-time terminal questions, answered ahead
                line = harness_setup.summary(harness_setup.prepare(a.mode, str(root)), lang)
                if line:
                    print(line, flush=True)
            from . import claude_inbound, claude_statusline
            claude_statusline.ensure_default(st)        # P127: same moment as the inbound default; never raises
            claude_inbound.ensure_shared_default(st)
            notice = claude_inbound.default_notice(st, preferences.get(preferences.effective(st), "appearance.language", "zh"))
            if notice:
                print(notice)  # retained for the phone after serve restarts
            from . import wizard
            from . import preferences
            wizard.bootstrap_root(root, lang=preferences.get(preferences.effective(st), "appearance.language", "zh"))
            _refresh_root(st, root)
            if a.mode == "claude" and mode == "shared":
                session_choice.pick(st, str(root), lang)      # P127: which live Claude Code session the phone attaches to
        except preferences.ConfigError as e:
            sys.exit("✗ " + str(e.detail))
        except ValueError as e:
            sys.exit({"bad_dir": "目录不存在",
                      "protected_dir": "这个目录是 Agent J 自己的（状态目录或程序目录），不能给 Agent 用：换一个工作目录"}.get(str(e), str(e)))
        st.log("agent_config", agent=a.mode, fence=not a.unfenced, change="docker" if a.allow_docker else None)
        if not a.unfenced and (st.agent_config() or {}).get("session_mode") != "shared":   # the fence is independent mode's
            from . import fence
            why = fence.problem(st, st.agent_config()["dir"])
            if why:   # P219: serve refuses an unfenced start; the phone gets the 「本次不隔离运行」 card (owner passkey)
                print(f"⚠ {fence.REASONS.get(why, why)}。\n  不修的话：每次启动 Agent 时手机上会弹「沙箱不可用 · 本次不隔离运行」卡，"
                      "要主人通行密钥批准；或者主人明确选择不隔离：agentj agent " + a.mode + " --unfenced", flush=True)
    elif a.mode == "off":
        st.set_agent_config(None)
        st.log("agent_config", agent="off")
    elif a.mode == "restart":   # P59 (A167): the harness Agent J owns, before its next turn; the conversation is kept
        try:
            res = names.ctl_call(st, {"cmd": "agent_restart"}, 10)
        except names.ServeBusy:
            sys.exit("✗ agentj serve 在运行但没有响应：没有重启 / serve is running but not answering")
        when = (res or {}).get("when")
        if res is not None and res.get("error") == "unknown command":   # a serve older than this CLI
            sys.exit("✗ 正在运行的 serve 版本较旧：运行 `agentj service restart` / the running serve is older: `agentj service restart`")
        if res is None or when == "next_start":
            print("✓ Agent 现在没有在运行：下一条消息启动时就用新的设置（代理、key），接着原来的对话。"
                  " / Not running now: the next message starts it with the new settings, same conversation.")
        elif when == "next_turn":
            print("✓ 已安排重启：下一条消息之前重启 Agent 的进程（不打断正在进行的这一轮），接着原来的对话。"
                  " / Restart scheduled before the next message (the running turn is not interrupted); same conversation.")
        else:
            sys.exit("✗ 共享会话用的是你Agent J 主机上原来的进程，Agent J 不替你重启：请在Agent J 主机上自己重启它。"
                     " / A shared session is your own process: restart it yourself.")
        return
    elif a.mode == "reset":
        for k in st.AGENT_KINDS:
            st.set_agent_session(k, None)
        print("已忘掉之前的对话：下一条手机消息开一段新对话。")
    print(_agent_line(st))
    if a.mode in ("claude", "codex", "opencode", "kimi", "qwen", "dsh", "off"):
        print("Takes effect after restarting `agentj serve`. The Agent runs as you, with your own login and permission settings; "
              "Agent J gives it no extra permission." if _lang(st) == "en" else
              "重启 `agentj serve` 后生效。Agent 以你自己的身份、登录和权限设置运行，本程序不会给它更多权限。")


def _first_connect(st: State, a) -> bool:
    """P220 (HK owner 10-13): `agentj agent switch claude` on a computer with no Agent connected yet said only 「Agent J 还没接任何
    Agent」. Nothing to switch from is a first connection: do exactly what `agentj agent <harness>` does, then restart the
    background service so it takes effect now. False = a real switch (or --scan / --result / the worker): unchanged."""
    target = getattr(a, "target", None)
    if (target not in ("claude", "codex", "opencode", "kimi", "qwen", "dsh") or getattr(a, "worker", False) or getattr(a, "scan", False)
            or getattr(a, "result", False) or getattr(a, "dry_run", False)):
        return False
    try:
        if not st.root.exists() or st.agent_config():
            return False
    except Exception:  # noqa: BLE001 — an unreadable state is the switch's business, as before
        return False
    print(("No Agent is connected yet, so there is nothing to switch from: connecting %s now." if _lang(st) == "en" else
           "还没接任何 Agent，不用切换：现在直接接上 %s。") % target, flush=True)
    a.mode = target
    cmd_agent(a)
    from . import service
    try:
        sv = service.status()
        if sv.get("installed") and sv.get("active") == "active":
            service.restart()
            print("✓ 后台服务已重启，手机发一句「你好」试试 / background service restarted: say hello from the phone", flush=True)
        else:
            print("还差一步：运行 agentj service install 让它在后台运行 / one more step: agentj service install", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"后台服务没能自动重启（{type(e).__name__}）：运行 agentj service restart / restart it: agentj service restart", flush=True)
    return True


def cmd_approvals(a) -> None:
    from . import approvals
    st = State()
    _need_init(st)
    every = approvals.read_log(st)
    recs = every[-a.last:] if a.last else every
    index = {str(r.get("id")): r for r in every if r.get("decision") == "allow_batch"
             or (r.get("tool") == "agentj goal" and r.get("decision") == "allow")}
    bad = 0
    for r in recs:
        v = approvals.check_record(st, r, index) if a.verify else ""
        bad += v == "bad"
        if a.json:
            print(json.dumps({**r, **({"verify": v} if v else {})}, ensure_ascii=False))
        else:
            when = time.strftime("%m-%d %H:%M:%S", time.localtime(r.get("ts", 0)))
            who = f"手机 {r.get('device')}" if r.get("device") else {"timeout": "超时", "no_device": "无可批准手机",
                                                                       "serve_stop": "serve 停止", "agent_gone": "Agent 撤回",
                                                                       "too_many": "请求过多",
                                                                       "elsewhere": "新遥控器已在别处确认，这张配对卡收起（未签名）",
                                                                       "gone": "卡片作废（配对已结束）",
                                                                       "policy": "超出 Agent 自己的沙箱，自动拒绝"}.get(r.get("reason"), r.get("reason"))
            if r.get("reason") == "batch":
                who = f"按手机 {r.get('device')} 的批量授权自动批准（{str(r.get('grant'))[:8]}…）"
            elif r.get("reason") in ("goal", "goal_auto"):    # P158
                who = ("目标模式：10 秒无人点，自动批准" if r.get("reason") == "goal_auto" else "目标模式：按目标授权自动批准") + \
                    f"（{str(r.get('grant'))[:8]}…）"
            elif r.get("reason") == "goal_grant_add":         # P339
                who = f"目标模式：手机 {r.get('device')} 批准并加进目标授权（{str(r.get('grant'))[:8]}…）"
            elif r.get("reason") in ("level_full", "goal_nocard", "goal_preauth", "parked", "offline", "parked_wait"):   # P336
                who = {"level_full": "全放行档位：自动批准", "goal_nocard": "设了目标就不弹窗：自动批准",
                       "goal_preauth": "设了目标就不弹窗：目标直接开始（没有开工卡）",
                       "parked": "主人批准了离线时留下的卡，重试直接执行", "offline": "主人没有遥控器在线，先告诉 Agent",
                       "parked_wait": "离线卡仍未批准"}[r["reason"]] + (f"（{str(r.get('grant'))[:8]}…）" if r.get("grant") else "")
            mark = {"ok": " ✓签名有效", "ok_removed": " ✓签名有效（设备已移除）", "bad": " ✗签名无效", "unsigned": "",
                    "legacy": " ·旧版记录未存签名（controls.log 里也已找不到可核对的那一行）",
                    "auto": " ✓授权签名有效"}.get(v, "")
            cats = ("  [" + "、".join(r["cats"]) + "]") if r.get("cats") else ""
            if r.get("kind") == "question":       # §10.7: the picks as option numbers, never the question text
                cats = f"  选了 {r.get('picks')}" if r.get("decision") == "answer" else ""
                print(f"{when}  {r.get('decision', '?'):11}  {'提问':12}  {who}{mark}{cats}")
                continue
            print(f"{when}  {r.get('decision', '?'):11}  {r.get('tool', '?'):12}  {who}{mark}{cats}")
    if not recs:
        print("还没有批准记录。")
    if a.verify and bad:
        sys.exit(f"{bad} 条记录签名无效")


# ------------------------------------------------------------------ phone controls, terminal side (ADR-A50 – A54)
def _estop_line(st: State) -> str:
    from . import controls
    e = controls.estop_state(st)
    if not e["on"]:
        return "急停：未急停 / not stopped"
    when = time.strftime("%m-%d %H:%M", time.localtime(e.get("at") or 0))
    by = e.get("by") or "?"
    by = "终端" if by == "terminal" else ("手机 " + (st.devices().get(by[6:], {}).get("name") or by[6:])) if by.startswith("phone:") else by
    return f"急停：⛔ 已急停（{when}，{by}）· 恢复：`agentj resume` / STOPPED — `agentj resume`"


def cmd_stop(a) -> None:
    """Stop everything. No passphrase: stopping only ever takes power away."""
    from . import activity, controls
    st = State()
    _need_init(st)
    try:
        res = names.ctl_call(st, {"cmd": "stop"}, 15)
    except names.ServeBusy:
        res = None
    if res is None:       # serve not running (or not answering): the switch is a file — serve starts stopped
        controls.set_estop(st, True, "terminal")
        st.log("estop", status="on")
        activity.record(st, "estop", by="终端")
    print("⛔ 已急停：Agent 停下、待批准的全部拒绝、批量授权收回、定时任务暂停；serve 重启后仍是急停。恢复：已配对手机上点「恢复」，或终端 `agentj resume`")


def _ctl_refused_msg(res: dict) -> str:
    """A4 (P139): serve refused a safety command over its control socket."""
    if res.get("error") == "passphrase":
        return "遥控器配对密码不对（或已锁定），没有恢复 / wrong or locked remote pairing password"
    return ("这个操作只能由主人在自己的终端里做，Agent 和脚本做不了（可以在已配对手机上操作）。 / Only the owner, in their "
            "own terminal (or on a paired phone), can do this — not an Agent or a script.")


def cmd_resume(a) -> None:
    from . import activity, controls
    st = State()
    _need_init(st)
    if not controls.estop_state(st)["on"]:
        print("没有急停，不用恢复。")
        return
    # F14: a stop is the owner's emergency switch — one tap on a paired phone lifts it (signed, no passphrase). At the
    # terminal the owner proves presence with the passphrase or, without one, a keyboard: the Agent cannot lift its own stop.
    req = {"cmd": "resume"}
    if gate.is_set(st):
        try:
            pw = _secret("输入遥控器配对密码恢复（或在已配对手机上点「恢复」）：")
            gate.verify(st, pw)
        except gate.GateError as e:
            sys.exit("✗ " + _gate_msg(e))
        req["pass"] = pw                     # A4 (P139): serve verifies it again — a bare socket write cannot resume
    elif not sys.stdin.isatty():
        sys.exit("✗ 急停只能由主人解除：在已配对手机上点「恢复」，或主人在自己的终端运行 `agentj resume`。")
    try:
        res = names.ctl_call(st, req, 15)
    except names.ServeBusy:
        sys.exit("agentj serve 在运行但没有响应：没有恢复")
    if res is not None and not res.get("ok", True):
        sys.exit("✗ " + _ctl_refused_msg(res))
    if res is None:
        controls.set_estop(st, False, "terminal")
        st.log("estop", status="off")
        activity.record(st, "resume", by="终端")
    print("✓ 已恢复：Agent 接收新消息，定时任务按各自的启用状态继续。")


def _mem_target(st: State, a) -> tuple[str | None, str | None]:
    c = st.agent_config()
    kind = a.harness or (c["kind"] if c else None)
    d = a.dir or (c["dir"] if c else None)
    if kind and not d:
        d = os.getcwd()
    return kind, (os.path.realpath(os.path.expanduser(d)) if d else None)


def _mem_items(src: dict) -> list[dict]:
    return [{"file": f["file"], "fsha": f["fsha"], **it} for f in src["files"] if f.get("fsha") for it in f["items"]]


def cmd_memory(a) -> None:
    from . import memory
    st = State()
    _need_init(st)
    kind, d = _mem_target(st, a)
    if a.mode == "restore":
        if not a.args:
            tr = memory.trash(st)
            if a.json:
                print(json.dumps([{k: r.get(k) for k in ("id", "ts", "label", "kind")} | {"text": memory.preview(r, 200)}
                                  for r in tr], ensure_ascii=False, indent=1))
                return
            if not tr:
                print("回收站是空的（删掉的记忆保留 7 天）。")
            for r in tr:
                when = time.strftime("%m-%d %H:%M", time.localtime(r.get("ts", 0)))
                print(f"{r['id']}  {when}  {r.get('label', '')}  {memory.preview(r)}")
            return
        try:
            r = memory.restore(st, a.args[0])
        except memory.MemoryError_ as e:
            sys.exit(f"✗ 没有恢复：{_MEM_WHY.get(e.reason, e.reason)}")
        from . import activity
        activity.record(st, "mem_undo", by="终端", label=r.get("label"), text=memory.preview(r, 200))
        print(f"✓ 已恢复到 {memory.tilde(r['path'])}")
        return
    if not kind:
        sys.exit("还没接 Agent：`agentj agent claude --dir <目录>`（或用 --harness claude|codex|opencode --dir <目录>）")
    res = memory.scan(kind, d)
    if a.mode == "list":
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
            return
        print(f"{memory.LABEL_HARNESS.get(kind, kind)} · 工作目录 {memory.tilde(d)}")
        for i, s in enumerate(res["sources"], 1):
            n = len(_mem_items(s))
            state = _MEM_WHY.get(s.get("problem"), s.get("problem")) if s.get("problem") else f"{n} 条"
            print(f"  {i}. {s['label']}  {s['path']}  — {state}")
        print("看一处：`agentj memory show <编号>`；删一条：`agentj memory rm <编号> <条目号>`；恢复：`agentj memory restore`")
        return
    if not a.args:
        sys.exit("要给记忆来源的编号（`agentj memory list` 里看）")
    try:
        src = res["sources"][int(a.args[0]) - 1]
    except (ValueError, IndexError):
        src = next((s for s in res["sources"] if s["id"] == a.args[0]), None)
        if src is None:
            sys.exit(f"没有这个来源：{a.args[0]}")
    items = _mem_items(src)
    if a.mode == "show":
        if a.json:
            print(json.dumps({**src, "items": items}, ensure_ascii=False, indent=1))
            return
        print(f"{src['label']}  {src['path']}" + (f"  — {_MEM_WHY.get(src['problem'], src['problem'])}" if src.get("problem") else ""))
        for i, it in enumerate(items, 1):
            first = (it.get("title") + "：" if it.get("title") else "") + " ".join(it["text"].split())
            print(f"  {i:>3}. {first[:150]}" + (f"   [{it['file']}]" if it["file"] else ""))
        return
    # rm
    if len(a.args) < 2:
        sys.exit("要给条目号：`agentj memory rm <来源编号> <条目号>`（`agentj memory show <来源编号>` 里看）")
    try:
        it = items[int(a.args[1]) - 1]
    except (ValueError, IndexError):
        sys.exit(f"没有第 {a.args[1]} 条")
    try:
        rec = memory.remove(st, kind, d, src["id"], it["file"], it["fsha"], it["iid"])
    except memory.MemoryError_ as e:
        sys.exit(f"✗ 没有删除：{_MEM_WHY.get(e.reason, e.reason)}")
    from . import activity
    activity.record(st, "mem_rm", by="终端", label=rec["label"], text=memory.preview(rec, 200), undo=rec["id"])
    print(f"✓ 已删除（原文进了回收站，7 天内可恢复）：`agentj memory restore {rec['id']}`")


_MEM_WHY = {"not_found": "不存在", "symlink": "是软链接，不跟随", "too_large": "文件太大（> 256 KiB），不显示",
            "changed": "文件刚被改过，请重新查看后再删", "unknown_item": "没有这一条（可能已经被改掉了）",
            "unknown_source": "没有这个来源", "exists": "那个文件已经存在且内容不同，没有覆盖", "io": "读写失败",
            "too_many_files": "文件太多，只显示前 200 个", "total_limit": "总量超过 2 MiB，后面的不显示"}


def cmd_activity(a) -> None:
    from . import activity
    st = State()
    _need_init(st)
    if a.clear:
        n = activity.clear(st)
        print(f"已删除 {n} 天的操作记录。")
        return
    since = None
    if a.since:
        m = __import__("re").fullmatch(r"(\d+)([hdm])", a.since)
        if m:
            since = int((time.time() - int(m.group(1)) * {"h": 3600, "d": 86400, "m": 60}[m.group(2)]) * 1000)
        else:
            try:
                import datetime as _dt
                since = int(_dt.datetime.fromisoformat(a.since).astimezone().timestamp() * 1000)
            except ValueError:
                sys.exit("--since 要写成 2h / 3d / 2026-10-01 / 2026-10-01T08:00")
    items = activity.all_since(st, since)
    if a.json:
        for r in items:
            r.pop("c", None)
            print(json.dumps(r, ensure_ascii=False))
        return
    if not activity.enabled(st):
        print("（操作记录已关闭：`agentj config activity on` 打开）")
    if not items:
        print("还没有操作记录。")
    for r in items:
        print(time.strftime("%m-%d %H:%M:%S", time.localtime(r.get("ts", 0) / 1000)) + "  " + activity_line(r))


def activity_line(r: dict) -> str:
    k = r.get("k")
    by = f"（{r['by']}）" if r.get("by") else ""
    t = r.get("text") or r.get("summary") or ""
    t = " ".join(str(t).split())[:120]
    cats = ("[" + "、".join(r["cats"]) + "] ") if r.get("cats") else ""
    task = f"〔定时任务 {r['task']}〕" if r.get("task") else ""
    return {
        "turn_start": f"对话开始{by}：{t}", "turn_end": f"对话结束（{r.get('result', '')}，{r.get('secs', '?')} 秒）",
        "ask": f"权限请求 {task}{cats}{r.get('tool', '')}：{t}",
        "decision": f"决定 {r.get('result', '')}{by} {r.get('tool', '')}",
        "auto": f"按批量授权自动批准{by} {r.get('tool', '')}：{t}", "grant": f"批量授权{by}：{r.get('scope', '')}",
        "grant_end": f"批量授权结束：{r.get('why', '')}", "estop": f"⛔ 急停{by}", "resume": f"恢复{by}",
        "message_refused": f"急停中，拒收消息{by}：{t}", "mem_rm": f"删除记忆{by} {r.get('label', '')}：{t}",
        "mem_undo": f"恢复记忆{by} {r.get('label', '')}：{t}", "task_on": f"启用定时任务{by} {r.get('id', '')}",
        "task_off": f"停用定时任务{by} {r.get('id', '')}", "task_run": f"定时任务开始 {r.get('title', r.get('id', ''))}（{r.get('trigger', '')}）",
        "task_done": f"定时任务结束 {r.get('title', r.get('id', ''))}：{r.get('verdict', '')} — {r.get('line', '')}"
                     + ("（只读运行）" if r.get("readonly") else ""),
        "shared": f"共享会话 {r.get('agent', '')} {r.get('ev', '')}：{r.get('reason', '')}",
        "control_refused": f"拒绝了手机命令 {r.get('action', '')}{by}：{r.get('why', '')}", "truncated": "（今天的记录已达上限，后面的没有记）",
    }.get(k, str(k))


def _history_switch(st: State, on: bool) -> None:
    from . import history
    history.set_enabled(st, on)
    st.log("history_switch", status="on" if on else "off")
    try:
        names.ctl_call(st, {"cmd": "history_reload"}, 10)     # a running serve picks it up at once
    except names.ServeBusy:
        pass


def _history_line(st: State) -> str:
    from . import history
    h = history.History(st)
    if not h.on:
        n, b = h.on_disk()
        left = ("" if not n else
                f"\n已有的记录没有删：{n} 个文件（{b / 1024 / 1024:.1f} MiB，{h.dir}）还在这台Agent J 主机上；"
                f"`agentj history clear --all` 立即全部删除（含归档，不能撤销） / existing history is NOT deleted: {n} "
                f"file(s) remain; `agentj history clear --all` deletes them (archives too, cannot be undone)")
        return ("关：新的聊天记录只在内存里（最近 100 页），重启就没了 / history off: the newest 100 pages in memory only"
                + left)
    return (f"开：聊天记录存在这台Agent J 主机上（{h.dir}，0600，最近 {history.KEEP} 页，归档 {len(h.archives())} 份）· "
            f"共 {h.meta()['count']} 页 / history on: kept on the Agent J host")


def cmd_history(a) -> None:
    """`agentj history [show <id>|search <words…>|clear|off|on]` (PROTOCOL §10.5, §28): the pages the phone shows, on the Agent J host only."""
    from . import history
    st = State()
    _need_init(st)
    if a.mode in ("on", "off"):
        _history_switch(st, a.mode == "on")
        print(_history_line(st))
        return
    if a.mode == "clear" and getattr(a, "all", False):    # P33-C07: delete current + every archive (not undoable)
        try:
            res = names.ctl_call(st, {"cmd": "history_clear", "all": True}, 10)
        except names.ServeBusy:
            sys.exit("agentj serve 没有响应，没有删除 / serve did not answer: nothing deleted")
        if res is None:
            res = {"deleted": history.History(st).purge()}
        st.log("history_purge", status=str(res.get("deleted", 0)))
        print(f"已删除这台Agent J 主机上的全部聊天记录（{res.get('deleted', 0)} 个文件，含归档）/ deleted all history on this "
              "computer (archives too)")
        return
    if a.mode == "clear":
        try:
            res = names.ctl_call(st, {"cmd": "history_clear"}, 10)
        except names.ServeBusy:
            sys.exit("agentj serve 没有响应，没有清空 / serve did not answer: nothing cleared")
        if res is None:                                   # serve not running: the files directly
            name = history.History(st).reset("cli")
            res = {"archived": bool(name)}
        print("已归档，手机上从新的一页开始（归档保留最近 10 份；`--all` 连归档一起删除）/ archived; the phone starts a new "
              "page (`--all` deletes the archives too)"
              if res.get("archived") else "没有可清空的记录 / nothing to clear")
        return
    h = history.History(st)
    if a.mode == "search":                                # P188 (§28): the same search the phone's 🔍 runs on the Agent J host
        from . import hsearch
        q = " ".join(a.id or [])
        if not hsearch.terms(q):
            sys.exit("用法：agentj history search <关键词…> [--time] [--json] / usage: agentj history search <words…>")
        hits, n = hsearch.search(list(h.turns.values()), q, "time" if a.time else "score", hsearch.HITS_MAX)
        if a.json:
            print(json.dumps({"epoch": h.epoch, "n": n, "hits": hits}, ensure_ascii=False))
            return
        print(f"找到 {n} 页（当前会话，{'按时间' if a.time else '按相关度'}；只显示前 {len(hits)} 页）/ {n} page(s) found")
        for x in hits:
            when = time.strftime("%m-%d %H:%M", time.localtime((x["ts"] or 0) / 1000))
            print(f"#{x['id']} {when} · {x['src'].get('k')}{(' · ' + x['src']['name']) if x['src'].get('name') else ''} · {x['score']}")
            for f in x["where"]:
                print(f"   {f}: {clean(x['snip'][f], 400)}")
        return
    if a.mode == "show":
        try:
            t = h.get(int((a.id or [None])[0]))
        except (TypeError, ValueError):
            sys.exit("用法：agentj history show <页码> / usage: agentj history show <id>")
        if t is None:
            sys.exit("没有这一页（可能在归档里）/ no such page in the current history")
        if a.json:
            print(json.dumps(t, ensure_ascii=False, indent=1))
            return
        src = t["src"]
        when = time.strftime("%m-%d %H:%M", time.localtime(t["ts"] / 1000))
        print(f"#{t['id']} {when} · {src.get('k')}{(' · ' + src['name']) if src.get('name') else ''} · {t['end']}")
        if src.get("text"):
            print("— 原话 / source:\n" + clean(src["text"], 20_000))
        print("— 回复 / reply:\n" + clean(t["reply"]["text"], 200_000))
        return
    print(_history_line(st))
    if a.json:
        print(json.dumps({**h.meta(), "on": h.on, "archives": h.archives()}, ensure_ascii=False))
        return
    for t in h.page(limit=10)[0]:
        first = (t["src"].get("text") or t["reply"]["text"] or "").split("\n", 1)[0]
        print(f"  #{t['id']:<5} {t['src'].get('k'):6} {clean(first, 60)}")


def cmd_inbox(a) -> None:
    """`agentj inbox [list|clear|path]` (PROTOCOL §10.4): what the phone uploaded into the Agent's folder."""
    from . import inbox
    st = State()
    _need_init(st)
    ag = st.agent_config()
    if not ag:
        sys.exit("还没接 Agent（`agentj agent claude --dir <目录>`），没有收件箱 / no Agent folder yet")
    wd = ag["dir"]
    if a.mode == "path":
        print(inbox.inbox_dir(wd))
        return
    if a.mode == "clear":
        try:
            n = inbox.clear(wd, log=st.log)
        except inbox.Unsafe:
            sys.exit(f"{inbox.inbox_dir(wd)} 或它上面的 .agentj 是链接（或不属于你），为安全起见什么都没删 / "
                     "the inbox or .agentj is a link (or not yours): nothing deleted")
        st.log("inbox_clear", status=str(n))
        print(f"已删除 {n} 个文件 / deleted {n} file(s)")
        return
    ds = inbox.days(wd)
    if a.json:
        print(json.dumps({"path": inbox.inbox_dir(wd), "days": [{"day": d, "files": f, "bytes": b} for d, f, b in ds]},
                         ensure_ascii=False))
        return
    print(f"{inbox.inbox_dir(wd)}  （保留 {inbox.RETAIN_DAYS} 天，最多 {inbox.QUOTA // 1024 ** 3} GiB / kept "
          f"{inbox.RETAIN_DAYS} days, ≤ {inbox.QUOTA // 1024 ** 3} GiB）")
    if not ds:
        print("空的 / empty")
    for d, f, b in ds:
        print(f"  {d}  {f} 个文件  {b / 1024 / 1024:.1f} MiB")


def cmd_asr(a) -> None:
    """`agentj asr install|status|test <wav>|engine <e>|remove` (PROTOCOL §10.9): asr.py does the work."""
    from . import asr
    st = State()
    _need_init(st)
    from . import preferences
    args=list(a.args or [])
    if len(args) in (2,3) and args[0]=='engine' and set(args[2:]) <= {'--force'}:
        selected={'auto':'sensevoice','sherpa':'sensevoice','voxtype':'voxtype','off':'off'}.get(args[1])
        if selected=='voxtype':   # P393 (#120): some desktops ship voxtype as English-only Whisper unless the human changed it
            go,line=asr.voxtype_guard('--force' in args)
            if line:print(line)
            if not go:sys.exit(2)
        if selected:sys.exit(preferences.command(['set','voice.asr.engine',selected]))
    # P393: status / test follow the same engine setting as serve, doctor and the phone (voice.asr.engine)
    try:
        override=preferences.get(preferences.effective(st),'voice.asr.engine')
    except Exception:  # noqa: BLE001 — unreadable preferences: config.json's asr.engine alone, as before
        override=None
    sys.exit(asr.cli_main(args, state_dir=st.root, engine_override=override))


def cmd_config(a) -> None:
    from . import activity
    st = State()
    _need_init(st)
    if a.key == "history":
        if a.value in ("on", "off"):
            _history_switch(st, a.value == "on")
        print(_history_line(st))
        return
    if a.key == "activity":
        if a.value in ("on", "off"):
            activity.set_enabled(st, a.value == "on")
            st.log("activity_switch", status=a.value)
        on = activity.enabled(st)
        print(("开：本机记录操作与审批（activity.log，只在这台Agent J 主机上，30 天，最多 20 MiB）；手机的「记录」页读它。"
               if on else "关：不再记录（已有的保留到 30 天；`agentj activity --clear` 立即删除）。")
              + f" 现有 {activity.size(st) // 1024} KiB / activity log " + ("on" if on else "off"))


def cmd_tasks(a) -> None:
    from . import activity, tasks
    st = State()
    _need_init(st)
    c = st.agent_config()
    wd = c["dir"] if c else None
    if a.mode == "list":
        rows = tasks.rows(st, wd)
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
            return
        if not wd:
            print("还没接 Agent：定时任务在 Agent 工作目录的 workflows/*/task.json 里。")
            return
        print(_estop_line(st))
        if not rows:
            print(f"{memory_tilde(wd)}/workflows 下没有任务。")
        for r in rows:
            state = ("✗ 无效：" + r["problems"][0]) if r["problems"] else ("✓ 已启用" if r["enabled"] else
                                                                         "! 已改动，需重新启用" if r["stale"] else "· 未启用")
            nx = time.strftime(" · 下次 %m-%d %H:%M", time.localtime(r["next"])) if r.get("next") else ""
            last = r.get("last") or {}
            lt = f" · 上次 {last.get('verdict')}" if last else ""
            print(f"  {r['id']:<20} {state}  {r.get('schedule') or ''} ({r.get('tz') or ''}) {r.get('mode') or ''}{nx}{lt}")
        return
    if not a.id:
        sys.exit("要给任务 id（`agentj tasks list`）")
    try:
        e = tasks.find(wd, a.id)
    except tasks.TaskError:
        sys.exit(f"没有这个任务：{a.id}")
    if a.mode == "show":
        row = next(r for r in tasks.rows(st, wd) if r["id"] == a.id)
        print(json.dumps({**row, "dir": memory_tilde(e["dir"]), "needs": (e["task"] or {}).get("needs")}, ensure_ascii=False, indent=1))
        return
    if a.mode in ("enable", "disable"):
        on = a.mode == "enable"
        if on:
            if e["problems"]:
                sys.exit("✗ task.json 无效，不能启用：" + e["problems"][0])
            print(f"启用「{e['task']['title']['zh']}」：按 {e['task']['schedule']}（{e['task']['tz']}）自动运行 "
                  f"{'（只读运行）' if e['task']['mode'] == 'research' else ''}；改了 task.json 或 {e['task']['prompt_file']} 就要重新启用。",
                  flush=True)
        try:
            tasks.set_enabled(st, wd, a.id, on, "terminal")
        except tasks.TaskError as err:
            sys.exit(f"✗ {err.detail or err.reason}")
        activity.record(st, "task_on" if on else "task_off", by="终端", id=a.id)
        st.log("task_on" if on else "task_off", id=a.id)
        _ctl_quiet(st, {"cmd": "tasks_changed"})
        print(("✓ 已启用" if on else "✓ 已停用") + f"：{a.id}")
        return
    # run
    if e["problems"]:
        sys.exit("✗ task.json 无效：" + e["problems"][0])
    if a.dry_run:
        t = e["task"]
        nx = tasks.next_run(t["schedule"], t["tz"])
        print(json.dumps({"id": a.id, "harness": c["kind"] if c else None, "cwd": memory_tilde(wd), "prompt_file":
                          f"workflows/{a.id}/{t['prompt_file']}", "mode": t["mode"], "read_only": t["mode"] == "research",
                          "fenced": bool(c and c.get("fence", True)), "timeout_s": int(tasks.RUN_TIMEOUT),
                          "next": nx.isoformat() if nx else None, "prompt_head": tasks.prompt_for(e, t["mode"] == "research")[:400]},
                         ensure_ascii=False, indent=1))
        return
    try:
        res = names.ctl_call(st, {"cmd": "task_run", "id": a.id}, 10)
    except names.ServeBusy:
        sys.exit("agentj serve 没有响应")
    if res is None:
        sys.exit("要 agentj serve 在运行（运行时的审批要经手机）")
    if not res.get("ok"):
        sys.exit({"stopped": "已急停：先 `agentj resume`", "no_agent": "还没接 Agent", "unknown": "serve 找不到这个任务"}
                 .get(res.get("error"), str(res)))
    print(f"已排队：{a.id}（等当前这一轮结束后运行；结果会发到手机，也写进 `agentj activity`）")


def memory_tilde(p: str | None) -> str:
    from .memory import tilde
    return tilde(p) if p else "?"


def cmd_remote_pair(a) -> None:
    from . import remote_pair
    st = State(); _need_init(st)
    if a.mode in ('on', 'off'): remote_pair.set_enabled(st, a.mode == 'on')
    print('从账户页或遥控器添加遥控器 / Pairing from the account page or a remote: ' + ('on' if remote_pair.enabled(st) else 'off'))


def cmd_remote_unbind(a) -> None:
    from .serve import REMOTE_UNBIND_PER_HOUR
    st = State()
    _need_init(st)
    if a.mode in ("on", "off"):
        st.set_remote_unbind(a.mode == "on")
        st.log("remote_unbind_switch", status=a.mode)
    on = st.remote_unbind()
    print(("开：账号的管理员可以在账号后台解绑这台Agent J 主机的手机遥控器（每小时最多 "
           f"{REMOTE_UNBIND_PER_HOUR} 次）。只能解绑，不能添加。") if on else
          "关：账号后台发来的解绑请求，这台Agent J 主机一律拒绝；要解绑就在这里运行 `agentj revoke`。")
    print("提醒：配对用的二维码和链接只会由这台Agent J 主机上的 `agentj pair` 显示。账号后台、邮件或客服发给你的二维码，一律别扫。")


def cmd_onboarding(a) -> None:
    """F28 (P72): what the installing Agent checks after each step — seat, the two required remotes, the welcome."""
    st = State()
    if not st.exists():
        sys.exit("先运行 `agentj init` / run `agentj init` first")
    from . import onboarding
    sys.exit(onboarding.command(a, st))


def cmd_reload(a) -> None:
    """P320 `agentj reload`: serve re-reads the configuration and its key file without restarting (see serve.reload_config)."""
    st = State()
    try:
        res = names.ctl_call(st, {"cmd": "reload"}, 30) if st.exists() else None
    except names.ServeBusy:
        sys.exit("✗ agentj serve 在运行但没有响应：没有重新加载 / serve is running but not answering")
    if res is None:
        unavailable = names.control_unavailable()
        if unavailable["error"] == "host_unreachable":
            if a.json:
                print(json.dumps(unavailable, ensure_ascii=False))
                raise SystemExit(1)
            sys.exit(unavailable["detail"])
        print("agentj serve 没有在运行：下次启动时直接读最新配置，不需要 reload。 / serve is not running: it reads the newest "
              "configuration when it starts.")
        return
    if res.get("error") == "unknown command":       # a serve older than this CLI
        sys.exit("✗ 正在运行的 serve 版本较旧，不认识 reload：运行 `agentj service restart` / the running serve is older: "
                 "`agentj service restart`")
    if a.json:
        print(json.dumps({k: res.get(k) for k in ("ok", "changed", "pending", "restart_serve", "env_changed", "harness", "key", "error")},
                         ensure_ascii=False))
    else:
        print(("✓ " if res.get("ok") else "✗ ") + (res.get("text") or res.get("error") or ""))
    if not res.get("ok"):
        sys.exit(1)


def cmd_doctor(a) -> None:
    from . import doctor
    if getattr(a, "read_only", False):
        if any(getattr(a, k, False) for k in ("reconnect", "post_upgrade", "upgrade_only", "isolation_only")):
            sys.exit("--read-only cannot be combined with repair or active probes")
        sys.exit(doctor.main(as_json=a.json, read_only=True))
    if getattr(a, "reconnect", False):         # P147: repair whichever of serve / relay / agent is broken
        from . import reconnect
        from . import host_diagnostics
        rc = host_diagnostics.request("reconnect", session=a.session, caller_session=os.environ.get("CLAUDE_CODE_SESSION_ID"), as_json=a.json)
        if rc is not None:
            sys.exit(rc)
        st = State()
        _need_init(st)
        sys.exit(reconnect.run(st, _lang(st), session=a.session, as_json=a.json))
    if getattr(a, "upgrade_only", False):
        sys.exit(doctor.main(as_json=a.json, upgrade_only=True, post_upgrade=getattr(a, "post_upgrade", False)))
    sys.exit(doctor.main(as_json=a.json, offline=a.offline, isolation_only=a.isolation_only, post_upgrade=getattr(a, "post_upgrade", False)))


SERVE_RUNNING = ("后台服务在运行（serve 在应答）；在 Agent 的隔离环境里看不到 launchd / systemd，以 serve 的应答为准 / the background "
                 "service is running (serve answers); launchd / systemd may be invisible from inside the Agent's isolation — "
                 "serve's own answer counts")


def cmd_service(a) -> None:
    import subprocess
    from . import service
    try:
        if getattr(a, "recovery_worker", None):
            from . import service_recovery
            raise SystemExit(service_recovery.run(State(), a.recovery_worker))
        if getattr(a, "deferred", None):
            from . import service_recovery, update
            if a.mode != "install" or a.deferred != __version__:
                raise service.ServiceError("recovery_version_mismatch")
            service_recovery.schedule(State(), service.agentj_argv(), a.deferred)
            print("✓ 服务重启已交给独立任务 / service restart scheduled independently")
            return
        if a.mode == "install" and service.needs_deferred_install():
            # Old apply invokes plain service install after replacing the package.
            # Handoff before remove_legacy or bootout can kill that old caller.
            from . import service_recovery
            service_recovery.schedule(State(), service.agentj_argv(), __version__)
            print("✓ 服务重装已交给独立任务 / service re-install scheduled independently")
            return
        if a.mode == "import-key":           # P198 (I): one step, no hand-editing of the service env file; value never shown
            key = getattr(a, "key", None) or service.JEV_KEY_ENV
            r = service.import_key(service.name(), key)
            path = service.tilde(service.env_file(service.name()))
            if r == "absent":
                sys.exit(f"✗ 这个终端里没有 {key}，没有可导入的 / {key} is not set in this terminal: nothing to import\n"
                         + service.absent_key_note(service.name(), key))   # P423 #140: per name, not always Jev's
            print(f"✓ {key} 已在服务环境文件里 / already in the service env file ({path})" if r == "present" else
                  f"✓ {key} 已导入服务环境文件（0600，值不显示）/ imported into the service env file ({path}, 0600, value not shown)")
            if r == "imported":
                print("  下一步 / next: agentj service restart")
            return
        if a.mode in ("start", "restart") and names.serve_answers(State()):
            from . import fence
            if a.mode == "start":     # P339: serve answers on its own socket = running; nothing to start
                print(SERVE_RUNNING + "\n  不用再启动 / nothing to start")
                return
            if fence.inside():        # P339: launchd / systemd are out of reach from inside the Agent's isolation
                sys.exit("✗ " + SERVE_RUNNING + "\n  在 Agent 的隔离环境里够不到 launchd / systemd，这里不能重启服务；也不需要管理员"
                         "权限（不要用 sudo）。切换 Agent 用 `agentj agent switch`（它自己会重启服务）。 / Inside the Agent's "
                         "isolation launchd / systemd cannot be reached, so the service cannot be restarted from here; no admin "
                         "rights are needed (never sudo). To switch the Agent use `agentj agent switch` (it restarts the "
                         "service itself).")
        if a.mode in ("start", "stop"):
            r = service.start(State()) if a.mode == "start" else service.stop()
            print(f"✓ {'已启动 / started' if a.mode == 'start' else '已停止 / stopped'}: {r['name']}")
            return
        if a.mode == "status":
            s = names.seen_status(State())       # P339: serve's own answer first — launchd / systemd may be invisible here
            if a.json:
                print(json.dumps({**s, "path": service.tilde(s["path"]) if s.get("path") else None}, ensure_ascii=False))
                return
            if s.get("seen_by") == "socket":
                print(SERVE_RUNNING + (f"  ({s['name']})" if s.get("name") else ""))
                return
            if s["kind"] == "none":
                print(f"没有可用的服务管理器 / no service manager here ({s['active']})")
                return
            print(f"{s['name']}：{'已安装 / installed' if s['installed'] else '没安装 / not installed'} · "
                  f"{s['active']}" + (f" · {s['enabled']}" if s.get("enabled") else "")
                  + (f"  ({service.tilde(s['path'])})" if s["installed"] else ""))
            return
        if a.mode == "restart":
            r = service.restart()
            print(f"✓ 已重启 / restarted: {r['name']}")
            return
        if a.mode == "uninstall":
            st = State()
            r = service.uninstall(st if st.root.is_dir() else None)
            print(f"✓ 已停止并删除 / stopped and removed: {r['name']}" if r["removed"]
                  else f"{r['name']} 没安装 / was not installed")
            if r.get("restored_disabled"):
                print("✓ 已恢复安装前的「停用」设置 / launchd's earlier \"disabled\" setting for it is back")
            return
        old = service.remove_legacy()      # first: stopping the old serve lets its state directory move (migrate.auto)
        if old:
            print(f"✓ 旧服务 {old['name']} 已停止并删除 / the old service {old['name']} was stopped and removed", flush=True)
            from . import migrate
            migrate.auto(decision="migrate")    # P127: an old service was running here = a real install upgrading, keep it
        st = State()
        _need_init(st)
        cur = service.status()
        try:
            running = names.ctl_call(st, {"cmd": "status"}, 5) is not None
        except names.ServeBusy:
            running = True
        if running and cur.get("active") != "active":
            sys.exit("✗ 有一个 `agentj serve` 正在终端里运行：先 Ctrl-C 停掉它，再安装服务 / "
                     "a `agentj serve` is running in a terminal: stop it first")
        _offer_key_import(service)          # P198 (I): before the service starts, so no restart is needed afterwards
        r = service.install(st)
    except service.ServiceError as e:
        if e.reason in service.NO_MANAGER:     # P278: no systemd here → a plain sentence + the fix, and a code update.py knows
            print("✗ " + service.MESSAGES[e.reason], file=sys.stderr)
            sys.exit(service.NO_MANAGER_EXIT)
        sys.exit("✗ " + service.MESSAGES.get(e.reason, e.reason) + (f"\n  {e.detail}" if e.detail else ""))
    except (OSError, subprocess.SubprocessError) as e:     # P278: a service-manager command that cannot run is never a traceback
        sys.exit(f"✗ 服务管理命令没能运行（{type(e).__name__}），运行 agentj doctor / a service-manager command could not run "
                 f"({type(e).__name__}): run agentj doctor")
    print(f"✓ 已安装 / installed: {r['name']} ({r['kind']}) → {service.tilde(r['path'])}")
    print("  " + " ".join(r["argv"]))
    for n in r["notes"]:
        print("! " + n)
    for _ in range(20):
        s = service.status()
        if s.get("active") == "active":
            break
        time.sleep(0.25)
    print(f"状态 / status: {s.get('active')}  ·  日志 / log: " + (f"journalctl --user -u {r['name']} -f" if r["kind"] == "systemd"
                                                                else service.tilde(str(st.root / 'service.log'))))
    print("服务日志里不会有你的消息内容 / the service log never holds your messages.")
    print(SERVICE_NEXT)


RESCUE_LOG_KEYS = ("ts", "ev", "why", "action", "result", "step", "code", "phase", "count", "reason")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def rescue_log_tail(st: State, n: int = 8) -> list[dict]:
    """The last n lines of <state>/rescue.log, only the metadata keys (the file never holds text or keys; this keeps it so)."""
    try:
        lines = (st.root / "rescue.log").read_text(errors="replace").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):   # review P332 LOW-6: no control character (raw or JSON-escaped) reaches the terminal
            out.append({k: (_CTRL.sub("", rec[k])[:80] if isinstance(rec[k], str) else rec[k])
                        for k in RESCUE_LOG_KEYS if k in rec and isinstance(rec[k], (str, int, float))})
    return out


def cmd_rescue(a) -> None:
    """P332 (§33): the rescue watchdog's unit."""
    import subprocess
    from . import service
    if a.mode == "watchdog":
        from . import rescue_watchdog
        raise SystemExit(rescue_watchdog.main())
    st = State()
    if a.mode == "undo":   # 「重置连接」 step 3 put a conversation aside (reset_undo.json): pin it again
        _rescue_undo(st)
        return
    try:
        if a.mode == "status":
            s = service.rescue_status()
            tail = rescue_log_tail(st) if st.root.is_dir() else []
            if a.json:
                print(json.dumps({**s, "path": service.tilde(s["path"]) if s.get("path") else None, "log": tail}, ensure_ascii=False))
                return
            if s["kind"] == "none":
                print(f"没有可用的服务管理器 / no service manager here ({s['active']})")
            else:
                print(f"{s['name']}：{'已安装 / installed' if s['installed'] else '没安装 / not installed'} · {s['active']}"
                      + (f"  ({service.tilde(s['path'])})" if s["installed"] else ""))
            for rec in tail:
                ts = rec.pop("ts", 0)
                when = time.strftime("%m-%d %H:%M:%S", time.localtime(ts if isinstance(ts, (int, float)) else 0))
                print(f"  {when}  " + " ".join(f"{k}={v}" for k, v in rec.items()))
            return
        if a.mode == "uninstall":
            r = service.uninstall_rescue(st if st.root.is_dir() else None)
            print(f"✓ 已停止并删除 / stopped and removed: {r['name']}" if r["removed"] else f"{r['name']} 没安装 / was not installed")
            return
        _need_init(st)
        r = service.install_rescue(st)
    except service.ServiceError as e:
        if e.reason in service.NO_MANAGER:
            print("✗ " + service.MESSAGES[e.reason], file=sys.stderr)
            sys.exit(service.NO_MANAGER_EXIT)
        sys.exit("✗ " + service.MESSAGES.get(e.reason, e.reason) + (f"\n  {e.detail}" if e.detail else ""))
    except (OSError, subprocess.SubprocessError) as e:
        sys.exit(f"✗ 服务管理命令没能运行（{type(e).__name__}），运行 agentj doctor / a service-manager command could not run "
                 f"({type(e).__name__}): run agentj doctor")
    print(f"✓ 已安装 / installed: {r['name']} ({r['kind']}) → {service.tilde(r['path'])}")
    if r.get("new"):
        print("  " + service.MAC_LOGIN_ITEM_NOTE)


def _rescue_undo(st: State) -> None:
    """P332: go back to the conversation the phone's 「重置连接」 put aside (step 3). The id comes only from
    <state>/reset_undo.json (written by reset.py); the owned harness restarts before its next turn and resumes it."""
    from . import reset
    try:
        rec = json.loads((st.root / reset.UNDO).read_text())
        kind, sid = rec.get("kind"), rec.get("session")
    except (OSError, ValueError, AttributeError):
        kind = sid = None
    if kind not in st.AGENT_KINDS or not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{4,128}", sid):
        sys.exit("没有可以找回的对话（「重置连接」没有换过新对话）/ nothing to go back to: no reset put a conversation aside")
    cur = st.agent_session(kind)
    st.set_agent_session(kind, sid)
    if cur and cur != sid:   # the conversation opened after the reset can come back the same way
        reset.write_undo(st, kind, cur)
    else:
        (st.root / reset.UNDO).unlink(missing_ok=True)
    st.log("reset_undo", agent=kind, result="ok")
    try:
        res = names.ctl_call(st, {"cmd": "agent_restart"}, 10)
    except names.ServeBusy:
        res = None
    if res is not None and res.get("error") == "shared":
        print("✓ 已记下原来的对话；共享会话请在Agent J 主机上自己切回 / noted; a shared session is switched on the Agent J host itself")
        return
    print("✓ 已找回原来的对话：下一条消息就接着它聊。/ The earlier conversation is back: the next message continues it.")


def _offer_key_import(service) -> None:
    """P198 (I): Jev's key (P157 approval layer 2) in this shell but not in the service env file → a person at a terminal is
    asked once (y/N) and it is imported in one step; an Agent (no terminal) gets the one command to run. Neither has it →
    one line on how to add it from the phone. Never a value; never fails the install."""
    try:
        n = service.name()
        note = service.key_note(n)
        if not note:
            return
        key = service.JEV_KEY_ENV
        if (os.environ.get(key) or "").strip() and sys.stdin.isatty() and sys.stdout.isatty():
            ans = input(f"? 这个终端里有 {key}，后台服务里没有。导入服务（不显示值）？/ {key} is in this terminal but not in the "
                        "service. Import it (value not shown)? [y/N] ").strip().lower()
            if ans in ("y", "yes", "是"):
                service.import_key(n, key)
                print(f"✓ {key} 已导入服务环境文件 / imported into the service env file ({service.tilde(service.env_file(n))})")
                return
        print("! " + note)
    except (service.ServiceError, OSError, EOFError):
        pass


SERVICE_NEXT = ("下一步：运行 `agentj doctor`，把每一行以 ! 或 ✗ 开头的内容报告出来（AI 装的话，原样转告主人）。/ Next: run "
                "`agentj doctor` and report every line that starts with ! or ✗ (an AI installing this: tell your human, "
                "word for word).")


def cmd_update(a) -> None:
    from . import service, update
    if a.mode == "auto":
        st = State()
        _need_init(st)
        if a.switch in ("on", "off"):
            with st.config_lock():
                cfg = st.config()
                cfg["update_check"] = a.switch == "on"
                st.write_private(st.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())
            st.log("update_check_switch", status=a.switch)
        on = update.auto_enabled(st)
        print(("开：agentj serve 每天向 GitHub 上的公开仓库查一次最新版本号，有新版就给手机发一条提醒（从不自动安装）。"
               if on else "关：serve 不查新版本（`agentj update check` 仍可手动查）。")
              + " / daily update check " + ("on" if on else "off"))
        return
    if a.mode == "apply" and (a.authorization or a.from_email):
        return _update_authorized(a)
    if a.mode != "apply" and (a.authorization or a.from_email or a.version):
        sys.exit("✗ 升级参数只用于 apply / upgrade options require apply")
    r = update.check()
    if a.mode == "check":
        if a.json:
            print(json.dumps(r, ensure_ascii=False))
            return
        print(_update_line(r))
        print("  " + update.EXPLAIN[r["status"]])
        if r["status"] == "newer":
            print(f"  （agentj update apply 会运行 / it runs:  {r['command']}）")
        return
    if r.get("status") == "newer" and not a.yes:
        held = _upgrade_gate(r)
        if held:
            print(update.result_block(held))
            raise SystemExit(update._exit_of(held["reason"]))
    st = State()
    res = update.apply(st, a.version, check_fn=lambda: r, say=print)
    _upgrade_failed(st, res)
    print(update.result_block(res))
    raise SystemExit(res["exit"])


def _upgrade_failed(st, res: dict) -> None:
    """P277: a failed attempt goes to support by itself (unless the owner switched it off) and the block gets the
    `support:` / `tell_owner:` lines. Never changes the result or the exit code."""
    if not isinstance(res, dict) or res.get("result") == "ok" or not res.get("step"):
        return
    try:
        from . import upgrade_diag
        upgrade_diag.after_failure(st, res, lang=_lang(st))
    except Exception:  # noqa: BLE001
        pass


def _upgrade_gate(r: dict) -> dict | None:
    """P203: before a manual upgrade, ask the running serve what a restart now would interrupt (running turn — not the
    caller's own when an Agent runs this —, queued messages, open cards, scheduled / background tasks, friend sessions).
    Nothing → None (go on, one step). Something → the owner decides: at a terminal a y/N here; from an Agent or a script
    a confirmation card on the phone (its 「现在升级」 is the phone's own signed /update) and a result block, nothing
    installed. No serve / no answer → None, as before (the restart waits for an idle serve anyway). --yes skips this."""
    from . import auto_update, ctl_auth, update
    from .names import ctl_call
    st = State()
    caller = "agent" if set(os.environ) & set(ctl_auth.AGENT_ENV) else None
    try:
        ans = ctl_call(st, {"cmd": "upgrade_impact", "caller": caller}, 10)
    except Exception:  # noqa: BLE001 — serve busy or gone: the old behaviour
        return None
    items = (ans or {}).get("items") if isinstance(ans, dict) and ans.get("ok") else None
    if not items:
        return None
    lang = _lang(st)
    lines = auto_update.impact_text(items, lang)
    base = {"result": "refused", "from": update.__version__, "to": r.get("latest"), "service": "unchanged", "interrupts": lines}
    if caller is None and sys.stdin.isatty() and sys.stdout.isatty():
        print("现在升级会打断：" if lang == "zh" else "Upgrading now would interrupt:")
        for line in lines:
            print("  · " + line)
        try:
            ok = input("仍然现在升级？[y/N] " if lang == "zh" else "Upgrade now anyway? [y/N] ").strip().lower() in ("y", "yes", "是")
        except EOFError:
            ok = False
        return None if ok else {**base, "reason": "not_confirmed"}
    try:
        ctl_call(st, {"cmd": "upgrade_card", "caller": caller}, 10)
    except Exception:  # noqa: BLE001 — the block still says why nothing was installed
        pass
    return {**base, "reason": "confirm_on_phone"}


def _update_authorized(a) -> None:
    """F12 / contract C3: the owner's upgrade email authorises the Agent to upgrade to exactly one version — no y/N, no
    terminal. Ends with update.result_block (fixed format the Agent copies back); the exit code says which outcome."""
    from . import update
    if a.authorization and a.from_email:
        sys.exit("✗ 只用一个：--authorization 或 --from-email / use one of --authorization or --from-email")
    mail = None
    if a.from_email:
        try:
            mail = update.parse_email(update.read_email(a.from_email))
        except (OSError, ValueError):
            mail = {"problem": "bad_email"}
    st = State()

    def say(line: str) -> None:
        print(line, flush=True)
    res = update.authorized_apply(st, a.authorization, a.version, mail=mail, say=say)
    _upgrade_failed(st, res)
    print(update.result_block(res), flush=True)
    sys.exit(res["exit"])


def _update_line(r: dict) -> str:
    from . import update
    s = r["status"]
    if s == "newer":
        return f"! newer · 有新版本 {r['latest']}（这台Agent J 主机是 {r['current']}）/ newer version {r['latest']} (the Agent J host: {r['current']})"
    if s == "current":
        return f"✓ current · 已是最新 / up to date: {r['current']}"
    if s == "ahead":
        return (f"✓ ahead · 这台Agent J 主机是 {r['current']}，公开仓库最新发布的是 {r['latest']} / the Agent J host runs {r['current']}, "
                f"the newest public release is {r['latest']}")
    why = update.WHY.get(r["why"], r["why"])
    return f"? unknown · 没查到最新版本（{why}）；这台Agent J 主机是 {r['current']} / could not check ({why}); the Agent J host: {r['current']}"


NEXT_STEPS = (   # (command, 中文, English)
    ("agentj init", "生成本机身份", "create this host's identity"),
    ("agentj login", "把这台Agent J 主机加到你的 Agent J 账号（有设置码：--seat-file）",
     "add the Agent J host to your Agent J account (setup code: --seat-file)"),
    ("agentj passphrase set", "设遥控器配对密码（自己输，别让 Agent 代劳）", "set the remote pairing password (yourself)"),
    ("agentj agent claude --dir <folder>", "接上 Claude Code（或 codex / opencode）", "connect Claude Code (or codex / opencode)"),
    ("agentj service install", "后台常驻运行 serve", "keep `agentj serve` running"),
    ("agentj pair", "手机扫码配对", "pair your phone (scan the QR)"),
)


def _done_steps(st: State) -> list[bool]:
    if not st.exists():
        return [False] * len(NEXT_STEPS)
    from . import service
    try:
        st.check_perms()
    except PermissionError:
        return [True] + [False] * (len(NEXT_STEPS) - 1)
    return [True, cloud.read_cloud(st) is not None, gate.is_set(st) or cloud.read_cloud(st) is not None, st.agent_config() is not None,
            bool(service.status().get("installed")), bool(st.devices())]


def cmd_hint() -> None:
    st = State()
    done = _done_steps(st)
    print(f"{DIST} {__version__} — 用手机和你自己的 Claude Code / Codex 对话 · talk to your own Agent from your phone\n")
    for i, ((cmd, zh, en), ok) in enumerate(zip(NEXT_STEPS, done), 1):
        if cloud.read_cloud(st) and cmd == "agentj passphrase set":
            zh, en = "设遥控器配对密码（可选，推荐：别的Agent J 主机上批准新遥控器、终端配对时用）", "set a remote pairing password (optional, recommended)"
        elif cloud.read_cloud(st) and cmd == "agentj pair":
            cmd, zh, en = "账户页 / Account page", "席位卡添加遥控器 → 扫码", "seat card → Add a remote → scan"
        print(f"  {'✓' if ok else '·'} {i}. {cmd:<36} {zh} / {en}")
    nxt = next((c for (c, _, _), ok in zip(NEXT_STEPS, done) if not ok), None)
    if nxt == "agentj pair" and cloud.read_cloud(st):
        nxt = "账户页席位卡 → 添加遥控器 / account seat card → Add a remote"
    print("\n" + (f"下一步 / next:  {nxt}" if nxt else "都设好了 / all set — 手机上打开 m.agentj.app 开始对话 / open m.agentj.app on your phone"))
    print("自检 / health check:  agentj doctor     ·     全部命令 / all commands:  agentj --help")


JARVIS_NOTICE = "`jarvis` 已改名为 `agentj`，旧命令下个版本移除 / `jarvis` is now `agentj`; the old name goes away in the next version"


def main_jarvis(argv=None) -> None:
    """`python -m jarvis_host.cli` (≤ 0.9 service units) and the checkout's host/jarvis: the notice, then the same CLI."""
    print(JARVIS_NOTICE, file=sys.stderr, flush=True)
    main(argv)


# `migrate status` reports, `migrate rollback` undoes, `docs-rule` prints (or, with --write and the human's y, appends to the
# AI's own memory file), `handover` only reads: none may move the state directory first
NO_MIGRATE = ("files", "diag", "preflight", "migrate", "docs-rule", "handover", "recall", "friends", "codex-sandbox", "capability", "google", "setup", "busy", "mail", "contacts", "imessage", "ask", "order", "core")   # recall / friends / codex-sandbox / capability: usually inside the fence


APPROVALS_HELP = {
    "standard": ("standard（默认）：共享会话里，只有改凭据、把凭据显示到屏幕、花钱、对外发送、公开删除才需要手机点头；"
                 "程序按名字读取 .env 等不再弹窗。",
                 "standard (default): in a shared session the phone is asked only when a credential is changed or shown on "
                 "screen, or for spending, sending out and public deletion; a program reading .env by name no longer asks."),
    "strict": ("strict：只要命令里出现凭据文件（哪怕只是读取或提到）就要手机点头，即旧规则。",
               "strict: any credential file anywhere in a call asks the phone, even a read or a mention (the old rule)."),
    "native": ("native：共享会话里 Agent J 不另外弹卡，花钱、对外发送、公开删除、改凭据都只看 Claude Code / Codex / OpenCode "
               "自己的权限设置（只有改 Agent J 的 hook 文件仍要手机点头）。",
               "native: in a shared session Agent J adds no card of its own — spending, sending out, public deletion and credential "
               "changes follow only Claude Code's / Codex's / OpenCode's own permission settings (rewriting Agent J's hook files "
               "still asks the phone)."),
}


def config_approvals(rest: list) -> int:
    """`agentj config approvals standard|strict|native|status` (P128, P198): shared-mode approval strength; independent
    mode unchanged."""
    from . import preferences
    p = argparse.ArgumentParser(prog="agentj config approvals",
                                description="共享模式审批强度 / shared-mode approval strength (standard = default, strict = old rule, "
                                            "native = the harness's own permissions only)")
    p.add_argument("level", nargs="?", default="status", choices=("standard", "strict", "native", "status"))
    a = p.parse_args(rest)
    lang = "en" if str(preferences.get(preferences.effective(State()), "appearance.language", "zh")).startswith("en") else "zh"
    if a.level == "status":
        cur = preferences.get(preferences.effective(State()), "agent.approvals", "standard")
        print(APPROVALS_HELP[cur if cur in APPROVALS_HELP else "standard"][lang == "en"])
        return 0
    code = preferences.command(["set", "agent.approvals", a.level])
    if not code:
        print(APPROVALS_HELP[a.level][lang == "en"])
    return code or 0


def cmd_instances(a) -> None:
    """P180 / P217: the instances on the Agent J host; `remove N` deletes instance N ≥ 2 (its workspace stays)."""
    from . import instance
    if a.action == "remove":
        if not a.number:
            sys.exit("✗ 用法 / usage: agentj instances remove <N>")
        try:
            r = instance.remove(instance._num(a.number))
        except instance.InstanceError as e:
            sys.exit("✗ " + str(e))
        print(f"✓ 实例 {r['instance']} 已删除 / removed；工作目录保留 / workspace kept: {r['workspace']}")
        return
    rows = [instance.describe(n) for n in instance.numbers()]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False))
        return
    if not rows:
        print("这台Agent J 主机上还没有 Agent J 实例 / no instance here")
        return
    for r in rows:
        on = "在线 online" if r["online"] else ("运行中 running" if r["running"] else "离线 offline")
        name = r["label"] or r["agent_name"] or "-"
        print(f"{r['instance']:>2}  {name}  · {on} · 遥控器 remotes {r['remotes']}"
              + (f"（在线 {r['remotes_online']}）" if r["remotes_online"] else "")
              + f" · 席位 seat {r['seat'] or '未绑定 unbound'} · {r['service']} {r['service_active']}"
              + f"\n    {r['agent'] or '-'} → {r['workspace'] or '-'}")


def cmd_label(a) -> None:
    from . import instance
    n = instance.current()
    try:
        if a.text:
            print("✓ " + (instance.set_label(n, " ".join(a.text)) or "（已清空 / cleared）"))
        else:
            print(instance.label(n) or "-")
    except instance.InstanceError as e:
        sys.exit("✗ " + str(e))


def main(argv=None) -> None:
    from . import instance
    try:   # P180: `--instance N` / AGENTJ_INSTANCE first — everything below reads the state dir / preferences / unit name
        inst, rest = instance.split_argv(list(sys.argv[1:] if argv is None else argv))
        inst = instance.apply(inst)
    except instance.InstanceError as e:
        sys.exit("✗ " + str(e))
    if rest != list(sys.argv[1:] if argv is None else argv):
        argv = rest
    if inst > 1 and not instance.exists(inst) and rest and rest[0] in instance.AUTO_CREATE:
        try:
            instance.create(inst)
        except instance.InstanceError as e:
            sys.exit("✗ " + str(e))
    from .service import load_launch_binary_env, ServiceError, user_runtime_dir
    user_runtime_dir()   # P329: an SSH login without PAM (Olares) still reaches the user manager, journalctl and systemd-run
    try:
        load_launch_binary_env()
    except ServiceError as e:
        sys.exit(e.detail or e.reason)
    from . import relay_auth
    relay_auth.load()
    args = list(sys.argv[1:] if argv is None else argv)
    # P127: the owner's answer about an old (≤ 0.9) identity, for runs without a terminal (migrate.auto)
    legacy = None
    for flag, d in (("--migrate-legacy", "migrate"), ("--fresh", "fresh")):
        if flag in args:
            if legacy and legacy != d:
                sys.exit("✗ --migrate-legacy 和 --fresh 只能选一个 / choose one of --migrate-legacy and --fresh")
            legacy = d
            args = [x for x in args if x != flag]
    if legacy:
        argv = args
    if args and args[0] in ("config", "provider", "installer", "voice", "theme", "menu", "key", "channel", "skill"):
        from . import migrate        # these answer before argparse: settle an old identity first, never adopt it silently
        try:
            if migrate.pending() == "ready" and migrate.auto(decision=legacy) == "undecided":
                sys.exit(2)
        except OSError:
            pass
    if args[:2] == ["config", "auto-update"]:
        from . import auto_update
        raise SystemExit(auto_update.command(args[2:]))
    if args[:2] == ["config", "private-instructions"]:
        from . import private_instructions
        raise SystemExit(private_instructions.command(args[2:]))
    if args[:2] == ["config", "claude-statusline"]:
        from . import claude_statusline
        raise SystemExit(claude_statusline.command(args[2:]))
    if args[:2] == ["config", "claude-inbound"]:
        from . import claude_inbound
        raise SystemExit(claude_inbound.command(args[2:]))
    if args[:2] == ["config", "approvals"]:
        raise SystemExit(config_approvals(args[2:]))
    if args[:2] == ["provider", "profile"]:
        from . import provider_profiles
        raise SystemExit(provider_profiles.command(args[2:]))
    if args and args[0] == "installer":
        from . import installer
        installer.main(args[1:])
        return
    if args and args[0] == "theme":      # P169 (§23): list|show|validate|apply|reset|restore|export|schema; light|dark|system as before
        from . import theme
        raise SystemExit(theme.command(args[1:]))
    if args and args[0] == "card":       # P360 (§34): Agent J Cards — list|validate|apply|on|off|remove|export|schema
        from . import jcards
        raise SystemExit(jcards.command(args[1:]))
    if args and args[0] in ("voice", "menu", "key", "channel", "skill"):
        from . import personalize
        raise SystemExit(personalize.command(args))
    if args and args[0] == "config" and not (len(args) >= 3 and args[1] in ("activity", "history") and args[2] in ("on", "off", "status")):
        from . import preferences
        raise SystemExit(preferences.command(args[1:]))
    if argv is None and os.path.basename(sys.argv[0] or "") == "jarvis":   # the compat symlink made on a migrated computer
        print(JARVIS_NOTICE, file=sys.stderr, flush=True)
    p = argparse.ArgumentParser(prog="agentj", description="Agent J 主机端（alpha）：用手机和你自己的 Agent 对话 / Agent J host "
                                                           "(alpha): talk to your own Agent from your phone")
    p.add_argument("-V", "--version", action="version", version=f"{DIST} {__version__}")
    # P127: read (and removed) before parsing, above; declared here for --help and the docs check
    p.add_argument("--migrate-legacy", action="store_true", help="把旧身份搬过来继续用 / keep "
                                                                "using an older identity found on the Agent J host")
    # P217: `--instance N` is read (and removed) before parsing, like the two above; declared here for --help and the docs check
    p.add_argument("--instance", metavar="N", help="同一系统用户下的第 N 个实例（1–10，1 = 原来那套）：一个实例一个席位、"
                                                  "一个工作目录 / instance N on this OS user (1–10; 1 = the original): one seat "
                                                  "and one folder per instance — see agentj instances")
    p.add_argument("--fresh", action="store_true", help="不用旧身份，开一个全新的（旧目录原样保留）/ ignore an older identity and "
                                                       "start fresh (the old folder stays as it is)")
    sub = p.add_subparsers(dest="cmd")
    dc = sub.add_parser("doctor", help="自检：一项一行 ✓/!/✗ + 修法 / health check, one line per check",
                        description="自检 / health check: ✓ ok · ! warning · ✗ must fix. Exit 0 unless a ✗. Never prints secrets.")
    dc.add_argument("--json", action="store_true", help="机器可读 / machine-readable (paths shown with ~)")
    dc.add_argument("--read-only", action="store_true", help="仅报告，不安装、修复或重连 / inspect only; no installation, repair or reconnect")
    dc.add_argument("--offline", action="store_true", help="跳过网络检查 / skip the network checks")
    dc.add_argument("--isolation-only", action="store_true", help="仅隔离预检：不读用户状态、不联网 / isolation preflight only, no user state or network")
    dc.add_argument("--post-upgrade", action="store_true", help="修复升级后的工作根与连接 / repair post-upgrade root and connection")
    dc.add_argument("--upgrade-only", action="store_true", help="仅升级后的包与服务完整性 / package and service integrity only")
    dc.add_argument("--reconnect", action="store_true", help="手机灰了/断开：自检 服务·中继·Agent 三段，哪段断了修哪段 / phone grey or "
                    "disconnected: check service, relay and Agent, repair the broken one")
    dc.add_argument("--session", help="和 --reconnect 一起：接上这个会话 ID / with --reconnect: attach this session ID")
    dc.set_defaults(fn=cmd_doctor)
    rl = sub.add_parser("reload", help="重新加载配置与服务密钥文件，不重启 serve / reload the configuration and the service key file "
                        "without restarting serve",
                        description="大多数设置改完 reload 就生效（主 Agent 下一条消息前重启，对话保留）；只有工作根目录、会话模式要重启服务；从密钥文件删掉的 key 也要重启才会消失。"
                                    " / Most settings take effect on reload (the main Agent restarts before its next message, same "
                                    "conversation); only the working root and session mode need `agentj service restart` — so does a key "
                                    "removed from the service key file.")
    rl.add_argument("--json", action="store_true", help="机器可读 / machine-readable")
    rl.set_defaults(fn=cmd_reload)
    sv = sub.add_parser("service", help="开机 / 登录后自动运行 serve：install · start · stop · restart · status / run serve as a service",
                        description="Linux: systemd user unit · macOS: LaunchAgent. 不写任何密钥 / never writes a secret.")
    sv.add_argument("mode", choices=["install", "uninstall", "status", "restart", "start", "stop", "import-key"], help="install 安装 · start 启动 · stop 停止 · restart 重启 · uninstall 删除 · status 状态 · import-key 把这个终端里的一个 key 导入服务（不显示值）/ install, start, stop, restart, uninstall, status, import-key: copy one key from this terminal into the service (value never shown)")
    sv.add_argument("key", nargs="?", default=None, help="import-key：变量名，默认 OPENROUTER_API_KEY（Jev 审批第二层）/ import-key: the variable name, default OPENROUTER_API_KEY (Jev, approval layer 2)")
    sv.add_argument("--json", action="store_true", help="status 的机器可读输出 / machine-readable status")
    sv.add_argument("--deferred", help=argparse.SUPPRESS)
    sv.add_argument("--recovery-worker", help=argparse.SUPPRESS)
    sv.set_defaults(fn=cmd_service)
    rs = sub.add_parser("rescue", help="救援守护（手机「紧急修复」在 Agent J 没响应时也能用）：status · install · uninstall / "
                                       "the rescue watchdog (the phone's emergency repair works even when Agent J stops answering)",
                        description="独立的小进程：Agent J 约 45 秒没响应就接手手机连接，只接已配对手机的签名请求（重启、请求远程帮忙）。"
                                    "agentj service install / update 会自动装上。 / A separate small process: when Agent J has not "
                                    "answered for about 45 s it takes the phone link and accepts only signed requests from paired "
                                    "phones (restart, ask support for help). Installed by agentj service install and every update.")
    rs.add_argument("mode", choices=["status", "install", "uninstall", "watchdog", "undo"],
                    help="status 状态与最近记录 · install 安装 · uninstall 删除 · watchdog 前台运行（服务用）· undo 找回「重置连接」"
                         "换掉的对话 / status and recent log lines · install · uninstall · watchdog: run in the foreground (what "
                         "the service runs) · undo: go back to the conversation 「重置连接」 put aside")
    rs.add_argument("--json", action="store_true", help="status 的机器可读输出 / machine-readable status")
    rs.set_defaults(fn=cmd_rescue)
    up = sub.add_parser("update", help="check 查版本 · apply Agent 自升级（无需终端）· auto on|off",
                        description="apply needs no y/N or terminal, restarts and runs doctor. F12 authorization remains optional.")
    up.add_argument("--yes", action="store_true", help="跳过升级前确认：现在会打断的事照样打断 / skip the check of what a restart now would interrupt")
    up.add_argument("mode", choices=["check", "apply", "auto"])
    up.add_argument("switch", nargs="?", choices=["on", "off", "status"], default="status", help="auto 的开关 / for auto")
    up.add_argument("--json", action="store_true", help="check 的机器可读输出 / machine-readable check")
    up.add_argument("--authorization", metavar="AJUP-…", help="主人升级邮件里的授权码 / the authorization code from the owner's upgrade email")
    up.add_argument("--from-email", metavar="FILE|-", help="整封升级邮件（文件或 - 读标准输入）：从中找授权码和目标版本 / the whole "
                                                          "upgrade email (file, or - for stdin): code and target version are read from it")
    up.add_argument("--version", help="目标版本（缺省：邮件里的目标版本，或公开仓库的最新版）/ target version (default: the email's, or the latest)")
    up.set_defaults(fn=cmd_update)
    i = sub.add_parser("init", help="生成主机身份密钥")
    i.add_argument("--relay", default=DEFAULT_RELAY)
    i.add_argument("--web", default=DEFAULT_WEB)
    i.add_argument("--force", action="store_true")
    i.add_argument("--working-root", help="工作根目录：已有目录或 ~/coding / work root; existing files stay put")
    i.set_defaults(fn=cmd_init)
    s = sub.add_parser("serve", help="在前台运行 Agent J，收发手机消息（平时用 agentj service install 让它在后台运行）/ run Agent J in the foreground")
    s.add_argument("--events", choices=["text", "jsonl", "quiet"], default="text",
                   help="text：终端（显示消息）· jsonl：脚本 · quiet：服务模式，只有元数据、不含消息 / quiet = service mode, metadata only")
    s.add_argument("--no-stdin", action="store_true", help="不读终端输入（服务 / 后台） / do not read stdin")
    s.set_defaults(fn=cmd_serve)
    from . import protocol_handler
    proto = sub.add_parser("protocol", help="注册或打开本机配对链接 / register or open local pairing link")
    protos = proto.add_subparsers(dest="mode", required=True)
    protos.add_parser("install")
    protos.add_parser("open").add_argument("url")
    proto.set_defaults(fn=protocol_handler.main)
    pr = sub.add_parser("pair", help="用二维码配对一台手机（Agent J 要在运行：agentj service status）/ pair a phone (Agent J must be "
                                     "running: agentj service status)",
                        description="配对手机：在这个终端输入手机上的 6 位安全码和遥控器配对密码即可批准（安全码输错可再试，共 3 次）；"
                                    "还没设遥控器配对密码的话，这条命令会先请你设一个（至少 8 位）。也可以到账户页席位卡批准，先完成的算数。远程 SSH：`agentj pair --link`，在同一个终端里完成。/ Pair a phone. Pairing at the "
                                    "terminal needs the remote pairing password; if none is set yet, this command asks you to set one first "
                                    "(at least 8 characters). Then type the 6-digit code the phone shows (3 tries) and the password here, or "
                                    "approve on the account page — whichever comes first. Over SSH: `agentj pair --link` and finish in the same terminal.")
    pr.add_argument("--no-qr", action="store_true", help="不画二维码，改为打印配对链接")
    pr.add_argument("--link", action="store_true", help="兼容选项：默认已同时打印二维码与配对链接 / Compatibility: QR and link are already shown by default")
    pr.add_argument("--check", action="store_true", help="只做配对前检查：该走账户页通行密钥还是本机遥控器配对密码（不开始配对）/ "
                                                        "pre-check only: account page or local remote pairing password route")
    pr.add_argument("--json", action="store_true", help="与 --check 一起：输出 JSON")
    pr.set_defaults(fn=cmd_pair)
    d = sub.add_parser("devices", help="列出已批准的设备")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_devices)
    r = sub.add_parser("revoke", help="吊销一台设备并立即断开")
    r.add_argument("device")
    r.set_defaults(fn=cmd_revoke)
    se = sub.add_parser("send", help="给所有在线的已批准设备发一条文字")
    se.add_argument("text")
    se.set_defaults(fn=cmd_send)
    nt = sub.add_parser("notify", help="主人自己的自动化往手机发一条消息（可带图片/PDF/文本文件）；Agent 不能用 / "
                                       "your own automation posts to the phone (never an Agent)")
    nt.add_argument("--from", dest="frm", metavar="NAME", help="自动化的名字（先 `agentj notify --allow NAME` 登记）")
    nt.add_argument("--title", help="标题（≤ 64 字）")
    nt.add_argument("--text", help="正文（≤ 4000 字；- = 从标准输入读）")
    nt.add_argument("--file", action="append", metavar="PATH", help="附件：png jpg gif webp pdf txt md，≤ 20 MB，最多 4 个（可重复）")
    nt.add_argument("--json", action="store_true", help="输出 JSON（delivered / files / bytes / agent: queued|no_agent|failed）")
    ng = nt.add_mutually_exclusive_group()
    ng.add_argument("--list", action="store_true", help="列出登记的自动化和开关")
    ng.add_argument("--allow", metavar="NAME", help="登记并打开一个自动化（建自动化时就决定）")
    ng.add_argument("--deny", metavar="NAME", help="关掉一个自动化（它再发会被拒绝）")
    ng.add_argument("--forget", metavar="NAME", help="删除一个自动化的登记")
    nt.set_defaults(fn=cmd_notify)
    ss = sub.add_parser("status", help="serve 的状态")
    ss.add_argument("--json", action="store_true", help="只输出 JSON / JSON only (P147)")
    ss.set_defaults(fn=cmd_status)
    nm = sub.add_parser("name", help="查看 / 修改这台Agent J 主机上 Agent 的名字（加入了 Agent J 账号的话，先在账号后台改，改好了才改这台Agent J 主机上的）")
    nm.add_argument("name", nargs="?", help="新名字（中文最多 8 个字，英文 / 数字 / 横杠最多 13 个；混写时中文算 1.6、合计 ≤ 13）；不填 = 查看")
    nm.set_defaults(fn=cmd_name)
    ad = sub.add_parser("admin", help="打开本机的 Agent 管理页（只在 127.0.0.1；配对、遥控器、改名）")
    ad.add_argument("--port", type=int, default=0, help="端口（默认随机）。地址永远是 127.0.0.1，不能改")
    ad.add_argument("--events", choices=["text", "jsonl"], default="text",
                    help="jsonl：每个新链接打印一行 {\"ev\":\"admin\",\"url\",\"port\",\"expires_in\"}（脚本 / 测试用）。"
                         "注意 url 里的 #t= 就是登录密钥：别把 stdout 接到日志 / journal；后台运行请用 --url-file")
    ad.add_argument("--url-file", metavar="PATH",
                    help="把链接（同样的 jsonl 行）写进这个新建的文件（0600，必须还不存在），stdout 不再出现链接")
    ad.add_argument("--no-stdin", action="store_true", help="不读终端输入（后台运行时用）")
    ad.add_argument("--open", action="store_true",
                    help="用这台Agent J 主机的默认浏览器直接打开管理页（链接不打印、只用一次；命令立刻返回，页面 30 分钟不用自动关闭）/ "
                         "open the admin page in the Agent J host's default browser (link never printed; returns at once)")
    ad.add_argument("--open-child", action="store_true", help=argparse.SUPPRESS)
    ad.set_defaults(fn=cmd_admin)
    lo = sub.add_parser("login", help="把这台Agent J 主机加到你的 Agent J 账号（占 1 个席位）/ add the Agent J host to your Agent J account (uses 1 seat)")
    lo.add_argument("--api", help=f"服务器地址（默认 $AGENTJ_API_URL 或 {cloud.DEFAULT_API}）")
    lo.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)   # only with --account; see _account_arg
    lo.add_argument("--account", metavar="ACCOUNT_ID",
                    help="你的 Agent J 账号 ID：账号后台报回来的账号不是它就拒绝，什么都不写（退出码 2）/ your Agent J account ID: "
                         "if the account dashboard reports another account, nothing is written (exit 2)")
    seat = lo.add_mutually_exclusive_group()
    seat.add_argument("--seat", metavar="CODE",
                      help="用账号后台给的设置码（ajt_…）直接加入，不用Agent J 主机绑定码、不问 y/N；`-` = 从标准输入读一行 / "
                           "join with a setup code from the account dashboard (no computer binding code, no y/N); `-` reads stdin")
    seat.add_argument("--seat-file", metavar="PATH",
                      help="从文件读设置码（文件必须 0600），这样它不进命令行和 shell 历史 / "
                           "read the setup code from a 0600 file, so it stays out of argv and shell history")
    seat.add_argument("--paste", action="store_true",
                      help="在终端里粘贴设置码或安装码（不显示、不进命令行；整段提示词也行）。在终端里直接运行 agentj login 也会先问 / "
                           "paste a setup or installation code at the prompt (hidden, out of argv; a whole prompt works). "
                           "Plain `agentj login` at a terminal asks first too")
    seat.add_argument("--install-code-file", metavar="PATH", help="从 0600 文件读取 AJI 安装码，给已安装的Agent J 主机绑定席位 / bind an installed host using an AJI code in a private file")
    lo.add_argument("--name", help="和 --seat 一起：本机 Agent 的名字（长度最多 13：中文每个字算 1.6，其他字符每个算 1，即纯中文最多 8 个字、"
                                   "纯英文最多 13 个字母；不给就在终端里问，回车或非交互时用主机名，重名自动加序号）/ "
                                   "with --seat: the Agent's name (length at most 13: each Chinese character counts 1.6, any other "
                                   "character 1, i.e. up to 8 Chinese characters or 13 letters; if omitted it is asked, Enter or no terminal = "
                                   "the host name, numbered on a clash). "
                                   "退出码 / exit: 3 名字已占用 name taken · 4 设置码无效 invalid code · 5 席位未付费 seat not paid · "
                                   "6 账单处理中，几分钟后重试 billing change in progress, retry in a few minutes · "
                                   "2 本地拒绝 refused locally")
    lo.set_defaults(fn=cmd_login)
    sub.add_parser("unlink", help="把这台Agent J 主机移出 Agent J 账号（删掉这台Agent J 主机上的账号记录）/ take the Agent J host out of the Agent J account").set_defaults(fn=cmd_unlink)
    nw = sub.add_parser("news", help="Daily AI News：每天早上把《Musk + AI 24h》（中文）发到这台 Agent J 主机的使用者自己的邮箱（默认关）："
                                     "status · on · off；还没有邮箱时 email <邮箱> 收验证码、verify <邮箱> <验证码> 确认 / "
                                     "the daily \"Musk + AI 24h\" digest (in Chinese) to the email of this Agent J host's own user "
                                     "(default off); with no email yet, email <address> sends a code and verify <address> <code> "
                                     "confirms it")
    nw.add_argument("mode", nargs="?", choices=["status", "on", "off", "email", "verify"], default="status")
    nw.add_argument("args", nargs="*", metavar="arg", help="email <邮箱>；verify <邮箱> <验证码> / email <address>; verify <address> <code>")
    nw.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    nw.set_defaults(fn=cmd_news)
    rh = sub.add_parser("report-hostname", help="账号后台是否显示这台Agent J 主机的名字（默认显示）/ show the Agent J host's name in the account dashboard")
    rh.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")
    rh.set_defaults(fn=cmd_report_hostname)
    rp = sub.add_parser("remote-pair", help="允许从账户页或已配对的遥控器添加遥控器（默认开）/ allow pairing from the account page or a paired remote")
    rp.add_argument("mode", nargs="?", choices=["on","off","status"], default="status")
    rp.set_defaults(fn=cmd_remote_pair)
    ru = sub.add_parser("remote-unbind", help="允许 / 禁止在账号后台解绑这台Agent J 主机的手机遥控器（默认允许）/ allow unlinking phone remotes from the account dashboard")
    ru.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")
    ru.set_defaults(fn=cmd_remote_unbind)
    ag = sub.add_parser("agent", help="接哪个 Agent：claude / codex / opencode / kimi / qwen / dsh / off；reset = 开一段新对话（重启 serve 生效）；"
                                      "restart = 下一条消息前重启 Agent 进程、对话不变（换了 key 或代理后）；"
                                      "detect = 本机有哪些可用 / which agents are usable here；"
                                      "switch <claude|codex|opencode|kimi|qwen|dsh> = 先无头探测、能用才切，失败自动切回 / probe first, switch only if it works, switch back on failure；"
                                      "route = 按本机实测推荐 AI 路线（用不了 Claude Code / Codex 时：中国大陆首推火山方舟 Coding Plan、先买 Lite，香港 / 海外推荐 Kimi，已有某一家就继续用）/ recommend an AI route from what works here (without Claude Code / Codex: Volcano Ark Coding Plan, Lite first, in mainland China; Kimi in Hong Kong / overseas; keep a plan you already have)")
    ag.add_argument("mode", nargs="?", choices=["claude", "codex", "opencode", "kimi", "qwen", "dsh", "off", "reset", "restart", "status", "detect", "switch", "route"], default="status",
                    help="detect = 本机有哪些可用（不需要 init；只看是否安装、登录文件是否存在）/ which agents are usable here "
                         "(no init needed; checks only what is installed and whether login files exist)")
    ag.add_argument("target", nargs="?", choices=["claude", "codex", "opencode", "kimi", "qwen", "dsh"],
                    help="switch：要切到的 harness；detect --probe：只探测这一个（可加 --model） / the harness to switch to; with detect --probe: probe only this one")
    ag.add_argument("--probe", action="store_true", help="detect: verify one tool-free model call using existing login (≤ 60 s each); no native output is returned")
    ag.add_argument("--dry-run", action="store_true", help="switch：只检查能不能切，不切 / only check whether it can switch")
    ag.add_argument("--result", action="store_true", help="switch：上一次切换的结果 / the last switch's outcome")
    ag.add_argument("--scan", action="store_true", help="switch：用服务的环境逐个无头探测三个，列出现在能切到哪些 / probe all three in the service's environment")
    ag.add_argument("--undo", action="store_true", help="switch：撤回上一次切换，不经检查直接切回原来那个（起不来会自动回到现在这个） / "
                    "go back to the harness used before the last switch, with no pre-check (if it does not come up, back here)")
    ag.add_argument("--force", action="store_true", help="switch：原来那个在后台过不了检查也照样切（之后用 --undo 切回） / "
                    "switch even if the current harness would fail the check on the way back (then --undo returns)")
    ag.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ag.add_argument("--job", help=argparse.SUPPRESS)
    ag.add_argument("--json", action="store_true", help="detect 的机器可读输出 / machine-readable detect output")
    ag.add_argument("--dir", help="Agent 的工作目录（默认当前目录）")
    ag.add_argument("--region", choices=["mainland-cn", "global"],
                    help="kimi：Kimi 会员在哪个站买的 —— mainland-cn = kimi.com（默认）· global = kimi.ai；kimi.com 的 key 在 api.kimi.com 和 api.kimi.ai 都能用"
                         " / kimi: which Kimi site the membership was bought on (a kimi.com key works on both api.kimi.com and api.kimi.ai)")
    ag.add_argument("--base-url", dest="base_url",
                    help="dsh：OpenAI 兼容服务的地址（Olares Router、LiteLLM、Ollama…），写成 https://<服务>/v1，配 --model"
                         " / dsh: an OpenAI-compatible server (Olares Router …), with --model")
    ag.add_argument("--model", help="模型（默认用你自己的设置）；OpenCode 写成 服务商/模型，例如 zhipuai/glm-5.3")
    ag.add_argument("--preset", choices=["ark"],
                    help="switch opencode：ark =「OpenCode · 火山方舟」——探测 OpenCode / arkcli、缺 key 给密钥卡命令、写方舟服务商、实测一次才切 "
                         "/ ark = OpenCode on Volcano Ark: detect, key card if missing, write the provider, one real call before switching")
    ag.add_argument("--plan", choices=["agent", "coding", "payg"],
                    help="--preset ark：agent = Agent Plan 套餐（默认，…/api/plan/v3）· coding = Coding Plan 套餐（…/api/coding/v3，只给编程 Agent 用）"
                         "· payg = 按量付费（…/api/v3） / agent = the Agent Plan (default) · coding = the Coding Plan address (coding agents"
                         " only) · payg = pay-as-you-go /api/v3")
    ag.add_argument("--unfenced", action="store_true",
                    help="不隔离运行 Agent（按底层 harness 自己的权限）。默认 Agent 在 bubblewrap 里运行，看不到 Agent J 的状态")
    ag.add_argument("--allow-docker", action="store_true",
                    help="隔离里也让 Agent 用 docker / podman（默认不开；主人要求时可直接打开）。默认容器引擎的 socket 对 Agent 隐藏")
    smx = ag.add_mutually_exclusive_group()
    smx.add_argument("--shared", dest="session_mode", action="store_const", const="shared",
                     help="共享会话（高级）：手机接你Agent J 主机上正开着的会话 / shared session (advanced): attach to your own open session")
    smx.add_argument("--independent", dest="session_mode", action="store_const", const="independent",
                     help="独立会话（默认）：Agent J 自己开一个、在隔离里运行 / independent session (the default): Agent J's own, fenced")
    ag.set_defaults(fn=cmd_agent)
    pp = sub.add_parser("passphrase", help="遥控器配对密码（只存在这台Agent J 主机上）：set 设置 · change 修改 · reset 忘了（会吊销全部遥控器；"
                        "加 --phone 由已配对手机确认、保留全部遥控器）· status · offer（安装结束时问一句，回车跳过）")
    pp.add_argument("mode", nargs="?", choices=["set", "change", "reset", "status", "offer"], default="status")
    pp.add_argument("--phone", action="store_true",
                    help="与 reset 一起：已配对手机确认后在终端输入新密码，全部遥控器保持配对 / with reset: a paired phone "
                         "confirms, you type the new password here, every remote stays paired")
    pp.add_argument("--json", action="store_true", help="与 offer 一起：最后一行输出 JSON（安装脚本用）")
    pp.set_defaults(fn=cmd_passphrase)
    ap = sub.add_parser("approvals", help="手机批准记录（approvals.log；不含命令内容，只有哈希）")
    ap.add_argument("--verify", action="store_true", help="逐条核对设备签名")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--last", type=int, default=0, help="只看最后 N 条")
    ap.set_defaults(fn=cmd_approvals)
    feedback.add_parser(sub)
    sub.add_parser("stop", help="⛔ 全部停下：中断 Agent、拒绝待批准、收回批量授权、暂停定时任务（重启后仍停）/ stop everything"
                   ).set_defaults(fn=cmd_stop)
    sub.add_parser("resume", help="从急停恢复（已配对手机点「恢复」即可；终端要遥控器配对密码或主人的键盘）/ resume after a stop (paired phone, or the owner at the terminal)").set_defaults(fn=cmd_resume)
    me = sub.add_parser("memory", help="Agent 记住了什么：list · show <来源> · rm <来源> <条目> · restore [id] / what the Agent remembers")
    me.add_argument("mode", choices=["list", "show", "rm", "restore"])
    me.add_argument("args", nargs="*")
    me.add_argument("--harness", choices=["claude", "codex", "opencode"], help="默认 = 接的 Agent / default: the configured Agent")
    me.add_argument("--dir", help="工作目录（默认 = Agent 的目录）")
    me.add_argument("--json", action="store_true")
    me.set_defaults(fn=cmd_memory)
    ac = sub.add_parser("activity", help="操作与审批记录（本机 activity.log，30 天）/ activity and approvals, newest last")
    ac.add_argument("--since", help="2h / 3d / 2026-10-01 / 2026-10-01T08:00")
    ac.add_argument("--json", action="store_true")
    ac.add_argument("--clear", action="store_true", help="立即删除全部记录 / delete all of it now")
    ac.set_defaults(fn=cmd_activity)
    cf = sub.add_parser("config", help="开关：activity on|off（操作记录）· history on|off（聊天记录存在这台Agent J 主机上）/ switches")
    cf.add_argument("key", choices=["activity", "history"])
    cf.add_argument("value", nargs="?", choices=["on", "off", "status"], default="status")
    cf.set_defaults(fn=cmd_config)
    hi = sub.add_parser("history", help="聊天记录（手机翻页看到的，存在这台Agent J 主机上）：status · show <页> · search <关键词> · clear · on · off / "
                                        "the phone's pages, kept on the Agent J host")
    hi.add_argument("mode", nargs="?", choices=["status", "show", "search", "clear", "on", "off"], default="status")
    hi.add_argument("id", nargs="*", help="show 的页码，或 search 的关键词（可多个，全部出现才算）/ the page for show, or the words for search (all must appear)")
    hi.add_argument("--time", action="store_true", help="search 按时间排，最新的在前（默认按相关度）/ search: newest first (default: best match)")
    hi.add_argument("--json", action="store_true")
    hi.add_argument("--all", action="store_true", help="clear：连同全部归档立即删除（不能撤销）/ with clear: delete current + every archive")
    hi.set_defaults(fn=cmd_history)
    ib = sub.add_parser("inbox", help="手机传来的文件（在 Agent 目录的 .agentj/inbox 里）：list · clear · path / uploads from the phone")
    ib.add_argument("mode", nargs="?", choices=["list", "clear", "path"], default="list")
    ib.add_argument("--json", action="store_true")
    ib.set_defaults(fn=cmd_inbox)
    sr = sub.add_parser("asr", help="本机语音转写：install · status · test <wav> · engine <sherpa|voxtype|off> · remove / "
                                    "local speech-to-text", add_help=False)
    sr.add_argument("args", nargs=argparse.REMAINDER)
    sr.set_defaults(fn=cmd_asr)
    tk = sub.add_parser("tasks", help="定时任务：list · show · enable · disable · run [--dry-run] / scheduled tasks")
    tk.add_argument("mode", choices=["list", "show", "enable", "disable", "run"])
    tk.add_argument("id", nargs="?")
    tk.add_argument("--dry-run", action="store_true", help="run：只显示会怎么跑，不运行 / show the plan only")
    tk.add_argument("--json", action="store_true")
    tk.set_defaults(fn=cmd_tasks)
    wizard.add_parser(sub)
    from . import wfrun
    wfrun.add_parser(sub)       # P426 #181-5: agentj automation resume|status (multi-step automations)
    from . import keep_awake
    keep_awake.add_parser(sub)
    from . import browser
    browser.add_parser(sub)   # P114: agentj browser setup|status|check|login|sites|open|endpoint|enable|disable
    from . import first_run, google
    from . import local_files
    local_files.add_parser(sub)
    first_run.add_parser(sub)   # P115 / P92: agentj setup checklist
    google.add_parser(sub)      # P115 / P91: agentj google status|check|install|plan|on|off
    from . import mail
    mail.add_parser(sub)        # P170: agentj mail setup|allow|deny|password|test|check|on|off|status|presets
    from . import report
    report.add_parser(sub, cmd_report)   # bare `agentj report` = the status report to the account dashboard (as before);
                                         # P373: `agentj report templates|schema|example|validate|render|send|new-template|publish`
    from . import contacts
    contacts.add_parser(sub)    # P184: agentj contacts list|find|add|edit|rm|import|export|card
    from . import hook_service
    hook_service.add_parser(sub)   # P366: agentj hook create|list|show|update|rotate|revoke|test|logs|wait|triage|forward|presets
    from . import ask
    ask.add_parser(sub)         # P322: agentj ask — the Agent J question card under any harness
    from . import screenhelp
    screenhelp.add_parser(sub)  # P411: agentj qr push / agentj screen ask|wait|cancel — screen help on the phone
    from . import workflows
    workflows.add_parser(sub)
    from .core import enabled as _core_on
    if _core_on():
        from .core import cli as core_cli
        core_cli.add_parser(sub)    # P359: agentj order … · agentj core … (Agent J Harness, AGENTJ_CORE=1 only)   # P244 (§30): agentj workflows [--json] — what the phone's five pages read, read-only
    elevate.add_parser(sub)   # F17: agentj sudo · agentj secret request
    from . import term_cli
    term_cli.add_parser(sub)  # P408: agentj terminal status|enable|allow|deny|disable|forget|logs (the web terminal)
    from . import opencode_provider
    opencode_provider.add_parser(sub)   # P60 / F25: agentj provider add|list|remove (OpenCode base_url + key)
    elevate_helper.add_parser(sub)   # F17: agentj sudo-helper install · sync · uninstall · status
    docsrule.add_parser(sub)
    handover.add_parser(sub)
    from . import tg_cursor
    from . import wechat
    wechat.add_parser(sub)
    tg_cursor.add_parser(sub)   # B3 (P117): agentj telegram-cursor status · fence · import · export (ids only)
    plaza.add_parser(sub)
    support.add_parser(sub)   # F18: agentj support ask | report | thread | list
    from . import library
    library.add_parser(sub)   # P367: agentj data list | get | search | coverage [--source] (Agent J points)
    from . import field_tools
    field_tools.add_parser(sub)
    from . import skills_cli
    skills_cli.add_parser(sub)   # P426 #181: agentj skills health|archive|restore|suggest (skill health, save-as-skill offers)
    from . import capability
    capability.add_parser(sub)
    from . import goal as goal_cli
    goal_cli.add_parser(sub)     # P158: agentj goal propose|status|progress|done|stop   # 0.18 (ADR-A193): agentj capability list|sync|check|show|prepare|result|dispatch|digest|report-check
    from . import busy
    from . import heartbeat as heartbeat_mod
    heartbeat_mod.add_parser(sub)   # P156: agentj heartbeat status|on|off|set|now|prompt show|edit|set|reset
    from . import quota as quota_mod
    quota_mod.add_parser(sub)       # P254: agentj quota [--json] [--remind] · on · off · reset add|used
    from . import imessage as imessage_mod
    imessage_mod.add_parser(sub)    # P274: agentj imessage status|doctor|on|off|set|instructions|vip|mute|scan|backfill|open-fda
    busy.add_parser(sub)         # P150 (§22): agentj busy list|start|stop|run — the phone's 「在办事项」 dots
    from . import local_model
    local_model.add_parser(sub)
    recall.add_parser(sub)    # F22 (P57): agentj recall <keywords> [--days N] [--date D] — the main Agent finds an earlier conversation
    from . import peer_service
    from .bots.cli import add_parser as add_bots_parser
    add_bots_parser(sub)
    peer_service.add_parser(sub)   # P71 (§17.9): agentj friends … — through <state>/agentperm/friends.sock, also inside the fence
    from . import codex_perm
    codex_perm.add_parser(sub)     # F30 (P73): agentj codex-sandbox status|set|default|fix — always the top level of config.toml
    mg = sub.add_parser("migrate", help="改名后的状态目录搬迁：status 查看 · rollback 撤销 / the 0.10 state move: status · rollback",
                        description="0.9 的状态目录 ~/.local/state/agentjarvis-alpha 会自动搬到 ~/.local/state/agentj（旧路径留一个链接）。"
                                    "rollback 搬回去（serve 必须没在运行）/ the old state directory moves automatically; rollback moves it back")
    mg.add_argument("mode", nargs="?", choices=["status", "rollback"], default="status")
    mg.add_argument("--json", action="store_true")
    mg.set_defaults(fn=cmd_migrate)
    al = sub.add_parser("alias", help="短命令 aj（迁移过的Agent J 主机另有旧命令 jarvis）：status · install · remove（同名已存在就不装）/ "
                                      "the short command `aj` (+ `jarvis` on a migrated computer)",
                        description="只在 PATH 里没有任何 aj 时，在 agentj 旁边建一个链接 aj → agentj；remove 只删我们自己的链接 / "
                                    "`aj` → agentj, only when no other `aj` exists; remove deletes only our own link")
    al.add_argument("mode", nargs="?", choices=["status", "install", "remove"], default="status")
    al.add_argument("--json", action="store_true")
    al.set_defaults(fn=cmd_alias)
    ins = sub.add_parser("instances", help="本机的 Agent J 实例（同一系统用户多人用）：list 查看 · remove N 删除第 N 个（工作目录保留）/ "
                                             "the Agent J instances on this OS user: list · remove N (its folder stays)")   # P180 / P217
    ins.add_argument("action", nargs="?", choices=["list", "remove"], default="list")
    ins.add_argument("number", nargs="?", type=int)
    ins.add_argument("--json", action="store_true")
    ins.set_defaults(fn=cmd_instances)
    lb = sub.add_parser("label", help="本实例的本机备注（只在 agentj instances 里显示）/ this instance's local note "
                                       "(shown only by agentj instances)")   # P180: `agentj --instance N label <note>`
    lb.add_argument("text", nargs="*")
    lb.set_defaults(fn=cmd_label)
    ob = sub.add_parser("onboarding", help="首次使用进度：席位、这台Agent J 主机的浏览器、主力手机、欢迎消息 / first-use progress: seat, "
                                           "the Agent J host's browser, main phone, welcome")
    ob.add_argument("--json", action="store_true")
    ob.set_defaults(fn=cmd_onboarding)
    a = p.parse_args(argv)
    from . import host_diagnostics
    host_owned = a.cmd in ("diag", "status", "support", "feedback") or (a.cmd == "doctor" and a.reconnect)
    host_owned = host_owned and host_diagnostics.agent_side()
    if not host_owned and a.cmd not in NO_MIGRATE and not (a.cmd == "report" and getattr(a, "action", None)) and a.cmd not in ("keep-awake", "browser") and not (a.cmd == "doctor" and (a.isolation_only or a.read_only)):
        from . import migrate
        try:
            # P127: `serve` without a terminal is the owner's own service restarting (auto-update / reboot): a 0.9 install
            # that never ran a newer command keeps its identity and phones instead of stopping with exit 2 (offline phone).
            # Commands a person or the install assistant runs still ask / stop, so a leftover is never adopted silently.
            if legacy is None and a.cmd == "serve" and not migrate._interactive():
                legacy = "migrate"
            r = migrate.auto(decision=legacy)
            if r == "moved":
                _alias_auto(sys.stderr, with_compat=True)
            elif r == "undecided":
                sys.exit(2)
        except OSError as e:      # never block a command: the old directory simply stays in use
            print(f"Agent J：状态目录检查失败（{type(e).__name__}），照常继续 / state check failed, continuing", file=sys.stderr)
    if a.cmd is None:
        cmd_hint()
        return
    a.fn(a)


if __name__ == "__main__":
    main()
