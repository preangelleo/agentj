"""Host-owned ordinary approval display context; never a source of authority."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import secrets
import time
import unicodedata
CONTEXT = 'agentjarvis/approval/v2'

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def safe_text(value, limit=6000):
    from . import privacy
    s = str(value or '')[:24000]
    s = ''.join(c for c in s if c in '\n\t' or unicodedata.category(c) not in ('Cc', 'Cf', 'Cs'))
    # Consume the complete quoted value first, including short words/spaces.
    # This also closes a partially masked quoted value without preserving a tail.
    labels = r"(?:password|passwd|api[_-]?key|access[_-]?token|secret|token|密码|密钥|令牌)"
    quoted = r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
    s = re.sub(r"(?i)(" + labels + r"[\"']?\s*[:=]\s*)" + quoted, r"\1<redacted>", s)
    s = re.sub(r"(?i)(--(?:password|passwd|token|secret|api-key)(?:=|\s+))" + quoted, r"\1<redacted>", s)
    s = privacy.owner_text(s, mask_access_codes=True)
    # Even a short credential must not survive merely because entropy/length heuristics do not recognize it.
    s = re.sub(r'(?i)((?:password|passwd|api[_-]?key|access[_-]?token|secret|token|密码|密钥|令牌)[\"\']?\s*[:=]\s*[\"\']?)(?!\$|<redacted>)([^\s\"\',;}]+)', r'\1<redacted>', s)
    s = re.sub(r'(?i)(--(?:password|passwd|token|secret|api-key)(?:=|\s+))(?!\$|<redacted>)([^\s\"\']+)', r'\1<redacted>', s)
    s = re.sub(r'(?i)(Authorization\s*:\s*(?:Bearer|Basic)\s+)(?!\$|<redacted>)[^\s\"\']+', r'\1<redacted>', s)
    return s if len(s) <= limit else s[:max(0,limit-14)] + '\n[truncated]'


def snapshot(host):
    """Resolve only host-held references. Caller text and requested message ids are never read."""
    from . import provenance
    scope=provenance.task_scope()
    if scope is not None:
        ref=scope.get('approval_task_ref')
        record=host.__dict__.get('_approval_task_refs',{}).get(ref) if scope.get('live') else None
        origin=record.get('origin') if record else None
        if origin:
            result=copy.deepcopy(origin)
            result['task_record']=record['task_record']
            result['chain']=[*result.get('chain',[]),{'kind':'scheduled_task','id':str(ref)}]
            return result
        return {'message_id':None,'root_message_id':None,'session':str(getattr(getattr(host,'hist',None),'epoch','')),
                'channel':'unknown','text':'','verified':False,'suspicious':True,'chain':[],
                'initiator':'定时任务','task_record':record['task_record'] if record else 'scheduled-task:historical-owner-unavailable'}
    agent = getattr(host, 'agent', None)
    active = provenance._REQUEST.get()
    if type(active) is provenance.SameHost:
        peer = host.__dict__.get('_approval_launch_refs', {}).get(active.get('unit'))
        return copy.deepcopy(peer) if peer else {}
    if active is not provenance._UNSET:
        # An independent request with no registered host origin cannot inherit the main thread's owner.
        return {}
    live = provenance.turn_source(agent)
    if type(live) is provenance.SameHost:
        peer = host.__dict__.get('_approval_launch_refs', {}).get(live.get('unit'))
        return copy.deepcopy(peer) if peer else {}
    send = getattr(agent, 'cur_send', None)
    cfg = getattr(agent, 'cfg', None) or {}
    shared = type(agent).__name__.startswith('Shared') or cfg.get('session_mode') == 'shared'
    ref = getattr(send, 'approval_origin', None) if not shared or (getattr(agent, 'phone_turn', False) and getattr(agent, 'phone_seen', False)) else None
    if ref:
        return copy.deepcopy(ref)
    # A desktop turn id is host-generated from a verified native transcript, not an agent field.
    tid = getattr(host, 'desktop_turn', None) if getattr(agent, 'phone_turn', False) is False else None
    refs = host.__dict__.get('_approval_origins', {})
    return copy.deepcopy(refs.get(tid))


def remember(host, turn, source):
    from . import provenance
    src = turn.get('src') or {}
    kind = 'terminal' if src.get('k') == 'host' else src.get('k', 'unknown')
    verified = provenance.is_owner(source) or provenance.owner_channel(source) is not None
    # External material remains suspicious even when carried by an owner-authenticated channel.
    suspicious = not verified
    entry = {'host_received_monotonic': str(time.monotonic()), 'message_id': turn['id'], 'root_message_id': turn['id'], 'session': str(host.hist.epoch),
             'channel': kind, 'time': turn['ts'], 'sender': safe_text(src.get('name') or '你', 80),
             'text': safe_text(src.get('text'), 1200), 'verified': verified, 'suspicious': suspicious,
             'history_outside_window': False, 'initiator': '主 Agent J', 'task_record': f'message:{host.hist.epoch}:{turn["id"]}',
             'chain': [{'kind':kind,'id':str(turn['id'])},{'kind':'main','id':'main'}]}
    quote = src.get('quote') or {}
    trigger = host.hist.get(quote.get('id')) if isinstance(quote, dict) and type(quote.get('id')) is int else None
    if trigger and (trigger.get('src') or {}).get('k') in ('email', 'wechat', 'telegram', 'friend_report', 'support'):
        material = trigger['src']
        entry['root'] = {'message_id': turn['id'], 'session': str(host.hist.epoch), 'channel': kind,
                         'text': entry['text'], 'verified': verified}
        entry.update(message_id=trigger['id'], channel=material['k'], sender=safe_text(material.get('name') or material['k'], 80),
                     text=safe_text(material.get('text'), 1200), time=trigger['ts'], suspicious=True)
        entry['trigger_message_id'] = trigger['id']
    else:
        entry['trigger_message_id'] = turn['id']
    refs = host.__dict__.setdefault('_approval_origins', {})
    refs[turn['id']] = copy.deepcopy(entry)
    # Independent from the 500-turn UI window, bounded to prevent untrusted memory growth.
    while len(refs) > 2048:
        refs.pop(next(iter(refs)))
    return entry



def bind(host, ask):
    source = snapshot(host) or {'channel':'unknown','text':'','root':{'text':''},
        'verified':False,'suspicious':True,'chain':[],'history_outside_window':True}
    source = copy.deepcopy(source)
    # The retained ledger is independent of the UI window. Never infer an owner
    # or older root from the current tool input, prompt file, or same-name unit.
    if 'root' not in source:
        source['root'] = {'text':source.get('text','') if source.get('verified') else '',
                          'message_id':source.get('root_message_id'),'channel':source.get('channel')}
    ask.envelope = {'v':2,'channel':host.channel,'request_id':ask.rid,'revision':1,
        'nonce':secrets.token_hex(16),'source':source,'details':ask.summary,
        'action':safe_text(ask.reason or ask.goal_what or ask.why or ask.tool,240),
        'action_en':safe_text(ask.why_en or ask.tool,240),'input_sha256':ask.input_sha,
        'controls':{'batch':ask.scope_text,'allow_offer':list(ask.allow_offer),
                    'goal_add':ask.goal_add['text'] if ask.goal_add else None}}
    ask.envelope_digest = digest(ask.envelope)


def launch(host, unit):
    source = snapshot(host)
    if not source:
        return
    refs=host.__dict__.setdefault('_approval_launch_refs',{})
    source=copy.deepcopy(source)
    source['chain']=[*source.get('chain',[]),{'kind':'same_host','id':unit}][-16:]
    refs[unit]=source
    while len(refs)>512:
        refs.pop(next(iter(refs)))
