"""0.30.5 hotfix (ADR-0305): host_write signatures — controls.canonical_json is wire.js canonicalJson, byte for byte.

Field fault since 0.30.1 (mail triage): a phone mail_set / mail_test host_write target carries
`before.triage.usd_per_day = 0.0`. The host hashed json.dumps ("0.0"), the page hashed wire.js canonicalJson after JSON.parse
("0") -> `host_write refused:bad_signature` on every phone mail settings save.

Three pieces of release evidence, all re-runnable:
  1. Bytes: the REAL wire.js canonicalJson (node) and controls.canonical_json give identical bytes + SHA-256 over one batch;
     the values the two sides cannot agree on are refused by the host (ValueError / TypeError -> check() "shape").
  2. No regression: wire.js canonicalJson is unchanged from ship/v0.30.0 to ship/v0.30.4 (git history), and for objects
     without floats the ship/v0.30.4 host text and the new text are byte-identical.
  3. End to end: the page signs a mail_set target holding usd_per_day = 0.0 -> verifies; the same signature against the
     ship/v0.30.4 object_text is refused bad_signature (negative control).
"""
import _hermetic  # noqa: F401,I001
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import random
import shutil
import struct
import subprocess
import time
import unittest
from unittest.mock import patch

from agentj import controls, mail, passkey, wire

AJ = pathlib.Path(__file__).resolve().parents[2]
WIRE_JS = (AJ / "web" / "public" / "proto" / "wire.js", AJ / "protocol" / "wire.js")
BASE_REF = "ship/v0.30.4"
# The release machine's pinned node (mise 26.7.0) when present, else AJ_NODE, else PATH; none -> the node classes skip.
_PINNED_NODE = [pathlib.Path(p).expanduser() for p in (os.environ.get("AJ_NODE") or "",
                "~/.local/share/mise/installs/node/26.7.0/bin/node") if p]
NODE = next((str(p) for p in _PINNED_NODE if p.is_file()), None) or shutil.which("node")
GROUP = ("host_write", "heartbeat_write", "event_write", "owner_hosts_set")


def js_canon(texts_json: str, wire_js=WIRE_JS[0]):
    """[[canonicalJson text, sha256 hex of its UTF-8]] from the real wire.js, for a JSON array given as text (JSON.parse
    first, exactly as the phone receives a target over the wire)."""
    js = (f"import {{ canonicalJson }} from {json.dumps(wire_js.as_uri())};\n"
          "import { createHash } from 'node:crypto';\n"
          "let s='';process.stdin.setEncoding('utf8');process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{"
          "console.log(JSON.stringify(JSON.parse(s).map(o=>{const t=canonicalJson(o);"
          "return [t, createHash('sha256').update(Buffer.from(t,'utf8')).digest('hex')];})));});")
    r = subprocess.run([NODE, "--input-type=module", "-e", js], input=texts_json.encode(), capture_output=True, timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr.decode(errors="replace"))
    return json.loads(r.stdout)


def js_texts(objs, wire_js=WIRE_JS[0]):
    return [t for t, _ in js_canon(json.dumps(objs, ensure_ascii=False), wire_js)]


def host(o):
    t = controls.canonical_json(o)
    return [t, hashlib.sha256(t.encode()).hexdigest()]


# Values both sides must agree on (evidence 1).
AGREE = [
    {"kind": "mail_set", "cfg": {"auth_servid": "mx.example.com"}, "before": mail.blank_config()},
    {"kind": "mail_test", "before": mail.blank_config()},
    {"usd_per_day": 0.0}, {"x": 1.0}, {"x": -0.0}, {"x": -3}, {"x": -1.5}, {"x": 0.1}, {"x": 1e-7}, {"x": 1e21},
    {"x": 1.5e300}, {"x": -1.5e300}, {"x": 1e-6}, {"x": 1e20}, {"x": 9.999999999999999e20}, {"x": 5e-324},
    {"x": 1.7976931348623157e308}, {"x": 123.456}, {"x": 0.01}, {"x": 2.5e-06}, {"x": 1e16},
    {"x": 2 ** 53}, {"x": -(2 ** 53)}, {"x": 2 ** 53 + 2}, {"x": float(2 ** 53)}, {"x": 2 ** 53 - 1}, {"x": 0}, {"x": 7},
    {"a": {"b": [1, {"c": [0.0, [], {}]}, [[2.5]]]}, "z": [{"y": None}]},
    {"名字": "王 · 主人", "é": "x", "～": 2, "\U0001f600": 1, "s": "q\"\\\n\t\r\b\f\x01\x1f\x7f  /"},
    {"b": 1, "a": 2, "B": 3, "_": 4, "aa": 5, "10": 6, "9": 7, "": 8},
    {"t": True, "f": False, "n": None}, {}, [], {"e": {}, "l": []}, [None, True, False, 0, "", [], {}],
    "plain", 0, None, True,
]

# Values JSON.parse + canonicalJson cannot reproduce: the host must refuse them, not hash its own spelling.
# (JSON text for node, Python object for the host, what JS writes)
DIVERGE_INTS = [2 ** 53 + 1, -(2 ** 53) - 1, 2 ** 60, 10 ** 21, 2 ** 70, 10 ** 400]


@unittest.skipUnless(NODE, "node not installed: Python <-> wire.js byte comparison skipped")
class Evidence1Bytes(unittest.TestCase):
    def test_bytes_and_digests_equal_both_wire_copies(self):
        for wire_js in WIRE_JS:
            js = js_canon(json.dumps(AGREE, ensure_ascii=False), wire_js)
            for o, j in zip(AGREE, js):
                with self.subTest(wire_js=wire_js.name, o=o):
                    self.assertEqual(host(o), j)

    def test_group_actions_use_it(self):
        o = {"before": {"triage": {"usd_per_day": 0.0, "usd_per_call": 0.01}}, "kind": "mail_set"}
        want = js_texts([o])[0]
        self.assertEqual(want, '{"before":{"triage":{"usd_per_call":0.01,"usd_per_day":0}},"kind":"mail_set"}')
        for action in GROUP:
            self.assertEqual(controls.object_text(action, o), want, action)

    def test_seeded_double_fuzz(self):
        rnd = random.Random(30405)
        xs = []
        while len(xs) < 4800:
            x = struct.unpack("<d", struct.pack("<Q", rnd.getrandbits(64)))[0]   # any bit pattern
            if math.isfinite(x):
                xs.append(x)
        for _ in range(200):
            xs += [rnd.random() * 10 ** rnd.randint(-8, 23), -float(rnd.randint(0, 10 ** 6)) * 10 ** rnd.randint(-7, 22),
                   round(rnd.random(), 6), round(rnd.uniform(0, 20), 2)]
        want = js_texts([[x] for x in xs])
        bad = [(x, w) for x, w in zip(xs, want) if controls.canonical_json([x]) != w]
        self.assertEqual(bad[:5], [], f"{len(bad)} of {len(xs)} differ")

    def test_big_integers_diverge_and_host_refuses(self):
        js = js_texts([[n] for n in DIVERGE_INTS])
        for n, j in zip(DIVERGE_INTS, js):
            with self.subTest(n=n):
                self.assertNotEqual(j, "[" + str(n) + "]")          # JS reads/writes another number
                with self.assertRaises(ValueError):
                    controls.canonical_json({"x": n})

    def test_non_finite_and_odd_values_refused(self):
        # JSON.stringify writes NaN / ±Infinity as null; the host cannot send them as JSON at all -> refuse.
        r = subprocess.run([NODE, "--input-type=module", "-e",
                            f"import {{ canonicalJson }} from {json.dumps(WIRE_JS[0].as_uri())};"
                            "console.log(canonicalJson([NaN, Infinity, -Infinity, '\\ud800']))"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), '[null,null,null,"\\ud800"]', r.stderr)
        for odd in (float("nan"), float("inf"), float("-inf"), "\ud800", {"\udc00": 1}):
            with self.subTest(odd=repr(odd)), self.assertRaises(ValueError):
                controls.canonical_json({"x": odd})
        for odd in ({1: "x"}, {"x": object()}, {"x": b"x"}, {"x": {1, 2}}):
            with self.subTest(odd=repr(odd)), self.assertRaises(TypeError):
                controls.canonical_json(odd)


def _git(*args):
    return subprocess.run(["git", "-C", str(AJ), *args], capture_output=True, text=True, timeout=30)


def _prefix():
    """This tree's path inside its git repository (export-neutral: no repository layout spelled in the source)."""
    return _git("rev-parse", "--show-prefix").stdout.strip()


def _have_ref(ref):
    return shutil.which("git") is not None and _git("rev-parse", "--verify", "-q", ref + "^{commit}").returncode == 0


def _old_controls():
    """ship/v0.30.4's agentj/controls.py, imported beside the current package (its `from . import wire, passkey`)."""
    src = _git("show", f"{BASE_REF}:{_prefix()}host/agentj/controls.py").stdout
    spec = importlib.util.spec_from_loader("agentj._controls_v0304", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "agentj"
    exec(compile(src, f"{BASE_REF}:controls.py", "exec"), mod.__dict__)
    return mod


def _rand_value(rnd, depth=0):
    pick = rnd.randrange(9 if depth < 4 else 6)
    if pick == 0:
        return None
    if pick == 1:
        return rnd.random() < 0.5
    if pick == 2:
        return rnd.randint(-(2 ** 53), 2 ** 53)
    if pick == 3:
        return rnd.randint(-1000, 1000)
    if pick in (4, 5):
        return _rand_str(rnd)
    if pick == 6:
        return [_rand_value(rnd, depth + 1) for _ in range(rnd.randrange(4))]
    return {_rand_str(rnd): _rand_value(rnd, depth + 1) for _ in range(rnd.randrange(5))}


_ALPHABET = "abcABZ_09 -\"\\\n\t\x00\x1f\x7féü名字王～ ￿"


def _rand_str(rnd):
    return "".join(rnd.choice(_ALPHABET) for _ in range(rnd.randrange(6)))


@unittest.skipUnless(_have_ref(BASE_REF), f"git ref {BASE_REF} not available (exported tree)")
class Evidence2NoRegression(unittest.TestCase):
    def test_wire_js_canonicaljson_unchanged_since_0300(self):
        def body(ref):
            src = _git("show", f"{ref}:{_prefix()}web/public/proto/wire.js").stdout
            i = src.index("export function canonicalJson")
            return src[i:src.index("\n}\n", i) + 2]
        now = body("HEAD")
        for v in ("0.30.0", "0.30.1", "0.30.2", "0.30.3", "0.30.4"):
            if _have_ref(f"ship/v{v}"):
                self.assertEqual(body(f"ship/v{v}"), now, v)

    def test_float_free_objects_unchanged_from_0304(self):
        old = _old_controls()
        rnd = random.Random(30500)
        objs = [o for o in AGREE if not _has_float(o)] + [{"x": n} for n in (2 ** 53, -(2 ** 53), 2 ** 53 + 2)]
        objs += [{"kind": rnd.choice(["mail_set", "bots", "x"]), "v": _rand_value(rnd)} for _ in range(3000)]
        objs = [o for o in objs if isinstance(o, dict) and not _has_float(o) and not _astral_key(o)]
        self.assertGreater(len(objs), 3000)
        for action in GROUP:
            diff = [o for o in objs if old.object_text(action, o) != controls.object_text(action, o)]
            self.assertEqual(diff[:3], [], f"{action}: {len(diff)} of {len(objs)} changed")

    def test_0304_differs_only_where_it_already_disagreed_with_the_page(self):
        # Floats and a key order mixing astral (U+10000+) with U+E000..U+FFFF keys: ship/v0.30.4 wrote neither like
        # wire.js, so no object the old host verified against a phone is in this set.
        old = _old_controls()
        for o in ({"usd_per_day": 0.0}, {"x": 1e-7}, {"～": 2, "\U0001f600": 1}):
            with self.subTest(o=o):
                self.assertNotEqual(old.object_text("host_write", o), controls.object_text("host_write", o))
                if NODE:
                    self.assertEqual(js_texts([o])[0], controls.object_text("host_write", o))
                    self.assertNotEqual(js_texts([o])[0], old.object_text("host_write", o))


def _has_float(o):
    if isinstance(o, float):
        return True
    if isinstance(o, dict):
        return any(_has_float(v) for v in o.values())
    if isinstance(o, list):
        return any(_has_float(v) for v in o)
    return False


def _astral_key(o):
    if isinstance(o, dict):
        return any(any(ord(c) > 0xFFFF for c in k) for k in o) or any(_astral_key(v) for v in o.values())
    if isinstance(o, list):
        return any(_astral_key(v) for v in o)
    return False


try:
    from test_p503_policy import HostExecution
except Exception:          # pragma: no cover - fixture module missing in an odd export
    HostExecution = unittest.IsolatedAsyncioTestCase


@unittest.skipUnless(NODE, "node not installed: page-signed end-to-end skipped")
class Evidence3PageSignedMailSet(HostExecution):
    async def _prepare(self, kind, cfg=None):
        target = {"kind": kind, **({"cfg": cfg} if kind == "mail_set" else {})}
        await self.h._app(self.session, {"t": "policy_prepare", "r": "c" * 16, "action": "host_write", "target": target})
        m = self.sent[-1][1]
        self.assertEqual(m["t"], "policy_ready", m)
        return json.loads(json.dumps(m)), m["n"]

    def _sign_as_page(self, prepared, n):
        digest = js_canon(json.dumps([prepared["target"]], ensure_ascii=False))[0][1]
        ts = int(time.time() * 1000)
        auth = {"n": n, "ts": ts, "sig": wire.b64u(self.ph.sk.sign(
            controls.signed_message(self.h.channel, self.ph.did, "host_write", n, ts, digest)))}
        auth["fa"] = self.auth.assert_(passkey.elevate_challenge(self.h.channel, self.ph.did, n, "host_write", n, digest),
                                       b"0", nonce=b"0", flags=5)
        return auth

    def _seed(self):
        cfg = mail.blank_config()
        cfg.update(preset="custom", address="owner@example.com", username="owner@example.com",
                   imap={"host": "imap.example.com", "port": 993, "security": "ssl"},
                   smtp={"host": "smtp.example.com", "port": 465, "security": "ssl"},
                   allow=[{"addr": "boss@example.com", "role": "owner"}])
        mail.save_config(self.st, mail.validate(cfg))

    def _results(self):
        p = self.st.controls_path
        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r["result"] for r in rows if r["action"] == "host_write"]

    async def test_page_signed_usd_per_day_zero_verifies_and_0304_refuses(self):
        self._seed()
        cfg = {"auth_servid": "mx.example.com"}
        prepared, n = await self._prepare("mail_set", cfg)
        usd = mail.read_config(self.st)["triage"]["usd_per_day"]
        self.assertIsInstance(usd, float)
        self.assertEqual(usd, 0.0)
        self.assertEqual(prepared["target"]["before"]["triage"]["usd_per_day"], 0.0)
        auth = self._sign_as_page(prepared, n)
        # Negative control first (the nonce is single use only after a verified signature): ship/v0.30.4's text.
        if _have_ref(BASE_REF):
            old = _old_controls()
            with patch.object(controls, "object_text", old.object_text):
                await self.h.on_mail(self.session, "mail_set", {"r": "d" * 16, "cfg": cfg, "authorization": auth})
            self.assertFalse(self.sent[-1][1]["ok"])
            self.assertEqual(mail.read_config(self.st)["auth_servid"], "")
            self.assertEqual(self._results()[-1], "refused:bad_signature")
            prepared, n = await self._prepare("mail_set", cfg)
            auth = self._sign_as_page(prepared, n)
        await self.h.on_mail(self.session, "mail_set", {"r": "e" * 16, "cfg": cfg, "authorization": auth})
        self.assertTrue(self.sent[-1][1]["ok"], self.sent[-1][1].get("why"))
        self.assertEqual(mail.read_config(self.st)["auth_servid"], "mx.example.com")
        self.assertEqual(self._results()[-1], "applied")

    async def test_mail_test_and_triage_writes_verify(self):
        self._seed()
        for cfg in ({"poll_minutes": 11}, {"triage": {**mail.blank_config()["triage"], "usd_per_day": 2.5}},
                    {"triage": {**mail.blank_config()["triage"], "usd_per_day": 0}}):
            with self.subTest(cfg=cfg):
                prepared, n = await self._prepare("mail_set", cfg)
                await self.h.on_mail(self.session, "mail_set", {"r": "e" * 16, "cfg": cfg, "authorization": self._sign_as_page(prepared, n)})
                self.assertTrue(self.sent[-1][1]["ok"], self.sent[-1][1].get("why"))
        prepared, n = await self._prepare("mail_test")
        with patch.object(mail, "load_secret", return_value=None):
            await self.h.on_mail(self.session, "mail_test", {"r": "a" * 16, "authorization": self._sign_as_page(prepared, n)})
        self.assertEqual([r for r in self._results() if r != "applied"], [])

    async def test_unrepresentable_values_refused_as_shape(self):
        for odd in (float("nan"), float("inf"), 2 ** 53 + 1, 10 ** 21, "\ud800"):
            target = {"kind": "mail_set", "cfg": {"x": odd}, "before": mail.blank_config()}
            n = self.h.nonces.issue_mail(self.ph.did, "0" * 64)
            auth = {"n": n, "ts": int(time.time() * 1000), "sig": "AA"}
            why = controls.check(self.st, self.h.nonces, self.h.channel, self.ph.did, auth, "host_write", target)
            self.assertEqual(why, "shape", repr(odd))


if __name__ == "__main__":
    unittest.main()
