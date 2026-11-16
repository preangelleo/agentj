"""R-0030 (0.30.29): Anthropic models (API change 2026-10-21) answer HTTP 400 to sampling parameters (temperature / top_p /
top_k). qrcrop.vision_corners talks to the owner's OpenAI-compatible endpoint with `temperature: 0`; pointed at a current
Claude model it 400'd every time. Contract: on HTTP 400 ask once more without sampling parameters; a second 400, or any
other error, still raises (tidy() then sends the original picture). The key goes only to the configured URL and never
appears in an exception text. Local fake endpoint on 127.0.0.1; no real provider.
"""
import _hermetic  # noqa: F401
import http.server
import json
import threading
import unittest
import urllib.error

from agentj import qrcrop

KEY = "sk-test-" + "x" * 24
ANSWER = {"top_left": [100, 100], "top_right": [900, 100], "bottom_right": [900, 900], "bottom_left": [100, 900]}


class _Endpoint:
    """mode: 'claude' = 400 when any sampling param is present; 'always400'; 'ok' = accepts everything."""
    def __init__(self, mode):
        self.mode, self.bodies, self.auth = mode, [], []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.bodies.append(body)
                outer.auth.append(self.headers.get("Authorization"))
                sampling = any(k in body for k in ("temperature", "top_p", "top_k"))
                if outer.mode == "always400" or outer.mode == "claude" and sampling:
                    data, code = json.dumps({"error": {"type": "invalid_request_error",
                                                       "message": "temperature is not supported"}}).encode(), 400
                else:
                    data, code = json.dumps({"choices": [{"message": {"content": json.dumps(ANSWER)}}]}).encode(), 200
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.env = {"AGENTJ_VISION_URL": f"http://127.0.0.1:{self.srv.server_port}/v1",
                    "AGENTJ_VISION_MODEL": "anthropic/claude-opus-4", "AGENTJ_VISION_API_KEY": KEY}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class VisionSampling(unittest.TestCase):
    def endpoint(self, mode):
        ep = _Endpoint(mode)
        self.addCleanup(ep.close)
        return ep

    def test_claude_400_on_temperature_is_retried_without_sampling_params(self):
        ep = self.endpoint("claude")
        corners = qrcrop.vision_corners(b"\x89PNG", 1000, 500, ep.env, timeout=10)
        self.assertEqual(corners, [[100.0, 50.0], [900.0, 50.0], [900.0, 450.0], [100.0, 450.0]])
        self.assertEqual(len(ep.bodies), 2)
        self.assertEqual(ep.bodies[0].get("temperature"), 0)
        self.assertFalse({"temperature", "top_p", "top_k"} & set(ep.bodies[1]))
        self.assertEqual(ep.bodies[1]["messages"], ep.bodies[0]["messages"])
        self.assertEqual(ep.auth, ["Bearer " + KEY] * 2)

    def test_accepting_endpoint_is_asked_once_with_temperature_zero(self):
        ep = self.endpoint("ok")
        self.assertIsNotNone(qrcrop.vision_corners(b"\x89PNG", 10, 10, ep.env, timeout=10))
        self.assertEqual(len(ep.bodies), 1)
        self.assertEqual(ep.bodies[0]["temperature"], 0)

    def test_second_400_raises_once_retried_and_key_not_in_error(self):
        ep = self.endpoint("always400")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            qrcrop.vision_corners(b"\x89PNG", 10, 10, ep.env, timeout=10)
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(len(ep.bodies), 2)
        self.assertNotIn(KEY, str(cm.exception) + repr(cm.exception))


if __name__ == "__main__":
    unittest.main()
