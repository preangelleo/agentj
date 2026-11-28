"""The local permission tool cancels exactly the MCP call that stopped waiting."""
import io
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from agentj import permtool


class PermissionLink(unittest.TestCase):
    def setUp(self):
        self.link = permtool.Link()
        local, self.host = socket.socketpair()
        self.addCleanup(local.close)
        self.addCleanup(self.host.close)
        self.host.settimeout(0.5)
        self.link.sock = local
        self.frames = self.host.makefile("rb")
        self.addCleanup(self.frames.close)
        # Closing the Host half also ends the real reader thread.
        reader_file = local.makefile("rb")
        self.addCleanup(reader_file.close)
        reader = threading.Thread(target=self.link._read, args=(reader_file,), daemon=True)
        reader.start()
        self.addCleanup(reader.join, 1)
        self.addCleanup(self.host.shutdown, socket.SHUT_RDWR)

    def frame(self):
        try:
            return json.loads(self.frames.readline())
        except TimeoutError:
            self.fail("permission link did not cancel the abandoned Host request")

    def answer(self, request, behavior="allow"):
        self.host.sendall((json.dumps({"t": "answer", "id": request["id"],
                                       "behavior": behavior, "updatedInput": {"fixture": True}}) + "\n").encode())

    def call(self, mid, wait):
        result = []
        with patch.object(permtool, "getenv", return_value=str(wait)):
            thread = threading.Thread(target=lambda: result.append(self.link.ask({"tool_name": "Test", "input": {}}, mid)))
            thread.start()
            request = self.frame()
        return thread, request, result

    def test_local_timeout_withdraws_only_its_host_request(self):
        expired, old, expired_result = self.call("earlier", 0.03)
        expired.join(1)
        self.assertEqual(expired_result[0]["behavior"], "deny")
        self.assertEqual(self.frame(), {"t": "cancel", "id": old["id"]})

        live, new, live_result = self.call("retry", 2)
        self.assertNotEqual(new["id"], old["id"])
        self.answer(old)
        self.answer(new)
        live.join(1)
        self.assertEqual(live_result, [{"behavior": "allow", "updatedInput": {"fixture": True}}])

    def test_native_cancellation_drops_late_allow_but_keeps_parallel_waiter(self):
        cancelled, old, lost = self.call("cancelled", 2)
        live, new, accepted = self.call("parallel", 2)
        self.link.cancel("cancelled")
        self.assertEqual(self.frame(), {"t": "cancel", "id": old["id"]})
        self.answer(old)
        self.answer(new)
        cancelled.join(1)
        live.join(1)
        self.assertEqual(lost[0]["behavior"], "deny")
        self.assertEqual(accepted[0]["behavior"], "allow")


    def test_cancel_buffered_immediately_after_call_targets_registered_waiter(self):
        call = {"jsonrpc": "2.0", "id": "back-to-back", "method": "tools/call",
                "params": {"name": "approve", "arguments": {"tool_name": "Test", "input": {}}}}
        cancel = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": call["id"]}}
        messages = io.StringIO("\n".join(map(json.dumps, (call, cancel))))
        finished = threading.Event()
        real_ask = self.link.ask

        class Output(io.StringIO):
            def write(self, text):
                count = super().write(text)
                finished.set()
                return count

        stdout = Output()

        def slow_worker(*args, **kwargs):
            time.sleep(0.04)  # Let the stdin reader reach the already-buffered cancellation.
            return real_ask(*args, **kwargs)

        with patch.object(permtool, "LINK", self.link), patch.object(self.link, "claim", return_value=True), \
                patch.object(self.link, "ask", side_effect=slow_worker), patch.object(permtool, "getenv", return_value="2"), \
                patch("agentj.fence.no_dump"), patch("sys.stdin", messages), patch("sys.stdout", stdout):
            permtool.main()
            request = self.frame()
            try:
                self.assertEqual(self.frame(), {"t": "cancel", "id": request["id"]})
            finally:
                self.answer(request)
                self.assertTrue(finished.wait(1))
            answer = json.loads(json.loads(stdout.getvalue())["result"]["content"][0]["text"])
            self.assertEqual(answer["behavior"], "deny")
