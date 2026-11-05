"""0.26.9 #482 (chairman 11-04: 「那个对勾是个骗人的」) — 「用 Face ID 记住」 on the sudo card, host half, end to end.

The real host (serve.Host + elevate + term_vault, a stand-in sudo): the FIRST sudo card (P573 shape: `helper_uv` 0, our `fa`,
`v.has` 0) answered with the typed password + `pct` (PRF over `vs`) → done + `saved:1`; the NEXT sudo request's card says
`v.has` 1 with the same salt; a Face ID answer (`vault:1`, PRF only, no typed value) runs sudo with the remembered password.
A save the host cannot keep is logged by reason (names only) and the password appears in no log or message.
The web half (the page actually sends `fa` + `pct` when the box is ticked): web/test/p0269s_sudo_remember.mjs.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import diskq, elevate, term_store, term_vault as vault, wire  # noqa: E402

from test_f17 import PW  # noqa: E402
from test_p414_vault import CardRig, sudo_answer  # noqa: E402


class Remember482(CardRig):
    def blob(self):
        diskq.flush()
        return json.dumps([o for _, o in self.sent], ensure_ascii=False) + "".join(
            p.read_text(errors="replace") for p in self.st.root.rglob("*.log"))

    def test_first_typed_save_then_next_sudo_is_face_id_only(self):
        async def go():
            t1 = asyncio.create_task(self.host.elevate.request(self.req()))
            c1 = await self.card()
            self.assertEqual((c1["v"]["has"], c1.get("helper_uv"), c1.get("fa")), (0, 0, wire.b64u(b"\x07" * 16)))
            self.assertFalse(c1.get("passkey_required"))                       # P573: the typed card is one tap — unless 记住
            vs = wire.unb64u(c1["v"]["vs"])
            await self.host._app(self.s, sudo_answer(self.ph, c1, PW.encode(), save_prf=self.prf))
            r1 = await asyncio.wait_for(t1, 15)
            t2 = asyncio.create_task(self.host.elevate.request(self.req()))
            c2 = await self.card()
            self.assertNotEqual(c2["id"], c1["id"])
            self.assertEqual(c2["v"]["has"], 1)                                # the next card: Face ID fills it in
            self.assertEqual(wire.unb64u(c2["v"]["vs"]), vs)                   # over the salt the save used
            await self.host._app(self.s, sudo_answer(self.ph, c2, self.prf + os.urandom(32), vault_ans=True))
            return r1, await asyncio.wait_for(t2, 15), c1, c2
        r1, r2, c1, c2 = asyncio.run(go())
        self.assertEqual((r1["result"], r1.get("saved")), ("done", 1), r1)
        self.assertEqual(self.done(c1["id"])[-1].get("saved"), 1)
        self.assertEqual((r2["result"], r2["code"]), ("done", 0), r2)        # sudo accepted the remembered password
        self.assertNotIn("saved", self.done(c2["id"])[-1])
        self.assertNotIn(PW, self.blob())
        self.assertFalse(any(r.get("ev") == "vault_save" for r in self._host_log()))
        self.assertEqual(elevate.read_ledger(self.st)["fails"], 0)

    def test_a_save_the_host_cannot_keep_is_logged_not_silent(self):
        with mock.patch.object(vault, "create", side_effect=OSError("disk full")):
            async def go():
                t = asyncio.create_task(self.host.elevate.request(self.req()))
                c = await self.card()
                await self.host._app(self.s, sudo_answer(self.ph, c, PW.encode(), save_prf=self.prf))
                return await asyncio.wait_for(t, 15), c
            res, c = asyncio.run(go())
        self.assertEqual(res["result"], "done")
        self.assertNotIn("saved", res)
        self.assertNotIn("saved", self.done(c["id"])[-1])                    # the page then says 「这次没有记住密码」
        rows = [r for r in self._host_log() if r.get("ev") == "vault_save"]
        self.assertEqual([(r.get("result"), r.get("why")) for r in rows], [("not_saved", "store:OSError")], rows)
        self.assertIsNone(vault.wrap_meta(self.st, self.ph.did))
        self.assertNotIn(PW, self.blob())
        self.assertNotIn("disk full", self.blob())                             # the exception text never reaches a log

    def test_a_wrong_password_saves_nothing_and_the_retry_saves(self):
        other = "-".join(("not", "the", "pw"))

        async def go():
            t = asyncio.create_task(self.host.elevate.request(self.req()))
            c = await self.card()
            await self.host._app(self.s, sudo_answer(self.ph, c, other.encode(), save_prf=self.prf))
            again = await self.card()
            self.assertEqual(again["bad"], 1)
            self.assertIsNone(vault.wrap_meta(self.st, self.ph.did))
            await self.host._app(self.s, sudo_answer(self.ph, again, PW.encode(), save_prf=self.prf))
            return await asyncio.wait_for(t, 15)
        self.assertEqual((lambda r: (r["result"], r.get("saved")))(asyncio.run(go())), ("done", 1))
        self.assertEqual(bytes(term_store.load(self.st, self.ch, self.ph.did, wire.b64u(b"\x07" * 16), self.prf)),
                         PW.encode())

    def _host_log(self):
        diskq.flush()
        out = []
        for p in self.st.root.rglob("*.log"):
            for line in p.read_text(errors="replace").splitlines():
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if isinstance(o, dict):
                    out.append(o)
        return out


if __name__ == "__main__":
    unittest.main()
