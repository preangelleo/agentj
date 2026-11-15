"""Closed owner app intent from a live native request; the native command stays denied."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
import os
import secrets
import shlex
import time
from . import codex_children,provenance
from .core import process_evidence
from .list_approval import NativeContext
from .friends_tell_action import MID,_digest

MAX_OPEN=32


def _argv(command):
    if (not isinstance(command,str) or len(command)>16000
            or any(c in command for c in ('$','`','\n','\r','\x00'))):return None
    try:
        lexer=shlex.shlex(command,posix=True,punctuation_chars=';&|<>()')
        lexer.whitespace_split=True
        return list(lexer)
    except ValueError:return None


def parse(inp,cwd):
    """No shell interpretation, expansions, pipelines, plan, grant or arbitrary command."""
    if not isinstance(inp,dict):return None
    args=_argv(inp.get('command'))
    if not args:return None
    if len(args)>=4 and args[0]=='cd' and args[2]=='&&':
        if not os.path.isabs(args[1]) or os.path.realpath(args[1])!=os.path.realpath(cwd):return None
        args=args[3:]
    if any(x in args for x in (';','&&','||','|','&','<','>','(',')')):return None
    if len(args)<6 or os.path.basename(args[0])!='agentj' or args[1]!='friends':return None
    args=args[2:]
    if args and args[0]=='--json':args=args[1:]
    if not args or args[0]!='tell':return None
    args=args[1:]
    if args.count('--sandbox-card')!=1:return None
    args.remove('--sandbox-card')
    if args.count('--json')>1:return None
    if '--json' in args:args.remove('--json')
    reply=None
    if args.count('--reply-to')>1:return None
    if '--reply-to' in args:
        index=args.index('--reply-to')
        if index+1>=len(args) or not MID.fullmatch(args[index+1]):return None
        reply=args[index+1];args=args[:index]+args[index+2:]
    if len(args)<2 or any(x.startswith('-') for x in args):return None
    text=' '.join(args[1:])
    if not text.strip() or len(text.encode())>6000:return None
    return {'t':'tell-card','friend':args[0],'text':text,**({'reply_to':reply} if reply else {})}


def _owner(adapter):
    cfg=adapter.cfg
    return (not adapter.research and adapter.persist and adapter.owner_thread and not adapter.halting
            and not cfg.get('_workflow_ceo') and cfg.get('mode')!='no_tools' and not cfg.get('no_tools')
            and not codex_children.is_child(adapter) and not getattr(adapter,'untrusted_foreign',False)
            and provenance.owner_intent(provenance.turn_source(adapter),getattr(adapter,'cur_send',None))
            and getattr(getattr(adapter,'cur_send',None),'state',None) not in ('cancelled','failed','uncertain'))


@dataclass(frozen=True,eq=False)
class _Intent:
    adapter: object
    context: NativeContext
    rid: object
    gone: object
    request: tuple
    nonce: str
    until: float
    send: object
    turn: str
    epoch: str
    authority: str
    source: str
    policy: str
    process_row: object
    used: bool=False

    def payload(self):
        friend,text,reply=self.request
        return {'t':'tell-card','friend':friend,'text':text,**({'reply_to':reply} if reply else {})}

    def live(self,host):
        a=self.adapter;p=self.context.process
        try:
            return (host is a.host and host.agent is a and self.context.live(host) and p is not None
                    and p.returncode is None and process_evidence.row(p.pid)==self.process_row
                    and a.pending.get(self.rid) is self.gone and not self.gone.done()
                    and time.monotonic()<self.until and a.cur_send is self.send and a.turn_id==self.turn
                    and str(host.hist.epoch)==self.epoch and host._approval_live_authority()==self.authority
                    and _digest(provenance.turn_source(a))==self.source and _digest(a.thread)==self.policy
                    and _owner(a) and not host.stopped() and not host.stopping.is_set()
                    and host.__dict__.get('_friends_native_intents',{}).get(self.nonce) is self)
        except (OSError,ValueError,TypeError,AttributeError):return False

    def claim(self,svc,req):
        if self.used or svc.host is not self.adapter.host or req!=self.payload() or not self.live(svc.host):return False
        object.__setattr__(self,'used',True)
        return True


def _capture(adapter,rid,req,native):
    from .agent_codex import CodexAgent
    if (type(adapter) is not CodexAgent or adapter.host.agent is not adapter or not _owner(adapter)
            or not isinstance(native,dict) or native.get('threadId')!=adapter.tid or native.get('turnId')!=adapter.turn_id
            or not adapter.tid or not adapter.turn_id or native.get('kind')=='writeStdin'
            or not isinstance(native.get('cwd'),str) or not os.path.isabs(native['cwd'])
            or os.path.realpath(native['cwd'])!=os.path.realpath(adapter.cfg['dir'])):
        raise ValueError('native_intent_unavailable')
    p=adapter.proc
    if p is None or p.returncode is not None or type(p.pid) is not int:raise ValueError('native_intent_unavailable')
    row=process_evidence.row(p.pid)
    if row is None or rid in adapter.pending:raise ValueError('native_intent_unavailable')
    if not (isinstance(rid,str) and 0<len(rid)<=100 or type(rid) is int):raise ValueError('native_intent_unavailable')
    host=adapter.host;host._task_id()
    book=host.__dict__.setdefault('_friends_native_intents',{})
    for key,old in list(book.items()):
        if not old.live(host):book.pop(key,None)
    if len(book)>=MAX_OPEN:raise ValueError('native_intent_unavailable')
    gone=asyncio.get_running_loop().create_future();adapter.pending[rid]=gone
    cap=_Intent(adapter,NativeContext.capture(adapter),rid,gone,(req['friend'],req['text'],req.get('reply_to')),
                secrets.token_hex(16),time.monotonic()+min(120,host.ask_ttl),adapter.cur_send,adapter.turn_id,
                str(host.hist.epoch),host._approval_live_authority(),_digest(provenance.turn_source(adapter)),
                _digest(adapter.thread),row)
    book[cap.nonce]=cap
    return cap


async def _run(cap,no):
    from . import friends_tell_action
    a=cap.adapter;host=a.host
    try:
        result=await friends_tell_action.handle(host.peers,cap.payload(),_native=cap)
        if result.get('ok'):
            a.local_only('固定好友消息已发送；原生命令仍被拒绝，不要重复发送。 / The fixed friend message was sent; the native command stays declined. Do not send it again.')
        elif result.get('why')=='outcome_unknown_do_not_retry':
            a.local_only('发送结果未确认；原生命令仍被拒绝。先核对好友历史，不要自动重发。 / Send outcome unknown; native command declined. Check friend history; do not retry automatically.')
        else:
            a.local_only('固定好友消息未发送；原生命令仍被拒绝。 / The fixed friend message was not sent; native command declined.')
    except Exception:
        # Never log exception text: a local path or transport error may contain secrets.
        # An uncertain send must not be repeated automatically.
        a.local_only('固定发送结果未确认；先核对好友历史，不要自动重发。 / Fixed send outcome unknown; check friend history and do not retry automatically.')
    finally:
        host.__dict__.get('_friends_native_intents',{}).pop(cap.nonce,None)
        if a.pending.get(cap.rid) is cap.gone:a.pending.pop(cap.rid,None)
        a._answer(cap.rid,no)


def offer(adapter,rid,tool,inp,no,native):
    """Called only on the native RPC refusal path; never accept the original Bash."""
    if tool!='Bash' or not isinstance(inp,dict) or '--sandbox-card' not in str(inp.get('command','')):return False
    req=parse(inp,adapter.cfg['dir'])
    try:
        if req is None or native is None:raise ValueError('native_intent_unavailable')
        cap=_capture(adapter,rid,req,native)
    except (OSError,ValueError,TypeError,AttributeError):
        # A duplicate server request also revokes the earlier pending capability.
        old=adapter.pending.get(rid)
        if old is not None and not old.done():old.set_result(None)
        adapter._answer(rid,no)
        adapter.local_only('固定发送请求的来源或格式无法核验，未发送；原生命令仍被拒绝。 / Cannot verify the fixed send request or its source; nothing sent, native command declined.')
        return True
    adapter._bg(_run(cap,no))
    return True
