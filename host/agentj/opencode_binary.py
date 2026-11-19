"""Select and bind an installed OpenCode image to one real model check and its later native launch."""
from pathlib import Path
import os
import re
import stat
import sys
import tempfile
import subprocess
import time

VERSION = re.compile(r'(?:opencode\s+)?v?(\d+)\.(\d+)\.(\d+)',re.I)
SOURCES = ('explicit','mise','asdf','native','system','npm')
LIMIT = 16
# Populated only by the fixed image child after its sealed-mount check. Host-side resolution never trusts unmapped UIDs.
_CHILD_FILES = frozenset()
SYSTEM = ('/usr/bin','/usr/local/bin','/opt/homebrew/bin',os.path.join(os.sep,'home','linuxbrew','.linuxbrew','bin'))


def _identity(s):
    return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]


def safe_path(path):
    if not path.is_absolute() or '..' in path.parts or len(os.fsencode(path)) > 1024 or any(ord(c)<32 for c in str(path)):
        return False
    try:
        if _CHILD_FILES and tuple(_identity(path.stat())) in _CHILD_FILES:
            return True
        for parent in (*reversed(path.parents),path):
            s = parent.stat()
            sticky = parent != path and s.st_uid == 0 and s.st_mode & stat.S_ISVTX
            if s.st_uid not in (0,os.getuid()) or (s.st_mode & 0o022 and not sticky):
                return False
        s=path.stat()
        return stat.S_ISREG(s.st_mode) and bool(s.st_mode & 0o111)
    except OSError:
        return False


def _source(path,home,env,explicit):
    for source,root in (('mise',Path(env.get('MISE_DATA_DIR',home/'.local/share/mise'))),
                        ('asdf',Path(env.get('ASDF_DATA_DIR',home/'.asdf')))):
        try:
            rel = path.relative_to(root.resolve()/'installs/opencode')
        except ValueError:
            continue
        if len(rel.parts) in (2,3) and re.fullmatch(r'v?\d+\.\d+\.\d+',rel.parts[0]) and rel.parts[1:] in (('opencode',),('bin','opencode')):
            return source
    # Official npm postinstall places a native executable here (bin/opencode is its symlink).
    prefixes = [*(Path(d).parent for d in SYSTEM), home/'.local',home/'.npm-global']
    for prefix in prefixes:
        if path == prefix/'lib/node_modules/@opencode/cli/bin/opencode.exe':
            return 'npm'
    if path in (home/'.opencode/bin/opencode',home/'.local/bin/opencode'):
        return 'native'
    if path.name == 'opencode' and path.parent.as_posix() in SYSTEM:
        return 'system'
    for prefix in prefixes:
        try:
            relative = path.relative_to(prefix/'Cellar/opencode')
        except ValueError:
            continue
        if len(relative.parts)==3 and re.fullmatch(r'\d+\.\d+\.\d+(?:_\d+)?',relative.parts[0]) and relative.parts[1:] == ('bin','opencode'):
            return 'system'
    return 'explicit' if explicit == path else None


def candidates(env,project):
    """Never execute PATH shadowing binaries, symlinks out of trusted installs or working-tree tools."""
    from . import binaries
    home=Path(env.get('HOME') or Path.home()).resolve()
    work=Path(project).resolve()
    raw=env.get('AGENTJ_OPENCODE_BIN') or env.get('AGENTJARVIS_OPENCODE_BIN')
    explicit=Path(raw).expanduser().resolve() if raw and Path(raw).expanduser().is_absolute() else None
    items={}
    # Service PATH may omit the shell's global npm bin or ~/.local/bin.
    env = {**env,'PATH':os.pathsep.join([env.get('PATH',''),str(home/'.local/bin'),str(home/'.npm-global/bin'),*SYSTEM])}
    for entry in binaries.installations('opencode',env):
        original=Path(entry)
        try:
            path=original.resolve()
        except (OSError,RuntimeError):
            continue
        if path in items or not safe_path(path) or not safe_path(original):
            continue
        source=_source(path,home,env,explicit)
        if not source or (any(p.is_relative_to(work) for p in (original,path)) and (work != home or source == 'explicit')):
            continue
        if work == home and original != path and original.is_relative_to(work) and not (
                _source(original,home,env,explicit) or original.parent in (home/'.local/bin',home/'.npm-global/bin')):
            continue
        if binaries.wrapper(path):
            continue
        if source != 'explicit':
            try:
                with path.open('rb') as image:
                    magic=image.read(4)
                if magic not in (b'\x7fELF',b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe',b'\xca\xfe\xba\xbf'):
                    continue
            except OSError:
                continue
        items[path]=(source,path)
    if explicit in items and items[explicit][0] == 'explicit':
        return [items[explicit]]
    def order(item):
        source,path = item
        managed = source in ('mise','asdf')
        rank = next((VERSION.fullmatch(p) for p in path.parts if VERSION.fullmatch(p)),None) if managed else None
        return (not managed,tuple(map(int,rank.groups())) if rank else (),str(path))
    return sorted(items.values(),key=order,reverse=True)[:LIMIT]


def record(path,version,source):
    return {'path':str(path),'version':version,'source':source,'identity':_identity(path.stat())}


def valid(pin):
    if not isinstance(pin,dict) or not isinstance(pin.get('path'),str) or not isinstance(pin.get('version'),str):
        return False
    if pin.get('source') not in SOURCES or not VERSION.fullmatch(pin['version']) or not (
            isinstance(pin.get('identity'),list) and len(pin['identity'])==5 and all(type(i) is int and i>=0 for i in pin['identity'])):
        return False
    try:
        path=Path(pin['path'])
        return path==path.resolve() and safe_path(path) and _identity(path.stat())==pin['identity']
    except (OSError,ValueError,RuntimeError):
        return False


def argv(pin,args):
    if not valid(pin):
        raise ValueError('binary_changed')
    return [sys.executable,'-I',__file__,pin['path'],','.join(map(str,pin['identity'])),*args]


def version(path,source,launch=None):
    """A dedicated metadata-only invocation cannot inherit the provider key, real HOME, or working directory."""
    try:
        pin=record(path,'0.0.0',source)
        with tempfile.TemporaryDirectory(prefix='agentj-oc-version-') as root:
            env={'HOME':root,'PATH':os.defpath,'LANG':'C'}
            command=argv(pin,['--version'])
            if launch and launch[1].get('fence',True):
                from . import fence
                if fence.problem(launch[0],root):
                    from .opencode_free import ProbeError
                    raise ProbeError('fence_unavailable')
                command=fence.wrap(launch[0],command,root,allow_docker=bool(launch[1].get('docker')))
                env=fence.launch_environment(env,bool(launch[1].get('docker')),'opencode')
            proc=subprocess.run(command,env=env,cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,text=True,timeout=3)
        match=VERSION.fullmatch(proc.stdout.strip()) if proc.returncode==0 else None
        if match and valid(pin):
            return {**pin,'version':'.'.join(match.groups())}
    except (OSError,ValueError,subprocess.TimeoutExpired):
        pass
    return None


def run(st,cfg,model,timeout):
    from . import agent_opencode2,opencode_free,proxy
    from .opencode_start import _prefs
    ready=[]
    rows=[]
    results={'name':'opencode','installed':False,'supported':True,'logged_in':None,'callable':False,'binary_candidates':rows}
    for source,path in candidates(os.environ,cfg['dir']):
        results['installed']=True
        try:
            pin=version(path,source,launch=(st,cfg))
        except opencode_free.ProbeError:
            results['probe_reason']='fence_unavailable'
            return results
        item={'path':str(path),'source':source,'version':pin['version'] if pin else None,'reason':'version_unavailable'}
        rows.append(item)
        if pin:
            rank=tuple(int(v) for v in pin['version'].split('.'))
            if rank[0] in (1,2):
                ready.append((rank,pin,item))
                item['reason']='not_attempted'
            else:
                item['reason']='unsupported_version'
    ready.sort(key=lambda v:v[0], reverse=True)
    rows.sort(key=lambda r:tuple(int(n) for n in (r['version'] or '0').split('.')),reverse=True)
    from . import provider_runtime, olares_probe
    env=dict(os.environ)
    if cfg.get('_agent_plan') and not olares_probe.inside():
        env=provider_runtime.fresh_environment(env,extra=('ARK_AGENT_PLAN_API_KEY',))
    env=proxy.environment(env,_prefs())
    end=time.monotonic()+timeout
    why=('unsupported_version' if any(r['reason']=='unsupported_version' for r in rows) else 'version_unavailable') if rows else 'no_trusted_binary'
    for index,(rank,pin,item) in enumerate(ready):
        budget=(end-time.monotonic()) / min(2,len(ready)-index)
        if budget <= 0:
            break
        try:
            native=agent_opencode2.probe if rank[0]==2 else opencode_free.probe
            reply=native(pin['path'],model,env,budget,launch=(st,{**cfg,'opencode_binary':pin}),waits=())
            if not isinstance(reply,str) or not reply.strip():
                raise opencode_free.ProbeError('empty_probe_reply')
            if not valid(pin):
                raise opencode_free.ProbeError('binary_changed')
            item['reason']='selected'
            for next_rank,_,other in ready[index+1:]:
                other['reason']='older_version' if next_rank<rank else 'not_attempted'
            results.update(callable=True,version=pin['version'],executable=pin['path'],executable_source=pin['source'],
                           opencode_binary=pin,start_checked=round(timeout-max(0,end-time.monotonic())))
            return results
        except opencode_free.ProbeError as err:
            reason=err.args[0]
            why=item['reason']=reason
            results.update(version=pin['version'],executable=pin['path'],executable_source=pin['source'])
            if reason in ('provider_key','quota_exhausted','rate_limited','ark_route_conflict','model_missing','fence_unavailable'):
                break
    results['probe_reason']=why
    return results


def execute():
    path=Path(sys.argv[1])
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        if _identity(os.fstat(fd)) != [int(i) for i in sys.argv[2].split(',')]:
            raise SystemExit(126)
        # Kernel executes the opened image even if the original install is replaced in the meantime.
        os.set_inheritable(fd,True)
        command=[str(path),*sys.argv[3:]]
        if os.execve in os.supports_fd:
            os.execve(fd,command,os.environ)
        if sys.platform == 'darwin':
            # Darwin's volfs names this vnode directly by volume ID and file ID. Keeping
            # fd open prevents inode reuse even if the package manager replaces the source.
            info = os.fstat(fd)
            vnode = f'/.vol/{info.st_dev}/{info.st_ino}'
            if _identity(os.stat(vnode)) == _identity(info):
                os.execve(vnode,command,os.environ)
        raise SystemExit(126)
    finally:
        os.close(fd)


if __name__=='__main__':
    execute()
