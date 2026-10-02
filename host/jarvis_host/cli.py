"""agentjarvis host CLI (alpha): init · serve · pair · admin · name · devices · revoke · send · status · login · report ·
unlink · remote-unbind · agent · approvals · passphrase · doctor · service (L3). No arguments = the next-step hint.

State dir: $AGENTJARVIS_STATE_DIR or ~/.local/state/agentjarvis-alpha (0700). Approval of a new device happens only here,
in the terminal running `jarvis pair`: the human types the 6-digit code shown on the phone. At most MAX_DEVICES (5) remotes
per host: when the list is full, `jarvis pair` lists them and the human unbinds one before approving. `jarvis admin` is the
same thing as a local web page (127.0.0.1 only, one-time link printed here; the human still types the phone's code).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import threading
import time

from . import DIST, __version__, cloud, gate, names
from .state import DEFAULT_RELAY, DEFAULT_WEB, MAX_DEVICES, State
from .text import STARTER_NAMES


def _need_init(st: State) -> None:
    if not st.exists():
        sys.exit(f"还没初始化：先运行 `jarvis init`（状态目录 {st.root}）")
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
    print(f"主机身份已生成：{st.root}\n通道 {cfg['channel']}\n中继 {cfg['relay']}\n下一步：`jarvis serve`，再在另一个终端 `jarvis pair`")


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
            print(f"jarvis serve {__version__} · 通道 {host.channel} · service mode (--events quiet)", flush=True)
            print(_agent_line(st), flush=True)
        if a.events == "text":
            print(f"jarvis serve · 通道 {host.channel} · 输入一行回车 = 发给所有已批准设备；Ctrl-C 退出", flush=True)
            print(f"Agent：{_name_or_unset(st)} · Agent 管理页：运行 `jarvis admin`", flush=True)
            print(_agent_line(st), flush=True)
        await host.run()

    asyncio.run(main())


def _print_qr(link: str) -> None:
    import segno
    qr = segno.make(link, error="m")
    qr.terminal(compact=True, border=2)


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
            sys.exit("jarvis serve 没在运行：先在另一个终端运行 `jarvis serve`")
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
                sys.exit("jarvis serve 断开了")
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
                       "device_limit": f"已达 {MAX_DEVICES} 台上限，没有批准：先解绑一台（`jarvis revoke <设备>`）再配对",
                       "pass_locked": gate.MESSAGES["locked"], "pass_not_set": gate.MESSAGES["not_set"]}
            msg = {"expired": "二维码已过期（5 分钟），请重新运行 jarvis pair", "gone": "手机断开了",
                   "replaced": "已被新的 jarvis pair 取代"}.get(kind, reasons.get(ev.get("reason"), str(ev)))
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
                sys.exit("jarvis serve 在运行但没有响应，没有重置")
            if res is None:
                st.remove_device(did)
        gate.reset(st)
        print("✓ 已重置，所有遥控器已吊销。运行 `jarvis passphrase set` 设新口令，再 `jarvis pair`。")
    else:
        left = gate.lock_left(st)
        print(("已设置" + (f"（锁定中，还要 {max(1, left // 60)} 分钟）" if left else "")) if gate.is_set(st)
              else "还没设置：运行 `jarvis passphrase set`")


def _name_or_unset(st: State) -> str:
    n = st.agent_name()
    return f"「{n}」" if n else "还没起名（`jarvis name <名字>`）"


def cmd_name(a) -> None:
    st = State()
    _need_init(st)
    if a.name is None:
        n = st.agent_name()
        print(f"Agent 名：「{n}」" if n else "Agent 名：还没起名。")
        if not n:
            print("起个名字：`jarvis name <名字>`。可以参考：" + "、".join(f"「{x}」" for x in STARTER_NAMES) + "，或者自己起一个。")
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
    print(f"遥控器 {len(devs)}/{MAX_DEVICES}" + ("（已满：先 `jarvis revoke <设备>` 才能再配对）" if len(devs) >= MAX_DEVICES else ""))
    if not devs:
        print("还没有已批准的设备。用 `jarvis pair` 配对。")
    for did, v in devs.items():
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(v.get("paired_at", 0)))
        print(f"{did}  {'在线' if did in online else '离线'}  {v.get('name', '')}  配对于 {when}")


def cmd_revoke(a) -> None:
    st = State()
    _need_init(st)
    try:
        res = names.ctl_call(st, {"cmd": "revoke", "device": a.device}, 10)
    except names.ServeBusy:
        sys.exit("jarvis serve 在运行但没有响应：没有吊销（不会绕过它直接改准许名单）。稍后再试，或先停掉 serve。")
    if res is None:  # serve certainly not running (no socket / refused): edit the allowlist directly, under its lock
        ok = st.remove_device(a.device)
        if ok:
            st.log("revoked", device=a.device)
            print("（jarvis serve 没在运行：已从准许名单删除；serve 启动后这台设备连不上。）")
        res = {"ok": ok, "closed": 0}
    if not res.get("ok"):
        sys.exit(f"没有这台设备：{a.device}（`jarvis devices` 查看）")
    print(f"已吊销 {a.device}，断开了 {res.get('closed', 0)} 个连接。")


def cmd_send(a) -> None:
    st = State()
    _need_init(st)
    try:
        res = names.ctl_call(st, {"cmd": "send", "text": a.text}, 10)
    except names.ServeBusy:
        sys.exit("jarvis serve 没有响应，没有发送")
    if res is None:
        sys.exit("jarvis serve 没在运行")
    if not res.get("ok"):
        sys.exit("太长了（上限 4000 字），没有发送" if res.get("error") == "too_long" else f"发送失败：{res}")
    print(f"已发给 {res.get('delivered', 0)} 台设备")


def cmd_status(a) -> None:
    st = State()
    _need_init(st)
    try:
        res = names.ctl_call(st, {"cmd": "status"}, 10)
    except names.ServeBusy:
        sys.exit("jarvis serve 在运行但没有响应")
    if res is None:
        print(f"jarvis serve 没在运行（状态目录 {st.root}，通道 {st.config()['channel']}）")
        print(f"Agent：{_name_or_unset(st)}")
        print(_link_line(st))
        print(f"批准口令：{'已设置' if gate.is_set(st) else '未设置（`jarvis passphrase set`）'}")
        return
    link = cloud.read_cloud(st)
    res["dashboard"] = link["tenant"]["slug"] if link else "未绑定 Dashboard"
    res["agent_name"] = st.agent_name()
    res["passphrase"] = gate.is_set(st)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def _link_line(st: State) -> str:
    link = cloud.read_cloud(st)
    return f"Dashboard：已添加到公司账号 {link['tenant']['slug']}（{link['tenant']['name']}）" if link else "未绑定 Dashboard"


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


def cmd_login(a) -> None:
    st = State()
    _need_init(st)
    link = cloud.read_cloud(st)
    if link:
        sys.exit(f"本机已添加到 Dashboard 公司账号 {link['tenant']['slug']}。要换绑先运行 `jarvis unlink`（并在 Dashboard 里解绑本机）。")
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
        print(f"✓ 已添加到 Dashboard 公司账号 {t['slug']}（{t['name']}）。jarvis serve 运行时会定期上报设备元数据（不含消息内容）。")
        if res.get("agent_name"):
            print(f"本机的 Agent 名：「{res['agent_name']}」（在 Dashboard 或 `jarvis name` 里可以改）")
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
    msgs = {"already_bound": "本机在 Dashboard 里仍是已绑定状态：先在 Dashboard 里解绑本机，再运行 `jarvis login`",
            "rate_limited": "登录请求太频繁，稍后再试", "rejected": "Dashboard 拒绝了这次绑定",
            "expired": "代码已过期（10 分钟），请重新运行 `jarvis login`"}
    sys.exit("✗ " + msgs.get(st_, f"登录失败（{res.get('http')}{' ' + res['error'] if res.get('error') else ''}）"))


def _hostname_notice(st: State) -> str:
    from .text import machine_name
    m = machine_name()
    if st.report_machine() and m:
        return f"绑定后会上报本机主机名「{m}」（Dashboard 卡片上显示「运行在 … 上」）；不想上报：`jarvis report-hostname off`"
    return "本机不上报主机名（`jarvis report-hostname on` 打开）"


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
        sys.exit("未绑定 Dashboard：先运行 `jarvis login`")
    res = _report_now(st)
    if res.kind == "ok":
        print(f"已上报到 Dashboard 公司账号 {link['tenant']['slug']}（seq {res.seq}）")
    elif res.kind == "unbound":
        sys.exit("Dashboard 那边已经解绑本机（not_bound）。本地记录还在：`jarvis unlink` 清掉后可重新 `jarvis login`")
    elif res.kind == "unlinked":
        sys.exit("未绑定 Dashboard：先运行 `jarvis login`")
    else:
        sys.exit(f"上报失败（{res.status}）")


def cmd_unlink(a) -> None:
    st = State()
    _need_init(st)
    link = cloud.read_cloud(st)
    removed = cloud.delete_cloud(st)
    if not removed:
        print("本机没有绑定 Dashboard。")
        return
    st.log("cloud_unlinked")
    slug = link["tenant"]["slug"] if link else "?"
    print(f"已删除本机的绑定记录（公司账号 {slug}），不再上报。Dashboard 那边的绑定要在 Dashboard 里解绑本机。")


AGENT_LABEL = {"claude": "Claude Code", "codex": "Codex"}


def _agent_line(st: State) -> str:
    c = st.agent_config()
    if not c:
        return "接的 Agent：无（手机消息只显示在这个终端；`jarvis agent claude --dir <目录>` 接上 Claude Code）"
    extra = "；手机上批准权限请求" if c["kind"] == "claude" else "；只收发文字（权限按你自己的 Codex 配置）"
    fz = " · 隔离运行（看不到 jarvis 的密钥与设备名单）" if c.get("fence", True) else " · ⚠ 不隔离运行（--unfenced）"
    return (f"接的 Agent：{AGENT_LABEL[c['kind']]} · 目录 {c['dir']}" + (f" · 模型 {c['model']}" if c["model"] else "")
            + fz + extra)


def cmd_agent(a) -> None:
    st = State()
    _need_init(st)
    if a.mode in ("claude", "codex"):
        if a.unfenced:
            print("⚠ 不隔离运行：Agent 能读到本机的密钥和设备名单、能改 jarvis 的设置——你在手机上批准的任何一条命令\n"
                  "  都可能借此给自己加一台遥控器。只在 bubblewrap 用不了、而你清楚风险时才这样做。", flush=True)
            try:
                gate.verify(st, _secret("输入批准口令确认："))
            except gate.GateError as e:
                sys.exit("✗ " + _gate_msg(e))
        try:
            st.set_agent_config(a.mode, a.dir, a.model, fence=not a.unfenced)
        except ValueError as e:
            sys.exit({"bad_dir": "目录不存在",
                      "protected_dir": "这个目录是 jarvis 自己的（状态目录或程序目录），不能给 Agent 用：换一个工作目录"}.get(str(e), str(e)))
        st.log("agent_config", agent=a.mode, fence=not a.unfenced)
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
    if a.mode in ("claude", "codex", "off"):
        print("重启 `jarvis serve` 后生效。Agent 以你自己的身份、登录和权限设置运行，本程序不会给它更多权限。")


def cmd_approvals(a) -> None:
    from . import approvals
    st = State()
    _need_init(st)
    recs = approvals.read_log(st)[-a.last:] if a.last else approvals.read_log(st)
    bad = 0
    for r in recs:
        v = approvals.check_record(st, r) if a.verify else ""
        bad += v == "bad"
        if a.json:
            print(json.dumps({**r, **({"verify": v} if v else {})}, ensure_ascii=False))
        else:
            when = time.strftime("%m-%d %H:%M:%S", time.localtime(r.get("ts", 0)))
            who = f"手机 {r.get('device')}" if r.get("device") else {"timeout": "超时", "no_device": "无可批准手机",
                                                                       "serve_stop": "serve 停止", "agent_gone": "Agent 撤回",
                                                                       "too_many": "请求过多"}.get(r.get("reason"), r.get("reason"))
            mark = {"ok": " ✓签名有效", "ok_removed": " ✓签名有效（设备已移除）", "bad": " ✗签名无效", "unsigned": ""}.get(v, "")
            print(f"{when}  {r.get('decision', '?'):5}  {r.get('tool', '?'):12}  {who}{mark}")
    if not recs:
        print("还没有批准记录。")
    if a.verify and bad:
        sys.exit(f"{bad} 条记录签名无效")


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
          "关：本机拒绝 Dashboard 的一切解绑请求（会回报「已关闭」）；解绑只能在这里用 `jarvis revoke`。")
    print("提醒：配对二维码 / 链接永远只由本机的 `jarvis pair` 显示；Dashboard、邮件或客服给你的二维码都不要扫。")


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
        st = State()
        _need_init(st)
        cur = service.status()
        try:
            running = names.ctl_call(st, {"cmd": "status"}, 5) is not None
        except names.ServeBusy:
            running = True
        if running and cur.get("active") != "active":
            sys.exit("✗ 有一个 `jarvis serve` 正在终端里运行：先 Ctrl-C 停掉它，再安装服务 / "
                     "a `jarvis serve` is running in a terminal: stop it first")
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


NEXT_STEPS = (   # (command, 中文, English)
    ("jarvis init", "生成本机身份", "create this host's identity"),
    ("jarvis login", "把本机加到 Dashboard 公司账号", "add this host to your Dashboard company"),
    ("jarvis passphrase set", "设批准口令（自己输，别让 Agent 代劳）", "set the approval passphrase (yourself)"),
    ("jarvis agent claude --dir <folder>", "接上 Claude Code（或 codex）", "connect Claude Code (or codex)"),
    ("jarvis service install", "后台常驻运行 serve", "keep `jarvis serve` running"),
    ("jarvis pair", "手机扫码配对", "pair your phone (scan the QR)"),
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
    print("\n" + (f"下一步 / next:  {nxt}" if nxt else "都设好了 / all set — 手机上打开 alpha-web 开始对话 / open the web client on your phone"))
    print("自检 / health check:  jarvis doctor     ·     全部命令 / all commands:  jarvis --help")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="jarvis", description="agentjarvis 主机端（alpha） / agentjarvis host (alpha)")
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
    lo.add_argument("--api", help=f"控制面地址（默认 $AGENTJARVIS_API_URL 或 {cloud.DEFAULT_API}）")
    lo.add_argument("--yes", action="store_true", help="不询问，直接确认 Dashboard 报回来的公司账号（脚本 / 测试用）")
    lo.set_defaults(fn=cmd_login)
    sub.add_parser("report", help="立即向 Dashboard 上报一次设备元数据").set_defaults(fn=cmd_report)
    sub.add_parser("unlink", help="删除本机的 Dashboard 绑定记录（Dashboard 端在 Dashboard 里解绑）").set_defaults(fn=cmd_unlink)
    rh = sub.add_parser("report-hostname", help="是否向 Dashboard 上报本机主机名（默认上报；off = 上报为空）")
    rh.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")
    rh.set_defaults(fn=cmd_report_hostname)
    ru = sub.add_parser("remote-unbind", help="允许 / 禁止 Dashboard 请求本机解绑遥控器（默认允许；只能减少访问）")
    ru.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")
    ru.set_defaults(fn=cmd_remote_unbind)
    ag = sub.add_parser("agent", help="接哪个 Agent：claude / codex / off；reset = 开一段新对话（重启 serve 生效）")
    ag.add_argument("mode", nargs="?", choices=["claude", "codex", "off", "reset", "status"], default="status")
    ag.add_argument("--dir", help="Agent 的工作目录（默认当前目录）")
    ag.add_argument("--model", help="模型（默认用你自己的设置）")
    ag.add_argument("--unfenced", action="store_true",
                    help="不隔离运行 Agent（不推荐；要输入批准口令）。默认 Agent 在 bubblewrap 里运行，看不到 jarvis 的状态")
    ag.set_defaults(fn=cmd_agent)
    pp = sub.add_parser("passphrase", help="批准口令：set 设置 · change 修改 · reset 忘了（会吊销全部遥控器）· status")
    pp.add_argument("mode", nargs="?", choices=["set", "change", "reset", "status"], default="status")
    pp.set_defaults(fn=cmd_passphrase)
    ap = sub.add_parser("approvals", help="手机批准记录（approvals.log；不含命令内容，只有哈希）")
    ap.add_argument("--verify", action="store_true", help="逐条核对设备签名")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--last", type=int, default=0, help="只看最后 N 条")
    ap.set_defaults(fn=cmd_approvals)
    a = p.parse_args(argv)
    if a.cmd is None:
        cmd_hint()
        return
    a.fn(a)


if __name__ == "__main__":
    main()
