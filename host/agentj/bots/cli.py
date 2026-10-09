"""Agent-facing bot commands: reads or signed-owner proposals, never direct writes."""
import json
import os
from pathlib import Path
import socket
from ..state import State

def send(req):
    path=os.environ.get('AGENTJ_BOTS_SOCK') or str(State().perm_dir/'bots.sock')
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(20);sock.connect(path);sock.sendall((json.dumps(req,ensure_ascii=False)+'\n').encode());data=b''
            while not data.endswith(b'\n'):
                part=sock.recv(65536)
                if not part or len(data)>3*1024*1024:break
                data+=part
        return json.loads(data)
    except (OSError,ValueError):return {'ok':False,'why':'host_unavailable'}

def command(a):
    if a.op=='tool':
        # Dry run is owner-proposed too; authenticated owner sample, no visitor privilege.
        req={'op':'tool_test','id':a.bot,'name':a.tool,'args':json.loads(a.args)}
    elif a.op=='request':req=json.loads(Path(a.file).read_text())
    elif a.op=='result':req={'op':'result','proposal':a.proposal}
    else:req={'op':a.op,**({'id':a.bot} if getattr(a,'bot',None) else {})}
    if getattr(a,'visitor',None):req['visitor']=a.visitor
    res=send(req);print(json.dumps(res,ensure_ascii=False));return 0 if res.get('ok') else 1

def add_parser(sub):
    p=sub.add_parser('bots',help='Manage customer-service bots through signed owner authorization / 经主人签名管理客服 bot')
    sp=p.add_subparsers(dest='op',required=True)
    sp.add_parser('list').set_defaults(fn=command)
    for op in ('detail','history','statistics','handoffs'):
        q=sp.add_parser(op);q.add_argument('bot');q.add_argument('--visitor') if op=='history' else None;q.set_defaults(fn=command)
    q=sp.add_parser('request');q.add_argument('--file',required=True);q.set_defaults(fn=command)
    q=sp.add_parser('result');q.add_argument('proposal');q.set_defaults(fn=command)
    q=sp.add_parser('tool');tp=q.add_subparsers(dest='tool_op',required=True);t=tp.add_parser('test');t.add_argument('bot');t.add_argument('tool');t.add_argument('--args',required=True);t.set_defaults(fn=command)
