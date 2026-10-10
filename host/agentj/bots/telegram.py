"""Host-direct Telegram transport. Telegram sees message plaintext; our cloud does not.

Private chats only, host-owned visitor binding, persistent offsets and request IDs.
All URL errors are fixed codes: a token must never escape via an exception or log.
"""
import asyncio
import contextlib
import hashlib
import re
from ..provider_profiles import secure_read,ProviderError
from .network import request_json
from .store import BotError
from .engine import UNAVAILABLE

NAME='TELEGRAM_BOT_TOKEN'
TURN_TIMEOUT=120

def token(store,bid):
    try:value=secure_read(store.secrets(bid)/NAME,256).decode().strip()
    except (OSError,ValueError,ProviderError):raise BotError('telegram_key_required') from None
    if not re.fullmatch(r'[0-9]{5,16}:[A-Za-z0-9_-]{20,200}',value):raise BotError('invalid_telegram_token')
    return value

def api(secret,method,body,transport=request_json):
    if method not in ('getMe','getUpdates','sendMessage'):raise BotError('invalid_telegram_method')
    try:
        value=transport('https://api.telegram.org/bot'+secret+'/'+method,'POST',body,timeout=30,limit=512*1024,domains=['api.telegram.org'])
        if not isinstance(value,dict) or value.get('ok') is not True:raise BotError('telegram_unavailable')
        return value.get('result')
    except Exception:raise BotError('telegram_unavailable') from None

def verify(store,bid,request=api):
    value=request(token(store,bid),'getMe',{})
    if not isinstance(value,dict) or value.get('is_bot') is not True or type(value.get('id')) is not int:raise BotError('telegram_unavailable')
    return True

class Channel:
    def __init__(self,runtime,bid,request=api):
        self.runtime=runtime;self.store=runtime.manager.state();self.bid=bid;self.request=request
        self.secret=token(self.store,bid)
        self.digest=hashlib.sha256(self.secret.encode()).hexdigest()
        self.epoch=self.store.lifecycle(bid)['epoch']
    def enabled(self):
        c=self.store.get(self.bid)
        return c['enabled'] and c['telegram']['enabled'] and not self.runtime.host.stopped() and self.store.lifecycle(self.bid)['epoch']==self.epoch
    def offset(self):
        row=self.store.db.execute('SELECT token_hash,offset FROM telegram_cursors WHERE bot=?',(self.bid,)).fetchone()
        return row['offset'] if row and row['token_hash']==self.digest else 0
    def advance(self,n):
        self.store.db.execute('INSERT INTO telegram_cursors VALUES(?,?,?) ON CONFLICT(bot) DO UPDATE SET token_hash=excluded.token_hash,offset=excluded.offset',(self.bid,self.digest,n))
    async def deliver(self,chat,result):
        if not self.enabled():return
        await asyncio.to_thread(self.request,self.secret,'sendMessage',{'chat_id':chat,'text':result['text'][:4096],'link_preview_options':{'is_disabled':True}})
    async def handle(self,update):
        message=update.get('message',{})
        if not isinstance(message,dict):return
        chat=message.get('chat',{});user=message.get('from',{})
        if not isinstance(chat,dict) or not isinstance(user,dict):return
        # Group members and public usernames are not authentication identities.
        if chat.get('type')!='private' or type(chat.get('id')) is not int or type(user.get('id')) is not int or user.get('is_bot') or chat['id']!=user['id']:return
        text=message.get('text')
        if not isinstance(text,str) or not text or len(text)>2000:return
        if not self.enabled():return
        try:
            # Admission can fail before Engine.reply, and an upstream call can stall.
            # Keep the channel responsive and give every admitted private text a fixed reply.
            async with asyncio.timeout(TURN_TIMEOUT):
                identity=hashlib.sha256(('telegram:'+str(user['id'])).encode()).hexdigest()[:32]
                vid,cap=self.store.admit_visitor(self.bid,identity)
                if hasattr(self.runtime,'telegram_targets'):self.runtime.telegram_targets[(self.bid,vid)]=(self,chat['id'])
                rid=hashlib.sha256((self.digest+':'+str(update['update_id'])).encode()).hexdigest()[:32]
                result=await self.runtime.engine().reply(self.bid,vid,cap,rid,text)
                if not isinstance(result,dict) or not isinstance(result.get('text'),str) or not result['text']:
                    raise BotError('telegram_unavailable')
        except asyncio.CancelledError:raise
        except Exception:
            self.store.event(self.bid,'','telegram_turn','unavailable')
            result={'text':UNAVAILABLE,'status':'refused'}
        # Mutation/estop while a model runs cancels outbound delivery.
        if not self.enabled():return
        await self.deliver(chat['id'],result)
    async def run(self):
        while self.enabled():
            try:
                updates=await asyncio.to_thread(self.request,self.secret,'getUpdates',{'offset':self.offset(),'timeout':20,'limit':10,'allowed_updates':['message']})
                if not isinstance(updates,list) or len(updates)>10:raise BotError('telegram_unavailable')
                for update in updates:
                    if not isinstance(update,dict) or type(update.get('update_id')) is not int:raise BotError('telegram_unavailable')
                    if update['update_id']<self.offset():continue
                    if not self.enabled():return
                    await self.handle(update)
                    self.advance(update['update_id']+1)
                if not updates:await asyncio.sleep(.1)
            except asyncio.CancelledError:raise
            except Exception:
                self.store.event(self.bid,'','telegram','unavailable')
                await asyncio.sleep(5)
