"""Recovered #483 fixed mail failure receipts, fake SMTP only."""
import _hermetic  # noqa: F401
import json
import os
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import contacts_serve, diskq, mail as M
from test_p184_contacts import OWNER, _Svc
MP4 = b"\x00\x00\x00\x18ftypisom"

class MailReasons(_Svc):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.mailbox()
        (self.work / "clip.mp4").write_bytes(MP4 + os.urandom(60_000))
        items, _ = self.media.scan("视频：[clip](clip.mp4)", 9)
        self.mid = items[0]["mid"]
        self.pages[9] = {"id": 9, "end": "done", "reply": {"text": "视频：[clip](clip.mp4)"}, "media": items}

    def logs(self):
        diskq.flush()
        return [json.loads(x) for x in self.st.log_path.read_text().splitlines() if '"mail_fwd"' in x]

    async def test_one_file_over_the_limit_is_refused_before_smtp_with_size(self):
        old = contacts_serve.FILES_MAX
        contacts_serve.FILES_MAX = 10_000
        self.addCleanup(setattr, contacts_serve, "FILES_MAX", old)
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "mid": self.mid})
        self.assertEqual((res["ok"], res["why"]), (False, "too_big"), res)
        self.assertEqual((res["bytes"], res["limit"]), ((self.work / "clip.mp4").stat().st_size, 10_000))
        self.assertEqual(self.smtp.sent, [], "never handed to the mail server")
        rec = self.logs()[-1]
        self.assertEqual((rec["result"], rec["files"]), ("too_big", 1))

    async def test_smtp_552_is_named_and_logged(self):
        self.smtp.data_reply = b"552 5.3.4 Message size exceeds fixed maximum message size"
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "mid": self.mid})
        self.assertEqual((res["ok"], res["why"], res["detail"]), (False, "send_failed", "smtp_too_big"), res)
        rec = self.logs()[-1]
        self.assertEqual((rec["result"], rec.get("why"), rec["files"]), ("send_failed", "smtp_too_big", 1), rec)
        self.assertNotIn("Message size exceeds", json.dumps(rec))
        self.assertNotIn("Message size exceeds", json.dumps(res))

    async def test_network_failure_logs_its_reason(self):
        cfg = M.read_config(self.st)
        cfg["smtp"]["port"] = 1          # nothing listens: connection refused
        M.save_config(self.st, M.validate(cfg))
        # A destination change intentionally invalidates the prior encrypted password.
        M.store_secret(self.st, bytearray(self.smtp.password.encode()), cfg["address"])
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "mid": self.mid})
        self.assertEqual((res["ok"], res["why"]), (False, "send_failed"), res)
        rec = self.logs()[-1]
        self.assertEqual(rec["result"], "send_failed")
        self.assertTrue(rec.get("why", "").startswith("smtp_"), rec)
        self.assertEqual(rec["files"], 1)
