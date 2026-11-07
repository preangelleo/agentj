"""Synthetic stdio bridge for real-browser Noise tests. No sockets/models/state."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch
from agentj.voice_stream import Manager
from test_voice_stream import Transport

async def main():
    manager=None
    def output(m):print(json.dumps(m),flush=True)
    async def send(s,m):output(m)
    async def say(s,m):
        output({'t':'fixture_submission','sid':m['sid']})
        async def reply():
            send=SimpleNamespace(device=s.device,sid=m['sid'])
            await asyncio.sleep(.025)
            await manager.reply(send,'First synthetic sentence.')
            await asyncio.sleep(.100)
            await manager.reply(send,'Last synthetic sentence.',True)
        asyncio.create_task(reply())
    async def cancel(*args,**kwargs):output({'t':'fixture_cancel'})
    async def connect(cfg):return Transport()
    host=SimpleNamespace(preferences={},_paired_live=lambda s:True,stopped=lambda:False,
        seat_refusal=lambda:None,agent=SimpleNamespace(cur_send=None,halt=cancel),
        send_app=send,on_say=say,sends=SimpleNamespace(cancel=cancel))
    manager=Manager(host)
    session=SimpleNamespace(cid=1,device='synthetic-browser')
    with patch('agentj.voice_stream.connect',connect),patch.object(manager,'available',return_value=True):
        while line:=await asyncio.to_thread(sys.stdin.readline):
            msg=json.loads(line)
            await manager.handle(session,msg)
        await manager.drop(1)
if __name__=='__main__':asyncio.run(main())
