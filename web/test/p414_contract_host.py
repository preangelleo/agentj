"""P414 contract — the HOST half: the REAL host classes (serve.Host, terminal.Desk + term_bang / term_vshare, elevate.Elevate,
term_vault, a real bash for the terminal, a stand-in sudo) driven by messages the REAL phone code builds
(web/test/p414_contract_phone.mjs, a JSON-lines co-process). No relay / Noise (p408 / p414 browser walks cover those); the
passkey signature itself is not checked (no authenticator) but the challenge in clientDataJSON is — through the host's own
passkey.client_data — so a phone that asked Face ID over another challenge fails here.

Walk (one phone A, later a second phone B):
  1. host block `vault` → the phone's parse/state: off
  2. §11 sudo card (no wrap) → the phone keeps it with `v.has` 0 + 「用 Face ID 记住」 → typed + remember (pct kind vault_save,
     signed save: tail) → elev_done done + saved:1 → the phone says 「已记住」; host block → has
  3. sudo card → `v.has` 1 + vn → 「用 Face ID 填入电脑密码」 (vault:1, prf1 ‖ prf2) → done; the wrap rotated to vn
  4. sudo card → vault answer with ANOTHER authenticator's PRF → the same card re-armed, has 0, no stale → the phone marks it
     (vaultMiss) and offers typing / remembering again → deny
  5. term_open with the vault: step 1 carries salt = the wrap's + vn → the phone's step 2 (prf1 ‖ prf2 sealed term_prf) →
     term_ready without a login card; the wrap rotated again
  6. 「!」: the phone's bangLine + a 22-char sid → term_line {bang} → term_bang ok + the owner page src.term → the phone's
     termOf / onBang; a page that cannot be written → term_bang {ok:false, why:"failed", state} → the phone says so and never
     offers the line back; a second 「!」 while one runs → `running` → offered back
  7. terminal_go with a line → term_go {cmd, h} → the phone puts it in the input → sent unedited → term_confirm agent:1 → the
     phone's signed resend runs it; terminal_go without a line → term_go {} → the phone opens with an empty input
  8. vshare: B (passkey, no wrap; an emoji-heavy name) → host block share → vault_share step 1 → the phone's step 2 → ctl_pending
     → a `vshare` card to A only (tries 1) → the phone keeps it (digest + challenge agree) → 同意 (PRF sealed kind vshare) →
     elev_done saved → the phone says 「已允许」; B ctl_res ok; B's block → has
Usage: <host venv python> web/test/p414_contract_host.py   (exit 0 = every check held; prints `P414 contract: N checks`)
"""
import pathlib
import sys

HT = pathlib.Path(__file__).resolve().parents[2] / "host" / "tests"
sys.path.insert(0, str(HT.parent))
sys.path.insert(0, str(HT))
import _hermetic  # noqa: E402,F401,I001
import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from cryptography.hazmat.primitives import serialization  # noqa: E402

from agentj import passkey, term_vault as vault, wire  # noqa: E402
from test_f17 import fake_sudo  # noqa: E402
from test_l1 import Phone, _ready  # noqa: E402
import test_p408_terminal as p408  # noqa: E402

PHONE_JS = pathlib.Path(__file__).resolve().parent / "p414_contract_phone.mjs"
FA2 = wire.b64u(b"\x09" * 16)
CHECKS = [0]


class Node:
    def __init__(self):
        node = os.environ.get("AJ_NODE") or shutil.which("node") or "node"
        self.p = subprocess.Popen([node, str(PHONE_JS)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def __call__(self, op, **kw):
        self.p.stdin.write(json.dumps({"op": op, **kw}, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        out = json.loads(self.p.stdout.readline())
        if not out.pop("ok"):
            raise AssertionError("phone " + op + ": " + out["error"])
        return out

    def close(self):
        self.p.stdin.close()
        self.p.wait(10)


def raw(sk):
    priv = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return wire.b64u(priv), wire.b64u(pub)


class Contract(p408.Rig):
    def setUp(self):
        super().setUp()
        from test_p414_bang import _Agent
        self.host.agent = _Agent()
        for name in ("_setup_round_trip", "_model_set_flush"):
            p = mock.patch.object(self.host, name, new=mock.AsyncMock())
            p.start()
            self.addCleanup(p.stop)
        self.pk.stop()                                     # the challenge in clientDataJSON IS checked (host's own parser)
        self.challenges = []

        def check(pk, f, ch):
            passkey.client_data(f["cd"], "webauthn.get", ch)
            self.challenges.append(wire.b64u(ch))
            return 1
        self.pk = mock.patch.object(passkey, "verify_assertion", side_effect=check)
        self.pk.start()
        self.passkey()
        self.enable()
        self.host.elevate.sudo = str(fake_sudo(pathlib.Path(self.tmp.name), p408.GOOD))
        self.node = Node()
        self.addCleanup(self.node.close)
        self.seen = set()

    def ok(self, cond, label, extra=None):
        self.assertTrue(cond, label + ("" if extra is None else " " + json.dumps(extra, ensure_ascii=False, default=str)[:600]))
        CHECKS[0] += 1

    def init_phone(self, name, ph):
        priv, pub = raw(ph.sk)
        self.node("init", name=name, priv=priv, pub=pub, channel=ph.channel, device=ph.did)

    def block(self, s):
        return self.node("block", vault=self.host.host_info(s)["vault"])

    async def card(self, cid, kind="sudo"):
        for _ in range(1500):
            for c, o in self.sent:
                if c == cid and o.get("t") == "elev" and o.get("kind") == kind and (o["id"], o["n"]) not in self.seen:
                    self.seen.add((o["id"], o["n"]))
                    return o
            await asyncio.sleep(0.01)
        self.fail("no card")

    async def done(self, cid, rid):
        return await self.until(lambda: next((o for c, o in self.sent if c == cid and o.get("t") == "elev_done" and o["id"] == rid), None), 20)

    def sudo_req(self):
        from agentj import elevate
        return elevate.norm_sudo({"argv": ["true"], "why": "Check the admin password works", "cwd": "/", "timeout": 30})

    def test_contract(self):
        A = self.s
        self.init_phone("a", self.phone)

        async def go():
            # ---- 1. host block: nothing remembered
            b = self.block(A)
            self.ok(b["state"] == "off" and b["parsed"] == {"has": False, "any": False, "peers": 0, "mode": None}, "1 block off", b)

            # ---- 2. sudo card, typed + 「用 Face ID 记住」
            task = asyncio.create_task(self.host.elevate.request(self.sudo_req()))
            msg = await self.card(A.cid)
            c = self.node("card", name="a", msg=msg)["card"]
            self.ok(c and c["offerSave"] and not c["useVault"] and c["v"]["has"] is False and c["v"]["vn"] is None, "2 card has 0 → 记住", c)
            ans = self.node("answer", name="a", id=msg["id"], how="typed_save", password=p408.GOOD)
            self.ok(ans["prfLen"] == 32 and "pct" in ans["msg"] and "vault" not in ans["msg"], "2 answer typed + pct (prf1 only)", ans["prfLen"])
            await self.host._app(A, ans["msg"])
            res = await asyncio.wait_for(task, 20)
            d = await self.done(A.cid, msg["id"])
            self.ok(res["result"] == "done" and d.get("saved") == 1, "2 elev_done done + saved:1", d)
            words = self.node("done", name="a", msg=d)["notified"]
            self.ok(len(words) == 1 and words[0]["text"].endswith("p414.e.saved") and words[0]["kind"] == "success", "2 the page says 已记住", words)
            self.ok(self.challenges[-1] == c["faCh"], "2 the host checked the challenge the page computed")
            wm = vault.wrap_meta(self.st, self.phone.did)
            self.ok(wm and wm["mode"] == "prf" and wire.b64u(wm["salt"]) == c["v"]["vs"], "2 wrap under the card's vs")
            b = self.block(A)
            self.ok(b["state"] == "has" and b["parsed"]["mode"] == "prf", "2 block has", b)

            # ---- 3. sudo card, 「用 Face ID 填入电脑密码」 (prf1 ‖ prf2, rotation)
            task = asyncio.create_task(self.host.elevate.request(self.sudo_req()))
            msg = await self.card(A.cid)
            c = self.node("card", name="a", msg=msg)["card"]
            self.ok(c["useVault"] and c["v"]["vs"] == wire.b64u(wm["salt"]) and c["v"]["vn"], "3 card has 1 + vs = the wrap's + vn", c)
            ans = self.node("answer", name="a", id=msg["id"], how="vault")
            self.ok(ans["prfLen"] == 64 and ans["msg"].get("vault") == 1 and "pct" not in ans["msg"], "3 vault:1, 64-byte PRF")
            await self.host._app(A, ans["msg"])
            res = await asyncio.wait_for(task, 20)
            self.ok(res["result"] == "done" and res.get("code") == 0, "3 sudo ran with the stored password", res)
            self.ok(wire.b64u(vault.wrap_meta(self.st, self.phone.did)["salt"]) == c["v"]["vn"], "3 M3: the wrap rotated to vn")

            # ---- 4. host build: a PRF that does not open the wrap → the same card, has 0, NO stale
            task = asyncio.create_task(self.host.elevate.request(self.sudo_req()))
            msg = await self.card(A.cid)
            self.node("card", name="a", msg=msg)
            ans = self.node("answer", name="a", id=msg["id"], how="vault", wrongPrf=True)
            await self.host._app(A, ans["msg"])
            again = await self.card(A.cid)
            self.ok(again["id"] == msg["id"] and again["v"]["has"] == 0 and "stale" not in again and again["n"] != msg["n"], "4 re-armed, has 0, no stale", again)
            c = self.node("card", name="a", msg=again)["card"]
            self.ok(c["vaultMiss"] and not c["stale"] and c["offerSave"] and not c["useVault"], "4 the page marks it and offers typing / 记住", c)
            self.ok(vault.wrap_meta(self.st, self.phone.did) is not None, "4 the stored password stays")
            ans = self.node("answer", name="a", id=again["id"], how="deny")
            await self.host._app(A, ans["msg"])
            self.ok((await asyncio.wait_for(task, 20))["result"] == "denied", "4 deny (signed) ends it")

            # ---- 5. term_open with the vault: salt + vn → prf1 ‖ prf2 → ready, no login card
            salt_before = vault.wrap_meta(self.st, self.phone.did)["salt"]
            n0 = len(self.sent)
            await self.host._app(A, {"t": "term_open", "r": p408.R})
            r1 = next(o for _, o in self.sent[n0:] if o.get("t") == "ctl_res")
            self.ok(r1["why"] == "passkey_required" and wire.unb64u(r1["salt"]) == salt_before and len(wire.unb64u(r1["vn"])) == 32, "5 step 1 salt = wrap + vn", r1)
            st2 = self.node("termOpen", name="a", res=r1, r=p408.R)
            self.ok(st2["prfLen"] == 64, "5 step 2 prf1 ‖ prf2")
            await self.host._app(A, st2["msg"])
            ready = await self.until(lambda: next((o for _, o in self.sent[n0:] if o.get("t") in ("term_ready",) or (
                o.get("t") == "ctl_res" and o.get("why") != "passkey_required") or (o.get("t") == "elev" and o.get("kind") == "login")), None), 30)
            self.ok(ready.get("t") == "term_ready" and ready.get("sudo") == "prf", "5 term_ready, no login card", ready)
            self.ok(vault.wrap_meta(self.st, self.phone.did)["salt"] == wire.unb64u(r1["vn"]), "5 M3 rotation on open")
            x = self.desk.term
            await self.until(lambda: x.at_prompt)

            # ---- 6. 「!」
            sid = self.node("newSid")["sid"]
            line = self.node("bangLine", text="!printf 'bang-%s\\n' $((40+2)); printf '%s=%s\\n' API_KEY abc")["line"]
            n0 = len(self.sent)
            await self.host._app(A, {"t": "term_line", "id": x.id, "seq": 1, "line": line, "bang": sid})
            tb = await self.until(lambda: next((o for _, o in self.sent[n0:] if o.get("t") == "term_bang"), None), 20)
            self.ok(tb["ok"] is True and tb["sid"] == sid and tb["state"] == "done" and isinstance(tb["turn"], int), "6 term_bang ok", tb)
            page = self.host.hist.get(tb["turn"])
            src = page["src"]
            self.ok(src["k"] == "phone" and src["text"] == "!" + line.replace("API_KEY abc", "API_KEY [已隐藏]") and src["term"]["sid"] == sid, "6 owner page src", src)
            tv = self.node("termOf", term=src["term"])["term"]
            self.ok(tv["out"] == src["term"]["out"] and "bang-42" in tv["out"] and "API_KEY=abc" not in tv["out"] and tv["hidden"] == 2
                    and tv["state"] == "done" and tv["cut"] is False, "6 the page's termOf keeps it whole", tv)
            self.ok(self.node("onBang", msg=tb, sid=sid, line=line) == {"calls": [], "cleared": True}, "6 ok → the chip clears, nothing else")
            # the page could not be written → why:"failed"
            sid2 = self.node("newSid")["sid"]
            n0 = len(self.sent)
            with mock.patch.object(self.host, "bang_turn", new=mock.AsyncMock(side_effect=OSError("disk"))):
                await self.until(lambda: x.at_prompt)
                await self.host._app(A, {"t": "term_line", "id": x.id, "seq": 2, "line": "echo two", "bang": sid2})
                tb2 = await self.until(lambda: next((o for _, o in self.sent[n0:] if o.get("t") == "term_bang"), None), 20)
            self.ok(tb2 == {"t": "term_bang", "id": x.id, "sid": sid2, "ok": False, "why": "failed", "state": "done"}, "6 failed shape", tb2)
            ob = self.node("onBang", msg=tb2, sid=sid2, line="echo two")
            self.ok(ob["calls"] == [["toast", "p414.bangFailed"]] and ob["cleared"], "6 failed → said once, never offered back", ob)
            # a second one while one runs → running → offered back
            sid3, sid4 = self.node("newSid")["sid"], self.node("newSid")["sid"]
            await self.until(lambda: x.at_prompt)
            await self.host._app(A, {"t": "term_line", "id": x.id, "seq": 3, "line": "sleep 1; echo slow", "bang": sid3})
            await asyncio.sleep(0.2)
            n0 = len(self.sent)
            await self.host._app(A, {"t": "term_line", "id": x.id, "seq": 4, "line": "echo four", "bang": sid4})
            tb4 = next(o for _, o in self.sent[n0:] if o.get("t") == "term_bang")
            ob = self.node("onBang", msg=tb4, sid=sid4, line="echo four")
            self.ok(tb4["why"] == "running" and ob["calls"] == [["toast", "p414.bangRunning"], ["restore", "echo four"]], "6 running → put back", ob)
            await self.until(lambda: next((o for _, o in self.sent if o.get("t") == "term_bang" and o.get("sid") == sid3), None), 20)
            await self.until(lambda: x.at_prompt)
            # P414 (R3-1): a program reads input → the real host answers `program`, writes nothing → the page opens the
            # terminal full screen with the explanation and never offers the line back
            await self.host._app(A, {"t": "term_line", "id": x.id, "seq": 5, "line": "read -r v; echo got-${#v}"})
            await asyncio.sleep(0.2)
            sid5 = self.node("newSid")["sid"]
            n0 = len(self.sent)
            await self.host._app(A, {"t": "term_line", "id": x.id, "seq": 6, "line": "not-for-the-agent", "bang": sid5})
            tb5 = next(o for _, o in self.sent[n0:] if o.get("t") == "term_bang")
            ob = self.node("onBang", msg=tb5, sid=sid5, line="not-for-the-agent")
            self.ok(tb5 == {"t": "term_bang", "id": x.id, "sid": sid5, "ok": False, "why": "program"}
                    and ob["calls"] == [["programWaits"]] and ob["cleared"], "6 program → explained, never put back", [tb5, ob])
            await self.host._app(A, {"t": "term_line", "id": x.id, "seq": 7, "line": "abc"})
            await self.until(lambda: x.at_prompt)
            self.ok("not-for-the-agent" not in json.dumps([o for _, o in self.sent]), "6 program: the line went nowhere", None)

            # ---- 7. terminal_go
            cmd = "echo go-$((6*7))"
            got = await self.desk.ctl_go({"line": cmd})
            tg = next(o for _, o in reversed(self.sent) if o.get("t") == "term_go")
            self.ok(got == {"ok": True, "reached": 1} and tg == {"t": "term_go", "cmd": cmd, "h": __import__("hashlib").sha256(cmd.encode()).hexdigest()}, "7 term_go", tg)
            inp = self.node("go", msg=tg)["input"]
            self.ok(inp == cmd, "7 the page puts it in the input")
            n0 = len(self.sent)
            await self.host._app(A, {"t": "term_line", "id": x.id, "seq": 5, "line": inp})
            tc = next(o for _, o in self.sent[n0:] if o.get("t") == "term_confirm")
            cf = self.node("confirm", name="a", msg=tc, cred=p408.FA["id"])
            self.ok(cf["agent"] and tc["why_en"] == "filled in by the Agent", "7 unedited → term_confirm agent:1", tc)
            await self.host._app(A, cf["msg"])
            await self.until(lambda: "go-42" in "".join(o.get("data", "") for _, o in self.sent[n0:] if o.get("t") == "term_out"), 20)
            self.ok(self.challenges[-1] == tc["fa_ch"], "7 signed resend ran (challenge = the host's)")
            self.desk.go_last = -1e9
            got = await self.desk.ctl_go({})
            tg = next(o for _, o in reversed(self.sent) if o.get("t") == "term_go")
            self.ok(got["ok"] and tg == {"t": "term_go"} and self.node("go", msg=tg)["input"] == "", "7 term_go without a line", tg)
            await self.desk.close("owner")

            # ---- 8. vshare
            B = Phone(self.st, name="新手机" + "\U0001F4F1" * 40)
            self.st.set_passkey(B.pub, {"id": FA2, "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0, "rp": "m.agentj.app", "at": 1})
            sb = _ready(self.host, B, cid=12)
            sb.p33 = True
            self.init_phone("b", B)
            b = self.block(sb)
            self.ok(b["state"] == "share" and b["parsed"]["peers"] == 1, "8 B block share", b)
            n0 = len(self.sent)
            await self.host._app(sb, {"t": "vault_share", "r": p408.R})
            r1 = next(o for c, o in self.sent[n0:] if c == sb.cid and o.get("t") == "ctl_res")
            vs = self.node("vaultShare", name="b", res=r1, r=p408.R)["msg"]
            self.ok(vs is not None, "8 the page accepts step 1", r1)
            await self.host._app(sb, vs)
            self.ok(any(c == sb.cid and o.get("t") == "ctl_pending" and o.get("timeout_ms") == 150000 for c, o in self.sent[n0:]), "8 ctl_pending")
            card = await self.card(A.cid, "vshare")
            self.ok(card["tries"] == 1 and card["v"]["has"] == 1 and card["dev"] == B.did and card["cred"] == FA2
                    and not any(c == sb.cid and o.get("t") == "elev" for c, o in self.sent[n0:]), "8 vshare card to A only, tries 1", card)
            c = self.node("card", name="a", msg=card).get("card")
            self.ok(c is not None and c["name"] == card["name"] and len(card["name"]) == 43, "8 the page keeps the card (emoji name)", c)
            ans = self.node("answer", name="a", id=card["id"], how="vshare")
            self.ok(ans["prfLen"] == 64 and "vault" not in ans["msg"], "8 同意 = PRF over A's salt + vn")
            await self.host._app(A, ans["msg"])
            d = await self.done(A.cid, card["id"])
            self.ok(self.challenges[-1] == c["faCh"], "8 the host checked the page's challenge (digest agrees)")
            words = self.node("done", name="a", msg=d)["notified"]
            self.ok(d["result"] == "saved" and words == [{"kind": "success", "text": "p414.e.vsDone"}], "8 elev_done saved → 已允许", [d, words])
            res = await self.until(lambda: next((o for c, o in self.sent[n0:] if c == sb.cid and o.get("t") == "ctl_res" and o.get("why") != "passkey_required"), None), 20)
            self.ok(res == {"t": "ctl_res", "r": p408.R, "action": "vault_share", "ok": True}, "8 B told ok", res)
            b = self.block(sb)
            self.ok(b["state"] == "has", "8 B block has", b)
        self.run_async(go)


if __name__ == "__main__":
    prog = unittest.main(exit=False, verbosity=1)
    ok = prog.result.wasSuccessful()
    print(f"P414 contract: {CHECKS[0]} checks {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
