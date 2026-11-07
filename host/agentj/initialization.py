"""#332: closed, inert package preparation metadata. No package-selected effects."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

FILE = 'initialization.schema.json'
MAX_BYTES = 65536
PROVIDERS = {
    'anthropic': ('ANTHROPIC_API_KEY',), 'openai': ('OPENAI_API_KEY',),
    'agentsrelay': ('AGENTSRELAY_OPENAI_KEY','AGENTSRELAY_CLAUDE_KEY'),
    'ark': ('ARK_AGENT_PLAN_API_KEY','ARK_CODING_PLAN_API_KEY','ARK_API_KEY'),
    'bailian': ('BAILIAN_TOKEN_PLAN_API_KEY','DASHSCOPE_API_KEY'),
    'kimi': ('KIMI_API_KEY','MOONSHOT_API_KEY'), 'deepseek': ('DEEPSEEK_API_KEY',),
    'google': ('GEMINI_API_KEY',), 'elevenlabs': ('ELEVENLABS_API_KEY',),
    'local': (), 'manual': (), 'custom': (),
}
_ID = re.compile(r'[a-z][a-z0-9-]{0,39}')
_NAME = re.compile(r'[A-Z][A-Z0-9_]{0,63}')
_LOCAL = re.compile(r'[A-Za-z0-9][A-Za-z0-9 ._+-]{0,79}')
_SECRET = re.compile(r'(?:sk-|gh[pousr]_|Bearer\s+|-----BEGIN|[A-Za-z0-9_+/=-]{40,})', re.I)


def _fail():
    raise ValueError('invalid_initialization_schema')


def _obj(x, allowed, required=()):
    if not isinstance(x, dict) or set(x)-set(allowed) or not set(required)<=set(x): _fail()


def _text(x, limit=300):
    if not isinstance(x, str) or not 1<=len(x)<=limit or any(ord(c)<32 for c in x) or _SECRET.search(x) or any(ord(c) in range(0x202a,0x202f) or ord(c) in range(0x2066,0x206a) for c in x): _fail()


def _list(x, limit=24):
    if not isinstance(x, list) or len(x)>limit: _fail()
    return x


def _bool(x):
    if type(x) is not bool: _fail()


def _pairs(rows):
    d={}
    for k,v in rows:
        if k in d: _fail()
        d[k]=v
    return d


def parse(raw: bytes, files: dict) -> dict:
    if not isinstance(raw, bytes) or len(raw)>MAX_BYTES: _fail()
    try:
        doc=json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, RecursionError, UnicodeError): _fail()
    _obj(doc, ('$schema','type','properties','required','additionalProperties','x-capabilities'), ('type','x-capabilities'))
    if doc.get('$schema', 'http://json-schema.org/draft-07/schema#')!='http://json-schema.org/draft-07/schema#' or doc['type']!='object': _fail()
    if 'additionalProperties' in doc and doc['additionalProperties'] is not False: _fail()
    props=doc.get('properties', {})
    if not isinstance(props, dict) or len(props)>24: _fail()
    for key, rule in props.items():
        if not _ID.fullmatch(key): _fail()
        _obj(rule, ('type','enum','default','description'), ('type','enum'))
        if rule['type'] not in ('string','boolean','integer'): _fail()
        values=_list(rule['enum'])
        if not values: _fail()
        typ={'string':str,'boolean':bool,'integer':int}[rule['type']]
        for value in values:
            if type(value) is not typ: _fail()
            if isinstance(value,str): _text(value,80)
            if type(value) is int and abs(value)>1000000: _fail()
        if 'default' in rule and (type(rule['default']) is not typ or rule['default'] not in values): _fail()
        if 'description' in rule: _text(rule['description'])
    if any(not isinstance(k,str) or k not in props for k in _list(doc.get('required', []))): _fail()
    ids=set()
    caps=_list(doc['x-capabilities'])
    if not caps: _fail()
    for cap in caps:
        _obj(cap, ('id','stage_zh','required','providers','if_missing_zh','degrade_zh','adapter'),
             ('id','stage_zh','required','providers','if_missing_zh','degrade_zh','adapter'))
        if not isinstance(cap['id'],str) or not _ID.fullmatch(cap['id']) or cap['id'] in ids: _fail()
        ids.add(cap['id']); _bool(cap['required'])
        for key in ('stage_zh','if_missing_zh','degrade_zh'): _text(cap[key])
        adapter=cap['adapter']
        if adapter is not None:
            _text(adapter,200)
            path=adapter.split('#',1)[0]
            parts=PurePosixPath(path).parts
            if (not parts or parts[0]!='references' or any(x in ('.','..') or x.startswith('.') for x in parts)
                    or '\\' in path or '//' in path or path not in files or not path.endswith('.md')): _fail()
        providers=_list(cap['providers'],8)
        if not providers or (len(providers)>1 and adapter is None): _fail()
        seen=set()
        for p in providers:
            _obj(p, ('id','name_zh','default','env','cli','fonts','manual','fallback','config'), ('id',))
            if not isinstance(p['id'],str) or p['id'] not in PROVIDERS or p['id'] in seen: _fail()
            seen.add(p['id'])
            if 'name_zh' in p: _text(p['name_zh'],80)
            for key in ('default','manual','fallback'):
                if key in p: _bool(p[key])
            for env in _list(p.get('env',[])):
                _obj(env, ('name','secret','optional'), ('name',))
                n=env['name']
                if not isinstance(n,str) or not _NAME.fullmatch(n) or n.startswith(('AGENTJ_','LD_','DYLD_','PYTHON','NODE','BASH','ENV','PATH','HOME')): _fail()
                if p['id'] not in ('custom','manual') and n not in PROVIDERS[p['id']]: _fail()
                for k in ('secret','optional'):
                    if k in env: _bool(env[k])
            for key in ('cli','fonts'):
                for fact in _list(p.get(key,[])):
                    _obj(fact, ('name','optional'), ('name',))
                    if not isinstance(fact['name'],str) or not _LOCAL.fullmatch(fact['name']): _fail()
                    if key=='cli' and ' ' in fact['name']: _fail()
                    if 'optional' in fact: _bool(fact['optional'])
            if any(not isinstance(k,str) or k not in props for k in _list(p.get('config',[]))): _fail()
    return doc


def from_bundle(manifest, files):
    declared=manifest.get('initialization_schema')
    if declared is None and FILE not in files: return None
    if declared!=FILE or manifest.get('type') not in ('skill','workflow') or FILE not in files: _fail()
    return parse(files[FILE],files)


def assess(doc, *, env_names, cli_names, fonts):
    """Inputs contain presence ONLY. None is unknown, never successful evidence."""
    rows=[]; requests=[]
    for c in doc['x-capabilities']:
        checks=[]
        for p in c['providers']:
            absent=[]; unknown=p['id']!='local' or bool(p.get('manual')) or bool(p.get('config'))
            for key, have in (('env',env_names),('cli',cli_names),('fonts',fonts)):
                for fact in p.get(key,[]):
                    if fact.get('optional'): continue
                    if have is None: unknown=True
                    elif fact['name'] not in have:
                        absent.append(fact['name'])
                        if key=='env' and fact.get('secret') and fact['name'] in PROVIDERS[p['id']]:
                            r={'provider':p['id'],'name':fact['name']}
                            if r not in requests: requests.append(r)
            # Empty local declarations are not evidence of any actual capability.
            if not any(p.get(k) for k in ('cli','fonts')): unknown=True
            checks.append({'id':p['id'],'missing':absent,'evidence':'unknown' if unknown else 'local',
                           'ready':not absent and not unknown,'fallback':bool(p.get('fallback'))})
        normal=next((p for p in checks if p['ready'] and not p['fallback']), None)
        fallback=any(p['ready'] for p in checks)
        rows.append({'id':c['id'],'stage':c['stage_zh'],'required':c['required'],
                     'status':'ready' if normal else 'degraded' if fallback or not c['required'] else 'missing',
                     'evidence':'local' if normal else 'unknown' if any(p['evidence']=='unknown' for p in checks) else 'local',
                     'providers':checks,'if_missing':c['if_missing_zh'],'degrade':c['degrade_zh'],'adapter':c['adapter']})
    return {'rows':rows,'key_requests':requests,'config':list(doc.get('properties',{})),
            'note':'Local presence only; cloud, accounts, manual steps and configuration are unverified.'}


def local_assess(doc):
    """Read-only facts. No package command, Python import, network or model probe."""
    from . import service
    names={v['name'] for c in doc['x-capabilities'] for p in c['providers'] for v in p.get('env',[])}
    present=set(); unknown=False
    for name in names:
        fact=service.env_file_has(service.env_file(service.name()),name)
        if fact is True or name in os.environ: present.add(name)
        elif fact is None: unknown=True
    clis={v['name'] for c in doc['x-capabilities'] for p in c['providers'] for v in p.get('cli',[])}
    have={name for name in clis if shutil.which(name)}
    fonts=None
    # One fixed inventory query; no package-supplied arguments or shell.
    fc='/usr/bin/fc-list'
    wanted_fonts={v['name'] for c in doc['x-capabilities'] for p in c['providers'] for v in p.get('fonts',[])}
    if wanted_fonts and Path(fc).is_file():
        try:
            with tempfile.TemporaryFile() as output:
                completed=subprocess.run([fc,'--format=%{family}\n'],stdout=output,
                                         stderr=subprocess.DEVNULL,timeout=3,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
                output.seek(0); raw=output.read(262145)
                if completed.returncode==0 and len(raw)<=262144:
                    fonts={n.strip() for line in raw.decode('utf-8',errors='replace').splitlines() for n in line.split(',')}
        except (OSError,subprocess.TimeoutExpired): pass
    return assess(doc,env_names=None if unknown else present,cli_names=have,fonts=fonts)


async def deliver(host, device, cards):
    """Only the device that signed the installation receives these bounded cards."""
    # Send one package per encrypted frame, never an unbounded group aggregate.
    for card in cards:
        message={'t':'setup_card','initialization':[card]}
        if len(json.dumps(message,ensure_ascii=False).encode())>48*1024:
            compact={**card,'details_omitted':True}
            compact['rows']=[{k:(v[:120] if isinstance(v,str) else v)
                              for k,v in row.items() if k!='providers'}
                             for row in card['rows']]
            message={'t':'setup_card','initialization':[compact]}
        for s in list(host.sessions.values()):
            if s.device==device and host._paired_live(s) and host.st.sign_key(device):
                await host.send_app(s, message)
