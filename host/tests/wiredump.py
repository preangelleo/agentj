"""Test-only entry point for the host CLI (tests/lib/host.mjs runs `host/tests/agentj-test`, not `host/agentj`).

With AGENTJ_WIRE_DUMP=<file> it records every frame `agentj serve` exchanges with the relay — exactly what the relay can
see: ciphertext and the relay auth, never plaintext — so the end-to-end tests can scan it for planted markers (Invariant 5).
The shipped package has no frame recorder at all (L2): `Host._tap` is a no-op there, and this file is not part of it.
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentj import cli, serve  # noqa: E402

dump = os.environ.pop("AGENTJ_WIRE_DUMP", None)   # never inherited by the agent
if dump:
    out = os.fdopen(os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a", buffering=1)

    def _tap(self, direction, data):
        raw = data.encode() if isinstance(data, str) else data
        out.write(json.dumps({"dir": direction, "t": "text" if isinstance(data, str) else "bin",
                              "b64": base64.b64encode(raw).decode()}) + "\n")

    serve.Host._tap = _tap

fake_asr = os.environ.pop("AGENTJ_TEST_FAKE_ASR", None)   # a stand-in speech engine (PROTOCOL §10.9) whose words are a marker
if fake_asr:
    class _FakeASR:
        @staticmethod
        def ready_state(state_dir=None, engine_override=None):
            return "ready"

        @staticmethod
        def transcribe(path, *, timeout_s, state_dir=None, engine_override=None):
            return {"ok": True, "text": f"{fake_asr} {os.path.getsize(path)}", "engine": "fake", "ms": 1}
    serve.asr_mod = _FakeASR()

cli.main()
