"""#461f round 4 N4-4: the codex daemon socket is asked only when the socket (not a link) and its folder are ours and the
folder is not group/other-writable — another user cannot plant the endpoint, darwin included (no peer uid there).
Synthetic CODEX_HOME + the p461f fake daemon; the owner's real daemon is never touched."""
import _hermetic  # noqa: F401,I001
import os
import sys
import unittest
from unittest import mock

import test_p461f_codex_daemon as t461
from agentj import codex_daemon


class SocketOwner(t461.Base):
    def test_folder_mode_and_owner(self):
        self.daemon(None, "idle")
        p = codex_daemon.socket_path()
        self.assertTrue(codex_daemon._ours(p))
        self.assertEqual(codex_daemon.thread_status(t461.SID)["status"], "idle")
        os.chmod(p.parent, 0o777)
        self.addCleanup(os.chmod, p.parent, 0o700)
        self.assertIsNone(codex_daemon.thread_status(t461.SID), "a world-writable folder: anyone could swap the socket")
        os.chmod(p.parent, 0o770)
        self.assertIsNone(codex_daemon.thread_status(t461.SID), "group-writable too")
        os.chmod(p.parent, 0o700)
        with mock.patch.object(codex_daemon.os, "getuid", return_value=os.getuid() + 1):
            self.assertIsNone(codex_daemon.thread_status(t461.SID), "another user's socket / folder")
        with mock.patch.object(codex_daemon.sys, "platform", "darwin"), \
                mock.patch.object(codex_daemon, "_peer", return_value=(None, None)):
            with mock.patch.object(codex_daemon.os, "getuid", return_value=os.getuid() + 1):
                self.assertIsNone(codex_daemon.thread_status(t461.SID), "darwin: no peer uid, the file owner decides")

    def test_a_symlinked_socket_is_not_followed(self):
        self.daemon(None, "idle")
        p = codex_daemon.socket_path()
        link = p.parent / "elsewhere.sock"
        link.symlink_to(p)
        self.assertIsNone(codex_daemon.thread_status(t461.SID, path=link))


if __name__ == "__main__":
    unittest.main()
