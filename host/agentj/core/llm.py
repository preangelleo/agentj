"""Model client (AGENTJ-CORE-SPEC 2.2 `core/llm`; design from Pi `pi-ai`): three wire protocols with tool calls —
OpenAI-compatible Chat Completions (streamed over SSE where the preset says `stream`), stateless OpenAI Responses (P429 C9)
and Anthropic Messages (streamed, `anthropic.py`; P431 C1).

- Streaming (C1): tool-call arguments are joined from their deltas by index; a stream that ends without its finish marker
  (`[DONE]` / a finish_reason, Anthropic `message_stop`) or that was cut by the output limit inside a tool call is refused
  as `bad_reply` — a truncated tool call is never executed. Each received event calls `on_delta` (liveness for stall watch).

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
MAX_REPLY = 8 * 1024 * 1024


class LLMError(Exception):
    def __init__(self, kind: str, status: int | None = None, detail: str = ""):
        super().__init__(kind)
        self.kind, self.status, self.detail = kind, status, detail[:300]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a model key to a redirect destination.


def _lines(r, cap: int | None = None):
    """The 200 body of a streamed reply, line by line, at most `cap` bytes in total; closes the response at the end."""
    total, cap = 0, MAX_REPLY if cap is None else cap
    try:
        while True:
            line = r.readline(1 << 20)
            if not line:
                return
            total += len(line)
            if total > cap:
                raise LLMError("bad_reply", None, "model response exceeds 8 MiB")
            yield line
    finally:
        r.close()


def _stream_transport(url: str, headers: dict, body: bytes, timeout: float):
    """Like _transport, but a 200 body is returned as an iterator of lines (read as it arrives; `timeout` is per read)."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        r = urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        with e:
            return e.code, _bounded(e)
    if r.status != 200:
        with r:
            return r.status, _bounded(r)
    return 200, _lines(r)


def sse(raw):
    """Server-sent events → (event, data) pairs. `raw` is bytes (a scripted transport) or an iterator of byte lines."""
    lines = raw.splitlines(keepends=True) if isinstance(raw, (bytes, bytearray)) else raw
    event, data = "", []
    for line in lines:
        line = line.decode("utf-8", "replace") if isinstance(line, (bytes, bytearray)) else line
        line = line.rstrip("\r\n")
        if not line:
            if data:
                yield event or "message", "\n".join(data)
            event, data = "", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:][1:] if line[5:6] == " " else line[5:])
    if data:
        yield event or "message", "\n".join(data)


def _peek_json(raw):
    """A server that ignored stream=true answers with one JSON body: (True, text) for that, else (False, lines)."""
    if isinstance(raw, (bytes, bytearray)):
        t = raw.lstrip()
        return (True, raw.decode("utf-8", "replace")) if t[:1] == b"{" else (False, raw)
    it = iter(raw)
    head = []
    for line in it:
        head.append(line)
        if line.strip():
            break
    if head and head[-1].lstrip()[:1] == b"{":
        rest = b"".join(head) + b"".join(it)
        return True, rest.decode("utf-8", "replace")
    import itertools
    return False, itertools.chain(head, it)


def _err_kind(err) -> str:
    """An error event inside a 200 stream (OpenAI-style {"error": …} or Anthropic {"type": "error", "error": {type}})."""
    t = str((err or {}).get("type") or (err or {}).get("code") or "").lower() if isinstance(err, dict) else ""
    msg = str((err or {}).get("message") or "").lower() if isinstance(err, dict) else ""
    if "rate" in t:
        return "rate_limit"
    if "overloaded" in t or "api_error" in t or "server" in t:
        return "server"
    if "auth" in t or "permission" in t:
        return "auth"
    if "context" in msg or "too long" in msg:
        return "context"
    return "bad_reply"


def _bounded(response) -> bytes:
    raw = response.read(MAX_REPLY + 1)
    if len(raw) > MAX_REPLY:
        raise LLMError("bad_reply", None, "model response exceeds 8 MiB")
    return raw


def _transport(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout) as r:
            return r.status, _bounded(r)
    except urllib.error.HTTPError as e:
        with e:
            return e.code, _bounded(e)


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


def _offered(result: dict, tools, status) -> dict:
    offered = {t["function"]["name"] for t in tools or []}
    if any(c["name"] not in offered for c in result["tool_calls"]):
        raise LLMError("bad_reply", status, "unexpected tool call")
    return result


def _once(preset: Preset, messages, tools, key: str, transport, timeout, max_tokens, on_delta=None,
          drop_thinking: bool = False) -> dict:
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": UA}
    stream = preset.stream
    if preset.protocol == "openai-responses":
        from . import responses
        body = responses.request(preset, messages, tools, max_tokens)
        route, stream = "/responses", False
    elif preset.protocol == "openai-chat":
        body = {"model": preset.model, "messages": wire_messages(messages, preset)}
        if tools:
            body["tools"] = tools
        if max_tokens:
            body["max_tokens"] = max_tokens
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        route = "/chat/completions"
    elif preset.protocol == "anthropic-messages":
        from . import anthropic
        body = anthropic.request(preset, messages, tools, max_tokens, stream, drop_thinking=drop_thinking)
        route = "/messages"
        headers = anthropic.headers(preset, key, UA)
    else:
        raise LLMError("bad_request", None, "unsupported protocol")
    transport = transport or (_stream_transport if stream else _transport)
    try:
        status, raw = transport(preset.base_url.rstrip("/") + route, headers,
                                json.dumps(body, ensure_ascii=False).encode(), timeout)
    except (OSError, TimeoutError) as e:
        raise LLMError("network", None, type(e).__name__) from None
    if status != 200:
        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        if (preset.protocol == "anthropic-messages" and status == 400 and not drop_thinking and "thinking" in text.lower()
                and any(m.get("_anthropic_blocks") for m in messages)):
            # Replayed thinking refused (an earlier turn was trimmed or compacted): once more without it.
            return _once(preset, messages, tools, key, transport, timeout, max_tokens, on_delta, drop_thinking=True)
        raise LLMError(_kind(status, text), status, text)
    try:
        if stream:
            whole, rest = _peek_json(raw)
            if whole:
                raw = rest
            else:
                if preset.protocol == "anthropic-messages":
                    return _offered(anthropic.parse_stream(sse(rest), preset, on_delta), tools, status)
                return _offered(_chat_stream(sse(rest), preset, on_delta), tools, status)
        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except (OSError, TimeoutError) as e:
        raise LLMError("network", None, "stream cut: " + type(e).__name__) from None
    if preset.protocol in ("openai-responses", "anthropic-messages"):
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            raise LLMError("bad_reply", status, "invalid JSON") from None
        if preset.protocol == "anthropic-messages":
            return _offered(anthropic.parse(data, preset), tools, status)
        return _offered(responses.parse(data, preset), tools, status)
    return _offered(_chat_reply(text, preset, status), tools, status)


def _chat_stream(events, preset: Preset, on_delta=None) -> dict:
    """Join a streamed Chat Completion: content, reasoning_content and tool_calls[index] deltas; usage from the last chunk."""
    text, reasoning, calls = [], [], {}
    finish, usage, done = None, {}, False
    try:
        for _, data in events:
            if data.strip() == "[DONE]":
                done = True
                break
            d = json.loads(data)
            if not isinstance(d, dict):
                raise ValueError("chunk")
            if d.get("error"):
                raise LLMError(_err_kind(d["error"]), 200, json.dumps(d["error"])[:300])
            if isinstance(d.get("usage"), dict):
                usage = d["usage"]
            for ch in d.get("choices") or []:
                delta = ch.get("delta") or {}
                if isinstance(delta.get("content"), str):
                    text.append(delta["content"])
                if isinstance(delta.get("reasoning_content"), str):
                    reasoning.append(delta["reasoning_content"])
                for tc in delta.get("tool_calls") or []:
                    i = tc.get("index", len(calls))
                    slot = calls.setdefault(i, {"id": None, "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = str(tc["id"])
                    f = tc.get("function") or {}
                    if isinstance(f.get("name"), str) and f["name"] != slot["name"]:
                        slot["name"] += f["name"]
                    if isinstance(f.get("arguments"), str):
                        slot["arguments"] += f["arguments"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
            if on_delta:
                on_delta()
    except (ValueError, TypeError, AttributeError, RecursionError):
        raise LLMError("bad_reply", 200, "invalid stream chunk") from None
    if not done and finish is None:
        raise LLMError("bad_reply", 200, "stream ended before the reply finished")
    if calls and finish == "length":
        raise LLMError("bad_reply", 200, "a tool call was cut by the output limit")
    out = [{"id": c["id"] or f"call_{i}", "name": c["name"], "arguments": c["arguments"] or "{}"}
           for i, c in sorted(calls.items()) if c["name"]]
    return {"text": "".join(text), "tool_calls": out, "finish": finish, "reasoning": "".join(reasoning) or None,
            "model": preset.name, "usage": {"input": int(usage.get("prompt_tokens") or 0),
                                            "output": int(usage.get("completion_tokens") or 0)}}


def _chat_reply(text: str, preset: Preset, status: int) -> dict:
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
         timeout: float = TIMEOUT, max_tokens: int | None = None, sleep=time.sleep, on_retry=None, on_delta=None) -> dict:
    """One model step → {text, tool_calls[{id,name,arguments}], finish, reasoning, model, usage{input,output}}.
    Raises LLMError after retries and the fallback chain; auth / bad_request / context are not retried."""
    key = preset.key(environ)
    if not key:
        raise LLMError("auth", None, "no key")
    from .presets import sibling
    chain = [preset] + [sibling(preset, m) for m in preset.fallback]
    last = None
    for p in chain:
        for i in range(len(BACKOFF) + 1):
            try:
                return _once(p, messages, tools, key, transport, timeout, max_tokens, on_delta)
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
