"""Paired signed application action; recoverable move, no permanent deletion.

All paths and registry bytes stay host-private. The existing phone-card API owns
signatures/nonce/TTL; a local host-signed journal can only finish or roll back that
exact directory move after interruption.
"""
from __future__ import annotations

import asyncio
import hashlib
import functools
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
import time

from . import wire
from .plaza import PlazaError

NAME = re.compile(r'[a-z0-9][a-z0-9-]{1,39}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
MAX_REGISTRY = 1024 * 1024
DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
JOURNAL = 'uninstall-journal.json'
DOMAIN = b'agentj-plaza-uninstall-journal-v1\0'


class Refused(Exception):
    pass


def _fixed_boundary(fn):
    @functools.wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except PlazaError:
            raise Refused('target_changed') from None
        except OSError:
            raise Refused('local_state_unavailable') from None
    return call


def _node_ident(path, dir_fd=None):
    row = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    return [row.st_dev, row.st_ino]


def _restore_noreplace(bfd, backup, pfd, leaf):
    # A rollback must never replace a directory that appeared after the move.
    # Unsupported kernels retain the backup and signed recovery journal instead.
    if not sys.platform.startswith('linux'):
        raise Refused('recovery_required')
    try:
        import ctypes
        fn = ctypes.CDLL(None, use_errno=True).renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        if fn(bfd, os.fsencode(backup), pfd, os.fsencode(leaf), 1) != 0:
            raise Refused('recovery_required')
        os.fsync(pfd);os.fsync(bfd)
    except (OSError, AttributeError):
        raise Refused('recovery_required') from None


def _write_journal(st, fd, journal):
    payload = _encode(journal)
    _atomic(fd,JOURNAL,_encode({'payload':wire.b64u(payload),
        'signature':wire.b64u(st.signing_key().sign(DOMAIN+payload))}))


def _reject_moved(st, fd, pfd, bfd, journal):
    # Bind the object actually moved before attempting rollback. If restoration
    # conflicts or is interrupted, later recovery must not assume it was the approved inode.
    journal = {**journal, 'rejected_move':True, 'rollback_ident':_node_ident(journal['backup'],bfd)}
    _write_journal(st,fd,journal)
    _restore_noreplace(bfd,journal['backup'],pfd,journal['leaf'])
    if _node_ident(journal['leaf'],pfd) != journal['rollback_ident']:
        raise Refused('recovery_ambiguous')
    os.unlink(JOURNAL,dir_fd=fd);os.fsync(fd)
    raise Refused('target_changed')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _atomic(fd, name, data):
    temp = '.uninstall-' + secrets.token_hex(12)
    out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(out, 'wb', closefd=False) as stream:
            stream.write(data);stream.flush();os.fsync(out)
        os.rename(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(out)
        try: os.unlink(temp, dir_fd=fd)
        except FileNotFoundError: pass


def _read(fd, name):
    leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        row = os.fstat(leaf)
        if not stat.S_ISREG(row.st_mode) or row.st_nlink != 1 or row.st_size > MAX_REGISTRY:
            raise Refused('invalid_registry')
        with os.fdopen(leaf, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_REGISTRY + 1)
        if len(raw) > MAX_REGISTRY: raise Refused('invalid_registry')
        return raw
    finally:
        os.close(leaf)


def _state(st):
    root = os.open(st.root, DIR)
    try:
        return os.open('plaza', DIR, dir_fd=root)
    finally:
        os.close(root)


def _registry(fd):
    try: raw = _read(fd, 'installed.json')
    except FileNotFoundError: raw = b'{"items":[]}'
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('items'), list) or len(data['items']) > 2000:
            raise ValueError
        if not isinstance(data.get('uninstalled', []), list): raise ValueError
        return raw, data
    except (ValueError, UnicodeError, TypeError):
        raise Refused('invalid_registry') from None


def _ident(path, dir_fd=None):
    row = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISDIR(row.st_mode) or row.st_uid != os.getuid(): raise Refused('target_changed')
    return [row.st_dev, row.st_ino]


def _backup(fd):
    try: os.mkdir('backups', 0o700, dir_fd=fd)
    except FileExistsError: pass
    out = os.open('backups', DIR, dir_fd=fd)
    if os.fstat(out).st_mode & 0o077:
        os.close(out);raise Refused('unsafe_backup')
    return out


def _tree(target, paths, *, dir_fd=None):
    from . import plaza_deps, bundle
    if (not isinstance(paths, list) or not paths or len(paths) > 2000
            or any(not isinstance(p, str) or not p or PurePosixPath(p).is_absolute()
                   or '..' in PurePosixPath(p).parts or '\\' in p for p in paths)):
        raise Refused('ownership_unverified')
    content, total = {}, 0
    root = os.open(target,DIR,dir_fd=dir_fd)
    def walk(fd, prefix=''):
        nonlocal total
        with os.scandir(fd) as entries:
            for entry in entries:
                relative = prefix+entry.name
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name,DIR,dir_fd=fd)
                    try: walk(child,relative+'/')
                    finally: os.close(child)
                elif stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1:
                    total += info.st_size
                    if info.st_size > bundle.MAX_FILE or total > bundle.MAX_TOTAL or len(content)>=2000:
                        raise Refused('target_changed')
                    leaf = os.open(entry.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                    try:
                        before = os.fstat(leaf)
                        if (before.st_dev,before.st_ino)!=(info.st_dev,info.st_ino) or not stat.S_ISREG(before.st_mode):
                            raise Refused('target_changed')
                        with os.fdopen(leaf,'rb',closefd=False) as stream:
                            data=stream.read(bundle.MAX_FILE+1)
                        after=os.fstat(leaf)
                        if len(data)>bundle.MAX_FILE or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                            raise Refused('target_changed')
                        content[relative]=data
                    finally: os.close(leaf)
                else:
                    raise Refused('target_changed')
    try: walk(root)
    finally: os.close(root)
    if sorted(content)!=sorted(paths): raise Refused('target_changed')
    return plaza_deps.tree_sha(content)


def _recover(st, fd):
    from . import market_guard
    try: wrapper = json.loads(_read(fd, JOURNAL))
    except FileNotFoundError: return
    try:
        raw = wire.unb64u(wrapper['payload'])
        st.signing_key().public_key().verify(wire.unb64u(wrapper['signature']), DOMAIN + raw)
        journal = json.loads(raw)
        if journal.get('schema') != 1: raise ValueError
        current, _ = _registry(fd)
        pfd = market_guard.open_pinned(journal['pin'])
        bfd = _backup(fd)
        try:
            try: source = _node_ident(journal['leaf'], pfd)
            except FileNotFoundError: source = None
            try: backup = _node_ident(journal['backup'], bfd)
            except FileNotFoundError: backup = None
            if journal.get('rejected_move') is True:
                moved=journal.get('rollback_ident')
                if _sha(current)!=journal['before']: raise Refused('recovery_ambiguous')
                if source is None and backup==moved:
                    _restore_noreplace(bfd,journal['backup'],pfd,journal['leaf'])
                elif source==moved and backup is None:
                    pass
                else: raise Refused('recovery_required')
            elif _sha(current) == journal['after'] and source is None and backup == journal['ident']:
                pass  # registry and durable move were committed before interruption
            elif _sha(current) == journal['before']:
                if source == journal['ident'] and backup is None:
                    pass  # journal was written but no move occurred
                elif source is None and backup == journal['ident']:
                    _restore_noreplace(bfd,journal['backup'],pfd,journal['leaf'])
                else: raise Refused('recovery_ambiguous')
            else: raise Refused('recovery_ambiguous')
            os.unlink(JOURNAL, dir_fd=fd);os.fsync(fd)
        finally:
            os.close(pfd);os.close(bfd)
    except Refused: raise
    except Exception:
        raise Refused('recovery_unverified') from None


@_fixed_boundary
def prepare(st, name, harness=None, *, recover=True):
    from . import market, market_guard
    if not isinstance(name, str) or not NAME.fullmatch(name) or (harness is not None and harness not in market.HARNESSES):
        raise Refused('bad_request')
    with st.file_lock('plaza-installed'):
        try: fd = _state(st)
        except FileNotFoundError: raise Refused('not_installed') from None
        try:
            if recover:
                _recover(st, fd)
            else:
                try: _read(fd,JOURNAL)
                except FileNotFoundError: pass
                else: raise Refused('recovery_required')
            raw, data = _registry(fd)
            rows = [r for r in data['items'] if isinstance(r, dict) and r.get('name') == name
                    and (harness is None or r.get('harness') == harness)]
            if not rows:
                removed = any(r.get('name') == name and (harness is None or r.get('harness') == harness)
                              for r in data.get('uninstalled', []) if isinstance(r, dict))
                raise Refused('already_uninstalled' if removed else 'not_installed')
            if len(rows) != 1: raise Refused('ambiguous_install')
            record = rows[0]
            if record.get('type') not in ('skill', 'workflow') or record.get('harness') not in market.HARNESSES:
                raise Refused('unsupported_package')
            if not isinstance(record.get('sha256'), str) or not HEX.fullmatch(record['sha256']):
                raise Refused('ownership_unverified')
            verify = record.get('verify') or {}
            if not isinstance(verify, dict) or not isinstance(verify.get('files'), str):
                raise Refused('ownership_unverified')
            raw_target = record.get('target')
            if not isinstance(raw_target, str) or not Path(raw_target).is_absolute() or '..' in Path(raw_target).parts:
                raise Refused('ownership_unverified')
            target, pin = market_guard.pin_target(raw_target)
            if str(target) != raw_target or target.is_symlink(): raise Refused('target_changed')
            ident = _ident(target)
            if _tree(target, verify.get('paths')) != verify['files']:
                raise Refused('target_changed')
            return {'record': record, 'registry': _sha(raw), 'target': str(target), 'pin': pin, 'ident': ident,
                    'files': verify['files'], 'backup': name+'-uninstall-'+secrets.token_hex(12)}
        finally:
            os.close(fd)


@_fixed_boundary
def commit(st, snapshot, live):
    from . import market_guard
    with st.file_lock('plaza-installed'):
        try: fd = _state(st)
        except FileNotFoundError: raise Refused('not_installed') from None
        try:
            _recover(st, fd)
            raw, data = _registry(fd)
            record = snapshot['record']
            if _sha(raw) != snapshot['registry'] or record not in data['items'] or not live():
                raise Refused('authority_changed')
            pfd = market_guard.open_pinned(snapshot['pin'])
            bfd = _backup(fd)
            try:
                target = Path(snapshot['target'])
                if _ident(target.name, pfd) != snapshot['ident'] or _tree(target, record['verify']['paths']) != snapshot['files']:
                    raise Refused('target_changed')
                if os.stat('.',dir_fd=pfd).st_dev != os.fstat(bfd).st_dev:
                    raise Refused('backup_other_filesystem')
                backup_path = str(st.root/'plaza/backups'/snapshot['backup'])
                data['items'] = [r for r in data['items'] if r != record]
                data.setdefault('uninstalled', []).append({**record, 'status':'uninstalled', 'backup':backup_path})
                after = _encode(data)
                if len(after) > MAX_REGISTRY: raise Refused('registry_full')
                journal = {'schema':1, 'before':_sha(raw), 'after':_sha(after), 'pin':snapshot['pin'],
                           'leaf':target.name, 'backup':snapshot['backup'], 'ident':snapshot['ident']}
                _write_journal(st,fd,journal)
                if not live(): raise Refused('authority_changed')
                if _ident(target.name,pfd) != snapshot['ident']: raise Refused('target_changed')
                os.rename(target.name,snapshot['backup'],src_dir_fd=pfd,dst_dir_fd=bfd)
                os.fsync(pfd);os.fsync(bfd)
                try:
                    matches = (_ident(snapshot['backup'],bfd)==snapshot['ident']
                               and _tree(snapshot['backup'],record['verify']['paths'],dir_fd=bfd)==snapshot['files'])
                except (OSError,Refused):
                    matches = False
                if not matches:
                    _reject_moved(st,fd,pfd,bfd,journal)
                if not live(): raise Refused('authority_changed')
                _atomic(fd,'installed.json',after)
                os.unlink(JOURNAL,dir_fd=fd);os.fsync(fd)
                st.log('plaza_pkg_uninstall',result='ok')
                return {'ok':True,'rc':0,'why':'uninstalled','out':'已卸载并保留可恢复备份 / Uninstalled; recoverable backup retained.'}
            except Exception:
                _recover(st,fd)
                raise
            finally:
                os.close(pfd);os.close(bfd)
        finally:
            os.close(fd)


async def handle(host, req, emit=None):
    from . import application_ingress, plaza_perms
    from .peer_guard import SecretIndex
    gate = plaza_perms.turn_gate(host)
    if gate['gated'] or not application_ingress.allowed(host) or host.stopped() or host.stopping.is_set():
        return {'ok':False,'rc':3,'why':'source_refused','out':'未卸载：来源或主机状态不可用 / Not uninstalled: source or host unavailable.'}
    host._task_id()
    agent, epoch, authority = host.agent, str(host.hist.epoch), host._approval_live_authority()
    request = application_ingress.current(host)
    try:
        snapshot = await asyncio.to_thread(prepare,host.st,req.get('name'),req.get('harness'),recover=req.get('preview') is not True)
        index = await asyncio.to_thread(SecretIndex.build,workdir=(host.agent_cfg or {}).get('dir'),fragment_size=8)
        await asyncio.to_thread(index.require_complete)
        record = snapshot['record']
        hidden = plaza_perms.Hider(host.lang=='en',index)
        target = hidden(snapshot['target'],4096,literal=False,path=True)
        backup = hidden(str(host.st.root/'plaza/backups'/snapshot['backup']),4096,literal=False,path=True)
        name = hidden(record['name'],80)
        version = hidden(record.get('version') or '?',80)
        info = {'operation':'uninstall','package':name,'version':version,'harness':record['harness'],
                'sha256':record['sha256'],'files_sha256':snapshot['files'],'registry_sha256':snapshot['registry'],
                'target':target,'backup':backup,'target_ref':secrets.token_urlsafe(32),
                'effect':'移到可恢复备份；保留发布方信任和安装历史，重装仍需正常签名确认。 / Move to recoverable backup; keep publisher trust and install history; reinstall needs normal signed confirmation.'}
        if req.get('preview') is True:
            return {'ok':True,'rc':0,'why':'preview','preview':info,'out':'仅预览，尚未卸载 / Preview only; not uninstalled.'}
        answer = await plaza_perms.signed_card(host,name,info,emit,gate)
        proof = answer.get('proof') or {}
        from . import approvals
        records = await asyncio.to_thread(lambda:[r for r in approvals.read_log(host.st) if r.get('id')==proof.get('rid')])
        proof_ok = (len(records)==1 and records[0].get('device')==proof.get('device')
                    and records[0].get('decision')=='allow' and records[0].get('reason')=='device'
                    and records[0].get('tool')=='agentj plaza'
                    and records[0].get('input_sha256')==approvals.input_digest(info)
                    and approvals.check_record(host.st,records[0])=='ok')
        until = time.monotonic()+min(30,host.ask_ttl)
        envelope = proof.get('envelope') or {}
        def live():
            return (proof_ok and time.monotonic()<=until
                    and (not envelope or envelope.get('expires_at',0)>=time.time())
                    and request is not None and request.worker_live() and host.agent is agent and str(host.hist.epoch)==epoch
                    and host._approval_live_authority()==authority and not host.stopped() and not host.stopping.is_set()
                    and answer.get('ok') is True and proof.get('device') and proof.get('sign_pub')
                    and host.st.sign_key(proof['device'])==proof['sign_pub'])
        if not answer.get('ok') or not proof.get('device'):
            raise Refused(answer.get('why') or 'no_signature')
        return await asyncio.to_thread(commit,host.st,snapshot,live)
    except Refused as exc:
        reason = str(exc)
    except (PlazaError, OSError, ValueError, TypeError):
        reason = 'local_state_unavailable'
    host.st.log('plaza_pkg_uninstall',result='refused',reason=reason)
    return {'ok':False,'rc':3,'why':reason,'out':'未卸载 / Not uninstalled: '+reason}


def run(name, *, harness=None, preview=False, st=None, out=None):
    import sys
    from . import market, plaza_perms, names
    from .state import State
    try:
        result = plaza_perms._call(st or State(),{'cmd':'plaza_uninstall','name':name,'harness':harness,'preview':preview},market.REQUEST_WAIT)
    except names.ServeBusy:
        result={'rc':3,'out':'结果未确认；先查看已装清单，不自动重试 / Outcome unknown; inspect installed packages before retrying.'}
    if result is None:
        result={'rc':3,'out':'未卸载：请先启动 Agent J 主机 / Not uninstalled: start the Agent J host first.'}
    print(result.get('out',''),file=out or sys.stdout)
    if result.get('preview'): print(json.dumps(result['preview'],ensure_ascii=False),file=out or sys.stdout)
    return result.get('rc',3)
