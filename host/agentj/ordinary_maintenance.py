"""Owner-only ordinary local maintenance effect proof; never an authority grant.

Host must bind real trusted ingress before/after this filesystem inspection.
Unknown syntax, credential operands and symlink aliases retain existing policy.
"""
import os
import contextvars
from pathlib import Path
import re
import time

TEMP_ROOTS = ('/tmp', '/var/tmp')
_PATHS = contextvars.ContextVar('ordinary_maintenance_paths', default=None)


def _path(raw, cwd):
    if not isinstance(raw,str) or any(c in raw for c in '$`*?[]{}\n'):
        return None
    joined=os.path.join(cwd or os.getcwd(),os.path.expanduser(raw))
    if '..' in Path(joined).parts:return None
    p=Path(os.path.abspath(joined))
    if p.resolve()!=p or any(part.is_symlink() for part in (p,*p.parents)):
        return None
    from . import danger
    if danger.cred_path(str(p)):
        return None
    tracked=_PATHS.get()
    if tracked is not None:tracked.add(p)
    return p


def _under(path, root):
    return path != root and root in path.parents


def _owned(path):
    try:return path.stat().st_uid==os.getuid()
    except OSError:return False


def _temporary(raw,cwd,permanent=False):
    path=_path(raw,cwd)
    if path is None or not any(_under(path,Path(root)) for root in TEMP_ROOTS) or not _owned(path):return False
    return not permanent or path.is_dir() and time.time()-path.stat().st_mtime>86400


def _write(raw,cwd,state_root):
    path=_path(raw,cwd)
    if path is None:return False
    home=Path(os.path.expanduser('~'))
    handoffs=[home/'.local/state/agentj/handoff/compact.md']
    if state_root:handoffs.append(Path(state_root)/'handoff/compact.md')
    if path == home/'.claude/settings.json':
        # This config path is not itself authentication material. Inspect any
        # existing contents before replacing it; do not launder actual secrets.
        from . import privacy
        try:
            if path.exists() and (not path.is_file() or path.stat().st_size>1048576 or privacy.credentials(path.read_text(),entropy=False)):return False
        except (OSError,UnicodeError):return False
        return True
    if path in handoffs:return True
    if path.parent==home/'bin':
        return path.name == 'tmp-test-reaper'
    return path.parent==home/'.config/systemd/user' and path.name in ('tmp-test-reaper.service','tmp-test-reaper.timer')


def _host_unit(name):
    return name in ('agentj.service','agentj-serve.service','agentj-host.service','agentj-relay.service') or name.startswith('agentj@')


def proven(tool,inp,*,cwd=None,state_root=None):
    from . import danger,goal,routine_uv,uv_effect
    if not isinstance(inp,dict) or inp.get('env') or inp.get('shell'):return False
    # Aliased tool inputs must not select a different effective working directory.
    if any(inp.get(k) is not None and inp[k] != cwd for k in ('cwd','workdir')):return False
    if 'cmd' in inp and 'command' in inp and inp['cmd'] != inp['command']:return False
    if tool in ('Write','Edit'):
        from . import privacy
        body='\n'.join(inp.get(k,'') for k in ('content','new_string','old_string') if isinstance(inp.get(k,''),str))
        if privacy.credentials(body,entropy=False):return False
        return _write(inp.get('file_path',inp.get('path')),cwd,state_root)
    if tool not in ('Bash','exec_command','shell_command','execute_command'):return False
    text=inp.get('command',inp.get('cmd'))
    if not isinstance(text,str) or uv_effect._shell_parameter_read(text):return False
    text=routine_uv._home_view(text)
    pieces=goal.pieces(text) or []
    saw=False
    for piece in pieces:
        nodes=danger.lex(piece)
        if len(nodes)!=1 or nodes[0].subst or nodes[0].piped:return False
        command=nodes[0];words=command.words
        if not words:return False
        name=os.path.basename(words[0]);args=words[1:]
        for simple in danger.unwrap(command):
            executable=simple.raw[0] if simple.raw else simple.name
            if '/' in executable and executable not in ('/usr/bin/'+os.path.basename(executable),'/bin/'+os.path.basename(executable)):return False
        if command.redirs:
            if name not in ('cat','printf','echo') or command.ins or not command.docs and name=='cat' and args:
                return False
            if not all(_write(p,cwd,state_root) for p in command.redirs):return False
            if any(not d[1] for d in command.docs):return False
            from . import privacy
            body='\n'.join(d[2] for d in command.docs) if command.docs else ' '.join(args)
            if privacy.credentials(body,entropy=False):return False
            saw=True;continue
        if name=='rm':
            targets=[]
            for arg in args:
                if arg=='--':continue
                if arg.startswith('-'):
                    if arg not in ('-r','-f','-rf','-fr','--recursive','--force','--one-file-system'):return False
                else:targets.append(arg)
            if not targets or not all(_temporary(p,cwd,True) for p in targets):return False
            saw=True;continue
        if name in ('gio','trash-put'):
            if name=='gio':
                if args[:1]!=['trash']:return False
                args=args[1:]
            if not args or any(a.startswith('-') for a in args) or not all(_temporary(p,cwd) for p in args):return False
            saw=True;continue
        if name=='find':
            if command.docs or any(a in ('-exec','-execdir','-ok','-okdir','-delete','-fprint','-fprint0','-fprintf','-files0-from','-L','-H') for a in args):return False
            if not args or args[0] not in TEMP_ROOTS:return False
            if any(a.startswith(('-exec','-ok','-fprint')) for a in args):return False
            saw=True;continue
        if name=='systemctl':
            if '--user' not in args or '--system' in args:return False
            rest=[a for a in args if a!='--user']
            if rest==['daemon-reload']:saw=True;continue
            if not rest or rest[0] not in ('stop','start','restart','enable','disable','status','is-active'):return False
            units=[a for a in rest[1:] if a!='--now']
            if not units or not all(re.fullmatch(r'[A-Za-z0-9_.@-]+\.(?:service|timer)',u) and not _host_unit(u) for u in units):return False
            saw=True;continue
        if name not in ('systemd-run','codex','sleep','date','printf','echo','true','test','head','stat','ls') or not routine_uv.proven_routine('Bash',{'command':piece},cwd=cwd):return False
    return saw


def _seal(paths):
    result=[]
    for path in sorted(paths):
        if path.resolve()!=path:return None
        for part in (path,*path.parents):
            try:
                st=part.lstat()
            except FileNotFoundError:
                result.append((str(part),None));continue
            if part.is_symlink():return None
            identity=(st.st_dev,st.st_ino,st.st_mode,st.st_uid)
            if part==path:identity+= (st.st_mtime_ns,st.st_ctime_ns,st.st_size)
            result.append((str(part),identity))
    return tuple(result)


def qualify(tool,inp,*,cwd=None,state_root=None):
    paths=set();token=_PATHS.set(paths)
    try:
        if not proven(tool,inp,cwd=cwd,state_root=state_root):return None
        seal=_seal(paths)
        if seal is None or not proven(tool,inp,cwd=cwd,state_root=state_root) or _seal(paths)!=seal:return None
        return (tuple(paths),seal)
    finally:_PATHS.reset(token)


def still_valid(proof):
    return _seal(proof[0])==proof[1]
