"""Opt-in direct Telegram Bot API channel. Own bot key stays in owner's process.
Telegram can read messages. No Telegram command may sign or answer an approval.
Only a human-enrolled private chat sender is accepted; production bot is never used in tests.
"""
import asyncio
import json
import os
import re
import uuid
from types import SimpleNamespace
import urllib.request
from . import preferences as p
from .state import State

def configuration(st=None):
    st=st or State()
    if not st.exists():return None
    cfg=st.config().get('telegram')
    if not isinstance(cfg,dict) or type(cfg.get('owner_id')) is not int or cfg['owner_id']<=0:return None
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',cfg.get('key_env','')) or not os.environ.get(cfg['key_env']):return None
    return cfg

def enroll(owner,key_env,confirm=None):
    if not os.isatty(0) or not os.isatty(1):return {'ok':False,'needs':['human'],'error':'Telegram enrollment requires the owner at an interactive terminal'}
    if type(owner) is not int or owner<=0 or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',key_env):return {'ok':False,'error':'positive numeric owner ID and environment variable NAME required'}
    if not os.environ.get(key_env):return {'ok':False,'error':'local bot key is absent; never paste it in chat'}
    if input('Telegram Bot API is not end-to-end encrypted: Telegram will read channel text/media. Only your private chat is allowed; approvals stay on the paired phone. Enable? [y/N] ').lower()!='y':return {'ok':False,'needs':['human']}
    st=State()
    with st.config_lock():
        cfg=st.config();cfg['telegram']={'owner_id':owner,'key_env':key_env,'generation':uuid.uuid4().hex};st.write_private(st.config_path,json.dumps(cfg).encode())
    return {'ok':True}

def api(cfg,method,values,timeout=20):
    key=os.environ.get(cfg['key_env'])
    if not key:raise ValueError('missing bot key')
    req=urllib.request.Request('https://api.telegram.org/bot'+key+'/'+method,data=json.dumps(values).encode(),headers={'Content-Type':'application/json'},method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args):return None
    try:
        with urllib.request.build_opener(NoRedirect).open(req,timeout=timeout) as r:
            data=r.read(1024*1024+1)
            if len(data)>1024*1024:raise ValueError()
        result=json.loads(data)
        if not result.get('ok'):raise ValueError()
        return result.get('result')
    except Exception:raise ValueError('Telegram request failed; no credentials or provider body logged') from None

def chunks(text,limit=4000):
    """Telegram limits text in UTF-16 units, including supplementary-plane emoji."""
    row=[];units=0
    for char in text:
        size=2 if ord(char)>0xffff else 1
        if units+size>limit:
            yield ''.join(row);row=[];units=0
        row.append(char);units+=size
    if row:yield ''.join(row)

class Telegram:
    def __init__(self,host):
        self.host=host;self.offset=0;self.out=asyncio.Queue(maxsize=20);self.error=False;self.turn_enrollment={};self.enrollment=None
        self.ledger=host.st.root/'telegram-offset.json'
        try:
            saved=json.loads(self.ledger.read_text());self.offset=saved.get('offset',0);self.enrollment=saved.get('enrollment')
        except (OSError,ValueError):pass
    def enabled(self):return any(x.get('type')=='telegram' for x in p.get(self.host.preferences,'channels.items',[]))
    def completed(self,turn):
        if not self.enabled() or not turn or turn.get('src',{}).get('k')!='telegram':return
        enrolled=self.turn_enrollment.pop(turn.get('id'),None)
        if enrolled is None or configuration(self.host.st)!=enrolled:return
        text=turn.get('reply',{}).get('text','')
        if text:
            try:self.out.put_nowait((enrolled,text))
            except asyncio.QueueFull:self.host.st.log('telegram_overflow')
    async def incoming(self,update,cfg):
        if not self.enabled() or configuration(self.host.st)!=cfg:return False
        m=update.get('message') or {};sender=m.get('from') or {};chat=m.get('chat') or {}
        if sender.get('id')!=cfg['owner_id'] or chat.get('id')!=cfg['owner_id'] or chat.get('type')!='private':return False
        text=m.get('text')
        if not isinstance(text,str):
            await asyncio.to_thread(api,cfg,'sendMessage',{'chat_id':cfg['owner_id'],'text':'Use the paired phone for attachments/voice in this alpha. Telegram approvals are unavailable.'})
            return False
        if text.lower().startswith(('/approve','/deny','/pair','/resume','/config','/model')):
            await asyncio.to_thread(api,cfg,'sendMessage',{'chat_id':cfg['owner_id'],'text':'Use your paired phone or local terminal for this action.'});return False
        if text=='/stop':
            await self.host.stop_turn('telegram owner');return True
        if not self.host.agent or self.host.stopped():return False
        if len(text)>20000:return False
        from .text import clean
        s=SimpleNamespace(device='telegram:'+str(cfg['owner_id']),name='Telegram owner',cid=0,source_kind='telegram')
        send=await self.host._accept(s,clean(text,20000),None)
        if send is not None and type(send.turn) is int:self.turn_enrollment[send.turn]=dict(cfg)
        return True
    async def run(self):
        while not self.host.stopping.is_set():
            cfg=configuration(self.host.st)
            if not self.enabled() or not cfg:
                await asyncio.sleep(1);continue
            try:
                if self.enrollment!=cfg:
                    self.offset=0;self.enrollment=dict(cfg)
                while not self.out.empty():
                    enrolled,text=self.out.get_nowait()
                    if enrolled!=cfg or configuration(self.host.st)!=cfg:continue
                    for part in chunks(text):
                        if configuration(self.host.st)!=cfg:break
                        await asyncio.to_thread(api,cfg,'sendMessage',{'chat_id':cfg['owner_id'],'text':part})
                updates=await asyncio.to_thread(api,cfg,'getUpdates',{'offset':self.offset,'timeout':10,'allowed_updates':['message']},15)
                if not self.enabled() or configuration(self.host.st)!=cfg:continue
                for item in updates or []:
                    if configuration(self.host.st)!=cfg:break
                    uid=item.get('update_id')
                    if type(uid) is not int or uid<self.offset:continue
                    # Persist consumption before delivery: no restart duplicate agent work.
                    self.offset=uid+1;self.host.st.write_private(self.ledger,json.dumps({'offset':self.offset,'enrollment':cfg}).encode())
                    await self.incoming(item,cfg)
                self.error=False
            except Exception:
                if not self.error:self.host.hist_add({'k':'sys','text':''},'Telegram unavailable; use the paired phone. / Telegram 不可用，请用已配对手机。','done')
                self.error=True;await asyncio.sleep(10)
