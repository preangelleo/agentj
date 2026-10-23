"""P489: bounded, owner-local layout measurements. No content, identifiers, URL or arbitrary text."""
from __future__ import annotations
import json
import math
import os
import re
import stat
import sys
import time

MAX_BYTES = 12_288
FIELDS = {'v','at','ios','standalone','ua','theme','status','conn','kb','known','compacting','level','bottom','innerHeight','clientHeight','vv','layers','meter','look','build'}
COLOR = re.compile(r'(?:#[0-9a-fA-F]{6}|rgba?\([0-9.,% /+\-]+\)|color\((?:srgb|display-p3) [0-9.eE% /+\-]+\))')

def number(v):
    return v is None or type(v) in (int,float) and math.isfinite(v) and abs(v) <= 1e7

def shape(obj, keys):
    return isinstance(obj,dict) and set(obj) == set(keys.split())

def colour(v):
    return v is None or isinstance(v,str) and len(v)<=128 and COLOR.fullmatch(v) is not None

def valid(s):
    if not isinstance(s,dict) or set(s)!=FIELDS or type(s['v']) is not int or s['v']!=1: return False
    if type(s['at']) not in (int,float) or not math.isfinite(s['at']) or not 0<=s['at']<1e15: return False
    if any(type(s[k]) is not bool for k in ('ios','standalone','kb','known','compacting')): return False
    for key,values in {'ua':['chrome','safari','firefox','edge','other'], 'theme':['light','dark','system','unknown'],
                       'status':['idle','working','waiting','question','offline','unknown'], 'conn':['on','off','up','down','quiet','connecting','unknown']}.items():
        if s[key] not in values: return False
    if any(not number(s[k]) for k in ('level','innerHeight','clientHeight')) or not colour(s['bottom']): return False
    if s['vv'] is not None and (not shape(s['vv'],'height offsetTop scale') or not all(number(v) for v in s['vv'].values())): return False
    if not shape(s['meter'],'ctx h5 week') or not all(number(v) for v in s['meter'].values()): return False
    if not shape(s['look'],'factory custom') or s['look']['factory'] not in ('robots','dots','unknown') or type(s['look']['custom']) is not bool: return False
    if not shape(s['build'],'version combined iosCode'): return False
    if not isinstance(s['build']['version'],str) or not re.fullmatch(r'\d{1,3}\.\d{1,3}\.\d{1,3}(?:[a-z]\d{1,4})?',s['build']['version']): return False
    if any(not isinstance(s['build'][k],str) or not re.fullmatch('[0-9a-f]{64}',s['build'][k]) for k in ('combined','iosCode')): return False
    if not isinstance(s['layers'],dict) or set(s['layers'])-{'water','body','root','band','probe'}: return False
    for layer in s['layers'].values():
        if layer is None: continue
        if not shape(layer,'rect background display opacity transform'): return False
        if not shape(layer['rect'],'x y width height') or not all(number(v) for v in layer['rect'].values()): return False
        if not colour(layer['background']) or not number(layer['opacity']): return False
        if layer['display'] not in ('none','block','flex','grid','inline','inline-block','contents','unknown'): return False
        t=layer['transform']
        if t is not None and (not isinstance(t,str) or len(t)>512 or t!='none' and not re.fullmatch(r'matrix(?:3d)?\([0-9., eE+\-]+\)',t)): return False
    return True

def path(st): return st.root / 'layout-diagnostic-last.json'

def save(st, sample):
    try:
        if not valid(sample): return False
        raw=(json.dumps({'received_at':int(time.time()),'sample':sample},allow_nan=False,separators=(',',':'))+'\n').encode()
        if len(raw)>MAX_BYTES: return False
        st.write_private(path(st),raw)
        st.log('layout_diag',status='saved',bytes=len(raw))
        return True
    except (ValueError,TypeError,OSError,OverflowError): return False

def last(st, out=None):
    out=out or sys.stdout
    try:
        fd=os.open(path(st),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        with os.fdopen(fd,'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): raise ValueError('shape')
            raw=f.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES: raise ValueError('size')
        obj=json.loads(raw)
        if not shape(obj,'received_at sample') or type(obj['received_at']) is not int or not valid(obj['sample']): raise ValueError('shape')
        print(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),file=out)
        return 0
    except (OSError,ValueError,TypeError,OverflowError,RecursionError):
        print('尚无有效画面诊断。请在手机设置 → 紧急修复发送。 / No valid layout diagnostic. Send one from Settings → Emergency repair.',file=out)
        return 1

def add_parser(sub, *, diagnostics=None):
    if diagnostics is None:
        p=sub.add_parser('diag',help='只读查看手机画面诊断 / read phone layout diagnostics')
        diagnostics=p.add_subparsers(dest='diag_cmd',required=True)
    q=diagnostics.add_parser('layout',help='画面几何与颜色 / layout geometry and colours')
    q.add_argument('--last',action='store_true',required=True,help='只读查看最近一次 / read the latest sample')
    def run(_):
        from .state import State
        from . import host_diagnostics
        rc = host_diagnostics.request("layout_last")
        raise SystemExit(last(State()) if rc is None else rc)
    q.set_defaults(fn=run)
