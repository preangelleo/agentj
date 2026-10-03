"""Agent J host CLI `agentj` (alpha): init · serve · pair · admin · name · devices · revoke · send · status · login · report ·
unlink · remote-unbind · agent · approvals · passphrase · doctor · service · update (L3) · stop · resume · memory · activity ·
config · tasks (phone controls, ADR-A50 – A54) · wizard (L3.5, `wizard/__init__.py`) · migrate · alias (0.10 rename).
No arguments = the next-step hint. `jarvis` (the ≤ 0.9 name) still works for one version cycle on computers that migrated
(a symlink made by alias.py, never shipped in the wheel): one notice line on stderr, then the same CLI. Every command first runs migrate.auto() (the 0.9 → 0.10 state move; messages on stderr).
Seat setup (0.7): `agentj login --seat <ajt_…> | --seat-file <path> | --seat - --name <name>` binds with a setup code from
the company (no y/N; exit 3 name taken · 4 invalid code · 5 seat not paid · 2 refused locally); `agentj agent detect`.
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
from . import feedback, plaza, wizard
from .state import DEFAULT_RELAY, DEFAULT_WEB, MAX_DEVICES, State
from .envcompat import getenv
from .text import STARTER_NAMES


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
    try:
        cfg = st.init(relay=a.relay, web=a.web, force=a.force)
    except FileExistsError as e:
        sys.exit(f"{e}（要重建身份密钥用 --force；所有已配对设备都要重配）")
    print(f"主机身份已生成：{st.root}\n通道 {cfg['channel']}\n中继 {cfg['relay']}\n下一步：`agentj serve`，再在另一个终端 `agentj pair`")
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
    if "utf8" not in loc:
        utf = False
    if utf and not getenv("AGENTJ_QR_ASCII", environ=e):
        return "compact"
    if e.get("TERM", "") not in ("", "dumb") and not getenv("AGENTJ_QR_ASCII", environ=e) and getattr(out, "isatty", lambda: False)():
        return "ansi"
    return "ascii"


def _qr_ascii(qr, border: int = 2) -> str:
    """Plain characters only: '##' = dark module, two spaces = light (reads like a printed code on a light background;
    on a dark terminal some phones still read it inverted — otherwise use --link)."""
    rows = []
    for row in qr.matrix_iter(scale=1, border=border):
        rows.append("".join("##" if dark else "  " for dark in row))
    return "\n".join(rows)


def _print_qr(link: str) -> None:
    import segno
    qr = segno.make(link, error="m")
    mode = _qr_mode()
    if mode == "compact":
        qr.terminal(compact=True, border=2)
    elif mode == "ansi":
        qr.terminal(compact=False, border=2)
    else:
        print(_qr_ascii(qr), flush=True)


def _remote_session(environ=None) -> bool:
    """A terminal over SSH without a desktop (a cloud server): no browser, a QR may not render well."""
    e = os.environ if environ is None else environ
    return bool(e.get("SSH_CONNECTION") or e.get("SSH_TTY")) and not (e.get("DISPLAY") or e.get("WAYLAND_DISPLAY"))


def cmd_pair(a) -> None:
    st = State()
    _need_init(st)
    if not gate.is_set(st):
        if not sys.stdin.isatty():
            sys.exit("✗ " + gate.MESSAGES["not_set"])
        print("配对前先设置批准口令（每次批准新遥控器都要输入，只在这台电脑上）。", flush=True)
        _set_passphrase_interactive(st, change=False)

    async def main():
        c = await _ctl(st)
        if not c:
            sys.exit("agentj serve 没在运行：先在另一个终端运行 `agentj serve`")
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
            print(f"\n把这个链接发到手机上打开（{mins} 分钟内有效，只能用一次；它就是配对密钥，别发给别人）：\n{first['link']}\n", flush=True)
        else:
            print(f"\n用手机相机扫上面的二维码（{mins} 分钟内有效，只能用一次）。扫不了就加 --link 重新运行。\n", flush=True)
            if _remote_session():
                print("（SSH 登录的服务器：手机扫不到终端里的码时，Ctrl-C 后运行 `agentj pair --link`，把打印出的链接用你自己的方式"
                      "发到手机上打开——同一个链接，它就是配对密钥，别发给别人。/ Over SSH: if the phone cannot scan it, run "
                      "`agentj pair --link` and open the same link on the phone.）\n", flush=True)
        if st.device_full():
            print(f"注意：本机已有 {MAX_DEVICES} 台遥控器（上限）。新设备扫码后，要先在这里解绑一台旧的才能批准它。\n", flush=True)

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
            if kind == "pending" and ev.get("full"):
                devs = ev.get("devices") or []
                print(f"一台新设备在等批准（它自称「{ev['name']}」，名字由设备自己填，不可信）。", flush=True)
                print(f"已达 {ev.get('limit', MAX_DEVICES)} 台上限，需先解绑一台遥控器才能添加新的。现有的：", flush=True)
                for i, d in enumerate(devs, 1):
                    pa = d.get("paired_at")
                    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(pa if isinstance(pa, (int, float)) else 0))
                    print(f"  {i}. {d.get('name') or '（没有名字）'}  {'在线' if d.get('online') else '离线'}  配对于 {when}  ({d['id']})",
                          flush=True)
                ans = await race(f"输入编号解绑那一台（1–{len(devs)}）；直接回车 = 不解绑，并拒绝新设备：")
                if ans is None:
                    ev = await take()
                    continue
                ans = ans.strip()
                pick = int(ans) if len(ans) <= 2 and ans.isascii() and ans.isdecimal() else 0
                if 1 <= pick <= len(devs):
                    d = devs[pick - 1]
                    sure = await race(f"确认解绑第 {pick} 台「{d.get('name') or '（没有名字）'}」（设备 {d['id']}）？它会立即断开，再用要重新扫码配对 [y/N] ")
                    if sure is None:
                        ev = await take()
                        continue
                    if sure.strip().lower() in ("y", "yes"):
                        await send({"cmd": "unbind", "device": d["id"]})
                        res = await take()
                        print(f"✓ 已解绑 {d.get('name') or d['id']}" if res.get("ok") else "✗ 没解绑成功（它可能已经被吊销了）", flush=True)
                        ev = await take()  # serve re-sends the pending event: now with room (or still full)
                    continue
                await send({"cmd": "code", "code": ""})
                ev = await take()
                continue
            if kind == "pending":
                print(f"一台设备已连上（它自称「{ev['name']}」，名字由设备自己填，不可信）。", flush=True)
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
    print("✓ 批准口令已保存（本机只存它的 scrypt 哈希）。")


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
        print("✓ 已重置，所有遥控器已吊销。运行 `agentj passphrase set` 设新口令，再 `agentj pair`。")
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
        where = "Dashboard 和本机都已改好" if res["linked"] else "本机没绑定 Dashboard，只改了本机"
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
    print(f"遥控器 {len(devs)}/{MAX_DEVICES}" + ("（已满：先 `agentj revoke <设备>` 才能再配对）" if len(devs) >= MAX_DEVICES else ""))
    if not devs:
        print("还没有已批准的设备。用 `agentj pair` 配对。")
    for did, v in devs.items():
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(v.get("paired_at", 0)))
        print(f"{did}  {'在线' if did in online else '离线'}  {v.get('name', '')}  配对于 {when}")


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
            print("（agentj serve 没在运行：已从准许名单删除；serve 启动后这台设备连不上。）")
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
        sys.exit("agentj serve 没在运行")
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
        print(f"agentj serve 没在运行（状态目录 {st.root}，通道 {st.config()['channel']}）")
        print(f"Agent：{_name_or_unset(st)}")
        print(_link_line(st))
        print(f"批准口令：{'已设置' if gate.is_set(st) else '未设置（`agentj passphrase set`）'}")
        print(_estop_line(st))
        return
    link = cloud.read_cloud(st)
    res["dashboard"] = link["tenant"]["slug"] if link else "未绑定 Dashboard"
    res["linked_via"] = link["via"] if link else None   # "seat" (seat setup code) | "code" (8-character agentj login)
    res["agent_name"] = st.agent_name()
    res["passphrase"] = gate.is_set(st)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def _link_line(st: State) -> str:
    link = cloud.read_cloud(st)
    if not link:
        return "未绑定 Dashboard"
    via = "用席位设置码绑定 / linked via seat setup" if link["via"] == "seat" else "用 8 位代码绑定 / linked via code"
    return f"Dashboard：已添加到公司账号 {link['tenant']['slug']}（{link['tenant']['name']}）· {via}"


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
        sys.exit(f"连不上控制面（{e.kind}）：{api}")
    s = res["status"]
    if s == "bound":
        t, name = res["tenant"], res["agent_name"]
        st.set_agent_name(name)
        st.log("agent_name_set", kind="seat")
        _ctl_quiet(st, {"cmd": "agent_name_changed"})
        print(f"✓ 已添加到公司账号 {t['slug']}（{t['name']}）的席位，Agent 名「{name}」")
        print(f"✓ Added to a seat of company {t['slug']} ({t['name']}); Agent name \"{name}\". "
              "If this is not the company you expected: `agentj unlink` — this also takes this computer out of that company.")
        r = _report_now(st)
        print("首次上报：成功 / first report: ok" if r.kind == "ok"
              else f"首次上报失败（{r.status}），serve 启动后会自动重试 / first report failed, serve retries")
        return
    msgs = {
        "bad_code": "设置码格式不对（应是 ajt_ 加 43 个字符）：从那句话里原样复制 / not a setup code (ajt_ + 43 characters): copy it exactly",
        "bad_name": "Agent 名不合规（1–32 个字，不能有控制字符）/ bad Agent name (1–32 characters, no control characters)",
        "name_required": "Agent 名不能为空 / the Agent name is required",
        "name_taken": "这个 Agent 名在公司里已经有了，换一个 / this Agent name is already used in the company",
        "invalid_setup": "设置码无效、已用过、已过期或已被作废——请向管理员要一个新的 / "
                         "the setup code is invalid, used, expired or revoked — ask the company's owner for a new one",
        "payment_required": "这个席位已经不在付费状态，请联系公司管理员 / this seat is no longer paid: contact the company's owner",
        "already_bound": "本机在 Dashboard 里仍是已绑定状态：先让公司所有者在 Dashboard 里解绑本机 / "
                         "this host is still bound in a Dashboard: the owner removes it there first",
        "rate_limited": "尝试太频繁，过一小时再试 / too many attempts: try again within the hour",
    }
    msg = "✗ " + msgs.get(s, f"绑定失败 / bind failed（{res.get('http')}{' ' + res['error'] if res.get('error') else ''}）")
    if s == "name_taken" and res.get("suggestions"):
        msg += "\n  可以试试 / try: " + "、".join(f"「{x}」" for x in res["suggestions"])
    print(msg, file=sys.stderr)
    sys.exit(SEAT_EXIT.get(s, 1))


def cmd_login(a) -> None:
    st = State()
    _need_init(st)
    link = cloud.read_cloud(st)
    if link:
        sys.exit(f"本机已添加到 Dashboard 公司账号 {link['tenant']['slug']}。要换绑先运行 `agentj unlink`（并在 Dashboard 里解绑本机）。")
    if a.seat is not None or a.seat_file:
        _seat_login(st, a)
        return
    if a.name is not None:
        sys.exit("--name 只和 --seat / --seat-file 一起用（8 位代码的方式在 Dashboard 里起名）/ --name goes with --seat only")
    try:
        api = cloud.api_url(st, override=a.api)
        app = cloud.app_url(st)
    except cloud.CloudError:
        sys.exit("拒绝：API / Dashboard 地址必须是 https://（http:// 只允许 127.0.0.1 / localhost）")

    def show(lg: dict) -> None:
        mins = max(1, lg["expires_in"] // 60)
        print(f"本机通道号：{cloud.channel_of(st)} — Dashboard 里显示的应该一样", flush=True)
        print(_hostname_notice(st), flush=True)
        # F7: the configured Dashboard; the server's verification_uri only when it is on that same origin
        print(f"在已登录的 Dashboard 里打开 {cloud.dashboard_uri(app, lg['verification_uri'])}", flush=True)
        print(f"输入这个代码：{lg['user_code']}（{mins} 分钟内有效，只能用一次）", flush=True)
        print("核对 Dashboard 显示的通道号和上面一致，再添加。等待中……（Ctrl-C 取消）", flush=True)

    def on_poll(kind: str) -> None:
        print(f"· {_POLL_NOTES.get(kind, kind)}", flush=True)

    def confirm(t: dict, agent_name: str | None = None) -> bool:
        """F5: the human at this terminal confirms the tenant the Dashboard bound this host to, before anything is written.
        A3.2: the prompt also shows the Agent name the Dashboard gave this host; the same y sets it locally."""
        q = (f"添加到公司账号 {t['slug']}（{t['name']}），Agent 名「{agent_name}」？[y/N] " if agent_name
             else f"添加到公司账号 {t['slug']}（{t['name']}）？[y/N] ")
        if a.yes:
            print(q + "y（--yes）", flush=True)
            return True
        try:
            ans = input(q)
        except EOFError:
            ans = ""
        return ans.strip().lower() in ("y", "yes")

    try:
        res = cloud.login(st, api, show=show, confirm=confirm, on_poll=on_poll)
    except KeyboardInterrupt:
        print("\n已取消，本机没有绑定。")
        sys.exit(130)
    except cloud.CloudError as e:
        sys.exit(f"连不上控制面（{e.kind}）：{api}")
    st_ = res["status"]
    if st_ == "bound":
        t = res["tenant"]
        if res.get("agent_name"):
            st.set_agent_name(res["agent_name"])   # after the human's y, like cloud.json (contract §3)
            st.log("agent_name_set", kind="login")
            _ctl_quiet(st, {"cmd": "agent_name_changed"})
        print(f"✓ 已添加到 Dashboard 公司账号 {t['slug']}（{t['name']}）。agentj serve 运行时会定期上报设备元数据（不含消息内容）。")
        if res.get("agent_name"):
            print(f"本机的 Agent 名：「{res['agent_name']}」（在 Dashboard 或 `agentj name` 里可以改）")
        r = _report_now(st)
        print("首次上报：成功" if r.kind == "ok" else f"首次上报失败（{r.status}），serve 启动后会自动重试")
        return
    if st_ == "declined":
        t = res["tenant"]
        if res.get("undone") == "undone":
            sys.exit(f"✗ 没有绑定：本机什么都没写，并已通知 Dashboard 撤销这次添加（公司账号 {t['slug']} 里不再有本机）。\n"
                     "  如果这不是你自己输入的代码，说明有人看到了这个终端上的代码——别再让别人看到。")
        sys.exit(f"✗ 没有绑定：本机什么都没写，也不会上报。但没能通知 Dashboard 撤销（{res.get('undone')}），"
                 f"那边可能仍把本机记在公司账号 {t['slug']}（{t['name']}）下：\n"
                 f"  如果这是你的公司账号，到 Dashboard 里移除本机；如果不是你的，说明有人用了这个终端上显示的代码——"
                 f"别再让别人看到代码，并请对方（或我们）在 Dashboard 里解绑本机。")
    msgs = {"already_bound": "本机在 Dashboard 里仍是已绑定状态：先在 Dashboard 里解绑本机，再运行 `agentj login`",
            "rate_limited": "登录请求太频繁，稍后再试", "rejected": "Dashboard 拒绝了这次绑定",
            "expired": "代码已过期（10 分钟），请重新运行 `agentj login`"}
    sys.exit("✗ " + msgs.get(st_, f"登录失败（{res.get('http')}{' ' + res['error'] if res.get('error') else ''}）"))


def _hostname_notice(st: State) -> str:
    from .text import machine_name
    m = machine_name()
    if st.report_machine() and m:
        return f"绑定后会上报本机主机名「{m}」（Dashboard 卡片上显示「运行在 … 上」）；不想上报：`agentj report-hostname off`"
    return "本机不上报主机名（`agentj report-hostname on` 打开）"


def cmd_report_hostname(a) -> None:
    st = State()
    _need_init(st)
    if a.mode in ("on", "off"):
        st.set_report_machine(a.mode == "on")
        st.log("report_machine_switch", status=a.mode)
        if cloud.read_cloud(st):
            _ctl_quiet(st, {"cmd": "agent_name_changed"})   # = "send a report soon", so the Dashboard sees the change
    print(_hostname_notice(st) if st.report_machine() else "关：上报里的 machine 是空的（null），Dashboard 显示「等主机上报」。")


def cmd_report(a) -> None:
    st = State()
    _need_init(st)
    link = cloud.read_cloud(st)
    if not link:
        sys.exit("未绑定 Dashboard：先运行 `agentj login`")
    res = _report_now(st)
    if res.kind == "ok":
        print(f"已上报到 Dashboard 公司账号 {link['tenant']['slug']}（seq {res.seq}）")
    elif res.kind == "unbound":
        sys.exit("Dashboard 那边已经解绑本机（not_bound）。本地记录还在：`agentj unlink` 清掉后可重新 `agentj login`")
    elif res.kind == "unlinked":
        sys.exit("未绑定 Dashboard：先运行 `agentj login`")
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
            print(f"已通知 Dashboard 把本机移出公司 {slug} / The Dashboard took this computer out of company {slug}.")
        elif r["status"] == "not_found":
            print(f"Dashboard 上本机已不在公司 {slug} 的席位里 / This computer is no longer in a seat of company {slug}.")
        else:
            why = {"rate_limited": "太频繁，稍后再试 / rate limited"}.get(r["status"], f"连不上或出错（{r.get('http', '')}）")
            print(f"没能通知 Dashboard 把本机移出公司 {slug}：{why}。请让公司管理员在 Dashboard 里收回这个席位。"
                  f" / Could not tell the Dashboard; ask the company's owner to recall the seat.")
    removed = cloud.delete_cloud(st)
    if not removed:
        print("本机没有绑定 Dashboard。")
        return
    st.log("cloud_unlinked")
    if left:
        print(f"已删除本机的绑定记录（公司账号 {slug}），不再上报。")
    else:
        print(f"已删除本机的绑定记录（公司账号 {slug}），不再上报。Dashboard 那边的绑定要在 Dashboard 里解绑本机。")


AGENT_LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


def _agent_line(st: State) -> str:
    c = st.agent_config()
    if not c:
        return "接的 Agent：无（手机消息只显示在这个终端；`agentj agent claude --dir <目录>` 接上 Claude Code）"
    extra = {"claude": "；手机上批准权限请求",
             "codex": "；手机上批准权限请求（agentj 让 Codex 每条非只读命令和每个改动都先问：untrusted，审批人 = 你）",
             "opencode": "；手机上批准权限请求（OpenCode 的规则由 agentj 加严：除只读操作外都问手机）"}.get(c["kind"], "")
    fz = " · 隔离运行（看不到 Agent J 的密钥与设备名单）" if c.get("fence", True) else " · ⚠ 不隔离运行（--unfenced）"
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
        if a.unfenced:
            print("⚠ 不隔离运行：Agent 能读到本机的密钥和设备名单、能改 Agent J 的设置——你在手机上批准的任何一条命令\n"
                  "  都可能借此给自己加一台遥控器。只在 bubblewrap 用不了、而你清楚风险时才这样做。", flush=True)
            try:
                gate.verify(st, _secret("输入批准口令确认："))
            except gate.GateError as e:
                sys.exit("✗ " + _gate_msg(e))
        elif a.allow_docker:
            print("⚠ 允许 Agent 用 docker / podman：能用容器引擎就能把整台电脑的文件挂进容器——包括 Agent J 的密钥和设备名单，\n"
                  "  隔离对它基本失效。只在 Agent 确实要跑容器、而你清楚风险时才这样做。", flush=True)
            try:
                gate.verify(st, _secret("输入批准口令确认："))
            except gate.GateError as e:
                sys.exit("✗ " + _gate_msg(e))
        try:
            st.set_agent_config(a.mode, a.dir, a.model, fence=not a.unfenced, docker=bool(a.allow_docker and not a.unfenced))
        except ValueError as e:
            sys.exit({"bad_dir": "目录不存在",
                      "protected_dir": "这个目录是 Agent J 自己的（状态目录或程序目录），不能给 Agent 用：换一个工作目录"}.get(str(e), str(e)))
        st.log("agent_config", agent=a.mode, fence=not a.unfenced, change="docker" if a.allow_docker else None)
        if not a.unfenced:
            from . import fence
            why = fence.problem(st, st.agent_config()["dir"])
            if why:
                print(f"⚠ {fence.REASONS.get(why, why)}：serve 启动时不会运行 Agent。", flush=True)
    elif a.mode == "off":
        st.set_agent_config(None)
        st.log("agent_config", agent="off")
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
    print("⛔ 已急停：Agent 停下、待批准的全部拒绝、批量授权收回、定时任务暂停；serve 重启后仍是急停。恢复：`agentj resume`")


def cmd_resume(a) -> None:
    from . import activity, controls
    st = State()
    _need_init(st)
    if not controls.estop_state(st)["on"]:
        print("没有急停，不用恢复。")
        return
    if gate.is_set(st):     # resuming gives the Agent its power back: the human's passphrase, like approving a remote
        try:
            gate.verify(st, _secret("输入批准口令恢复："))
        except gate.GateError as e:
            sys.exit("✗ " + _gate_msg(e))
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
        "control_refused": f"拒绝了手机命令 {r.get('action', '')}{by}：{r.get('why', '')}", "truncated": "（今天的记录已达上限，后面的没有记）",
    }.get(k, str(k))


def cmd_config(a) -> None:
    from . import activity
    st = State()
    _need_init(st)
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
            if not gate.is_set(st):
                sys.exit("✗ 先设置批准口令（`agentj passphrase set`）：启用定时任务要用它确认")
            print(f"启用「{e['task']['title']['zh']}」：按 {e['task']['schedule']}（{e['task']['tz']}）自动运行 "
                  f"{'（只读运行）' if e['task']['mode'] == 'research' else ''}；改了 task.json 或 {e['task']['prompt_file']} 就要重新启用。",
                  flush=True)
            try:
                gate.verify(st, _secret("输入批准口令确认："))
            except gate.GateError as err:
                sys.exit("✗ " + _gate_msg(err))
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
    print(("开：Dashboard 的所有者可以请求本机解绑一台遥控器；本机核对它在准许名单上、每小时最多执行 "
           f"{REMOTE_UNBIND_PER_HOUR} 次（重启也不清零），执行与拒绝都记进 host.log。（只能让设备失去访问，不能加设备或批准设备。）") if on else
          "关：本机拒绝 Dashboard 的一切解绑请求（会回报「已关闭」）；解绑只能在这里用 `agentj revoke`。")
    print("提醒：配对二维码 / 链接永远只由本机的 `agentj pair` 显示；Dashboard、邮件或客服给你的二维码都不要扫。")


def cmd_doctor(a) -> None:
    from . import doctor
    sys.exit(doctor.main(as_json=a.json, offline=a.offline))


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
        if a.mode == "uninstall":
            r = service.uninstall()
            print(f"✓ 已停止并删除 / stopped and removed: {r['name']}" if r["removed"]
                  else f"{r['name']} 没安装 / was not installed")
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
    print("服务只记元数据，不记消息 / the service logs metadata only, never messages.")


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
    r = update.check()
    if a.mode == "check":
        if a.json:
            print(json.dumps(r, ensure_ascii=False))
            return
        print(_update_line(r))
        if r["status"] in ("newer", "unknown"):
            print("升级 = 人在自己的终端运行 / to upgrade, the human runs:  agentj update apply")
            print(f"  （它会执行 / it runs:  {r['command']}）")
        return
    # apply
    if r["status"] == "unknown":
        sys.exit(f"✗ 查不到最新版本（{r['why']}），没有升级。稍后再试，或手动运行：{r['command']} / could not find out the "
                 "latest version; nothing changed")
    if r["status"] != "newer":
        print(_update_line(r))
        return
    try:
        update.preflight()
    except update.Refused as e:
        print(update.REFUSED[e.reason], file=sys.stderr)
        print(f"  （命令 / command:  {r['command']}）", file=sys.stderr)
        sys.exit(2)
    print(f"新版本 {r['latest']}（本机 {r['current']}，安装方式 {r['install']}）。将运行 / will run:\n  {r['command']}")
    svc_on = bool(service.status().get("installed") or service.legacy_status().get("installed"))
    if svc_on:
        print("之后会重新安装并重启服务 / then the service is re-installed and restarted")
    try:
        ans = input("现在升级？/ upgrade now? [y/N] ")
    except EOFError:
        ans = ""
    if ans.strip().lower() not in ("y", "yes"):
        sys.exit("没有升级 / not upgraded")
    import subprocess
    info = update.install_kind()
    for c in update.commands(info):
        rc = subprocess.run(c).returncode
        if rc != 0:
            sys.exit(f"✗ 升级命令失败（退出码 {rc}）：{' '.join(c)} / upgrade command failed")
    argv = update.new_argv(info)
    v = subprocess.run(argv + ["--version"], capture_output=True, text=True)
    print("✓ 现在是 / now: " + (v.stdout.strip() or v.stderr.strip() or "?"))
    if svc_on:
        rc = subprocess.run(argv + ["service", "install"]).returncode
        if rc != 0:
            sys.exit("✗ 服务没能重新安装：运行 `agentj service install` / the service was not re-installed")
    print("完成 / done. `agentj doctor` 再检查一遍 / run `agentj doctor` to check")


def _update_line(r: dict) -> str:
    s = r["status"]
    if s == "newer":
        return f"! 有新版本 / newer version: {r['latest']}（本机 / installed {r['current']}）"
    if s == "current":
        return f"✓ 已是最新 / up to date: {r['current']}"
    if s == "ahead":
        return f"✓ 本机 {r['current']} 比发布版 {r['latest']} 新（开发版） / ahead of the release"
    return f"! 查不到最新版本 / could not check ({r['why']}); 本机 / installed {r['current']}"


NEXT_STEPS = (   # (command, 中文, English)
    ("agentj init", "生成本机身份", "create this host's identity"),
    ("agentj login", "把本机加到 Dashboard 公司账号（有席位设置码：--seat-file）",
     "add this host to your Dashboard company (setup code: --seat-file)"),
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


NO_MIGRATE = ("migrate",)   # `migrate status` reports, `migrate rollback` undoes: neither may move anything first


def main(argv=None) -> None:
    if argv is None and os.path.basename(sys.argv[0] or "") == "jarvis":   # the compat symlink made on a migrated computer
        print(JARVIS_NOTICE, file=sys.stderr, flush=True)
    p = argparse.ArgumentParser(prog="agentj", description="Agent J 主机端（alpha）：用手机和你自己的 Agent 对话 / Agent J host "
                                                           "(alpha): talk to your own Agent from your phone")
    p.add_argument("-V", "--version", action="version", version=f"{DIST} {__version__}")
    sub = p.add_subparsers(dest="cmd")
    dc = sub.add_parser("doctor", help="自检：一项一行 ✓/!/✗ + 修法 / health check, one line per check",
                        description="自检 / health check: ✓ ok · ! warning · ✗ must fix. Exit 0 unless a ✗. Never prints secrets.")
    dc.add_argument("--json", action="store_true", help="机器可读 / machine-readable (paths shown with ~)")
    dc.add_argument("--offline", action="store_true", help="跳过网络检查 / skip the relay and Dashboard checks")
    dc.set_defaults(fn=cmd_doctor)
    sv = sub.add_parser("service", help="开机 / 登录后自动运行 serve：install · uninstall · status / run serve as a service",
                        description="Linux: systemd user unit · macOS: LaunchAgent. 不写任何密钥 / never writes a secret.")
    sv.add_argument("mode", choices=["install", "uninstall", "status"], help="install 安装并启动 · uninstall 停止并删除 · status 状态")
    sv.add_argument("--json", action="store_true", help="status 的机器可读输出 / machine-readable status")
    sv.set_defaults(fn=cmd_service)
    up = sub.add_parser("update", help="新版本：check 查（谁都可以）· apply 升级（只由人在终端确认）· auto on|off 每天自动查 / updates",
                        description="check: 查最新版本并给出与安装方式匹配的升级命令（网络不通 = 查不到，不算错）。apply: 只在交互终端里、"
                                    "人输入 y 之后才执行；没有 --yes。auto: serve 每天查一次、有新版就提醒手机（从不自动安装）。")
    up.add_argument("mode", choices=["check", "apply", "auto"])
    up.add_argument("switch", nargs="?", choices=["on", "off", "status"], default="status", help="auto 的开关 / for auto")
    up.add_argument("--json", action="store_true", help="check 的机器可读输出 / machine-readable check")
    up.set_defaults(fn=cmd_update)
    i = sub.add_parser("init", help="生成主机身份密钥")
    i.add_argument("--relay", default=DEFAULT_RELAY)
    i.add_argument("--web", default=DEFAULT_WEB)
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)
    s = sub.add_parser("serve", help="连上中继，收发消息（前台）")
    s.add_argument("--events", choices=["text", "jsonl", "quiet"], default="text",
                   help="text：终端（显示消息）· jsonl：脚本 · quiet：服务模式，只有元数据、不含消息 / quiet = service mode, metadata only")
    s.add_argument("--no-stdin", action="store_true", help="不读终端输入（服务 / 后台） / do not read stdin")
    s.set_defaults(fn=cmd_serve)
    pr = sub.add_parser("pair", help="终端二维码配对一台新设备（需 serve 在运行）")
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
    nm = sub.add_parser("name", help="查看 / 修改本机 Agent 的名字（绑定了 Dashboard 时先在 Dashboard 改，成功才改本机）")
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
    lo = sub.add_parser("login", help="把本机添加到 Dashboard 公司账号（占 1 个席位；只上报元数据，不含消息内容）")
    lo.add_argument("--api", help=f"控制面地址（默认 $AGENTJ_API_URL 或 {cloud.DEFAULT_API}）")
    lo.add_argument("--yes", action="store_true", help="不询问，直接确认 Dashboard 报回来的公司账号（脚本 / 测试用）")
    seat = lo.add_mutually_exclusive_group()
    seat.add_argument("--seat", metavar="CODE",
                      help="用公司给的席位设置码（ajt_…）直接绑定，不用 8 位代码、不问 y/N；`-` = 从标准输入读一行 / "
                           "bind with a seat setup code from the company (no 8-character code, no y/N); `-` reads stdin")
    seat.add_argument("--seat-file", metavar="PATH",
                      help="从文件读设置码（文件必须 0600），这样它不进命令行和 shell 历史 / "
                           "read the setup code from a 0600 file, so it stays out of argv and shell history")
    lo.add_argument("--name", help="和 --seat 一起：本机 Agent 的名字（1–32 个字）/ with --seat: the Agent's name (1–32 characters). "
                                   "退出码 / exit: 3 名字已占用 name taken · 4 设置码无效 invalid code · 5 席位未付费 seat not paid · "
                                   "2 本地拒绝 refused locally")
    lo.set_defaults(fn=cmd_login)
    sub.add_parser("report", help="立即向 Dashboard 上报一次设备元数据").set_defaults(fn=cmd_report)
    sub.add_parser("unlink", help="删除本机的 Dashboard 绑定记录（Dashboard 端在 Dashboard 里解绑）").set_defaults(fn=cmd_unlink)
    rh = sub.add_parser("report-hostname", help="是否向 Dashboard 上报本机主机名（默认上报；off = 上报为空）")
    rh.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")
    rh.set_defaults(fn=cmd_report_hostname)
    ru = sub.add_parser("remote-unbind", help="允许 / 禁止 Dashboard 请求本机解绑遥控器（默认允许；只能减少访问）")
    ru.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")
    ru.set_defaults(fn=cmd_remote_unbind)
    ag = sub.add_parser("agent", help="接哪个 Agent：claude / codex / opencode / off；reset = 开一段新对话（重启 serve 生效）；"
                                      "detect = 本机有哪些可用 / which agents are usable here")
    ag.add_argument("mode", nargs="?", choices=["claude", "codex", "opencode", "off", "reset", "status", "detect"], default="status",
                    help="detect = 本机有哪些可用（不需要 init；只看是否安装、登录文件是否存在）/ which agents are usable here "
                         "(no init needed; checks only what is installed and whether login files exist)")
    ag.add_argument("--json", action="store_true", help="detect 的机器可读输出 / machine-readable detect output")
    ag.add_argument("--dir", help="Agent 的工作目录（默认当前目录）")
    ag.add_argument("--model", help="模型（默认用你自己的设置）；OpenCode 写成 服务商/模型，例如 zhipuai/glm-5.3")
    ag.add_argument("--unfenced", action="store_true",
                    help="不隔离运行 Agent（不推荐；要输入批准口令）。默认 Agent 在 bubblewrap 里运行，看不到 Agent J 的状态")
    ag.add_argument("--allow-docker", action="store_true",
                    help="隔离里也让 Agent 用 docker / podman（不推荐；要输入批准口令）。默认容器引擎的 socket 对 Agent 隐藏")
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
    sub.add_parser("resume", help="从急停恢复（要批准口令）/ resume after a stop (approval passphrase)").set_defaults(fn=cmd_resume)
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
    cf = sub.add_parser("config", help="开关：activity on|off（操作记录）/ switches")
    cf.add_argument("key", choices=["activity"])
    cf.add_argument("value", nargs="?", choices=["on", "off", "status"], default="status")
    cf.set_defaults(fn=cmd_config)
    tk = sub.add_parser("tasks", help="定时任务：list · show · enable（要口令）· disable · run [--dry-run] / scheduled tasks")
    tk.add_argument("mode", choices=["list", "show", "enable", "disable", "run"])
    tk.add_argument("id", nargs="?")
    tk.add_argument("--dry-run", action="store_true", help="run：只显示会怎么跑，不运行 / show the plan only")
    tk.add_argument("--json", action="store_true")
    tk.set_defaults(fn=cmd_tasks)
    wizard.add_parser(sub)
    plaza.add_parser(sub)
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
    a = p.parse_args(argv)
    if a.cmd not in NO_MIGRATE:
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
