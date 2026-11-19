"""Install only the selected, bundled Agent Plan route. Owner files are never rewritten."""
import os
from pathlib import Path
import json
import secrets

from . import ark, opencode_free, opencode_probe_route as probe


def chosen(model):
    return isinstance(model, str) and model.partition('/')[0] == 'ark-agent-plan'


def directory(env):
    home = Path(env.get('HOME') or Path.home())
    return Path(env.get('XDG_CONFIG_HOME') or home / '.config') / 'opencode'


def official(env, project, model):
    """Existing owner route or a private projection of the bundled one. Conflicts stop before the first request."""
    if not chosen(model):
        raise opencode_free.ProbeError('bad_model')
    preset = ark.plan_data('agent')['block']
    mid = model.split('/',1)[1]
    if mid not in preset['models']:
        raise opencode_free.ProbeError('model_missing')
    route = probe.selected_route(env, project, model)
    block = route.get('provider', {}).get('ark-agent-plan')
    if block and (block.get('npm') != preset['npm'] or block.get('options') != preset['options'] or
                  block.get('models', {}).get(mid, {}).get('id',mid) != mid):
        raise opencode_free.ProbeError('ark_route_conflict')
    return block, preset


def install(env, project, model):
    """Publish an additive private config with link-if-absent, never copying any owner's existing secrets."""
    block, preset = official(env, project, model)
    if block:
        return "native"
    root = directory(env)
    if not root.is_absolute() or '..' in root.parts:
        raise opencode_free.ProbeError('ark_route_conflict')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root.anchor, flags)
    tmp = '.agentj-plan-' + secrets.token_hex(12)
    created = False
    try:
        for part in root.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        tmp_fd = os.open(tmp,os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,0o600,dir_fd=fd)
        created = True
        with os.fdopen(tmp_fd,'w',encoding='utf-8') as f:
            json.dump({'provider':{'ark-agent-plan':preset}}, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        for name in ('opencode.json','opencode.jsonc','ark-agent-plan.agentj.json'):
            if official(env,project,model)[0]:
                return "native"
            try:
                os.link(tmp,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
            except FileExistsError:
                if name == 'ark-agent-plan.agentj.json' and probe._read(root/name) == {'provider':{'ark-agent-plan':preset}}:
                    return 'managed'
                continue
            os.fsync(fd)
            if name == 'ark-agent-plan.agentj.json':
                return 'managed'  # used through the managed native launch overlay; both owner config files stay byte-for-byte intact

            if official(env,project,model)[0]:
                return "native"
            break
        raise opencode_free.ProbeError('ark_route_conflict')
    except (OSError, ValueError, TypeError):
        raise opencode_free.ProbeError('ark_route_conflict') from None
    finally:
        if created:
            os.unlink(tmp,dir_fd=fd)
        os.close(fd)


def runtime(env, project, model):
    """The managed native process consumes the same pinned route as the check. Its own global settings remain available."""
    if not chosen(model):
        return env
    block, preset = official(env,project,model)
    content = json.loads(env.get('OPENCODE_CONFIG_CONTENT') or '{}')
    content['provider'] = {**(content.get('provider') or {}), 'ark-agent-plan':preset}
    # Project provider/adapter settings may change after the check, including while the server is running.
    # For this selected plan only, keep its public connection material in the final native config layer.
    return {**env,'OPENCODE_DISABLE_PROJECT_CONFIG':'1','OPENCODE_CONFIG_CONTENT':json.dumps(content,ensure_ascii=False)}


def authority(st, device):
    """A local receipt from serve. The client cannot name a pairing or working directory."""
    import hashlib
    from . import working_root
    key = st.sign_key(device)
    return {'setup_device':device, 'setup_signing':hashlib.sha256(key).hexdigest(),
            'setup_root':str(working_root.select(st))} if key else {}


def _set_first(st,root,model,expected,*,remove=False,live=None,pin=None):
    from .state import _write_private
    from contextlib import nullcontext
    with st.config_lock(), (st.devices_lock() if live else nullcontext()):
        cfg = st.config()
        if cfg.get('agent') != expected or (live and not live()):
            return False
        if remove:
            cfg.pop('agent',None)
        else:
            cfg['agent'] = {'kind':'opencode','dir':root,'model':model,'fence':True}
            if pin is not None:
                from .opencode_binary import valid
                if not valid(pin):
                    return False
                cfg['agent']['opencode_binary'] = pin
        _write_private(st.config_path,json.dumps(cfg,ensure_ascii=False,indent=1).encode())
        return True


def first(st,job,model,dry_run,native,ctl,restart,sleep,clock,*,metadata=None):
    """First connection has no harness to roll back to. Pairing, model and persisted selection are checked separately."""
    import time
    from functools import partial
    from . import harness, switch_harness as switch, working_root
    rec = switch.read_receipt(st) or {}
    def live():
        device = rec.get('setup_device')
        current = switch.read_receipt(st) or {}
        return (job and current == rec and rec.get('job') == job and rec.get('status') == 'probing'
                and 0 <= time.time()-rec['at'] < switch.JOB_MAX_AGE and set(st.devices()) == {device}
                and authority(st,device) == {k:rec[k] for k in ('setup_device','setup_signing','setup_root') if k in rec})
    if not dry_run and not live():
        return 'first_device' if st.devices() else 'first_pair'
    root = str(working_root.select(st))
    if not Path(root).is_dir():
        return 'config_write_failed'
    cfg = {'kind':'opencode','dir':root,'fence':True,'model':model,'_agent_plan':True}
    test = switch.gate('opencode',model,native or partial(harness.probe,opencode_launch=(st,cfg)))
    if metadata is not None:
        metadata.update({k:test[k] for k in ("executable","executable_source","binary_candidates","version") if k in test})
    if "opencode_binary" in test:
        cfg["opencode_binary"] = test["opencode_binary"]
    if not test['ok'] or dry_run:
        return test.get('reason')
    if not live():
        return 'first_device'
    try:
        install(os.environ,root,model)
    except (opencode_free.ProbeError,OSError):
        return 'ark_route_conflict'
    expected = {k:v for k,v in cfg.items() if k != '_agent_plan'}
    if not live() or not _set_first(st,root,model,None,live=live,pin=test.get("opencode_binary")):
        return 'config_mismatch'
    try:
        restart()
        check = lambda: switch._raw_agent(st) == expected
        why = switch._wait_up(ctl,'opencode',sleep,clock,verify=check)
        if not why:
            greeting = switch._hello(ctl,{'outcome':'first','to':'opencode'},sleep,clock,verify=check)
            if greeting is not None:
                why = greeting.get('reason') or (None if greeting.get('busy') else 'no_reply')
            pin = cfg.get('opencode_binary')
            if pin and not why and (ctl({'cmd':'status'}) or {}).get('agent_binary') != {k:pin[k] for k in ('path','source','version')}:
                why = 'binary_changed'
    except Exception:
        why = 'restart_failed'
    if why and _set_first(st,root,model,expected,remove=True):
        try:
            restart()
        except Exception:
            return 'restart_failed'
    return why


def _merge(base, layer):
    for key, value in layer.items():
        if isinstance(value,dict) and isinstance(base.get(key),dict):
            _merge(base[key],value)
        elif key in ('instructions','plugin') and isinstance(value,list) and isinstance(base.get(key),list):
            base[key] += [item for item in value if item not in base[key]]
        else:
            base[key] = value


def project_settings(env, project):
    """Copy bounded declarative settings only. Project executables never inherit a newly entered plan key."""
    folder = Path(project).resolve()
    layers = [*reversed(folder.parents),folder]
    options = {}
    skipped = False
    permitted = {'theme','default_agent','small_model','model','enabled_providers','disabled_providers'}
    executable = {'plugin','plugins','mcp','lsp','formatter','experimental','tools','command','agent'}
    for directory in layers:
        for name in ('opencode.json','opencode.jsonc','.opencode/opencode.json','.opencode/opencode.jsonc'):
            values = probe._read(directory/name)
            skipped |= bool(executable & values.keys()) or bool(values.get('instructions'))
            selected = {k:v for k,v in values.items() if k in permitted}
            if any('{env:' in str(v) or '{file:' in str(v) for v in selected.values()):
                raise opencode_free.ProbeError('ark_route_conflict')
            _merge(options,selected)
        for name in ('plugins','plugin','tools','tool','node_modules'):
            asset = directory/'.opencode'/name
            if asset.is_dir():
                if name != 'plugins' or any(p.name not in ('agentj-shared.js','agentj-dispatch') for p in asset.iterdir()):
                    skipped = True
    if len(json.dumps(options)) > probe.MAX_CONFIG:
        raise opencode_free.ProbeError('ark_route_conflict')
    return options, skipped


def project_view(env, project, view, *, version2, channel):
    """Only freshly rendered product guard code runs from the private native config directory."""
    from . import main_opencode_hook, shared_opencode_hook
    options, skipped = project_settings(env,project)
    installer = main_opencode_hook.install if version2 else shared_opencode_hook.install
    installer(view,channel)
    directory = Path(view)/'.opencode'
    if options:
        fd = os.open(directory/'opencode.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(options,handle,ensure_ascii=False)
    # Native loaders expand file references before merging the final overlay. This private
    # global config prevents a second, independent read of a customer's credential file.
    config = Path(view)/'config'/'opencode'
    config.mkdir(parents=True,mode=0o700)
    (config/'opencode.json').write_text(json.dumps({'provider':{'ark-agent-plan':ark.plan_data('agent')['block']}}))
    return {k:v for k,v in env.items() if k != 'OPENCODE_CONFIG'} | {
        'XDG_CONFIG_HOME':str(config.parent), 'OPENCODE_CONFIG_DIR':str(directory)}
