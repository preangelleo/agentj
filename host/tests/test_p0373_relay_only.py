"""0373: `agentj update apply` that installed the new version but whose only failed self-check row is `relay`.

A customer host that cannot reach the relay at that moment printed `UPGRADE_RESULT failed / doctor_failed /
failed_checks: relay` (exit 1) although the new version was installed, verified and its service running. Contract:
- only this one case becomes `UPGRADE_RESULT ok / reason: upgraded_relay_pending` (exit 0): the new program answered
  `--version` with the target, the service hand-off was accepted, and the self-check's blockers are exactly `relay`
  while its `service` row is ok (the manager says active; not an "unverifiable" row);
- the block's `why:` line names the new version, says the relay reconnects by itself and how to confirm (`agentj --version`);
- every other failure keeps its result, reason and exit code (a second blocker, no service, a service row that is not
  ok, a failed install / version check / service hand-off);
- security review O1: the doctor's failed `relay` row says why in a closed word (`relay_reason`, upgrade_diag.RELAY_REASONS).
  Only `unreachable` (the anonymous probe got a network error: nothing answered) may become the ok result. A failed signed
  host leg (`host_leg`: identity / replaced / limit / tls / auth refused / …), a probe that failed some other way
  (`probe_failed`), a missing word and a word outside the enum all stay `failed / doctor_failed`;
- security review r2 N1: `unreachable` is an allow-list — before the WebSocket handshake completed, a `TimeoutError`, a
  `ConnectionError` subclass, a `socket.gaierror`, or an `OSError` whose errno is ENETUNREACH / EHOSTUNREACH / ENETDOWN /
  EHOSTDOWN. `ssl.SSLError` and its subclasses (a certificate that does not verify, plaintext on the wss port),
  `PermissionError`, `FileNotFoundError`, any other `OSError` and anything after the handshake (the relay sends no first
  frame) are `probe_failed`. `RealProbe` raises them through the real websockets client against local stand-in servers.
The judgement is made by the updater that runs `apply` — the version being upgraded FROM. No new authority or network.
"""
import _hermetic  # noqa: F401
import contextlib
import datetime
import errno
import ipaddress
import json
import os
import pathlib
import socket
import ssl
import struct
import subprocess
import threading
from unittest import mock

from test_p277_upgrade_diag import _Env, NEW
from agentj import doctor, update, upgrade_diag

REASON = "upgraded_relay_pending"
NET_DOWN = (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.EHOSTDOWN)
NO_WORD = {"id": "relay", "status": "fail", "summary": "wss://relay.example unreachable (timeout)"}
RELAY = {**NO_WORD, "relay_reason": "unreachable"}
LEG_WHYS = ("identity", "replaced", "limit", "tls", "auth_refused", "bad_challenge", "closed", "http", "dns", "timeout",
            "proxy", "refused", "network", "error")
BASE = [{"id": "version", "status": "ok"}, {"id": "state", "status": "ok"},
        {"id": "serve", "status": "warn", "summary": "running · relay reconnecting"}]
SVC_OK = {"id": "service", "status": "ok", "summary": "agentj installed, active"}
RELAY_ONLY = BASE + [SVC_OK, RELAY]


class _Judge(_Env):
    def go(self, rows, svc_on=True, version=NEW, deferred=0):
        self.doctors = 0

        def run(argv, **kw):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {version}", "")
            if argv[-2:] == ["doctor", "--json"]:
                self.doctors += 1
                return subprocess.CompletedProcess(argv, int(any(r.get("status") == "fail" for r in rows)),
                                                   json.dumps({"checks": rows}), "")
            if "--deferred" in argv:
                return subprocess.CompletedProcess(argv, deferred, "", "x\n" if deferred else "")
            return subprocess.CompletedProcess(argv, 0, "ok\n", "")
        with mock.patch.object(update.time, "sleep"):
            return self.apply(run, svc_on=svc_on)

    def failed(self, res, checks):
        self.assertEqual((res["result"], res["reason"], res["exit"]), ("failed", "doctor_failed", 1))
        self.assertEqual(res["failed_checks"], checks)
        self.assertEqual(update.result_block(res).split("\n")[0], "UPGRADE_RESULT failed")

    def pending(self, row):
        self.assertEqual((row["status"], row.get("relay_reason")), ("fail", "unreachable"), row)
        res = self.go(BASE + [SVC_OK, row])
        self.assertEqual((res["result"], res["reason"], res["exit"]), ("ok", REASON, 0))

    def refused(self, row):
        self.assertEqual((row["status"], row.get("relay_reason")), ("fail", "probe_failed"), row)
        self.failed(self.go(BASE + [SVC_OK, row]), ["relay"])


class RelayOnly(_Judge):
    def test_installed_running_relay_only_is_not_a_failure(self):
        res = self.go(RELAY_ONLY)
        self.assertEqual((res["result"], res["reason"], res["exit"]), ("ok", REASON, 0))
        self.assertEqual((res["to"], res["service"]), (NEW, "restart_scheduled"))
        self.assertEqual(self.doctors, 1, "relay is not a row that settles: one look")
        block = update.result_block(res)
        lines = block.split("\n")
        self.assertEqual(lines[0], "UPGRADE_RESULT ok")
        self.assertIn(f"reason: {REASON}", lines)
        self.assertIn(f"to: {NEW}", lines)
        msg = [ln for ln in lines if ln.startswith("why: ")]
        self.assertEqual(len(msg), 1, block)
        for needle in (NEW, "agentj --version", "中继", "自动重连", "relay", "reconnect"):
            self.assertIn(needle, msg[0])
        self.assertFalse([ln for ln in lines if ln.startswith("message: ")], "not the plain success sentence")
        self.assertNotIn("{to}", block)
        self.assertNotIn("failed", block)
        self.assertFalse([ln for ln in lines if ln.startswith(("failed_checks:", "step:", "support:"))], block)
        self.assertNotEqual(lines[-1], "next: none")
        for banned in ("试用", "trial"):
            self.assertNotIn(banned, block)

    def test_record_and_report_follow_the_result(self):
        res = self.go(RELAY_ONLY)
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec["result"], rec["reason"], rec["to"]), ("ok", REASON, NEW))
        self.assertNotIn("failed_checks", rec)
        self.assertEqual(upgrade_diag.report(self.st, res, send=lambda text: self.fail("no report for ok")),
                         {"status": "skipped"})

    def test_a_second_blocker_stays_failed(self):
        for extra, ids in (({"id": "version", "status": "fail"}, ["relay", "version"]),
                           ({"id": "state", "status": "fail"}, ["relay", "state"]),
                           ({"id": "dashboard", "status": "fail"}, ["relay", "dashboard"]),
                           ({"id": "serve", "status": "fail"}, ["relay", "serve"]),
                           ({"status": "fail"}, ["relay", update.DOCTOR_UNNAMED])):
            with self.subTest(extra=extra):
                rows = [r for r in RELAY_ONLY if r.get("id") != extra.get("id")] + [extra]
                self.failed(self.go(rows), sorted(ids, key=[r.get("id") or update.DOCTOR_UNNAMED for r in rows].index))

    def test_service_not_known_running_stays_failed(self):
        for name, svc in (("missing", None), ("warn", {"id": "service", "status": "warn", "service_reason": "not_installed"}),
                          ("unverifiable", {**SVC_OK, "unverifiable": True})):
            with self.subTest(name):
                self.failed(self.go(BASE + ([svc] if svc else []) + [RELAY]), ["relay"])

    def test_service_failed_stays_failed(self):
        svc = {"id": "service", "status": "fail", "service_reason": "not_active:failed"}
        self.failed(self.go(BASE + [svc, RELAY]), ["service", "relay"])

    def test_relay_row_without_the_closed_word_stays_failed(self):
        self.failed(self.go(BASE + [SVC_OK, NO_WORD]), ["relay"])

    def test_relay_reason_outside_unreachable_stays_failed(self):
        for word in ("host_leg", "probe_failed", "identity", "replaced", "Unreachable", "unreachable ", "", None, True, 1,
                     ["unreachable"], {"unreachable": 1}):
            with self.subTest(word=word):
                self.failed(self.go(BASE + [SVC_OK, {**NO_WORD, "relay_reason": word}]), ["relay"])

    def test_two_relay_rows_stay_failed(self):
        self.failed(self.go(RELAY_ONLY + [{**NO_WORD, "relay_reason": "host_leg"}]), ["relay"])
        self.failed(self.go(RELAY_ONLY + [{"id": "relay", "status": "ok"}]), ["relay"])

    def leg_row(self, d):
        with mock.patch.object(doctor, "probe_relay", return_value=(True, "ok")), \
                mock.patch.object(doctor, "_fenced", return_value=False), \
                mock.patch.object(doctor, "_serve_status", return_value=None), \
                mock.patch.object(doctor, "probe_host_leg", return_value=(False, d)):
            return doctor.check_relay(self.st)

    def test_every_failed_host_leg_reason_stays_failed(self):
        for why in LEG_WHYS:
            with self.subTest(why=why):
                row = self.leg_row({"phase": "auth", "why": why, "code": None, "http": None, "kind": "X"})
                self.assertEqual((row["id"], row["status"], row.get("relay_reason")), ("relay", "fail", "host_leg"), row)
                self.failed(self.go(BASE + [SVC_OK, row]), ["relay"])

    def probe_row(self, error=None, answer=None):
        ws = mock.MagicMock()
        ws.__enter__.return_value.recv.return_value = answer
        with mock.patch("websockets.sync.client.connect", side_effect=error, return_value=ws):
            return doctor.check_relay(self.st)

    def test_only_a_network_error_of_the_probe_is_unreachable(self):
        for error in (TimeoutError(), ConnectionRefusedError(), ConnectionResetError(), ConnectionAbortedError(),
                      BrokenPipeError(), socket.gaierror(-2, "x"), *(OSError(n, "x") for n in NET_DOWN)):
            with self.subTest(error=error):
                self.pending(self.probe_row(error))

    def test_tls_permission_and_other_os_errors_of_the_probe_stay_failed(self):
        """Security review r2 N1: `unreachable` is an allow-list, not "any OSError"."""
        for error in (ssl.SSLError("x"), ssl.SSLCertVerificationError("x"), ssl.SSLEOFError(8, "x"),
                      ssl.SSLError(errno.ENETUNREACH, "x"), PermissionError(errno.EACCES, "x"), PermissionError(errno.EPERM, "x"),
                      FileNotFoundError(errno.ENOENT, "x"), OSError("x"), OSError(errno.EMFILE, "x"),
                      OSError(errno.EADDRNOTAVAIL, "x"), EOFError()):
            with self.subTest(error=error):
                self.refused(self.probe_row(error))

    def test_an_error_after_the_handshake_stays_failed(self):
        """The relay completed the WebSocket handshake: it answered, so silence or a drop after that is not `unreachable`."""
        for error in (TimeoutError(), ConnectionResetError(), BrokenPipeError(), OSError(errno.ENETUNREACH, "x")):
            with self.subTest(error=error):
                ws = mock.MagicMock()
                ws.__enter__.return_value.recv.side_effect = error
                with mock.patch("websockets.sync.client.connect", return_value=ws):
                    self.refused(doctor.check_relay(self.st))

    def test_a_probe_that_failed_another_way_stays_failed(self):
        for name, kw in (("import", {"error": ImportError("python_socks")}), ("value", {"error": ValueError("x")}),
                         ("runtime", {"error": RuntimeError("x")}), ("answer", {"answer": "not json"}),
                         ("other", {"answer": json.dumps({"t": "nope"})})):
            with self.subTest(name):
                row = self.probe_row(**kw)
                self.assertEqual((row["status"], row.get("relay_reason")), ("fail", "probe_failed"), row)
                self.failed(self.go(BASE + [SVC_OK, row]), ["relay"])
        with mock.patch.object(doctor, "probe_relay", return_value=(False, "TimeoutError")):   # a stand-in that shows no error
            row = doctor.check_relay(self.st)
        self.assertEqual(row.get("relay_reason"), "probe_failed")

    def test_the_words_are_a_closed_set_and_ok_rows_carry_none(self):
        self.assertEqual(set(upgrade_diag.RELAY_REASONS), {"unreachable", "probe_failed", "host_leg"})
        with mock.patch.object(doctor, "probe_relay", return_value=(True, "ok")), \
                mock.patch.object(doctor, "_host_leg", return_value=(doctor.WARN, "x", "")):
            self.assertNotIn("relay_reason", doctor.check_relay(self.st))

    def test_without_a_service_stays_failed(self):
        self.failed(self.go(RELAY_ONLY, svc_on=False), ["relay"])

    def test_real_install_failures_keep_their_reason(self):
        res = self.go(RELAY_ONLY, version="0.0.1")
        self.assertEqual((res["result"], res["reason"], res["exit"]), ("failed", "not_upgraded", 1))
        res = self.go(RELAY_ONLY, deferred=1)
        self.assertEqual((res["result"], res["reason"], res["exit"]), ("failed", "service_restart_failed", 1))

    def test_all_green_is_still_plain_upgraded(self):
        res = self.go(BASE + [SVC_OK, {"id": "relay", "status": "ok"}])
        self.assertEqual((res["result"], res["reason"], res["exit"]), ("ok", "upgraded", 0))



class RealProbe(_Judge):
    """The real `doctor.check_relay` → `probe_relay` → websockets client against stand-in servers on 127.0.0.1 (1 s timeout)."""
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for k in [k for k in os.environ if k.lower().endswith("_proxy")]:
            del os.environ[k]
        self.stop = threading.Event()
        self.addCleanup(self.stop.set)
        self.held = []

    def row(self, url, sock_error=None):
        """`sock_error`: what the socket layer raises under the real websockets client (the probe only, not the judgement)."""
        real = doctor.probe_relay
        with mock.patch.object(doctor, "_relay_url", return_value=url), \
                mock.patch.object(doctor, "probe_relay", lambda relay, net=None: real(relay, 1, net)), \
                (mock.patch("socket.create_connection", side_effect=sock_error) if sock_error else contextlib.nullcontext()):
            return doctor.check_relay(self.st)

    def tcp(self, handler):
        srv = socket.socket()
        self.addCleanup(srv.close)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)

        def loop():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                self.held.append(conn)
                self.addCleanup(conn.close)
                try:
                    handler(conn)
                except OSError:
                    pass
        threading.Thread(target=loop, daemon=True).start()
        return srv.getsockname()[1]

    def self_signed(self):
        """A throw-away key + certificate for 127.0.0.1 in this test's temp dir: valid name and dates, no trusted issuer."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "127.0.0.1")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(hours=1))
                .not_valid_after(now + datetime.timedelta(hours=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                .sign(key, hashes.SHA256()))
        pem = pathlib.Path(self.tmp.name) / "standin.pem"
        pem.touch(0o600)
        pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()) + cert.public_bytes(serialization.Encoding.PEM))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(pem)
        return ctx

    def test_self_signed_certificate_stays_failed(self):
        ctx = self.self_signed()
        seen = []

        def spy(*a, **k):
            try:
                return real(*a, **k)
            except Exception as e:
                seen.append(e)
                raise
        from websockets.sync import client
        real = client.connect
        with mock.patch.object(client, "connect", spy):
            row = self.row(f"wss://127.0.0.1:{self.tcp(lambda c: ctx.wrap_socket(c, server_side=True))}")
        self.assertIsInstance(seen[0], ssl.SSLCertVerificationError)
        self.refused(row)

    def test_plaintext_on_the_wss_port_stays_failed(self):
        def http(conn):
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\ncontent-length: 0\r\n\r\n")
        self.refused(self.row(f"wss://127.0.0.1:{self.tcp(http)}"))

    def test_tls_peer_that_hangs_up_stays_failed(self):
        def eof(conn):
            conn.recv(4096)
            conn.close()
        self.refused(self.row(f"wss://127.0.0.1:{self.tcp(eof)}"))

    def test_handshake_done_but_no_first_frame_stays_failed(self):
        from websockets.sync.server import serve
        srv = serve(lambda ws: self.stop.wait(10), "127.0.0.1", 0)
        self.addCleanup(srv.shutdown)
        self.addCleanup(self.stop.set)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.refused(self.row(f"ws://127.0.0.1:{srv.socket.getsockname()[1]}"))

    def test_an_http_answer_instead_of_the_upgrade_stays_failed(self):
        def http(conn):
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\n\r\n")
        self.refused(self.row(f"ws://127.0.0.1:{self.tcp(http)}"))

    def test_socket_layer_permission_and_missing_file_stay_failed(self):
        for error in (PermissionError(errno.EACCES, "x"), PermissionError(errno.EPERM, "x"), FileNotFoundError(errno.ENOENT, "x"),
                      OSError(errno.EMFILE, "x"), OSError(errno.EADDRNOTAVAIL, "x")):
            with self.subTest(error=error):
                self.refused(self.row("ws://127.0.0.1:9", error))

    def test_connection_refused_is_unreachable(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        for scheme in ("ws", "wss"):
            with self.subTest(scheme):
                self.pending(self.row(f"{scheme}://127.0.0.1:{port}"))

    def test_tcp_peer_that_never_answers_is_unreachable(self):
        port = self.tcp(lambda conn: None)       # accepted and held open: the opening handshake times out
        for scheme in ("ws", "wss"):
            with self.subTest(scheme):
                self.pending(self.row(f"{scheme}://127.0.0.1:{port}"))

    def test_reset_during_the_tls_handshake_is_unreachable_by_the_letter(self):
        """A reset before TLS is up is a `ConnectionResetError`: indistinguishable from a dropped network, taken as listed."""
        def rst(conn):
            conn.recv(16)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            conn.close()
        self.pending(self.row(f"wss://127.0.0.1:{self.tcp(rst)}"))

    def test_socket_layer_dns_timeout_and_no_route_are_unreachable(self):
        for error in (socket.gaierror(socket.EAI_NONAME, "x"), socket.gaierror(socket.EAI_AGAIN, "x"), TimeoutError("timed out"),
                      *(OSError(n, "x") for n in NET_DOWN)):
            with self.subTest(error=error):
                self.pending(self.row("ws://relay.invalid", error))


if __name__ == "__main__":
    import unittest
    unittest.main()
