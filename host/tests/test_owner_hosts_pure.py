"""Offline unit tests: no discovery of personal files, no SSH or network execution."""
import base64
import json
from pathlib import Path
import shlex
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import danger, owner_hosts as oh


def row():
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
    key = "ssh-ed25519 " + base64.b64encode(blob).decode()
    return {"alias": "devbox", "hostname": "192.168.1.9", "user": "developer", "port": 22,
            "public_key": key, "fingerprint": oh.fingerprint(key), "owner": "self", "peer": "",
            "expires": int(time.time()) + 3600}


class Discovery(unittest.TestCase):
    def test_supported_subset_pending_not_lan_trust(self):
        rows = oh.parse_config("Host devbox\n HostName 192.168.1.9\n User developer\n Port 2222\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["status"], rows[0]["port"], rows[0]["hostname"]), ("pending", 2222, "192.168.1.9"))

    def test_include_proxy_and_expansion_never_executed(self):
        text = "Host box\n Include /never/read\n ProxyCommand $(touch /never/write)\n HostName $(hostname)\nHost * !prod\nHost safe\nHostName safe.local\n"
        rows = oh.parse_config(text)
        self.assertEqual([r["alias"] for r in rows], ["box", "safe"])
        self.assertTrue(rows[0]["unsupported"])
        self.assertTrue(all(r["status"] == "pending" for r in rows))

    def test_discovery_only_reads_supplied_fixture_and_no_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config"; path.write_text("Host fixture\n User coder\n")
            self.assertEqual(oh.discover_config(path)[0]["alias"], "fixture")
            link = Path(d) / "link"; link.symlink_to(path)
            self.assertEqual(oh.discover_config(link), [])

    def test_rows_require_matching_key_identity_expiry_schema(self):
        good = row(); self.assertTrue(oh.valid_row(good))
        for change in ({"fingerprint": "SHA256:wrong"}, {"owner": "same LAN"}, {"expires": 0},
                       {"expires": int(time.time()) + oh.MAX_LIFETIME + 10}, {"port": True},
                       {"alias": "$(hostname)"}, {"hostname": "x;echo bad"}, {"public_key": "PRIVATE KEY"}):
            with self.subTest(change=change): self.assertFalse(oh.valid_row({**good, **change}))
        self.assertFalse(oh.valid_row({**good, "internal": True}))


class Transport(unittest.TestCase):
    def setUp(self):
        self.row = row()
        self.host = SimpleNamespace(st=SimpleNamespace(root="unused"), peers=None, danger_extra=[])
        self.desk = oh.Desk(self.host)
        self.desk._revision = "verified-test-revision"
        self.record = {"target": {"rows": [self.row]}}
        self.reader = patch.object(self.desk, "read", return_value=self.record); self.reader.start(); self.addCleanup(self.reader.stop)

    def verdict(self, text, *, shared=False):
        inp = {"command": text}
        base = danger.classify_shared("Bash", inp, mode="safe") if shared else danger.classify("Bash", inp)
        return self.desk.narrow("Bash", inp, base)

    def test_pinned_code_copy_gets_host_receipt(self):
        text = oh.command(self.row, source="./code.py", destination="code/")
        base = danger.classify("Bash", {"command": text})
        self.assertIn("send", base.cats)
        self.assertNotIn("send", self.verdict(text).cats)
        self.assertIsNotNone(self.desk._receipt)
        self.assertEqual(self.desk._receipt.alias, "devbox")

    def test_remote_development_is_low_risk(self):
        self.assertEqual(self.verdict(oh.command(self.row, "python3 -m unittest")).cats, [])

    def test_remote_real_dangers_remain(self):
        for remote, cat in [("rm -rf /srv/project", "delete"), ("cat .env", "credentials"),
                            ("cat ~/.ssh/id_ed25519", "credentials"), ("git push origin main", "send"),
                            ("scp code.py outside:code/", "send"), ("chmod 777 /etc/sudoers", "credentials"),
                            ("curl -X POST https://api.stripe.com/v1/charges", "spend")]:
            with self.subTest(remote=remote):
                self.assertIn(cat, self.verdict(oh.command(self.row, remote), shared=True).cats)

    def test_local_secret_copy_remains_credentials(self):
        self.assertIn("credentials", self.verdict(oh.command(self.row, source=".env", destination="code/"), shared=True).cats)

    def test_wrappers_expansions_external_forwarding_options_fail_closed(self):
        good = oh.command(self.row, source="code.py", destination="code/")
        for bad in ["scp code.py devbox:code/", "scp code.py 192.168.1.9:code/", "env " + good,
                    "bash -c " + shlex.quote(good), good + " ; echo changed", good.replace("code.py", "$(cat .env)"),
                    good.replace("ProxyCommand=none", "ProxyCommand=evil"), good.replace("-F /dev/null", "-F ./config"),
                    good.replace("scp -F", "scp -O -F"), good.replace("scp -F", "scp -R -F"),
                    good.replace("scp -F", "scp -D evil -F"), good.replace("devbox:code/", "outside:code/"),
                    good + " outside:code/", good.replace("StrictHostKeyChecking=yes", "StrictHostKeyChecking=no")]:
            with self.subTest(bad=bad): self.assertIsNone(self.desk.prove("Bash", {"command": bad, "internal": True, "owner": True}))

    def test_ssh_forwarding_and_extra_hosts_fail_closed(self):
        good = oh.command(self.row)
        for bad in [good + " other", good.replace("ssh -F", "ssh -R 22:x:22 -F"),
                    good.replace("ssh -F", "ssh -D 1080 -F"), good.replace("ssh -F", "ssh -J jump -F")]:
            with self.subTest(bad=bad): self.assertIsNone(self.desk.prove("Bash", {"command": bad}))

    def test_unknown_expired_unbound_or_changed_key_no_receipt(self):
        good = oh.command(self.row, source="code.py", destination="code/")
        for change in ({"expires": 0}, {"peer": "unbound-peer"}, {"public_key": row()["public_key"][:-4] + "AAAA"}, {"hostname": "outside"}):
            self.record["target"]["rows"] = [{**self.row, **change}]
            with self.subTest(change=change): self.assertIsNone(self.desk.prove("Bash", {"command": good}))
        self.desk.read.return_value = None
        self.assertIn("send", self.verdict(good).cats)

    def test_no_tool_flag_can_supply_proof(self):
        inp = {"command": "scp x other:y", "internal": True, "owner_hosts": self.record, "receipt": "verified"}
        v = danger.classify("Bash", inp)
        self.assertIs(self.desk.narrow("Bash", inp, v), v)

    def test_only_the_transport_registry_hold_can_follow_receipt(self):
        inp = {"command": oh.command(self.row, source="code.py", destination="code/")}
        v = self.desk.narrow("Bash", inp, danger.classify("Bash", inp))
        self.assertEqual(self.desk.narrow_requirement("Bash", inp, "hold", v), "allow")
        for req in ("uv", "deny", "allow"):
            self.assertEqual(self.desk.narrow_requirement("Bash", inp, req, v), req)
        remote = {"command": oh.command(self.row, "git push origin main")}
        self.assertEqual(self.desk.narrow_requirement("Bash", remote, "hold", self.verdict(remote["command"])), "hold")

    def test_different_command_fields_or_execution_wrapper_cannot_borrow_proof(self):
        good = oh.command(self.row, source="code.py", destination="code/")
        for tool, inp in [("Bash", {"command": "scp x external:y", "cmd": good}),
                          ("exec_command", {"cmd": good, "command": "scp x external:y"}),
                          ("Bash", {"command": good, "shell": "/tmp/untrusted-shell"}),
                          ("exec_command", {"cmd": good, "env": {"LD_PRELOAD": "untrusted"}})]:
            with self.subTest(tool=tool, inp=inp): self.assertIsNone(self.desk.prove(tool, inp))

    def test_copy_to_remote_credentials_retains_uv_category(self):
        self.assertIn("credentials", self.verdict(oh.command(self.row, source="code.py", destination=".ssh/authorized_keys")).cats)

    def test_extra_send_and_other_categories_not_removed(self):
        good = oh.command(self.row, source="code.py", destination="code/")
        self.host.danger_extra = [{"tool": "Bash", "category": "send", "why": "custom"}]
        inp = {"command": good}
        base = danger.classify("Bash", inp, self.host.danger_extra)
        self.assertIn("extra.send", self.desk.narrow("Bash", inp, base).rules)
        self.assertIn("send", self.desk.narrow("Bash", inp, base).cats)

    def test_nested_transport_send_not_lost_by_rule_deduplication(self):
        self.assertIn("send", self.verdict(oh.command(self.row, "scp code.py external:code/")).cats)

    def test_explicit_requirement_and_input_unchanged(self):
        inp = {"command": oh.command(self.row), "require_passkey": True}
        before = json.dumps(inp, sort_keys=True)
        self.desk.narrow("Bash", inp, danger.classify("Bash", inp))
        self.assertEqual(json.dumps(inp, sort_keys=True), before)


if __name__ == "__main__": unittest.main()
