"""P230 numeric metadata and Host execution observations.

Historical targets are thresholds, never measured performance claims. Observer
uses one monotonic clock from Host dispatch to adapter first output/end. Phone
transit and working-indicator timings remain unknown. No model probe is run by
recording, reading, or doctor --latency. No message/model/account text is stored.
"""
from __future__ import annotations
import hashlib
import math
import time

KEEP = 50


WINDOW = 20


SHORT_UNITS = 40                       # 「你好」这类短问题: ≤ 40 characters and no attachment


METRICS = ("t0", "t1", "ttft_first", "ttft_later", "done_short", "done")


TARGETS = {"t0": 1000, "t1": 1000, "ttft_first": 15000, "ttft_later": 8000, "done_short": 20000}


MAX_MS = 3_600_000                     # anything longer is not a latency (a sleeping laptop): dropped


LABELS = {                             # the same words as the phone's Settings → 诊断 (web i18n lat.m.*)
    "zh": {"t0": "手机到电脑", "t1": "显示「在干活」", "ttft_first": "第一次输出（刚启动）", "ttft_later": "第一次输出（之后）",
           "done_short": "短问题回完", "done": "回完（全部）"},
    "en": {"t0": "Phone to computer", "t1": "Shows working", "ttft_first": "First output (just started)",
           "ttft_later": "First output (later)", "done_short": "Short reply done", "done": "Reply done (all)"},
}


def sid_hash(sid) -> str:
    return hashlib.sha256(str(sid).encode()).hexdigest()[:8]


def now_ms() -> int:
    return int(time.time() * 1000)


def _ms(v):
    return v if type(v) is int and 0 <= v <= MAX_MS else None


def make(*, t_recv: int, t_first_out: int | None, t_done: int, harness: str | None, model: str | None, n: int,
         fresh: bool, short: bool, sid=None, result: str = "done") -> dict:
    """One turn's record: only these keys, only numbers / ids / flags (no text can enter)."""
    rec = {"ts": int(t_done), "t_recv": int(t_recv), "t_first_out": int(t_first_out) if t_first_out is not None else None,
           "t_done": int(t_done), "harness": harness if harness in ("claude", "codex", "opencode") else None,
           "model": str(model)[:64] if isinstance(model, str) and model else None, "n": int(n), "fresh": bool(fresh),
           "short": bool(short), "result": result if result in ("done", "stopped", "failed") else "done",
           "id": sid_hash(sid) if sid else None}
    return {k: v for k, v in rec.items() if v is not None}


def values(rows: list[dict], metric: str) -> list[int]:
    out = []
    for r in rows:
        if metric in ("t0", "t1"):
            v = r.get(metric)
        elif metric.startswith("ttft"):
            if r.get("t_first_out") is None or bool(r.get("fresh")) != (metric == "ttft_first"):
                continue
            v = r["t_first_out"] - r["t_recv"]
        else:
            if metric == "done_short" and not r.get("short"):
                continue
            v = r.get("t_done", 0) - r.get("t_recv", 0)
        v = _ms(v)
        if v is not None:
            out.append(v)
    return out


def percentile(xs, p: float):
    """Nearest-rank percentile (p in 0..100) of a list of numbers; None for an empty list."""
    xs = sorted(xs)
    if not xs:
        return None
    k = max(1, math.ceil(p / 100 * len(xs)))
    return xs[min(k, len(xs)) - 1]


def summary(rows: list[dict], window: int = WINDOW) -> dict:
    """{"turns": n, "metrics": {metric: {"n", "p50", "p95", "target", "over"}}} over the newest `window` turns."""
    rows = rows[-window:]
    out = {}
    for m in METRICS:
        xs = values(rows, m)
        p95 = percentile(xs, 95)
        tgt = TARGETS.get(m)
        out[m] = {"n": len(xs), "p50": percentile(xs, 50), "p95": p95, "target": tgt,
                  "over": bool(tgt is not None and p95 is not None and p95 > tgt)}
    return {"turns": len(rows), "metrics": out}


def _sec(ms) -> str:
    return "—" if ms is None else f"{ms / 1000:.1f} s"


def table(summ: dict, lang: str = "zh") -> str:
    """The `agentj doctor --latency` text: one row per metric, `!` = p95 over its target."""
    zh = lang != "en"
    lab = LABELS["zh" if zh else "en"]
    head = (f"最近 {summ['turns']} 轮的响应速度（p50 / p95；目标看 p95）" if zh else
            f"Response speed over the last {summ['turns']} turns (p50 / p95; the target is for p95)")
    lines = [head]
    w = max(len(v) for v in lab.values()) + 2
    for m in METRICS:
        x = summ["metrics"][m]
        tgt = ("≤ " + _sec(x["target"])) if x["target"] else "—"
        mark = "!" if x["over"] else ("✓" if x["p95"] is not None and x["target"] else " ")
        flag = ("  超标" if zh else "  over target") if x["over"] else ""
        lines.append(f"{mark} {lab[m]:<{w}} {_sec(x['p50']):>8} / {_sec(x['p95']):>8}   "
                     f"{('目标' if zh else 'target')} {tgt}  (n={x['n']}){flag}")
    if not summ["turns"]:
        lines.append("还没有记录：从手机发几条消息再看。" if zh else "Nothing recorded yet: send a few messages from the phone.")
    elif not summ["metrics"]["t0"]["n"]:
        lines.append("「手机到电脑」和「显示在干活」由手机在下一条消息里报回；旧版网页不报。" if zh else
                     "Phone to computer and Shows working are reported by the phone with its next message; older pages do not.")
    return "\n".join(lines)


def _sampled(ms: list[int], errors: list[str], n: int) -> dict:
    return {"n": n, "ok": len(ms), "ms": ms, "p50": percentile(ms, 50), "p95": percentile(ms, 95), "errors": errors}


def sample(fn, n: int = 3) -> dict:
    """Call fn() n times, timing each; an exception counts as a failed try (its class name is kept, never its message)."""
    ms, errors = [], []
    for _ in range(max(1, int(n))):
        t = time.monotonic()
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — a probe failing is a result
            errors.append(type(e).__name__)
            continue
        ms.append(int((time.monotonic() - t) * 1000))
    return _sampled(ms, errors, max(1, int(n)))


async def asample(fn, n: int = 3) -> dict:
    """sample() for a coroutine function: `await fn()` n times, one after the other."""
    ms, errors = [], []
    for _ in range(max(1, int(n))):
        t = time.monotonic()
        try:
            await fn()
        except Exception as e:  # noqa: BLE001
            errors.append(type(e).__name__)
            continue
        ms.append(int((time.monotonic() - t) * 1000))
    return _sampled(ms, errors, max(1, int(n)))


def over(metric: str, ms) -> bool:
    """One measurement above its target (a probe marks it yellow)."""
    t = TARGETS.get(metric)
    return bool(t is not None and ms is not None and ms > t)




class Observer:
    """Host execution TTFT, one monotonic clock; no phone transit inference.

    No text/model/account ids are accepted. Missing first output stays unknown.
    Timings start at Host turn dispatch, not at phone send or model invocation.
    """
    def __init__(self, clock=None):
        self.clock = clock or (lambda: time.monotonic_ns() // 1_000_000)
        self.turn = None
        self.n = 0

    def begin(self, *, harness=None, fresh=False, short=False):
        self.n += 1
        self.turn = {'recv': self.clock(), 'first': None, 'harness': harness,
                     'fresh': fresh, 'short': short}

    def first_output(self):
        if self.turn is not None and self.turn['first'] is None:
            self.turn['first'] = self.clock()

    def finish(self, result):
        turn, self.turn = self.turn, None
        if turn is None:
            return None
        return make(t_recv=turn['recv'], t_first_out=turn['first'], t_done=self.clock(),
                    harness=turn['harness'], model=None, n=self.n, fresh=turn['fresh'],
                    short=turn['short'], result=result)


def read(st):
    """Read only the bounded metadata file. No directory creation or active probe."""
    import json
    try:
        path = st.root / 'latency.json'
        with path.open('rb') as f:
            data = f.read(65537)
        if len(data) > 65536:
            return []
        rows = json.loads(data)
        if not isinstance(rows, list):
            return []
        # Only records emitted by Observer, no arbitrary persisted strings.
        clean = []
        for row in rows[-KEEP:]:
            if not isinstance(row, dict):
                continue
            if any(type(row.get(k)) is not int for k in ('t_recv', 't_done', 'n', 'ts')):
                continue
            out = {k: row[k] for k in ('t_recv', 't_done', 'n', 'ts')}
            for k in ('t_first_out', 't0', 't1'):
                if type(row.get(k)) is int:
                    out[k] = row[k]
            for k in ('fresh', 'short'):
                out[k] = row.get(k) is True
            if row.get('harness') in ('claude', 'codex', 'opencode'):
                out['harness'] = row['harness']
            if row.get('result') in ('done', 'failed', 'stopped'):
                out['result'] = row['result']
            clean.append(out)
        return clean
    except (OSError, ValueError, TypeError):
        return []


def add(st, row):
    import json
    rows = (read(st) + [row])[-KEEP:]
    st.write_private(st.root / 'latency.json', json.dumps(rows, separators=(',', ':')).encode())
