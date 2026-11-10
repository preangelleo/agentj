"""Pure-data user preferences. Schema is the catalogue; hidden State remains human-only.
Writes are serialized and atomic, bounded history stores overrides, and serve acknowledges
before a CLI reports an applied change. No credentials in this layer. Local TTS argv is the explicit host-only execution seam.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import json5
from .state import State, _write_private
from .preference_values import ConfigError, get, put
from .preference_data import (
    DATA, SCHEMA, MAX_BYTES, flatten, merge, _PARSED, _cached, defaults,
    _defaults, _line, _dotted, _undot, ignored, COMPOSER_BUTTONS,
    composer_order_ok, TOKEN, spans, edit, template, decode,
)
from .preference_checks import rows_problem as notify_problem, asr_endpoint


def path():
    from .instance import current   # P180: instance N ≥ 2 keeps its own preferences in agentj-N/
    n = current()
    return guard(Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/('agentj' if n == 1 else f'agentj-{n}')/'config.json5')

def under_test():
    # agentj itself never imports unittest / pytest: either loaded means a test runner (`-m unittest`, the gates' `-c "import unittest…"`)
    return os.environ.get('AGENTJ_TEST') == '1' or 'pytest' in sys.modules or 'unittest.runner' in sys.modules

def real_home():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError):   # Windows / no passwd entry: the profile dir
        return os.environ.get('USERPROFILE') or os.path.expanduser('~')

def guard(p):
    """P316: a test process never resolves the developer's real ~/.config/agentj* (the passwd home, not $HOME): some desktops export
    XDG_CONFIG_HOME=~/.config, so `HOME=$tmp python -m unittest` still rewrote the owner's config.json5 with /tmp working roots."""
    if under_test():
        real = os.path.realpath(os.path.join(real_home(), '.config'))
        try: rel = os.path.relpath(os.path.realpath(p), real)
        except ValueError: return p   # another Windows drive
        if rel.split(os.sep)[0].startswith('agentj'):
            raise RuntimeError(f'test process refused the real {real}/{rel}: import tests/_hermetic first or set XDG_CONFIG_HOME to a temp dir')
    return p

def remote_servers_ok(rows):
    """Display labels only: no remote command, login, credential or path settings."""
    from .privacy import redact
    if not isinstance(rows, list) or len(rows) > 32:
        return False
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'host', 'name'}:
            return False
        host, name = row['host'], row['name']
        if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}', host) or host in seen:
            return False
        if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 40 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            return False
        if redact(name) != name:
            return False
        seen.add(host)
    return True

def validate(doc,raw=''):
    if not isinstance(doc,dict): raise ConfigError('/', 'expected an object')
    dotted=_dotted(doc)
    if dotted: raise ConfigError(dotted,'a key may not contain "."; nest it instead: {"agent": {"allow_docker": false}}',_line(raw,dotted))
    for k,v in flatten(doc):
        m=SCHEMA.get(k); line=_line(raw,k)
        if not m: raise ConfigError(k,'unknown key; use agentj config keys',line)
        if m['tier']!='user' and k!='version':
            raise ConfigError(k,'security floor is locked' if m['tier']=='locked' else 'human-only; use the existing human command',line,2 if m['tier']=='human' else 1)
        if k=='version' and v!=1: raise ConfigError(k,'supported version: 1',line)
        typ=m['type']
        valid={'boolean':type(v) is bool,'integer':type(v) is int,'number':type(v) in (int,float) and math.isfinite(v),'string':isinstance(v,str),'array':isinstance(v,list),'object':isinstance(v,dict)}.get(typ,False)
        if not valid: raise ConfigError(k,'expected '+typ,line)
        if 'enum' in m and v not in m['enum']: raise ConfigError(k,'allowed: '+', '.join(map(str,m['enum'])),line)
        if isinstance(v,str):
            if any(ord(c)<32 and not (k in ('agent.instructions','imessage.instructions') and c in '\n\t') for c in v) or len(v)>m.get('maxLength',1000): raise ConfigError(k,'invalid characters or length',line)
            if m.get('pattern') and not re.fullmatch(m['pattern'],v): raise ConfigError(k,'expected '+m['patternHint'] if m.get('patternHint') else 'expected environment variable NAME, never a secret',line)
        if typ in ('integer','number') and not m.get('minimum',-math.inf)<=v<=m.get('maximum',math.inf): raise ConfigError(k,f"allowed range: {m.get('minimum')}..{m.get('maximum')}",line)
        if typ=='number' and m.get('multipleOf') and abs(v/m['multipleOf']-round(v/m['multipleOf']))>1e-6: raise ConfigError(k,f"steps of {m['multipleOf']}",line)
    for lk in ('imessage.vip','imessage.mute'):   # P274: numbers / e-mails / keywords, matched exactly; never names guessed
        lv=get(doc,lk,[])
        if len(lv)>200 or any(not isinstance(x,str) or not x.strip() or len(x)>100 or any(ord(c)<32 for c in x) for x in lv): raise ConfigError(lk,'a list of up to 200 numbers, e-mails or keywords (each 1–100 characters)')
    if not remote_servers_ok(get(doc, 'ui.remote_servers', [])):
        raise ConfigError('ui.remote_servers', 'up to 32 unique SSH host names and display labels (1–40 characters); no credentials or paths')
    why = notify_problem(get(doc, 'notify.automations', []))
    if why: raise ConfigError('notify.automations', why, _line(raw, 'notify.automations'))
    cats = get(doc, 'updates.skill_categories', [])
    if len(cats)>4 or any(c not in ('content','app','commerce','general') for c in cats): raise ConfigError('updates.skill_categories','allowed: content, app, commerce, general')
    order = get(doc, 'ui.composer_buttons')   # P340: a permutation of the five ids (or the complete legacy four); anything else is refused (the phone keeps the default)
    if order is not None and not composer_order_ok(order): raise ConfigError('ui.composer_buttons','expected each of '+', '.join(COMPOSER_BUTTONS)+' exactly once, e.g. ["mic","file","image","camera","full"]',_line(raw,'ui.composer_buttons'))
    xf = get(doc, 'api_keys.extra_env_files', [])   # P362: paths only; keys_found reads each one safely, names + tails only
    if len(xf)>8 or any(not isinstance(x,str) or not (x.startswith('/') or x.startswith('~/')) or len(x)>1024 or any(ord(c)<32 for c in x) for x in xf):
        raise ConfigError('api_keys.extra_env_files','up to 8 local file paths, each absolute or ~/path, e.g. ["~/projects/.env"]',_line(raw,'api_keys.extra_env_files'))
    pif = get(doc, 'agent.private_instructions_file', '')
    if pif and not (pif.startswith('/') or pif.startswith('~/')):
        raise ConfigError('agent.private_instructions_file', 'use an absolute path or ~/path (a local file, never a URL)')
    root = get(doc, 'agent.working_root', '')
    if root and not (os.path.isabs(root) or root == '~' or root.startswith('~/')):
        raise ConfigError('agent.working_root', 'use an absolute path or ~/path')
    out=merge(defaults(),{k:v for k,v in doc.items() if k!='version'})
    if order is not None and len(order) == 4: put(out, 'ui.composer_buttons', [*order, 'full'])
    # Defaults belong to the selected provider; explicit model/key choices always win.
    if get(out,'voice.asr.mode')=='custom' and get(doc,'voice.asr.key_env') is None:
        put(out,'voice.asr.key_env','')
    provider=get(out,'voice.tts.provider')
    for key in ('voice.tts.model','voice.tts.key_env'):
        if get(doc,key) is None:
            put(out,key,SCHEMA[key].get('providerDefaults',{}).get(provider,SCHEMA[key]['default']))
    command=get(out,'voice.tts.command')
    if len(command)>32 or any(not isinstance(a,str) or not a or len(a)>1000 or any(ord(c)<32 for c in a) for a in command):
        raise ConfigError('voice.tts.command','expected at most 32 non-empty argv strings; no inline credentials')
    if command and not any('{output}' in a for a in command[1:]):
        raise ConfigError('voice.tts.command','include {output} in an output argument; text arrives on stdin')
    if get(out,'voice.tts.mode')=='cloud' and get(out,'voice.tts.provider')=='command':
        raise ConfigError('voice.tts.mode','local command uses host mode')
    for k in ('menu.items','keyboard.bindings','channels.items'):
        rows=get(doc,k,[]); ids=set()
        if len(rows)>60: raise ConfigError(k,'maximum 60 entries',_line(raw,k))
        allowed={'menu.items':{'id','cmd','desc','group','order','disabled'},'keyboard.bindings':{'id','combo','action','disabled'},'channels.items':{'id','type','disabled'}}[k]
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}',row['id']) or row['id'] in ids: raise ConfigError(k,'each entry needs a unique plain id',_line(raw,k))
            ids.add(row['id'])
            if set(row)-allowed: raise ConfigError(k,'unknown entry field; allowed: '+', '.join(sorted(allowed)),_line(raw,k))
            if 'disabled' in row and type(row['disabled']) is not bool: raise ConfigError(k,'disabled must be boolean',_line(raw,k))
        effective=get(out,k)
        if k=='menu.items':
            from .menu import validate as menu_validate
            errors=menu_validate({'version':1,'items':[{a:b for a,b in x.items() if a!='id'} for x in effective]})
            if errors: raise ConfigError(k,'; '.join(errors),_line(raw,k))
        if k=='keyboard.bindings':
            combos=set()
            for x in effective:
                if not isinstance(x.get('combo'),str) or not re.fullmatch(r'(?:ctrl\+|alt\+|shift\+|meta\+)*[a-z0-9]{1,16}',x['combo']): raise ConfigError(k,'combo example: ctrl+shift+j')
                if x['combo'] in combos: raise ConfigError(k,'shortcut conflicts with another binding')
                if x.get('action') not in ('open-menu','read-reply','stop-speech','focus-input','interrupt'): raise ConfigError(k,'allowed actions: open-menu, read-reply, stop-speech, focus-input, interrupt')
                combos.add(x['combo'])
        if k=='channels.items':
            for x in effective:
                if x.get('type') not in ('phone','telegram'): raise ConfigError(k,'supported channels: phone, telegram')
    groups = get(out, 'telegram.groups', [])
    ids = set()
    if len(groups) > 20: raise ConfigError('telegram.groups', 'maximum 20 groups')
    for row in groups:
        if (not isinstance(row, dict) or set(row) - {'id','members','profile','label','names'}
                or not isinstance(row.get('id'), str) or not re.fullmatch(r'-[1-9][0-9]{0,18}', row['id'])
                or row['id'] in ids):
            raise ConfigError('telegram.groups', 'unique negative chat id string required')
        ids.add(row['id'])
        members = row.get('members', [])
        if (not isinstance(members, list) or not members or len(members) > 100
                or any(type(uid) is not int or uid <= 0 for uid in members)):
            raise ConfigError('telegram.groups', 'explicit positive numeric sender allowlist required')
        if row.get('profile','proxy') not in ('proxy','family'):
            raise ConfigError('telegram.groups', 'profiles: proxy, family')
        label = row.get('label','Telegram group')
        if not isinstance(label,str) or len(label)>64 or any(ord(c)<32 for c in label):
            raise ConfigError('telegram.groups', 'plain label up to 64 characters required')
        names = row.get('names', {})   # P118 (B2): how each allowlisted sender is called; only listed numeric IDs
        if (not isinstance(names, dict) or len(names) > 100
                or any(not re.fullmatch(r'[1-9][0-9]{0,18}', k) or int(k) not in members for k in names)
                or any(not isinstance(v, str) or not v.strip() or len(v) > 32 or any(ord(c) < 32 for c in v) for v in names.values())):
            raise ConfigError('telegram.groups', 'names: {"<allowlisted numeric sender id>": "plain name up to 32 characters"}')
    domains = get(out, 'telegram.private_domains', [])
    if len(domains) > 50 or any(not isinstance(d, str) or len(d) > 253
                                or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}', d) for d in domains):
        raise ConfigError('telegram.private_domains', 'up to 50 lowercase domain names like example.com')
    from .tg_owner_cmds import problem as owner_cmd_problem
    why = owner_cmd_problem(get(out, 'telegram.owner_commands', []))
    if why: raise ConfigError('telegram.owner_commands', why)
    from .proxy import url_problem, bypass_problem   # P59: direct proxy values; never echo the value (it may hold a password)
    for field in ('https','http'):
        why=url_problem(field,get(out,'proxy.'+field,''))
        if why: raise ConfigError('proxy.'+field,why,_line(raw,'proxy.'+field))
    why=bypass_problem(get(out,'proxy.no_proxy',''))
    if why: raise ConfigError('proxy.no_proxy',why,_line(raw,'proxy.no_proxy'))
    # #440: only an endpoint and a key NAME enter preferences, never authentication material.
    endpoint=get(out,'voice.asr.base_url','')
    if endpoint or get(out,'voice.asr.mode')=='custom': asr_endpoint(endpoint)
    if get(out,'voice.asr.mode') in ('custom','cloud') and not get(out,'voice.asr.model'):
        raise ConfigError('voice.asr.model','remote ASR requires a model ID')
    hotwords=get(out,'voice.asr.hotwords',[])
    if len(hotwords)>100 or any(not isinstance(x,str) or not x.strip() or len(x)>80 or any(ord(c)<32 for c in x) for x in hotwords):
        raise ConfigError('voice.asr.hotwords','up to 100 plain words, each 1–80 characters')
    corrections=get(out,'voice.asr.corrections',{})
    if len(corrections)>100 or any(not isinstance(x,str) or not x.strip() or len(x)>80 or any(ord(c)<32 for c in x) for pair in corrections.items() for x in pair):
        raise ConfigError('voice.asr.corrections','up to 100 plain wrong-word to correct-word pairs')
    if get(out,'voice.asr.mode')=='cloud' and not get(out,'voice.asr.key_env'):
        raise ConfigError('voice.asr.key_env','cloud ASR requires a key variable NAME')
    if get(out,'voice.asr.mode')=='cloud' and not get(out,'voice.asr.model'): raise ConfigError('voice.asr.model','cloud ASR requires an audio-capable model ID')
    if get(out,'voice.tts.mode')=='cloud' and not get(out,'voice.tts.voice'): raise ConfigError('voice.tts.voice','cloud TTS requires a provider voice ID')
    if len(json.dumps(out).encode()) > 12000: raise ConfigError("/", "effective configuration must be at most 12 KiB")
    return out

def parse(raw):
    doc=decode(raw)
    validate(doc,raw)
    return doc

def read():
    try: raw=path().read_text()
    except FileNotFoundError: raw='// Agent J user overrides; factory defaults update independently.\n{version: 1}\n'
    return raw,_cached('parse',raw,parse)   # P331: parse once per distinct text

def effective(st=None):
    _,doc=read(); out=validate(doc)
    if not get(out,'voice.wake_word'): put(out,'voice.wake_word','嘿 '+((st or State()).agent_name() or 'Agent J'))
    return out

def ensure():
    p=path(); p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(p.parent,0o700)
    if not p.exists():
        raw=template()
        st=State()
        if st.exists() and st.config().get('asr',{}).get('engine') in ('voxtype','off'):
            raw=edit(raw,'voice.asr.engine',st.config()['asr']['engine'])
        _write_private(p,raw.encode())
    return p

def runtime(st): return st.root/'runtime/last-good.json'
def save_good(st,doc):
    runtime(st).parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(runtime(st).parent,0o700)
    _write_private(runtime(st),json.dumps(doc,ensure_ascii=False).encode())

def history(st,old):
    d=st.root/'config-history'; d.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(d,0o700)
    target=d/(str(time.time_ns())+'.json5');_write_private(target,old.encode());return target

def commit_files(st,newraw,candidate,*,signed_key=None):
    """All durable writes precede in-memory activation. Restore both files on any failure."""
    ensure(); old=path().read_bytes(); last=runtime(st)
    oldgood=last.read_bytes() if last.exists() else None
    snapshot=history(st,old.decode())  # failure here cannot expose a new candidate
    try:
        save_good(st,candidate)
        _write_private(path(),newraw.encode())
    except OSError:
        _write_private(path(),old)
        if oldgood is not None: _write_private(last,oldgood)
        elif last.exists(): last.unlink()
        snapshot.unlink(missing_ok=True)
        raise
    for previous in sorted(snapshot.parent.glob("*.json5"))[:-20]:
        try:previous.unlink()
        except OSError:st.log("config_history_cleanup_failed")
    if signed_key != "jev.mode" and get(validate(parse(old.decode())), "jev.mode") != get(candidate, "jev.mode"):
        from . import approval_level
        approval_level.drop_signer(st, "jev.mode")
    note_language(st,candidate)
    from . import session_choice  # P127: a committed session-mode change is the owner's recorded choice
    session_choice.note_commit(st,old.decode(),newraw,candidate)


# ------------------------------------------------------------------ A1 (P44): the one language value and when it was set
# `appearance.language` is THE language (web UI, account mails, the language Agent J speaks with the owner). Its "last set"
# time is not a preference (the schema stays pure user data): it lives next to the host state as language.json (0600)
# {"language": "zh"|"en", "at": <ms>}. note_language() is called after every activation/commit of a configuration; a
# recorded language that differs from the effective one = a local change → stamped now. The account sync (cloud report,
# last write wins) passes the account's own `at`, so an applied account value is not re-sent as a newer host change.
LANG_FILE = 'language.json'

def _lang_rec(st):
    try:
        d = json.loads((st.root/LANG_FILE).read_text())
    except (OSError, ValueError):
        return None
    if isinstance(d,dict) and d.get('language') in ('zh','en') and type(d.get('at')) is int and d['at'] >= 0:
        return d
    return None

def _first_at():
    """No record yet (a host from before 0.15): a language the owner wrote into the file counts from the file's mtime; the
    factory default counts as never set (0), so an account value wins over it."""
    try:
        raw = path().read_text()
        doc = json5.loads(raw, allow_duplicate_keys=False)
        if get(doc,'appearance.language') in ('zh','en'):
            return int(path().stat().st_mtime*1000)
    except (OSError, ValueError):
        pass
    return 0

def language_state(st, effective_doc=None):
    """(language, language_at ms) of this host. Never raises."""
    rec = _lang_rec(st)
    if effective_doc is None:
        try: effective_doc = effective(st)
        except (ConfigError, OSError, ValueError): effective_doc = {}
    lang = get(effective_doc,'appearance.language') or (rec or {}).get('language') or SCHEMA['appearance.language']['default']
    if rec and rec['language'] == lang:
        return lang, rec['at']
    # no record yet, or the file was edited while serve was not running: record it now (idempotent from then on)
    note_language(st,{'appearance':{'language':lang}})
    rec = _lang_rec(st)
    if rec and rec['language'] == lang:
        return lang, rec['at']
    return lang, 0

def note_language(st, candidate, at=None):
    """Record when the effective language changed (see above). `at` = the account's time for a value taken from the
    account. Returns True when a new record was written."""
    lang = get(candidate,'appearance.language')
    if lang not in ('zh','en') or not st.root.is_dir():
        return False
    rec = _lang_rec(st)
    if rec and rec['language'] == lang and at is None:
        return False
    stamp = int(at) if at is not None else (_first_at() if rec is None else int(time.time()*1000))
    try:
        _write_private(st.root/LANG_FILE, json.dumps({'language':lang,'at':stamp}).encode())
    except OSError:
        return False
    return True


def set_language(st, lang, at):
    """Account → host (A1 sync): write `appearance.language` through the same transactional path as `agentj config set`
    (serve acknowledges it, or the file is committed when serve is not running), then stamp the account's time.
    Returns the transact() result, or None when nothing had to change."""
    if lang not in ('zh','en'):
        raise ConfigError('appearance.language','allowed: zh, en')
    st.root.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd = os.open(st.root/'preferences.lock',os.O_RDWR|os.O_CREAT,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        ensure(); raw,doc = read()
        if get(validate(doc),'appearance.language') == lang:
            note_language(st,{'appearance':{'language':lang}},at)
            return None
        result = transact(st, edit(raw,'appearance.language',lang))
    finally:
        os.close(fd)
    note_language(st,{'appearance':{'language':lang}},at)
    return result


def transact(st,newraw, *, _working_root_record=False):
    # Consume even a rejected attempt; no string/JSON flag can mint this receipt.
    from .working_root import _consume_initial_setup
    try:
        candidate=validate(parse(newraw))
    except Exception:
        if _working_root_record:
            st._initial_root_setup = None
        raise
    initial_setup = _consume_initial_setup(st, candidate) if _working_root_record else False
    from .names import ctl_call, ServeBusy
    try:
        result=ctl_call(st,{'cmd':'config_apply','raw':newraw},10) if st.exists() else None
    except ServeBusy:
        # Lost ACK is not a rollback. Query the running host, never overwrite a committed revision.
        try:result=ctl_call(st,{'cmd':'config_revision'},10)
        except ServeBusy:raise ConfigError('/','host acknowledgement unavailable; inspect config diff and doctor before retrying',code=3) from None
        import hashlib
        wanted=hashlib.sha256(json.dumps(candidate,sort_keys=True).encode()).hexdigest()
        if not result or result.get('revision')!=wanted:
            raise ConfigError('/','host did not confirm candidate; configuration remains unchanged or pending; inspect doctor',code=3)
        result={'ok':True,'applied':True,'verify':{'ok':True,'detail':'commit confirmed after lost acknowledgement'}}
    if result is not None:
        if not result.get('ok'):raise ConfigError(result.get('key','/'),result.get('error','apply rejected'),code=3)
        if result.get('reload'):
            # P320: a reload-tier key (an Agent's `agentj config set`, the owner's terminal) — reload at once, never ask
            try:result['reloaded']=ctl_call(st,{'cmd':'reload'},30)
            except ServeBusy as e:result['reloaded']=e.response()
            if (result['reloaded'] or {}).get('ok'):result['needs']=[n for n in result.get('needs',[]) if n!='reload']
        return result
    if result is None:
        from . import ctl_auth, uv_list
        current = effective(st)
        security = uv_list.config_keys(ctl_auth.security_changes(current, candidate))
        # Initial root bookkeeping, called only by working_root.record. It
        # cannot edit approval.* or reopen a host with pairing history.
        bootstrap_root = (_working_root_record and set(security) == {'agent.working_root'}
            and not get(current,'agent.working_root','') and not st.devices() and not st.removed_ledger()
            and not st.removed_path.exists()
            and get(candidate,'agent.working_root','') == (st.config().get('agent') or {}).get('dir'))
        if security and not (bootstrap_root or initial_setup):
            raise ConfigError(security[0], 'security settings require a running host and fresh paired-device UV; no offline change was written', code=3)
    # The running host owns provider/channel dependencies in its service environment.
    # Only an offline write is validated against this CLI process's environment.
    from .voice import validate_runtime
    validate_runtime(candidate)
    from . import claude_auth
    claude_auth.require_transition(st, effective(st), candidate)
    if initial_setup and not initial_setup.live(st, candidate):
        raise ConfigError('agent.working_root', 'initial setup changed before commit; no configuration was written', code=3)
    commit_files(st,newraw,candidate)
    from . import claude_inbound, claude_statusline
    claude_statusline.ensure_default(st)            # P127: same moment as the inbound default; never raises
    claude_inbound.ensure_shared_default(st)
    return {'ok':True,'applied':False,'needs':['start serve'],'verify':{'ok':True,'detail':'validated; takes effect on next serve'}}

def resolve_key(key):
    if not key or key in SCHEMA:return key
    matches=[k for k,m in SCHEMA.items() if key.casefold() in [a.casefold() for a in m['aliases']]]
    if len(matches)>1:raise ConfigError(key,'ambiguous alias; use the full key: '+', '.join(matches))
    return matches[0] if matches else key

def model_command(st, action, value=None, *, dry_run=False):
    """Virtual key: the existing Host state remains the only model owner."""
    from . import provider_profiles
    from .names import ServeBusy
    from .opencode_provider import MODEL_RE, ProviderError
    cfg = st.agent_config() or {}
    if action == 'get':
        return {'ok': True, 'key': 'agent.model', 'value': cfg.get('model'), 'source': 'host-state'}
    if not cfg:
        raise ConfigError('agent.model', 'configure an Agent first')
    model = value if action == 'set' else None
    if action == 'set' and (not isinstance(model, str) or not MODEL_RE.fullmatch(model)):
        raise ConfigError('agent.model', 'expected a model ID; use unset to clear')
    try:
        result = provider_profiles.selection({'harness': cfg['kind']}, model, write=not dry_run)
        if not result.get('applicable'):
            raise ProviderError('agent_kind_changed')
    except ServeBusy:
        raise ConfigError('agent.model', 'Host acknowledgement unavailable; inspect the current model before retrying', code=3) from None
    except ProviderError as error:
        raise ConfigError('agent.model', str(error), code=3) from None
    return {'ok': True, 'key': 'agent.model', 'old': cfg.get('model'), 'new': model,
            'stored': not dry_run, 'applied': False,
            'needs': [] if dry_run else ['harness restart' if result.get('running') else 'start serve'],
            'storage': 'host-state', **result}


def command(args):
    p=argparse.ArgumentParser(prog='agentj config')
    p.add_argument('action',nargs='?',default='path',choices=['path','keys','get','show','set','unset','validate','diff','reset','history','rollback','apply','explain','migrate'])
    p.add_argument('key',nargs='?'); p.add_argument('value',nargs='?'); p.add_argument('--json',action='store_true'); p.add_argument('--json-value',action='store_true'); p.add_argument('--dry-run',action='store_true'); p.add_argument('--search',default=''); p.add_argument('--file'); p.add_argument('--source',action='store_true'); p.add_argument('--effective',action='store_true'); p.add_argument('--against',default='default'); p.add_argument('--all',action='store_true'); p.add_argument('--yes',action='store_true'); p.add_argument('--pending',action='store_true')
    a=p.parse_args(args); st=State()
    try:
        a.key=resolve_key(a.key)
        if a.key == 'agent.model' and a.action in ('get', 'set', 'unset', 'reset'):
            result = model_command(st, a.action, a.value, dry_run=a.dry_run)
        elif a.action=='path': result={'ok':True,'path':str(path())}
        elif a.action=='keys': result={'ok':True,'keys':{k:m for k,m in SCHEMA.items() if a.search.lower() in (k+' '+m['description']+' '+' '.join(m['aliases'])).lower()}}
        elif a.action=='explain': result={'ok':True,'key':a.key,**SCHEMA[a.key]}
        elif a.action=='validate':
            raw=Path(a.file).read_text() if a.file else read()[0]; parse(raw); result={'ok':True,'path':str(a.file or path())}
            if ignored(raw): result['ignored']=ignored(raw)   # P324: pre-P318 dotted keys, never read; nest them to use them
        elif a.action in ('show','get','diff'):
            raw,doc=read(); out=effective(st)
            if a.action=='show':
                put(out, 'agent.model', (st.agent_config() or {}).get('model'))
                result={'ok':True,'effective':out,'sources':{k:'user' if get(doc,k) is not None else m['tier'] if m['tier']!='user' else 'default' for k,m in SCHEMA.items()}}
            elif a.action=='get':
                if a.key not in SCHEMA: raise ConfigError(a.key,'unknown key')
                result={'ok':True,'key':a.key,'value':get(out,a.key,SCHEMA[a.key]['default']),'source':'user' if get(doc,a.key) is not None else SCHEMA[a.key]['tier'] if SCHEMA[a.key]['tier']!='user' else 'default'}
            else:
                if a.against=='default':base=defaults()
                elif a.against=='last-good':base=json.loads(runtime(st).read_text())
                elif re.fullmatch(r'[0-9]+',a.against):base=validate(parse((st.root/'config-history'/(a.against+'.json5')).read_text()))
                else:raise ConfigError('/','against must be default, last-good or a history version')
                result={'ok':True,'diff':{k:{'old':get(base,k),'new':v} for k,v in flatten(out) if get(base,k)!=v}}
        elif a.action=='history': result={'ok':True,'versions':[p.stem for p in sorted((st.root/'config-history').glob('*.json5'))]}
        elif a.action=='migrate':
            from .config_migrations import run
            result=run(st,pending=a.pending or a.dry_run)
        else:
            st.root.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd=os.open(st.root/'preferences.lock',os.O_RDWR|os.O_CREAT,0o600)
            try:
                fcntl.flock(fd,fcntl.LOCK_EX); raw,doc=read(); key=a.key
                if a.action in ('set','unset') or (a.action=='reset' and key in SCHEMA):
                    m=SCHEMA.get(key)
                    if not m: raise ConfigError(key,'unknown key; use agentj config keys')
                    if m['tier']!='user': raise ConfigError(key,'locked security floor' if m['tier']=='locked' else 'human-only; use human command',code=2 if m['tier']=='human' else 1)
                    value=a.value
                    if a.action=='set':
                        if value is None: raise ConfigError(key,'value required')
                        if a.json_value or m['type']!='string':
                            try: value=json5.loads(value,allow_duplicate_keys=False)
                            except ValueError: raise ConfigError(key,'expected '+m['type']) from None
                    newraw=edit(raw,key,value,remove=a.action!='set')
                elif a.action=='reset':
                    if a.all:
                        # F14: --yes (or --dry-run) is the owner's (or, at their request, the Agent's) answer; no terminal needed
                        if not (a.yes or a.dry_run):
                            if not os.isatty(0): raise ConfigError('/','reset --all resets every preference: add --yes',code=1)
                            if input('Reset all user preferences? [y/N] ').lower()!='y': raise ConfigError('/','cancelled',code=1)
                        newraw=template()
                    elif key:
                        if not any(k.startswith(key+'.') for k in SCHEMA):raise ConfigError(key,'unknown section; use agentj config keys')
                        newraw=raw
                        for k,_ in flatten(doc):
                            if k.startswith(key+'.'): newraw=edit(newraw,k,None,remove=True)
                    else: raise ConfigError('/','choose key, section, or --all')
                elif a.action=='rollback':
                    versions=sorted((st.root/'config-history').glob('*.json5'))
                    chosen=next((p for p in versions if p.stem==a.key),None) if a.key else (versions[-1] if versions else None)
                    if chosen is None: raise ConfigError('/','history version not found')
                    newraw=chosen.read_text()
                else: newraw=raw
                candidate=parse(newraw)
                if key=='agent.shared_session_id' and a.action in ('set','unset') and not a.dry_run:
                    from . import shared_pin  # P330: the owner chose (again): the next serve start absorbs it, even unchanged
                    shared_pin.forget_owner(st) if a.action=='set' else shared_pin.unpin(st)
                result={'ok':True,'applied':False,'verify':{'ok':True,'detail':'dry run'},'effective':validate(candidate)} if a.dry_run else transact(st,newraw)
                if key in SCHEMA:result.update(key=key,old=get(validate(doc),key),new=get(validate(candidate),key),needs=result.get('needs',[]))
                if key=='agent.session_mode' and result['old']!=result['new']:
                    from . import session_choice  # P174: what the switch means and the one line that undoes it
                    result['notice']=session_choice.explain(result['new'],result['old'],get(validate(candidate),'appearance.language','zh'))
            finally: os.close(fd)
        print(json.dumps(result,ensure_ascii=False)); return 0
    except ConfigError as e: print(json.dumps(e.result(),ensure_ascii=False)); return e.code
    except (OSError,KeyError) as e: print(json.dumps({'ok':False,'error':type(e).__name__})); return 1


def account_is_newer(st, account, effective_doc=None):
    """A1 last write wins: the account's {"language","language_at"} replaces the local value only when strictly newer."""
    if not isinstance(account,dict) or account.get('language') not in ('zh','en') or type(account.get('language_at')) is not int:
        return False
    return account['language_at'] > language_state(st, effective_doc)[1]


def adopt_account_language(st, account):
    """CLI paths that report without serve (`agentj report`, a rename): apply a newer account language locally (the same
    transactional write as `agentj config set`). Returns True when the local value changed. Never raises."""
    try:
        if not account_is_newer(st, account):
            return False
        res = set_language(st, account['language'], account['language_at'])
        if res is not None:
            st.log('language_synced', status=account['language'])
        return res is not None
    except (ConfigError, OSError, ValueError):
        return False
