import _hermetic
import asyncio
import json
import tempfile
from unittest.mock import Mock
from test_l1 import _host, _state
from agentj import privacy_host

async def run():
    with tempfile.TemporaryDirectory() as temp:
        host = _host(_state(temp), [])
        host.emit = Mock()
        # Run normal admission on a caller without native execution provenance.
        result = await privacy_host.handle(host, {'t':'privacy_check','gate':'feedback',
                                                  'draft':{'body':'SYNTHETIC PRIVATE DRAFT'}}, peer_pid=None)
        record = list(host.hist.turns.values())[-1]
        assert result['status'] == 'unavailable'
        assert 'SYNTHETIC PRIVATE DRAFT' not in json.dumps(record)
        assert record['src']['k'] == 'sys' and record['card'] == {'kind':'support_feedback_retry'}
        print(json.dumps(record,ensure_ascii=False))
asyncio.run(run())
