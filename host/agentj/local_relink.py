"""Typed owner-custom Codex skill links only; no package code write or inferred grants."""
from __future__ import annotations
import asyncio
import copy
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import secrets
from . import local_record as core

TOOL='agentj local relink'
DOMAIN=b'agentj-local-relink-journal-v1\0'
ID=re.compile(r'f[1-9][0-9]{0,9}\Z')


def mapping(value):
    from . import market
    name=value.get('skill_name')
    if (value.get('entry_kind')!='harness_skill' or value.get('harness')!='codex' or not isinstance(name,str)
            or not market._NAME.fullmatch(name) or market.RESERVED.match(name)):raise core.Refused('unsupported_owner_skill_mapping')
    root=core.absolute(str(market.skills_root('codex')))
    return {'entry_schema':1,'entry_kind':'harness_skill','harness':'codex','skill_name':name,'entry':str(root/name)}


def facts(fd,prefix=''):
    out={}
    def fields(row):return [row.st_dev,row.st_ino,row.st_uid,row.st_mode,row.st_nlink,row.st_size,row.st_mtime_ns,row.st_ctime_ns]
    root=os.fstat(fd)
    if root.st_uid!=os.getuid() or root.st_mode&0o022:raise core.Refused('unsafe_source_directory')
    out[prefix or '.']=fields(root)
    for name in sorted(os.listdir(fd)):
        path=core.relative(prefix+name);row=os.stat(name,dir_fd=fd,follow_symlinks=False)
        if stat.S_ISDIR(row.st_mode):
            child=os.open(name,core.DIR,dir_fd=fd)
            try:
                held=os.fstat(child)
                if (row.st_dev,row.st_ino)!=(held.st_dev,held.st_ino):raise core.Refused('source_changed')
                out.update(facts(child,path+'/'))
                if core.named_identity(fd,name)!=core.ident(child):raise core.Refused('source_changed')
            finally:os.close(child)
        elif stat.S_ISREG(row.st_mode):
            if row.st_uid!=os.getuid() or row.st_nlink!=1 or row.st_mode&0o022:raise core.Refused('unsafe_source_file')
            out[path]=fields(row)
        else:raise core.Refused('unsafe_source_file')
        if len(out)>100:raise core.Refused('inventory_too_large')
    if fields(os.fstat(fd))!=fields(root):raise core.Refused('source_changed')
    return out


def source(record):
    from . import workflows
    path=core.absolute(record.get('path'));mapped=mapping(record);slot=Path(mapped['entry']).parent
    for blocked in (core.absolute(record.get('install_root')),core.absolute(str(core.INSTALL_ROOT)),slot):
        if path==blocked or blocked in path.parents:raise core.Refused('source_inside_protected_root')
    fd=core.directory(path)
    try:
        before=facts(fd);files=core.inventory(fd)
        if facts(fd)!=before or record.get('source_facts')!=before:raise core.Refused('source_changed')
        if 'SKILL.md' not in files:raise core.Refused('skill_document_missing')
        meta,_=workflows.front(files['SKILL.md'].decode())
        if meta.get('name')!=mapped['skill_name'] or meta.get('aliases') not in (None,[]):raise core.Refused('skill_identity_mismatch')
        if not core.current(path,fd):raise core.Refused('source_changed')
        return mapped,core.ident(fd),files
    finally:os.close(fd)


def registry(st,name,entry):
    """Read existing trust stores fail-closed; never reinterpret a tombstone as free."""
    root=core.directory(st.root,private=True)
    try:
        try:fd=os.open('plaza',core.DIR,dir_fd=root)
        except FileNotFoundError:return {'installed':None,'publishers':None}
        try:
            info=os.fstat(fd)
            if info.st_uid!=os.getuid() or info.st_mode&0o077:raise core.Refused('plaza_history_unavailable')
            out={}
            for leaf in ('installed.json','publishers.json'):
                try:raw=core.read(fd,leaf)
                except FileNotFoundError:out[leaf.split('.')[0]]=None;continue
                value=json.loads(raw)
                if not isinstance(value,dict) or 'items' not in value:raise core.Refused('plaza_history_unavailable')
                if leaf=='installed.json':
                    if not isinstance(value['items'],list) or not isinstance(value.get('uninstalled',[]),list):raise core.Refused('plaza_history_unavailable')
                    for row in value['items']+value.get('uninstalled',[]):
                        if (not isinstance(row,dict) or not isinstance(row.get('name'),str)
                                or not isinstance(row.get('target'),str)):raise core.Refused('plaza_history_unavailable')
                        if row['name']==name or row['target']==entry or Path(row['target']).name==name:raise core.Refused('plaza_history_conflict')
                else:
                    if not isinstance(value['items'],dict) or any(not isinstance(k,str) or not isinstance(v,dict) for k,v in value['items'].items()):raise core.Refused('plaza_history_unavailable')
                    if name in value['items']:raise core.Refused('plaza_history_conflict')
                out[leaf.split('.')[0]]=core.sha(raw)
            if core.named_identity(root,'plaza')!=core.ident(fd):raise core.Refused('plaza_history_unavailable')
            return out
        finally:os.close(fd)
    finally:os.close(root)


def catalog(name,path,status):
    from . import workflows
    rows,hidden,conflicts=workflows.common_skills('codex')
    selected=[]
    for row in rows:
        names=[row.get('name'),*(row.get('_al') or [])]
        if any(isinstance(n,str) and n.casefold()==name.casefold() for n in names):
            if not (status=='already_present' and row.get('scope')=='global' and row.get('_path')==str(Path(path)/'SKILL.md')):
                raise core.Refused('registered_skill_conflict')
            continue
        selected.append({k:row.get(k) for k in ('name','scope','_path','_al')})
    return core.sha(core.encode({'skills':selected,'hidden':hidden,'conflicts':conflicts}))


def snapshot(st,rid,work):
    if not isinstance(rid,str) or not ID.fullmatch(rid):raise core.Refused('invalid_feature_id')
    from . import __version__, update
    if update.installed_version()!=__version__:raise core.Refused('running_package_changed')
    package=core.directory(core.INSTALL_ROOT)
    try:package_identity=core.ident(package)
    finally:os.close(package)
    private=Path.home()/'.agentj-local';fd=core.directory(private,private=True)
    try:
        raw,data=core.manifest(fd);rows=[r for r in data['features'] if r['id']==rid]
        if len(rows)!=1:raise core.Refused('feature_not_registered')
        row=rows[0]
        if row.get('entry_schema')!=1:raise core.Refused('legacy_entry_not_relinkable')
        doc_raw=core.read(fd,'features/'+rid+'/record.json');doc=json.loads(doc_raw)
        if not isinstance(doc,dict) or {k:v for k,v in doc.items() if k not in ('payload_sha256','install_identity','baseline_identity')}!=row:raise core.Refused('feature_record_mismatch')
        mapped,identity,files=source(doc)
        if (doc.get('source_identity')!=identity or doc.get('source_sha256')!={p:core.sha(b) for p,b in files.items()}
                or any(doc.get(k)!=v for k,v in mapped.items())):raise core.Refused('source_changed')
        root=Path(mapped['entry']).parent;parent=core.directory(root)
        try:
            if not core.current(root,parent):raise core.Refused('skill_root_changed')
            try:
                leaf=os.stat(mapped['skill_name'],dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:status='missing';link_identity=None
            else:
                if not stat.S_ISLNK(leaf.st_mode) or os.readlink(mapped['skill_name'],dir_fd=parent)!=doc['path']:raise core.Refused('entry_conflict')
                status='already_present';link_identity=[leaf.st_dev,leaf.st_ino]
            state={'id':rid,'record_sha256':core.sha(doc_raw),'manifest_sha256':core.sha(raw),'workspace_identity':core.ident(fd),
                   'source':doc['path'],'source_identity':identity,'source_sha256':doc['source_sha256'],'mapping':mapped,
                   'parent_identity':core.ident(parent),'history':registry(st,mapped['skill_name'],mapped['entry']),
                   'status':status,'link_identity':link_identity,
                   'skill_catalog':catalog(mapped['skill_name'],doc['path'],status),
                   'current_package_root':str(core.INSTALL_ROOT),'current_package_identity':package_identity,'current_version':__version__}
            core.scan(work,[*core.strings(doc),*core.strings(state),*files.keys(),*files.values()])
            if not core.current(private,fd):raise core.Refused('workspace_changed')
            return state
        finally:os.close(parent)
    finally:os.close(fd)


def journal(st,document):
    """New, private, host-signed receipt only. No replay executor or custom verifier."""
    from . import wire
    value=core.encode(document)
    path=st.root/('local-relink-'+secrets.token_hex(16)+'.json')
    fd=core.directory(st.root,private=True)
    try:
        body=core.encode({'payload':wire.b64u(value),'signature':wire.b64u(st.signing_key().sign(DOMAIN+value))})+b'\n'
        core.write(fd,path.name,body)
    finally:os.close(fd)
    return path.name


def commit(st,rid,work,expected,live):
    with st.file_lock('local-patches'),st.file_lock('plaza-installed'):
        current=snapshot(st,rid,work)
        if current!=expected or not live():raise core.Refused('relink_context_changed')
        root=Path(current['mapping']['entry']).parent;parent=core.directory(root)
        try:
            if core.ident(parent)!=current['parent_identity'] or not core.current(root,parent):raise core.Refused('skill_root_changed')
            record={'schema':1,'operation':'owner_skill_relink','feature_id':rid,'snapshot':current,'status':'prepared'}
            journal(st,record)
            if not live() or snapshot(st,rid,work)!=expected:raise core.Refused('relink_context_changed')
            os.symlink(current['source'],current['mapping']['skill_name'],dir_fd=parent)
            os.fsync(parent)
            try:
                after=snapshot(st,rid,work)
                expected_after={**expected,'status':'already_present','link_identity':after['link_identity']}
                if after!=expected_after or not core.current(root,parent) or not live():raise core.Refused('relink_recovery_required')
                journal(st,{**record,'status':'linked','link_identity':after['link_identity']})
            except (OSError,ValueError,TypeError,KeyError):raise core.Refused('relink_recovery_required') from None
            return {'ok':True,'id':rid,'status':'linked','modified':True,'functional_verified':False,'scope':'owner_custom'}
        except FileExistsError:raise core.Refused('entry_conflict') from None
        finally:os.close(parent)


async def handle(host,request):
    from . import application_ingress, task_create, codex_perm, provenance
    try:
        if not sys.platform.startswith('linux'):raise core.Refused('relink_platform_unsupported')
        frame=application_ingress.current(host)
        if frame is None or frame.kind=='task_local' or not isinstance(request,dict) or set(request)!={'cmd','id'} or request['cmd']!='local_relink':raise core.Refused('owner_application_required')
        actor=host.agent;send=getattr(actor,'cur_send',None);cfg=host.agent_cfg;work=(cfg or {}).get('dir')
        if frame.kind!='terminal' and not provenance.owner_intent(provenance.turn_source(actor),send):raise core.Refused('owner_application_required')
        first=task_create._write_ceiling(host,work)
        if codex_perm.mode_of(first[1].get('sandbox'))!='danger-full-access':raise core.Refused('native_full_access_required')
        prepared=await asyncio.to_thread(snapshot,host.st,request['id'],work)
        if prepared['status']=='already_present':return {'ok':True,'id':request['id'],'status':'already_present','modified':False,'functional_verified':False,'scope':'owner_custom'}
        def context_live():
            try:
                now=task_create._write_ceiling(host,work)
                return (frame.worker_live() and host.agent_cfg is cfg and getattr(actor,'cur_send',None) is send
                        and (frame.kind=='terminal' or provenance.owner_intent(provenance.turn_source(actor),send))
                        and all(a is b for a,b in zip(first[:3],now[:3])) and first[3]==now[3])
            except (task_create.Refused,OSError):return False
        inp={'purpose':'relink_owner_custom_skill','id':prepared['id'],'source':prepared['source'],'entry':prepared['mapping']['entry'],
             'snapshot_sha256':core.sha(core.encode(prepared)),'source_sha256':prepared['source_sha256'],
             'effect':'Create only this missing owner-custom skill link; no code execution or compatibility proof. / 只创建这个缺失的主人自定义技能链接；不执行代码、不证明功能兼容。'}
        input_sha=core.sha(core.encode(inp));answer=await host.ask(TOOL,inp,grant_info=True,batch=False,always_ask=True,require_passkey=True)
        grant=answer.get('grant_info') or {};until=time.monotonic()+min(30,host.ask_ttl)
        if (input_sha!=core.sha(core.encode(inp)) or answer.get('behavior')!='allow' or grant.get('uv') is not True
                or not grant.get('device') or not grant.get('sign_pub') or not grant.get('rid') or host.st.sign_key(grant['device'])!=grant['sign_pub']):raise core.Refused('fresh_owner_uv_required')
        def live():return context_live() and time.monotonic()<=until and host.st.sign_key(grant['device'])==grant['sign_pub']
        result=await asyncio.to_thread(commit,host.st,request['id'],work,prepared,live)
        host.st.log('local_skill_relinked',id=request['id'],rid=grant['rid'],functional_verified=False)
        return result
    except core.Refused as exc:return {'ok':False,'error':str(exc)}
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):return {'ok':False,'error':'local_relink_unavailable'}


def command(args):
    from . import names
    from .state import State
    try:
        if args.contract or not args.id or not ID.fullmatch(args.id):raise core.Refused('feature_id_required')
        result=names.ctl_call(State(),{'cmd':'local_relink','id':args.id},timeout=900)
        if not result:result={'ok':False,'error':'host_unavailable'}
    except names.ServeBusy:result={'ok':False,'error':'outcome_unknown_inspect_link_before_retry'}
    except (OSError,ValueError,TypeError):result={'ok':False,'error':'invalid_relink_request'}
    print(json.dumps(result,ensure_ascii=False));return 0 if result.get('ok') else 2
