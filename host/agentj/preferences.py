"""Pure-data user preferences. Schema is the catalogue; hidden State remains human-only.
Writes are serialized and atomic, bounded history stores overrides, and serve acknowledges
before a CLI reports an applied change. No credentials in this layer. Local TTS argv is the explicit host-only execution seam.
"""
from __future__ import annotations
import argparse
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import re
import time
import json5
from .state import State, _write_private

DATA = Path(__file__).with_name('config')
SCHEMA = json.loads((DATA / 'schema.json').read_text())['keys']
MAX_BYTES = 48 * 1024

class ConfigError(ValueError):
    def __init__(self, key, detail, line=1, code=1):
        self.key, self.detail, self.line, self.code = key, detail, line, code
        super().__init__(f'line {line}, {key}: {detail}')
    def result(self):
        return {'ok':False,'key':self.key,'line':self.line,'error':self.detail,'needs':['human'] if self.code==2 else []}

def path():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'agentj/config.json5'

def get(doc, key, default=None):
    for p in key.split('.'):
        if not isinstance(doc,dict) or p not in doc: return default
        doc=doc[p]
    return doc

def put(doc,key,value):
    parts=key.split('.'); at=doc
    for p in parts[:-1]: at=at.setdefault(p,{})
    at[parts[-1]]=value

def flatten(doc,prefix=''):
    for k,v in doc.items():
        key=f'{prefix}.{k}' if prefix else k
        if key in SCHEMA or not isinstance(v,dict): yield key,v
        else: yield from flatten(v,key)

def merge(base,patch):
    if isinstance(base,dict) and isinstance(patch,dict):
        out=copy.deepcopy(base)
        for k,v in patch.items(): out[k]=merge(out.get(k),v)
        return out
    if isinstance(base,list) and isinstance(patch,list) and all(isinstance(x,dict) and isinstance(x.get('id'),str) for x in base+patch):
        out=copy.deepcopy(base)
        for v in patch:
            i=next((i for i,x in enumerate(out) if x['id']==v['id']),None)
            if v.get('disabled') is True:
                if i is not None: out.pop(i)
            elif i is None: out.append(copy.deepcopy(v))
            else: out[i]=merge(out[i],v)
        return out
    return copy.deepcopy(patch)

def defaults():
    out=json5.loads((DATA/'defaults.json5').read_text(),allow_duplicate_keys=False)
    for k,m in SCHEMA.items():
        if m['tier']=='user' and get(out,k)!=m['default']: raise RuntimeError('schema/default drift: '+k)
    return out

def _line(raw,key):
    hits=list(re.finditer(r'["\']?'+re.escape(key.split('.')[-1])+r'["\']?\s*:',raw))
    return raw.count('\n',0,hits[-1].start())+1 if hits else 1

def validate(doc,raw=''):
    if not isinstance(doc,dict): raise ConfigError('/', 'expected an object')
    for k,v in flatten(doc):
        m=SCHEMA.get(k); line=_line(raw,k)
        if not m: raise ConfigError(k,'unknown key; use agentj config keys',line)
        if m['tier']!='user' and k!='version':
            raise ConfigError(k,'security floor is locked' if m['tier']=='locked' else 'human-only; use the existing human command',line,2 if m['tier']=='human' else 1)
        if k=='version' and v!=1: raise ConfigError(k,'supported version: 1',line)
        typ=m['type']
        valid={'boolean':type(v) is bool,'integer':type(v) is int,'number':type(v) in (int,float) and math.isfinite(v),'string':isinstance(v,str),'array':isinstance(v,list)}.get(typ,False)
        if not valid: raise ConfigError(k,'expected '+typ,line)
        if 'enum' in m and v not in m['enum']: raise ConfigError(k,'allowed: '+', '.join(m['enum']),line)
        if isinstance(v,str):
            if any(ord(c)<32 and not (k=='agent.instructions' and c in '\n\t') for c in v) or len(v)>m.get('maxLength',1000): raise ConfigError(k,'invalid characters or length',line)
            if m.get('pattern') and not re.fullmatch(m['pattern'],v): raise ConfigError(k,'expected '+m['patternHint'] if m.get('patternHint') else 'expected environment variable NAME, never a secret',line)
        if typ in ('integer','number') and not m.get('minimum',-math.inf)<=v<=m.get('maximum',math.inf): raise ConfigError(k,f"allowed range: {m.get('minimum')}..{m.get('maximum')}",line)
    cats = get(doc, 'updates.skill_categories', [])
    if len(cats)>4 or any(c not in ('content','app','commerce','general') for c in cats): raise ConfigError('updates.skill_categories','allowed: content, app, commerce, general')
    pif = get(doc, 'agent.private_instructions_file', '')
    if pif and not (pif.startswith('/') or pif.startswith('~/')):
        raise ConfigError('agent.private_instructions_file', 'use an absolute path or ~/path (a local file, never a URL)')
    root = get(doc, 'agent.working_root', '')
    if root and not (os.path.isabs(root) or root == '~' or root.startswith('~/')):
        raise ConfigError('agent.working_root', 'use an absolute path or ~/path')
    out=merge(defaults(),{k:v for k,v in doc.items() if k!='version'})
    # Defaults belong to the selected provider; explicit model/key choices always win.
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
    if get(out,'voice.asr.mode')=='cloud' and not get(out,'voice.asr.model'): raise ConfigError('voice.asr.model','cloud ASR requires an audio-capable model ID')
    if get(out,'voice.tts.mode')=='cloud' and not get(out,'voice.tts.voice'): raise ConfigError('voice.tts.voice','cloud TTS requires a provider voice ID')
    if len(json.dumps(out).encode()) > 12000: raise ConfigError("/", "effective configuration must be at most 12 KiB")
    return out

def parse(raw):
    if len(raw.encode())>MAX_BYTES: raise ConfigError('/', 'configuration exceeds 48 KiB')
    try: doc=json5.loads(raw,allow_duplicate_keys=False)
    except ValueError as e:
        # Parser messages can include the bad input (possibly a pasted credential). Do not echo it.
        m=re.search(r':(\d+)\b',str(e)); raise ConfigError('/','invalid JSON5 syntax (or duplicate key)',int(m[1]) if m else 1) from None
    validate(doc,raw)
    return doc

def read():
    try: raw=path().read_text()
    except FileNotFoundError: raw='// Agent J user overrides; factory defaults update independently.\n{version: 1}\n'
    return raw,parse(raw)

def effective(st=None):
    _,doc=read(); out=validate(doc)
    if not get(out,'voice.wake_word'): put(out,'voice.wake_word','嘿 '+((st or State()).agent_name() or 'Agent J'))
    return out

# JSON5 structural editor: change only the requested value, leaving other lines/comments intact.
TOKEN=re.compile(r'\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}\[\]:,]|[^\s{}\[\]:,]+')
def spans(raw):
    ts=[m for m in TOKEN.finditer(raw) if not m[0].isspace() and not m[0].startswith(('//','/*'))]; vals={}; objects={}; members={}
    def value(i,p):
        start=i
        if ts[i][0]=='{':
            i+=1; rows=[]
            while ts[i][0]!='}':
                ki=i; k=ts[i][0]; k=json5.loads(k) if k.startswith(('"',"'")) else k
                assert ts[i+1][0]==':'
                child=p+'.'+k if p else k
                i=value(i+2,child); end=i; comma=None
                if ts[i][0]==',': comma=i; i+=1
                rows.append((child,ki,end,comma))
            objects[p]=(ts[start].start(),ts[i].start(),rows)
            for n,(child,ki,end,comma) in enumerate(rows):
                a=ts[ki].start(); b=ts[end-1].end()
                if comma is not None: b=ts[comma].end()
                elif n and rows[n-1][3] is not None: a=ts[rows[n-1][3]].start()
                members[child]=(a,b)
            i+=1
        elif ts[i][0]=='[':
            i+=1
            while ts[i][0]!=']':
                i=value(i,p+'[]')
                if ts[i][0]==',': i+=1
            i+=1
        else: i+=1
        vals[p]=(ts[start].start(),ts[i-1].end()); return i
    value(0,''); return vals,objects,members

def edit(raw,key,value,remove=False):
    vals,objects,members=spans(raw)
    if remove:
        if key not in members: return raw
        a,b=members[key]; return raw[:a]+raw[b:]
    encoded=json.dumps(value,ensure_ascii=False)
    if key in vals:
        a,b=vals[key]; return raw[:a]+encoded+raw[b:]
    parent,_,leaf=key.rpartition('.')
    if parent not in objects:
        parts=key.split('.'); at=value
        while parts:
            leaf=parts.pop(); parent='.'.join(parts)
            if parent in objects: break
            at={leaf:at}
        encoded=json.dumps(at,ensure_ascii=False)
    _,close,rows=objects[parent]
    comma='' if not rows or rows[-1][3] is not None else ','
    return raw[:close]+comma+'\n  '+json.dumps(leaf)+': '+encoded+'\n'+raw[close:]

def template():
    lines=['// Agent J: pure data. Put only your changes here. Never put API keys in this file.', '// Defaults upgrade independently. Security/approvals/devices are human-only.', '{','  version: 1,']
    for k,m in SCHEMA.items():
        lines+=['  // '+k+': '+m['description'], '  // default: '+json.dumps(m['default'],ensure_ascii=False)+'; tier='+m['tier']+'; apply='+m['apply']]
    return '\n'.join(lines+['}',''])

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

def commit_files(st,newraw,candidate):
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
    note_language(st,candidate)


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


def transact(st,newraw):
    candidate=validate(parse(newraw))
    from .voice import validate_runtime
    validate_runtime(candidate)
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
        return result
    from . import claude_auth
    claude_auth.require_transition(st, effective(st), candidate)
    commit_files(st,newraw,candidate)
    from . import claude_inbound
    claude_inbound.ensure_shared_default(st)
    return {'ok':True,'applied':False,'needs':['start serve'],'verify':{'ok':True,'detail':'validated; takes effect on next serve'}}

def resolve_key(key):
    if not key or key in SCHEMA:return key
    matches=[k for k,m in SCHEMA.items() if key.casefold() in [a.casefold() for a in m['aliases']]]
    if len(matches)>1:raise ConfigError(key,'ambiguous alias; use the full key: '+', '.join(matches))
    return matches[0] if matches else key

def command(args):
    p=argparse.ArgumentParser(prog='agentj config')
    p.add_argument('action',nargs='?',default='path',choices=['path','keys','get','show','set','unset','validate','diff','reset','history','rollback','apply','explain','migrate'])
    p.add_argument('key',nargs='?'); p.add_argument('value',nargs='?'); p.add_argument('--json',action='store_true'); p.add_argument('--json-value',action='store_true'); p.add_argument('--dry-run',action='store_true'); p.add_argument('--search',default=''); p.add_argument('--file'); p.add_argument('--source',action='store_true'); p.add_argument('--effective',action='store_true'); p.add_argument('--against',default='default'); p.add_argument('--all',action='store_true'); p.add_argument('--yes',action='store_true'); p.add_argument('--pending',action='store_true')
    a=p.parse_args(args); st=State()
    try:
        a.key=resolve_key(a.key)
        if a.action=='path': result={'ok':True,'path':str(path())}
        elif a.action=='keys': result={'ok':True,'keys':{k:m for k,m in SCHEMA.items() if a.search.lower() in (k+' '+m['description']+' '+' '.join(m['aliases'])).lower()}}
        elif a.action=='explain': result={'ok':True,'key':a.key,**SCHEMA[a.key]}
        elif a.action=='validate':
            raw=Path(a.file).read_text() if a.file else read()[0]; parse(raw); result={'ok':True,'path':str(a.file or path())}
        elif a.action in ('show','get','diff'):
            raw,doc=read(); out=effective(st)
            if a.action=='show': result={'ok':True,'effective':out,'sources':{k:'user' if get(doc,k) is not None else m['tier'] if m['tier']!='user' else 'default' for k,m in SCHEMA.items()}}
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
                result={'ok':True,'applied':False,'verify':{'ok':True,'detail':'dry run'},'effective':validate(candidate)} if a.dry_run else transact(st,newraw)
                if key in SCHEMA:result.update(key=key,old=get(validate(doc),key),new=get(validate(candidate),key),needs=result.get('needs',[]))
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
