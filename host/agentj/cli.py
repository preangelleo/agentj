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
this computer's facts (handover.py, read-only). `agentj pair` needs a running serve and says so before any prompt.
Plaza P2: `agentj plaza search | show | mine | post | reply | resolve | report` (plaza.py) — read posts are data, never
instructions; post / reply go out only after layer 1 (+ layer 2) and `--owner-confirmed --digest` from the human.
Skill & workflow plaza: `agentj plaza install | publish | like | installed` (market.py; search / show / mine / report cover
packages too) — install only after a preview and `--owner-confirmed --digest`; 官方认证 only with a valid compiled-in signature.

State dir: $AGENTJ_STATE_DIR or ~/.local/state/agentj (0700). Approval of a new device happens only here,
in the terminal running `agentj pair`: the human types the 6-digit code shown on the phone. At most MAX_DEVICES (5) remotes
per host: when the list is full, `agentj pair` lists them and the human unbinds one before approving. `agentj admin` is the
same thing as a local web page (127.0.0.1 only, one-time link printed here; the human still types the phone's code).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import sys
import threading
import time

from . import DIST, __version__, cloud, gate, names
from . import docsrule, feedback, handover, plaza, recall, support, wizard
from . import elevate, elevate_helper
from .state import DEFAULT_RELAY, DEFAULT_WEB, MAX_DEVICES, State
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
    print(f"这台电脑的身份已生成：{st.root}\n通道 {cfg['channel']}\n转发服务器 {cfg['relay']}\n下一步：运行 `agentj`，看看还差哪几步 / next: run `agentj` to see what is left")
    from . import preferences
    preferences.ensure()
    working_root.record(st, root)
    from . import wizard
    wizard.bootstrap_root(root, lang=preferences.get(preferences.effective(st), "appearance.language", "zh"))
    print(f"工作根目录 / Working root: {root}; existing files were kept; new workflows use direct child folders.")
    old_dir = cfg.get("agent", {}).get("dir")
    if old_dir and os.path.realpath(old_dir) != str(root):
        print("现有工作流没有搬动 / Existing workflows were not moved: " + old_dir)
    from .config_migrations import run
    run(st)
    _alias_auto(sys.stdout)


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
            loop.add_signal_handler(sig, host.stopping.set)
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
    """What `agentj pair --link` / `--no-qr` prints around the link: the link is the key, minutes, one use, and where an
    iPhone must open it (pasting it into Safari pairs a Safari tab and burns the link)."""
    return (f"\n把这个链接发到手机上打开（{mins} 分钟内有效，只能用一次；它就是配对密钥，别发给别人）：\n"
            f"Open this link on the phone (valid {mins} min, works once; it is the pairing key, never send it to anyone else):\n"
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


SERVE_NOT_RUNNING = ("✗ Agent J 现在没在这台电脑上运行，所以没法配对手机。先运行 `agentj service install` 让它一直在后台运行"
                     "（想看它在不在跑：`agentj service status`），然后再运行一次 `agentj pair`。/ Agent J is not running on this "
                     "computer, so a phone cannot be paired. Run `agentj service install` to keep it running in the background "
                     "(check with `agentj service status`), then run `agentj pair` again.")
SERVE_BUSY = ("✗ Agent J 在运行，但没有响应。运行 `agentj service status` 看看，或者重启它（`agentj service install`），再配对。"
              "/ Agent J is running but not answering: check `agentj service status` or restart it with `agentj service install`, "
              "then pair again.")


def _serve_or_exit(st: State) -> None:
    """S-10: pairing goes through the running serve. Fail fast, before any prompt, with what to do."""
    try:
        up = names.ctl_call(st, {"cmd": "status"}, 5)
    except names.ServeBusy:
        sys.exit(SERVE_BUSY)
    if up is None:
        sys.exit(SERVE_NOT_RUNNING)


def cmd_pair(a) -> None:
    st = State()
    _need_init(st)
    _serve_or_exit(st)
    if not gate.is_set(st):
        if not sys.stdin.isatty():
            sys.exit("✗ " + gate.MESSAGES["not_set"])
        print("配对前先设置批准口令（每次批准新遥控器都要输入，只在这台电脑上）。", flush=True)
        _set_passphrase_interactive(st, change=False)

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
            _print_qr(first["link"])
        mins = max(1, (first['expires'] - int(time.time())) // 60)
        if a.no_qr or a.link:  # the link IS the pairing key: only print it when asked (terminal scrollback keeps it)
            print(link_text(first["link"], mins), flush=True)
        else:
            print(f"\n用手机相机扫上面的二维码（{mins} 分钟内有效，只能用一次）。扫不了就加 --link 重新运行。\n", flush=True)
            if _remote_session():
                print("（SSH 登录的服务器：手机扫不到终端里的码时，Ctrl-C 后运行 `agentj pair --link`，把打印出的链接用你自己的方式"
                      "发到手机上打开——同一个链接，它就是配对密钥，别发给别人。/ Over SSH: if the phone cannot scan it, run "
                      "`agentj pair --link` and open the same link on the phone.）\n", flush=True)
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
                print("安全码只显示在手机屏幕上——这个终端永远不会显示它。", flush=True)
                code = await race("看着手机，输入手机上的 6 位安全码并回车（120 秒内；直接回车 = 拒绝）：")
                if code is None:
                    ev = await take()
                    continue
                if not code.strip():
                    await send({"cmd": "code", "code": ""})
                    ev = await take()
                    continue
                while True:
                    ask, f = _secret_async("批准口令："), next_line()
                    done, _ = await asyncio.wait({ask, f}, return_when=asyncio.FIRST_COMPLETED)
                    if f in done:
                        print()
                        break
                    await send({"cmd": "code", "code": code, "pass": ask.result()})
                    ev = await take()
                    if ev.get("ev") != "pass_wrong":
                        break
                    print(f"✗ 批准口令不对（再错 {ev.get('left', 0)} 次会锁定）。设备还在等，请再输一次。", flush=True)
                if f in done:
                    ev = await take()
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
            reasons = {"code_mismatch": "安全码不一致，已拒绝", "denied": "已拒绝", "timeout": "120 秒未确认，已拒绝",
                       "abandoned": "已取消", "bad_handshake": "握手失败", "hs_timeout": "手机没有完成握手，已拒绝",
                       "device_limit": f"已达 {MAX_DEVICES} 台上限，没有批准：先解绑一台（`agentj revoke <设备>`）再配对",
                       "pass_locked": gate.MESSAGES["locked"], "pass_not_set": gate.MESSAGES["not_set"]}
            msg = {"expired": "二维码已过期（5 分钟），请重新运行 agentj pair", "gone": "手机断开了",
                   "replaced": "已被新的 agentj pair 取代"}.get(kind, reasons.get(ev.get("reason"), str(ev)))
            sys.exit(f"✗ {msg}")

    asyncio.run(main())


def _dev_line(d: dict) -> str:
    """「<名称> 配对于 <时间>」 for one remote in a serve event (names are device-chosen: one cleaned line)."""
    pa = d.get("paired_at")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(pa if isinstance(pa, (int, float)) and not isinstance(pa, bool) else 0))
    return f"{clean_label(str(d.get('name') or '')) or '（没有名字）'} 配对于 {when}" + ("（在线）" if d.get("online") is True else "")


def _ask_async(prompt: str) -> asyncio.Future:
    """input() on a daemon thread, so an unanswered prompt never blocks exit."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def run():
        try:
            v = input(prompt)
        except EOFError:
            v = ""
        loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(v))

    threading.Thread(target=run, daemon=True).start()
    return fut


def _secret(prompt: str) -> str:
    """The approval passphrase: never echoed on a terminal; a pipe (scripts, tests) gives one line."""
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

    threading.Thread(target=run, daemon=True).start()
    return fut


def _gate_msg(e: gate.GateError) -> str:
    if e.reason == "locked":
        return f"批准口令连续输错，锁定中（还要 {max(1, e.info.get('seconds', 60) // 60)} 分钟）。"
    if e.reason == "wrong":
        return f"批准口令不对（再错 {e.info.get('left', 0)} 次会锁定）。"
    return gate.MESSAGES.get(e.reason, e.reason)


def _set_passphrase_interactive(st: State, change: bool) -> None:
    old = _secret("现在的批准口令：") if change else None
    print("批准口令：以后每次批准一台新的遥控器都要输入它。只有你知道——别告诉 Agent，也别让 Agent 替你设。", flush=True)
    new = _secret(f"新的批准口令（至少 {gate.MIN_LEN} 个字符）：")
    if _secret("再输一次：") != new:
        sys.exit("✗ " + gate.MESSAGES["mismatch"])
    try:
        gate.set_passphrase(st, new, old)
    except gate.GateError as e:
        sys.exit("✗ " + _gate_msg(e))
    print("✓ 批准口令已保存。/ approval passphrase saved.")


def cmd_passphrase(a) -> None:
    st = State()
    _need_init(st)
    if a.mode == "set":
        if gate.is_set(st):
            sys.exit(gate.MESSAGES["exists"])
        _set_passphrase_interactive(st, change=False)
    elif a.mode == "change":
        if not gate.is_set(st):
            sys.exit(gate.MESSAGES["not_set"])
        _set_passphrase_interactive(st, change=True)
    elif a.mode == "reset":
        n = len(st.devices())
        print(f"忘了批准口令：重置会删除它，并吊销本机全部 {n} 台遥控器（之后用新口令重新配对）。", flush=True)
        if input("确认重置？输入 RESET 回车：").strip() != "RESET":
            sys.exit("没有重置。")
        for did in list(st.devices()):
            try:
                res = names.ctl_call(st, {"cmd": "revoke", "device": did}, 10)
            except names.ServeBusy:
                sys.exit("agentj serve 在运行但没有响应，没有重置")
            if res is None:
                st.remove_device(did)
        gate.reset(st)
        print("✓ 已重置，所有手机都已解除配对。运行 `agentj pair` 重新配对：它会先请你设一个新的批准口令。/ Reset: every "
              "phone is unpaired. Run `agentj pair` to pair again; it asks you to choose a new approval passphrase first.")
    else:
        left = gate.lock_left(st)
        print(("已设置" + (f"（锁定中，还要 {max(1, left // 60)} 分钟）" if left else "")) if gate.is_set(st)
              else "还没设置：运行 `agentj passphrase set`")


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
        where = "账号后台和这台电脑都改好了" if res["linked"] else "这台电脑没加到 Agent J 账号里，只改了这台电脑上的"
        print(f"✓ Agent 名改为「{res['name']}」（{where}）")
        return
    msg = names.RENAME_MESSAGES.get(res["error"], "名字没改")
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
    return (f"你是用 SSH 登录的：管理页只在这台服务器的 127.0.0.1 上。在你自己的电脑上另开一个终端运行\n"
            f"  ssh -N -L {port}:127.0.0.1:{port} {getpass.getuser()}@{host}\n"
            f"再在那台电脑的浏览器里打开上面的链接。/ Over SSH: run the line above on your own computer, then open the link there.")


def cmd_admin(a) -> None:
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
    devs = st.devices()
    status = _ctl_quiet(st, {"cmd": "status"}) or {}
    online = {s["device"] for s in status.get("sessions", []) if s.get("state") == "ready"}
    if a.json:
        print(json.dumps({d: {**v, "online": d in online} for d, v in devs.items()}, ensure_ascii=False, indent=1))
        return
    print(f"Agent：{_name_or_unset(st)}")
    print(_link_line(st))
    print(f"遥控器 {len(devs)}/{MAX_DEVICES}" + ("（已满：再批准新设备会自动解绑最久没用的那台；想自己挑就先 `agentj revoke <设备>`）" if len(devs) >= MAX_DEVICES else ""))
    if not devs:
        print("还没有已批准的设备。用 `agentj pair` 配对。")
    for did, v in devs.items():
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(v.get("paired_at", 0)))
        fid = "  Face ID ✓" if isinstance(v.get("pk"), dict) else ""   # F20: this phone can reconnect with its passkey
        print(f"{did}  {'在线' if did in online else '离线'}  {v.get('name', '')}  配对于 {when}{fid}")


def cmd_revoke(a) -> None:
    st = State()
    _need_init(st)
    try:
        res = names.ctl_call(st, {"cmd": "revoke", "device": a.device}, 10)
    except names.ServeBusy:
        sys.exit("agentj serve 在运行但没有响应：没有吊销（不会绕过它直接改准许名单）。稍后再试，或先停掉 serve。")
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
    if not res.get("ok"):
        sys.exit("太长了（上限 4000 字），没有发送" if res.get("error") == "too_long" else f"发送失败：{res}")
    print(f"已发给 {res.get('delivered', 0)} 台设备")


def cmd_status(a) -> None:
    st = State()
    _need_init(st)
    try:
        res = names.ctl_call(st, {"cmd": "status"}, 10)
    except names.ServeBusy:
        sys.exit("agentj serve 在运行但没有响应")
    if res is None:
        print(f"Agent J 现在没在运行（要让它一直在后台运行：`agentj service install`）· 状态目录 {st.root} · 通道 {st.config()['channel']}"
              " / Agent J is not running (keep it running: `agentj service install`)")
        print(f"Agent：{_name_or_unset(st)}")
        print(_link_line(st))
        print(f"批准口令：{'已设置' if gate.is_set(st) else '未设置（`agentj passphrase set`）'}")
        print(_estop_line(st))
        return
    link = cloud.read_cloud(st)
    res["dashboard"] = link["tenant"]["slug"] if link else "没加到 Agent J 账号"
    res["linked_via"] = link["via"] if link else None   # "seat" (seat setup code) | "code" (8-character agentj login)
    res["agent_name"] = st.agent_name()
    res["passphrase"] = gate.is_set(st)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def _link_line(st: State) -> str:
    link = cloud.read_cloud(st)
    if not link:
        return "Agent J 账号：还没加入（`agentj login`）/ not in an Agent J account yet"
    via = "用设置码加入 / joined with a setup code" if link["via"] == "seat" else "用 8 位代码加入 / joined with the 8-character code"
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
SEAT_EXIT = {"name_taken": 3, "invalid_setup": 4, "payment_required": 5, "bad_code": 2, "bad_name": 2, "name_required": 2}


def _read_seat_code(a) -> str:
    """The setup code from --seat <code>, --seat - (one line on stdin) or --seat-file <path> (a regular file the human /
    agent wrote with mode 0600; refused when group / others may read it). Never printed, never logged."""
    if a.seat_file:
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
    return text.strip()


def _seat_login(st: State, a) -> None:
    """`agentj login --seat …` (seat setup §4.1): no y/N — the human gave the code to this computer's Agent, that is the
    decision. Prints the company it joined (the human must see it); never the code."""
    if a.name is None:
        print("✗ 用设置码绑定要同时给 Agent 起名：--name \"<名字>\" / --seat needs --name \"<name>\"", file=sys.stderr)
        sys.exit(2)
    code = _read_seat_code(a)
    try:
        api = cloud.api_url(st, override=a.api)
    except cloud.CloudError:
        sys.exit("拒绝：API 地址必须是 https://（http:// 只允许 127.0.0.1 / localhost）")
    try:
        res = cloud.seat_bind(st, api, code, a.name)
    except cloud.CloudError as e:
        sys.exit(f"连不上 Agent J 服务器（{e.kind}）：{api} / cannot reach the Agent J server")
    s = res["status"]
    if s == "bound":
        t, name = res["tenant"], res["agent_name"]
        st.set_agent_name(name)
        st.log("agent_name_set", kind="seat")
        _ctl_quiet(st, {"cmd": "agent_name_changed"})
        print(f"✓ 已加到 Agent J 账号 {t['slug']}（{t['name']}）的一个席位，Agent 名「{name}」")
        print(f"✓ Added to a seat of the Agent J account {t['slug']} ({t['name']}); Agent name \"{name}\". "
              "Not the account you expected? Run `agentj unlink`: it also takes this computer out of that account.")
        r = _report_now(st)
        print("首次上报：成功 / first report: ok" if r.kind == "ok"
              else f"首次上报失败（{r.status}），serve 启动后会自动重试 / first report failed, serve retries")
        return
    msgs = {
        "bad_code": "设置码格式不对（应是 ajt_ 加 43 个字符）：从那句话里原样复制 / not a setup code (ajt_ + 43 characters): copy it exactly",
        "bad_name": "Agent 名不合规（1–32 个字，不能有控制字符）/ bad Agent name (1–32 characters, no control characters)",
        "name_required": "Agent 名不能为空 / the Agent name is required",
        "name_taken": "这个账号里已经有叫这个名字的 Agent 了，换一个 / this account already has an Agent with that name",
        "invalid_setup": "设置码无效、已用过、已过期或已被作废——请向管理员要一个新的 / "
                         "the setup code is invalid, used, expired or revoked: ask the account owner for a new one",
        "payment_required": "这个席位没在付费了，请找账号的管理员 / this seat is no longer paid: contact the account owner",
        "already_bound": "账号后台里这台电脑还挂在别的地方：先请账号的管理员在账号后台把它移除 / "
                         "this computer is still listed in an account dashboard: the owner removes it there first",
        "rate_limited": "尝试太频繁，过一小时再试 / too many attempts: try again within the hour",
    }
    msg = "✗ " + msgs.get(s, f"绑定失败 / bind failed（{res.get('http')}{' ' + res['error'] if res.get('error') else ''}）")
    if s == "name_taken" and res.get("suggestions"):
        msg += "\n  可以试试 / try: " + "、".join(f"「{x}」" for x in res["suggestions"])
    print(msg, file=sys.stderr)
    sys.exit(SEAT_EXIT.get(s, 1))


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
    if acct and (a.seat is not None or a.seat_file):
        _refuse("--yes / --account 只用于 8 位代码的登录，设置码登录不需要 / --yes and --account are for the 8-character code "
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
        sys.exit(f"这台电脑已经加到 Agent J 账号 {link['tenant']['slug']} 里了。要换账号，先运行 `agentj unlink`（再到账号后台把这台电脑移除）。"
                 f" / This computer is already in the Agent J account {link['tenant']['slug']}: run `agentj unlink` first.")
    if a.seat is not None or a.seat_file:
        _seat_login(st, a)
        return
    if a.name is not None:
        sys.exit("--name 只和 --seat / --seat-file 一起用（用 8 位代码的话，名字在账号后台里起）/ --name goes with --seat only")
    try:
        api = cloud.api_url(st, override=a.api)
        app = cloud.app_url(st)
    except cloud.CloudError:
        sys.exit("拒绝：服务器地址必须是 https://（http:// 只允许 127.0.0.1 / localhost）/ refused: the server address must be https://")

    def show(lg: dict) -> None:
        mins = max(1, lg["expires_in"] // 60)
        print(f"这台电脑的通道号：{cloud.channel_of(st)}（账号后台里显示的应该一样）", flush=True)
        print(_hostname_notice(st), flush=True)
        # F7: the configured Dashboard; the server's verification_uri only when it is on that same origin
        print(f"在已登录的账号后台里打开 {cloud.dashboard_uri(app, lg['verification_uri'])}", flush=True)
        print(f"输入这个代码：{lg['user_code']}（{mins} 分钟内有效，只能用一次）", flush=True)
        print("看一下账号后台显示的通道号和上面一样，再点添加。等待中……（Ctrl-C 取消）", flush=True)

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
            print(f"这台电脑的 Agent 名：「{res['agent_name']}」（在账号后台或用 `agentj name` 可以改）")
        r = _report_now(st)
        print("首次上报：成功" if r.kind == "ok" else f"首次上报失败（{r.status}），serve 启动后会自动重试")
        return
    if st_ == "declined" and mismatch:
        undone = ("已通知账号后台撤销这次添加 / the account dashboard was told to undo it" if res.get("undone") == "undone"
                  else f"没能通知账号后台撤销（{res.get('undone')}），请到账号后台把这台电脑移除 / could not tell the account "
                       "dashboard to undo it: remove this computer there")
        _refuse(f"没有加入：账号后台报回来的账号是 {mismatch[0]}，和 --account {acct} 不一样。这台电脑什么都没写；{undone}。"
                f"请和主人核对账号 ID。/ Not added: the account dashboard reported the account {mismatch[0]}, not {acct}. "
                "Nothing was written on this computer. Check the account ID with your human.")
    if st_ == "declined":
        t = res["tenant"]
        if res.get("undone") == "undone":
            sys.exit(f"✗ 没有加入：这台电脑什么都没写，也已经通知账号后台撤销这次添加（账号 {t['slug']} 里不会有这台电脑）。\n"
                     "  如果这不是你自己输入的代码，说明有人看到了这个终端上的代码——别再让别人看到。")
        sys.exit(f"✗ 没有加入：这台电脑什么都没写。但没能通知账号后台撤销（{res.get('undone')}），"
                 f"那边可能还把这台电脑记在账号 {t['slug']}（{t['name']}）下：\n"
                 f"  如果这是你的账号，到账号后台把这台电脑移除；如果不是你的，说明有人用了这个终端上显示的代码。"
                 f"别再让别人看到代码，并请对方（或我们）在账号后台把这台电脑移除。")
    msgs = {"already_bound": "账号后台里这台电脑还挂在别的地方：先在账号后台把它移除，再运行 `agentj login`",
            "rate_limited": "登录请求太频繁，稍后再试", "rejected": "账号后台没有接受这次添加",
            "expired": "代码已过期（10 分钟），请重新运行 `agentj login`"}
    sys.exit("✗ " + msgs.get(st_, f"登录失败（{res.get('http')}{' ' + res['error'] if res.get('error') else ''}）"))


def _hostname_notice(st: State) -> str:
    from .text import machine_name
    m = machine_name()
    if st.report_machine() and m:
        return f"加入后，账号后台会显示这台电脑的名字「{m}」（「运行在 … 上」）；不想显示：`agentj report-hostname off`"
    return "账号后台不显示这台电脑的名字（要显示：`agentj report-hostname on`）"


def cmd_report_hostname(a) -> None:
    st = State()
    _need_init(st)
    if a.mode in ("on", "off"):
        st.set_report_machine(a.mode == "on")
        st.log("report_machine_switch", status=a.mode)
        if cloud.read_cloud(st):
            _ctl_quiet(st, {"cmd": "agent_name_changed"})   # = "send a report soon", so the Dashboard sees the change
    print(_hostname_notice(st) if st.report_machine() else "关：账号后台不显示这台电脑的名字。")


def cmd_report(a) -> None:
    st = State()
    _need_init(st)
    link = cloud.read_cloud(st)
    if not link:
        sys.exit("这台电脑还没加到 Agent J 账号：先运行 `agentj login`")
    res = _report_now(st)
    if res.kind == "ok":
        print(f"已上报到 Agent J 账号 {link['tenant']['slug']}（seq {res.seq}）")
    elif res.kind == "unbound":
        sys.exit("账号后台那边已经把这台电脑移除了。这台电脑上的记录还在：`agentj unlink` 清掉后，可以重新 `agentj login`")
    elif res.kind == "unlinked":
        sys.exit("这台电脑还没加到 Agent J 账号：先运行 `agentj login`")
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
            print(f"已经把这台电脑移出账号 {slug} / This computer was taken out of the account {slug}.")
        elif r["status"] == "not_found":
            print(f"这台电脑已经不在账号 {slug} 的席位里了 / This computer is no longer in a seat of the account {slug}.")
        else:
            why = {"rate_limited": "太频繁，稍后再试 / rate limited"}.get(r["status"], f"连不上或出错（{r.get('http', '')}）")
            print(f"没能通知账号后台把这台电脑移出账号 {slug}：{why}。请账号的管理员在账号后台收回这个席位。"
                  f" / Could not reach the account dashboard; ask the account owner to take the seat back there.")
    removed = cloud.delete_cloud(st)
    if not removed:
        print("这台电脑没加到 Agent J 账号里。")
        return
    st.log("cloud_unlinked")
    if left:
        print(f"已删掉这台电脑上的账号记录（账号 {slug}）。")
    else:
        print(f"已删掉这台电脑上的账号记录（账号 {slug}）。账号后台那边，还要在账号后台里把这台电脑移除。")


AGENT_LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


def _agent_line(st: State) -> str:
    c = st.agent_config()
    if not c:
        return "接的 Agent：无（手机消息只显示在这个终端；`agentj agent claude --dir <目录>` 接上 Claude Code）"
    extra = "；需要你点头的环节，它会在手机上弹窗问你" if c["kind"] in AGENT_LABEL else ""
    fz = " · 隔离运行" if c.get("fence", True) else " · ⚠ 不隔离运行（--unfenced）"
    if c.get("fence", True) and c.get("docker"):
        fz += " · ⚠ 允许用 docker（--allow-docker）"
    return (f"接的 Agent：{AGENT_LABEL[c['kind']]} · 目录 {c['dir']}" + (f" · 模型 {c['model']}" if c["model"] else "")
            + fz + extra)


def cmd_agent(a) -> None:
    if a.mode == "detect":   # works before `agentj init`: only looks at PATH and whether login files exist
        from . import harness
        sys.exit(harness.main(as_json=a.json))
    st = State()
    _need_init(st)
    if a.mode in ("claude", "codex", "opencode"):
        if a.mode == "opencode" and a.model and not re.fullmatch(r"[A-Za-z0-9._-]+/[^\s/][^\s]*", a.model):
            sys.exit("✗ OpenCode 的模型写成「服务商/模型」，例如 zhipuai/glm-5.3 或 deepseek/deepseek-flash（`opencode models` 列出可用的）")
        if a.unfenced:   # F14: the owner's (or, at the owner's request, the Agent's) switch — no passphrase
            print("⚠ 不隔离运行：Agent 按底层 harness 自己的权限运行，能看到 Agent J 的状态目录。随时可以去掉 --unfenced 改回来。", flush=True)
        elif a.allow_docker:
            print("⚠ 允许 Agent 用 docker / podman：容器能挂载这台电脑上的任何文件（包括 Agent J 的状态目录）。", flush=True)
        try:
            from . import working_root
            root = working_root.select(st, a.dir)
            st.set_agent_config(a.mode, str(root), a.model, fence=not a.unfenced, docker=bool(a.allow_docker and not a.unfenced))
            from . import wizard
            from . import preferences
            wizard.bootstrap_root(root, lang=preferences.get(preferences.effective(st), "appearance.language", "zh"))
        except ValueError as e:
            sys.exit({"bad_dir": "目录不存在",
                      "protected_dir": "这个目录是 Agent J 自己的（状态目录或程序目录），不能给 Agent 用：换一个工作目录"}.get(str(e), str(e)))
        st.log("agent_config", agent=a.mode, fence=not a.unfenced, change="docker" if a.allow_docker else None)
        if not a.unfenced:
            from . import fence
            why = fence.problem(st, st.agent_config()["dir"])
            if why:
                print(f"⚠ {fence.REASONS.get(why, why)}：serve 会按普通模式运行 Agent（不隔离，底层 harness 的权限照常）。", flush=True)
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
            sys.exit("✗ 共享会话用的是你电脑上原来的进程，Agent J 不替你重启：请在电脑上自己重启它。"
                     " / A shared session is your own process: restart it yourself.")
        return
    elif a.mode == "reset":
        for k in st.AGENT_KINDS:
            st.set_agent_session(k, None)
        print("已忘掉之前的对话：下一条手机消息开一段新对话。")
    print(_agent_line(st))
    if a.mode in ("claude", "codex", "opencode", "off"):
        print("重启 `agentj serve` 后生效。Agent 以你自己的身份、登录和权限设置运行，本程序不会给它更多权限。")


def cmd_approvals(a) -> None:
    from . import approvals
    st = State()
    _need_init(st)
    every = approvals.read_log(st)
    recs = every[-a.last:] if a.last else every
    index = {str(r.get("id")): r for r in every if r.get("decision") == "allow_batch"}
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
                                                                       "policy": "超出 Agent 自己的沙箱，自动拒绝"}.get(r.get("reason"), r.get("reason"))
            if r.get("reason") == "batch":
                who = f"按手机 {r.get('device')} 的批量授权自动批准（{str(r.get('grant'))[:8]}…）"
            mark = {"ok": " ✓签名有效", "ok_removed": " ✓签名有效（设备已移除）", "bad": " ✗签名无效", "unsigned": "",
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


def cmd_resume(a) -> None:
    from . import activity, controls
    st = State()
    _need_init(st)
    if not controls.estop_state(st)["on"]:
        print("没有急停，不用恢复。")
        return
    # F14: a stop is the owner's emergency switch — one tap on a paired phone lifts it (signed, no passphrase). At the
    # terminal the owner proves presence with the passphrase or, without one, a keyboard: the Agent cannot lift its own stop.
    if gate.is_set(st):
        try:
            gate.verify(st, _secret("输入批准口令恢复（或在已配对手机上点「恢复」）："))
        except gate.GateError as e:
            sys.exit("✗ " + _gate_msg(e))
    elif not sys.stdin.isatty():
        sys.exit("✗ 急停只能由主人解除：在已配对手机上点「恢复」，或主人在自己的终端运行 `agentj resume`。")
    try:
        res = names.ctl_call(st, {"cmd": "resume"}, 15)
    except names.ServeBusy:
        sys.exit("agentj serve 在运行但没有响应：没有恢复")
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
                f"\n已有的记录没有删：{n} 个文件（{b / 1024 / 1024:.1f} MiB，{h.dir}）还在这台电脑上；"
                f"`agentj history clear --all` 立即全部删除（含归档，不能撤销） / existing history is NOT deleted: {n} "
                f"file(s) remain; `agentj history clear --all` deletes them (archives too, cannot be undone)")
        return ("关：新的聊天记录只在内存里（最近 100 页），重启就没了 / history off: the newest 100 pages in memory only"
                + left)
    return (f"开：聊天记录存在这台电脑上（{h.dir}，0600，最近 {history.KEEP} 页，归档 {len(h.archives())} 份）· "
            f"共 {h.meta()['count']} 页 / history on: kept on this computer")


def cmd_history(a) -> None:
    """`agentj history [show <id>|clear|off|on]` (PROTOCOL §10.5): the pages the phone shows, on this computer only."""
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
        print(f"已删除这台电脑上的全部聊天记录（{res.get('deleted', 0)} 个文件，含归档）/ deleted all history on this "
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
    if a.mode == "show":
        try:
            t = h.get(int(a.id))
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
    args=list(a.args or [])
    if len(args)==2 and args[0]=='engine':
        from . import preferences
        selected={'auto':'sensevoice','sherpa':'sensevoice','voxtype':'voxtype','off':'off'}.get(args[1])
        if selected:sys.exit(preferences.command(['set','voice.asr.engine',selected]))
    sys.exit(asr.cli_main(args, state_dir=st.root))


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
        print(("开：本机记录操作与审批（activity.log，只在这台电脑上，30 天，最多 20 MiB）；手机的「记录」页读它。"
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


def cmd_remote_unbind(a) -> None:
    from .serve import REMOTE_UNBIND_PER_HOUR
    st = State()
    _need_init(st)
    if a.mode in ("on", "off"):
        st.set_remote_unbind(a.mode == "on")
        st.log("remote_unbind_switch", status=a.mode)
    on = st.remote_unbind()
    print(("开：账号的管理员可以在账号后台解绑这台电脑的手机遥控器（每小时最多 "
           f"{REMOTE_UNBIND_PER_HOUR} 次）。只能解绑，不能添加。") if on else
          "关：账号后台发来的解绑请求，这台电脑一律拒绝；要解绑就在这里运行 `agentj revoke`。")
    print("提醒：配对用的二维码和链接只会由这台电脑上的 `agentj pair` 显示。账号后台、邮件或客服发给你的二维码，一律别扫。")


def cmd_onboarding(a) -> None:
    """F28 (P72): what the installing Agent checks after each step — seat, the two required remotes, the welcome."""
    st = State()
    if not st.exists():
        sys.exit("先运行 `agentj init` / run `agentj init` first")
    from . import onboarding
    sys.exit(onboarding.command(a, st))


def cmd_doctor(a) -> None:
    from . import doctor
    sys.exit(doctor.main(as_json=a.json, offline=a.offline, isolation_only=a.isolation_only))


def cmd_service(a) -> None:
    from . import service
    try:
        if a.mode == "status":
            s = service.status()
            if a.json:
                print(json.dumps({**s, "path": service.tilde(s["path"]) if s.get("path") else None}, ensure_ascii=False))
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
            migrate.auto()
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
        r = service.install(st)
    except service.ServiceError as e:
        sys.exit("✗ " + service.MESSAGES.get(e.reason, e.reason) + (f"\n  {e.detail}" if e.detail else ""))
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
    res = update.apply(State(), a.version, check_fn=lambda: r, say=print)
    print(update.result_block(res))
    raise SystemExit(res["exit"])


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
    print(update.result_block(res), flush=True)
    sys.exit(res["exit"])


def _update_line(r: dict) -> str:
    from . import update
    s = r["status"]
    if s == "newer":
        return f"! newer · 有新版本 {r['latest']}（这台电脑是 {r['current']}）/ newer version {r['latest']} (this computer: {r['current']})"
    if s == "current":
        return f"✓ current · 已是最新 / up to date: {r['current']}"
    if s == "ahead":
        return (f"✓ ahead · 这台电脑是 {r['current']}，公开仓库最新发布的是 {r['latest']} / this computer runs {r['current']}, "
                f"the newest public release is {r['latest']}")
    why = update.WHY.get(r["why"], r["why"])
    return f"? unknown · 没查到最新版本（{why}）；这台电脑是 {r['current']} / could not check ({why}); this computer: {r['current']}"


NEXT_STEPS = (   # (command, 中文, English)
    ("agentj init", "生成本机身份", "create this host's identity"),
    ("agentj login", "把这台电脑加到你的 Agent J 账号（有设置码：--seat-file）",
     "add this computer to your Agent J account (setup code: --seat-file)"),
    ("agentj passphrase set", "设批准口令（自己输，别让 Agent 代劳）", "set the approval passphrase (yourself)"),
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
    return [True, cloud.read_cloud(st) is not None, gate.is_set(st), st.agent_config() is not None,
            bool(service.status().get("installed")), bool(st.devices())]


def cmd_hint() -> None:
    st = State()
    done = _done_steps(st)
    print(f"{DIST} {__version__} — 用手机和你自己的 Claude Code / Codex 对话 · talk to your own Agent from your phone\n")
    for i, ((cmd, zh, en), ok) in enumerate(zip(NEXT_STEPS, done), 1):
        print(f"  {'✓' if ok else '·'} {i}. {cmd:<36} {zh} / {en}")
    nxt = next((c for (c, _, _), ok in zip(NEXT_STEPS, done) if not ok), None)
    print("\n" + (f"下一步 / next:  {nxt}" if nxt else "都设好了 / all set — 手机上打开 m.agentj.app 开始对话 / open m.agentj.app on your phone"))
    print("自检 / health check:  agentj doctor     ·     全部命令 / all commands:  agentj --help")


JARVIS_NOTICE = "`jarvis` 已改名为 `agentj`，旧命令下个版本移除 / `jarvis` is now `agentj`; the old name goes away in the next version"


def main_jarvis(argv=None) -> None:
    """`python -m jarvis_host.cli` (≤ 0.9 service units) and the checkout's host/jarvis: the notice, then the same CLI."""
    print(JARVIS_NOTICE, file=sys.stderr, flush=True)
    main(argv)


# `migrate status` reports, `migrate rollback` undoes, `docs-rule` prints (or, with --write and the human's y, appends to the
# AI's own memory file), `handover` only reads: none may move the state directory first
NO_MIGRATE = ("migrate", "docs-rule", "handover", "recall", "friends", "codex-sandbox")   # recall / friends / codex-sandbox: usually inside the fence


def main(argv=None) -> None:
    from .service import load_launch_binary_env
    load_launch_binary_env()
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:2] == ["provider", "profile"]:
        from . import provider_profiles
        raise SystemExit(provider_profiles.command(args[2:]))
    if args and args[0] == "installer":
        from . import installer
        installer.main(args[1:])
        return
    if args and args[0] in ("voice", "theme", "menu", "key", "channel", "skill"):
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
    sub = p.add_subparsers(dest="cmd")
    dc = sub.add_parser("doctor", help="自检：一项一行 ✓/!/✗ + 修法 / health check, one line per check",
                        description="自检 / health check: ✓ ok · ! warning · ✗ must fix. Exit 0 unless a ✗. Never prints secrets.")
    dc.add_argument("--json", action="store_true", help="机器可读 / machine-readable (paths shown with ~)")
    dc.add_argument("--offline", action="store_true", help="跳过网络检查 / skip the network checks")
    dc.add_argument("--isolation-only", action="store_true", help="仅隔离预检：不读用户状态、不联网 / isolation preflight only, no user state or network")
    dc.set_defaults(fn=cmd_doctor)
    sv = sub.add_parser("service", help="开机 / 登录后自动运行 serve：install · uninstall · status / run serve as a service",
                        description="Linux: systemd user unit · macOS: LaunchAgent. 不写任何密钥 / never writes a secret.")
    sv.add_argument("mode", choices=["install", "uninstall", "status", "restart"], help="install 安装并启动 · uninstall 停止并删除 · status 状态")
    sv.add_argument("--json", action="store_true", help="status 的机器可读输出 / machine-readable status")
    sv.set_defaults(fn=cmd_service)
    up = sub.add_parser("update", help="check 查版本 · apply Agent 自升级（无需终端）· auto on|off",
                        description="apply needs no y/N or terminal, restarts and runs doctor. F12 authorization remains optional.")
    up.add_argument("--yes", action="store_true", help="兼容脚本；默认已不询问 / compatibility; apply already needs no confirmation")
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
                                     "running: agentj service status)")
    pr.add_argument("--no-qr", action="store_true", help="不画二维码，改为打印配对链接")
    pr.add_argument("--link", action="store_true", help="二维码之外也打印配对链接（它就是配对密钥）")
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
    sub.add_parser("status", help="serve 的状态").set_defaults(fn=cmd_status)
    nm = sub.add_parser("name", help="查看 / 修改这台电脑上 Agent 的名字（加入了 Agent J 账号的话，先在账号后台改，改好了才改这台电脑上的）")
    nm.add_argument("name", nargs="?", help="新名字（1–32 个字）；不填 = 查看")
    nm.set_defaults(fn=cmd_name)
    ad = sub.add_parser("admin", help="打开本机的 Agent 管理页（只在 127.0.0.1；配对、遥控器、改名）")
    ad.add_argument("--port", type=int, default=0, help="端口（默认随机）。地址永远是 127.0.0.1，不能改")
    ad.add_argument("--events", choices=["text", "jsonl"], default="text",
                    help="jsonl：每个新链接打印一行 {\"ev\":\"admin\",\"url\",\"port\",\"expires_in\"}（脚本 / 测试用）。"
                         "注意 url 里的 #t= 就是登录密钥：别把 stdout 接到日志 / journal；后台运行请用 --url-file")
    ad.add_argument("--url-file", metavar="PATH",
                    help="把链接（同样的 jsonl 行）写进这个新建的文件（0600，必须还不存在），stdout 不再出现链接")
    ad.add_argument("--no-stdin", action="store_true", help="不读终端输入（后台运行时用）")
    ad.set_defaults(fn=cmd_admin)
    lo = sub.add_parser("login", help="把这台电脑加到你的 Agent J 账号（占 1 个席位）/ add this computer to your Agent J account (uses 1 seat)")
    lo.add_argument("--api", help=f"服务器地址（默认 $AGENTJ_API_URL 或 {cloud.DEFAULT_API}）")
    lo.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)   # only with --account; see _account_arg
    lo.add_argument("--account", metavar="ACCOUNT_ID",
                    help="你的 Agent J 账号 ID：账号后台报回来的账号不是它就拒绝，什么都不写（退出码 2）/ your Agent J account ID: "
                         "if the account dashboard reports another account, nothing is written (exit 2)")
    seat = lo.add_mutually_exclusive_group()
    seat.add_argument("--seat", metavar="CODE",
                      help="用账号后台给的设置码（ajt_…）直接加入，不用 8 位代码、不问 y/N；`-` = 从标准输入读一行 / "
                           "join with a setup code from the account dashboard (no 8-character code, no y/N); `-` reads stdin")
    seat.add_argument("--seat-file", metavar="PATH",
                      help="从文件读设置码（文件必须 0600），这样它不进命令行和 shell 历史 / "
                           "read the setup code from a 0600 file, so it stays out of argv and shell history")
    lo.add_argument("--name", help="和 --seat 一起：本机 Agent 的名字（1–32 个字）/ with --seat: the Agent's name (1–32 characters). "
                                   "退出码 / exit: 3 名字已占用 name taken · 4 设置码无效 invalid code · 5 席位未付费 seat not paid · "
                                   "2 本地拒绝 refused locally")
    lo.set_defaults(fn=cmd_login)
    sub.add_parser("report", help="马上向账号后台报一次这台电脑的状态 / report this computer's status to the account dashboard now").set_defaults(fn=cmd_report)
    sub.add_parser("unlink", help="把这台电脑移出 Agent J 账号（删掉这台电脑上的账号记录）/ take this computer out of the Agent J account").set_defaults(fn=cmd_unlink)
    rh = sub.add_parser("report-hostname", help="账号后台是否显示这台电脑的名字（默认显示）/ show this computer's name in the account dashboard")
    rh.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")
    rh.set_defaults(fn=cmd_report_hostname)
    ru = sub.add_parser("remote-unbind", help="允许 / 禁止在账号后台解绑这台电脑的手机遥控器（默认允许）/ allow unlinking phone remotes from the account dashboard")
    ru.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")
    ru.set_defaults(fn=cmd_remote_unbind)
    ag = sub.add_parser("agent", help="接哪个 Agent：claude / codex / opencode / off；reset = 开一段新对话（重启 serve 生效）；"
                                      "restart = 下一条消息前重启 Agent 进程、对话不变（换了 key 或代理后）；"
                                      "detect = 本机有哪些可用 / which agents are usable here")
    ag.add_argument("mode", nargs="?", choices=["claude", "codex", "opencode", "off", "reset", "restart", "status", "detect"], default="status",
                    help="detect = 本机有哪些可用（不需要 init；只看是否安装、登录文件是否存在）/ which agents are usable here "
                         "(no init needed; checks only what is installed and whether login files exist)")
    ag.add_argument("--json", action="store_true", help="detect 的机器可读输出 / machine-readable detect output")
    ag.add_argument("--dir", help="Agent 的工作目录（默认当前目录）")
    ag.add_argument("--model", help="模型（默认用你自己的设置）；OpenCode 写成 服务商/模型，例如 zhipuai/glm-5.3")
    ag.add_argument("--unfenced", action="store_true",
                    help="不隔离运行 Agent（按底层 harness 自己的权限）。默认 Agent 在 bubblewrap 里运行，看不到 Agent J 的状态")
    ag.add_argument("--allow-docker", action="store_true",
                    help="隔离里也让 Agent 用 docker / podman（默认不开；主人要求时可直接打开）。默认容器引擎的 socket 对 Agent 隐藏")
    ag.set_defaults(fn=cmd_agent)
    pp = sub.add_parser("passphrase", help="批准口令：set 设置 · change 修改 · reset 忘了（会吊销全部遥控器）· status")
    pp.add_argument("mode", nargs="?", choices=["set", "change", "reset", "status"], default="status")
    pp.set_defaults(fn=cmd_passphrase)
    ap = sub.add_parser("approvals", help="手机批准记录（approvals.log；不含命令内容，只有哈希）")
    ap.add_argument("--verify", action="store_true", help="逐条核对设备签名")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--last", type=int, default=0, help="只看最后 N 条")
    ap.set_defaults(fn=cmd_approvals)
    feedback.add_parser(sub)
    sub.add_parser("stop", help="⛔ 全部停下：中断 Agent、拒绝待批准、收回批量授权、暂停定时任务（重启后仍停）/ stop everything"
                   ).set_defaults(fn=cmd_stop)
    sub.add_parser("resume", help="从急停恢复（已配对手机点「恢复」即可；终端要批准口令或主人的键盘）/ resume after a stop (paired phone, or the owner at the terminal)").set_defaults(fn=cmd_resume)
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
    cf = sub.add_parser("config", help="开关：activity on|off（操作记录）· history on|off（聊天记录存在这台电脑上）/ switches")
    cf.add_argument("key", choices=["activity", "history"])
    cf.add_argument("value", nargs="?", choices=["on", "off", "status"], default="status")
    cf.set_defaults(fn=cmd_config)
    hi = sub.add_parser("history", help="聊天记录（手机翻页看到的，存在这台电脑上）：status · show <页> · clear · on · off / "
                                        "the phone's pages, kept on this computer")
    hi.add_argument("mode", nargs="?", choices=["status", "show", "clear", "on", "off"], default="status")
    hi.add_argument("id", nargs="?")
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
    elevate.add_parser(sub)   # F17: agentj sudo · agentj secret request
    from . import opencode_provider
    opencode_provider.add_parser(sub)   # P60 / F25: agentj provider add|list|remove (OpenCode base_url + key)
    elevate_helper.add_parser(sub)   # F17: agentj sudo-helper install · sync · uninstall · status
    docsrule.add_parser(sub)
    handover.add_parser(sub)
    plaza.add_parser(sub)
    support.add_parser(sub)   # F18: agentj support ask | report | thread | list
    recall.add_parser(sub)    # F22 (P57): agentj recall <keywords> [--days N] [--date D] — the main Agent finds an earlier conversation
    from . import peer_service
    peer_service.add_parser(sub)   # P71 (§17.9): agentj friends … — through <state>/agentperm/friends.sock, also inside the fence
    from . import codex_perm
    codex_perm.add_parser(sub)     # F30 (P73): agentj codex-sandbox status|set|default|fix — always the top level of config.toml
    mg = sub.add_parser("migrate", help="改名后的状态目录搬迁：status 查看 · rollback 撤销 / the 0.10 state move: status · rollback",
                        description="0.9 的状态目录 ~/.local/state/agentjarvis-alpha 会自动搬到 ~/.local/state/agentj（旧路径留一个链接）。"
                                    "rollback 搬回去（serve 必须没在运行）/ the old state directory moves automatically; rollback moves it back")
    mg.add_argument("mode", nargs="?", choices=["status", "rollback"], default="status")
    mg.add_argument("--json", action="store_true")
    mg.set_defaults(fn=cmd_migrate)
    al = sub.add_parser("alias", help="短命令 aj（迁移过的电脑另有旧命令 jarvis）：status · install · remove（同名已存在就不装）/ "
                                      "the short command `aj` (+ `jarvis` on a migrated computer)",
                        description="只在 PATH 里没有任何 aj 时，在 agentj 旁边建一个链接 aj → agentj；remove 只删我们自己的链接 / "
                                    "`aj` → agentj, only when no other `aj` exists; remove deletes only our own link")
    al.add_argument("mode", nargs="?", choices=["status", "install", "remove"], default="status")
    al.add_argument("--json", action="store_true")
    al.set_defaults(fn=cmd_alias)
    ob = sub.add_parser("onboarding", help="首次使用进度：席位、这台电脑的浏览器、主力手机、欢迎消息 / first-use progress: seat, "
                                           "this computer's browser, main phone, welcome")
    ob.add_argument("--json", action="store_true")
    ob.set_defaults(fn=cmd_onboarding)
    a = p.parse_args(argv)
    if a.cmd not in NO_MIGRATE and not (a.cmd == "doctor" and a.isolation_only):
        from . import migrate
        try:
            if migrate.auto() == "moved":
                _alias_auto(sys.stderr, with_compat=True)
        except OSError as e:      # never block a command: the old directory simply stays in use
            print(f"Agent J：状态目录检查失败（{type(e).__name__}），照常继续 / state check failed, continuing", file=sys.stderr)
    if a.cmd is None:
        cmd_hint()
        return
    a.fn(a)


if __name__ == "__main__":
    main()
