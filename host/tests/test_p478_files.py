"""P478: real local files, mocked macOS boundary, existing signed owner protocol."""
import _hermetic  # noqa: F401
import asyncio
import errno
import json
import os
import pathlib
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from agentj import mac_file_copy as M
from agentj import local_files as F, controls, wire, doctor, first_run, ctl_auth


class Files(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p478-")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        self.state = self.root / "state"; self.state.mkdir()
        self.work = self.root / "work"; self.work.mkdir()
        self.source = self.root / "Downloads"; self.source.mkdir()
        self.file = self.source / 'recording "中文".txt'; self.file.write_text("meeting notes")
        self.logs = []
        self.st = SimpleNamespace(root=self.state, agent_config=lambda: {"dir": str(self.work)},
          write_private=lambda p, b: pathlib.Path(p).write_bytes(b), log=lambda *a, **kw: self.logs.append((a, kw)))
        self.h = SimpleNamespace(st=self.st, lang="zh", stopped=lambda: False, question=AsyncMock(return_value=("answer", [[1]])),
                                 send_app=AsyncMock(), nonces=controls.Nonces(), channel="test-channel")
        self.h.agent = SimpleNamespace(native_source={"kind":"terminal","role":"owner"})
        self.svc = F.Files(self.h)
        # Test fixture declares only its isolated host state as protected.
        self.hidden = patch.object(F.J, "hidden", return_value=[str(self.state)])
        self.hidden.start(); self.addCleanup(self.hidden.stop)

    async def fetch(self):
        return await self.svc.fetch({"path": str(self.file)})

    async def test_once_import_preserves_source_no_grant_and_prompts_each_time(self):
        for _ in range(2):
            result = await self.fetch(); self.assertTrue(result["ok"], result)
            self.assertEqual(pathlib.Path(result["path"]).read_text(), "meeting notes")
            self.assertEqual(os.stat(result["path"]).st_mode & 0o777, 0o600)
        self.assertEqual(self.file.read_text(), "meeting notes")
        self.assertEqual(F.grants(self.st), [])
        self.assertEqual(self.h.question.await_count, 2)

    async def test_deny_cancel_timeout_no_device_typed_and_malformed_never_copy(self):
        for decision, picks in [("answer", [[3]]), ("cancel", None), ("timeout", None), ("no_device", None),
                                ("answer", ["yes"]), ("answer", [[1, 2]])]:
            self.h.question.return_value = decision, picks
            self.assertFalse((await self.fetch())["ok"])
        self.assertEqual(list(self.work.iterdir()), [])

    async def test_permanent_skips_card_until_revoked(self):
        self.h.question.return_value = "answer", [[2]]
        self.assertTrue((await self.fetch())["ok"])
        self.assertTrue(F.has_grant(self.st, self.file))
        self.h.question.reset_mock()
        self.assertTrue((await self.fetch())["ok"]); self.h.question.assert_not_awaited()
        F.save(self.st, {"grants": []})
        self.h.question.return_value = "answer", [[3]]
        self.assertFalse((await self.fetch())["ok"])

    async def test_expiry_and_invalid_expiry(self):
        F.grant(self.st, str(self.source), int(time.time()) + 5)
        self.assertTrue(F.has_grant(self.st, self.file))
        with patch.object(F.time, "time", return_value=time.time() + 10):
            self.assertFalse(F.has_grant(self.st, self.file))
        for expires in (True, -1, 1, int(time.time()) + 90000):
            with self.assertRaises(F.Refused): F.grant(self.st, str(self.source), expires)

    async def test_source_mutation_during_card_refused(self):
        async def answer(*args):
            self.file.write_text("changed"); return "answer", [[1]]
        self.h.question.side_effect = answer
        self.assertFalse((await self.fetch())["ok"])
        self.assertEqual(list(self.work.iterdir()), [])

    async def test_estop_denies_even_permanent_grant(self):
        F.grant(self.st, str(self.source), 0)
        self.h.stopped = lambda: True
        self.assertFalse((await self.fetch())["ok"])

    async def test_links_traversal_hidden_sensitive_roots_hardlinks_fifo(self):
        link = self.source / "link"; link.symlink_to(self.file)
        directory = self.root / "alias"; directory.symlink_to(self.source)
        dot = self.source / ".env"; dot.write_text("not a real credential")
        hard = self.source / "hard"; os.link(self.file, hard)
        fifo = self.source / "pipe"; os.mkfifo(fifo)
        for p in (link, directory / self.file.name, dot, fifo, self.state / "nope", pathlib.Path("relative")):
            with self.assertRaises((F.Refused, OSError)): F.allowed_path(self.st, str(p))
        with self.assertRaises(F.Refused): F.allowed_path(self.st, str(self.file))
        with self.assertRaises(F.Refused): F.allowed_path(self.st, str(self.source) + "/../Downloads/x")
        for p in (self.root, pathlib.Path("/")):
            with self.assertRaises(F.Refused): F.allowed_path(self.st, str(p), folder=True)

    async def test_permission_denial_linux_windows_never_finder(self):
        original = F.os.open
        def opening(path, flags, *args, **kw):
            if str(path) == str(self.file): raise PermissionError(errno.EACCES, "denied")
            return original(path, flags, *args, **kw)
        for platform in ("linux", "win32"):
            with patch.object(F.sys, "platform", platform), patch.object(F.os, "open", side_effect=opening), patch.object(F, "finder_copy") as finder:
                self.assertFalse((await self.fetch())["ok"]); finder.assert_not_called()

    async def test_tcc_denied_uses_finder_with_literal_paths(self):
        original = F.os.open
        def opening(path, flags, *args, **kw):
            if str(path) == str(self.file): raise PermissionError(errno.EPERM, "denied")
            return original(path, flags, *args, **kw)
        def finder(st, source, dest, before): (dest / source.name).write_bytes(source.read_bytes())
        with patch.object(F.sys, "platform", "darwin"), patch.object(F.os, "open", side_effect=opening), patch.object(F, "finder_copy", side_effect=finder) as cp:
            result = await self.fetch()
            self.assertTrue(result["ok"], result); self.assertEqual(result["method"], "finder")
            cp.assert_called_once(); self.assertEqual(cp.call_args.args[1], self.file)

    async def test_finder_not_authorized_does_not_send_copy(self):
        native = SimpleNamespace(preflight=lambda ask: False, duplicate=unittest.mock.Mock())
        with patch.object(M, "Native", return_value=native):
            with self.assertRaisesRegex(F.Refused, "local_permission"):
                F.finder_copy(self.st, self.file, self.work, F.allowed_path(self.st, str(self.file))[1])
            native.duplicate.assert_not_called()

    async def test_native_event_uses_file_reference_and_forbids_prompts(self):
        calls = []
        native = M.Native.__new__(M.Native)
        native.target = lambda: M.Desc()
        native.ae = SimpleNamespace(AECreateAppleEvent=lambda *a: 0, AEDisposeDesc=lambda *a: 0,
            AEPutParamPtr=lambda *a: calls.append(a) or 0, AESendMessage=lambda *a: calls.append(a) or 0,
            AEGetParamPtr=lambda *a: -1701)
        ref = b"file:///.file/id=1.12345/"
        native.duplicate(ref, self.work)
        self.assertEqual(calls[0][1:4], (M.code("----"), M.code("furl"), ref))
        self.assertEqual(calls[1][1:4], (M.code("insh"), M.code("furl"), self.work.as_uri().encode()))
        self.assertEqual(calls[2][2], 3 | 0x10 | 0x80000)

    async def test_finder_symlink_output_is_not_published(self):
        original = F.os.open
        def opening(path, flags, *args, **kw):
            if str(path) == str(self.file): raise PermissionError(errno.EPERM, "denied")
            return original(path, flags, *args, **kw)
        def finder(st, source, dest, before): (dest / source.name).symlink_to(source)
        with patch.object(F.sys, "platform", "darwin"), patch.object(F.os, "open", side_effect=opening), patch.object(F, "finder_copy", side_effect=finder):
            self.assertFalse((await self.fetch())["ok"])
        self.assertEqual(list(self.work.iterdir()), [])

    async def test_nonpermission_error_never_falls_back(self):
        original = F.os.open
        def opening(path, flags, *args, **kw):
            if str(path) == str(self.file): raise OSError(errno.EIO, "io")
            return original(path, flags, *args, **kw)
        with patch.object(F.os, "open", side_effect=opening), patch.object(F, "finder_copy") as cp:
            self.assertFalse((await self.fetch())["ok"]); cp.assert_not_called()

    async def test_revocation_before_publish_stops_copy(self):
        with self.assertRaises(F.Refused): F.copy_file(self.st, str(self.file), authorized=lambda: False)
        self.assertEqual(list(self.work.iterdir()), [])

    async def test_cancelled_fetch_cannot_publish_from_surviving_copy_thread(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        scan = F.media._hash_and_scan
        copy = F.copy_file
        def blocked(*args, **kw):
            entered.set(); release.wait(3)
            return scan(*args, **kw)
        def tracked(*args, **kw):
            try: return copy(*args, **kw)
            finally: done.set()
        with patch.object(F.media, "_hash_and_scan", side_effect=blocked), patch.object(F, "copy_file", side_effect=tracked):
            task = asyncio.create_task(self.fetch())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(done.wait, 3))
        self.assertEqual(list(self.work.iterdir()), [])

    async def test_revocation_during_output_removes_partial_copy(self):
        calls = 0
        def authorized():
            nonlocal calls
            calls += 1
            return calls < 3  # before publish and first chunk allowed; final check withdraws
        with self.assertRaises(F.Refused):
            F.copy_file(self.st, str(self.file), authorized=authorized)
        self.assertEqual(list(self.work.iterdir()), [])

    async def test_signed_settings_add_remove_tamper_and_replay(self):
        private = Ed25519PrivateKey.generate()
        pub = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.st.sign_key = lambda device: pub if device == "phone" else None
        s = SimpleNamespace(device="phone")
        with patch.object(controls, "log"):
            for op in ("add", "remove"):
                target = {"op": op, "path": str(self.source), "expires": 0}
                n, ts = os.urandom(16).hex(), int(time.time() * 1000)
                sig = private.sign(controls.signed_message("test-channel", "phone", "files_write", n, ts, controls.object_digest("files_write", target)))
                obj = {"t": "files_write", **target, "n": n, "ts": ts, "sig": wire.b64u(sig)}
                await self.svc.on_phone(s, {**obj, "expires": ts})
                self.assertFalse(self.h.send_app.call_args.args[1]["ok"])
                await self.svc.on_phone(s, obj)
                self.assertTrue(self.h.send_app.call_args.args[1]["ok"])
                self.assertEqual(F.has_grant(self.st, self.file), op == "add")
                await self.svc.on_phone(s, obj)
                self.assertEqual(self.h.send_app.call_args.args[1]["why"], "replay")

    async def test_doctor_status_never_prompts_and_prepare_is_claimed(self):
        self.assertIn("files_prepare", ctl_auth.SENSITIVE)
        with patch.object(F.sys, "platform", "darwin"), patch.object(M, "automation") as run, patch.object(F.os, "scandir") as scan:
            self.assertIsNotNone(doctor.check_local_files(self.st)); F.snapshot(self.st)
            run.assert_not_called(); scan.assert_not_called()

    async def test_prepare_other_platform_noop(self):
        for p in ("linux", "win32"):
            with patch.object(F.sys, "platform", p), patch.object(M, "automation") as run:
                self.assertTrue(F.prepare(self.st)["ok"]); run.assert_not_called()

    async def test_nonowner_cannot_use_even_permanent_grant(self):
        F.grant(self.st, str(self.source), 0)
        with patch("agentj.provenance.untrusted_turn", return_value=True):
            self.assertEqual((await self.fetch())["why"], "owner_turn_required")
        self.h.question.assert_not_awaited()

    async def test_macos_prepare_records_service_observations(self):
        with patch.object(F.sys, "platform", "darwin"), patch.object(pathlib.Path, "home", return_value=self.root), patch.object(M, "automation", return_value=True):
            result = F.prepare(self.st)
            self.assertEqual(result["tcc"]["folders"]["Downloads"], "readable")
            self.assertEqual(result["tcc"]["folders"]["Desktop"], "missing")
            self.assertEqual(result["tcc"]["automation"], "available")
            self.assertEqual(F.read(self.st)["tcc"], result["tcc"])

    def native_fixture(self, copy):
        def reference(p):
            p = pathlib.Path(p)
            return f"file:///.file/id=1.{p.stat().st_ino}/".encode(), str(p)
        return SimpleNamespace(preflight=lambda ask: True, reference=reference, duplicate=copy)

    async def test_swapped_reference_inode_is_refused_even_when_source_path_restored(self):
        copy = unittest.mock.Mock()
        native = self.native_fixture(copy)
        true_reference = native.reference
        def reference(p):
            raw, resolved = true_reference(p)
            return (b"file:///.file/id=1.0/", resolved) if p == self.file else (raw, resolved)
        native.reference = reference
        with patch.object(M, "Native", return_value=native):
            with self.assertRaisesRegex(M.NativeError, "unverifiable_reference"):
                M.duplicate(self.file, self.work, F.allowed_path(self.st, str(self.file))[1][0][-1], lambda p: None)
        copy.assert_not_called()

    async def test_unknown_reference_format_or_volume_is_refused(self):
        for wrong in (b"file:///normal/path", b"file:///.file/id=999.0/"):
            native = self.native_fixture(unittest.mock.Mock())
            reference = native.reference
            native.reference = lambda p: (wrong, str(p)) if p == self.file else reference(p)
            with patch.object(M, "Native", return_value=native):
                with self.assertRaises(M.NativeError):
                    M.duplicate(self.file, self.work, F.allowed_path(self.st, str(self.file))[1][0][-1], lambda p: None)
            native.duplicate.assert_not_called()

    async def test_stable_reference_import_never_reads_transient_symlink(self):
        private = self.state / "private.txt"; private.write_text("private text!")
        def copy(ref, dest):
            self.assertTrue(ref.endswith(f".{self.file.stat().st_ino}/".encode()))
            saved = self.file.with_suffix(".saved")
            self.file.rename(saved); self.file.symlink_to(private)
            try:
                # A native file-reference URL follows the original inode, represented
                # here by saved. It is never reconstructed from the original path.
                (dest / self.file.name).write_bytes(saved.read_bytes())
            finally:
                self.file.unlink(); saved.rename(self.file)
        native = self.native_fixture(copy)
        original = F.os.open
        def opening(path, flags, *args, **kw):
            if str(path) == str(self.file) and not flags & 0x8000:
                raise PermissionError(errno.EPERM, "simulated TCC")
            return original(path, flags, *args, **kw)
        with patch.object(M, "Native", return_value=native), patch.object(F.os, "open", side_effect=opening), patch.object(F.sys, "platform", "darwin"):
            result = await self.fetch()
        self.assertTrue(result["ok"], result)
        self.assertEqual(pathlib.Path(result["path"]).read_text(), "meeting notes")
        self.assertEqual(private.read_text(), "private text!")

    async def test_metadata_open_must_be_available_for_finder(self):
        native = self.native_fixture(unittest.mock.Mock())
        with patch.object(M, "Native", return_value=native), patch.object(M.os, "open", side_effect=PermissionError(errno.EPERM, "metadata blocked")):
            with self.assertRaises(PermissionError):
                M.duplicate(self.file, self.work, F.allowed_path(self.st, str(self.file))[1][0][-1], lambda p: None)
        native.duplicate.assert_not_called()

    async def test_home_path_grant_is_normalized_and_tcc_corruption_is_unknown(self):
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            F.grant(self.st, "~/Downloads", 0)
        self.assertTrue(F.has_grant(self.st, self.file))
        data = F.read(self.st); data["tcc"] = []; F.save(self.st, data)
        with patch.object(F.sys, "platform", "darwin"):
            self.assertIsNone(F.snapshot(self.st)["tcc"])

    async def test_parallel_scope_writes_do_not_lose_a_grant(self):
        from concurrent.futures import ThreadPoolExecutor
        second = self.root / "Documents"; second.mkdir()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(F.grant, self.st, str(p), 0) for p in (self.source, second)]
            for future in futures: future.result()
        self.assertEqual({g["path"] for g in F.grants(self.st)}, {str(self.source), str(second)})
