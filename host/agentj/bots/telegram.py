"""Host-direct Telegram transport. Telegram sees message plaintext; our cloud does not.

Private chats only, host-owned visitor binding, persistent offsets and request IDs.
All URL errors are fixed codes: a token must never escape via an exception or log.
"""
import asyncio
import collections
import contextlib
import hashlib
import re
from ..provider_profiles import secure_read,ProviderError
from .network import request,request_json
from .store import BotError
from .engine import UNAVAILABLE

NAME='TELEGRAM_BOT_TOKEN'
TURN_TIMEOUT=120
MAX_PENDING=20
SEGMENT=4096     # Telegram's sendMessage limit; a longer reply goes out in order, split at line breaks where possible
MAX_SEGMENTS=8
TOO_LONG="Message too long (maximum 2000 characters). Please send a shorter message. 消息太长（最多 2000 字），请缩短后再发。"

def segments(text,size=SEGMENT,most=MAX_SEGMENTS):
    """P193 (B11): never cut a reply silently. Split at the last paragraph / line / space break inside each window."""
    out=[]
    while text and len(out)<most:
        if len(text)<=size:out.append(text);break
        # the first kind of break (paragraph, line, word) found in the second half of the window wins
        cut=next((c for c in (text.rfind(b,0,size) for b in ('\n\n','\n',' ')) if c>=size//2),size)
        out.append(text[:cut].rstrip());text=text[cut:].lstrip()
    else:
        if text and out:out[-1]=out[-1][:size-1]+'…'   # past MAX_SEGMENTS the end is marked, never dropped unseen
    return [x for x in out if x]

def token(store,bid):
    try:value=secure_read(store.secrets(bid)/NAME,256).decode().strip()
    except (OSError,ValueError,ProviderError):raise BotError('telegram_key_required') from None
    if not re.fullmatch(r'[0-9]{5,16}:[A-Za-z0-9_-]{20,200}',value):raise BotError('invalid_telegram_token')
    return value

def api(secret,method,body,transport=request_json):
    if method not in ('getMe','getUpdates','sendMessage','getUserProfilePhotos','removeMyProfilePhoto'):raise BotError('invalid_telegram_method')
    try:
        value=transport('https://api.telegram.org/bot'+secret+'/'+method,'POST',body,timeout=30,limit=512*1024,domains=['api.telegram.org'])
        if not isinstance(value,dict) or value.get('ok') is not True:raise BotError('telegram_unavailable')
        return value.get('result')
    except Exception:raise BotError('telegram_unavailable') from None

def upload(secret,method,fields,field,name,mime,data,transport=request):
    """P275: one multipart upload (setMyProfilePhoto only), same pinned HTTPS boundary and fixed error code as api()."""
    if method!='setMyProfilePhoto':raise BotError('invalid_telegram_method')
    import json,uuid
    boundary='agentj'+uuid.uuid4().hex;body=bytearray()
    for k,v in fields.items():body+=('--'+boundary+'\r\nContent-Disposition: form-data; name="'+k+'"\r\n\r\n'+str(v)+'\r\n').encode()
    body+=('--'+boundary+'\r\nContent-Disposition: form-data; name="'+field+'"; filename="'+name+'"\r\nContent-Type: '+mime+'\r\n\r\n').encode()+data+('\r\n--'+boundary+'--\r\n').encode()
    try:
        value=json.loads(transport('https://api.telegram.org/bot'+secret+'/'+method,'POST',bytes(body),{'Content-Type':'multipart/form-data; boundary='+boundary},timeout=60,limit=65536,domains=['api.telegram.org']))
        if not isinstance(value,dict) or value.get('ok') is not True:raise BotError('telegram_unavailable')
        return value.get('result')
    except Exception:raise BotError('telegram_unavailable') from None

def avatar(store,bid,root,action=None,request=api,send=upload):
    """P275: action None = the automatic first check (tg_avatar.ensure); else status / set / reset for the CLI and the card."""
    from .. import tg_avatar
    secret=token(store,bid)
    me=request(secret,'getMe',{})
    if not isinstance(me,dict) or me.get('is_bot') is not True or type(me.get('id')) is not int:raise BotError('telegram_unavailable')
    call=lambda m,v:request(secret,m,v)
    put=lambda *a:send(secret,*a)
    try:
        if action is None:return tg_avatar.ensure(root,call,put,me['id'])
        return tg_avatar.act(root,action,call,put,me['id'])
    except BotError:raise
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
    def identity(self,uid):
        return hashlib.sha256(('telegram:'+str(uid)).encode()).hexdigest()[:32]
    def refusal(self,error):
        return UNAVAILABLE
    async def no_text(self,chat):
        return None
    async def deliver(self,chat,result):
        if not self.enabled():return
        for part in segments(result['text']):
            if not self.enabled():return
            await asyncio.to_thread(self.request,self.secret,'sendMessage',{'chat_id':chat,'text':part,'link_preview_options':{'is_disabled':True}})
    async def handle(self,update):
        message=update.get('message',{})
        if not isinstance(message,dict):return
        chat=message.get('chat',{});user=message.get('from',{})
        if not isinstance(chat,dict) or not isinstance(user,dict):return
        # Group members and public usernames are not authentication identities.
        if chat.get('type')!='private' or type(chat.get('id')) is not int or type(user.get('id')) is not int or user.get('is_bot') or chat['id']!=user['id']:return
        text=message.get('text')
        if not isinstance(text,str) or not text:return await self.no_text(chat['id'])
        if len(text)>2000:
            await self.deliver(chat['id'],{'text':TOO_LONG});return
        if not self.enabled():return
        try:
            # Admission can fail before Engine.reply, and an upstream call can stall.
            # Keep the channel responsive and give every admitted private text a fixed reply.
            async with asyncio.timeout(TURN_TIMEOUT):
                identity=self.identity(user['id'])
                vid,cap=self.store.admit_visitor(self.bid,identity)
                if hasattr(self.runtime,'telegram_targets'):self.runtime.telegram_targets[(self.bid,vid)]=(self,chat['id'])
                rid=hashlib.sha256((self.digest+':'+str(update['update_id'])).encode()).hexdigest()[:32]
                result=await self.runtime.engine().reply(self.bid,vid,cap,rid,text)
                if not isinstance(result,dict) or not isinstance(result.get('text'),str) or not result['text']:
                    raise BotError('telegram_unavailable')
        except asyncio.CancelledError:raise
        except Exception as e:
            self.store.event(self.bid,'','telegram_turn','unavailable')
            result={'text':self.refusal(e),'status':'refused'}
        # Mutation/estop while a model runs cancels outbound delivery.
        if not self.enabled():return
        await self.deliver(chat['id'],result)
    async def auto_avatar(self):
        # P275: once per Telegram bot, only when it has no photo; never stops the channel.
        try:result=await asyncio.to_thread(avatar,self.store,self.bid,self.runtime.host.st.root,None,self.request)
        except Exception:result='failed'
        if result!='skipped':self.store.event(self.bid,'','telegram_avatar',result)
    async def run(self):
        # Bounded pending work; one chain per chat preserves order while other chats progress.
        avatar_task=asyncio.create_task(self.auto_avatar())
        pending=set();tails={}
        async def after(previous,update):
            if previous is not None:
                with contextlib.suppress(Exception):await previous
            if not self.enabled():return
            try:await self.handle(update)
            except asyncio.CancelledError:raise
            except Exception:self.store.event(self.bid,'','telegram','unavailable')
        def finished(task,chat):
            pending.discard(task)
            if tails.get(chat) is task:tails.pop(chat,None)
        try:
            while self.enabled():
                try:
                    updates=await asyncio.to_thread(self.request,self.secret,'getUpdates',{'offset':self.offset(),'timeout':20,'limit':10,'allowed_updates':['message']})
                    if not isinstance(updates,list) or len(updates)>10:raise BotError('telegram_unavailable')
                    for update in updates:
                        if not isinstance(update,dict) or type(update.get('update_id')) is not int:raise BotError('telegram_unavailable')
                        if update['update_id']<self.offset():continue
                        if not self.enabled():return
                        if len(pending)>=MAX_PENDING:
                            await asyncio.wait(pending,return_when=asyncio.FIRST_COMPLETED)
                        message=update.get('message',{})
                        chat=message.get('chat',{}) if isinstance(message,dict) else {}
                        cid=chat.get('id') if isinstance(chat,dict) else None
                        cid=cid if type(cid) is int else None
                        task=asyncio.create_task(after(tails.get(cid),update))
                        pending.add(task);tails[cid]=task
                        task.add_done_callback(lambda t,c=cid:finished(t,c))
                        # At-most-once intake (unchanged): scheduled work is not replayed after a crash.
                        self.advance(update['update_id']+1)
                    await asyncio.sleep(.1 if not updates else 0)
                except asyncio.CancelledError:raise
                except Exception:
                    self.store.event(self.bid,'','telegram','unavailable')
                    await asyncio.sleep(5)
        finally:
            avatar_task.cancel()
            tasks=list(pending)
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            pending.clear();tails.clear()


# P297: guests of the owner's own Agent J Telegram bot. Same bot, two identities: the P283-enrolled owner chat is the main
# session (serve's telegram.py, never here); every other private chat is a visitor of the one bot the owner attached.
LIMIT='Too many messages right now. Please try again later. / 消息有点多，请稍后再试。'
HOURS='We are outside service hours. Please come back later. / 现在不在服务时间，请稍后再来。'
TEXT_ONLY='Text messages only, please. / 请发文字消息。'
NOTICE_GAP=60

class GuestChannel(Channel):
    """Visitor turns arriving on the owner's bot token. The token stays in serve's environment: `send(chat, text)` is
    serve's own Bot API call, so this class never reads it. Visitors are keyed by bot id + Telegram user id (a separate
    identity from a bot's own-token channel); limits, Jev reviews, knowledge, tools and the 30-day history are the bot's."""
    def __init__(self,runtime,bid,bot_id,send):
        self.runtime=runtime;self.store=runtime.manager.state();self.bid=bid;self.send=send;self.secret=None;self.bot_id=bot_id
        self.digest=hashlib.sha256(('agentj-owner-bot:'+str(bot_id)).encode()).hexdigest()
        self.epoch=self.store.lifecycle(bid)['epoch'];self.pending=set();self.tails={};self.noticed=collections.OrderedDict()
    def enabled(self):
        try:c=self.store.get(self.bid);life=self.store.lifecycle(self.bid)
        except BotError:return False
        return c['enabled'] and c['telegram'].get('owner_bot') is True and not life['removed'] and life['epoch']==self.epoch and not self.runtime.host.stopped()
    def identity(self,uid):
        return hashlib.sha256(('telegram-owner-bot:'+str(self.bot_id)+':'+str(uid)).encode()).hexdigest()[:32]
    def refusal(self,error):
        why=str(error) if isinstance(error,BotError) else ''
        return LIMIT if why in ('message_limit','busy','session_busy') else HOURS if why=='outside_hours' else UNAVAILABLE
    async def no_text(self,chat):
        now=asyncio.get_running_loop().time()
        if now-self.noticed.get(chat,-1e9)<NOTICE_GAP:return
        self.noticed[chat]=now;self.noticed.move_to_end(chat)
        while len(self.noticed)>512:self.noticed.popitem(last=False)
        await self.deliver(chat,{'text':TEXT_ONLY})
    async def deliver(self,chat,result):
        if not self.enabled():return
        await self.send(chat,result['text'][:4096])
    def submit(self,update):
        """Called from serve's Telegram poll loop: never waits for a model. One chain per chat keeps order; at most
        MAX_PENDING turns wait (False = full, the caller answers nothing more). At-most-once like the own-token channel."""
        if not self.enabled() or len(self.pending)>=MAX_PENDING:return False
        chat=((update.get('message') or {}).get('chat') or {}).get('id')
        previous=self.tails.get(chat)
        async def after():
            if previous is not None:
                with contextlib.suppress(Exception):await previous
            if not self.enabled():return
            try:await self.handle(update)
            except asyncio.CancelledError:raise
            except Exception:self.store.event(self.bid,'','telegram_guest','unavailable')
        task=asyncio.create_task(after());self.pending.add(task);self.tails[chat]=task
        def finished(t):
            self.pending.discard(t)
            if self.tails.get(chat) is t:self.tails.pop(chat,None)
        task.add_done_callback(finished)
        return True
    async def close(self):
        tasks=list(self.pending)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.pending.clear();self.tails.clear()
