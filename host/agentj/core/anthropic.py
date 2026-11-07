"""Anthropic Messages wire codec for the native Harness (P431 C1; AGENTJ-CORE-SPEC 2.2 `core/llm`, Pi `anthropic.ts`).

Raw HTTP, standard library only (the spec rules out SDKs and new dependencies). Our session messages (OpenAI-shaped:
user / assistant + tool_calls / tool) become Messages content blocks: tool results are `tool_result` blocks of the next
user message, several in one message; tool calls are `tool_use` blocks with a parsed object input.

Replay: the assistant's content blocks — thinking with its signature, redacted thinking, text, tool_use — are kept in the
session as `_anthropic_blocks`, bound to the exact provider/model in `_anthropic_model`, and sent back unchanged only to that
model (thinking must accompany its tool_use; another model gets ordinary text + tool_use). Hosted / server tools are never
offered; any block type we did not ask for fails the reply closed, like a truncated stream or a tool_use cut by max_tokens.
"""
from __future__ import annotations

import json

VERSION = "2023-06-01"
MAX_OUTPUT = 32000
KEEP = {"text": ("type", "text"), "tool_use": ("type", "id", "name", "input"),
        "thinking": ("type", "thinking", "signature"), "redacted_thinking": ("type", "data")}


def headers(preset, key: str, ua: str) -> dict:
    h = {"Content-Type": "application/json", "User-Agent": ua, "anthropic-version": VERSION}
    if preset.auth == "bearer":
        h["Authorization"] = "Bearer " + key
    else:
        h["x-api-key"] = key
    return h


def _args(text) -> dict:
    try:
        v = json.loads(text or "{}")
    except (ValueError, RecursionError):
        return {}
    return v if isinstance(v, dict) else {}


def messages(msgs: list[dict], preset, drop_thinking: bool = False) -> tuple[str, list[dict]]:
    """(system text, Messages-API messages). Consecutive same-role turns are merged; the first turn is a user turn."""
    system, out = [], []

    def push(role, blocks):
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})
    for m in msgs:
        role, content = m.get("role"), m.get("content")
        if role in ("system", "developer"):
            if content:
                system.append(str(content))
        elif role == "user":
            if content:
                push("user", [{"type": "text", "text": str(content)}])
        elif role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": str(m.get("tool_call_id")),
                           "content": str(content or "(no output)")}])
        elif role == "assistant":
            stored = m.get("_anthropic_blocks")
            if stored and m.get("_anthropic_model") == preset.name:
                blocks = [dict(b) for b in stored
                          if not (drop_thinking and b.get("type") in ("thinking", "redacted_thinking"))]
            else:
                blocks = [{"type": "text", "text": str(content)}] if content else []
                for c in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                                   "input": _args(c["function"].get("arguments"))})
            push("assistant", blocks or [{"type": "text", "text": "…"}])
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation continues)"}]})
    return "\n\n".join(system), out


def request(preset, msgs, tools, max_tokens, stream: bool, drop_thinking: bool = False) -> dict:
    system, wire = messages(msgs, preset, drop_thinking)
    body = {"model": preset.model, "max_tokens": int(max_tokens or preset.max_output or MAX_OUTPUT), "messages": wire}
    if system:
        body["system"] = [{"type": "text", "text": system, **({"cache_control": {"type": "ephemeral"}} if preset.cache else {})}]
    if tools:
        body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                          "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                         for t in tools]
        if preset.cache:
            body["tools"][-1]["cache_control"] = {"type": "ephemeral"}
    if stream:
        body["stream"] = True
    return body


def _usage(u: dict) -> tuple[int, int]:
    n = [u.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")]
    if not all(type(x) is int and x >= 0 for x in n):
        raise ValueError("usage")
    return n[0] + n[1] + n[2], n[3]


def _result(blocks: list[dict], stop, usage: tuple[int, int], preset) -> dict:
    """Content blocks → the normalized step; fail closed on anything we cannot replay or did not offer."""
    from .llm import LLMError
    text, calls, kept, ids = [], [], [], set()
    for b in blocks:
        t = b.get("type")
        if t not in KEEP:
            raise LLMError("bad_reply", 200, f"unsupported content block {str(t)[:30]}")
        if t == "text":
            if not isinstance(b.get("text"), str):
                raise LLMError("bad_reply", 200, "text block")
            text.append(b["text"])
        elif t == "tool_use":
            if not (isinstance(b.get("id"), str) and b["id"] and isinstance(b.get("name"), str) and b["name"]) \
                    or not isinstance(b.get("input"), dict) or b["id"] in ids:
                raise LLMError("bad_reply", 200, "tool_use block")
            ids.add(b["id"])
            calls.append({"id": b["id"], "name": b["name"], "arguments": json.dumps(b["input"], ensure_ascii=False)})
        elif t == "thinking" and not (isinstance(b.get("thinking"), str) and isinstance(b.get("signature", ""), str)):
            raise LLMError("bad_reply", 200, "thinking block")
        elif t == "redacted_thinking" and not isinstance(b.get("data"), str):
            raise LLMError("bad_reply", 200, "redacted thinking block")
        kept.append({k: b[k] for k in KEEP[t] if k in b})
    if calls and stop == "max_tokens":
        raise LLMError("bad_reply", 200, "a tool call was cut by the output limit")
    if stop == "refusal" and not text:
        text.append("(the model declined this request)")
    return {"text": "".join(text), "tool_calls": calls, "finish": stop, "reasoning": None, "model": preset.name,
            "usage": {"input": usage[0], "output": usage[1]}, "anthropic_blocks": kept}


def parse(data, preset) -> dict:
    """A non-streamed Messages reply."""
    from .llm import LLMError
    try:
        if not isinstance(data, dict) or data.get("type") != "message" or not isinstance(data.get("content"), list):
            raise ValueError("not a message")
        return _result(data["content"], data.get("stop_reason"), _usage(data.get("usage") or {}), preset)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise LLMError("bad_reply", 200, "invalid Messages reply") from None


def parse_stream(events, preset, on_delta=None) -> dict:
    """message_start · content_block_start/delta/stop · message_delta · message_stop (ping ignored, error raised)."""
    from .llm import LLMError, _err_kind
    blocks: dict[int, dict] = {}
    closed: set[int] = set()
    inp, out, stop, done = 0, 0, None, False
    try:
        for _, data in events:
            d = json.loads(data)
            t = d.get("type") if isinstance(d, dict) else None
            if t == "message_start":
                inp, out = _usage((d.get("message") or {}).get("usage") or {})
            elif t == "content_block_start":
                i, cb = d["index"], dict(d["content_block"])
                if type(i) is not int or i in blocks:
                    raise ValueError("index")
                if cb.get("type") == "tool_use":
                    cb["_json"] = ""
                    cb["input"] = {}
                blocks[i] = cb
            elif t == "content_block_delta":
                cb, dl = blocks[d["index"]], d["delta"]
                k = dl.get("type")
                if k == "text_delta":
                    cb["text"] = cb.get("text", "") + dl["text"]
                elif k == "input_json_delta":
                    cb["_json"] += dl["partial_json"]
                elif k == "thinking_delta":
                    cb["thinking"] = cb.get("thinking", "") + dl["thinking"]
                elif k == "signature_delta":
                    cb["signature"] = cb.get("signature", "") + dl["signature"]
                elif k != "citations_delta":
                    raise ValueError("delta")
            elif t == "content_block_stop":
                i = d["index"]
                cb = blocks[i]
                if cb.get("type") == "tool_use":
                    v = json.loads(cb.pop("_json") or "{}")
                    if not isinstance(v, dict):
                        raise ValueError("tool input")
                    cb["input"] = v
                closed.add(i)
            elif t == "message_delta":
                stop = (d.get("delta") or {}).get("stop_reason") or stop
                u = d.get("usage") or {}
                if "output_tokens" in u:
                    out = _usage({"output_tokens": u["output_tokens"]})[1]
                if any(k in u for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
                    inp = _usage({k: u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                           "cache_creation_input_tokens")})[0] or inp
            elif t == "message_stop":
                done = True
                break
            elif t == "error":
                raise LLMError(_err_kind(d.get("error")), 200, json.dumps(d.get("error"))[:300])
            elif t != "ping":
                raise ValueError("event")
            if on_delta:
                on_delta()
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise LLMError("bad_reply", 200, "invalid Messages stream") from None
    if not done:
        raise LLMError("bad_reply", 200, "stream ended before message_stop")
    if set(blocks) != closed:
        raise LLMError("bad_reply", 200, "a content block was not finished")
    return _result([blocks[i] for i in sorted(blocks)], stop, (inp, out), preset)
