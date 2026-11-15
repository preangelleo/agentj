"""#439: the owner's own text keeps code exact — another user's home path and an SSH public key are not rewritten.

Jarvis 11-03 19:4x: a reply said `chown -R olares: <that user's home>/.ssh`; the phone showed and copied `~/.ssh`, the owner ran it
in a root shell (`~` = /root) and sshd refused the key (bad ownership). Same reply: an ed25519 public key became
`<redacted>`. Rules for owner delivery (`privacy.owner_text`, `redact(owner=True)`):
- inside a code block / inline code nothing is rewritten as a path: what is copied is what the Agent wrote;
- outside code only THIS computer's own home becomes `~`; another user's home stays whole;
- an OpenSSH public key line (`ssh-ed25519 AAAA… comment`) is not a credential and stays whole;
- a real credential is still masked everywhere (inside code too), and the outward (strict) filter is unchanged.
"""
# Recovered from d72a043d78 / dc4e7364dc; timing and delimiter regressions
# remain in test_privacy_code_runtime.py. Strict outward path masking is also
# covered by test_p194_owner_direct.GalleryPaths.
import _hermetic  # noqa: F401,I001
import os
import unittest
from unittest import mock

from agentj import privacy

OTHER = "/" + "home/olares"                                                    # another user on another machine
PUB = " ".join(("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAI" + "K7mQx3pZr2Vt9Lw4sNc8Hy1Ud6Fj0Ga5Eb2Wo7Tq3Ri", "owner@laptop"))
KEY = privacy.FAKE_SECRETS["openrouter"]                                     # a real key shape (built at runtime)


class OwnerPaths(unittest.TestCase):
    def setUp(self):
        self.home = "/" + "home/ownerme"
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start(); self.addCleanup(p.stop)

    def test_code_block_is_copied_exactly(self):
        text = f"运行：\n```bash\nchown -R olares: {OTHER}/.ssh\nchmod 700 {self.home}/.ssh\n```\n"
        out = privacy.owner_text(text)
        self.assertIn(f"chown -R olares: {OTHER}/.ssh", out)
        self.assertIn(f"chmod 700 {self.home}/.ssh", out, "even the owner's own home stays as written inside code")
        self.assertNotIn("~/.ssh", out)

    def test_inline_code_is_copied_exactly(self):
        out = privacy.owner_text(f"然后跑 `ls -la {OTHER}/.ssh` 看属主")
        self.assertIn(f"`ls -la {OTHER}/.ssh`", out)

    def test_outside_code_only_own_home_becomes_tilde(self):
        out = privacy.owner_text(f"报告在 {self.home}/notes/r.md，对方机器上是 {OTHER}/.ssh/authorized_keys")
        self.assertIn("~/notes/r.md", out)
        self.assertIn(f"{OTHER}/.ssh/authorized_keys", out)

    def test_public_key_stays_whole(self):
        for text in (PUB, f"把这行加进去：\n```\n{PUB}\n```", f"公钥 `{PUB}`"):
            out = privacy.owner_text(text)
            self.assertIn(PUB, out, text)
            self.assertNotIn("<redacted>", out, text)

    def test_credentials_still_masked_in_code(self):
        out = privacy.owner_text(f"```\nexport OPENROUTER_API_KEY={KEY}\n```")
        self.assertNotIn(KEY, out)
        self.assertNotIn(KEY, privacy.owner_text(f"`{KEY}`"))

    def test_idempotent(self):
        text = f"`{OTHER}/a` {self.home}/b {PUB}"
        once = privacy.owner_text(text)
        self.assertEqual(privacy.owner_text(once), once)





if __name__ == "__main__":
    unittest.main()
