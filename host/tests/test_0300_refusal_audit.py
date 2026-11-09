"""Pre-card refusal records request/task hashes, never plaintext or a fake device."""
import _hermetic
import unittest
from agentj import approvals
import test_uv_list as fixture

class RefusalAudit(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ListApproval.asyncSetUp

    async def test_two_refusals_are_correlated_without_raw_input_or_signer(self):
        self.h.stopping.set()
        task=self.h._task_id()
        canary='SYNTHETIC_AUDIT_PRIVATE_CANARY_0300'
        path=str(self.st.root/'private-owner-file')
        rows=[]
        for suffix in ('first','second'):
            inp={'summary':canary,'path':path,'action':suffix}
            receipt={}
            result=await self.h.ask('AuthRescue',inp,grant_info=True,_receipt=receipt)
            self.assertEqual((result['behavior'],result['reason']),('deny','serve_stop'))
            self.assertNotIn('grant_info',result)
            row=approvals.read_log(self.st)[-1];rows.append(row)
            self.assertEqual(receipt['id'],row['id'])
            self.assertFalse(receipt['approval_card_shown'])
            self.assertEqual(row['task_id'],task)
            self.assertEqual(row['agent'],self.h.agent.kind)
            self.assertEqual(row['input_sha256'],approvals.input_digest(inp))
            self.assertEqual(row['shown_sha256'],approvals.shown_digest('AuthRescue','[not displayed]'))
            self.assertEqual((row['decision'],row['reason']),('deny','serve_stop'))
            for key in ('device','sk','sig'):self.assertIsNone(row[key])
            self.assertFalse(set(row)&{'fa','source','root','summary','tool_input'})
        self.assertNotEqual(rows[0]['id'],rows[1]['id'])
        self.assertNotEqual(rows[0]['input_sha256'],rows[1]['input_sha256'])
        self.assertFalse(self.h.asks)
        for file in (self.st.approvals_path,self.st.log_path):
            text=file.read_text()
            self.assertNotIn(canary,text);self.assertNotIn(path,text)
            for row in rows:self.assertIn(row['id'],text)
