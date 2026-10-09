"""Native shared sessions; Agent J-owned OpenCode honors the requested fence.

Claude uses the proven relay messaging and committed Stop state machine.
Native approvals are signed phone requests; pre-execution warnings only tighten
permissions. Owner processes are never terminated on detach. An owned ordinary
Claude PTY supports single-Esc interruption; existing terminals use exact Herdr
session routing. Native permission settings and other hooks remain authoritative.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import signal
import sys
import uuid

from .agent import Agent
from .agent_opencode import OpenCodeAgent, Client, HTTPError, CONNECT_WAIT, to_tool, split_model
from . import privacy, shared_hook, danger
from .shared_state import AgentState
from .agent import hooks_blocked, _bin
from .shared_risk import RiskGuard
from . import shared_opencode_hook

CONTROL_WAIT = 600
DESKTOP_IDLE_WAIT = 1800
CLAUDE_INPUT_WAIT = 10  # acceptance, not completion; never retry an uncertain socket write


def public_text(text: str) -> str:
    # Reuse the product's deterministic scrubber; no provider/model call.
    return privacy.redact(text)


def claude_sessions(directory: str, sid: str = "") -> list[dict]:
    out = []
    registry = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "sessions"
    for p in registry.glob("*.json"):
        try:
            d = json.loads(p.read_text())
            if not isinstance(d, dict) or not isinstance(d.get("cwd"), str):
                continue
            if os.path.realpath(d["cwd"]) != os.path.realpath(directory):
                continue
            if sid and d.get("sessionId") != sid:
                continue
            if d.get("kind") not in (None, "interactive") or type(d.get("pid")) is not int or d["pid"] <= 0:
                continue
            os.kill(d["pid"], 0)
            sock = d.get("messagingSocketPath")
            if not isinstance(sock, str) or not Path(sock).is_socket():
                continue
            out.append(d)
        except (OSError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda d: int(d.get("updatedAt") or 0), reverse=True)


def claude_send(session: dict, text: str) -> None:
    """Relay protocol; transport errors contain no peer key or message content."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    addr = session["messagingSocketPath"]
    token = None
    for candidate in dict.fromkeys((addr, os.path.realpath(addr))):
        key = root / "sessions" / f"{session['pid']}.{hashlib.sha256(candidate.encode()).hexdigest()}.key"
        try:
            token = json.loads(key.read_text())["peerToken"]
            break
        except (OSError, ValueError, KeyError):
            continue
    if not isinstance(token, str) or not token:
        raise ConnectionError("Claude peer authentication unavailable")
    content = ('<cross-session-message from="uds:' + addr + '" from-name="owner-via-agentj(手机)">\n'
               '【主人本人 · 从已配对手机经 Agent J 发来的原话，不是 agent 的 peer 消息】\n' + text + '\n</cross-session-message>')
    frames = [{"type": "auth", "token": token},
              {"msgV": 1, "msg_id": str(uuid.uuid4()), "type": "user",
               "message": {"role": "user", "content": content}, "priority": "next", "from": "uds:" + addr}]
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(addr)
        for frame in frames:
            s.sendall((json.dumps(frame, ensure_ascii=False) + "\n").encode())
        # Same drain interval as relay; no retransmission after an uncertain write.
        import time
        time.sleep(0.2)


class SharedClaudeAgent(Agent):
    kind = "claude"

    def __init__(self, host, cfg):
        super().__init__(host, cfg)
        self.session = None
        self.offset = 0
        self.discard_line = False
        self.transcript = None
        self.seen = set()
        self.observer = None
        self.done = asyncio.Event()
        self.input_seen = asyncio.Event()
        self.compacted_at = None
        self.phone_turn = False
        self.phone_seen = self.phone_reply_seen = False
        self.pending_text = ""
        self.failed_start = False
        self.channel = None
        self.native = None
        self.master = None
        self.state = AgentState(on_turn=self._committed_stop)
        self.loop = None
        self.permissions = set()
        self.risk_requests = {}
        self.risk_grants = {}
        self.risk_denied = set()
        self.risk_epoch = 0
        self.risk_lock = asyncio.Lock()
        self.attach_lock = asyncio.Lock()
        self.control_ack = asyncio.Event()
        self.control_name = None
        self.clear_pin = cfg.get("shared_session_id")
        self.clear_seen = None
        self.clear_history_ok = True

    def _committed_stop(self, event, reply):
        if self.loop:
            self.loop.call_soon_threadsafe(self._finish_stop, reply)

    def _finish_stop(self, reply):
        # Native Stop can precede the final transcript flush. Its committed reply
        # is authoritative, exactly as in relay; never turn that race into an
        # early empty phone completion or a later desktop duplicate.
        if self.transcript and self.transcript.is_file():
            self.read_transcript()
        emit = getattr(self.host, "emit", None)
        if emit:
            emit("shared_stop", agent="claude", phone=self.phone_turn, phone_input=self.phone_turn and self.phone_seen, source="committed-Stop")
        if self.phone_turn and self.phone_seen:
            self.phone_reply_seen = True
            self.host.agent_text(public_text(reply))
            self.done.set()
        else:
            self.host.desktop_text(public_text(reply))
            self.host.desktop_end()

    async def hook_event(self, event):
        if not isinstance(event, dict) or not self.session:
            return {}
        name = event.get("hook_event_name")
        sid = event.get("session_id")
        if name == "SessionStart" and event.get("source") == "clear" and sid != self.session["sessionId"]:
            # /clear changes UUID, but never which desktop process we serve.
            for _ in range(20):
                live = claude_sessions(self.cfg["dir"], sid)
                if any(d["pid"] == self.session["pid"] for d in live):
                    break
                await asyncio.sleep(.05)
            else:
                return {}
            origin = self.clear_pin or self.host.st.agent_session(self.kind + ".clear_from")
            self.host.st.set_agent_session(self.kind + ".clear_from", origin if isinstance(origin, str) else self.session["sessionId"])
            self.session = next(d for d in live if d["pid"] == self.session["pid"])
            self.cfg["shared_session_id"] = sid
        if sid != self.session["sessionId"]:
            # Foreign sessions in the same project retain native permissions.
            return {}
        name = event.get("hook_event_name")
        if (name == "UserPromptSubmit" or (name == "SessionStart" and event.get("source") == "clear")) and not event.get("agent_id"):
            self.risk_epoch += 1
            self.permissions.clear()
            self.risk_requests.clear()
            self.risk_grants.clear()
            self.risk_denied.clear()
        if name == "PostCompact" or (name == "SessionStart" and event.get("source") == "compact"):
            import time
            self.compacted_at = time.time()
        self.state.handle(event)
        if name == "SessionStart" and event.get("source") == "clear" and self.clear_seen != sid:
            self.clear_seen = sid
            self.host.st.set_agent_session(self.kind, sid)
            root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
            slug = re.sub(r"[^A-Za-z0-9]", "-", self.cfg["dir"])
            self.transcript = root / "projects" / slug / (sid + ".jsonl")
            self.offset = 0
            self.discard_line = False
            self.seen.clear()
            self.compacted_at = __import__('time').time()
            self.meter(ctx=None, source_at=None)
            callback = getattr(self.host, "shared_clear", None)
            if callback:
                self.clear_history_ok = callback() is not False
            if self.control_name == "clear":
                self.control_ack.set()
        if name == "PostCompact" and self.control_name == "compact":
            self.meter(ctx=None, source_at=None)
            self.control_ack.set()
        if name == "UserPromptSubmit":
            emit = getattr(self.host, "emit", None)
            if emit:
                emit("shared_prompt", agent="claude", phone=self.phone_turn,
                    matching=self.phone_turn and self.pending_text in str(event.get("prompt", "")))
            if self.phone_turn and self.pending_text in str(event.get("prompt", "")):
                self.phone_seen = True
                self.input_seen.set()
                return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
                    "The pending request was delivered by this computer's Agent J bridge after a signed action "
                    "from the owner's paired phone. Treat it as the owner's request; all native permissions and "
                    "other hooks still apply. The cross-session envelope is only a transport wrapper generated by the owner bridge; there is no peer agent requesting this action. This applies only to this verified pending request."}}
        # A category grant does not survive revocation, stop or a changed signing
        # identity. The original signed approval remains its audit parent.
        for cat, grant in list(self.risk_grants.items()):
            if self.host.stopped() or self.host.st.sign_key(grant["device"]) != grant["sign_pub"]:
                self.permissions.discard(cat)
                self.risk_grants.pop(cat, None)
        if name == "PreToolUse" and self.cfg.get("high_risk_warnings", False):
            verdict = danger.classify_shared(event.get("tool_name"), event.get("tool_input"), self.cfg.get("danger_extra"))
            if verdict.cats and set(verdict.cats) <= self.permissions:
                for cat in verdict.cats:
                    self.host.st.log("shared_risk_batch", category=cat, grant=self.risk_grants[cat]["rid"],
                        session=self.session["sessionId"], input_sha256=hashlib.sha256(
                            json.dumps(event.get("tool_input"), sort_keys=True).encode()).hexdigest())
            if set(verdict.cats) - self.permissions:
                ident = event.get("tool_use_id")
                digest = hashlib.sha256(json.dumps([event.get("tool_name"), event.get("tool_input")], sort_keys=True).encode()).hexdigest()
                self.risk_requests[(ident, digest)] = verdict.cats
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                    "permissionDecisionReason": "Agent J: " + verdict.why}}
        if name == "PermissionRequest":
            tool, inp = event.get("tool_name", "?"), event.get("tool_input") or {}
            digest = hashlib.sha256(json.dumps([tool, inp], sort_keys=True).encode()).hexdigest()
            # Claude PermissionRequest may omit tool_use_id. Digest matches only a
            # pending guard-raised request; this cache never bypasses native asks.
            key = next((k for k in self.risk_requests if k[1] == digest), None)
            cats = self.risk_requests.pop(key, []) if key else []
            epoch = self.risk_epoch
            async with self.risk_lock:
                for cat, grant in list(self.risk_grants.items()):
                    if self.host.stopped() or self.host.st.sign_key(grant['device']) != grant['sign_pub']:
                        self.permissions.discard(cat)
                        self.risk_grants.pop(cat, None)
                if set(cats) & self.risk_denied:
                    answer = {'behavior': 'deny'}
                elif cats and set(cats) <= self.permissions:
                    answer = {"behavior": "allow"}
                else:
                    answer = await self.host.ask(tool, inp, batch=False, risk_scope=bool(cats))
                    if cats and answer.get("behavior") == "allow":
                        grant = answer.get("risk_grant")
                        if self.host.stopped() or (grant and self.host.st.sign_key(grant['device']) != grant['sign_pub']):
                            answer = {'behavior': 'deny'}
                        elif grant and self.risk_epoch == epoch:
                            self.permissions.update(cats)
                            self.risk_grants.update({c: grant for c in cats})
                    elif cats and self.risk_epoch == epoch:
                        self.risk_denied.update(cats)
            decision = {"behavior": "allow"} if answer.get("behavior") == "allow" else {
                "behavior": "deny", "message": "Denied by paired phone or timeout."}
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
        return {}

    async def prepare_channel(self):
        if self.channel:
            return
        if hooks_blocked(self.cfg["dir"]):
            raise ValueError("native Claude hooks disabled")
        self.loop = asyncio.get_running_loop()
        self.channel = shared_hook.Channel(self, shared_hook.channel_path(self.host.st.root, "claude"))
        await self.channel.start()
        shared_hook.install(self.cfg["dir"], self.channel.path)

    async def ordinary_start(self):
        import pty
        await self.prepare_channel()
        exe = _bin("AGENTJ_CLAUDE_BIN", "claude")
        if not exe:
            raise ValueError("Claude binary unavailable")
        master, slave = pty.openpty()
        import fcntl, termios, struct
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        env = {k: v for k, v in os.environ.items() if k not in
               ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_AGENT_SDK_SESSION_ID",
                "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                "CLAUDE_PID", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_EFFORT", "CLAUDE_CODE_EXECPATH")}
        from .proxy import environment
        env = environment(env, self.host.preferences)
        env["TERM"] = "xterm-256color"
        try:
            argv = [exe]
            if self.cfg.get("model"):
                argv += ["--model", self.cfg["model"]]
            self.native = await asyncio.create_subprocess_exec(*argv, cwd=self.cfg["dir"], env=env,
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        finally:
            os.close(slave)
        self.master = master
        os.set_blocking(master, False)
        self.loop.add_reader(master, self._drain_terminal)
        for _ in range(120):
            sessions = claude_sessions(self.cfg["dir"])
            if any(d["pid"] == self.native.pid for d in sessions):
                return
            if self.native.returncode is not None:
                break
            await asyncio.sleep(.25)
        raise ValueError("ordinary Claude startup unconfirmed")

    def _drain_terminal(self):
        # Drain display bytes only; transcript/hooks own all messages and state.
        try:
            os.read(self.master, 65536)
        except (OSError, BlockingIOError):
            if self.loop and self.master is not None:
                self.loop.remove_reader(self.master)

    def is_down(self):
        return self.failed_start

    async def command(self, name, arg):
        from .slash import Result, HELP
        if name == "help":
            return Result(HELP + "\n共享 Claude：clear/compact 等当前回合结束后执行；成功以原生回执确认。 / Shared Claude waits for the desktop turn and confirms native receipts.", "info")
        if name in ("context", "usage", "status", "cost", "model") and not arg:
            from . import claude_statusline
            m = claude_statusline.read(self.host.st.root, (self.session or {}).get("sessionId"), self.compacted_at)
            self.meter(**m)
            if name == "context":
                c = m.get("ctx") or {}
                return Result("上下文 / Context: " + (str(c['pct']) + '%' if c.get('pct') is not None else "—（等待原生读数 / awaiting native measurement）"), "info")
            if name == "usage":
                return Result("5h: " + (str(m['h5']['pct']) + '%' if m.get('h5') else "—") + " · week: " + (str(m['week']['pct']) + '%' if m.get('week') else "—"), "info")
            if name == "cost":
                return Result("花费 / Cost: —（共享状态栏不提供美元花费；终端 /cost 可查看 / use desktop /cost）", "info")
            return Result("Claude · " + str(m.get('model_name') or m.get('model') or '—') + " · " + self.state.status, "info")
        if name not in ("clear", "compact") or (name == "clear" and arg) or any(c in arg for c in '\r\n\x1b\x00'):
            return Result("此控制没有执行：共享终端没有可验证的模型切换/撤销接口；用终端 /model 或 /resume。 / Not executed: use native /model or /resume; no verified shared control receipt.", "refused")
        if self.session is None and not await self.attach():
            return Result("没清成 / No change: Claude 会话未连接 / session unavailable", "error")
        # The phone queue already serializes phone turns. Also wait for DESKTOP
        # turns, which do not occupy that queue. No Esc, no approval answers.
        import time
        deadline = time.monotonic() + DESKTOP_IDLE_WAIT
        while True:
            if self.halting or self.host.stopped() is True:
                return Result("已取消，未发送控制。 / Cancelled; control not sent.", "error")
            live = claude_sessions(self.cfg['dir'], self.session['sessionId'])
            if not live or live[0]['pid'] != self.session['pid']:
                return Result("没清成 / No change: 所选桌面会话已离线 / selected desktop session offline", "error")
            if self.state.status == 'idle' and live[0].get('status') in ('idle', 'done'):
                break
            if time.monotonic() >= deadline:
                return Result("没清成 / No change: 等待桌面回合结束超时，未发送命令 / desktop idle timeout; command not sent", "error")
            await asyncio.sleep(.1)
        self.control_ack.clear()
        self.control_name = name
        try:
            text = '/' + name + (' ' + arg if arg else '')
            if self.native and self.native.pid == self.session['pid'] and self.master is not None:
                os.write(self.master, text.encode())
                await asyncio.sleep(.1)
                os.write(self.master, b'\r')
            else:
                from .shared_controls import send
                why = await asyncio.to_thread(send, self.session, text)
                if why != 'sent':
                    return Result("没清成 / No change: 桌面终端未提供安全输入通道（" + why + "）；请在所选终端运行 /" + name + "。 / Run the command in the selected terminal.", "error")
            try:
                await asyncio.wait_for(self.control_ack.wait(), CONTROL_WAIT)
            except asyncio.TimeoutError:
                return Result("没清成：未收到原生回执，结果未确认；请核实桌面，勿重复发送。 / No confirmed change: native receipt timeout; check desktop before retrying.", "error")
            if self.halting or self.host.stopped() is True:
                return Result("控制结果尚未确认，已停止等待；请核实电脑。 / Control unconfirmed; waiting stopped, check the desktop.", "error")
            self.read_statusline()
            if name == 'clear' and not self.clear_history_ok:
                return Result("桌面已清空，但手机历史归档失败，旧页仍保留；请检查本机磁盘。 / Desktop cleared, but phone history archive failed; check local disk.", "error")
            return Result("已清空，旧历史已归档；水位等待新会话实际读数。 / Cleared; history archived, awaiting the new session measurement." if name == 'clear' else "已压缩；水位等待压缩后实际读数。 / Compacted; awaiting the post-compaction measurement.", sep=name == 'clear')
        finally:
            self.control_name = None

    async def apply_model(self, model, effort, default=False):
        return "unsupported"

    async def attach(self):
        async with self.attach_lock:
            return await self._attach_selected()

    async def _attach_selected(self):
        pinned = self.cfg.get("shared_session_id", "")
        if pinned and pinned == self.host.st.agent_session(self.kind + ".clear_from"):
            self.cfg["shared_session_id"] = self.host.st.agent_session(self.kind) or pinned
        sessions = claude_sessions(self.cfg["dir"], self.cfg.get("shared_session_id", ""))
        if not sessions and not self.cfg.get("shared_session_id") and self.native is None:
            await self.ordinary_start()
            sessions = claude_sessions(self.cfg["dir"])
        if len(sessions) != 1:
            if not self.failed_start:
                self.local_fail("共享 Claude 会话不存在或有多个匹配：在电脑指定 session id。 / Choose one live Claude session ID.")
            self.failed_start = True
            return False
        self.session = sessions[0]
        await self.prepare_channel()
        sid = self.session.get("sessionId")
        if not isinstance(sid, str) or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", sid):
            return False
        root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        slug = re.sub(r"[^A-Za-z0-9]", "-", self.cfg["dir"])
        self.transcript = root / "projects" / slug / (sid + ".jsonl")
        if not self.transcript.is_file() and not (self.native and self.native.pid == self.session["pid"]):
            if not self.failed_start:
                self.local_fail("Claude 会话的 transcript 不可读，未投递。 / Claude transcript unavailable.")
            self.failed_start = True
            return False
        self.offset = self.transcript.stat().st_size if self.transcript.exists() else 0
        self.host.st.set_agent_session(self.kind, sid)
        self.failed_start = False
        return True

    def start(self):
        super().start()
        self.observer = asyncio.create_task(self.observe())

    async def observe(self):
        while True:
            try:
                if self.session is None:
                    await self.attach()
                if self.transcript is not None and self.transcript.is_file():
                    self.read_transcript()
                self.read_statusline()
            except (OSError, ValueError):
                self.failed_start = True
                self.set_status("down")
                self.done.set()
            await asyncio.sleep(5 if self.session is None else 0.25)

    def read_statusline(self):
        from . import claude_statusline
        sid = (self.session or {}).get("sessionId")
        self.meter(**claude_statusline.read(self.host.st.root, sid, self.compacted_at))

    def read_transcript(self):
        with self.transcript.open("rb") as f:
            if f.seek(0, 2) < self.offset:
                self.offset = 0
                self.discard_line = False
            f.seek(self.offset)
            # Bounded per tick; never read tool results or child transcripts.
            for _ in range(100):
                line = f.readline(1024 * 1024 + 1)
                if not line:
                    break
                if self.discard_line or len(line) > 1024 * 1024:
                    # Advance through oversized tool-result records in bounded
                    # chunks; otherwise one long line stalls every future turn.
                    self.offset = f.tell()
                    self.discard_line = not line.endswith(b"\n")
                    continue
                if not line.endswith(b"\n"):
                    break
                self.offset = f.tell()
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                ident = item.get("uuid")
                if ident and ident in self.seen:
                    continue
                if ident:
                    self.seen.add(ident)
                msg = item.get("message") or {}
                role = item.get("type")
                blocks = msg.get("content", "")
                text = blocks if isinstance(blocks, str) else "\n".join(
                    b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
                if not text or role not in ("user", "assistant"):
                    continue
                if role == "user":
                    from .transcript_input import human_input
                    if not human_input(item, blocks, text):
                        continue
                    if any('from-name="' + name + '"' in text for name in ('owner-via-agentj(手机)', 'Agent-J-phone')) or (item.get("origin") or {}).get("name") in ('owner-via-agentj(手机)', 'Agent-J-phone'):
                        if self.phone_turn and self.pending_text in text:
                            self.phone_seen = True
                            self.input_seen.set()
                        continue
                    self.host.desktop_input(public_text(text))
                # Assistant prose is emitted only by the committed Stop payload.
                # Transcript timing is not a completion/ownership signal.
        live = claude_sessions(self.cfg["dir"], self.session["sessionId"])
        if not live and not any(d['pid'] == self.session['pid'] for d in claude_sessions(self.cfg['dir'])):
            self.failed_start = True
            self.set_status("down")
            self.done.set()
            self.session = None

    async def turn(self, text):
        if self.session is None and not await self.attach():
            return
        # Cross-session messages need not emit UserPromptSubmit in every native
        # version; the phone turn boundary must also revoke prior risk grants.
        self.risk_epoch += 1
        self.permissions.clear()
        self.risk_requests.clear()
        self.risk_grants.clear()
        self.risk_denied.clear()
        self.done.clear()
        self.input_seen.clear()
        self.phone_turn = True
        self.phone_seen = self.phone_reply_seen = False
        self.pending_text = text
        try:
            await self.deliver(lambda: asyncio.to_thread(claude_send, self.session, text))
            try:
                await asyncio.wait_for(self.input_seen.wait(), CLAUDE_INPUT_WAIT)
            except asyncio.TimeoutError:
                # Socket write is not native acceptance. Busy desktop tools or a
                # held inbound message can both delay it: never claim certainty.
                if self.cfg.get("language", "zh") == "en":
                    notice = ("Claude has not acknowledged this message; it may be held for desktop approval. "
                              "Run agentj config claude-inbound on. Repository/managed policy can still block it. "
                              "Check the desktop before resending: the original may still arrive.")
                else:
                    notice = ("电脑上的 Claude Code 尚未接收这条消息，可能拦下等你批准。运行 "
                              "agentj config claude-inbound on 后不再按默认策略拦截；项目/组织策略仍可能拦截。"
                              "请先在电脑核实，别重复发送，原消息仍可能送达。")
                self.fail_notice(notice)
                return
            # Only a committed native Stop belonging to this phone input ends
            # the turn; registry idle and transcript flush timing never do.
            await asyncio.wait_for(self.done.wait(), 300)
        except asyncio.TimeoutError:
            self.fail_notice("共享 Claude 未确认回合完成，请在电脑核实。 / Turn completion unconfirmed; check the desktop.")
        finally:
            self.phone_turn = False

    async def halt(self, clear_queue=True):
        self.halting = True
        if self.control_name:
            self.control_ack.set()  # wake the wait; halting prevents a success claim
        busy = self.status == "working" or self.state.status == "working"
        if clear_queue:
            while not self.q.empty():
                item = self.q.get_nowait()
                if getattr(self.host, "queue_dropped", None):
                    self.host.queue_dropped(item)
        # Esc must never answer a native approval dialog.
        if self.state.status == "waiting":
            self.host.agent_notice("请先处理审批卡片。 / Resolve the approval card first.")
            return False
        if not busy or not self.session:
            return busy
        if self.native and self.native.pid == self.session["pid"] and self.master is not None:
            os.write(self.master, b"\x1b")
            self.done.set()
            return True
        # Relay's validated Herdr route, bound to an exact session id.
        from .shared_interrupt import interrupt
        result = await asyncio.to_thread(interrupt, self.session)
        if result == "sent":
            self.done.set()
        else:
            self.host.agent_notice("此电脑终端未提供安全中断通道。 / Desktop interrupt unavailable.")
        return result == "sent"

    async def stop(self):
        self.meter(model=None, model_name=None, effort=None, ctx=None, h5=None, week=None, source_at=None)
        # Dispose only the actor we launched. Never call Agent.halt on an owner TUI.
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.observer:
            self.observer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.observer
        if self.channel:
            await self.channel.stop()
        if self.native and self.native.returncode is None:
            self.native.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.native.wait(), 5)
            if self.native.returncode is None:
                self.native.kill()
                await self.native.wait()
        if self.master is not None:
            self.loop.remove_reader(self.master)
            os.close(self.master)
            self.master = None


class _ExternalServer:
    # Lifecycle sentinel for inherited SSE/turn code, never an OS subprocess.
    returncode = None


def owner_password_note() -> str:
    """P64: how the owner hands their OpenCode server's password to Agent J — through the phone's secret card into the service
    environment, never by Agent J reading another process's environment (Leo / Jarvis 2026-10-06)."""
    from . import service
    try:
        env_file = service.env_file(service.name())
    except Exception:  # noqa: BLE001 — the note must never fail
        env_file = "<Agent J 服务的环境文件 / the Agent J service env file>"
    cmd = (f"agentj secret request --name OPENCODE_SERVER_PASSWORD --purpose '你电脑上 OpenCode server 的密码' "
           f"--dest env:{env_file}#OPENCODE_SERVER_PASSWORD")
    return ("你电脑上开着的 OpenCode server 要密码，Agent J 还没有这把密码（它不会去读别的程序的环境变量）。在电脑上运行："
            f"`{cmd}`，在手机的密钥卡片里贴上你启动 server 时设的 OPENCODE_SERVER_PASSWORD（用户名不是 opencode 的话，再用同样的方法存"
            " OPENCODE_SERVER_USERNAME），然后运行 `agentj service restart`。"
            f" / Your OpenCode server needs a password Agent J does not have (it never reads another program's environment). Run `{cmd}`,"
            " paste the server's OPENCODE_SERVER_PASSWORD on the phone's secret card (OPENCODE_SERVER_USERNAME too if it is not"
            " \"opencode\"), then `agentj service restart`.")


class SharedOpenCodeAgent(OpenCodeAgent):
    """Existing loopback server + exact session; native rules are never PATCHed."""
    def __init__(self, host, cfg):
        super().__init__(host,cfg)
        self.risk=RiskGuard(self)
        self.risk_channel=None
        self.owned_server=False   # P59: True once Agent J started this ordinary server itself (restartable, same session)

    async def prepare_risk(self):
        if not self.cfg.get('high_risk_warnings',False) or self.risk_channel:
            return
        self.risk_channel=shared_hook.Channel(self.risk,shared_hook.channel_path(self.host.st.root,'opencode'))
        await self.risk_channel.start()
        shared_opencode_hook.install(self.cfg['dir'],self.risk_channel.path)

    async def stop(self):
        await super().stop()
        if self.risk_channel:
            await self.risk_channel.stop()

    # P63: our own ordinary server (owned_server) follows the installed OpenCode — v2 = shared_opencode2 — and, being Agent
    # J's child, takes the owner's model choice and the phone's slash commands; an owner server stays text-only.
    def _own_protocol(self):
        return self.owned_server

    def _switch(self, version):
        from .shared_opencode2 import switch_shared
        return switch_shared(self, version)

    async def command(self, name, arg):
        initial = getattr(self, "attach_task", None)
        if initial is not None and not initial.done():
            await initial
        if self.v2:
            return await self.command(name, arg)      # switched to shared_opencode2 while starting
        if self.owned_server:
            return await OpenCodeAgent.command(self, name, arg)
        from .slash import Result
        if name in ("compact", "context", "cost", "usage", "status", "help"):
            res = await OpenCodeAgent.command(self, name, arg)
            if name == 'compact' and res.kind == 'ok':
                self.meter(ctx=None)
                with contextlib.suppress(OSError, ValueError, HTTPError, asyncio.TimeoutError):
                    await self.context_meter(include_quota=False)
            return res
        return Result("没有执行：附加的 OpenCode 服务器不提供切换桌面会话的接口；用桌面 /new 新建、/model 换模型，再指定新 session id。 / Not executed: attached server cannot switch the desktop session; use /new or /model there, then select its session ID.", "error")

    async def apply_model(self, model, effort, default=False):
        if self.owned_server:
            return await super().apply_model(model, effort, default)
        return "unsupported"

    async def _session_ok(self):
        """Owned serve: the session agent.json names (after /clear or 「撤销清空」 it changed), else a new ordinary one."""
        if not self.owned_server:
            return await super()._session_ok()
        if self.sid is not None and self.sid == self.host.st.agent_session(self.kind):
            return True
        self.cfg["shared_session_id"] = self.host.st.agent_session(self.kind) or ""
        try:
            await self._prepare()
            return True
        except (OSError, HTTPError, ValueError, KeyError, TypeError, AttributeError, asyncio.TimeoutError) as e:
            self.fail_notice(f"OpenCode 没能打开这段对话（{type(e).__name__}）：这条没有交给它。")
            return False

    def launch_argv(self, argv):
        from .provider_runtime import shared_launch
        return shared_launch(self, argv)

    async def _prepare(self):
        # Native server startup keeps native rules: no independent session_rules
        # or permission PATCH. Owned main identity is supplied on each phone turn.
        self.connected_at_start = None                # P63: which providers this owned serve has a key for (no_key class)
        if self.owned_server:
            with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
                st, pv = await self.client.request("GET", "/provider")
                if st == 200 and isinstance(pv, dict) and isinstance(pv.get("connected"), list):
                    self.connected_at_start = [x for x in pv["connected"] if isinstance(x, str)]
        sid = self.cfg.get("shared_session_id")
        if self.owned_server and isinstance(sid, str) and sid:
            # P59 (A167): our own server restarted (credentials / proxy changed): the same ordinary session again
            status, session = await self.client.request("GET", f"/session/{sid}")
            if (status == 200 and isinstance(session, dict) and session.get("id") == sid
                    and os.path.realpath(session.get("directory") or "") == os.path.realpath(self.cfg["dir"])):
                self.sid, self.rules = sid, []
                self.host.st.set_agent_session(self.kind, sid)
                await self._prime()
                return
        status, session = await self.client.request("POST", "/session", {"title": "Agent J shared"})
        if status != 200 or not isinstance(session, dict) or not isinstance(session.get("id"), str):
            raise HTTPError("ordinary session creation failed")
        if os.path.realpath(session.get("directory") or "") != os.path.realpath(self.cfg["dir"]):
            raise HTTPError("ordinary session has wrong project")
        self.sid = session["id"]
        self.rules = []
        self.cfg["shared_session_id"] = self.sid
        self.host.st.set_agent_session(self.kind, self.sid)
        await self._prime()

    async def _spawn(self):
        port = self.cfg.get("shared_opencode_port")
        if not port and (not self.cfg.get("shared_session_id") or self.owned_server):
            # An explicit shared experiment without an owner session starts an
            # ordinary native server/session. Native config and permissions stay
            # untouched. A stale explicit owner selector never falls through here.
            self.owned_server = True
            if not await self._installed_v2():        # P63: the v1 plugin is not a v2 plugin (shared_opencode2)
                await self.prepare_risk()
            return await super()._spawn()
        if type(port) is not int or not 1 <= port <= 65535:
            self.local_fail("指定电脑 OpenCode server 的 loopback 端口。 / Set the existing OpenCode loopback port.")
            return False
        # Owner's server password is read from its native environment, never config
        # or phone input. Empty supports a native unauthenticated loopback server.
        self.client = Client(port, os.environ.get("OPENCODE_SERVER_PASSWORD", ""),
                             os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"))
        self.proc = _ExternalServer()
        self.owner_unauthorized = False
        if await self._owner_v2():
            # P64: the owner's v2 server is attached (text, cards, desktop mirroring) by shared_opencode2's owner class
            from .shared_opencode2 import OwnerOpenCodeV2Agent
            self.proc = None
            self.__class__ = OwnerOpenCodeV2Agent
            self.host.st.log("agent_protocol", agent=self.kind, version="v2", owner=True)
            return await self._spawn()
        if self.owner_unauthorized:                   # P64: the owner's server wants a password Agent J does not have
            self.proc = None
            self.failed_start = True
            self.local_fail(owner_password_note())
            return False
        await self.prepare_risk()                     # the v1 pre-execution plugin: v1 owner servers only
        try:
            await self._attach()
            if self.risk_channel:
                status, busy=await self.client.request('GET','/session/status')
                if status != 200 or not isinstance(busy,dict) or any(
                    (v or {}).get('type') != 'idle' for v in busy.values()):
                    raise HTTPError('native server busy; guard reload refused')
                # Native instance reload retains persisted session ids and owner
                # permissions. Never reload an instance with another active turn.
                status,_=await self.client.request('POST','/instance/dispose',{})
                if status not in (200,204):
                    raise HTTPError('native plugin reload unavailable')
                await self._attach()
                await asyncio.wait_for(self.risk.ready.wait(),CONNECT_WAIT)
            await self._prime()
            self._bg(self._events(self.proc))
            await asyncio.wait_for(self.connected.wait(), CONNECT_WAIT)
            await self._resync()
        except (OSError, ValueError, HTTPError, asyncio.TimeoutError):
            self.proc = None
            self.failed_start = True
            self.fail_notice("不能附着电脑 OpenCode server/session。 / Cannot attach the desktop OpenCode server/session.")
            return False
        return True

    async def _installed_v2(self):
        from .harness import version_of, opencode_v2
        exe = _bin("AGENTJ_OPENCODE_BIN", "opencode")
        return bool(exe) and opencode_v2(await asyncio.to_thread(version_of, exe))

    async def _owner_v2(self):
        """The owner's server speaks v2 (its legacy routes answer HTML; the API is /api/*): OwnerOpenCodeV2Agent attaches it."""
        try:
            status, info = await self.client.request("GET", "/api/info")
        except (OSError, ValueError, HTTPError, asyncio.TimeoutError):
            return False
        self.owner_unauthorized = status == 401
        return status == 200 and isinstance(info, dict) and isinstance(info.get("version"), str) \
            and info["version"].lstrip("v").startswith("2")

    async def _attach(self):
        sid = self.cfg.get("shared_session_id")
        if not isinstance(sid, str) or not re.fullmatch(r"ses[A-Za-z0-9_-]+", sid):
            raise HTTPError("exact session id required")
        status, session = await self.client.request("GET", f"/session/{sid}")
        if status != 200 or not isinstance(session, dict) or session.get("id") != sid:
            raise HTTPError("session unavailable")
        if os.path.realpath(session.get("directory") or "") != os.path.realpath(self.cfg["dir"]):
            raise HTTPError("wrong working directory")
        self.sid = sid
        self.rules = []
        self.host.st.set_agent_session(self.kind, sid)

    async def _prime(self):
        status, messages = await self.client.request("GET", f"/session/{self.sid}/message")
        if status != 200 or not isinstance(messages, list):
            raise HTTPError("messages unavailable")
        self.meter()  # P98: attached native identity, even without model/quota data.
        # Existing history is not replayed as fresh phone output.
        self.desktop_messages = set()
        self.message_roles = {}
        self.phone_messages = set()
        self.desktop_turn = False
        for message in messages:
            info = message.get("info") or {}
            self.desktop_messages.add(info.get("id"))
            self.message_roles[info.get("id")] = info.get("role")
            self.emitted.update(p.get("id") for p in message.get("parts") or [])

    def start(self):
        super().start()
        self.attach_task = asyncio.create_task(self._spawn())
        self.tasks.add(self.attach_task)
        self.attach_task.add_done_callback(self.tasks.discard)

    def _on_perm(self, req):
        # Reconnection calls this directly with /permission's server-wide list,
        # bypassing on_event. Keep that path confined to the selected session too.
        if req.get("sessionID") != self.sid:
            return
        super()._on_perm(req)

    def on_event(self, ev):
        p = ev.get("properties") or {}
        if ev.get("type") == "message.updated":
            info = p.get("info") or {}
            if info.get("sessionID") != self.sid:
                return
            mid = info.get("id")
            self.message_roles[mid] = None if info.get("synthetic") or info.get("ignored") else info.get("role")
        if ev.get("type") == "message.part.updated":
            part = p.get("part") or {}
            mid = part.get("messageID")
            if part.get("sessionID") == self.sid and self.message_roles.get(mid) == "user":
                if part.get("type") == "text" and mid not in self.desktop_messages:
                    from .transcript_input import human_input
                    if not human_input(part, [part], part.get("text")):
                        return
                    self.desktop_messages.add(mid)
                    if mid not in self.phone_messages:
                        self.desktop_turn = True
                        self.risk.reset(mid)
                        self.host.desktop_input(public_text(part.get("text") or ""))
                return
        if ev.get("type") in ("permission.asked", "question.asked") and p.get("sessionID") != self.sid:
            return
        if ev.get("type") in ("permission.replied", "question.replied", "question.rejected"):
            # Desktop is a legitimate native approver. Resolve phone cards without
            # answering again, and never treat its native decisions as tampering.
            if p.get("sessionID") != self.sid:
                return
            rid = p.get("requestID")
            for pending in (self.pending, self.questions):
                f = pending.get(rid)
                if f and not f.done():
                    f.set_result(True)
            return
        if ev.get("type") in ("session.idle", "session.status") and p.get("sessionID") == self.sid:
            if ev.get("type") == "session.idle" or (p.get("status") or {}).get("type") == "idle":
                self.host.desktop_end()
                self.desktop_turn = False
        super().on_event(ev)

    def _text_part(self, part):
        if not isinstance(part, dict) or self.message_roles.get(part.get("messageID")) != "assistant":
            return
        if part.get("sessionID") != self.sid or part.get("type") != "text" or not (part.get("time") or {}).get("end"):
            return
        if part.get("id") in self.emitted or part.get("synthetic") or part.get("ignored"):
            return
        self.emitted.add(part.get("id"))
        if self.quiet:
            return
        text = public_text(part.get("text") or "")
        if text:
            (self.host.desktop_text if self.desktop_turn else self.host.agent_text)(text)

    async def turn(self, text):
        initial = getattr(self, "attach_task", None)
        if initial is not None and not initial.done():
            if not await initial:
                return
        if self.v2:                 # P63: switched to shared_opencode2 while starting
            return await self.turn(text)
        await self._creds_check()   # P59: only our own server (owned_server); never the owner's attached one
        if self.proc is None and not await self._spawn():
            return
        if self.v2:
            return await self.turn(text)
        if self.owned_server and not await self._session_ok():
            return
        if self.risk_channel:
            await asyncio.wait_for(self.risk.ready.wait(),CONNECT_WAIT)
        self.risk.reset()
        self.turn_done = asyncio.Event()
        self.turn_progress = __import__("time").monotonic()
        self.saw_busy = False
        self.provider_fail_noted = self.restart_after_turn = False
        mid = "msg" + uuid.uuid4().hex
        self.phone_messages.add(mid)
        self.turn_proc = self.proc
        body = {"messageID": mid, "parts": [{"type": "text", "text": text}]}
        from . import main_identity
        main = self.owned_server and self.persist and not self.cfg.get('_workflow_ceo')
        # Owned main gets its full constitution; an attached owner keeps native
        # identity and receives only this phone turn's language instruction.
        body['system'] = main_identity.prompt(self.cfg) if main else main_identity.LANGUAGE_LINE[main_identity.language_of(self.cfg)]
        m = split_model(self.cfg.get("model")) if self.owned_server else None
        if m:                       # P63: the owner's `agentj agent opencode --model` / phone /model on our own server
            body["model"] = m
        async def post():
            status, _ = await self.client.request("POST", f"/session/{self.sid}/prompt_async", body)
            if status not in (200, 204):
                raise HTTPError("prompt refused")
            return True
        await self.deliver(post)
        if main:
            if not hasattr(self, 'identity_sent'):
                self.identity_sent = {}
            self.identity_sent[self.sid] = body['system']
            main_identity.audit(self.cfg, self.kind, self.host.st, self.sid)
        await self._wait_turn()
        if self.owned_server and self.persist and self.collect is None:
            self.refresh_usage()
        if self.restart_after_turn and self.owned_server and self.proc and not isinstance(self.proc, _ExternalServer):
            self.restart_after_turn = False       # P60/P63: a key / provider failure — the next message gets a fresh serve
            self.host.st.log("agent_restart", agent=self.kind, reason="provider_failure")
            await self._kill(self.proc)
            self.proc = self.client = None

    async def _catch_up(self):
        if not self.client or not self.sid:
            return
        status, messages = await self.client.request("GET", f"/session/{self.sid}/message")
        if status != 200 or not isinstance(messages, list):
            return
        for message in messages:
            info = message.get("info") or {}
            mid = info.get("id")
            self.message_roles[mid] = None if info.get("synthetic") or info.get("ignored") else info.get("role")
            for part in message.get("parts") or []:
                if info.get("role") == "user":
                    self.on_event({"type": "message.part.updated", "properties": {"part": part}})
                else:
                    self._text_part(part)

    async def _kill(self, p):
        if not isinstance(p, _ExternalServer):
            return await super()._kill(p)
        # Connection disposal only, never the existing server's process tree.
        p.returncode = 0
        if self.proc is p:
            self.proc = None

    async def halt(self, clear_queue=True):
        busy = self.status == "working"
        if clear_queue:
            while not self.q.empty():
                item = self.q.get_nowait()
                if getattr(self.host, "queue_dropped", None):
                    self.host.queue_dropped(item)
        await self.interrupt_request(self.proc)
        if self.turn_done:
            self.turn_done.set()
        return busy


def make_shared(host, cfg):
    if cfg.get("high_risk_warnings", False) and cfg["kind"] not in ("claude", "codex", "opencode"):
        return UnavailableSharedAgent(host, cfg)
    if cfg["kind"] == "claude":
        return SharedClaudeAgent(host, cfg)
    if cfg["kind"] == "opencode":
        return SharedOpenCodeAgent(host, cfg)
    if cfg["kind"] == "codex":
        from .shared_codex import SharedCodexAgent
        return SharedCodexAgent(host, cfg)
    return UnavailableSharedAgent(host, cfg)


class UnavailableSharedAgent(Agent):
    def __init__(self, host, cfg):
        super().__init__(host, cfg)
        self.kind = cfg["kind"]
    async def turn(self, text):
        self.local_fail("共享模式的高危执行前拦截尚未验证，未投递；Codex 接续也尚未完成。 / Shared pre-execution guard and Codex continuation are not ready.")
