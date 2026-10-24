"""Handoff-only verified contact and signed owner mail; no automatic retry or SMTP credentials."""
import asyncio
import hashlib
import re
import secrets
from .store import BotError
from .policy import inbound,problems

def setup(store):
    store.db.executescript("""
    CREATE TABLE IF NOT EXISTS bot_contacts(bot TEXT,visitor TEXT,email TEXT,code_hash TEXT,expires INTEGER,tries INTEGER,verified INTEGER,sends INTEGER,created INTEGER,PRIMARY KEY(bot,visitor));
    CREATE TABLE IF NOT EXISTS bot_mail_sends(bot TEXT,visitor TEXT,request TEXT,created INTEGER,PRIMARY KEY(bot,visitor,request));
    """)

def contact(store,bid,vid,email):
    setup(store)
    if not store.get(bid)['handoff']['email_enabled']:raise BotError('email_disabled')
    if not isinstance(email,str) or len(email)>254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",email):raise BotError('invalid_email')
    old=store.db.execute('SELECT * FROM bot_contacts WHERE bot=? AND visitor=?',(bid,vid)).fetchone()
    if old:
        if old['email']!=email:raise BotError('contact_already_bound')
        return
    store.db.execute('INSERT INTO bot_contacts VALUES(?,?,?,?,?,?,?,?,?)',(bid,vid,email,'',0,0,0,0,store.now()))

def verify(store,bid,vid,code):
    setup(store)
    with store.transaction():
        row=store.db.execute('SELECT * FROM bot_contacts WHERE bot=? AND visitor=?',(bid,vid)).fetchone()
        valid=bool(row and row['expires']>store.now() and row['tries']<5 and isinstance(code,str) and re.fullmatch(r'[0-9]{6}',code))
        if row:store.db.execute('UPDATE bot_contacts SET tries=tries+1 WHERE bot=? AND visitor=?',(bid,vid))
        good=valid and secrets.compare_digest(row['code_hash'],hashlib.sha256(code.encode()).hexdigest())
        if good:store.db.execute("UPDATE bot_contacts SET verified=1,code_hash='',expires=0 WHERE bot=? AND visitor=?",(bid,vid))
    if not good:raise BotError('email_code_refused')

def details(store,bid,vid):
    setup(store)
    row=store.db.execute('SELECT email,verified,sends FROM bot_contacts WHERE bot=? AND visitor=?',(bid,vid)).fetchone()
    return dict(row) if row else None

async def signed_send(manager,req):
    # Device signatures admit the request; only final-message passkey UV authorizes SMTP.
    from .. import mail
    store=manager.state();setup(store)
    row=store.db.execute("SELECT * FROM handoffs WHERE id=? AND bot=? AND status IN ('pending','replied')",(req.get('handoff'),req.get('id'))).fetchone()
    if not row or store.now()-row['created']>=store.get(row['bot'])['handoff']['reply_window_h']*3600:raise BotError('handoff_expired')
    bid,vid=row['bot'],row['visitor'];c=store.get(bid)
    if manager.host.stopped() or store.lifecycle(bid)['removed'] or not c['handoff']['email_enabled']:raise BotError('email_disabled')
    target=store.db.execute('SELECT * FROM bot_contacts WHERE bot=? AND visitor=?',(bid,vid)).fetchone()
    if not target or req.get('to')!=target['email']:raise BotError('contact_mismatch')
    if target['sends']>=3:raise BotError('email_limit')
    code_mail=req['op']=='human_email_code'
    if code_mail:
        if target['verified'] or target['expires']>store.now():raise BotError('email_code_pending')
        code=f'{secrets.randbelow(1000000):06d}'
        text='Your customer service email verification code / 客服邮箱验证码: '+code+'\nValid for 10 minutes / 10 分钟有效。'
    else:
        if not target['verified']:raise BotError('email_not_verified')
        text=req.get('text')
        if not isinstance(text,str) or not 0<len(text)<=4000:raise BotError('invalid_message')
        text=inbound(text)
        result=await manager.runtime.engine().human_reply(bid,vid,row['request'],text)
        text=result['text']
    if problems(text,c):raise BotError('safety_refused')
    cfg=mail.read_config(manager.host.st)
    if not cfg['enabled']:raise BotError('mail_required')
    msg=mail.build_notes(cfg,[target['email']],'Customer service / 客服',text,'Agent J')
    ans=await manager.host.ask('agentj mail send',mail.send_target(cfg,msg,[target['email']]),
                               batch=False,require_passkey=True)
    if ans.get('behavior')!='allow':raise BotError('denied')
    if mail.read_config(manager.host.st)!=cfg:raise BotError('stale')
    password=mail.load_secret(manager.host.st,cfg['address'])
    if not password:raise BotError('mail_required')
    # Recheck after safety and final UV awaits. Reserve durably before SMTP; ambiguous failure is charged, never retried.
    with store.transaction():
        cur=store.db.execute('SELECT * FROM bot_contacts WHERE bot=? AND visitor=?',(bid,vid)).fetchone()
        current=store.db.execute('SELECT * FROM handoffs WHERE id=?',(row['id'],)).fetchone()
        if (not cur or dict(cur)!=dict(target) or not current or dict(current)!=dict(row)
                or store.now()-row['created']>=c['handoff']['reply_window_h']*3600
                or store.get(bid)!=c or store.lifecycle(bid)['removed']):raise BotError('stale')
        if manager.host.stopped() or cur['sends']>=3:raise BotError('email_limit')
        if code_mail:
            if cur['verified'] or cur['expires']>store.now():raise BotError('email_code_pending')
            store.db.execute('UPDATE bot_contacts SET code_hash=?,expires=?,tries=0 WHERE bot=? AND visitor=?',(hashlib.sha256(code.encode()).hexdigest(),store.now()+600,bid,vid))
        try:store.db.execute('INSERT INTO bot_mail_sends VALUES(?,?,?,?)',(bid,vid,req['send_id'],store.now()))
        except Exception:raise BotError('email_already_attempted') from None
        store.db.execute('UPDATE bot_contacts SET sends=sends+1 WHERE bot=? AND visitor=?',(bid,vid))
    if manager.host.stopped():raise BotError('stopped')
    try:await asyncio.to_thread(mail.send_many,cfg,password,msg,[target['email']])
    except Exception:raise BotError('mail_delivery_unknown') from None
    if not code_mail:
        store.db.execute("UPDATE handoffs SET status='replied' WHERE id=?",(row['id'],))
        store.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(bid,vid,row['request'],'assistant',text,store.now()))
    store.event(bid,vid,'human_mail','verification_sent' if code_mail else 'reply_sent')
    return {'sent':True,'verification':code_mail}
