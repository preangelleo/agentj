"""Owner reads or signed phone proposals; never raw-key stdout."""
import json
from pathlib import Path
from ..bots.cli import send

def command(a):
    if a.op=='request':req=json.loads(Path(a.file).read_text());req['op']='api_'+req.get('op','')
    elif a.op=='result':req={'op':'result','proposal':a.proposal}
    else:req={'op':'api_'+a.op,**({'id':a.endpoint} if getattr(a,'endpoint',None) else {})}
    result=send(req);print(json.dumps(result,ensure_ascii=False));return 0 if result.get('ok') else 1

def add_parser(sub):
    p=sub.add_parser('api',help='Seat API: owner reads and signed management proposals / 席位 API 管理')
    sp=p.add_subparsers(dest='op',required=True)
    for op in ('list','usage','workflows'):sp.add_parser(op).set_defaults(fn=command)
    for op in ('spec','review','manifest'):q=sp.add_parser(op);q.add_argument('endpoint');q.set_defaults(fn=command)
    q=sp.add_parser('request');q.add_argument('--file',required=True);q.set_defaults(fn=command)
    q=sp.add_parser('result');q.add_argument('proposal');q.set_defaults(fn=command)
