"""Isolated Host/UDS and real paired software authenticator; fake active Codex facts."""
import _hermetic  # noqa
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from agentj.state import State
from test_l1 import Phone, _owner_host, _ready
from agentj import diskq

async def main():
    root,mode=Path(sys.argv[1]),sys.argv[2]
    st=State(root/'state')
    if not st.root.exists():st.init(relay='ws://127.0.0.1:1')
    st.set_agent_config('codex',str(root/'work'),record_root=False)
    if importlib.util.find_spec('agentj.local_record'):
        from agentj import local_record
        local_record.INSTALL_ROOT=root/'install'
        if mode in ('manifest_swap','manifest_same_bytes_swap'):
            original_read=local_record.read
            def raced_read(fd,path):
                raw=original_read(fd,path)
                if path.startswith('.manifest-') and not (root/'race-proof.json').exists():
                    os.rename(path,'retained-old-manifest',src_dir_fd=fd,dst_dir_fd=fd)
                    leaf=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=fd)
                    body=raw if mode=='manifest_same_bytes_swap' else b'unknown recovery marker'
                    os.write(leaf,body);new=os.fstat(leaf);os.close(leaf)
                    (root/'race-proof.json').write_text(json.dumps({'path':path,'inode':[new.st_dev,new.st_ino],'same':mode=='manifest_same_bytes_swap'}))
                return raw
            local_record.read=raced_read
    if mode in ('stage_swap','publish_swap'):
        from agentj import bounded_plan
        rename=bounded_plan._rename_exclusive
        def altered(srcfd,src,dstfd,dst):
            if dst=='p1':
                if mode=='stage_swap':
                    os.rename(src,'retained-original',src_dir_fd=srcfd,dst_dir_fd=srcfd)
                    os.mkdir(src,0o700,dir_fd=srcfd)
                rename(srcfd,src,dstfd,dst)
                if mode=='publish_swap':
                    os.rename(dst,'retained-original',src_dir_fd=dstfd,dst_dir_fd=dstfd)
                    os.mkdir(dst,0o700,dir_fd=dstfd)
                at=os.open(dst,os.O_RDONLY|os.O_DIRECTORY,dir_fd=dstfd)
                try:
                    leaf=os.open('unknown.marker',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=at);os.write(leaf,b'keep unknown data');os.close(leaf)
                finally:os.close(at)
            else:rename(srcfd,src,dstfd,dst)
        bounded_plan._rename_exclusive=altered
    if mode=='link_swap':
        original_link=os.symlink
        def replaced_link(src,dst,**kw):
            original_link(src,dst,**kw)
            if dst=='owner-tool' and 'dir_fd' in kw:
                fd=kw['dir_fd'];os.rename(dst,'retained-approved-link',src_dir_fd=fd,dst_dir_fd=fd)
                leaf=os.open(dst,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=fd);os.write(leaf,b'unknown slot marker');os.close(leaf)
        os.symlink=replaced_link
    sent=[];host=_owner_host(st,sent);host.ask_ttl=1.5
    host.agent.proc=SimpleNamespace(pid=os.getpid(),returncode=None);host.agent.tid='fixture-native-thread'
    host.agent.thread={'id':'fixture-native-thread','cwd':str(root/'work'),'sandbox':{'type':'dangerFullAccess'}}
    if mode in ('workspace','read_only'):host.agent.thread['sandbox']={'type':'workspaceWrite' if mode=='workspace' else 'readOnly'}
    if mode=='unknown':host.agent.thread={}
    if mode=='incomplete':
        from agentj.peer_guard import SecretIndex
        old_build=SecretIndex.build
        def build(*a,**k):
            index=old_build(*a,**k);index.complete=False;return index
        SecretIndex.build=build
    if mode=='changed_package':
        from agentj import update
        update.installed_version=lambda:'0.0.0'
    phone=Phone(st,with_passkey=True);session=_ready(host,phone);seen=set()
    async def answers():
        while True:
            await asyncio.sleep(.01)
            for _,card in sent:
                if card.get('t')!='ask' or card['id'] in seen:continue
                seen.add(card['id']);(root/'cards.json').write_text(json.dumps([o for _,o in sent if o.get('t')=='ask']))
                if mode=='timeout':continue
                if mode=='drop_write':host.agent.thread['sandbox']={'type':'readOnly'}
                if mode=='change_source':(root/'feature/SKILL.md').write_text('changed source during UV\n')
                if mode=='source_same_bytes_inode_swap':
                    p=root/'feature/run.py';body=p.read_bytes();p.rename(root/'retained-original-source');p.write_bytes(body)
                if mode=='package_root_swap':
                    (root/'install').rename(root/'retained-install');(root/'install').mkdir()
                if mode=='execution_done':host.agent.proc.returncode=0
                if mode=='slot_root_swap':
                    (root/'.agents/skills').rename(root/'.agents/retained-skills')
                    (root/'.agents/skills').symlink_to(root/'feature',target_is_directory=True)
                if mode=='new_history':
                    p=root/'state/plaza';p.mkdir(mode=0o700,exist_ok=True)
                    q=p/'installed.json';q.write_text(json.dumps({'items':[],'uninstalled':[{'name':'owner-tool','target':str(root/'.agents/skills/owner-tool'),'certified':True,'version':'1.0.0','status':'uninstalled'}]}));q.chmod(0o600)
                if mode=='known_secret':os.environ['LOCAL_RECORD_TOKEN']='recorded_fixture_value_314159_local_only'
                if mode=='swap_workspace':
                    (root/'.agentj-local').symlink_to(root/'feature',target_is_directory=True)
                answer=phone.answer(card,mode!='deny')
                if mode=='no_uv':answer.pop('fa',None)
                if mode=='uv_bit_clear':
                    from agentj import passkey
                    ask=host.asks[card['id']]
                    challenge=passkey.elevate_challenge(host.channel,phone.did,ask.rid,ask.pk_kind,ask.rid,ask.digest)
                    answer['fa']=phone.auth.assert_(challenge,b'0',nonce=b'0',flags=0x01)
                if mode=='unsigned':answer.pop('sig',None)
                if mode=='revoke':st.remove_device(phone.did)
                if mode=='wrong_hash':answer=phone.answer(card,True,summary=card['summary']+'changed')
                await host._app(session,answer)
    server=await asyncio.start_unix_server(host.on_ctl,path=str(st.sock_path));watcher=asyncio.create_task(answers())
    print('READY',flush=True)
    try:await server.serve_forever()
    finally:watcher.cancel();await diskq.aflush()

if __name__=='__main__':asyncio.run(main())
