"""Fixed Agent-facing diagnostics/support operations; identity keys stay in serve."""
from __future__ import annotations

import io
import os


def agent_side():
    from . import elevate, fence
    return bool(os.environ.get(elevate.ENV)) or fence.inside()


def request(op, **fields):
    from . import elevate, fence, names
    from .state import State
    st = State()
    required = agent_side()
    if not required:
        return None
    try:
        res = elevate.client_request(st, {"t": "host_diagnostics", "op": op, **fields},
                                     timeout=(90 + min(max(fields.get("wait", 0), 0), 180) if op in ("support", "feedback_send") else 300), path=os.environ.get(elevate.ENV) or names.elevate_sock(st))
    except (OSError, ValueError) as e:
        res = {"error": type(e).__name__}  # class name only
    if isinstance(res, dict) and type(res.get("exit")) is int and isinstance(res.get("output"), str):
        print(res["output"], end="")
        return res["exit"]
    why = str(res.get("error") or "")[:40] if isinstance(res, dict) else ""
    print(f"({why}) " * bool(why) + "聊天能继续时，可忽略这一步连接检查；安全隔离内不能读取本机钥匙文件，后台暂时没有返回结果。"
              "反馈若未收到编号，就还没有确认提交，请先查看客服会话再重试。 / If chat works, this connection check can be skipped. "
              "Isolation hides the local key files; the background service has not returned a result. "
              "Feedback without a ticket ID is not confirmed sent; check support threads before retrying.")
    return 1


def feedback_snapshot():
    """No harness/fence probes during feedback. Existing support.prepare redacts this."""
    from . import doctor, __version__
    rows = doctor.run_read_only()
    return "Agent J " + __version__ + "\n" + "\n".join(str(r.get("summary", "")) for r in rows)


ALLOWED = {  # P569: each op is signature-tested
    "diag": set(), "layout_last": set(), "status": set(), "reconnect": {"session", "caller_session", "as_json"},
    "support": {"kind", "text", "thread", "attach_doctor", "wait", "lang", "owner_confirmed"},
    "thread": {"thread", "wait"}, "list": set(),
    "feedback_send": {"raw", "owner_confirmed"}, "feedback_replies": {"as_json"},
}


def execute(st, req):
    """No paths, URLs, credentials, subprocesses or caller-selected function names accepted."""
    from . import cloud, feedback, reconnect, support
    op, allowed = req.get("op"), ALLOWED
    fields = set(req) - {"t", "op"}
    if not isinstance(op, str) or op not in allowed or fields - allowed[op]:
        return {"exit": 1, "output": "Request refused: shape\n"}
    for key in ("as_json", "attach_doctor", "owner_confirmed"):
        if key in req and type(req[key]) is not bool:
            return {"exit": 1, "output": "Request refused: boolean\n"}
    for key, limit in (("session", 256), ("caller_session", 256), ("text", 65536), ("thread", 64), ("raw", feedback.MAX_BODY)):
        if key in req and req[key] is not None and (not isinstance(req[key], str) or len(req[key].encode()) > limit):
            return {"exit": 1, "output": "Request refused: text\n"}
    wait = req.get("wait", 0)
    if type(wait) is not int or not 0 <= wait <= 180:
        return {"exit": 1, "output": "Request refused: wait\n"}
    if op == "support" and (req.get("kind") not in support.KINDS or not isinstance(req.get("text"), str)
                            or req.get("lang") not in (None, "zh", "en")):
        return {"exit": 1, "output": "Request refused: support fields\n"}
    out = io.StringIO()
    try:
        if op == "diag":
            from . import field_tools
            import json
            print(json.dumps(field_tools.collect(st, local=True)), file=out)
            rc = 0
        elif op == "layout_last":
            from . import layout_diag
            rc = layout_diag.last(st, out)
        elif op == "status":
            from . import names
            import json
            res = names.ctl_call(st, {"cmd": "status"}, 10) or {}
            safe = {k: res.get(k) for k in ("ok", "agent", "agent_status", "relay_up", "relay_state", "identity_ok", "harness_readiness")}
            safe["agent_name"] = st.agent_name()  # P502: current owner-defined display data, including inside the fence
            from .text import agent_label
            safe["agent_display_name"] = agent_label(safe["agent_name"])
            print(json.dumps(safe), file=out)
            rc = 0 if res.get("ok") else 1
        elif op == "reconnect":
            from . import preferences
            lang = preferences.get(preferences.effective(st), "appearance.language", "zh")
            rc = reconnect.run(st, lang, session=req.get("session"),
                               env={"CLAUDE_CODE_SESSION_ID": req.get("caller_session")},
                               ask=False, as_json=req.get("as_json", False), out=out)
        elif op == "support":
            rc = support.run_send(req.get("kind"), req.get("text"), thread=req.get("thread"),
                                  attach_doctor=req.get("attach_doctor", False), wait=wait, lang=req.get("lang"),
                                  ch=support.channel_for(st), doctor_fn=feedback_snapshot, out=out,
                                  owner_confirmed=req.get("owner_confirmed", False))  # P573: as feedback_send
        elif op == "thread":
            rc = support.run_thread(req.get("thread"), wait=wait, ch=support.channel_for(st), out=out)
        elif op == "list":
            rc = support.run_list(st=st, out=out)
        elif op == "feedback_send":
            rc = feedback.run_send(None, raw_text=req.get("raw"), owner_confirmed=req.get("owner_confirmed", False), out=out)
        else:
            rc = feedback.run_replies(as_json=req.get("as_json", False), out=out)
    except (support.SupportError, cloud.CloudError, feedback.FeedbackError) as exc:
        # Existing errors are safe, but arbitrary OS errors may contain private paths.
        rc = support.EXIT_SEAT if isinstance(exc, support.SupportSeatError) else 1
        print(str(exc), file=out)
    except Exception as exc:  # P569: class name only, never the text
        rc, name = 1, type(exc).__name__
        try:
            st.log("host_diagnostics_failed", action=op, exception_type=name)
        except OSError:
            pass
        print(f"后台请求出错（{name}），不是还没回复 / Background request failed ({name}); not 'no reply yet'", file=out)
    return {"exit": rc, "output": out.getvalue()}
