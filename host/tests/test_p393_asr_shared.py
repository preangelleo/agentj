"""P393 (#120): instances of one OS user share one ASR model; status follows voice.asr.engine; voxtype language check.

- whoever installs first publishes the verified model + wheels to the shared folder; a later instance is set up from it with
  no download (explicit install, or lazily on its first take — `ready.asr` is already "ready");
- two installers at once: one download, the other waits and reuses;
- a damaged shared copy is dropped (names only) and downloaded again; the instances' own copies are never touched;
- a per-instance install from before this release seeds the shared folder lazily (model re-hashed, never moved);
- `agentj asr engine voxtype` → `agentj asr status` shows voxtype (the preference serve / doctor / phone use);
- switching to voxtype is refused when its config is English-only (a desktop default Whisper base.en) unless --force.
Downloads, the venv build, the self-test and voxtype itself are stubs: no network, no real engine, voxtype never runs.
"""
import _hermetic  # noqa: F401,I001
import argparse
import contextlib
import hashlib
import io
import json
import os
import pathlib
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from agentj import asr, cli, fence
from agentj.state import State

KEY = ("linux", "x86_64", "cp313")
BLOBS = {"model.int8.onnx": b"M" * 4096, "tokens.txt": b"tokens\n" * 10, "LICENSE": b"licence", "README.md": b"readme",
         "zh.wav": b"RIFFfake"}
WHEEL_BYTES = {"sherpa_onnx-1.13.8-cp313-cp313-manylinux.whl": b"W1" * 300,
               "sherpa_onnx_core-1.13.8-py3-none-manylinux.whl": b"W2" * 500}


def sha(b):
    return hashlib.sha256(b).hexdigest()


FILES = {n: ("test_wavs/zh.wav" if n == "zh.wav" else n, len(b), sha(b)) for n, b in BLOBS.items()}
WHEELS = {(*KEY[:2], tag): (f"aa/bb/{n}", len(WHEEL_BYTES[n]), sha(WHEEL_BYTES[n]))
          for tag, n in zip(("cp313", "py3"), WHEEL_BYTES)}
CONTENT = {**BLOBS, **WHEEL_BYTES}


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-p393-asr-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"AGENTJ_ASR_SHARED_DIR": str(self.tmp / "shared" / "asr"),
                                           "XDG_CONFIG_HOME": str(self.tmp / "config")})
        env.start()
        self.addCleanup(env.stop)
        self.a, self.b = self.tmp / "a", self.tmp / "b"
        for p in (self.a, self.b):
            State(p).init(relay="ws://127.0.0.1:1")
        self.fetches, self.lock = [], threading.Lock()
        self.fetch_delay = 0.0

        def fetch(urls, dest, size, sha_, out, label):
            with self.lock:
                self.fetches.append(dest.name)
            time.sleep(self.fetch_delay)
            dest.write_bytes(CONTENT[dest.name])
            return "stub"

        def make_venv(d, wheels, shas):
            for w, h in zip(wheels, shas):
                assert sha(w.read_bytes()) == h      # the venv is only ever built from hash-checked wheels
            (d / "venv" / "bin").mkdir(parents=True, exist_ok=True)
            (d / "venv" / "bin" / "python").write_text("#!/bin/sh\n")

        self.siblings = []
        for p in (mock.patch.object(asr, "platform_key", return_value=KEY), mock.patch.object(asr, "FILES", FILES),
                  mock.patch.object(asr, "WHEELS", WHEELS), mock.patch.object(asr, "route_order", return_value=["hf-mirror"]),
                  mock.patch.object(asr, "_fetch", side_effect=fetch), mock.patch.object(asr, "_make_venv", side_effect=make_venv),
                  mock.patch.object(asr, "_self_test"), mock.patch.object(asr, "_siblings", side_effect=lambda sd=None: list(self.siblings))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(asr.shutdown)

    def install(self, state):
        lines = []
        rc = asr.install(state, yes=True, out=lines.append)
        return rc, "\n".join(lines)

    def shared(self):
        return pathlib.Path(os.environ["AGENTJ_ASR_SHARED_DIR"])


class Shared(_Env):
    def test_second_instance_with_shared_model_downloads_nothing(self):
        self.assertEqual(self.install(self.a)[0], 0)
        self.assertEqual(sorted(self.fetches), sorted(CONTENT))          # first installer: everything, once
        self.assertTrue((self.shared() / "manifest.json").is_file())
        self.fetches.clear()
        self.assertEqual(asr.ready_state(self.b), "ready")               # the phone goes straight to ready
        rc, text = self.install(self.b)
        self.assertEqual(rc, 0, text)
        self.assertEqual(self.fetches, [])
        self.assertIn("no download", text)
        self.assertEqual(asr._installed_ok(asr.asr_dir(self.b)), (True, None))
        self.assertEqual(json.loads((asr.asr_dir(self.b) / "installed.json").read_text())["route"], "shared")
        own, shared = asr.asr_dir(self.b) / "model" / "model.int8.onnx", self.shared() / "model" / "model.int8.onnx"
        self.assertEqual(own.stat().st_ino, shared.stat().st_ino)        # one copy on disk (same file system)
        s = asr.status(self.b)
        self.assertTrue(s["shared"]["used"] and s["shared"]["present"])

    def test_first_take_on_second_instance_links_shared_model(self):
        self.install(self.a)
        self.fetches.clear()
        wav = self.tmp / "t.wav"
        import wave
        with wave.open(str(wav), "wb") as f:
            f.setnchannels(1), f.setsampwidth(2), f.setframerate(16000), f.writeframes(b"\x01\x00" * 16000)
        worker = mock.Mock()
        worker.request.return_value = {"ok": True, "text": "你好"}
        with mock.patch.object(asr, "_resident", return_value=worker):
            r = asr.transcribe(str(wav), timeout_s=30, state_dir=self.b)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.fetches, [])
        self.assertEqual(asr._installed_ok(asr.asr_dir(self.b)), (True, None))

    def test_concurrent_installers_download_once(self):
        self.fetch_delay = 0.05
        rcs = {}
        ts = [threading.Thread(target=lambda s=s: rcs.__setitem__(s, self.install(s)[0])) for s in (self.a, self.b)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        self.assertEqual(rcs, {self.a: 0, self.b: 0})
        self.assertEqual(sorted(self.fetches), sorted(CONTENT))          # every artifact fetched exactly once
        for s in (self.a, self.b):
            self.assertEqual(asr._installed_ok(asr.asr_dir(s)), (True, None))

    def test_corrupted_shared_copy_is_downloaded_again(self):
        self.install(self.a)
        bad = self.shared() / "model" / "model.int8.onnx"
        bad.unlink()                                                     # a different file, same size (A keeps its own link)
        bad.write_bytes(b"X" * len(BLOBS["model.int8.onnx"]))
        self.fetches.clear()
        rc, text = self.install(self.b)
        self.assertEqual(rc, 0, text)
        self.assertIn("model.int8.onnx", self.fetches)
        self.assertEqual(sha(bad.read_bytes()), FILES["model.int8.onnx"][2])          # republished, intact
        self.assertEqual(sha((asr.asr_dir(self.a) / "model" / "model.int8.onnx").read_bytes()), FILES["model.int8.onnx"][2])
        self.assertEqual(asr._installed_ok(asr.asr_dir(self.b)), (True, None))

    def test_old_per_instance_install_seeds_shared_without_model_download(self):
        self.install(self.a)
        shutil.rmtree(self.shared())                                     # as before this release: no shared folder,
        shutil.rmtree(asr.asr_dir(self.a) / "wheels")                    # and the wheels were deleted after install
        before = sha((asr.asr_dir(self.a) / "model" / "model.int8.onnx").read_bytes())
        self.siblings = [asr.asr_dir(self.a)]
        self.fetches.clear()
        rc, text = self.install(self.b)
        self.assertEqual(rc, 0, text)
        self.assertEqual(sorted(self.fetches), sorted(WHEEL_BYTES))      # only the two small wheels, never the model
        self.assertEqual(sha((asr.asr_dir(self.a) / "model" / "model.int8.onnx").read_bytes()), before)
        self.assertEqual(asr._installed_ok(asr.asr_dir(self.a)), (True, None))

    def test_remove_never_deletes_shared_or_other_instance(self):
        self.install(self.a)
        self.install(self.b)
        asr.remove(self.b, out=lambda _: None)
        self.assertTrue(asr.shared_present(KEY))
        self.assertEqual(asr._installed_ok(asr.asr_dir(self.a)), (True, None))

    def test_damaged_adoption_falls_back_to_not_installed(self):
        self.install(self.a)
        self.assertEqual(asr.ready_state(self.b), "ready")
        with mock.patch.object(asr, "_make_venv", side_effect=asr.InstallError("no venv")):
            self.assertFalse(asr.adopt_shared(self.b, 5))
        self.assertEqual(asr.ready_state(self.b), "not_installed")      # no endless "ready" that never transcribes

    def test_fence_hides_shared_folder(self):
        st = State(self.a)
        self.assertIn(os.path.realpath(self.shared()), fence.other_states(st, str(self.tmp)))


class EngineCli(_Env):
    def run_cli(self, *args):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.a)}), contextlib.redirect_stdout(buf):
            try:
                cli.cmd_asr(argparse.Namespace(args=list(args)))
                code = 0
            except SystemExit as e:
                code = e.code or 0
        return code, buf.getvalue()

    def voxtype_config(self, text):
        p = self.tmp / "config" / "voxtype" / "config.toml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def which(self, name, *a, **kw):
        return "/opt/fake/voxtype" if name == "voxtype" else shutil.which(name, *a, **kw)

    def test_status_shows_voxtype_after_engine_set(self):
        self.voxtype_config('engine = "sensevoice"\n[sensevoice]\nmodel = "sensevoice-small"\nlanguage = "zh"\n')
        with mock.patch("shutil.which", side_effect=self.which), mock.patch("subprocess.Popen") as popen:
            code, text = self.run_cli("engine", "voxtype")
            self.assertEqual(code, 0, text)
            self.assertIn("understands Chinese", text)
            code, text = self.run_cli("status", "--json")
        self.assertEqual(code, 0, text)
        s = json.loads(text)
        self.assertEqual((s["engine"], s["state"], s["active"]), ("voxtype", "ready", "voxtype"))
        popen.assert_not_called()                                         # voxtype itself never runs

    def test_voxtype_english_whisper_refused_unless_force(self):
        self.voxtype_config('engine = "whisper"\n[whisper]\nmodel = "base.en"\nlanguage = "auto"\n')
        with mock.patch("shutil.which", side_effect=self.which), mock.patch("subprocess.Popen") as popen:
            code, text = self.run_cli("engine", "voxtype")
            self.assertEqual(code, 2)
            self.assertIn("English-only", text)
            self.assertIn("只认英文", text)
            _, status = self.run_cli("status", "--json")
            self.assertNotEqual(json.loads(status)["engine"], "voxtype")
            code, text = self.run_cli("engine", "voxtype", "--force")
            self.assertEqual(code, 0, text)
            self.assertEqual(json.loads(self.run_cli("status", "--json")[1])["engine"], "voxtype")
        popen.assert_not_called()

    def test_language_detection_table(self):
        cases = [("", False),                                              # no config: voxtype's own default base.en
                 ('engine = "whisper"\n[whisper]\nmodel = "large-v3"\nlanguage = "auto"\n', True),
                 ('engine = "whisper"\n[whisper]\nmodel = "small"\nlanguage = "en"\n', False),
                 ('engine = "whisper"\n[whisper]\nmodel = "base.en"\nmode = "remote"\nremote_model = "qwen3-asr"\n', True),
                 ('engine = "whisper"\n[whisper]\nmode = "remote"\nremote_model = "x"\n', None),
                 ('engine = "parakeet"\n', False), ('engine = "paraformer"\n', True), ("not toml [", None)]
        for text, want in cases:
            p = self.tmp / "vt.toml"
            if text:
                p.write_text(text)
            else:
                p.unlink(missing_ok=True)
            self.assertEqual(asr.voxtype_language(p)["chinese"], want, text)


if __name__ == "__main__":
    unittest.main()
