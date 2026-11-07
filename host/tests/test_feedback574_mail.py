"""#543 supersedes #397: legacy opt-out never disables completed-mail receipts."""
import _hermetic
from unittest.mock import patch
from test_feedback543_mail_read import Processing
from agentj import mail as M, mail_read

class SupersededOptOut(Processing):
    async def test_legacy_false_still_acknowledges_completed_mail(self):
        cfg = {**self.cfg, 'mark_processed_read': False}
        M.save_config(self.st, cfg)
        with patch.object(self.m, 'password', return_value='synthetic'), patch.object(M, 'mark_read') as mark:
            await mail_read.completed(self.m, cfg, 9, 5, 'synthetic-mid')
            mark.assert_called_once_with(cfg, 'synthetic', 9, 5, 'synthetic-mid')
            self.assertTrue(mail_read.load(self.m, cfg)['rows']['synthetic-mid']['acked'])
