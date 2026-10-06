import _hermetic
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from agentj import protocol_handler as handler
from agentj import main_identity
from agentj.state import State

class PairProtocol(unittest.TestCase):
    def test_url_cannot_select_other_host_or_carry_credentials(self):
        for url in ['agentj://pair','agentj://pair?channel=public-channel']:
            handler.validate_url(url,'public-channel')
        for url in ['https://pair','agentj://pair?channel=wrong','agentj://pair?token=secret','agentj://pair#token','agentj://user@pair','agentj://pair?channel=public-channel&channel=public-channel','agentj://pair/approve']:
            with self.assertRaises(ValueError):handler.validate_url(url,'public-channel')

    def test_opens_only_local_one_use_admin_without_printing(self):
        with tempfile.TemporaryDirectory() as d:
            st=State(Path(d)/'state');st.init()
            stop=threading.Event();stop.set()
            urls=[]
            with patch('agentj.state.State',return_value=st):
                handler.open_pair('agentj://pair?channel='+st.config()['channel'],stop=stop,opener=lambda u:urls.append(u) or True)
            self.assertEqual(len(urls),1)
            self.assertTrue(urls[0].startswith('http://127.0.0.1:'))
            self.assertIn('/?pair=1#t=',urls[0])
            self.assertEqual(st.devices(),{})

    def test_wrong_computer_shows_static_local_explanation_without_admission(self):
        with tempfile.TemporaryDirectory() as d:
            st=State(Path(d)/'state');st.init();urls=[]
            with patch('agentj.state.State',return_value=st),patch('agentj.admin.AdminServer') as server:
                handler.open_pair('agentj://pair?channel=another',opener=lambda u:urls.append(u) or True)
                server.assert_not_called()
            self.assertEqual(len(urls),1)
            self.assertTrue(urls[0].startswith('data:text/html;'))
            self.assertNotIn('another',urls[0]);self.assertNotIn('#t=',urls[0])

    def test_linux_registration_uses_exact_python_and_url_argument(self):
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{'XDG_DATA_HOME':d}),patch.object(handler.sys,'platform','linux'),patch.object(handler.subprocess,'run') as run:
            out=handler.install();text=Path(out['path']).read_text()
            self.assertIn(' -m agentj protocol open %u',text)
            self.assertIn('MimeType=x-scheme-handler/agentj;',text)
            run.assert_called_once_with(['xdg-mime','default','agentj-pair.desktop','x-scheme-handler/agentj'],check=True,timeout=15,capture_output=True)

    def test_installation_identity_requires_real_pairing_without_locking_routine_work(self):
        for language in ['en','zh']:
            p=main_identity.prompt({'kind':'codex','language':language})
            self.assertIn('agentj devices --json',p)
            self.assertIn('agentj://pair',p)
            self.assertIn(main_identity.PAIRING_LINE[language],p)

class SeatUnbound(unittest.TestCase):
    def test_removed_seat_revokes_existing_remotes_and_link_preserves_files(self):
        import asyncio
        from agentj import cloud,serve,wire
        with tempfile.TemporaryDirectory() as d:
            st=State(Path(d)/'state');st.init()
            did=st.add_device(b'R'*32,'Existing remote')
            link={'api':'https://agentj.app/api','host_id':'old_host','tenant':{'slug':'acme-co','name':'Acme'},'linked_at':1,'last_seq':0,'via':'seat'}
            cloud.write_cloud(st,link);expected=cloud.read_cloud(st)
            history=Path(d)/'chat.txt';history.write_text('local history')
            host=serve.Host(st,events='quiet',read_stdin=False)
            asyncio.run(host.seat_unbound(expected));asyncio.run(host.seat_unbound(expected))
            self.assertEqual(st.devices(),{});self.assertIsNone(cloud.read_cloud(st));self.assertEqual(history.read_text(),'local history')
            self.assertFalse(host.stopping.is_set(),'no lock on native work or owner reinstall')

    def test_late_unbound_reply_cannot_revoke_a_new_binding(self):
        import asyncio
        from agentj import cloud,serve,wire
        with tempfile.TemporaryDirectory() as d:
            st=State(Path(d)/'state');st.init();did=st.add_device(b'R'*32,'Remote')
            link={'api':'https://agentj.app/api','host_id':'old_host','tenant':{'slug':'acme-co','name':'Acme'},'linked_at':1,'last_seq':0,'via':'seat'}
            cloud.write_cloud(st,link);old=cloud.read_cloud(st);cloud.write_cloud(st,{**link,'host_id':'new_host','linked_at':2})
            host=serve.Host(st,events='quiet',read_stdin=False);asyncio.run(host.seat_unbound(old))
            self.assertIn(did,st.devices());self.assertEqual(cloud.read_cloud(st)['host_id'],'new_host')
