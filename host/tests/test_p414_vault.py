"""P414 (PROTOCOL §37.9–§37.11, ADR-P414-bang #135): 电脑密码保险箱 — the vault, §11 sudo cards that use it, `vshare`.

- vault: ciphertext only (vault-<kid>.json + wrap-<device>.json, 0600); prf needs the PRF; a new kid per remote that types it;
  M3 rotation (prf1 ‖ prf2 + vn); P408 login-<device>.bin migrates on first load; forget / unpair / passkey change drop the
  wrap; a kid without a wrap goes; L2 a kid goes only on sudo's own wrong-password words; `local` wraps (M5) never count.
- sudo cards: `v` per viewer; `vault:1` happy path (+ rotation); stale cached password → re-armed `stale:1`, no lock / tries;
  M5 a local wrap is refused; M2 a `pct` without the `save:` tail is refused; typed + save → `elev_done.saved`; #116 grant with
  a vault answer.
- vshare: happy (the other remote's PRF wraps K for the requester), deny, timeout, refusals (temporary, web, no_peer, has).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import controls, elevate, passkey, term_askpass, term_store, term_vault as vault, wire  # noqa: E402
from agentj.term_shell import Term  # noqa: E402

from test_f17 import PW, fake_sudo  # noqa: E402
from test_l1 import Phone, _owner_host as _host, _ready, _state  # noqa: E402

FA = {"id": wire.b64u(b"\x07" * 16), "cd": "e30", "ad": "AA", "sig": "AA"}
FA2 = {"id": wire.b64u(b"\x09" * 16), "cd": "e30", "ad": "AA", "sig": "AA"}
OTHER = "-".join(("not", "the", "pw"))
R = "abcdef0123456789"


def tmpdir():
    return tempfile.TemporaryDirectory(prefix="p414-", dir=os.environ.get("TEST_TMP") or None)


def set_pk(st, ph, fa=FA):
    st.set_passkey(ph.pub, {"id": fa["id"], "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0, "rp": "m.agentj.app",
                            "at": int(time.time())})


# ------------------------------------------------------------ storage
class Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.st = _state(self.tmp.name)
        self.prf, self.salt = os.urandom(32), os.urandom(32)

    def tearDown(self):
        self.tmp.cleanup()

    def files(self):
        return sorted(p.name.split("-")[0] for p in term_store.root(self.st).iterdir() if p.name.startswith(("wrap-", "vault-")))

    def test_ciphertext_only_prf_needed_and_a_kid_per_remote(self):
        k1 = term_store.save(self.st, "CH", "dev1", "cred", "prf", self.salt, bytearray(PW.encode()), self.prf)
        k2 = term_store.save(self.st, "CH", "dev2", "cred2", "local", os.urandom(32), bytearray(PW.encode()), None)
        self.assertNotEqual(k1, k2)
        self.assertEqual(self.files(), ["vault", "vault", "wrap", "wrap"])
        raw = b"".join(p.read_bytes() for p in term_store.root(self.st).iterdir() if p.is_file())
        self.assertNotIn(PW.encode(), raw)
        for p in term_store.root(self.st).glob("*-*.json"):
            self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
        self.assertIsNone(term_store.load(self.st, "CH", "dev1", "cred", None))
        self.assertIsNone(term_store.load(self.st, "CH", "dev1", "cred", os.urandom(32)))
        self.assertIsNone(term_store.load(self.st, "CH", "dev1", "other", self.prf))
        self.assertIsNone(term_store.load(self.st, "CH2", "dev1", "cred", self.prf))
        self.assertEqual(bytes(term_store.load(self.st, "CH", "dev1", "cred", self.prf)), PW.encode())
        self.assertEqual(bytes(term_store.load(self.st, "CH", "dev2", "cred2", None)), PW.encode())
        self.assertEqual(term_store.meta(self.st, "dev1")["kid"], k1)
        term_store.save(self.st, "CH", "dev1", "cred", "prf", self.salt, bytearray(PW.encode()), self.prf)   # a new kid
        self.assertEqual(self.files(), ["vault", "vault", "wrap", "wrap"])                                  # the old kid went

    def test_rotation_m3(self):
        term_store.save(self.st, "CH", "d", "cred", "prf", self.salt, bytearray(PW.encode()), self.prf)
        prf2, vn = os.urandom(32), os.urandom(32)
        self.assertEqual(bytes(term_store.load(self.st, "CH", "d", "cred", self.prf + prf2, vn)), PW.encode())
        self.assertEqual(term_store.meta(self.st, "d")["salt"], vn)
        self.assertIsNone(term_store.load(self.st, "CH", "d", "cred", self.prf))          # the old PRF opens nothing now
        self.assertEqual(bytes(term_store.load(self.st, "CH", "d", "cred", prf2)), PW.encode())
        self.assertEqual(term_store.meta(self.st, "d")["salt"], vn)                         # 32 B = no rotation

    def test_p408_file_migrates_on_first_load(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        iv = os.urandom(12)
        ct = AESGCM(term_store._key(self.st, "prf", self.salt, self.prf)).encrypt(iv, PW.encode(),
                                                                                 term_store.aad("prf", "CH", "d", "cred"))
        self.st.write_private(term_store.root(self.st) / "login-d.bin", json.dumps(
            {"v": 1, "mode": "prf", "salt": wire.b64u(self.salt), "cred": "cred", "ct": wire.b64u(iv + ct)}).encode())
        self.assertTrue(term_store.meta(self.st, "d")["legacy"])
        self.assertIsNone(term_store.load(self.st, "CH", "d", "cred", os.urandom(32)))     # a wrong PRF migrates nothing
        self.assertTrue((term_store.root(self.st) / "login-d.bin").exists())
        self.assertEqual(bytes(term_store.load(self.st, "CH", "d", "cred", self.prf)), PW.encode())
        self.assertFalse((term_store.root(self.st) / "login-d.bin").exists())
        m = term_store.meta(self.st, "d")
        self.assertEqual((m["mode"], m["salt"], "kid" in m), ("prf", self.salt, True))
        self.assertEqual(bytes(term_store.load(self.st, "CH", "d", "cred", self.prf)), PW.encode())

    def test_forget_gc_and_shared_kid(self):
        term_store.save(self.st, "CH", "a", "ca", "prf", self.salt, bytearray(PW.encode()), self.prf)
        prf_b, salt_b = os.urandom(32), os.urandom(32)
        self.assertTrue(vault.share(self.st, "CH", "a", "ca", self.prf, None, "b", "cb", salt_b, prf_b))
        self.assertEqual(vault.wrap_meta(self.st, "a")["kid"], vault.wrap_meta(self.st, "b")["kid"])
        self.assertEqual(bytes(term_store.load(self.st, "CH", "b", "cb", prf_b)), PW.encode())
        self.assertEqual(term_store.forget(self.st, "a"), 1)
        self.assertEqual(self.files(), ["vault", "wrap"])                                # b still holds the kid
        self.assertEqual(term_store.forget(self.st, "b"), 1)
        self.assertEqual(self.files(), [])                                               # no wrap left → the kid went

    def test_l2_wrong_password_kills_the_kid_and_every_wrap(self):
        term_store.save(self.st, "CH", "a", "ca", "prf", self.salt, bytearray(PW.encode()), self.prf)
        vault.share(self.st, "CH", "a", "ca", self.prf, None, "b", "cb", os.urandom(32), os.urandom(32))
        term_store.save(self.st, "CH", "c", "cc", "prf", os.urandom(32), bytearray(PW.encode()), os.urandom(32))
        self.assertEqual(term_store.forget_kid(self.st, "a"), 2)
        self.assertIsNone(vault.wrap_meta(self.st, "b"))
        self.assertIsNotNone(vault.wrap_meta(self.st, "c"))                              # another kid stays
        self.assertEqual(self.files(), ["vault", "wrap"])

    def test_drop_local_and_prune(self):
        a, b, c = Phone(self.st), Phone(self.st), Phone(self.st)
        for ph in (a, b, c):
            set_pk(self.st, ph)
        ch = self.st.config()["channel"]
        term_store.save(self.st, ch, a.did, FA["id"], "prf", self.salt, bytearray(PW.encode()), self.prf)
        term_store.save(self.st, ch, b.did, FA["id"], "local", os.urandom(32), bytearray(PW.encode()), None)
        term_store.save(self.st, ch, c.did, "an-old-credential", "prf", os.urandom(32), bytearray(PW.encode()), os.urandom(32))
        term_store.prune(self.st)                       # nobody on the terminal list
        self.assertIsNotNone(vault.wrap_meta(self.st, a.did))     # a prf wrap follows the remote, not the terminal list
        self.assertIsNone(vault.wrap_meta(self.st, b.did))        # a local wrap is terminal-only (owner entry)
        self.assertIsNone(vault.wrap_meta(self.st, c.did))        # the passkey changed
        term_store.save(self.st, ch, b.did, FA["id"], "local", os.urandom(32), bytearray(PW.encode()), None)
        self.assertEqual(term_store.drop_local(self.st), 1)
        self.assertIsNone(vault.wrap_meta(self.st, b.did))
        d = self.st.devices()
        d.pop(a.did)                                    # unpaired
        self.st.write_private(self.st.devices_path, json.dumps(d).encode())
        term_store.prune(self.st)
        self.assertEqual(self.files(), [])

    def test_info_block_m5(self):
        a, b, c = Phone(self.st), Phone(self.st), Phone(self.st)
        for ph in (a, b, c):
            set_pk(self.st, ph)
        ch = self.st.config()["channel"]
        self.assertEqual(vault.info(self.st, a.did), {"has": False, "any": False, "peers": 0, "mode": None})
        term_store.save(self.st, ch, a.did, FA["id"], "prf", self.salt, bytearray(PW.encode()), self.prf)
        term_store.save(self.st, ch, b.did, FA["id"], "local", os.urandom(32), bytearray(PW.encode()), None)
        self.assertEqual(vault.info(self.st, a.did), {"has": True, "any": True, "peers": 0, "mode": "prf"})
        self.assertEqual(vault.info(self.st, b.did), {"has": False, "any": True, "peers": 1, "mode": "local"})
        self.assertEqual(vault.info(self.st, c.did), {"has": False, "any": True, "peers": 1, "mode": None})

    def test_askpass_l2_only_on_sudos_own_words(self):
        """The terminal's askpass path: a second ask with another message keeps the kid; sudo's own words delete it."""
        from agentj import terminal
        st = self.st
        ph = Phone(st)
        set_pk(st, ph)
        ch = st.config()["channel"]
        term_store.save(st, ch, ph.did, FA["id"], "prf", self.salt, bytearray(PW.encode()), self.prf)
        vault.share(st, ch, ph.did, FA["id"], self.prf, None, "zz", "cz", os.urandom(32), os.urandom(32))
        host = types.SimpleNamespace(st=st, channel=ch, _post=lambda *a, **k: None, _send_ready=None, agent_notice=lambda t: None)
        desk = terminal.Desk(host)

        async def frame(x, data, prompt):
            pass
        desk._frame = frame
        s = types.SimpleNamespace(device=ph.did)
        x = Term(s, R, "prf", None, 15, time.monotonic, time.time)
        x.seen[5], x.recent = 1, "sudo: a terminal is required"

        async def go():
            with mock.patch.object(asyncio, "sleep", new=mock.AsyncMock()):
                self.assertIsNone(await desk._sudo_pw(x, 5))
            self.assertIsNotNone(vault.wrap_meta(st, ph.did))
            x.seen[6], x.recent = 1, "对不起，请重试。\n"
            self.assertIsNone(await desk._sudo_pw(x, 6))
        asyncio.run(go())
        self.assertIsNone(vault.wrap_meta(st, ph.did))
        self.assertIsNone(vault.wrap_meta(st, "zz"))
        self.assertTrue(term_askpass.SUDO_WRONG.search("Sorry, try again."))

    def test_r2_n3_askpass_output_alone_never_forgets(self):
        """R2-N3 (terminal): a command's own "Sorry, try again" before sudo first asks is not sudo's message — the first ask
        starts the window afresh; only a SECOND ask from the same sudo with sudo's words afterwards deletes the kid."""
        from agentj import terminal
        st = self.st
        ph = Phone(st)
        set_pk(st, ph)
        ch = st.config()["channel"]
        term_store.save(st, ch, ph.did, FA["id"], "prf", self.salt, bytearray(PW.encode()), self.prf)
        host = types.SimpleNamespace(st=st, channel=ch, _post=lambda *a, **k: None, _send_ready=None, agent_notice=lambda t: None,
                                     send_app=mock.AsyncMock())
        desk = terminal.Desk(host)
        desk._frame = mock.AsyncMock()
        s = types.SimpleNamespace(device=ph.did)
        x = Term(s, R, "prf", None, 15, time.monotonic, time.time)
        x.recent = "Sorry, try again.\nsudo: 3 incorrect password attempts\n"    # printed by an earlier command

        async def go():
            with mock.patch.object(term_askpass, "proc_info", return_value={"argv": ["sudo", "-A", "true"]}), \
                    mock.patch.object(term_askpass, "SUDO_TTL", 0.05):
                self.assertIsNone(await desk._sudo_pw(x, 41))                   # the first ask: a card (unanswered here)
            self.assertEqual(x.recent, "")
            self.assertIsNotNone(vault.wrap_meta(st, ph.did))
            with mock.patch.object(asyncio, "sleep", new=mock.AsyncMock()):
                self.assertIsNone(await desk._sudo_pw(x, 41))                   # asked again, no new sudo words: kept
            self.assertIsNotNone(vault.wrap_meta(st, ph.did))
        asyncio.run(go())
        self.assertTrue(host.send_app.await_count >= 1)


# ------------------------------------------------------------ §11 sudo cards with the vault (§37.10)
def sudo_answer(ph, card, value: bytes, *, kind=None, vault_ans=False, save_prf=None, sign_save=True, grant=None):
    """What the phone sends: the value sealed (kind `sudo`, or `vault` for the PRF), optional `pepk`/`pct` (kind
    `vault_save`, AAD digest `digest|sha256(ct)`) and the signature with `grant:` then `save:` lines."""
    rid, n, ts = card["id"], card["n"], int(time.time() * 1000)
    digest = elevate.shown_digest(card["kind"], elevate.shown_fields(card))
    epk, ct = elevate.seal(wire.unb64u(card["epk"]), value,
                           elevate.seal_aad(ph.channel, ph.did, rid, kind or ("vault" if vault_ans else card["kind"]), n, digest))
    ct_sha = hashlib.sha256(ct).hexdigest()
    m = {"t": "elev_answer", "id": rid, "ok": True, "n": n, "ts": ts, "epk": wire.b64u(epk), "ct": wire.b64u(ct), "fa": FA}
    save = None
    if save_prf is not None:
        pepk, pct = elevate.seal(wire.unb64u(card["epk"]), save_prf,
                                 elevate.seal_aad(ph.channel, ph.did, rid, "vault_save", n, digest + "|" + ct_sha))
        m.update(pepk=wire.b64u(pepk), pct=wire.b64u(pct))
        save = hashlib.sha256(pct).hexdigest() if sign_save else None
    if vault_ans:
        m["vault"] = 1
    if grant:
        m["grant"] = grant
    m["sig"] = wire.b64u(ph.sk.sign(elevate.signed_message(ph.channel, ph.did, rid, card["kind"], "allow", n, ts, digest,
                                                           ct_sha, grant, save)))
    return m


class CardRig(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        root = pathlib.Path(self.tmp.name)
        self.st, self.sent = _state(str(root)), []
        self.host = _host(self.st, self.sent)
        self.host.agent = types.SimpleNamespace(kind="claude", cur_send=None, native_source={"kind":"terminal","role":"owner"})
        self.ph = Phone(self.st)
        set_pk(self.st, self.ph)
        self.s = _ready(self.host, self.ph)
        self.s.p33 = True
        self.host.elevate.sudo = str(fake_sudo(root, PW))
        self.ch = self.host.channel
        self.prf, self.salt = os.urandom(32), os.urandom(32)
        self.pk = mock.patch.object(passkey, "verify_assertion", return_value=1)
        self.pk.start()
        self.seen = set()

    def tearDown(self):
        self.pk.stop()
        self.tmp.cleanup()

    def store(self, pw=PW, mode="prf", ph=None, prf=None, salt=None):
        ph = ph or self.ph
        term_store.save(self.st, self.ch, ph.did, FA["id"], mode, salt or self.salt, bytearray(pw.encode()),
                        (prf or self.prf) if mode == "prf" else None)

    def req(self, argv=("true",)):
        return elevate.norm_sudo({"argv": list(argv), "why": "Check the admin password works", "cwd": "/", "timeout": 30})

    async def card(self, cid=None, after=None):
        for _ in range(800):
            for c, obj in self.sent:
                if obj.get("t") == "elev" and (cid is None or c == cid) and (obj["id"], obj["n"]) not in self.seen:
                    self.seen.add((obj["id"], obj["n"]))
                    return obj
            await asyncio.sleep(.01)
        raise AssertionError("no card")

    def done(self, rid):
        return [o for _, o in self.sent if o.get("t") == "elev_done" and o["id"] == rid]


class SudoCards(CardRig):
    def test_card_shows_v_and_a_vault_answer_runs_sudo_and_rotates(self):
        self.store()

        async def go():
            task = asyncio.create_task(self.host.elevate.request(self.req()))
            card = await self.card()
            self.assertEqual(card["v"]["has"], 1)
            self.assertEqual(wire.unb64u(card["v"]["vs"]), self.salt)
            vn = wire.unb64u(card["v"]["vn"])
            prf2 = os.urandom(32)
            await self.host._app(self.s, sudo_answer(self.ph, card, self.prf + prf2, vault_ans=True))
            res = await asyncio.wait_for(task, 15)
            return res, vn, prf2
        res, vn, prf2 = asyncio.run(go())
        self.assertEqual((res["result"], res["code"]), ("done", 0), res)
        self.assertEqual(term_store.meta(self.st, self.ph.did)["salt"], vn)                     # M3: rotated
        self.assertEqual(bytes(term_store.load(self.st, self.ch, self.ph.did, FA["id"], prf2)), PW.encode())
        blob = json.dumps([o for _, o in self.sent], ensure_ascii=False) + "".join(
            p.read_text(errors="replace") for p in self.st.root.rglob("*.log"))
        self.assertNotIn(PW, blob)
        self.assertEqual(elevate.read_ledger(self.st)["fails"], 0)

    def test_stale_cached_password_rearms_without_counting(self):
        self.store(pw=OTHER)

        async def go():
            task = asyncio.create_task(self.host.elevate.request(self.req()))
            card = await self.card()
            await self.host._app(self.s, sudo_answer(self.ph, card, self.prf, vault_ans=True))
            again = await self.card()
            self.assertEqual(again["id"], card["id"])
            self.assertEqual((again.get("stale"), again["v"]["has"], again["tries"]), (1, 0, elevate.TRIES))
            self.assertNotEqual(again["n"], card["n"])
            self.assertIsNone(vault.wrap_meta(self.st, self.ph.did))                               # L2: the kid went
            self.assertEqual(elevate.read_ledger(self.st)["fails"], 0)                              # not a wrong-password try
            await self.host._app(self.s, sudo_answer(self.ph, again, PW.encode()))
            return await asyncio.wait_for(task, 15)
        self.assertEqual(asyncio.run(go())["result"], "done")

    def test_other_sudo_failure_keeps_the_kid(self):
        self.store(pw=OTHER)
        with mock.patch.object(elevate, "run_sudo", return_value={"result": "bad_password", "code": 1, "stdout": "",
                                                                  "stderr": "", "truncated": False, "wrong": False}):
            async def go():
                task = asyncio.create_task(self.host.elevate.request(self.req()))
                card = await self.card()
                await self.host._app(self.s, sudo_answer(self.ph, card, self.prf, vault_ans=True))
                return await asyncio.wait_for(task, 15)
            res = asyncio.run(go())
        self.assertEqual((res["result"], res.get("why")), ("failed", "sudo_failed"))
        self.assertIsNotNone(vault.wrap_meta(self.st, self.ph.did))

    def test_m5_a_local_wrap_never_answers(self):
        self.store(mode="local")

        async def go():
            task = asyncio.create_task(self.host.elevate.request(self.req()))
            card = await self.card()
            self.assertEqual(card["v"]["has"], 0)
            await self.host._app(self.s, sudo_answer(self.ph, card, os.urandom(32), vault_ans=True))
            await asyncio.sleep(0.3)
            self.assertFalse(task.done())
            self.assertTrue(any(r.get("reason") == "shape" and r.get("result") == "refused" for r in elevate.read_log(self.st)))
            await self.host._app(self.s, sudo_answer(self.ph, card, PW.encode()))               # the card is still open
            return await asyncio.wait_for(task, 15)
        self.assertEqual(asyncio.run(go())["result"], "done")

    def test_vault_needs_a_passkey_and_a_sudo_card(self):
        self.store()
        d = self.st.devices()
        d[self.ph.did].pop("pk")
        self.st.write_private(self.st.devices_path, json.dumps(d).encode())

        async def go():
            task = asyncio.create_task(self.host.elevate.request(self.req()))
            card = await self.card()
            self.assertNotIn("v", card)                                                           # no passkey: no vault
            await self.host._app(self.s, sudo_answer(self.ph, card, self.prf, vault_ans=True))
            await asyncio.sleep(0.3)
            self.assertFalse(task.done())
            task.cancel()
        asyncio.run(go())

    def test_m2_typed_save_needs_the_signed_tail_then_saves(self):
        async def go():
            task = asyncio.create_task(self.host.elevate.request(self.req()))
            card = await self.card()
            self.assertEqual(card["v"]["has"], 0)
            vs = wire.unb64u(card["v"]["vs"])
            await self.host._app(self.s, sudo_answer(self.ph, card, PW.encode(), save_prf=self.prf, sign_save=False))
            await asyncio.sleep(0.3)
            self.assertFalse(task.done())
            self.assertTrue(any(r.get("reason") == "shape" for r in elevate.read_log(self.st)))
            await self.host._app(self.s, sudo_answer(self.ph, card, PW.encode(), save_prf=self.prf))
            return await asyncio.wait_for(task, 15), vs, card
        res, vs, card = asyncio.run(go())
        self.assertEqual((res["result"], res.get("saved")), ("done", 1))
        self.assertEqual(self.done(card["id"])[-1].get("saved"), 1)
        self.assertEqual(term_store.meta(self.st, self.ph.did)["salt"], vs)
        self.assertEqual(bytes(term_store.load(self.st, self.ch, self.ph.did, FA["id"], self.prf)), PW.encode())

    def test_typed_wrong_password_saves_nothing(self):
        async def go():
            task = asyncio.create_task(self.host.elevate.request(self.req()))
            card = await self.card()
            await self.host._app(self.s, sudo_answer(self.ph, card, OTHER.encode(), save_prf=self.prf))
            again = await self.card()
            self.assertEqual(again["bad"], 1)
            task.cancel()
        asyncio.run(go())
        self.assertIsNone(vault.wrap_meta(self.st, self.ph.did))

    def test_signed_message_save_tail_after_grant(self):
        m = elevate.signed_message("C", "D", "i" * 32, "sudo", "allow", "n", 1, "d", "c", "g" * 8, "s" * 8).decode()
        self.assertTrue(m.endswith("\ngrant:" + "g" * 8 + "\nsave:" + "s" * 8))
        self.assertTrue(elevate.signed_message("C", "D", "i", "sudo", "allow", "n", 1, "d", "c", None, "s").decode()
                        .endswith("\nc\nsave:s"))

    def test_116_grant_with_a_vault_answer(self):
        self.store()
        got = []
        offer = {"h": "a" * 64, "fmt": "true", "program": "true", "options": []}
        with mock.patch.object(self.host.elevate.grants, "offer", return_value=offer), \
                mock.patch.object(self.host.elevate.grants, "create", side_effect=lambda c, d, sk, pw: got.append(bytes(pw))):
            async def go():
                task = asyncio.create_task(self.host.elevate.request(self.req()))
                card = await self.card()
                self.assertEqual(card["grant"]["h"], offer["h"])
                await self.host._app(self.s, sudo_answer(self.ph, card, self.prf, vault_ans=True, grant=offer["h"]))
                return await asyncio.wait_for(task, 15)
            self.assertEqual(asyncio.run(go())["result"], "done")
        self.assertEqual(got, [PW.encode()])

    def test_r2_n3_command_output_is_not_a_wrong_password(self):
        """R2-N3 (§11): the right stored password, a command that prints sudo's words and fails → the kid stays, no lock try."""
        self.store()
        argv = ("sh", "-c", 'echo "Sorry, try again" >&2; echo "sudo: 1 incorrect password attempt" >&2; exit 1')

        async def go():
            task = asyncio.create_task(self.host.elevate.request(self.req(argv)))
            card = await self.card()
            await self.host._app(self.s, sudo_answer(self.ph, card, self.prf, vault_ans=True))
            return await asyncio.wait_for(task, 15)
        res = asyncio.run(go())
        self.assertEqual((res["result"], res["code"]), ("done", 1), res)
        self.assertIsNotNone(vault.wrap_meta(self.st, self.ph.did))
        self.assertEqual(elevate.read_ledger(self.st)["fails"], 0)
        r = elevate.run_sudo(list(argv), bytearray(PW.encode()), "/", 30, self.host.elevate.sudo)
        self.assertEqual((r["result"], r["prompts"]), ("done", 1))
        r = elevate.run_sudo(["true"], bytearray(OTHER.encode()), "/", 30, self.host.elevate.sudo)
        self.assertEqual((r["result"], r["wrong"]), ("bad_password", True))          # sudo asked twice: really wrong

    def test_r2_n5_split_prf_halves_wiped_after_a_save(self):
        halves = []
        real = vault.split_prf

        def spy(prf):
            got = real(prf)
            halves.extend(h for h in got if h is not None)
            return got

        async def go():
            task = asyncio.create_task(self.host.elevate.request(self.req()))
            card = await self.card()
            await self.host._app(self.s, sudo_answer(self.ph, card, PW.encode(), save_prf=self.prf + os.urandom(32)))
            return await asyncio.wait_for(task, 15)
        with mock.patch.object(vault, "split_prf", side_effect=spy):
            res = asyncio.run(go())
        self.assertEqual((res["result"], res.get("saved")), ("done", 1))
        self.assertEqual(len(halves), 2)
        self.assertTrue(all(not any(h) for h in halves))
        self.assertEqual(bytes(term_store.load(self.st, self.ch, self.ph.did, FA["id"], self.prf)), PW.encode())

    def test_temporary_entry_gets_no_v(self):
        term_store.write_config(self.st, {"on": True, "entries": [{"device": self.ph.did, "sudo": True, "web": False,
                                                                   "until": int(time.time()) + 3600}], "minutes": 15})

        async def go():
            task = asyncio.create_task(self.host.elevate.request(self.req()))
            card = await self.card()
            task.cancel()
            return card
        self.assertNotIn("v", asyncio.run(go()))


# ------------------------------------------------------------ vshare (§37.11)
class Vshare(CardRig):
    def setUp(self):
        super().setUp()
        self.b = Phone(self.st, name="新手机")
        set_pk(self.st, self.b, FA2)
        self.sb = _ready(self.host, self.b, cid=12)
        self.sb.p33 = True
        self.desk = self.host.term_desk
        self.prf_b = os.urandom(32)

    def signed(self, ph, n):
        ts = int(time.time() * 1000)
        msg = controls.signed_message(ph.channel, ph.did, "vault_share", n, ts, controls.object_digest("vault_share", {}))
        return {"n": n, "ts": ts, "sig": wire.b64u(ph.sk.sign(msg))}

    async def ask(self):
        n0 = len(self.sent)
        await self.desk.on_phone(self.sb, {"t": "vault_share", "r": R})
        return [o for _, o in self.sent[n0:] if o.get("t") == "ctl_res"][-1]

    async def step2(self, c1):
        aad = elevate.seal_aad(self.b.channel, self.b.did, c1["n"], "vault_share", c1["n"],
                               controls.object_digest("vault_share", {}))
        pepk, pct = elevate.seal(wire.unb64u(c1["epk"]), self.prf_b, aad)
        n0 = len(self.sent)
        await self.desk.on_phone(self.sb, {"t": "vault_share", "r": R, **self.signed(self.b, c1["n"]), "fa": FA2,
                                           "pepk": wire.b64u(pepk), "pct": wire.b64u(pct)})
        return [o for _, o in self.sent[n0:]]

    def approve(self, card, prf, ok=True):
        if not ok:
            rid, n, ts = card["id"], card["n"], int(time.time() * 1000)
            digest = elevate.shown_digest("vshare", elevate.shown_fields(card))
            sig = self.ph.sk.sign(elevate.signed_message(self.ph.channel, self.ph.did, rid, "vshare", "deny", n, ts, digest, None))
            return {"t": "elev_answer", "id": rid, "ok": False, "n": n, "ts": ts, "sig": wire.b64u(sig)}
        return sudo_answer(self.ph, card, prf)

    async def result(self):
        for _ in range(800):
            got = [o for c, o in self.sent if c == 12 and o.get("t") == "ctl_res" and o.get("action") == "vault_share"
                   and o.get("why") != "passkey_required"]
            if got:
                return got[-1]
            await asyncio.sleep(.01)
        raise AssertionError("no result")

    def test_happy_path_m4_fields_and_rotation(self):
        self.store()

        async def go():
            c1 = await self.ask()
            self.assertEqual(c1["why"], "passkey_required")
            self.assertEqual(c1["fa"], FA2["id"])
            self.assertEqual(c1["fa_ch"], wire.b64u(passkey.elevate_challenge(
                self.ch, self.b.did, c1["n"], "vault_share", c1["n"], controls.object_digest("vault_share", {}))))
            out = await self.step2(c1)
            self.assertEqual(out[-1]["t"], "ctl_pending")
            card = await self.card(cid=self.s.cid)
            self.assertEqual(card["kind"], "vshare")
            self.assertEqual((card["dev"], card["cred"], card["flags"], card["v"]["has"]), (self.b.did, FA2["id"], [], 1))
            self.assertEqual(card["paired"], self.st.devices()[self.b.did]["paired_at"])
            self.assertFalse([o for c, o in self.sent if c == 12 and o.get("t") == "elev"])        # not to the requester
            prf2 = os.urandom(32)
            await self.host._app(self.s, self.approve(card, self.prf + prf2))
            return await self.result(), card, prf2
        res, card, prf2 = asyncio.run(go())
        self.assertTrue(res["ok"], res)
        self.assertEqual(bytes(term_store.load(self.st, self.ch, self.b.did, FA2["id"], self.prf_b)), PW.encode())
        self.assertEqual(term_store.meta(self.st, self.ph.did)["salt"], wire.unb64u(card["v"]["vn"]))   # M3 on the approver
        self.assertEqual(bytes(term_store.load(self.st, self.ch, self.ph.did, FA["id"], prf2)), PW.encode())
        log = [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]
        row = [r for r in log if r["action"] == "vshare"][-1]
        self.assertEqual((row["device"], row["by"], row["result"]), (self.b.did, self.ph.did, "ok"))
        self.assertEqual(self.desk.vault_info(self.b.did)["has"], True)

    def test_deny_and_timeout_store_nothing(self):
        self.store()

        async def go():
            await self.step2(await self.ask())
            card = await self.card(cid=self.s.cid)
            await self.host._app(self.s, self.approve(card, None, ok=False))
            first = await self.result()
            self.sent.clear()
            with mock.patch.dict(elevate.TTL, {"vshare": 0.3}):
                await self.step2(await self.ask())
                second = await self.result()
            return first, second
        first, second = asyncio.run(go())
        self.assertEqual((first["ok"], first["why"]), (False, "denied"))
        self.assertEqual((second["ok"], second["why"]), (False, "timeout"))
        self.assertIsNone(vault.wrap_meta(self.st, self.b.did))

    def test_refusals(self):
        async def why():
            return (await self.ask())["why"]

        async def go():
            self.assertEqual(await why(), "no_vault")
            self.store(mode="local")
            self.assertEqual(await why(), "no_peer")                   # only a local wrap elsewhere: nobody can approve
            self.store()
            term_store.write_config(self.st, {"on": True, "entries": [{"device": self.b.did, "sudo": True, "web": False,
                                                                       "until": int(time.time()) + 3600}], "minutes": 15})
            self.assertEqual(await why(), "temporary")
            term_store.write_config(self.st, {"on": True, "entries": [], "minutes": 15})
            d = self.st.devices()
            d[self.b.did]["rt"] = {"i": "x"}
            self.st.write_private(self.st.devices_path, json.dumps(d).encode())
            self.assertEqual(await why(), "web_not_allowed")
            term_store.write_config(self.st, {"on": True, "entries": [{"device": self.b.did, "sudo": True, "web": True}],
                                              "minutes": 15})
            self.assertEqual(await why(), "passkey_required")
            vault.share(self.st, self.ch, self.ph.did, FA["id"], self.prf, None, self.b.did, FA2["id"], os.urandom(32),
                        os.urandom(32))
            self.assertEqual(await why(), "has")
        asyncio.run(go())
        self.assertFalse([o for _, o in self.sent if o.get("t") == "elev"])

    def test_r2_n7_two_racing_asks_start_one_card(self):
        import threading
        self.store()
        gate, real = threading.Barrier(2, timeout=5), self.desk._vshare_refusal

        def held(s, busy=True):
            r = real(s, busy)
            if busy:                       # both asks have their verdict before either returns (each step): the race
                gate.wait()
            return r

        async def go():
            c1a, c1b = await self.ask(), await self.ask()
            with mock.patch.object(self.desk, "_vshare_refusal", side_effect=held):
                await asyncio.gather(self.step2(c1a), self.step2(c1b))
            await self.card(cid=self.s.cid)
            await asyncio.sleep(0.2)
            self.host.elevate.cancel_all("stopped")
            await self.result()
            for _ in range(200):
                if self.desk.sharing is None:
                    break
                await asyncio.sleep(.01)
        asyncio.run(go())
        mine = [o for c, o in self.sent if c == 12 and o.get("action") == "vault_share"]
        self.assertEqual(len([o for o in mine if o.get("t") == "ctl_pending"]), 1)
        self.assertEqual(len([o for o in mine if o.get("why") == "busy"]), 1)
        self.assertEqual(len([o for _, o in self.sent if o.get("t") == "elev" and o.get("kind") == "vshare"]), 1)
        self.assertIsNone(self.desk.sharing)

    def test_r2_n7_sink_rechecks_at_approval(self):
        self.store()

        async def go():
            await self.step2(await self.ask())
            card = await self.card(cid=self.s.cid)
            term_store.write_config(self.st, {"on": True, "entries": [{"device": self.b.did, "sudo": True, "web": False,
                                                                       "until": int(time.time()) + 3600}], "minutes": 15})
            await self.host._app(self.s, self.approve(card, self.prf))
            return await self.result()
        res = asyncio.run(go())
        self.assertEqual((res["ok"], res["why"]), (False, "failed"))
        self.assertIsNone(vault.wrap_meta(self.st, self.b.did))
        self.assertIsNone(self.desk.sharing)

    def test_web_flag_on_the_card(self):
        self.store()
        term_store.write_config(self.st, {"on": True, "entries": [{"device": self.b.did, "sudo": True, "web": True}],
                                          "minutes": 15})
        d = self.st.devices()
        d[self.b.did]["rt"] = {"i": "x"}
        d[self.b.did]["source"] = "account"
        self.st.write_private(self.st.devices_path, json.dumps(d).encode())

        async def go():
            await self.step2(await self.ask())
            card = await self.card(cid=self.s.cid)
            self.host.elevate.cancel_all("stopped")
            return card
        card = asyncio.run(go())
        self.assertEqual(card["flags"], ["web", "account"])
        self.assertEqual(elevate.shown_fields(card), [self.b.did, FA2["id"], str(card["paired"]), "web,account"])


class JsParity(unittest.TestCase):
    def test_js_signs_and_hashes_the_same_bytes(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        aj = pathlib.Path(__file__).resolve().parents[2]
        card = {"kind": "vshare", "dev": "DEV", "cred": "CRED", "paired": 1790000000, "flags": ["web", "account"]}
        for f in ("protocol/wire.js", "web/public/proto/wire.js"):
            js = (f"import {{ elevateMessage, elevateDigest, elevateFields, controlObject }} from {json.dumps((aj / f).as_uri())};\n"
                  f"const m = Buffer.from(elevateMessage('C', 'D', 'I', 'sudo', 'allow', 'N', 1, 'G', 'S', 'gg', 'ss')).toString('hex');\n"
                  f"const d = await elevateDigest('vshare', elevateFields({json.dumps(card)}));\n"
                  "console.log(JSON.stringify([m, d, controlObject('vault_share', {})]));")
            r = subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            want = [elevate.signed_message("C", "D", "I", "sudo", "allow", "N", 1, "G", "S", "gg", "ss").hex(),
                    elevate.shown_digest("vshare", elevate.shown_fields(card)), controls.object_text("vault_share", {})]
            self.assertEqual(json.loads(r.stdout), want, f)


if __name__ == "__main__":
    unittest.main()
