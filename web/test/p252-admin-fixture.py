"""Hermetic ephemeral admin HTTP fixture; real AdminServer/session/assets, no installed Host/serve or real account.
The one-time test URL travels only over private subprocess stdout, never report/console/screenshot.
"""
import json,pathlib,sys,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'host/tests'),str(ROOT/'host')]
import _hermetic
from test_l1 import _state
from agentj.admin import AdminServer
with tempfile.TemporaryDirectory(prefix='p252-admin-')as td:
 server=AdminServer(_state(td)).start()
 try:
  for line in sys.stdin:
   request=json.loads(line)
   if request['op']=='link':print('P252RPC:'+json.dumps({'url':server.new_link()}),flush=True)
   elif request['op']=='stop':break
 finally:server.stop()
