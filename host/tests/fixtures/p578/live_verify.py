# P578 live regression: a real `opencode serve` 2.x + Volcano Ark (Agent Plan or Coding Plan) under the real host, and the
# switch job's OWN verdict function switch_harness._hello driven through a control adapter on that host (status = the same
# fields serve's `status` command returns). Scenarios:
#   plain     — the hello alone (the normal switch)
#   busy      — a phone message queued right behind the hello (Avon: her WeChat channel kept the new Agent working), with
#               IDLE_WAIT shrunk to P578_IDLE_WAIT seconds so a short second turn outlasts it
#   phone-lost — paired phone disconnects before hello; host and status stay available
#   status-reconnect (alias lost) — status adapter unavailable for P578_LOST seconds, then recovers
# usage (isolated HOME + XDG_*, key by exact name in the environment, never printed):
#   P578_HOST_DIR=<agentjarvis/host to test> python3 live_verify.py <plan> <model> <scenario> <out.json>
import asyncio, json, os, pathlib, sys, tempfile, shutil, time
plan, model, scenario, out = sys.argv[1], sys.argv[2], sys.argv[3], pathlib.Path(sys.argv[4])
hostdir = pathlib.Path(os.environ.get("P578_HOST_DIR") or pathlib.Path.cwd() / "agentjarvis/host")
root = pathlib.Path(tempfile.mkdtemp(prefix="p578-verify-", dir=os.environ.get("TMPDIR")))
sys.path[:0] = [str(hostdir), str(hostdir / "tests")]          # the tested tree's own test_l1 (it puts its parent first)
from test_l1 import Phone, _host, _ready, _state       # noqa: E402  (redirects XDG_CONFIG_HOME on import)
from agentj import ark, switch_harness as sw            # noqa: E402
assert pathlib.Path(sw.__file__).resolve().is_relative_to(hostdir.resolve()), sw.__file__
d = ark.plan_data(plan)
cfgdir = pathlib.Path(os.environ["XDG_CONFIG_HOME"]) / "opencode"
cfgdir.mkdir(parents=True, exist_ok=True)
(cfgdir / "opencode.json").write_text(json.dumps({"$schema": "https://opencode.ai/config.json", "provider": {d["provider"]: d["block"]}}))
work = root / "work"; work.mkdir()
st = _state(str(root)); st.set_agent_config("opencode", str(work), model=f"{d['provider']}/{model}", fence=False)
sent = []; host = _host(st, sent); ph = Phone(st)
if scenario == "busy":
    sw.IDLE_WAIT = float(os.environ.get("P578_IDLE_WAIT", "4"))
assert scenario in ("plain", "busy", "lost", "phone-lost", "status-reconnect"), scenario
lost_for = float(os.environ.get("P578_LOST", "200")) if scenario in ("lost", "status-reconnect") else 0
hello_wait = float(os.environ.get("P578_HELLO_WAIT", str(sw.HELLO_WAIT)))
proof = {"plan": plan, "model": f"{d['provider']}/{model}", "scenario": scenario, "host_dir": str(hostdir),
         "opencode": os.environ.get("AGENTJ_OPENCODE_BIN"), "IDLE_WAIT": sw.IDLE_WAIT, "lost_for": lost_for, "hello_wait": hello_wait,
         "backend": "stand-in" if os.environ.get("P578_STANDIN") == "1" else "native"}
polls = []


async def go():
    loop = asyncio.get_running_loop()
    run = asyncio.create_task(host.run())
    hello_at = [None]

    def on_loop(fn):
        return asyncio.run_coroutine_threadsafe(_call(fn), loop).result(30)

    async def _call(fn):
        return fn()

    def ctl(req):
        if req["cmd"] == "harness_hello":
            r = on_loop(lambda: host.harness_hello({k: v for k, v in req.items() if k != "cmd"}))
            hello_at[0] = time.monotonic()
            if scenario == "busy":
                on_loop(lambda: host.agent.submit("Count from 1 to 150 in words, one per line. No tools."))
            return r
        if req["cmd"] == "status":
            if lost_for and hello_at[0] and time.monotonic() - hello_at[0] < lost_for:
                polls.append({"t": round(time.monotonic() - hello_at[0], 1), "lost": True})
                return None
            s = on_loop(lambda: {"ok": True, "agent": host.agent.kind if host.agent else None, "agent_status": host.eff_status(),
                                 "agent_said_at": getattr(host, "agent_said_at", 0),
                                 **dict(zip(("agent_turn_start_at", "agent_turn_end_at", "agent_turn_failed"),
                                            getattr(host, "turn_at", (0.0, 0.0, False)))), **host._agent_phase()})
            polls.append({"t": round(time.monotonic() - (hello_at[0] or time.monotonic()), 1), "status": s["agent_status"],
                          "said": s["agent_said_at"] > 0,
                          "phone_ready": on_loop(lambda: any(s.state == "ready" for s in host.sessions.values()))})
            return s
        return {"ok": False}
    try:
        for _ in range(400):
            if host.agent is not None: break
            await asyncio.sleep(.05)
        _ready(host, ph)
        if scenario == "phone-lost":
            host.sessions.clear()  # paired device remains; its transport is gone
        up = await asyncio.to_thread(sw._wait_up, ctl, "opencode", time.sleep, time.monotonic)
        proof["up"] = up
        t = time.monotonic()
        h = await asyncio.to_thread(sw._hello, ctl, {"outcome": "ok", "from": "codex", "to": "opencode", "handoff": "none"},
                                    time.sleep, time.monotonic, hello_wait,
                                    pending=lambda: proof.setdefault("unconfirmed_at_s", round(time.monotonic() - t, 1)))
        proof["verdict"] = "ok" if h is None else h
        proof["verdict_after_s"] = round(time.monotonic() - t, 1)
        proof["would_roll_back"] = not (h is None or h.get("ok") is True)
        if scenario == "phone-lost":
            proof["checks"] = {"phone_absent": bool(polls) and all(p.get("phone_ready") is False for p in polls),
                               "host_verdict_ok": not proof["would_roll_back"]}
        elif scenario in ("lost", "status-reconnect"):
            proof["checks"] = {"status_was_lost": any(p.get("lost") for p in polls),
                               "status_recovered": any("status" in p for p in polls),
                               "waited_for_recovery": proof["verdict_after_s"] >= lost_for,
                               "host_verdict_ok": not proof["would_roll_back"]}
        if "checks" in proof:
            assert all(proof["checks"].values()), proof["checks"]
        for _ in range(240):                       # let a queued turn finish before stopping (clean evidence)
            if host.eff_status() == "idle": break
            await asyncio.sleep(.5)
        proof["replies"] = [o.get("text", "")[:240] for _, o in sent if o.get("t") == "msg"]
    finally:
        host.stopping.set(); await run
asyncio.run(go())
# compress the poll timeline to transitions
tl, last = [], None
for p in polls:
    k = (p.get("lost"), p.get("status"), p.get("said"), p.get("phone_ready"))
    if k != last:
        tl.append(p); last = k
proof["timeline"] = tl
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(proof, ensure_ascii=False, indent=1) + "\n")
shutil.rmtree(root, ignore_errors=True)
print(json.dumps({k: proof.get(k) for k in ("plan", "scenario", "verdict", "verdict_after_s", "would_roll_back")}, ensure_ascii=False))
