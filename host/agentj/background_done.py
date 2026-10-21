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
            if self.enabled() and not v.get("fg"):   # P426: a foreground sub-agent's result is already in the turn
                self.pending[key] = {
                    "label": privacy.redact(v.get("label") or v.get("who") or "后台活", paths=True),
                    "since": v["since"], "ended": v["ended"], "exit_code": v.get("exit_code"),
                    "report": privacy.redact(v.get("report") or "", paths=True),
                }
        if not self.enabled() and self.inflight is not None and self.inflight.state == "pending":
            dropped = getattr(self.host, "queue_dropped", None)
            if callable(dropped):
                dropped(self.inflight)  # close the history page as well as cancelling the unsent prompt
            else:
                self.inflight.state = "cancelled"
        if not self.enabled() and self.pending:
            self.pending.clear()
            changed = True
        if changed or receipts:
            self.save()
        # Receipts remain until durable dedup/pending state exists (short jobs need no live scan).
        from .busy import items_dir, _safe
        for key in receipts:
            p = items_dir(self.host.st.root) / "finished" / (_safe(key) + ".json")
            p.unlink(missing_ok=True)

    def complete(self, send):
        for key in send.background_done_ids:
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
        batch = list(self.pending.items())[:16]
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
        self.host.agent.submit(send)
        self.inflight = send
        self.last_sent = now
        self.save()
        self.host.st.log("background_done_queued", count=len(batch))
        return send
