"""Real Host + local OpenCode protocol stand-in; no credentials or paid API calls."""
import _hermetic  # noqa: F401,I001
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


class LiveVerify(unittest.TestCase):
    def test_phone_disconnect_and_status_reconnect(self):
        host = Path(__file__).resolve().parents[1]
        driver = next((parent / "reports" / "qa" / "p578" / "live_verify.py" for parent in host.parents
                       if (parent / "reports" / "qa" / "p578" / "live_verify.py").is_file()), None)
        if driver is None:
            portable = host / "tests" / "fixtures" / "p578" / "live_verify.py"
            driver = portable if portable.is_file() else None
        self.assertIsNotNone(driver, "P578 live QA source helper must accompany private and public tests")
        tree = driver.parents[3]
        with tempfile.TemporaryDirectory(prefix="p578c-live-", dir=os.environ.get("TEST_TMP")) as td:
            root = Path(td)
            wrapper = root / "opencode"
            wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " +
                               shlex.quote(str(host / "tests" / "fakeopencode2.py")) + ' "$@"\n')
            wrapper.chmod(0o700)
            env = {"PATH": os.defpath, "HOME": str(root / "home"),
                   **{f"XDG_{k}_HOME": str(root / k.lower()) for k in ("CONFIG", "DATA", "STATE", "CACHE")},
                   "AGENTJ_OPENCODE_BIN": str(wrapper), "FAKE_OC2_CONNECTED": "ark-agent-plan",
                   "FAKE_OC2_CONFIG_PROVIDERS": "ark-agent-plan", "P578_STANDIN": "1", "P578_LOST": "6",
                   "P578_HELLO_WAIT": "2", "P578_HOST_DIR": str(host)}
            for scenario in ("phone-lost", "status-reconnect"):
                with self.subTest(scenario=scenario):
                    out = root / (scenario + ".json")
                    r = subprocess.run([sys.executable, str(driver), "agent",
                                        "doubao-seed-2.1-pro", scenario, str(out)], env=env, cwd=tree,
                                       capture_output=True, text=True, timeout=90)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    proof = json.loads(out.read_text())
                    self.assertEqual(proof["backend"], "stand-in")
                    self.assertTrue(all(proof["checks"].values()), proof)
                    if scenario == "status-reconnect":
                        self.assertLess(proof["unconfirmed_at_s"], proof["verdict_after_s"])
                        timeline = proof["timeline"]
                        lost = next(i for i, p in enumerate(timeline) if p.get("lost"))
                        recovered = next(i for i in range(lost + 1, len(timeline)) if "status" in timeline[i])
                        self.assertGreater(recovered, lost)
                        self.assertGreaterEqual(timeline[recovered]["t"], proof["lost_for"])
                        self.assertTrue(timeline[recovered]["said"])


if __name__ == "__main__":
    unittest.main()
