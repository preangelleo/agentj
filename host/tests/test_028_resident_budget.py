"""Real resident request + Store reserve/settle against in-memory SQLite, no chmod/files."""
import json
import sqlite3
import threading
import unittest
from zoneinfo import ZoneInfo
from agentj import privacy_cheap
from agentj.bots.store import Store
from agentj.core import presets
from test_028_tail_runtime import candidate

class Budget(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_reservation_stays_on_owner_thread_and_caps_next_call(self):
        store=object.__new__(Store);store.timezone=ZoneInfo('UTC')
        store.db=sqlite3.connect(':memory:',isolation_level=None);store.db.row_factory=sqlite3.Row
        self.addCleanup(store.db.close)
        store.db.execute('CREATE TABLE calls(id TEXT PRIMARY KEY,bot TEXT,visitor TEXT,request TEXT,day TEXT,reserved INTEGER,actual INTEGER,kind TEXT,created INTEGER)')
        config={'limits':{'host_tokens':100000},'provider':{'source':'main'}}
        store.get=lambda _:config
        c=candidate();preset=presets.resolve(c['model'],environ={});calls=[]
        main_thread=threading.get_ident()
        def transport(*args):
            self.assertNotEqual(threading.get_ident(),main_thread)
            calls.append(1)
            return 200,json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"score":0}'}}],
                'usage':{'prompt_tokens':20,'completion_tokens':8}}).encode()
        args=dict(config=c,store=store,bid='bot',vid='visitor',rid='request',env={preset.key_env:'fixture'},transport=transport)
        v=await privacy_cheap.resident_layer2({},**args)
        self.assertEqual(v.status,'ok')
        row=store.db.execute('SELECT actual,kind FROM calls').fetchone()
        self.assertEqual((row['actual'],row['kind']),(28,'jev'))
        config['limits']['host_tokens']=28
        v=await privacy_cheap.resident_layer2({},**args)
        self.assertEqual(v.status,'unavailable');self.assertEqual(calls,[1])

if __name__=='__main__':unittest.main()
