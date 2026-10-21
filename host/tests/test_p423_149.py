"""P423 #149: NetEase (163 / 126 / yeah / 188 / 企业邮) IMAP — send RFC 2971 ID after LOGIN, otherwise SELECT says "Unsafe Login".

A fake IMAP connection object (no socket) records the command order. Other providers keep the old behaviour (ID only when the
server advertises it); a failing ID never breaks the login.
"""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import __version__, mail as M  # noqa: E402


class FakeConn:
    def __init__(self, caps=("IMAP4REV1",), id_error=None):
        self.capabilities, self.id_error = tuple(caps), id_error
        self.seq: list[str] = []
        self.id_args: list[str] = []

    def login(self, user, password):
        self.seq.append("LOGIN")
        return "OK", [b"logged in"]

    def _simple_command(self, name, *args):
        self.seq.append(name)
        if name == "ID":
            self.id_args.append(args[0])
            if self.id_error:
                raise self.id_error
        return "OK", [b"done"]

    def logout(self):
        self.seq.append("LOGOUT")


def cfg(host, address="agent@example.cn"):
    return {**M.blank_config(), "address": address, "username": address, "imap": {"host": host, "port": 993, "security": "ssl"}}


class NetEaseId(unittest.TestCase):
    def login(self, conf, conn):
        with patch.object(M, "_imap", return_value=conn):
            return M.imap_login(conf, "code")

    def test_netease_hosts_get_id_after_login_even_without_capability(self):
        for host, addr in (("imap.163.com", "a@163.com"), ("imap.126.com", "a@126.com"), ("imap.yeah.net", "a@yeah.net"),
                           ("imap.qiye.163.com", "a@corp.example"), ("imap.188.com", "a@188.com"), ("IMAP.163.COM.", "a@x.example")):
            conn = FakeConn()
            self.assertIs(self.login(cfg(host, addr), conn), conn)
            self.assertEqual(conn.seq, ["LOGIN", "ID"], host)

    def test_address_domain_alone_is_enough(self):
        conn = FakeConn()
        self.login(cfg("mail.custom-proxy.example", "me@vip.163.com"), conn)
        self.assertEqual(conn.seq, ["LOGIN", "ID"])

    def test_id_arguments_are_rfc2971_and_brand_neutral(self):
        conn = FakeConn()
        self.login(cfg("imap.163.com", "a@163.com"), conn)
        args = conn.id_args[0]
        self.assertEqual(args, f'("name" "AgentJ" "version" "{__version__}" "vendor" "AgentJ")')
        self.assertTrue(args.startswith("(") and args.endswith(")"))

    def test_other_providers_unchanged(self):
        for host, addr in (("imap.qq.com", "a@qq.com"), ("imap.gmail.com", "a@example.com"), ("imap.not163.com", "a@not163.com"),
                           ("imap.163.com.evil.example", "a@evil.example")):
            conn = FakeConn()
            self.login(cfg(host, addr), conn)
            self.assertEqual(conn.seq, ["LOGIN"], host)
        # a server advertising ID keeps getting it before and after LOGIN, as before
        conn = FakeConn(caps=("IMAP4REV1", "ID"))
        self.login(cfg("imap.gmail.com", "a@example.com"), conn)
        self.assertEqual(conn.seq, ["ID", "LOGIN", "ID"])

    def test_id_failure_is_not_fatal(self):
        for err in (M.imaplib.IMAP4.error("BAD unknown command"), OSError("reset")):
            conn = FakeConn(id_error=err)
            self.assertIs(self.login(cfg("imap.126.com", "a@126.com"), conn), conn)
            self.assertEqual(conn.seq, ["LOGIN", "ID"])

    def test_wrong_password_sends_no_id(self):
        conn = FakeConn()
        conn.login = lambda u, p: (conn.seq.append("LOGIN"), ("NO", [b"bad"]))[1]
        with self.assertRaises(M.MailError):
            self.login(cfg("imap.163.com", "a@163.com"), conn)
        self.assertEqual(conn.seq, ["LOGIN", "LOGOUT"])


if __name__ == "__main__":
    unittest.main()
