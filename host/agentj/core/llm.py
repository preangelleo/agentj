"""Model client (AGENTJ-CORE-SPEC 2.2 `core/llm`; design from Pi `pi-ai` openai-completions): OpenAI-compatible Chat
Completions with tool calls. P359 scope: non-streaming requests (the loop only needs whole steps); streaming, Anthropic
Messages and OpenAI Responses follow in C1 / C9.

- Retries (Pi): 429 / 5xx / network → backoff 2 s, 4 s, 8 s; then the preset's fallback chain (omp #2), one model after another.
- Failure kinds (pstack ④) for the retry table in orders.py: auth · rate_limit · network · context · server · bad_request.
- Reasoning replay (Pi's DeepSeek compat flag): `reasoning_content` of an assistant step is kept and sent back only for
  models whose preset says replay_reasoning.
- Nothing but the conversation goes to the model: no telemetry, no session ids, no machine facts (spec §5.5).
The key is read by NAME from `environ` per request and only ever placed in the Authorization header.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .presets import Preset

UA = "agentj-core/1"
BACKOFF = (2, 4, 8)
TIMEOUT = 300


class LLMError(Exception):
    def __init__(self, kind: str, status: int | None = None, detail: str = ""):
        super().__init__(kind)
        self.kind, self.status, self.detail = kind, status, detail[:300]


def _transport(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _kind(status: int, text: str) -> str:
    low = text.lower()
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server"
    if "context" in low and ("length" in low or "window" in low or "too long" in low or "exceed" in low):
        return "context"
    if status == 404 or "model" in low and ("not found" in low or "does not exist" in low or "unavailable" in low):
        return "model"
    return "bad_request"


def wire_messages(messages: list[dict], preset: Preset) -> list[dict]:
    """Our session messages → the wire: drop local-only fields; keep reasoning only where the model wants it back."""
    out = []
    for m in messages:
        w = {k: v for k, v in m.items() if not k.startswith("_") and k != "reasoning_content"}
        if m.get("reasoning_content") and preset.replay_reasoning and m.get("role") == "assistant":
            w["reasoning_content"] = m["reasoning_content"]
        out.append(w)
    return out


def _once(preset: Preset, messages, tools, key: str, transport, timeout, max_tokens) -> dict:
    body = {"model": preset.model, "messages": wire_messages(messages, preset)}
    if tools:
        body["tools"] = tools
    if max_tokens:
        body["max_tokens"] = max_tokens
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": UA}
    try:
        status, raw = transport(preset.base_url.rstrip("/") + "/chat/completions", headers,
                                json.dumps(body, ensure_ascii=False).encode(), timeout)
    except (OSError, TimeoutError) as e:
        raise LLMError("network", None, type(e).__name__) from None
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if status != 200:
        raise LLMError(_kind(status, text), status, text)
    try:
        d = json.loads(text)
        ch = d["choices"][0]
        msg = ch.get("message") or {}
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("bad_reply", status, text) from None
    calls = []
    for c in msg.get("tool_calls") or []:
        f = c.get("function") or {}
        if isinstance(f.get("name"), str):
            calls.append({"id": str(c.get("id") or f"call_{len(calls)}"), "name": f["name"],
                          "arguments": f.get("arguments") if isinstance(f.get("arguments"), str) else json.dumps(f.get("arguments") or {})})
    u = d.get("usage") or {}
    return {"text": msg.get("content") or "", "tool_calls": calls, "finish": ch.get("finish_reason"),
            "reasoning": msg.get("reasoning_content") or None, "model": preset.name,
            "usage": {"input": int(u.get("prompt_tokens") or 0), "output": int(u.get("completion_tokens") or 0)}}


def chat(preset: Preset, messages: list[dict], tools: list[dict] | None = None, *, environ=None, transport=None,
         timeout: float = TIMEOUT, max_tokens: int | None = None, sleep=time.sleep, on_retry=None) -> dict:
    """One model step → {text, tool_calls[{id,name,arguments}], finish, reasoning, model, usage{input,output}}.
    Raises LLMError after retries and the fallback chain; auth / bad_request / context are not retried."""
    key = preset.key(environ)
    if not key:
        raise LLMError("auth", None, "no key")
    transport = transport or _transport
    from .presets import sibling
    chain = [preset] + [sibling(preset, m) for m in preset.fallback]
    last = None
    for p in chain:
        for i in range(len(BACKOFF) + 1):
            try:
                return _once(p, messages, tools, key, transport, timeout, max_tokens)
            except LLMError as e:
                last = e
                if e.kind in ("auth", "bad_request", "context", "bad_reply"):
                    raise
                if e.kind == "model":
                    break                       # the next model of the chain
                if i < len(BACKOFF):
                    if on_retry:
                        on_retry(p.name, e.kind, BACKOFF[i])
                    sleep(BACKOFF[i])
    raise last or LLMError("network")


def hello(model: str | None = None, timeout: float = 60, environ=None, transport=None) -> str:
    """harness.probe for Agent J Harness: one tool-free call on the chosen (or first keyed) preset."""
    from .presets import resolve
    p = resolve(model, "main", environ)
    r = chat(p, [{"role": "system", "content": "Reply to the supplied greeting only. No tools are available."},
                 {"role": "user", "content": "Hello. Reply with a short greeting only."}], environ=environ,
             transport=transport, timeout=timeout, max_tokens=64, sleep=lambda s: None)
    return r["text"]
