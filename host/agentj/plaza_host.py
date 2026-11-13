"""#428 + #422: `agentj plaza request` — the Agent J host installs a plaza package after ONE signed card.

The Agent's CLI sends only semantic fields (control.sock `plaza_install`, inside the fence elevate.sock `t: plaza_install`);
serve fetches, runs every existing check (signature, trust / downgrade, publisher TOFU, static audit, permissions, params,
target conflict) through market.run_install in a worker thread, raises one always-asked `agentj plaza` card that names the
package, version, sha256 + install digest, signature status, publisher, every permission (+ the part beyond the Agent),
audit findings and the target, and installs only after a paired device's signed allow. Nothing is relaxed: an unsigned
package with a block finding is refused before any card; a bad signature is refused; a swapped package is a digest change.
The receipt goes back to the CLI and one line to the chat (agent_notice), also when the CLI has gone away meanwhile.

#437: requests beyond MAX_RUNNING wait in a FIFO (≤ QUEUE_MAX packages in flight, else `queue_full`), only one plaza card is
on the phone at a time (plaza_perms.card_turn) and its TTL starts when it appears. `names` (≤ GROUP_MAX) = one card for a
group: every package runs every check in its own thread; the ones that reach the card are listed on ONE signed card (a
group SHA-256 binds each name / version / sha256 / digest); the rest are listed as failed before the card. Allow → each
installs with its own receipt; deny → none.
"""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import hashlib
import io
import os
import re
import secrets
import threading
import time

_NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")
_VER = re.compile(r"[0-9A-Za-z.+-]{1,64}")
MAX_OUT = 48 * 1024
MAX_RUNNING = 2            # security round 1 (L2): fetch / audit / verify / install threads at once, across packages (FIFO)
QUEUE_MAX = 32             # #437: packages in flight (running + queued); more → queue_full
GROUP_MAX = 25             # #437: names in one `agentj plaza request a b c`
KEEPALIVE = 30             # s: a queued request tells the CLI it still waits (each line resets the socket timeout)
FIELDS = {"name", "names", "version", "harness", "workspace", "params", "params_obj", "sign_as", "replace"}


def check(req: dict) -> dict:
    """The request's fields only (ValueError otherwise). Paths stay strings; run_install validates the rest. `names` (#437)
    = a group: unique, ≤ GROUP_MAX, and no per-package options (version / params / params file / sign-as)."""
    from .market import HARNESSES
    spec = {k: req.get(k) for k in FIELDS}
    names = spec.pop("names")
    if names is None:
        names = [spec["name"]]
    elif not (isinstance(names, list) and 1 <= len(names) <= GROUP_MAX and len(set(map(str, names))) == len(names)
              and spec["name"] is None):
        raise ValueError("shape")
    spec.pop("name")
    group = len(names) > 1
    ok = (all(isinstance(n, str) and _NAME.fullmatch(n) for n in names)
          and (spec["version"] is None or isinstance(spec["version"], str) and _VER.fullmatch(spec["version"]))
          and (spec["harness"] is None or spec["harness"] in HARNESSES)
          and (spec["workspace"] is None or isinstance(spec["workspace"], str) and spec["workspace"].startswith("/")
               and 1 < len(spec["workspace"]) <= 4096 and "\x00" not in spec["workspace"])
          and isinstance(spec["params"] or [], list) and len(spec["params"] or []) <= 64
          and all(isinstance(p, str) and len(p) <= 4096 for p in spec["params"] or [])
          and (spec["params_obj"] is None or isinstance(spec["params_obj"], dict))
          and (spec["sign_as"] is None or isinstance(spec["sign_as"], str))
          and isinstance(spec["replace"] or False, bool)
          and not (group and (spec["version"] or spec["params"] or spec["params_obj"] is not None or spec["sign_as"])))
    if not ok:
        raise ValueError("shape")
    return {**spec, "names": list(names)}


def _say(host, key: str, **kw) -> str:
    from . import humane
    return humane.of(host, "plaza.install." + key, **kw)


class Gate:
    """FIFO slots for the install threads (thread side: loop-agnostic). `on_wait(position)` while waiting (≥ every KEEPALIVE s)."""

    def __init__(self, n: int):
        self.n, self.used, self.line, self.cv = n, 0, collections.deque(), threading.Condition()

    def acquire(self, on_wait=None) -> None:
        with self.cv:
            me = object()
            self.line.append(me)
            while self.line[0] is not me or self.used >= self.n:
                if on_wait is not None:
                    on_wait(self.line.index(me) + 1)
                self.cv.wait(KEEPALIVE)
            self.line.popleft()
            self.used += 1
            self.cv.notify_all()

    def release(self) -> None:
        with self.cv:
            self.used -= 1
            self.cv.notify_all()


def _gate(host) -> Gate:
    g = host.__dict__.get("_plaza_gate")
    if g is None or g.n != MAX_RUNNING:
        g = host._plaza_gate = Gate(MAX_RUNNING)
    return g


def _refuse(host, name, why, market) -> dict:
    host.st.log("plaza_request", name=name, result=why)
    return {"ok": False, "rc": market.EXIT_PERMISSION, "why": why, "out": market._perm_refusal(name, {"why": why})}


SHORT = {"denied": ("你拒绝了", "declined"), "timeout": ("卡超时", "card expired"), "secret_path": ("路径像有密钥", "secret-like path"),
         "busy": ("已在安装或排队", "already queued"), "serve_stop": ("主机在停止", "host stopping")}
RC_SHORT = {5: ("签名不对", "bad signature"), 2: ("审计拦下", "audit blocked"), 4: ("digest 不符", "digest mismatch")}


def _path_hit(index, path: str) -> bool:
    return index.hits(path) or index.hits(path.replace("/", ""))


def _private_target(info: dict, index, en: bool) -> str:
    """A fixed display for a Host-private target; no path components leave."""
    if not info.get("private_target"):
        return str(info["target"])
    # Do not expose a leaf merely because it contains fewer than eight indexed
    # characters: combining it with other card fields could reconstruct a key.
    return ("Installation directory contains a suspected secret fragment; full path hidden" if en
            else "安装目录含疑似密钥片段，已隐藏完整路径")


def _short(r: dict, en: bool) -> str:
    w = SHORT.get(r.get("why")) or RC_SHORT.get(r["rc"]) or (f"exit {r['rc']}", f"exit {r['rc']}")
    return w[1 if en else 0]


async def handle(host, req: dict, emit=None, **run_kw) -> dict:
    """→ {ok, rc, why?, out, results? (a group)}. `emit(event)` gets {"stage": "card", "online", "ttl"} when the card is out
    and {"stage": "queued", "position"} / {"stage": "checking", "done", "total"} meanwhile. run_kw: tests only (post / get /
    keyring for the synthetic plaza)."""
    from . import market, plaza, plaza_perms, application_ingress
    request = application_ingress.current(host)
    try:
        spec = check(req)
    except ValueError:
        return {"ok": False, "rc": market.EXIT_ERROR, "why": "bad_request", "out": ""}
    names = spec.pop("names")
    group, first = len(names) > 1, names[0]
    gate = plaza_perms.turn_gate(host)            # round 2 (L4): the asker's source when the request arrives
    if gate["gated"]:                             # round 1 (M1): refused now — no fetch, no queue slot, no card later
        plaza_perms.gate_refused(host, first, {"names": names}, gate["why"])
        return _refuse(host, first, gate["why"], market)
    early = ("estop" if host.stopped() else "serve_stop" if host.stopping.is_set() else
             "application_ingress_unverified" if not application_ingress.allowed(host) else None)
    flight = host.__dict__.setdefault("_plaza_installing", set())
    todo = [n for n in names if n not in flight]
    if early is None and not todo:
        early = "busy"                                  # #437: this package is already installing / queued
    if early is None and len(flight) + len(todo) > QUEUE_MAX:
        early = "queue_full"
    if early:
        return _refuse(host, first, early, market)
    from .peer_guard import SecretIndex
    index = await asyncio.to_thread(SecretIndex.build, workdir=(getattr(host, "agent_cfg", None) or {}).get("dir") or None,
                                    fragment_size=8)
    # Do not reject an indexed workspace before checking the package: a skill may
    # install elsewhere. Keep the raw request entirely host-private on a hit.
    private_workspace = bool(spec["workspace"] and _path_hit(index, spec["workspace"]))
    # Capture the lexical workspace too: a symlink may resolve into a private
    # target, and market errors before host_ask can otherwise echo its alias.
    private_workspace = private_workspace or bool(spec["workspace"] and
        _path_hit(index, os.path.realpath(spec["workspace"])))
    # A skill target may live outside the requested workspace. Resolve it before
    # entering market: pre-card errors must obey the same privacy classification.
    def private_request(n):
        if private_workspace:
            return True
        try:
            h = market.resolve_harness(spec["harness"], host.st)
            for candidate in (market.skills_root(h) / n, market.workspace_root(spec["workspace"]) / n):
                if _path_hit(index, str(candidate)) or _path_hit(index, os.path.realpath(candidate)):
                    return True
        except (ValueError, OSError, market.PlazaError):
            pass
        return False
    private = {n: private_request(n) for n in todo}
    loop = asyncio.get_running_loop()
    slots = _gate(host)
    infos, waits, card, pre, changed = {}, {}, {}, {}, asyncio.Event()
    bufs = {n: io.StringIO() for n in todo}
    en = (getattr(host, "lang", "zh") or "zh").startswith("en")

    def soon(fn, *a):
        try:
            loop.call_soon_threadsafe(fn, *a)
        except RuntimeError:            # the loop is gone (serve stopped): nobody to tell
            pass

    def say(ev):
        if emit is not None:
            soon(emit, ev)

    def asker(n):
        def ask(info: dict) -> dict:              # worker thread → the coordinator's ONE card, then back
            # The input workspace and the pinned target can differ (symlinks,
            # skills). Either hit makes the target opaque on every external surface.
            info = {**info, "private_target": private[n] or _path_hit(index, str(info["target"]))}
            private[n] = info["private_target"]
            f = concurrent.futures.Future()
            waits[n] = f
            infos[n] = {**info, "via": gate["via"], "mail": gate.get("mail")}
            slots.release()                       # waiting for the owner holds no install slot
            soon(changed.set)
            try:
                return f.result()
            finally:
                slots.acquire()
        return ask

    def classify_target(n, target):
        if _path_hit(index, target) or _path_hit(index, os.path.realpath(target)):
            private[n] = True

    def run(n) -> int:
        slots.acquire(None if group else lambda pos: say({"stage": "queued", "position": pos}))
        try:
            plaza.seat_gate(host.st)              # the plaza needs a usable seat (P199), checked where the state is
            return market.run_install(n, version=spec["version"], harness=spec["harness"], workspace=spec["workspace"],
                                      params=spec["params"] or [], params_file=spec["params_obj"], sign_as=spec["sign_as"],
                                      replace=bool(spec["replace"]), st=host.st, out=bufs[n], host_ask=asker(n),
                                      _host_live=request._transport_live if request else lambda:False,
                                      _target_private=lambda target: classify_target(n, target), **run_kw)
        except plaza.PlazaError as e:
            print("没有安装 / NOT installed" if private[n] else f"agentj plaza: {e}", file=bufs[n])
            return market.EXIT_ERROR
        except Exception as e:  # noqa: BLE001 — a receipt, never a silent drop
            print(f"agentj plaza: install failed ({type(e).__name__})", file=bufs[n])
            return market.EXIT_ERROR
        finally:
            slots.release()

    def start(n) -> asyncio.Future:
        fut = loop.create_future()

        def body():
            rc = run(n)
            soon(lambda: fut.done() or fut.set_result(rc))
            soon(changed.set)
        threading.Thread(target=body, daemon=True, name="agentj-plaza-install").start()
        return fut

    flight.update(todo)
    for n in todo:
        host.st.log("plaza_request", name=n, result="start")
    futs = {n: start(n) for n in todo}
    try:
        while True:                               # every package reached the card or finished before it
            changed.clear()
            left = [n for n in todo if n not in infos and not futs[n].done()]
            if group and emit is not None:
                emit({"stage": "checking", "done": len(todo) - len(left), "total": len(todo)})
            if not left:
                break
            try:
                await asyncio.wait_for(changed.wait(), KEEPALIVE)
            except asyncio.TimeoutError:
                pass
        ready = [n for n in todo if n in infos and not futs[n].done()]
        if ready:
            card.update(await _card(host, [infos[n] for n in ready], group,
                                    [(n, futs[n].result()) for n in todo if futs[n].done()], gate, index, emit, en))
            for n in ready:                       # the card's signed tool_input binds every name / sha256 / digest
                waits[n].set_result({k: card[k] for k in ("ok", "rid", "device", "why", "inherited", "parent_grant") if k in card})
        rcs = {n: await futs[n] for n in todo}
    finally:
        for f in waits.values():
            if not f.done():
                f.set_result({"ok": False, "why": "serve_stop"})
        flight.difference_update(todo)
    results = []
    for n in names:
        if n not in rcs:
            results.append({"name": n, "rc": market.EXIT_PERMISSION, "why": "busy"})
            continue
        rc = rcs[n]
        why = None if rc == 0 else pre.get(n) or (card.get("why") if n in infos and card and not card.get("ok") else "error")
        host.st.log("plaza_request", name=n, result="ok" if rc == 0 else str(why), rc=rc)
        results.append({"name": n, "rc": rc, **({"why": why} if why else {})})
    if card.get('ok') and card.get('device'):
        from . import initialization
        installed = {r['name']: r for r in market.read_installed(host.st)}
        cards = [{'name': r['name'], **installed[r['name']]['initialization']}
                 for r in results if r['rc'] == 0 and installed.get(r['name'], {}).get('initialization')]
        if cards:
            await initialization.deliver(host, card['device'], cards)
    pending = _deps_task(host, [r["name"] for r in results if r["rc"] == 0], en)      # #466
    if not group:
        r, out = results[0], bufs[first].getvalue()
        if card or pre:                           # the owner saw a card (or it was refused at it): the chat hears the outcome
            tid = _notice(host, _say(host, "deps_pending", name=first, deps="、".join(pending[first])) if first in pending else
                          _say(host, "done", name=first, version=infos[first]["version"]) if r["rc"] == 0 else
                          market._perm_refusal(first, card if card else {"why": r["why"]}) if card.get("why") or pre else
                          _say(host, "failed", name=first, why=f"exit {r['rc']}"))     # fixed words: package text never reaches the chat
            if first in pending and tid is not None:
                _rows(host)[first] = {"tid": tid, "names": [first], "bad": [], "pending": {first}}
        if private[first]:
            out = "已安装 / installed" if r["rc"] == 0 else "没有安装 / NOT installed"
        return {"ok": r["rc"] == 0, "rc": r["rc"], **({"why": r["why"]} if "why" in r else {}), "out": out[-MAX_OUT:]}
    each = MAX_OUT // len(names)
    out = "".join(f"── {n} ──\n" + bufs[n].getvalue()[-each:] for n in todo)
    if card.get("why"):
        out += market._perm_refusal(first, card) + "\n"
    good = [r["name"] for r in results if r["rc"] == 0]
    bad = [f"{r['name']}（{_short(r, en)}）" if not en else f"{r['name']} ({_short(r, en)})" for r in results if r["rc"]]
    if card:
        row = {"tid": None, "names": good, "bad": bad, "pending": set(pending) & set(good)}
        row["tid"] = _notice(host, _group_text(host, row))
        if row["tid"] is not None:
            _rows(host).update({n: row for n in row["pending"]})
    rc = 0 if not bad else card.get("why") and market.EXIT_PERMISSION or next(r["rc"] for r in results if r["rc"])
    if any(private.values()):
        out = "已安装 / installed" if not bad else "没有安装 / NOT installed"
    return {"ok": not bad, "rc": rc, **({"why": card["why"]} if card.get("why") else {}), "out": out[-MAX_OUT:],
            "results": results}


def _deps_task(host, names: list, en: bool) -> dict:
    """#466: ONE system task for the pending packages → {name: [missing]}."""
    from . import market, plaza_deps
    recs = {n: r for n in names if (r := market.pending_of(host.st, n))}
    if not recs:
        return {}
    cli = {n: [c.get("name") for c in (r.get("requires") or {}).get("cli") or () if isinstance(c, dict)] for n, r in recs.items()}
    py = {n: (r["verify"].get("python") if isinstance(r["verify"].get("python"), dict) else {}) for n, r in recs.items()}
    agreed = {n: isinstance(r["verify"].get("python_prep"), dict) and py[n].get("prep") not in ("no_consent", "host_only")
              for n, r in recs.items()}      # F1: the card's yes to uv, as reverify judged it (market.run_reverify)
    text = "\n\n".join(plaza_deps.task_text(n, r["version"], [{"name": d, "user": plaza_deps.user_copies(d),   # ADR-0305: + found / prep
                                                               "found": py[n].get("found"), "prep": py[n].get("prep"),
                                                               "consent": agreed[n]}
                                                              for d in r["verify"]["missing"]], en, market.VERIFY_PATH, cli[n])
                        for n, r in recs.items())
    try:
        import secrets
        from .compose import Send
        from .provenance import system
        from . import privacy
        page = host.hist_add({"k": "agent", "via": "plaza_deps", "text": privacy.owner_text(text)}, "", "open")
        send = Send("plaza_deps", secrets.token_urlsafe(16), page["id"], text=text, by="plaza_deps", source=system())
        host.agent.submit(send)
        host.st.log("plaza_deps_task", count=len(recs))
    except Exception as e:  # noqa: BLE001 — the CLI receipt carries the same task text
        host.st.log("plaza_deps_task", result="failed", error=type(e).__name__)
    unk = _say(host, "deps_unknown")              # #479: an unnamed failure ("? (exit 1)") reads as words in the chat
    return {n: [unk if d.startswith("?") else plaza_deps.label(d, cli[n], en) for d in r["verify"]["missing"]]
            for n, r in recs.items()}   # N4-6: chat too


async def verify(host, req: dict) -> dict:
    from . import market, plaza
    name, harness = req.get("name"), req.get("harness")
    if not (isinstance(name, str) and _NAME.fullmatch(name)) or not (harness is None or harness in market.HARNESSES):
        return {"ok": False, "rc": market.EXIT_ERROR, "why": "bad_request", "out": ""}
    buf = io.StringIO()

    def body() -> int:
        try:
            return market.run_reverify(name, harness=harness, st=host.st, out=buf, local=True)
        except plaza.PlazaError as e:
            print(f"agentj plaza: {e}", file=buf)
            return market.EXIT_ERROR
    rc = await asyncio.to_thread(body)
    ran = rc != 0 or _ran(host, name, harness)    # security r1 L2: a skipped / refused / sandbox-less rerun never says 「自检通过」
    key = "ready" if ran else "not_run"
    row = _rows(host).get(name) if rc == 0 else None
    if row is not None:                           # ADR-0269-w4-plaza (#479): the install's own chat line becomes the final word
        _rows(host).pop(name, None)
        row["pending"].discard(name)
        try:
            host.hist_update(row["tid"], text=_group_text(host, row) if len(row["names"]) > 1 or row["bad"] else
                             _say(host, key, name=name))
        except Exception:  # noqa: BLE001
            row = None
    if rc == 0 and row is None:
        _notice(host, _say(host, "verified" if ran else "not_run", name=name))
    return {"ok": rc == 0, "rc": rc, "out": buf.getvalue()[-MAX_OUT:]}


def _notice(host, line: str) -> int | None:
    try:
        return host.agent_notice(line)
    except Exception:  # noqa: BLE001
        host.st.log("plaza_request_notice", result="failed")
        return None


def _ran(host, name: str, harness) -> bool:
    from . import market
    try:
        recs = [r for r in market.read_installed(host.st) if r["name"] == name and r["type"] != "card"
                and (harness is None or r["harness"] == harness)]
    except Exception:  # noqa: BLE001 — unknown → never claim the self-check passed
        return False
    return bool(recs) and (recs[-1].get("verify") or {}).get("state") == "ok"


def _rows(host) -> dict:
    """#479: {package: its install chat line} while its dependencies are pending (this serve only; after a restart a passing
    rerun says so in a new line)."""
    return host.__dict__.setdefault("_plaza_rows", {})


def _group_text(host, row: dict) -> str:
    mark = _say(host, "pending_mark")
    ok = [n + (mark if n in row["pending"] else "") for n in row["names"]]
    if not row["bad"]:
        return _say(host, "group_ok", n=len(ok), ok="、".join(ok) or "—")
    return _say(host, "group_done", n=len(ok), ok="、".join(ok) or "—", m=len(row["bad"]), bad="、".join(row["bad"]))


async def _card(host, infos: list, group: bool, before: list, gate: dict, index, emit, en: bool) -> dict:
    """ONE signed card for the packages that reached it (one package, not a group: the full #428 card)."""
    from . import plaza_perms
    from .privacy import owner_text
    # One-use references live only in this invocation; neither target nor workspace
    # enters a signed input, phone frame, or activity entry. The local installed
    # receipt retains its real target for reverify and ownership checks.
    refs = {i["name"]: secrets.token_urlsafe(32) for i in infos if i.get("private_target")}
    pk = [{"package": i["name"], "version": i["version"], "sha256": i["sha256"], "digest": i["digest"],
           "target": ("位置已隐藏" if i.get("private_target") else str(i["target"])),
           **({"target_ref": refs[i["name"]]} if i.get("private_target") else {}),
           "certified": bool(i["certified"]), "publisher": i["publisher"].get("fp")} for i in infos]
    if not group:
        i = infos[0]
        text = owner_text(plaza_perms.install_card_text(i, en, index,
                          target_display=_private_target(i, index, en) if i.get("private_target") else None))
        tool_input = {"summary": text, **{k: v for k, v in pk[0].items() if k != "package"}, "package": i["name"],
                      "items": [{"kind": x["kind"], "name": x["name"]} for x in i.get("extra") or []]}
    else:
        gsha = hashlib.sha256("\n".join(f"{p['package']} {p['version']} {p['sha256']} {p['digest']}" for p in pk).encode()).hexdigest()
        failed = [(n, _short({"rc": rc}, en)) for n, rc in before if rc]
        text = owner_text(plaza_perms.group_card_text(infos, en, index, failed, gsha,
                           target_displays=[_private_target(i, index, en) if i.get("private_target") else None for i in infos]))
        tool_input = {"summary": text, "packages": pk, "group_sha256": gsha,
                      "items": [{"kind": x["kind"], "name": x["name"]} for i in infos for x in i.get("extra") or []][:plaza_perms.MAX_ITEMS]}
    host.st.log("plaza_request_card", name=infos[0]["name"], n=len(tool_input["items"]), packages=len(infos),
                certified=all(p["certified"] for p in pk))
    label = infos[0]["name"] if not group else f"{len(infos)} packages"
    needs_uv = any(x.get("kind") == "secret" for i in infos for x in i.get("extra") or [])
    # The reference is not an authority. The Host owns the pending request and
    # consumes it before releasing worker threads, even on denial or exception.
    from . import approvals, market_guard, application_ingress
    pending = host.__dict__.setdefault("_plaza_private_pending", {})
    token = secrets.token_urlsafe(32)
    if refs:
        host._task_id()  # initialize before capturing the live authority
        bindings = []
        for i in infos:
            if i.get("private_target"):
                try:
                    target, pin = market_guard.pin_target(i["target"])
                except (market_guard.PlazaError, OSError, ValueError):
                    return {"ok": False, "why": "authority_changed", "version": infos[0]["version"]}
                bindings.append((i["name"], str(target), pin, i["digest"], refs[i["name"]]))
        pending[token] = {"bindings": bindings, "session": str(host.hist.epoch),
                          "agent": host.agent, "authority": host._approval_live_authority(),
                          "input": approvals.input_digest(tool_input), "deadline": time.monotonic() + host.ask_ttl,
                          "receipt": None}
    receipt = {}
    if refs:
        pending[token]["receipt"] = receipt
    try:
        result = await plaza_perms.signed_card(host, label, tool_input, emit, gate, require_passkey=needs_uv,
                                               receipt=receipt if refs else None)
        if refs:
            bound = pending.pop(token, None)  # one use, including failed validation
            proof = result.get("proof") or {}
            rid, device = proof.get("rid"), proof.get("device")
            env = proof.get("envelope") or {}
            records = await asyncio.to_thread(
                lambda: [r for r in approvals.read_log(host.st) if r.get("id") == rid]) if rid else []
            valid = (bound is not None and result.get("ok") is True and not result.get("inherited")
                     and rid and device and bound["receipt"] is receipt and receipt.get("id") == rid
                     and (not env or (env.get("request_id") == rid and env.get("session") == bound["session"]
                                      and env.get("input_sha256") == bound["input"]
                                      and env.get("expires_at", 0) >= time.time()))
                     and time.monotonic() <= bound["deadline"]
                     and bound["agent"] is host.agent and bound["session"] == str(host.hist.epoch)
                     and bound["authority"] == host._approval_live_authority()
                     and application_ingress.allowed(host) and len(records) == 1
                     and records[0].get("device") == device and records[0].get("tool") == "agentj plaza"
                     and records[0].get("input_sha256") == bound["input"]
                     and records[0].get("reason") == "device" and records[0].get("decision") == "allow"
                     and approvals.check_record(host.st, records[0]) == "ok"
                     and host.st.sign_key(device) == proof.get("sign_pub"))
            if valid:
                for name, target, pin, digest, ref in bound["bindings"]:
                    i = next((item for item in infos if item["name"] == name), None)
                    projected = next((p for p in pk if p["package"] == name), None)
                    if i is None or projected is None:
                        valid = False
                        break
                    try:
                        current, current_pin = market_guard.pin_target(i["target"])
                    except (market_guard.PlazaError, OSError, ValueError):
                        valid = False
                        break
                    if (str(current) != target or current_pin != pin
                            or i["digest"] != digest or projected["digest"] != digest
                            or projected.get("target_ref") != ref):
                        valid = False
                        break
            if not valid and result.get("ok"):
                result = {"ok": False, "why": "authority_changed"}
    finally:
        pending.pop(token, None)
    return {**result, "version": infos[0]["version"]}
