"""0.27.1: stopping is an execution boundary for ordinary, full and UV requests."""
import _hermetic  # noqa: F401
from unittest import mock
from agentj import approvals
from test_p551_confirm_levels import Base

class Stopping(Base):
    def stopped_request(self, *, full=False, uv=False):
        if full:
            self.harness()
        self.host.stopping.set()
        res = self.ask({'summary':'ordinary repair'}, tool='AuthRescue', grant_info=True, require_passkey=uv)
        self.assertEqual((res['behavior'],res.get('reason')),('deny','serve_stop'))
        self.assertEqual((self.last()['decision'],self.last()['reason']),('deny','serve_stop'))
        self.assertEqual(self.last()['input_sha256'],approvals.input_digest({'summary':'ordinary repair'}))
        self.assertFalse(self.cards())
        self.assertNotIn('grant_info',res)

    def test_standard_stopping_denies_without_card(self):
        self.stopped_request()

    def test_full_stopping_denies_without_inheriting_authority(self):
        self.stopped_request(full=True)

    def test_uv_stopping_denies_without_requesting_face_id(self):
        self.stopped_request(full=True,uv=True)

    def test_stopping_during_classification_denies_full_automatic_branch(self):
        self.harness()
        original=self.host._classify
        def stop(*a,**kw):
            self.host.stopping.set()
            return original(*a,**kw)
        with mock.patch.object(self.host,'_classify',side_effect=stop):
            res=self.ask({'summary':'ordinary repair'},tool='AuthRescue',grant_info=True)
        self.assertEqual((res['behavior'],res.get('reason')),('deny','serve_stop'))
        self.assertFalse(self.cards())
        self.assertNotIn('grant_info',res)

    def test_stopping_during_full_scope_check_denies_inherited_result(self):
        from agentj import approval_level
        self.harness()
        def stop(*a,**kw):
            self.host.stopping.set()
            return None
        with mock.patch.object(approval_level,'full_access_keep',side_effect=stop):
            res=self.ask({'summary':'ordinary repair'},tool='AuthRescue',grant_info=True)
        self.assertEqual((res['behavior'],res.get('reason')),('deny','serve_stop'))
        self.assertFalse(self.cards())
        self.assertNotIn('grant_info',res)
