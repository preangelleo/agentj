"""State-free schema, factory defaults and JSON5 data operations.

Only packaged schema/default files are read here. User paths, identity, activation
and semantic runtime validation remain owned by the preferences facade.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import re
import json5
from .preference_values import ConfigError, get, put

DATA = Path(__file__).with_name('config')
SCHEMA = json.loads((DATA / 'schema.json').read_text())['keys']
MAX_BYTES = 48 * 1024

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

# P331: the pure-Python json5 parser costs ~20 ms per call for defaults.json5 alone on a fast desktop and 6–16 s on a
# loaded Mac, and effective() runs from several serve loops. The file is still read every time (cheap); the parse is kept,
# keyed by the exact text, so any change by any writer is seen at once. Callers get a private deep copy.
_PARSED={}

def _cached(slot,raw,make):
    hit=_PARSED.get(slot)
    if hit is None or hit[0]!=raw:
        try: hit=(raw,True,make(raw))
        except ConfigError as e: hit=(raw,False,e)
        _PARSED[slot]=hit
    if not hit[1]:
        e=hit[2]; raise ConfigError(e.key,e.detail,e.line,e.code) from None
    return copy.deepcopy(hit[2])

def defaults():
    return _cached('defaults',(DATA/'defaults.json5').read_text(),_defaults)

def _defaults(raw):
    out=json5.loads(raw,allow_duplicate_keys=False)
    for k,m in SCHEMA.items():
        if m['tier']=='user' and get(out,k)!=m['default']: raise RuntimeError('schema/default drift: '+k)
    return out

def _line(raw,key):
    hits=list(re.finditer(r'["\']?'+re.escape(key.split('.')[-1])+r'["\']?\s*:',raw))
    return raw.count('\n',0,hits[-1].start())+1 if hits else 1

def _dotted(doc,prefix=''):
    """P318: the first object key spelled with a '.' (a literal "agent.allow_docker" would alias the nested key: flatten
    sees one schema key twice, the runtime reads only the nested one) — keys inside a schema value are data, not checked."""
    for k,v in doc.items():
        key=f'{prefix}.{k}' if prefix else str(k)
        if not isinstance(k,str) or '.' in k: return key
        if key not in SCHEMA and isinstance(v,dict):
            hit=_dotted(v,key)
            if hit: return hit
    return None

def _undot(doc,prefix=''):
    """P324 upgrade compatibility: a config.json5 written before P318 may hold a literal dotted key ("agent.x": …). The
    runtime never read those (get() walks nested objects), so they are dropped here — same effect as before, no error at
    upgrade, and a dropped key can no longer alias a nested one. → (doc without them, their dotted paths)."""
    out,gone={},[]
    for k,v in doc.items():
        key=f'{prefix}.{k}' if prefix else str(k)
        if not isinstance(k,str) or '.' in k: gone.append(key); continue
        if key not in SCHEMA and isinstance(v,dict):
            v,g=_undot(v,key); gone+=g
        out[k]=v
    return out,gone

def ignored(raw):
    """P324: the literal dotted keys parse() drops from this text (names only, for doctor / `agentj config check`)."""
    try: doc=json5.loads(raw,allow_duplicate_keys=False)
    except ValueError: return []
    return _undot(doc)[1] if isinstance(doc,dict) else []

COMPOSER_BUTTONS = ('file', 'image', 'camera', 'mic', 'full')   # P340: the default left-to-right order above the phone's message box

def composer_order_ok(v):
    # P462: old remotes / archives may still send the original complete four-item permutation.
    return isinstance(v, list) and all(isinstance(x, str) for x in v) and any(
        len(v) == len(ids) and sorted(v) == sorted(ids) for ids in (COMPOSER_BUTTONS, COMPOSER_BUTTONS[:-1]))

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
                if '.' in k: child='\0'+child     # P324: a literal dotted key is not the nested key it spells; edit() never targets it
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

def decode(raw):
    """Decode sparse overrides; preferences.parse additionally enforces semantics."""
    if len(raw.encode())>MAX_BYTES: raise ConfigError('/', 'configuration exceeds 48 KiB')
    try: doc=json5.loads(raw,allow_duplicate_keys=False)
    except ValueError as e:
        # Parser messages can include the bad input (possibly a pasted credential). Do not echo it.
        m=re.search(r':(\d+)\b',str(e)); raise ConfigError('/','invalid JSON5 syntax (or duplicate key)',int(m[1]) if m else 1) from None
    if isinstance(doc,dict): doc=_undot(doc)[0]   # P324: pre-P318 dotted keys are ignored (as the runtime always did), not an error
    return doc

