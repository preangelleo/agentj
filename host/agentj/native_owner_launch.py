"""Exact in-memory owner launch holder; display origins and system labels confer nothing."""
import copy
import contextlib
import contextvars
import asyncio
import time
from pathlib import Path
from . import provenance

_BOARD=contextvars.ContextVar('native_launch_trust',default=None)


class Launch:
    def __init__(self, agent, origin):
        from .busy_launch import CHAIN_TTL
        self.agent,self.host=agent,agent.host
        self.send=agent.cur_send
        self.source=copy.deepcopy(provenance.turn_source(agent))
        if (not provenance.is_owner(self.source) or provenance.untrusted_turn(agent)
                or not isinstance(origin,dict) or not origin.get('verified') or origin.get('suspicious')):
            raise ValueError('not original owner launch')
        if self.source.get('kind')=='phone' and not (agent.phone_turn and agent.phone_seen):raise ValueError('native owner delivery not observed')
        self.origin=copy.deepcopy(origin)
        self.terminal_id=getattr(self.host,'desktop_turn',None)
        if self.send is not None:
            if self.send.approval_origin!=origin:raise ValueError('different launch send')
        elif (self.source!=provenance.terminal() or origin.get('channel')!='terminal'
                or self.host.__dict__.get('_approval_origins',{}).get(self.terminal_id)!=origin):
            raise ValueError('no exact terminal holder')
        self.session={k:(agent.session or {}).get(k) for k in ('sessionId','pid','procStart','pidDomain','cwd')}
        self.transcript=Path(agent.transcript)
        self.state,self.epoch,self.estop=self.host.st,str(self.host.hist.epoch),self.host.estop
        self.identity=(self.host.channel,self.host.kp.pub)
        self.pairing_epoch=getattr(self.host,'_pairing_epoch',None)
        self.device=getattr(self.send,'device',None)
        self.key=self.state.sign_key(self.device) if self.device else None
        if self.device and not self.key:raise ValueError('launch key')
        self.expires=time.monotonic()+CHAIN_TTL
        if not self.live(agent):raise ValueError('launch identity')

    def live(self,agent):
        try:
            h=self.host
            original=(self.send is not None and self.send.state not in ('cancelled','failed')
                      and provenance.normalize(self.send.source)==self.source and self.send.approval_origin==self.origin
                      or self.send is None and h.__dict__.get('_approval_origins',{}).get(self.terminal_id)==self.origin)
            return (original and agent is self.agent and h.agent is agent and h.st is self.state and not h.stopped()
                and not h.stopping.is_set() and h.estop is self.estop and str(h.hist.epoch)==self.epoch
                and (h.channel,h.kp.pub)==self.identity and time.monotonic()<self.expires
                and getattr(h,'_pairing_epoch',None)==self.pairing_epoch and not getattr(h,'_unbinding_all',False)
                and {k:(agent.session or {}).get(k) for k in self.session}==self.session
                and Path(agent.transcript)==self.transcript
                and (not self.device or h.st.sign_key(self.device)==self.key))
        except (OSError,ValueError,TypeError,AttributeError):return False


def capture(agent,origin):
    try:return Launch(agent,origin)
    except (OSError,ValueError,TypeError,AttributeError):return None


def activate(agent, rows, binding, successful):
    agent._native_owner_launch=None
    if not successful or not rows or agent.__dict__.get('_owner_cancel_overflow'):return
    first=rows[0][0]
    if (type(first) is not Launch or any(type(cap) is not Launch or cap.send is not first.send
            or cap.origin!=first.origin or not cap.live(agent) for cap,_,_ in rows)):return
    agent._native_owner_launch=(binding,tuple(rows))


def current(agent):
    """Return only the exact consumed completion holder, never latest-owner display state."""
    value=getattr(agent,'_native_owner_launch',None)
    try:
        if (agent.__dict__.get('_owner_cancel_overflow') or not value or value[0] is not getattr(agent,'_native_approval_origin',None)
                or provenance.unattended_turn(agent)!='system'):return None
        board=agent.host.busy
        for cap,key,generation in value[1]:
            if (type(cap) is not Launch or not cap.live(agent) or board.launched.get(key) is not False
                    or board.native_starts.get(key,{}).get('generation')!=generation):return None
        return value
    except (AttributeError,TypeError,ValueError):return None


def _call(agent,event):
    import hashlib,json
    if (event.get('agent_id') or event.get('parent_tool_use_id') or event.get('isSidechain') or event.get('session_id')!=(agent.session or {}).get('sessionId')
            or not isinstance(event.get('tool_use_id'),str) or not 1<=len(event['tool_use_id'])<=256):return None
    identity=[event.get('tool_name'),event.get('tool_input'),event.get('cwd',agent.cfg.get('dir')),event.get('prompt_id'),
              {k:(agent.session or {}).get(k) for k in ('sessionId','pid','procStart','pidDomain','cwd')},str(agent.transcript)]
    try:digest=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    except (TypeError,ValueError):return None
    return ((agent.session['sessionId'],event['tool_use_id']),digest)


def before(agent,event,origin):
    call=_call(agent,event)
    if call is None:return
    key,digest=call
    seen=agent.__dict__.setdefault('_owner_background_calls',set())
    pending=agent.__dict__.setdefault('_owner_launch_pending',{})
    if key in seen or key in pending or len(seen)>=4096:return
    native=event.get('tool_name') in ('Bash','Agent','Task')
    pending[key]=(digest,{'origin':copy.deepcopy(origin) if native else None,
                         'owner_launch':capture(agent,origin) if native else None,
                         'trusted':native and (not provenance.untrusted_turn(agent) or provenance.unattended_turn(agent) in ('system','scheduled_task'))})
    while len(pending)>64:pending.pop(next(iter(pending)))


def after(agent,event,background):
    call=_call(agent,event)
    if call is None:return None
    key,digest=call
    row=agent.__dict__.setdefault('_owner_launch_pending',{}).pop(key,None)
    if background:
        seen=agent.__dict__.setdefault('_owner_background_calls',set())
        if len(seen)<4096:seen.add(key)
    return row[1] if row and row[0]==digest else None


@contextlib.contextmanager
def board_scope(agent,event):
    value=agent.__dict__.pop('_native_launch_decision',None)
    if value is None or value[0] is not event:
        yield;return
    try:task=asyncio.current_task()
    except RuntimeError:task=None
    token=_BOARD.set((agent,task,value[1]))
    try:yield
    finally:_BOARD.reset(token)


def board_trusted(agent):
    value=_BOARD.get()
    try:task=asyncio.current_task()
    except RuntimeError:task=None
    if value is not None and value[0] is agent and value[1] is task:return value[2]
    return not provenance.untrusted_turn(agent) or provenance.unattended_turn(agent) in ('system','scheduled_task')
