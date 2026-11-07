"""Owner-only ordinary local maintenance effect proof; never an authority grant.

Host must bind real trusted ingress before/after this filesystem inspection.
Unknown syntax, credential operands and symlink aliases retain existing policy.
"""
import os
import contextvars
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

TEMP_ROOTS = ('/tmp', '/var/tmp')
_PATHS = contextvars.ContextVar('ordinary_maintenance_paths', default=None)
_UNITS = contextvars.ContextVar('ordinary_maintenance_units', default=None)
_STARTUP_ENV = frozenset(('PATH','HOME','BASH_ENV','ENV','PYTHONPATH','PYTHONHOME','PYTHONSTARTUP',
    'PYTHONUSERBASE','PYTHONINSPECT','NODE_OPTIONS','SHELLOPTS','BASHOPTS','IFS','CDPATH','GLOBIGNORE','PS4','RUBYOPT','PERL5OPT'))

# Reviewed native dispatch profile, like local_service_notice's source pins.
# Path digests keep private workflow layout out of the distributed package.
# Neither a matching basename nor editable registry text grants this exception.
REVIEWED_DISPATCH = {
    'name':'daily-video-publish',
    'launcher':'2b07389441efc4551bd5f9a8fbb2094b8fbfd3c84ccc2bb082cf3547a823833c',
    'unit':'0533c0926a80c630ad03b429d4de76cab173df82316c0d6d0b7a41ab81babb13',
    'work':'b226524629053cdca962e878419e3114b7166ee8430b02698b778200fe2521be',
    'files':{
        '22c5d786e765b89097974e5a9cbfd7507c290c3aa0835b96358bca12545ec784':'2b07389441efc4551bd5f9a8fbb2094b8fbfd3c84ccc2bb082cf3547a823833c',
        'ab27c5e6de8395b971b781257c0584152056a3673337248413de0d6699a80daf':'0a96fa43fe6340b63077d88c80caa144e9a8871bb7b9504aeb85b951443f1c02',
        '5da60444ae04535999970bda1bbb2d6149c946176bc9d97d252318a333f542cb':'4dd40cd1274560b6774ba4ac60219640ebe15d1d8067c22096e5317b90df03c5',
        '17ae6725b6e35a49fcef8a71da7182670536fddddadc56aefcea2fb4d9893e10':'74f02b27a27234df265a3e62d99848af30952996a5fc73ddc052ba26c6ea2f66',
        '64b111a7661c30c3eaaebe85ea7ea0f155f5210cee683b2dbc102ad06542bb20':'a7549b056a98796cd18101234f9bb41878e4880fc071e131723824885e82442c',
        'a36539215ac67fc37d0e4d0fa1640cfb01b1bee81935f4f8b28d73c04c9a222e':'ed500a50d3da985b69079601b060d24386d3eea65d50eac792e80e12ea2a0c92',
        'fa93fda1a5128b6221538881170561e68aa87e91c8e49eb722830bfd54ee5056':'84dcc78463887a7c13dea339ce35558b4fb8c8b6028609bd29a06e49ab0075cf',
        '302e810b2d1d5ee3082077aea0129e4638e7692195456b2bb0ff4057d77fe0cc':'d91e5a18d463e2c33ff78ccd6a79d9b538feaf63ccf91f0a69294e4da17ceeda',
    },
}


def _digest(value):
    return hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()


def _dispatch_runtime(body,cwd):
    """Bind env-shebang resolution and the launcher's fixed exported PATH."""
    from . import binaries,peer_origin
    result=subprocess.run(['/usr/bin/systemctl','--user','show-environment'],capture_output=True,text=True,timeout=5)
    if result.returncode or len(result.stdout)>1048576:return False
    # Manager environment is inspected inside the Host; never returned/logged.
    manager_path=None
    forbidden=set(peer_origin._BAD_ENV)|(_STARTUP_ENV-{'PATH','HOME'})
    for line in result.stdout.splitlines():
        key,eq,value=line.partition('=')
        if eq and (key in forbidden or key.startswith(('LD_','DYLD_'))) and value:return False
        if eq and key=='PATH':manager_path=value
    fixed=re.search(r'^export PATH="([^"\n]+)"$',body,re.M)
    if not manager_path or not fixed:return False
    fixed_path=fixed[1].replace('$HOME',str(Path.home()))
    if '$' in fixed_path:return False
    for path,names in ((manager_path,('bash',)),(fixed_path,('bash','python3','codex','date','hostname','cat','grep','mkdir','tee','flock','head','curl','sleep','systemctl'))):
        dirs=path.split(':')
        if not dirs or any(not d.startswith('/') or any(c in d for c in '$`\n') for d in dirs):return False
        for name in names:
            chosen=shutil.which(name,path=path)
            if not chosen:return False
            real=Path(chosen).resolve()
            if name=='codex':
                known=binaries.resolve('codex',environ={**os.environ,'PATH':fixed_path}).get('path')
                trees=[Path(p) for n,p in binaries.trees() if n=='codex']
                if not known or real!=Path(known).resolve() or not any(t in real.parents for t in trees):return False
            elif real!=Path('/usr/bin',name).resolve():return False
            # Include missing earlier candidates, so a newly introduced shadow
            # executable invalidates the filesystem proof before admission.
            for directory in dirs:
                candidate=Path(directory,name)
                # This is only executable-search metadata, not a file-content
                # operand. An absent candidate in a protected directory is not
                # a credential read. Seal its identity without reading bytes.
                tracked=_PATHS.get()
                if tracked is not None:tracked.add(candidate.resolve())
                if str(candidate)==chosen:break
    tracked=_UNITS.get()
    if tracked is not None:tracked.append(('dispatch-runtime',_digest(manager_path),_digest(fixed_path)))
    return _path('/usr/bin/env',cwd) is not None


def _reviewed_dispatch(script,body,cwd):
    """Exact audited launcher/Prompt/helpers + current registration, not shell parsing."""
    profile=REVIEWED_DISPATCH
    if _digest(body)!=profile['launcher']:return False
    home=Path.home()
    work=re.search(r'^WORK="\$HOME/([^"\n]+)"$',body,re.M)
    if not work or _digest(work[1])!=profile['work'] or str(home/work[1])!=str(cwd):return False
    registry=_path(str(home/'.workflow-registry/registry.json'),cwd)
    unit=_path(str(home/'.config/systemd/user'/(profile['name']+'.service')),cwd)
    if registry is None or unit is None:return False
    try:
        if registry.stat().st_size>1048576 or _digest(unit.read_bytes())!=profile['unit']:return False
        rows=json.loads(registry.read_text()).get('automations',[])
        matches=[r for r in rows if isinstance(r,dict) and r.get('name')==profile['name']]
        if len(matches)!=1:return False
        row=matches[0]
        if (row.get('unit') not in (profile['name']+'.timer',profile['name']+'.service')
                or row.get('kind') not in ('systemd-timer','systemd-manual')
                or os.path.expanduser(row.get('home',''))!=str(cwd)
                or 'codex exec' not in row.get('runtime','')):return False
        candidates={str(Path(script).relative_to(home)),*re.findall(r'\$HOME/([^"$\n]+)',body)}
        found=set()
        for rel in candidates:
            key=_digest(rel)
            if key not in profile['files']:continue
            p=_path(str(home/rel),cwd)
            if p is None or not p.is_file() or not _owned(p) or p.stat().st_mode&0o022 or p.stat().st_size>1048576:return False
            if _digest(p.read_bytes())!=profile['files'][key]:return False
            found.add(key)
        return found==set(profile['files']) and _dispatch_runtime(body,cwd)
    except (OSError,UnicodeError,ValueError,TypeError):return False


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
    if not permanent:return True
    if not path.is_dir():return False
    if time.time()-path.stat().st_mtime>86400:return True
    # Today's explicitly named test/fixture workspaces need not age for a day.
    # A tmp location alone never proves that the owner's files are disposable.
    if not re.search(r'(?:^|[-_.])(?:test|tests|fixture|fixtures)(?:$|[-_.])',path.name):return False
    from . import privacy
    pending=[path];count=0
    while pending:
        parent=pending.pop()
        for child in parent.iterdir():
            count+=1
            if count>256 or _path(str(child),cwd) is None or not _owned(child):return False
            if child.is_dir():pending.append(child)
            elif child.is_file():
                if child.stat().st_size>1048576:return False
                try:
                    if privacy.credentials(child.read_text(),entropy=False):return False
                except (OSError,UnicodeError):return False
            else:return False
    return True


def _write(raw,cwd,state_root):
    path=_path(raw,cwd)
    if path is None:return False
    from . import privacy
    try:
        if path.exists() and (not path.is_file() or path.stat().st_size>1048576 or privacy.credentials(path.read_text(),entropy=False)):return False
    except (OSError,UnicodeError):return False
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
        # The operation is writing an ordinary script, not a privilege tied to
        # yesterday's particular script name. Keep the host launcher protected.
        return bool(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',path.name)) and path.name not in ('agentj','jarvis-host')
    return (path.parent==home/'.config/systemd/user'
            and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*\.(?:service|timer)',path.name) is not None
            and not _host_unit(path.name))


def _host_unit(name):
    # systemctl supplies an implicit .service suffix for a bare name.
    return name.removesuffix('.service') in ('agentj','agentj-serve','agentj-host','agentj-relay') or name.startswith('agentj@')


def _environment(args):
    """Only explicit public values; never inherit/read credentials or startup code."""
    from . import approval_policy, privacy
    if not args:return False
    for arg in args:
        key,equals,value=arg.partition('=')
        if (not equals or not re.fullmatch(r'[A-Za-z_]\w*',key)
                or approval_policy.secret_name(key)
                or key in _STARTUP_ENV
                or key.startswith(('LD_','DYLD_')) or any(c in value for c in '$`\n')
                or privacy.credentials(value,entropy=False)):
            return False
    return True


def _unit_exec(value,cwd):
    """Assess fixed systemd argv and sealed local script bodies, never execute."""
    from . import routine_uv
    home=os.path.expanduser('~')
    value=value.replace('%%','\x00').replace('%h',home).replace('\x00','%')
    if '%' in value:return False
    try:words=shlex.split(value)
    except ValueError:return False
    if not words:return True  # an empty systemd directive resets earlier argv
    if words[0].startswith(('-','+','!','@',':','|')):return False
    script=None;body=None;interpreter=None
    first=os.path.basename(words[0])
    if first in ('sh','bash','python','python3') and len(words)>1 and not words[1].startswith('-'):
        interpreter=first;script=words[1]
    elif '/' in words[0] and not words[0].startswith(('/usr/bin/','/bin/')):
        script=words[0]
    if script is not None:
        p=_path(script,cwd)
        if p is None or not p.is_file() or not _owned(p) or p.stat().st_mode&0o022 or p.stat().st_size>65536:return False
        try:body=p.read_text()
        except (OSError,UnicodeError):return False
        if interpreter is None and len(words)==1 and _reviewed_dispatch(p,body,cwd):return True
        if interpreter is None:
            header=body.splitlines()[0] if body else ''
            if header not in ('#!/bin/sh','#!/bin/bash','#!/usr/bin/bash','#!/usr/bin/python3'):return False
            interpreter=os.path.basename(header[2:])
        # Positional arguments are not a finite path/program proof here.
        if re.search(r'\$[0-9@*]|\$\{[0-9@*]',body):return False
        if interpreter in ('sh','bash'):
            return routine_uv.proven_routine('Bash',{'command':body},cwd=cwd)
        return routine_uv.proven_routine('Bash',{'command':interpreter+' -c '+shlex.quote(body)},cwd=cwd)
    return routine_uv.proven_routine('Bash',{'command':shlex.join(words)},cwd=cwd)


def _unit_body(body,cwd,*,execute=False):
    from . import approval_policy,privacy,uv_effect
    if privacy.credentials(body,entropy=False):return False
    if execute:
        for line in body.splitlines():
            if line.strip().startswith('WorkingDirectory='):
                directory=line.strip().partition('=')[2].replace('%h',os.path.expanduser('~'))
                cwd=_path(directory,cwd)
                if cwd is None:return False
    for line in body.splitlines():
        key,eq,value=line.strip().partition('=')
        if not eq or key.startswith(('#',';')):continue
        if execute and key in ('Requires','Wants','Requisite','BindsTo','Upholds','OnFailure','OnSuccess','Unit',
                'RootDirectory','RootImage','ExecSearchPath','User','Group','DynamicUser','PrivateUsers',
                'BindPaths','BindReadOnlyPaths','TemporaryFileSystem') and value:return False
        if key in ('Environment','PassEnvironment','EnvironmentFile','LoadCredential','LoadCredentialEncrypted','SetCredential','SetCredentialEncrypted'):
            if key!='Environment':return False
            import shlex
            try:values=shlex.split(value)
            except ValueError:return False
            if not _environment(values):return False
        if key.startswith('Exec') and (approval_policy.shows_secret(value)
                or uv_effect.assess('Bash',{'command':value},cwd=cwd).reason is not None
                or execute and not _unit_exec(value,cwd)):return False
    return True


def _unit_roots():
    """Native complete load order, plus live manager overrides and XDG roots."""
    from . import fence
    result=subprocess.run(['/usr/bin/systemd-analyze','--user','unit-paths'],capture_output=True,text=True,timeout=5)
    roots=result.stdout.splitlines() if result.returncode==0 and len(result.stdout)<65536 else []
    if not roots or len(roots)>128 or any(not p.startswith('/') for p in roots):raise OSError('unverified unit paths')
    home=Path(os.path.expanduser('~'))
    # Keep legacy/default roots even when the current process has XDG overrides;
    # the running user manager may have inherited different XDG values.
    roots.extend([str(home/'.config/systemd/user'),str(home/'.local/share/systemd/user')])
    manager=fence._manager_unit_paths(os.environ)
    roots.extend(manager)
    # Distribution load-root aliases (e.g. /etc/xdg/systemd/user) are part of
    # the native path result; bind their resolved destinations on every check.
    # Individual unit and drop-in aliases still fail the no-link file proof.
    roots=tuple(dict.fromkeys(str(Path(p).resolve()) for p in roots))
    snapshots=_UNITS.get()
    if snapshots is not None:snapshots.append(('paths',roots))
    return roots,bool(manager)


def _loaded_unit(target,roots):
    result=subprocess.run(['/usr/bin/systemctl','--user','show',target,
        '--property=Id,LoadState,FragmentPath,DropInPaths,NeedDaemonReload'],capture_output=True,text=True,timeout=5)
    if result.returncode or len(result.stdout)>65536:return False
    props=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if props.get('NeedDaemonReload')=='yes':return False
    if props.get('LoadState') not in ('loaded','not-found'):return False
    if props.get('LoadState')=='loaded':
        if props.get('Id')!=target or props.get('FragmentPath') not in [str(Path(root)/target) for root in roots]:return False
        if any(not any(Path(root) in Path(p).parents for root in roots) for p in props.get('DropInPaths','').split()):return False
    snapshots=_UNITS.get()
    if snapshots is not None:snapshots.append((target,tuple(sorted(props.items()))))
    return props


def _ordinary_unit(name,cwd,*,execute=True):
    """Service transport cannot hide an explicitly sensitive service operand."""
    if (_host_unit(name) or re.search(r'(?:^|[-_.@])(?:vault|keyring|secret|secrets|credential|credentials|auth|password|token)(?:$|[-_.@])',name,re.I)):
        return False
    unit=name if name.endswith(('.service','.timer')) else name+'.service'
    units=[unit,unit[:-6]+'.service'] if unit.endswith('.timer') else [unit]
    try:roots,live=_unit_roots()
    except (OSError,subprocess.SubprocessError):return False
    for target in units:
        found=False
        loaded=_loaded_unit(target,roots) if live else {}
        if live and not loaded:return False
        for root_name in roots:
            root=Path(root_name)
            files=[root/target]
            files.extend(Path(p) for p in loaded.get('DropInPaths','').split() if root in Path(p).parents)
            # systemd also loads service.d and dash-prefix drop-ins. Templates
            # and linked/aliased files need their own binding; abstain here.
            if '@' in target:return False
            prefixes=[target+'.d',target.rsplit('.',1)[-1]+'.d']
            stem=target.rsplit('.',1)[0]
            prefixes.extend(stem[:i+1]+'.'+target.rsplit('.',1)[1]+'.d' for i,c in enumerate(stem) if c=='-')
            for prefix in prefixes:
                dropins=root/prefix
                if _path(str(dropins),cwd) is None:return False
                if dropins.exists():files.extend(dropins.glob('*.conf'))
            if len(files)>32:return False
            for file in files:
                if _path(str(file),cwd) is None:return False
                if not file.exists():continue
                if file==root/target:found=True
                if not file.is_file() or file.stat().st_size>1048576:return False
                try:body=file.read_text()
                except (OSError,UnicodeError):return False
                if not _unit_body(body,os.path.expanduser('~'),execute=execute):return False
        if execute and not found:return False
    return True


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
        raw=inp.get('file_path',inp.get('path'))
        if isinstance(raw,str) and raw.endswith(('.service','.timer')) and not _unit_body(body,cwd):return False
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
            if any(p.endswith(('.service','.timer')) for p in command.redirs) and not _unit_body(body,cwd):return False
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
            if rest[:1]==['set-environment']:
                if not _environment(rest[1:]):return False
                saw=True;continue
            if not rest or rest[0] not in ('stop','start','restart','enable','disable','status','is-active'):return False
            units=[a for a in rest[1:] if a!='--now']
            executes=rest[0] in ('start','restart') or rest[0]=='enable' and '--now' in rest
            if not units or not all(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*',u) and _ordinary_unit(u,cwd,execute=executes) for u in units):return False
            saw=True;continue
        if name not in ('systemd-run','codex','sleep','date','printf','echo','true','test','head','stat','ls') or not routine_uv.proven_routine('Bash',{'command':piece},cwd=cwd):return False
        saw=True
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


def qualify(tool,inp,*,cwd=None,state_root=None,_native_scratch=None):
    if _native_scratch is not None:
        from . import native_scratch
        if type(_native_scratch) is not native_scratch.Context:return None
        try:
            proof=native_scratch.qualify(tool,inp,cwd,_native_scratch)
            if proof is not None:return ('native-scratch-prefix-v1',proof)
            if _native_scratch.background is not None:return None
        except (OSError,ValueError,TypeError):pass
    paths=set();token=_PATHS.set(paths);units=[];unit_token=_UNITS.set(units)
    try:
        if not proven(tool,inp,cwd=cwd,state_root=state_root):return None
        seal=_seal(paths)
        if seal is None or not proven(tool,inp,cwd=cwd,state_root=state_root) or _seal(paths)!=seal:return None
        return (tuple(sorted(paths)),seal,tuple(units),tool,inp,cwd,state_root)
    except (OSError,UnicodeError,ValueError,subprocess.SubprocessError):return None
    finally:
        _PATHS.reset(token);_UNITS.reset(unit_token)


def still_valid(proof):
    if proof[0]=='native-scratch-prefix-v1':
        from . import native_scratch
        return native_scratch.still_valid(proof[1])
    if _seal(proof[0])!=proof[1]:return False
    current=qualify(proof[3],proof[4],cwd=proof[5],state_root=proof[6])
    return current is not None and current[:3]==proof[:3]
