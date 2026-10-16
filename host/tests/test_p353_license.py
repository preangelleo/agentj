"""P353: the published package carries Agent J's own LICENSE (Apache-2.0) and NOTICE.

host/LICENSE and host/NOTICE are byte-equal to the public repository's front (agentjarvis/release/public/), are declared as
PEP 639 license-files, and land in the wheel (.dist-info/licenses/) and the sdist."""
import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
import zipfile

HOST = pathlib.Path(__file__).resolve().parents[1]
FRONT = HOST.parent / "release" / "public"
if not FRONT.is_dir():                     # the public repository: its front files are the tree root itself
    FRONT = HOST.parent
FILES = ("LICENSE", "NOTICE")


class LicenseFiles(unittest.TestCase):
    def test_host_copies_equal_the_public_front(self):
        for name in FILES:
            self.assertEqual((HOST / name).read_bytes(), (FRONT / name).read_bytes(), f"host/{name} != release/public/{name}")
        self.assertIn("Apache License", (HOST / "LICENSE").read_text())
        self.assertIn("Version 2.0", (HOST / "LICENSE").read_text())

    def test_pyproject_declares_them(self):
        conf = tomllib.loads((HOST / "pyproject.toml").read_text())
        self.assertEqual(conf["project"]["license"], "Apache-2.0")
        self.assertEqual(conf["project"]["license-files"], list(FILES))
        for name in FILES:
            self.assertIn(name, conf["tool"]["hatch"]["build"]["targets"]["sdist"]["include"])


@unittest.skipUnless(shutil.which("uv") and not os.environ.get("AJ_SKIP_WHEEL"), "uv not available")
class BuiltPackages(unittest.TestCase):
    def test_wheel_and_sdist_carry_license_and_notice(self):
        with tempfile.TemporaryDirectory(prefix="aj-p353-") as out:
            r = subprocess.run([shutil.which("uv"), "build", "--sdist", "--wheel", "--out-dir", out, str(HOST)],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            wheel = next(pathlib.Path(out).glob("agentj-*.whl"))
            with zipfile.ZipFile(wheel) as z:
                names = z.namelist()
                meta = z.read(next(n for n in names if n.endswith(".dist-info/METADATA"))).decode()
                for name in FILES:
                    member = next((n for n in names if n.endswith(f".dist-info/licenses/{name}")), None)
                    self.assertIsNotNone(member, f"wheel misses {name}")
                    self.assertEqual(z.read(member), (FRONT / name).read_bytes())
                    self.assertIn(f"License-File: {name}", meta)
                self.assertIn("License-Expression: Apache-2.0", meta)
            sdist = next(pathlib.Path(out).glob("agentj-*.tar.gz"))
            with tarfile.open(sdist) as t:
                for name in FILES:
                    self.assertTrue(any(m.endswith(f"/{name}") and m.count("/") == 1 for m in t.getnames()), f"sdist misses {name}")


if __name__ == "__main__":
    unittest.main()
