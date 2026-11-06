"""Full access preserves launch provenance and audit source on its ordinary no-card branch."""
import _hermetic  # noqa: F401
import json
from unittest import mock
from agentj import provenance
from agentj.compose import Send
from test_p551_confirm_levels import Base

class Metadata(Base):
    def done(self):
        return [json.loads(s) for s in (self.st.root/'host.log').read_text().splitlines() if json.loads(s).get('ev')=='ask_done'][-1]

    def test_avatar_ordinary_auto_keeps_avatar_audit_source(self):
        self.harness()
        res=self.ask({'summary':'ordinary repair'},tool='AuthRescue',avatar='email')
        self.assertEqual(res['behavior'],'allow')
        self.assertEqual(self.done().get('via'),'avatar')
        self.assertFalse(self.cards())

    def test_system_ordinary_auto_keeps_system_audit_source(self):
        self.harness()
        self.host.agent.cur_send=Send('system','system',1,source=provenance.system())
        res=self.ask({'summary':'ordinary repair'},tool='AuthRescue')
        self.assertEqual(res['behavior'],'allow')
        self.assertEqual(self.done().get('via'),'system')
        self.assertFalse(self.cards())

    def dispatch(self, note):
        self.harness()
        self.host.agent.cur_send=Send('system','system',1,source=provenance.system())
        with mock.patch.object(self.host,'_dispatch_check',return_value=('synthetic.service','ok')), \
                mock.patch.object(self.host,'_peer_launch_note',new=note):
            return self.ask({'command':'echo isolated'},tool='Bash')

    def test_verified_dispatch_records_launch_before_returning(self):
        note=mock.AsyncMock()
        self.assertEqual(self.dispatch(note)['behavior'],'allow')
        note.assert_awaited_once()
        self.assertEqual(self.done().get('via'),'system')

    def test_launch_note_await_cannot_hide_changed_source(self):
        async def change(*a,**kw):
            self.host.agent.cur_send=Send('outside','outside',2,source=provenance.email_guest('guest@example.com'))
        self.assertEqual(self.dispatch(mock.AsyncMock(side_effect=change))['behavior'],'deny')
        self.assertFalse(self.cards())
