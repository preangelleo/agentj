"""Host-owned creation of disabled tasks; transfer limits are declarations, never runtime grants."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

TOOL = 'agentj tasks create'
MAX_BYTES = 16000
MAX_PROMPT = 8000
LIMIT = re.compile(r'(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,8})?\Z')
CURRENCY = re.compile(r'[A-Z][A-Z0-9]{1,11}\Z')


class Refused(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def contract(value):
    from . import taskspec, tasks, privacy
    raw = canonical(value)
    if len(raw) > MAX_BYTES or not isinstance(value, dict) or set(value) not in ({'task', 'prompt'}, {'task', 'prompt', 'large_transfer'}):
        raise Refused('invalid_contract')
    task, prompt = copy.deepcopy(value['task']), value['prompt']
    if (taskspec.problems(task) or task.get('steps') or task.get('enabled') is not False
            or task['prompt_file'] != 'PROMPT.md' or task['dry_run_prompt_file'] != 'PROMPT.md'
            or not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > MAX_PROMPT or '\x00' in prompt):
        raise Refused('invalid_contract')
    transfer = value.get('large_transfer')
    if 'large_transfer' in value:
        if (not isinstance(transfer, dict) or set(transfer) != {'kind','limit','currency'}
                or transfer['kind'] not in ('bank','crypto') or not isinstance(transfer['limit'], str)
                or not LIMIT.fullmatch(transfer['limit']) or not any(c in '123456789' for c in transfer['limit'])
                or not isinstance(transfer['currency'], str) or not CURRENCY.fullmatch(transfer['currency'])):
            raise Refused('invalid_transfer_declaration')
        prompt += ('\n\n## Owner-approved transfer declaration / 主人批准的转账声明\n'
                   + transfer['limit'] + ' ' + transfer['currency'] + ' (' + transfer['kind'] + ')\n'
                   'This limit is informational, not enforced. Agent J does not monitor, verify amounts or intercept runtime transfers.\n'
                   '金额上限仅为说明，不是强制预算；Agent J 不在运行期监控、核实金额或拦截转账。\n')
    if any(privacy.secret_at(text) is not None for text in (raw.decode(), *_strings(value), prompt)):
        raise Refused('secret_in_contract')
    task_raw, prompt_raw = canonical(task) + b'\n', prompt.encode()
    return {'id': task['id'], 'task': task, 'task_raw': task_raw, 'prompt_raw': prompt_raw,
            'large_transfer': copy.deepcopy(transfer), 'tsha': tasks.contract_sha(task_raw, prompt_raw)}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _scan(root, value, prepared):
    from . import peer_guard
    index = peer_guard.SecretIndex.build(workdir=root, fragment_size=8)
    index.require_complete()
    # JSON escaping changes secret windows. Check the actual user strings and
    # final rendered prompt too, using this same complete, fresh source index.
    for text in (canonical(value).decode(), *_strings(value), prepared['prompt_raw'].decode()):
        if not peer_guard.outbound(text, index).ok:
            raise Refused('secret_in_contract')
    index.require_complete()



def _write_ceiling(host, root):
    """Current native thread facts, not caller labels or a UV permission upgrade."""
    from . import codex_perm
    actor = host.agent
    cfg = getattr(actor, 'cfg', None)
    thread = getattr(actor, 'thread', None)
    proc = getattr(actor, 'proc', None)
    tid = getattr(actor, 'tid', None)
    if (proc is None or getattr(proc, 'returncode', 1) is not None or not isinstance(tid, str) or not tid
            or getattr(actor, 'kind', None) != 'codex' or not isinstance(cfg, dict)
            or not isinstance(cfg.get('dir'), str) or os.path.abspath(cfg['dir']) != root or getattr(actor, 'research', False)
            or not isinstance(thread, dict)):
        raise Refused('native_write_unverified')
    sandbox = thread.get('sandbox')
    mode = codex_perm.mode_of(sandbox)
    if mode not in ('workspace-write', 'danger-full-access'):
        raise Refused('native_write_unverified')
    # Match the existing busy_native conservative ceiling: do not reinterpret
    # custom roots/exclusions or external sandboxes as a general write grant.
    if mode == 'workspace-write' and (not isinstance(thread.get('cwd'), str) or os.path.abspath(thread['cwd']) != root):
        raise Refused('native_write_unverified')
    if mode == 'workspace-write' and isinstance(sandbox, dict) and any(
            sandbox.get(key) for key in ('writableRoots','writable_roots','excludeTmpdirEnvVar','excludeSlashTmp')):
        raise Refused('native_write_unverified')
    return actor, thread, proc, digest({'kind':actor.kind,'root':cfg['dir'],'thread_id':tid,
                          'sandbox':sandbox,'research':bool(getattr(actor,'research',False))})


def _open_root(root):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        os.close(fd)
        raise Refused('unsafe_working_root')
    return fd, (info.st_dev, info.st_ino)


def _current_root(root, fd, identity):
    try:
        now = os.stat(root, follow_symlinks=False)
        held = os.fstat(fd)
        return (stat.S_ISDIR(now.st_mode) and (now.st_dev, now.st_ino) == identity
                and (held.st_dev, held.st_ino) == identity)
    except OSError:
        return False


def _absent(root, fd, tid):
    from . import tasks
    try:
        os.stat(tid, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise Refused('task_exists')
    if any(row['id'] == tid for row in tasks.discover(root)):
        raise Refused('task_exists')


def _no_authorization(st, tid):
    from . import tasks
    if tid in tasks.load(st)['on']:
        raise Refused('task_authorization_exists')


def _check_new(st, root, fd, tid):
    with st.file_lock('tasks'):
        _no_authorization(st, tid)
        _absent(root, fd, tid)


def _directory_matches(parent, name, held, prepared):
    """Bind the published name to the held inode and exact closed task bytes."""
    try:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        owned = os.fstat(held)
        if (not stat.S_ISDIR(named.st_mode) or (named.st_dev, named.st_ino) != (owned.st_dev, owned.st_ino)
                or owned.st_uid != os.getuid() or owned.st_mode & 0o077
                or set(os.listdir(held)) != {'task.json','PROMPT.md'}):
            return False
        for leaf, expected in (('task.json',prepared['task_raw']), ('PROMPT.md',prepared['prompt_raw'])):
            file = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=held)
            try:
                before = os.fstat(file)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1
                        or before.st_mode & 0o077 or before.st_size != len(expected)):
                    return False
                actual = b''
                while len(actual) <= len(expected):
                    chunk = os.read(file, len(expected) + 1 - len(actual))
                    if not chunk: break
                    actual += chunk
                after = os.fstat(file)
                named_file = os.stat(leaf, dir_fd=held, follow_symlinks=False)
                if (actual != expected or any(getattr(before,k) != getattr(after,k) for k in
                        ('st_dev','st_ino','st_mode','st_uid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))
                        or (named_file.st_dev,named_file.st_ino) != (after.st_dev,after.st_ino)):
                    return False
            finally:
                os.close(file)
        final = os.stat(name, dir_fd=parent, follow_symlinks=False)
        return (final.st_dev,final.st_ino) == (owned.st_dev,owned.st_ino)
    except OSError:
        return False


def _publish_unlocked(root, fd, identity, prepared, live):
    from . import bounded_plan
    if not live() or not _current_root(root, fd, identity):
        raise Refused('creation_context_changed')
    _absent(root, fd, prepared['id'])
    parent = '.agentj-task-staging'
    try:
        os.mkdir(parent, 0o700, dir_fd=fd)
    except FileExistsError:
        pass
    pending = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    parent_info = os.fstat(pending)
    if parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o077:
        os.close(pending)
        raise Refused('unsafe_staging_directory')
    stage = secrets.token_hex(16)
    os.mkdir(stage, 0o700, dir_fd=pending)
    staged = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pending)
    published = False
    try:
        for name, data in (('task.json',prepared['task_raw']), ('PROMPT.md',prepared['prompt_raw'])):
            leaf = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=staged)
            try:
                remain = memoryview(data)
                while remain:
                    remain = remain[os.write(leaf, remain):]
                os.fsync(leaf)
            finally:
                os.close(leaf)
        os.fsync(staged)
        if not live() or not _current_root(root, fd, identity):
            raise Refused('creation_context_changed')
        _absent(root, fd, prepared['id'])
        if not _directory_matches(pending, stage, staged, prepared):
            raise Refused('publication_conflict_recovery_required')
        # Existing atomic no-replace primitive; unsupported filesystems refuse.
        bounded_plan._rename_exclusive(pending, stage, fd, prepared['id'])
        published = True
        if (not _directory_matches(fd, prepared['id'], staged, prepared) or not live()
                or not _current_root(root, fd, identity)):
            # Never remove an externally substituted object. Nothing is enabled;
            # preserve the conflicting directory for explicit owner recovery.
            raise Refused('publication_conflict_recovery_required')
        os.fsync(fd)
    finally:
        if not published and _directory_matches(pending, stage, staged, prepared):
            for name in ('task.json','PROMPT.md'):
                try: os.unlink(name, dir_fd=staged)
                except FileNotFoundError: pass
            try: os.rmdir(stage, dir_fd=pending)
            except FileNotFoundError: pass
        os.close(staged)
        os.close(pending)


def _publish(st, root, fd, identity, prepared, live):
    # Serialize the final authorization check and directory publication with
    # existing enable/disable state mutations. Never clear prior authorization.
    with st.file_lock('tasks'):
        _no_authorization(st, prepared['id'])
        _publish_unlocked(root, fd, identity, prepared, live)
        _no_authorization(st, prepared['id'])


async def handle(host, request):
    from . import application_ingress
    fd = None
    try:
        frame = application_ingress.current(host)
        if frame is None:
            raise Refused('application_ingress_unverified')
        if not isinstance(request, dict) or set(request) != {'cmd','contract','sha256'} or request['cmd'] != 'task_create':
            raise Refused('request_shape')
        value = copy.deepcopy(request['contract'])
        if request['sha256'] != digest(value):
            raise Refused('contract_hash_mismatch')
        prepared = contract(value)
        cfg = host.agent_cfg
        root = cfg.get('dir') if isinstance(cfg, dict) else None
        if not isinstance(root, str) or not os.path.isabs(root):
            raise Refused('no_working_root')
        root = os.path.abspath(root)
        actor, native_thread, native_proc, ceiling = _write_ceiling(host, root)
        fd, identity = await asyncio.to_thread(_open_root, root)
        await asyncio.to_thread(_check_new, host.st, root, fd, prepared['id'])
        await asyncio.to_thread(_scan, root, value, prepared)
        def context_live():
            try:
                asyncio.current_task()
            except RuntimeError:
                transport_live = frame.worker_live()
            else:
                transport_live = frame.live()
            try:
                current_actor, current_thread, current_proc, current_ceiling = _write_ceiling(host, root)
            except Refused:
                return False
            return (host.agent_cfg is cfg and os.path.abspath(cfg.get('dir', '')) == root and transport_live
                    and current_actor is actor and current_thread is native_thread
                    and current_proc is native_proc and current_ceiling == ceiling)
        if not context_live() or not _current_root(root, fd, identity):
            raise Refused('creation_context_changed')
        grant = None
        if prepared['large_transfer'] is not None:
            inp = {'purpose':'declared_large_transfer_creation', 'effect':'Create a disabled task; transfer limit is informational, not enforced at runtime.',
                   'root':root, 'task':prepared['task'], 'prompt':prepared['prompt_raw'].decode(),
                   'contract_sha256':prepared['tsha'], 'large_transfer':prepared['large_transfer']}
            expected = digest(inp)
            answer = await host.ask(TOOL, inp, grant_info=True, batch=False, always_ask=True, require_passkey=True)
            grant = answer.get('grant_info') or {}
            if (answer.get('behavior') != 'allow' or grant.get('uv') is not True or not grant.get('rid')
                    or not grant.get('device') or not grant.get('sign_pub') or digest(inp) != expected
                    or host.st.sign_key(grant['device']) != grant['sign_pub']):
                raise Refused('fresh_owner_uv_required')
        def live():
            return (context_live() and (grant is None or host.st.sign_key(grant['device']) == grant['sign_pub']))
        await asyncio.to_thread(_scan, root, value, prepared)
        await asyncio.to_thread(_publish, host.st, root, fd, identity, prepared, live)
        host.scheduler.wake.set()
        host.st.log('task_created', id=prepared['id'], contract_sha256=prepared['tsha'], transfer=grant is not None,
                    rid=grant.get('rid') if grant else None)
        return {'ok':True,'id':prepared['id'],'contract_sha256':prepared['tsha'],'enabled':False,
                'transfer_limit_enforced':False}
    except Refused as error:
        return {'ok':False,'error':str(error)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return {'ok':False,'error':'creation_unavailable'}
    finally:
        if fd is not None: os.close(fd)


def command(args):
    from . import names
    from .state import State
    try:
        if not args.contract or args.id:
            raise Refused('use_tasks_create_contract')
        fd = os.open(args.contract, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise Refused('invalid_contract_file')
            raw = os.read(fd, MAX_BYTES + 1)
        finally: os.close(fd)
        value = json.loads(raw)
        contract(value)
        result = names.ctl_call(State(), {'cmd':'task_create','contract':value,'sha256':digest(value)}, timeout=900)
        if not result: result = {'ok':False,'error':'host_unavailable'}
    except names.ServeBusy:
        result = {'ok':False,'error':'outcome_unknown_inspect_task_before_retry'}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        result = {'ok':False,'error':'invalid_contract_file'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') else 2
