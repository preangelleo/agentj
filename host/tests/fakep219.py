"""P219 stand-in for `claude -p --input-format stream-json --output-format stream-json`: the ways a first message stalled.

FAKE219_MODE (env):
  retry      → init, then `api_retry` attempts 1–6 (what Claude Code 2.1.296 emits while it cannot reach the model service),
               then the reply
  model400   → with any --model but claude-opus-5-5: the synthetic "API Error: 400 Claude Fable 5.1 is not supported" and an
               error result with api_error_status 400 (measured shape); with claude-opus-5-5: "ECHO: <text>"
  prompt     → prints a terminal question (not stream-json) and then waits forever without a word
  quiet      → init, then nothing at all
  (other)    → "ECHO: <text>"
Every control_request is answered with an empty success. FAKE219_LOG: one JSON line per start with argv.
"""
import json
import os
import sys
import time

MODE = os.environ.get("FAKE219_MODE", "")
if os.environ.get("FAKE219_LOG"):
    with open(os.environ["FAKE219_LOG"], "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:]}) + "\n")
model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None


def out(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()


def result(text, error=False, status=None):
    r = {"type": "result", "subtype": "success", "is_error": error, "result": text, "session_id": "s-219"}
    if status:
        r["api_error_status"] = status
    out(r)


if MODE == "prompt":
    sys.stdout.write("Do you trust the files in this folder? " + "sk-ant-" + "api03-" + "Z" * 30 + "\n❯ 1. Yes, proceed  2. No, exit\n")
    sys.stdout.flush()
    sys.stdin.read()
    sys.exit(0)

for line in sys.stdin:
    try:
        ev = json.loads(line)
    except ValueError:
        continue
    if ev.get("type") == "control_request":
        out({"type": "control_response", "response": {"subtype": "success", "request_id": ev.get("request_id"), "response": {}}})
        continue
    text = ev.get("message", {}).get("content")
    out({"type": "system", "subtype": "init", "session_id": "s-219", "model": model or "claude-fable-5-1"})
    if MODE == "quiet":
        continue
    if MODE == "retry":
        for n in range(1, 7):
            out({"type": "system", "subtype": "api_retry", "attempt": n, "max_retries": 10, "retry_delay_ms": 50,
                 "error_status": None, "error": "unknown"})
            time.sleep(0.05)
    if MODE == "model400" and model != "claude-opus-5-5":
        msg = "API Error: 400 Claude Fable 5.1 is not supported"
        out({"type": "assistant", "message": {"model": "<synthetic>", "role": "assistant", "content": [{"type": "text", "text": msg}]}})
        result(msg, True, 400)
        continue
    out({"type": "assistant", "message": {"model": model or "claude-fable-5-1", "role": "assistant",
                                          "content": [{"type": "text", "text": f"ECHO: {text}"}]}})
    result(f"ECHO: {text}")
