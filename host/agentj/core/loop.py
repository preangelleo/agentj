"""The agent loop (AGENTJ-CORE-SPEC 2.2 `core/loop` + `core/subagent`; Pi `pi-agent-core` agent-loop + mini-swe-agent):
model → tool calls → the gate → the toolbox → results → model, until the model answers without tools.

Step boundary (12.2 / O4 / O7): before every model call the loop reads `control` (stop) and `inbox` (appended instructions);
a stop or a spent budget (wall clock / tokens, 12.9 「先判后调模型」) gets ONE last call without tools that must write the
(partial) report, so there is always a report. Tool calls of one step run in parallel when all are read-only or `task`
(Pi), else in order. Old tool outputs are trimmed when the conversation nears the model's window (deepseek-harness result
trimming); still too big → a summary compaction (compact.py, P431 C5), and once more after a context-overflow error. Tool output is scanned for credentials (privacy.credentials) before the model sees it.

`task` (12.1 sub-agent; omp #3): an isolated child loop with its own toolbox and session file, at most `max_parallel` at a
time per CEO, no `task` / `dispatch` of its own (D11), the same gate (order-wide refusals, C14) with the host-built origin
`… › sub-agent「<description>」#n`; only its final message (≤ SUB_MAX tokens) returns, optionally checked against an
output_schema (ltschema's validator).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from . import compact as compaction, llm, prompt
from .tools import READ_ONLY

SUB_MAX = 2000
TRIM_KEEP = 6
TOOL_OUT_MAX = 60_000

TASK_SPEC = {"type": "function", "function": {
    "name": "task",
    "description": "Run a sub-agent on a self-contained piece of work; several task calls in one step run in parallel. "
                   "Returns only its final message.",
    "parameters": {"type": "object", "properties": {
        "description": {"type": "string", "description": "3-6 word label"},
        "prompt": {"type": "string", "description": "Complete instructions; it sees nothing else"},
        "output_schema": {"type": "object", "description": "Optional JSON Schema for its final JSON"}},
        "required": ["description", "prompt"]}}}


def _replay(m: dict, r: dict) -> dict:
    """Keep what only the producing model may get back: Responses output items (C9), Anthropic content blocks (C1)."""
    if r.get("response_items"):
        m.update(_response_items=r["response_items"], _response_model=r["model"])
    if r.get("anthropic_blocks"):
        m.update(_anthropic_blocks=r["anthropic_blocks"], _anthropic_model=r["model"])
    return m


def _redact(text: str) -> str:
    try:
        from ..privacy import credentials
        spans = credentials(text)
    except Exception:  # noqa: BLE001 — the scanner failing never shows the raw text
        return "[output withheld: the credential scan failed]"
    for _, _, s, e in reversed(spans):
        text = text[:s] + "[已打码]" + text[e:]
    return text


def _cap(text: str, limit_tokens: int, where: str) -> str:
    if prompt.tokens(text) <= limit_tokens:
        return text
    cut = text[:limit_tokens * 3]
    while prompt.tokens(cut) > limit_tokens and len(cut) > 200:
        cut = cut[: int(len(cut) * 0.9)]
    return cut + f"\n[… cut at {limit_tokens} tokens; the full text is in {where}]"


class Loop:
    def __init__(self, *, preset, system: str, specs: list[dict], gate, toolbox=None, session=None, environ=None,
                 call=None, max_steps: int = 80, budget_tokens: int | None = None, deadline: float | None = None,
                 inbox=None, control=None, on_event=None, extra=None, who: str | None = None, on_delta=None,
                 auto_compact: bool = True, on_compact=None, mcp=None):
        self.preset, self.system, self.specs, self.gate, self.toolbox = preset, system, specs, gate, toolbox
        self.session, self.environ, self.call = session, environ, call or (lambda *a, **k: llm.chat(*a, **k))
        self.max_steps, self.budget_tokens, self.deadline = max_steps, budget_tokens, deadline
        self.inbox, self.control = inbox or (lambda: []), control or (lambda: None)
        self.on_event = on_event or (lambda *a, **k: None)
        self.extra = extra or {}
        self.who = who
        self.on_delta = on_delta              # C1: called per streamed event (the adapter's stall watch)
        self.auto_compact, self.on_compact = auto_compact, on_compact     # C5: on_compact(summary text, files)
        self.mcp = mcp                        # C8: the session's mcp.Hub (calls run here, after the gate)
        self.usage = {"input": 0, "output": 0}
        self.last_input = 0
        self.msgs: list[dict] = session.load() if session else []

    # ------------------------------------------------------------------ messages
    def _add(self, m: dict) -> None:
        self.msgs.append(m)
        if self.session:
            self.session.append(m)

    def _wire(self) -> list[dict]:
        return [{"role": "system", "content": self.system}] + self.msgs

    def _limit(self) -> int:
        return max(8000, self.preset.context - compaction.RESERVE)

    def _trim(self) -> None:
        limit = self._limit()
        size = lambda: compaction.size(self.system, self.msgs)
        if size() <= limit:
            return
        for m in self.msgs[:-TRIM_KEEP]:
            if m.get("role") == "tool" and m.get("content") != "[older output trimmed]":
                m["content"] = "[older output trimmed]"
                if size() <= limit:
                    return

    async def compact(self, instructions: str = "") -> dict | None:
        """Summarize all but the recent tail (compact.py). → {before, after, kept, files, text} | None (nothing to cut)."""
        i = compaction.cut(self.msgs, min(compaction.KEEP, self._limit() // 2))   # a small window keeps less
        if i <= 0:
            return None
        old, tail = self.msgs[:i], self.msgs[i:]
        before = compaction.size(self.system, self.msgs)
        files = compaction.files_touched(old)
        body = compaction.transcript(old, int(self.preset.context * compaction.INPUT_MAX_SHARE))
        ask = [{"role": "system", "content": compaction.SYSTEM},
               {"role": "user", "content": (f"Extra instructions from the owner: {instructions}\n\n" if instructions else "")
                + "Transcript:\n\n" + body}]
        r = await asyncio.to_thread(self.call, self.preset, ask, None, environ=self.environ)
        self._count(r)
        text = _redact(r.get("text") or "").strip()
        if not text:
            return None
        where = str(self.session.path) if self.session else "the session log"
        summary = compaction.summary_message(text, files, where)
        self.msgs = [summary] + tail
        if self.session:
            self.session.compact(summary, len(tail))
        self.on_event("compacted", who=self.who, kept=len(tail))
        if self.on_compact:
            try:
                self.on_compact(text, files)
            except Exception:  # noqa: BLE001 — the handover file is a copy; the session already holds the summary
                pass
        return {"before": before, "after": compaction.size(self.system, self.msgs), "kept": len(tail), "files": files,
                "text": text}

    def spent(self) -> int:
        return self.usage["input"] + self.usage["output"]

    def _count(self, r: dict) -> None:
        self.last_input = r["usage"]["input"]       # the provider's own count of the last prompt (the context meter)
        self.usage["input"] += r["usage"]["input"]
        self.usage["output"] += r["usage"]["output"]
        if self.session:
            self.session.add_usage(r.get("model") or self.preset.name, r["usage"]["input"], r["usage"]["output"])

    async def _call(self, tools: bool) -> dict:
        kw = {"on_delta": self.on_delta} if self.on_delta and getattr(self.preset, "stream", False) else {}
        r = await asyncio.to_thread(self.call, self.preset, self._wire(), self.specs if tools else None, environ=self.environ,
                                    **kw)
        self._count(r)
        return r

    async def _model(self, tools: bool = True) -> dict:
        self._trim()
        if self.auto_compact and compaction.size(self.system, self.msgs) > self._limit():
            await self.compact()
        try:
            return await self._call(tools)
        except llm.LLMError as e:
            if e.kind != "context" or not self.auto_compact or not await self.compact():
                raise
            return await self._call(tools)          # one retry on the compacted conversation

    # ------------------------------------------------------------------ tools
    async def _one(self, c: dict) -> str:
        try:
            args = json.loads(c["arguments"] or "{}")
        except ValueError:
            return "Error: the arguments are not valid JSON; call the tool again with a JSON object."
        name = c["name"]
        self.on_event("step", tool=name, who=self.who)
        if name in self.extra:
            try:
                return await self.extra[name](args)
            except Exception as e:  # noqa: BLE001
                return f"Error: {type(e).__name__}"
        if name.startswith("mcp__") and not (self.mcp is not None and self.mcp.owns(name)):
            return f"Error: unknown tool {name}."
        ok, why = await self.gate.check(name, args, self.who)
        if not ok:
            self.on_event("refused", tool=name, who=self.who)
            return "Refused: " + why
        if self.mcp is not None and self.mcp.owns(name):
            return _redact(await asyncio.to_thread(self.mcp.call, name, args))
        if self.toolbox is None:
            return "Error: no toolbox in this session."
        ok, out = await asyncio.to_thread(self.toolbox.call, name, args)
        return _redact(out if ok else "Error: " + out)

    async def _tools(self, calls: list[dict]) -> list[str]:
        if all(c["name"] in READ_ONLY or c["name"] == "task" for c in calls):
            return list(await asyncio.gather(*(self._one(c) for c in calls)))
        return [await self._one(c) for c in calls]

    # ------------------------------------------------------------------ the loop
    async def _final(self, note: str, reason: str) -> dict:
        self._add({"role": "user", "content": note})
        r = await self._model(tools=False)
        self._add(_replay({"role": "assistant", "content": r["text"]}, r))
        return {"text": r["text"], "reason": reason, "usage": dict(self.usage)}

    async def run(self, text: str | None) -> dict:
        if text:
            self._add({"role": "user", "content": text})
        for _ in range(self.max_steps):
            if self.control() == "stop":
                self.on_event("stopping", who=self.who)
                return await self._final("〔叫停〕Stop now. Do not call tools. Write your (partial) report: what is done, "
                                         "what is not, where you stopped.", "stopped")
            if (self.budget_tokens and self.spent() >= self.budget_tokens) or \
                    (self.deadline and time.monotonic() >= self.deadline):
                self.on_event("budget", who=self.who)
                return await self._final("〔预算用完〕Budget reached (time or tokens). Do not call tools. Write your "
                                         "(partial) report now.", "budget")
            for n, body in self.inbox():
                self._add({"role": "user", "content": f"〔追加 #{n}〕{body}"})
                self.on_event("appended", n=n, who=self.who)
            r = await self._model()
            m = _replay({"role": "assistant", "content": r["text"] or None}, r)
            if r.get("reasoning"):
                m["reasoning_content"] = r["reasoning"]
            if r["tool_calls"]:
                m["tool_calls"] = [{"id": c["id"], "type": "function",
                                    "function": {"name": c["name"], "arguments": c["arguments"]}} for c in r["tool_calls"]]
            self._add(m)
            if not r["tool_calls"]:
                return {"text": r["text"], "reason": "done", "usage": dict(self.usage)}
            outs = await self._tools(r["tool_calls"])
            for c, out in zip(r["tool_calls"], outs):
                self._add({"role": "tool", "tool_call_id": c["id"], "content": out[:TOOL_OUT_MAX]})
        return await self._final("〔步数上限〕Step limit reached. Do not call tools. Write your (partial) report now.",
                                 "max_steps")


class SubAgents:
    """The `task` tool of one CEO (or main) session: isolated child loops, ≤ max_parallel at a time, depth 1."""

    def __init__(self, *, preset, root: str, gate, make_toolbox, sessions_dir, environ=None, call=None,
                 max_parallel: int = 4, budget_tokens: int | None = None, deadline: float | None = None, control=None,
                 on_event=None, origin: str = "", research: bool = False):
        self.preset, self.root, self.gate, self.make_toolbox = preset, root, gate, make_toolbox
        self.sessions_dir, self.environ, self.call = Path(sessions_dir), environ, call
        self.sem = asyncio.Semaphore(max_parallel)
        self.max_parallel = max_parallel
        self.budget_tokens, self.deadline, self.control = budget_tokens, deadline, control
        self.on_event = on_event or (lambda *a, **k: None)
        self.origin, self.n, self.running, self.peak = origin, 0, 0, 0
        self.usage = {"input": 0, "output": 0}

    async def __call__(self, args: dict) -> str:
        desc = " ".join(re.sub(r"[\x00-\x1f\x7f›»「」<>]", " ", str(args.get("description") or "sub-agent")).split())[:40] \
            or "sub-agent"           # model text on a card: no control characters, no chain separators (P359 review L5)
        body = args.get("prompt")
        if not isinstance(body, str) or not body.strip():
            return "Error: task needs a prompt."
        schema = args.get("output_schema") if isinstance(args.get("output_schema"), dict) else None
        self.n += 1
        n = self.n
        who = f"{self.origin} › sub-agent「{desc}」#{n}" if self.origin else f"sub-agent「{desc}」#{n}"
        async with self.sem:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.on_event("subagent_start", n=n, label=desc)
            from .presets import for_role
            from .session import Session
            tb = None
            try:
                tb = await asyncio.to_thread(self.make_toolbox)
                p = for_role(self.preset, "sub")
                text = body + (f"\n\nOutput schema (end with one JSON object matching it):\n{json.dumps(schema)}" if schema else "")
                sess = Session(self.sessions_dir / f"sub-{n}.jsonl")
                lp = Loop(preset=p, system=prompt.system("sub", self.root, p.tier, edit_mode=p.edit_mode), specs=prompt.specs("sub", p.tier, p.edit_mode),
                          gate=self.gate, toolbox=tb, session=sess, environ=self.environ, call=self.call,
                          budget_tokens=self.budget_tokens, deadline=self.deadline, control=self.control,
                          on_event=self.on_event, who=who, max_steps=40)
                r = await lp.run(text)
                self.usage["input"] += r["usage"]["input"]
                self.usage["output"] += r["usage"]["output"]
                out = _cap(r["text"] or "(no answer)", SUB_MAX, str(sess.path))
                if schema:
                    out += "\n" + schema_note(r["text"] or "", schema)
                self.on_event("subagent_end", n=n, label=desc, reason=r["reason"])
                return f"[sub-agent #{n} {desc}: {r['reason']}]\n{out}"
            except llm.LLMError as e:
                self.on_event("subagent_end", n=n, label=desc, reason="error:" + e.kind)
                return f"[sub-agent #{n} {desc}: failed — model error {e.kind}]"
            finally:
                self.running -= 1
                if tb is not None:
                    await asyncio.to_thread(tb.close)


def schema_note(text: str, schema: dict) -> str:
    """The sub-agent's last JSON object against its output_schema: '[output_schema: ok]' or the first problems."""
    from ..ltschema import _collect
    i = text.rfind("{")
    while i >= 0:
        try:
            obj = json.loads(text[i:text.rfind("}") + 1])
            break
        except ValueError:
            i = text.rfind("{", 0, i)
    else:
        return "[output_schema: no JSON object found]"
    try:
        errs = _collect(obj, schema, "$")
    except Exception as e:  # noqa: BLE001 — a schema the validator does not support
        return f"[output_schema: not checked ({type(e).__name__})]"
    return "[output_schema: ok]" if not errs else "[output_schema: " + "; ".join(errs[:3]) + "]"
