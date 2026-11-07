"""Private native-session scratch deletion proof, not a source or permission grant."""
import hashlib
import os
from pathlib import Path
import re
import shlex

_TMP = Path('/tmp')
_TOKEN = object()


class Context:
    def __init__(self, token, host):
        if token is not _TOKEN: raise ValueError('private context')
        from . import approval_special, peer_origin, provenance, shared, approval_level, native_owner_launch
        self.host, self.actor = host, host.agent
        if approval_level.level(host.preferences,host.st)!='harness':raise ValueError('full access only')
        if not isinstance(self.actor, shared.SharedClaudeAgent): raise ValueError('native harness')
        self.session = self.actor.session
        self.session_value = dict(self.session or {})
        sid = self.session_value.get('sessionId')
        cwd = self.session_value.get('cwd')
        transcript = self.actor.transcript
        if (not isinstance(sid,str) or not re.fullmatch(r'[0-9a-f-]{36}',sid)
                or not isinstance(cwd,str) or not Path(cwd).is_absolute() or transcript is None
                or Path(transcript).stem != sid or Path(transcript).parent.name != re.sub(r'[^A-Za-z0-9]','-',cwd)):
            raise ValueError('native session identity')
        self.transcript = Path(transcript)
        self.pid = self.session_value.get('pid')
        self.start = self.session_value.get('procStart')
        self.send = getattr(self.actor,'cur_send',None)
        self.state, self.epoch = host.st, host.hist.epoch
        self.source, self.task = approval_special._current_context(host)
        self.turn_source = provenance.turn_source(self.actor)
        self.background = native_owner_launch.current(self.actor)
        if self.background is None and self.turn_source.get('kind')=='phone' and not (self.actor.phone_turn and self.actor.phone_seen):raise ValueError('native owner delivery not observed')
        if (not self.source or not self.source.get('verified') or self.source.get('suspicious')
                or not (provenance.is_owner(self.turn_source) and self.send is not None or self.background is not None)):
            raise ValueError('original owner holder')
        self.root = _TMP / ('claude-'+str(os.getuid())) / Path(transcript).parent.name / sid / 'scratchpad'
        if not self.live(): raise ValueError('native process')

    def live(self):
        from . import approval_special, peer_origin, provenance, shared, approval_level, native_owner_launch
        h,a=self.host,self.actor
        row=peer_origin._stat(Path('/proc'),self.pid) if type(self.pid) is int else None
        try:
            return (approval_level.level(h.preferences,h.st)=='harness' and shared._same_process(self.session_value)
                and h.agent is a and h.st is self.state and h.hist.epoch==self.epoch and not h.stopped()
                and not h.stopping.is_set() and a.cur_send is self.send and (self.send is None or self.send.state not in ('cancelled','failed'))
                and a.session is self.session and a.session==self.session_value and Path(a.transcript)==self.transcript
                and approval_special._current_context(h)==(self.source,self.task)
                and provenance.turn_source(a)==self.turn_source
                and (self.background is not None and native_owner_launch.current(a) is self.background
                     or self.background is None and not provenance.untrusted_turn(a))
                and isinstance(self.start,str) and row is not None and str(row[2])==self.start
                and peer_origin._uid(Path('/proc'),self.pid)==os.getuid())
        except (OSError,ValueError,TypeError,AttributeError):
            return False


def context(host):
    try:return Context(_TOKEN,host)
    except (OSError,ValueError,TypeError,AttributeError):return None


def _path(raw,cwd):
    from . import danger
    if not isinstance(raw,str) or any(x in raw for x in '$`*?[]{}~\n') or '..' in Path(raw).parts:return None
    p=Path(os.path.abspath(os.path.join(cwd,raw)))
    if danger.cred_path(str(p)) or p.resolve()!=p:return None
    for part in (p,*p.parents):
        if part.is_symlink():return None
    if p.exists():
        st=p.stat()
        if st.st_uid!=os.getuid() or p.is_file() and st.st_nlink!=1:return None
    return p


def qualify(tool,inp,cwd,scope):
    from . import static_shell as shell, danger, uv_effect, policy_registry, ordinary_maintenance as maintenance
    if type(scope) is not Context or not scope.live() or tool not in ('Bash','exec_command','shell_command','execute_command'):
        return None
    if not isinstance(inp,dict) or inp.get('env') or inp.get('shell'):return None
    if 'cmd' in inp and 'command' in inp and inp['cmd']!=inp['command']:return None
    if any(inp.get(k) is not None and inp[k]!=cwd for k in ('cwd','workdir')):return None
    text=inp.get('command',inp.get('cmd'))
    if not isinstance(text,str) or len(text)>65536:return None
    if re.search(r'(?<!\\)\$(?:[0-9@*#?$!-]|\{[^A-Za-z_]|\()',text):return None
    rows=shell._statements(text)
    if not rows:return None
    commands=[]
    danger._bash(text,danger._Hits(),collect=commands)
    names=[os.path.basename(c.name) for c in commands]
    if names.count('rm')!=1 or any(n in ('rmdir','unlink','shred','srm','wipe') for n in names):return None
    # Opaque execution cannot inherit this narrow proof. Shell read-loop syntax
    # remains data/control syntax, never permission to execute a new program.
    allowed={'rm','mkdir','cd','cp','grep','find','wc','du','read','test','[','printf','echo','true','while','do','done'}
    if any(n not in allowed for n in names):return None
    known={};rm=None
    for words,op in rows:
        if not words or any(w[3] for w in words):return None
        raw=[w[2] for w in words]
        if all(re.match(r'[A-Za-z_]\w*=',w) for w in raw):
            if op not in (';','\n') or len(known)+len(raw)>8:return None
            for word in raw:
                name=word.split('=',1)[0]
                if name in known or name in maintenance._STARTUP_ENV:return None
                expanded=shell._expand(word,known);value=shell._literal(expanded)
                if value is None or danger.cred_path(value):return None
                known[name]=value
            continue
        expanded=[shlex.split(shell._expand(w,known)) for w in raw]
        if any(len(w)!=1 for w in expanded):return None
        values=[w[0] for w in expanded]
        if values[0]!='rm' or op not in (';','&&','\n'):return None
        args=values[1:];targets=[x for x in args if not x.startswith('-')]
        if len(targets)!=1 or any(x.startswith('-') and x not in ('-rf','-fr','-r','-f','--') for x in args):return None
        rm=(words,targets[0]);break
    if rm is None:return None
    # No ambient shell state supplies operands. Native read-loop data may retain
    # the original remaining-program policy, but every environment variable must
    # have a literal binding in this exact call.
    read_vars=set()
    for words,_ in rows:
        values=[shlex.split(w[2])[0] for w in words if not w[3]]
        if values[:1]==['while'] and 'read' in values and re.fullmatch(r'[A-Za-z_]\w*',values[-1]):
            read_vars.add(values[-1])
    references={m[1] or m[2] for m in shell._VAR.finditer(text)}
    if references-set(known)-read_vars:return None
    root=_path(str(scope.root),cwd);target=_path(rm[1],cwd)
    if root is None or not root.is_dir() or target is None or root not in target.parents:return None
    # Fixed namespace parents are owned and not shared writable; /tmp itself is not such a parent.
    for part in (root,*list(root.parents)[:3]):
        st=part.lstat()
        if st.st_uid!=os.getuid() or st.st_mode&0o022:return None
    paths={root,target}
    # This proof changes only the first rm's delete classification. No copy
    # permission is introduced: retain the COMPLETE remaining original program
    # for the existing independent effect policy below.
    for words,op in rows:
        expanded=[shell._expand(w[2],known) for w in words if not w[3]]
        try:values=[shlex.split(w)[0] for w in expanded]
        except (ValueError,IndexError):return None
        if not values:continue
        names=values[1:] if values[0]=='do' else values
        if names and names[0] in ('mv','ln','eval','source','.','exec','sh','bash','python','python3','node','perl','ruby','xargs'):
            return None
        if values[0]=='find' and any(v in ('-exec','-execdir','-ok','-okdir') for v in values):return None
    words=rm[0]
    analysis=text[:words[0][0]]+'true'+text[words[-1][1]:]
    if uv_effect.assess(tool,{'command':analysis},cwd=cwd).reason is not None:return None
    if policy_registry.main_requirement(tool,{'command':analysis},cwd=cwd)!='allow':return None
    seal=maintenance._seal(paths)
    if seal is None or not scope.live():return None
    return (scope,tool,inp,cwd,tuple(paths),seal,hashlib.sha256(text.encode()).hexdigest())


def still_valid(proof):
    from . import ordinary_maintenance as maintenance
    scope,tool,inp,cwd,paths,seal,digest=proof
    if type(scope) is not Context or not scope.live() or maintenance._seal(paths)!=seal:return False
    text=inp.get('command',inp.get('cmd'))
    return isinstance(text,str) and hashlib.sha256(text.encode()).hexdigest()==digest and qualify(tool,inp,cwd,scope) is not None
