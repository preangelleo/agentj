"""23:17 approved rollback: restore the formal04 pre-relay revocation synchronization barrier."""
import _hermetic
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from websockets.asyncio.server import serve as ws_serve
from agentj import cloud, serve
from agentj.state import State
from test_p317_relay_diag import VerifyingRelay


class SyncRollback(unittest.IsolatedAsyncioTestCase):
    async def test_signed_relay_auth_still_requires_revocation_sync_before_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            st=State(Path(tmp)/'s')
            relay=VerifyingRelay()
            async with ws_serve(relay.handler,'127.0.0.1',0) as server:
                st.init(relay=f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}')
                cloud._write_cloud_file(st,{'api':'https://offline.invalid','host_id':'h'*22,
                    'tenant':{'slug':'fixture','name':'Fixture'},'linked_at':1,'last_seq':0,'via':'code'})
                h=serve.Host(st,events='quiet',read_stdin=False)
                h.seat.cert=lambda:None
                with patch.object(h.credential_revocations,'poll',return_value=(503,{})) as poll:
                    task=asyncio.create_task(h.relay_loop())
                    try:
                        for _ in range(100):
                            rows=[json.loads(x) for x in st.log_path.read_text().splitlines()] if st.log_path.exists() else []
                            if any(x.get('ev')=='relay_down' and x.get('phase')=='revocations' for x in rows):break
                            await asyncio.sleep(.005)
                        self.assertTrue(relay.auths,'actual relay signature verified')
                        self.assertEqual(poll.call_count,1)
                        self.assertFalse(h.relay_up)
                        self.assertFalse(any(x.get('ev')=='relay_up' for x in rows))
                        self.assertTrue(any(x.get('ev')=='relay_down' and x.get('phase')=='revocations' for x in rows))
                    finally:
                        h.stopping.set();task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
