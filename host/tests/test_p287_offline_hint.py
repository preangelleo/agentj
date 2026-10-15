"""P287: the 「重启后请登录一次…」 reminder is never pushed from the host (a service restart or an upgrade re-ran it on every
phone connect). The host only tells a ready phone its platform and how it comes back after an OS restart (`plat` / `boot`,
PROTOCOL §10.0); the phone shows its own offline line (web/test/p287.test.mjs covers the OS-restart case)."""
import _hermetic  # noqa: F401
import asyncio
import os
import pwd
import unittest
from unittest import mock

import test_p72_onboarding as fx
from agentj import doctor, service

REBOOT_WORDS = ("重启后", "快速切换用户", "log in once", "Fast User Switching")


class HostSide(unittest.TestCase):
    def _run(self, plat, linger, steps):
        """One serve process (a fresh Host = a service restart or an upgrade's new process) on `plat`: pair, then `steps`."""
        f = fx.Serve()
        f.setUp()
        try:
            async def go():
                with mock.patch.object(service, "platform", return_value=plat), \
                        mock.patch.object(service, "_linger", return_value=linger):
                    s = await f._pair(8, fx.PHONE)
                    for _ in range(steps):
                        await f.host.on_ready(s, None)
                        for _ in range(20):
                            await asyncio.sleep(0.01)
                    s.p33 = True                     # what a current phone's approved / ready carries (§10.0)
                    for _ in range(2):
                        caps.append(f.host._caps(s))
                        for _ in range(20):
                            await asyncio.sleep(0.01)
            caps = []
            asyncio.run(go())
            sys_lines = [t["reply"]["text"] for t in f.host.hist.page(limit=50)[0] if t["src"].get("k") == "sys"]
            notices = [o.get("text", "") for _, o in f.sent if isinstance(o, dict)]
            return sys_lines, notices, caps
        finally:
            f.tearDown()

    def test_service_restart_and_upgrade_push_nothing(self):
        for plat in ("macos", "linux"):
            # first process (install / service restart), then a second process on the same state (upgrade = new serve)
            for steps in (1, 3):
                with self.subTest(plat=plat, reconnects=steps):
                    lines, notices, _ = self._run(plat, "no", steps)
                    for text in lines + notices:
                        self.assertFalse(any(w in text for w in REBOOT_WORDS), text)

    def test_ready_tells_the_phone_platform_and_boot(self):
        for plat, linger, boot in (("macos", None, "login"), ("linux", "yes", "auto"), ("linux", "no", "login")):
            with self.subTest(plat=plat, linger=linger):
                _, _, caps = self._run(plat, linger, 1)
                self.assertTrue(caps)
                self.assertTrue(all(m.get("plat") == plat for m in caps))
                self.assertEqual(caps[-1].get("boot"), boot, "the background loginctl answer reaches the next ready")

    def test_wsl_counts_as_windows(self):
        with mock.patch.object(service, "platform", return_value="linux"), \
                mock.patch("builtins.open", mock.mock_open(read_data="5.15.167.4-microsoft-standard-WSL2")):
            self.assertEqual(service.host_platform(), "windows")
            self.assertEqual(service.boot_mode(), "login")
        with mock.patch.object(service, "platform", return_value="other"):
            self.assertEqual(service.host_platform(), "other")
            self.assertIsNone(service.boot_mode())

    def test_computer_side_reminder_is_plain(self):
        user = pwd.getpwuid(os.getuid()).pw_name
        for lang in ("zh", "en", None):
            text = service.login_reminder(lang)
            self.assertNotIn(user, text, "no system user name")
            self.assertNotIn("快速切换用户", text)
            self.assertNotIn("Fast User Switching", text)
        self.assertFalse(hasattr(service, "residency_hint"), "the host push is gone")
        row = doctor.check_login_session({"kind": "launchd"}) if service.platform() == "macos" else None
        self.assertTrue(row is None or service.login_reminder() in row.get("hint", ""))


if __name__ == "__main__":
    unittest.main()
