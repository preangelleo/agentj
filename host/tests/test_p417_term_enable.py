"""#185 (P417): switch the web terminal on from a COMPUTER browser — and never use it from a phone.

- the device kind comes from the label the remote gave itself at pairing (`self_name` 「网页 · <OS> <browser>」):
  iOS / Android / 华为 → phone, macOS / Windows / Linux → desktop, anything else → unknown.
- desktop: `term_open` mode "enable" = host nonce + signature over the object text "enable" + passkey, then the `login` card
  (the typed computer password, checked by sudo) → on, this device an owner entry, and the terminal opens; a later normal
  open works. Audit: controls.log `term_open` start (mode enable, signature) + `terminal_cfg` enable:remote.
- refused: a phone (enable AND a normal open, even when listed at the computer) · an unknown kind (enable only) · a bad /
  missing signature · a "line" nonce reused for enable · a failed passkey · a wrong password (nothing switched on) ·
  the Agent's terminal_go skips phones.
- `agentj terminal enable` (serve `terminal_cfg`) unchanged for a desktop remote; a phone is not a default and is refused by name.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import controls, passkey, term_cli, term_store, terminal  # noqa: E402

from test_l1 import Phone, _ready  # noqa: E402
from test_p408_terminal import BAD, FA, GOOD, R, Rig, signed  # noqa: E402

DESK, IPHONE, ANDROID = "网页 · Linux Chrome", "网页 · iOS Safari", "网页 · Android Chrome"


class Kind(Rig):
    def label(self, text, ph=None):
        ph = ph or self.phone
        d = self.st.devices()
        d[ph.did]["self_name"] = text
        self.st.write_private(self.st.devices_path, json.dumps(d).encode())
        self.st._devices_cache = None

    async def enable_step1(self, s=None):
        s = s or self.s
        n0 = len(self.sent)
        await self.desk.on_phone(s, {"t": "term_open", "r": R, "mode": "enable"})
        return [o for _, o in self.sent[n0:] if o.get("t") == "ctl_res"][-1]

    async def enable_step2(self, c1, target=None, **over):
        obj = {"t": "term_open", "r": R, "mode": "enable", **signed(self.phone, "term_open", target or {"mode": "enable"}, c1["n"]),
               "fa": FA, **over}
        n0 = len(self.sent)
        await self.desk.on_phone(self.s, obj)
        return [o for _, o in self.sent[n0:]]

    async def finish(self, c1, pw=GOOD, tries=1):
        """Answer the login card(s) with `pw`; → the final term_ready / ctl_res."""
        def end():
            return next((o for o in self.sent_after(c1) if o.get("t") == "term_ready" or (
                o.get("t") == "ctl_res" and o.get("action") == "term_open" and o.get("why") != "passkey_required")), None)

        def card():
            return next((o for o in self.sent_after(c1) if o.get("t") == "elev" and o.get("kind") == "login"
                         and o["n"] not in self.used), None)
        while True:
            got = await self.until(lambda: end() or card(), 30)
            if got.get("t") != "elev":
                return got
            self.used.add(got["n"])
            await self.answer_login(got, pw)


class Kinds(unittest.TestCase):
    def test_labels(self):
        st = mock.Mock()
        for label, want in ((DESK, "desktop"), ("网页 · macOS Safari", "desktop"), ("网页 · Windows Edge", "desktop"),
                            ("网页 · macOS 主屏幕", "desktop"), (IPHONE, "phone"), (ANDROID, "phone"),
                            ("网页 · 华为手机 浏览器", "phone"), ("网页 · 华为平板 Chrome", "phone"), ("网页 · Chrome", "unknown"),
                            ("手机", "unknown"), ("", "unknown")):
            st.devices.return_value = {"d": {"name": label, "self_name": label}}
            self.assertEqual(term_store.device_kind(st, "d"), want, label)
        st.devices.return_value = {"d": {"name": "老板的电脑", "self_name": IPHONE, "owner_name": "老板的电脑"}}
        self.assertEqual(term_store.device_kind(st, "d"), "phone")        # the owner's rename never changes the kind
        self.assertEqual(term_store.device_kind(st, "x"), "unknown")
        self.assertEqual(term_store.device_kind(st, None), "unknown")


class RemoteEnable(Kind):
    def test_desktop_enables_opens_and_opens_again(self):
        self.label(DESK)
        self.passkey()
        self.assertEqual(self.host.preferences_msg(self.s)["host"]["terminal"]["kind"], "desktop")

        async def go():
            c1 = await self.enable_step1()
            self.assertEqual(c1["why"], "passkey_required", c1)
            want = passkey.elevate_challenge(self.host.channel, self.phone.did, c1["n"], "term_open", c1["n"],
                                             controls.object_digest("term_open", {"mode": "enable"}))
            self.assertEqual(c1["fa_ch"], terminal.wire.b64u(want))       # Face ID is over "enable", not "line"
            out = await self.enable_step2(c1)
            self.assertEqual(out[-1]["t"], "ctl_pending", out)
            self.assertFalse(term_store.read_config(self.st)["on"])       # nothing switched on before the password
            ready = await self.finish(c1)
            self.assertEqual(ready["t"], "term_ready", ready)
            cfg = term_store.read_config(self.st)
            self.assertTrue(cfg["on"])
            self.assertEqual(cfg["entries"], [{"device": self.phone.did, "sudo": True, "web": False}])
            await self.line("echo hi", "hi")
            await self.desk.close("owner")
            ready2 = await self.open(expect_card=False)                  # a normal open now; the password was kept (local)
            self.assertEqual(ready2["t"], "term_ready", ready2)
        self.run_async(go)
        log = [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]
        start = [r for r in log if r["action"] == "term_open" and r.get("result") == "start"]
        self.assertTrue(start and start[0].get("sig"))
        self.assertTrue(any(r["action"] == "terminal_cfg" and r.get("result") == "enable:remote" for r in log))
        blob = json.dumps([o for _, o in self.sent], ensure_ascii=False).encode()
        files = b"".join(p.read_bytes() for p in self.st.root.rglob("*") if p.is_file())
        for hay in (blob, files):
            self.assertNotIn(GOOD.encode(), hay)

    def test_web_version_becomes_a_web_entry(self):
        self.label(DESK)
        self.passkey()
        d = self.st.devices()
        d[self.phone.did]["rt"] = {"i": "x"}
        self.st.write_private(self.st.devices_path, json.dumps(d).encode())
        self.st._devices_cache = None

        said = []

        async def go():
            c1 = await self.enable_step1()
            await self.enable_step2(c1)
            self.assertEqual((await self.finish(c1))["t"], "term_ready")
        with mock.patch.object(self.desk, "_notice", side_effect=lambda zh, en: said.append(zh)):
            self.run_async(go)
        self.assertTrue(term_store.read_config(self.st)["entries"][0]["web"])
        self.assertTrue(any("网页版遥控器" in z and "agentj terminal disable" in z for z in said), said)   # security review A1
        self.assertTrue(any("遥控器「" in z and "不是你本人开启" in z for z in said), said)   # RESUME 9 ②: names the remote + the way off

    def test_wrong_password_switches_nothing_on(self):
        self.label(DESK)
        self.passkey()

        async def go():
            c1 = await self.enable_step1()
            await self.enable_step2(c1)
            res = await self.finish(c1, pw=BAD)
            self.assertEqual(res["t"], "ctl_res")
            self.assertIn(res["why"], ("locked", "bad_password"))
        self.run_async(go)
        self.assertFalse(term_store.read_config(self.st)["on"])
        self.assertEqual(term_store.read_config(self.st)["entries"], [])
        self.assertIsNone(self.desk.term)
        self.assertGreater(term_store.locked_for(self.st, self.phone.did), 0)

    def test_bad_missing_signature_wrong_nonce_failed_passkey(self):
        self.label(DESK)
        self.passkey()

        async def go():
            c1 = await self.enable_step1()                                # signed over "line" for an enable nonce
            self.assertEqual((await self.enable_step2(c1, target={"mode": "line"}))[-1]["why"], "bad_signature")
            c1 = await self.enable_step1()
            out = await self.enable_step2(c1, sig=None)
            self.assertEqual(out[-1]["why"], "shape")                    # no signature
            c1 = await self.enable_step1()
            self.enable()                                                 # (listed, so the "line" refusals pass)
            self.assertEqual((await self.enable_step2(c1, mode="line", **signed(self.phone, "term_open", {"mode": "line"},
                                                                                 c1["n"])))[-1]["why"], "shape")   # the nonce was for enable
            term_store.write_config(self.st, {"on": False, "entries": [], "minutes": 15})
            c1 = await self.step1()                                       # a "line" nonce cannot be spent on enable
            self.assertEqual(c1["why"], "disabled")
            c1 = await self.enable_step1()
            with mock.patch.object(passkey, "verify_assertion", side_effect=passkey.PasskeyError("bad", "sig")):
                self.assertEqual((await self.enable_step2(c1))[-1]["why"], "passkey_failed")
            self.assertEqual((await self.enable_step2(c1))[-1]["why"], "replay")   # that nonce is spent (one use)
        self.run_async(go)
        self.assertFalse(term_store.read_config(self.st)["on"])

    def test_no_passkey_estop_os(self):
        self.label(DESK)

        async def go():
            self.assertEqual((await self.enable_step1())["why"], "no_passkey")
            self.passkey()
            self.desk.os_ok = False
            self.assertEqual((await self.enable_step1())["why"], "os")
            self.desk.os_ok = True
            await self.desk.on_phone(self.s, {"t": "term_open", "r": R, "mode": "root"})
            self.assertEqual(self.msgs("ctl_res")[-1]["why"], "shape")
        self.run_async(go)


class PhoneRefused(Kind):
    def test_phone_cannot_enable_or_open_even_when_listed(self):
        for label in (IPHONE, ANDROID):
            self.label(label)
            self.passkey()
            self.assertEqual(self.host.preferences_msg(self.s)["host"]["terminal"]["kind"], "phone")

            async def go():
                self.assertEqual((await self.enable_step1())["why"], "phone")
                self.enable()                                             # listed at the computer (older config)
                self.assertEqual((await self.step1())["why"], "phone")
                term_store.write_config(self.st, {"on": False, "entries": [], "minutes": 15})
            self.run_async(go)
        self.assertIsNone(self.desk.term)

    def test_phone_signed_step_two_still_refused(self):
        """A phone that got a nonce while it was labelled otherwise is refused again at step 2."""
        self.label(DESK)
        self.passkey()

        async def go():
            c1 = await self.enable_step1()
            self.label(IPHONE)
            self.assertEqual((await self.enable_step2(c1))[-1]["why"], "phone")
        self.run_async(go)
        self.assertFalse(term_store.read_config(self.st)["on"])

    def test_unknown_kind_may_open_when_listed_but_not_enable(self):
        self.passkey()                                                     # the rig's label 「手机」: unknown

        async def go():
            self.assertEqual((await self.enable_step1())["why"], "device_type")
            self.enable()
            self.assertEqual((await self.open())["t"], "term_ready")
        self.run_async(go)

    def test_terminal_go_skips_phones(self):
        self.label(IPHONE)
        self.passkey()
        self.enable()

        res = asyncio.run(self.desk.ctl_go({}))
        self.assertEqual(res.get("error"), "offline", res)
        self.assertEqual(self.msgs("term_go"), [])
        self.label(DESK)
        self.assertTrue(asyncio.run(self.desk.ctl_go({}))["ok"])          # the same remote as a computer browser: reached
        self.assertEqual(len(self.msgs("term_go")), 1)


class CliEnable(Kind):
    async def cli(self, devices, answer=True):
        task = asyncio.create_task(term_cli.ctl_cfg(self.desk, {"op": "enable", "code": "123456", "devices": devices}))
        if not answer:
            return await task
        ask = await self.until(lambda: next((o for o in self.msgs("ask", cid=self.s.cid) if o["tool"] == terminal.TOOL), None)
                               or (task.done() and {"done": 1}))
        if "done" in ask:
            return await task
        await self.host._answer(self.s, {**self.phone.answer(ask, True), "fa": FA})
        return await task

    def test_cli_enable_unchanged_for_a_desktop_remote(self):
        self.label(DESK)
        self.passkey()
        res = asyncio.run(self.cli([]))                                   # the default: the only passkey remote
        self.assertTrue(res["ok"], res)
        cfg = term_store.read_config(self.st)
        self.assertTrue(cfg["on"])
        self.assertEqual([e["device"] for e in cfg["entries"]], [self.phone.did])

    def test_cli_enable_refuses_a_named_phone_and_skips_it_as_default(self):
        self.label(IPHONE)
        self.passkey()
        self.assertEqual(asyncio.run(self.cli([self.phone.did], answer=False))["error"], "phone_device")
        self.assertEqual(asyncio.run(self.cli([], answer=False))["error"], "no_passkey")
        laptop = Phone(self.st, name=DESK)
        _ready(self.host, laptop, cid=12).p33 = True
        self.passkey(laptop)
        res = asyncio.run(self.cli([]))                                   # the phone answers the card; the laptop is listed
        self.assertTrue(res["ok"], res)
        self.assertEqual([e["device"] for e in term_store.read_config(self.st)["entries"]], [laptop.did])
        self.assertIn("phone_device", term_cli.ERR)


if __name__ == "__main__":
    unittest.main()
