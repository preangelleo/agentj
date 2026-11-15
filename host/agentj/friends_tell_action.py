"""One UV-approved message to one existing friend; no shell or permission grant."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
import secrets
import shlex
import time
from . import application_ingress,approvals,peer_guard,wire

TOOL='FriendsTellOnce'
OP='friends_tell_once'
MID=re.compile(r'[0-9a-f]{16}\Z')
HINT=('这次原生沙盒拒绝仍然有效。若只需把原消息发给原好友，可明确重试 '
      '`agentj friends tell --sandbox-card <好友> <原消息>`；Host 将显示“仅这一次发送”专属卡并要求已配对设备验证。'
      '不会运行浏览器命令或扩大沙盒。 / The native sandbox refusal remains in force. For only the intended message '
      'to the intended friend, retry `agentj friends tell --sandbox-card <friend> <original-message>`; '
      'Host asks for paired verification on a dedicated Send once card. No browser command or sandbox widening.')


def sandbox_guidance(tool,inp):
    """Read-only closed command recognition; never execute or reconstruct body text."""
    if tool not in ('Bash','exec_command','shell_command') or not isinstance(inp,dict):return None
    command=inp.get('command',inp.get('cmd'))
    if not isinstance(command,str) or len(command)>16000:return None
    if any(char in command for char in ('$','`','\n','\r')):return None
    try:
        lexer=shlex.shlex(command,posix=True,punctuation_chars=';&|<>()')
        lexer.whitespace_split=True;args=list(lexer)
    except ValueError:return None
    if (len(args)>=5 and os.path.basename(args[0])=='agentj' and args[1:3]==['friends','tell']
            and not any(x in args for x in ('--sandbox-card','--actions','--grant',';','&&','||','|','&','<','>','(',')'))):
        return HINT
    return None


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def _binding(svc,fid):
    row=svc.store.get(fid)
    if row is None or svc.store.is_blocked(fid) or not svc.store.on:return None
    return _digest({k:row.get(k) for k in ('id','x','pk','mbox','group')})


def _passkey_binding(host,did):
    row=host.st.passkey_of_device(did)
    return {k:row.get(k) for k in ('id','alg','spki','rp','at')} if isinstance(row,dict) else None


async def _clean(svc,fid,text):
    host=svc.host
    def scan():
        index=peer_guard.SecretIndex.build(workdir=(host.agent_cfg or {}).get('dir'),fragment_size=8)
        index.require_complete()
        return peer_guard.inbound(text).ok and peer_guard.outbound(fid+'\n'+text,index,svc.store.never_tell_extra).ok
    try:return await asyncio.to_thread(scan)
    except (OSError,ValueError,TypeError):return False


def refused(why):
    return {'ok':False,'why':why,'message':'未发送 / Not sent: '+why}


async def handle(svc,req,*,_native=None):
    host=svc.host
    frame=application_ingress.current(host)
    native=None
    if _native is not None:
        from .friends_tell_native import _Intent
        if type(_native) is not _Intent or not _native.claim(svc,req):return refused('source_refused')
        native=_native
    if (frame is None and native is None) or host.stopped() or host.stopping.is_set():return refused('source_refused')
    if not isinstance(req,dict) or set(req)-{'t','friend','text','reply_to'} or req.get('t')!='tell-card':
        return refused('shape')
    text,reply=req.get('text'),req.get('reply_to')
    if (not isinstance(text,str) or not text.strip() or len(text.encode())>6000 or wire.text_problem(text,6000) is not None
            or (reply is not None and (not isinstance(reply,str) or not MID.fullmatch(reply)))):
        return refused('shape')
    fid,why=svc._resolve(req.get('friend'))
    if fid is None:return refused('target_refused')
    name=svc.store.name_of(fid)
    binding=_binding(svc,fid)
    if binding is None or svc.seat_refusal():return refused('target_refused')
    agent=host.agent;send=getattr(agent,'cur_send',None);epoch=str(host.hist.epoch)
    host._task_id();authority=host._approval_live_authority()
    signers={did:(host.st.sign_key(did),_passkey_binding(host,did)) for did in host.st.devices()}
    def intent():
        return (((native.live(host) if native else application_ingress.current(host) is frame and frame._transport_live())) and host.agent is agent
                and getattr(agent,'cur_send',None) is send and str(host.hist.epoch)==epoch
                and host._approval_live_authority()==authority and not host.stopped() and not host.stopping.is_set()
                and _binding(svc,fid)==binding and not svc.seat_refusal())
    if not await _clean(svc,fid+'\n'+name,text) or not intent():return refused('message_or_source_refused')
    payload={'schema':1,'operation':OP,'scope':'one_message_one_friend','friend_id':fid,'friend_name':name,'text':text,
             'reply_to':reply,'friend_binding_sha256':binding,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
             'request_nonce':secrets.token_hex(16),'policy':'no_shell_no_permission_change',
             'description':'仅这一次向这个好友发送这条消息 / Send this message to this friend once',
             'effect':'只发上面的固定消息；不执行命令、不扩大原生沙盒。 / Send only this exact message; no command execution or native sandbox widening.'}
    answer=await host.ask(TOOL,payload,batch=False,grant_info=True,require_passkey=True,always_ask=True,
                          ttl=min(120,host.ask_ttl),gone=native.gone if native else None,
                          _native_context=native.context if native else None)
    proof=answer.get('grant_info') or {};did=proof.get('device');pub=proof.get('sign_pub')
    if answer.get('behavior')!='allow' or not proof.get('rid') or not did or not pub or proof.get('uv') is not True or proof.get('approval_v')!=2:
        return refused('no_signed_uv')
    signer=signers.get(did)
    records=await asyncio.to_thread(lambda:[r for r in approvals.read_log(host.st) if r.get('id')==proof['rid']])
    valid=(len(records)==1 and records[0].get('tool')==TOOL and records[0].get('device')==did
           and records[0].get('decision')=='allow' and records[0].get('reason')=='device'
           and records[0].get('input_sha256')==approvals.input_digest(payload)
           and approvals.check_record(host.st,records[0])=='ok')
    until=time.monotonic()+min(30,host.ask_ttl)
    envelope=proof.get('envelope') or {}
    def live():
        return (valid and signer is not None and signer[0]==pub and host.st.sign_key(did)==pub
                and _passkey_binding(host,did)==signer[1] and time.monotonic()<=until
                and envelope.get('expires_at',0)>=time.time() and intent())
    async def authorized():
        return live() and await _clean(svc,fid+'\n'+name,text) and live()
    if not await authorized():return refused('authority_changed')
    try:
        # Host-only callback; never accepted from the CLI/wire. Existing peer
        # routing/outbound/reply-latch rules still decide the actual effect.
        result=await svc.tell(fid,text,reply_to=reply,_authorized=authorized)
    except Exception:
        host.st.log('friends_tell_once',result='unknown')
        return refused('outcome_unknown_do_not_retry')
    host.st.log('friends_tell_once',result='sent' if result.get('ok') else 'refused')
    return ({'ok':True,'mid':result.get('mid'),'route':result.get('route'),'message':'已向固定好友发送一次 / Sent once to the fixed friend'}
            if result.get('ok') else refused('send_refused'))
