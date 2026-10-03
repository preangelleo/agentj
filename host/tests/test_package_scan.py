"""Package-mode layer 1 (market.package_scan) against the shared cases the server's scanPackageText also runs
(protocol/vectors/package-scan.json): code-shaped "name = value" lines pass, literal secrets / e-mails / keys refuse."""
import base64
import json
import pathlib
import unittest

from jarvis_host import market

V = json.loads((pathlib.Path(__file__).resolve().parents[2] / "protocol" / "vectors" / "package-scan.json").read_text())


class PackageScan(unittest.TestCase):
    def test_shared_cases(self):
        for c in V["cases"]:
            text = c["text"] if "text" in c else base64.b64decode(c["text_b64"]).decode()
            self.assertEqual(bool(market.package_scan(text, "x-host", "x-user")), c["hit"], text)

    def test_literal_secret(self):
        for v in ("app_password", "file-key", "args.password", "os.environ.get", "YOUR_TOKEN_HERE", "密码密码密码密码密码密码", "`--token-env`、`--x`"):
            self.assertFalse(market.literal_secret(v), v)
        for v in ("Hunter2Hunter2Hunter2", "Xq7pL2vR9sT4wY8zK3mN", "'S3cr3tPassw0rdX'"):
            self.assertTrue(market.literal_secret(v), v)

    def test_machine_names_still_refuse(self):
        self.assertIn("hostname", market.package_scan("deployed from workbox-x today", "workbox-x", "nobody-x"))
        self.assertIn("home_path", market.package_scan("see /" "home/alice/notes", None, None))


if __name__ == "__main__":
    unittest.main()
