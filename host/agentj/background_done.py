"""P262: completion-driven main-session wakeup, independent of the heartbeat.

Board owns terminal detection; this consumer owns deduplication, one-minute batching
and system provenance. It uses the ordinary adapter Send queue, never a new transport.
Pending work survives serve restarts. No report contents or command lines are stored.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path

from . import preferences, privacy, provenance
from .compose import Send
from .state import _write_private

WINDOW = 60


class CompletionSend(Send):
    def __init__(self, turn, text, ids):
        super().__init__("background_done", secrets.token_urlsafe(16), turn, text=text, by="background_done")
        self.source = provenance.system()
        self.background_done_ids = ids


class Notifier:
    def __init__(self, host):
        self.host = host
        from .completion_recovery import Book
        self.recovery = Book(host)
        self.path = Path(host.st.root) / "busy" / "completion.json"
        self.pending = {}
        self.seen = []
        self.last_sent = 0
        self.inflight = None
        try:
            d = json.loads(self.path.read_text())
            if not isinstance(d, dict):
                raise ValueError("invalid completion state")
            pending, seen, last = d.get("pending", {}), d.get("seen", []), d.get("last_sent", 0)
            if not isinstance(pending, dict) or not isinstance(seen, list) or not isinstance(last, (int, float)):
                raise ValueError("invalid completion state")
            self.pending = pending
            self.seen = seen[-512:]
            self.last_sent = last
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_private(self.path, json.dumps({"pending": self.pending, "seen": self.seen[-512:],
                                             "last_sent": self.last_sent}, ensure_ascii=False).encode())

    def enabled(self):
        return preferences.get(getattr(self.host, "preferences", {}) or {}, "background_done.enabled", True) is not False

    def collect(self):
        board = self.host.busy
        changed = False
        receipts = []
        while board.finished:
            v = board.finished.popleft()
            key = hashlib.sha256(f"{v['key']}:{v.get('generation') or v['since']}".encode()).hexdigest()
            receipts.append(v['key'])
            if key in self.seen:
                continue
            self.seen.append(key)
            self.seen = self.seen[-512:]
            changed = True
            if (self.enabled() or v.get("foreign") is not False) and not v.get("fg"):   # P426: a foreground sub-agent's result is already in the turn
                self.pending[key] = {
                    "label": privacy.redact(v.get("label") or v.get("who") or "后台活", paths=True),
                    "since": v["since"], "ended": v["ended"], "exit_code": v.get("exit_code"),
                    "report": privacy.redact(v.get("report") or "", paths=True), "foreign": v.get("foreign"),
                    "report_path": (v.get("report") or "") if privacy.owner_text(v.get("report") or "") == (v.get("report") or "") else "", "task_id": privacy.owner_text(v["key"]), "generation": v.get("generation"), "ended": v["ended"],
                }
        if not self.enabled() and self.inflight is not None and self.inflight.state == "pending":
            dropped = getattr(self.host, "queue_dropped", None)
            if callable(dropped):
                dropped(self.inflight)  # close the history page as well as cancelling the unsent prompt
            else:
                self.inflight.state = "cancelled"
        if not self.enabled() and self.pending:
            for key, value in self.pending.items():
                if value.get("foreign") is not False:
                    self.recovery.capture(key, value, snapshot=False)
            self.pending = {k:v for k,v in self.pending.items() if v.get("foreign") is not False and k not in self.recovery.rows}
            changed = True
        if changed or receipts:
            self.save()
        # Receipts remain until durable dedup/pending state exists (short jobs need no live scan).
        from .busy import items_dir, _safe
        for key in receipts:
            p = items_dir(self.host.st.root) / "finished" / (_safe(key) + ".json")
            p.unlink(missing_ok=True)

    async def prepare(self):
        import asyncio
        self.collect()
        for key, value in list(self.pending.items()):
            if value.get("foreign") is not False:
                await asyncio.to_thread(self.recovery.capture, key, value)

    def complete(self, send):
        for key in send.background_done_ids:
            value = self.pending.get(key, {})
            if value.get("foreign") is False or key in self.recovery.rows:
                self.pending.pop(key, None)
        self.save()

    def ready(self):
        h = self.host
        a = getattr(h, "agent", None)
        if a is None or h.stopped():
            return False
        if self.inflight is not None and self.inflight.state in ("pending", "delivering"):
            return False
        if a.status != "idle" or not a.q.empty() or getattr(a, "cur_send", None) or getattr(a, "cur_cmd", None):
            return False
        if getattr(h, "desktop_turn", None) is not None:
            return False
        if a.is_down():
            return False
        nb = getattr(a, "native_busy", None)
        if callable(nb) and nb():
            return False
        if any(not x.fut.done() for x in getattr(h, "asks", {}).values()) or h._open_question():
            return False
        if getattr(getattr(h, "elevate", None), "cards", None):
            return False
        if getattr(getattr(h, "scheduler", None), "current_id", None):
            return False
        return True

    def tick(self, now=None):
        self.collect()
        now = time.time() if now is None else now
        a = getattr(self.host, "agent", None)
        if (self.inflight is not None and self.inflight.state == "pending" and a is not None
                and a.status == "idle" and a.q.empty() and getattr(a, "cur_send", None) is None):
            self.inflight.state = "failed"  # adapter returned before a native write: keep evidence and retry
        if not self.enabled() or not self.pending or not self.ready():
            return None
        if self.last_sent and now < self.last_sent + WINDOW:
            return None
        # Bound each prompt; any remainder goes in the next minute's batch.
        for key, value in self.pending.items():
            if value.get("foreign") is not False and key not in self.recovery.rows:
                self.recovery.capture(key, value, snapshot=False)
        known = [(k, v) for k, v in self.pending.items() if v.get("foreign") is False]
        batch = (known or [(k, v) for k, v in self.pending.items() if v.get("foreign") is not False and k in self.recovery.rows])[:16]
        if not batch:
            return None
        zh = getattr(self.host, "lang", "zh") == "zh"
        lines = ["〔Agent J 系统提示〕" if zh else "[Agent J system notice]"]
        for _, v in batch:
            seconds = max(0, int(v["ended"] - v["since"]))
            code = v["exit_code"] if v["exit_code"] is not None else ("未知" if zh else "unknown")
            lines.append(f"后台活『{v['label']}』已结束（用时 {seconds} 秒，退出码 {code}）。" if zh else
                         f"Background job '{v['label']}' ended ({seconds}s, exit code {code}).")
            if v["report"]:
                lines.append(("报告：" if zh else "Report: ") + v["report"])
        lines.append("请读实际报告与证据，再用一两句向主人汇报结论；不凭退出码判断成功。报告缺失也要说明并追查。" if zh else
                     "Read the actual reports and evidence, then report the result to the owner in one or two sentences; "
                     "never infer success from an exit code. Say when a report is missing and investigate.")
        page = self.host.hist_add({"k": "agent", "via": "background_done", "text": ""}, "", "open")
        send = CompletionSend(page["id"], "\n".join(lines), [k for k, _ in batch])
        send.report_paths = [v["report_path"] for _, v in batch if v.get("report_path")]   # #450: own_report scope
        if any(v.get("foreign") is not False for _, v in batch):
            send.source = provenance.normalize(None)    # P570: a non-owner turn's job → the batch's strictest source
        self.host.agent.submit(send)
        self.inflight = send
        self.last_sent = now
        self.save()
        self.host.st.log("background_done_queued", count=len(batch))
        return send


OWN_SHOWN = "own_report: no card (this completion turn's own report, read only)"
_SUFFIX = (".md", ".txt", ".json", ".log", ".csv")


OWN_MAX = 1 << 20


def own_clean(path, workdir) -> bool:   # M3 (ADR-#455 round 1): no-follow walk, one link, ≤ 1 MiB, no secret; worker thread
    import hashlib, os, stat as _stat
    from pathlib import Path
    from .media import _hash_and_scan
    from .peer_guard import SecretIndex
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        f = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(f, "rb") as stream:
            st = os.fstat(stream.fileno())
            if not _stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid() or st.st_size > OWN_MAX:
                return False
            sha, secret = _hash_and_scan(stream.fileno(), st.st_size, True, False)
            data = stream.read(OWN_MAX + 1)
            return not (secret or hashlib.sha256(data).hexdigest() != sha
                        or SecretIndex.build(workdir=os.path.realpath(workdir)).hits(data.decode("utf-8", "replace")))
    except Exception:  # noqa: BLE001 — media.Gone (changed while read), OSError …: cannot tell → the card
        return False
    finally:
        os.close(fd)


def own_report(agent, tool, tool_input, workdir):
    """#450 1b: in a known completion turn, a pure read of the batch's own report (or a plain sibling file in a dedicated
    report folder inside the workspace) and `agentj busy list/show` need no card. Everything else → the ordinary path."""
    send = getattr(agent, "cur_send", None)
    paths = getattr(send, "report_paths", None)
    if (not isinstance(send, CompletionSend) or not paths or provenance.normalize(send.source)["kind"] != "system"
            or provenance.unattended_turn(agent) != "system" or not isinstance(workdir, str) or not isinstance(tool_input, dict)):
        return None
    import os
    from . import danger, handoff
    if tool in ("Bash", "shell", "bash", "exec_command") and not set(tool_input) - {"command", "description", "timeout"}:
        argv = handoff._canon(tool_input.get("command") or "")
        if argv and argv[:2] == ["agentj", "busy"] and (
                argv[2:3] == ["list"] and set(argv[3:]) <= {"--json", "--no-units"}
                or argv[2:3] == ["show"] and len(argv) >= 4 and not argv[3].startswith("-") and set(argv[4:]) <= {"--json", "--no-units"}):
            return "busy"
    t = handoff._read_target(tool, tool_input, workdir)
    if t is None:
        return None
    try:
        root = os.path.realpath(workdir)

        def place(p):          # the real path, only when no component below the workspace is a link and it stays inside
            lex = os.path.normpath(os.path.join(workdir, p))
            rel = os.path.relpath(lex, os.path.normpath(workdir))
            real = os.path.realpath(lex)
            if rel.startswith("..") or os.path.isabs(rel) or real != os.path.join(root, rel) or real == root:
                return None
            return real, rel
        got = place(t[0])
        if got is None or danger.cred_path(got[0]):
            return None
        parts = got[1].split(os.sep)
        for r in paths:
            own = place(r)
            if own is None:
                continue
            if got[0] == own[0]:
                return got[0]                                  # the report itself (even under .jarvis/)
            folder = os.path.dirname(own[0])
            if (os.path.dirname(got[0]) == folder and folder != root and got[0].lower().endswith(_SUFFIX)
                    and not any(x.startswith(".") or x.lower() in ("credentials", "secrets") for x in parts)):
                return got[0]                                  # evidence beside it in a dedicated report folder
    except (OSError, ValueError, TypeError):
        return None
    return None


RETRY = ("后台活已经结束，结果已就绪；这一轮是系统通知，读报告要你在场。回我一句「看结果」我就读给你。[看结果](reply:看结果)",
         "A background job has finished and its result is ready. This turn was a system notice, so reading the report needs "
         "you. Reply \"show the result\" and I will read it to you. [Show the result](reply:Show the result)")


def retry_notice(agent, message):
    """#450 1c: the owner-facing line for a source refusal in a completion turn — None = not one (show the host's reason),
    "" = already told this turn. The model still gets the host's full reason."""
    send = getattr(agent, "cur_send", None)
    if not isinstance(send, CompletionSend) or "cannot request owner approval" not in str(message):
        return None
    if getattr(send, "retry_told", False):
        return ""
    send.retry_told = True
    from .text import pick
    return pick(getattr(agent.host, "lang", "zh"), *RETRY)
