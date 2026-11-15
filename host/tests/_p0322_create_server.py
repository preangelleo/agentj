"""Isolated real Host/UDS + software paired authenticator; no production state."""
import _hermetic  # noqa
import asyncio
import json
import os
import sys
from pathlib import Path
from agentj.state import State
from test_l1 import Phone, _owner_host, _ready
from agentj import diskq

async def main():
    root, mode = Path(sys.argv[1]), sys.argv[2]
    st = State(root/'state'); st.init(relay='ws://127.0.0.1:1')
    work=root/'work';work.mkdir(exist_ok=True)
    st.set_agent_config('codex',str(work),record_root=False)
    if mode in ('publish_fail','crash_before_publish'):
        from agentj import bounded_plan
        def fail_publish(*args):
            if mode=='crash_before_publish':os._exit(73)
            raise OSError('synthetic publish failure')
        bounded_plan._rename_exclusive=fail_publish
    if mode in ('stage_swap','published_swap','armed_stage_swap','armed_published_swap'):
        from agentj import bounded_plan
        original_rename=bounded_plan._rename_exclusive
        def swapped_publish(src_fd,src,dst_fd,dst):
            if mode.startswith('armed_') and not (root/'arm-swap').exists():
                return original_rename(src_fd,src,dst_fd,dst)
            def substitute(parent,name):
                os.rename(name,'retained-original',src_dir_fd=parent,dst_dir_fd=parent)
                os.mkdir(name,0o700,dir_fd=parent)
                fake=os.open(name,os.O_RDONLY|os.O_DIRECTORY,dir_fd=parent)
                try:
                    spec={'v':1,'id':'new-work','title':{'en':'Injected','zh':'置换'},'schedule':'0 8 * * *','tz':'local',
                          'prompt_file':'PROMPT.md','dry_run_prompt_file':'PROMPT.md','mode':'normal','enabled':True,'needs':[],'outputs':[]}
                    for leaf,body in (('task.json',json.dumps(spec)),('PROMPT.md','Injected payload'),('unknown.marker','keep')):
                        f=os.open(leaf,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=fake)
                        os.write(f,body.encode());os.close(f)
                finally:os.close(fake)
            if mode.endswith('stage_swap'):substitute(src_fd,src)
            original_rename(src_fd,src,dst_fd,dst)
            if mode.endswith('published_swap'):substitute(dst_fd,dst)
        bounded_plan._rename_exclusive=swapped_publish
    sent=[];host=_owner_host(st,sent);host.ask_ttl=0.6
    host.agent_notice=lambda *a,**k:None
    from types import SimpleNamespace
    host.agent.proc=SimpleNamespace(pid=os.getpid(),returncode=None)
    host.agent.tid='fixture-native-thread'
    host.agent.thread={'id':'fixture-native-thread','cwd':str(work),'sandbox':{'type':'workspaceWrite','writableRoots':[],'networkAccess':False}}
    if mode=='read_only':host.agent.thread['sandbox']={'type':'readOnly'}
    if mode=='unknown_ceiling':host.agent.thread={}
    if mode=='wrong_native_cwd':host.agent.thread['cwd']=str(root/'outside')
    if mode=='disconnected_native':host.agent.proc.returncode=1
    if mode=='custom_roots':host.agent.thread['sandbox']['writableRoots']=['/unverified']
    phone=Phone(st,with_passkey=True);session=_ready(host,phone)
    seen=set()
    async def answers():
        while True:
            await asyncio.sleep(.01)
            for _,card in sent:
                if card.get('t')!='ask' or card['id'] in seen:continue
                seen.add(card['id'])
                (root/'cards.json').write_text(json.dumps([o for _,o in sent if o.get('t')=='ask']))
                if mode=='timeout':continue
                if mode=='replace_thread':host.agent.thread=dict(host.agent.thread)
                if mode=='drop_write':host.agent.thread['sandbox']={'type':'readOnly'}
                if mode=='root_swap':
                    work.rename(root/'old-work');work.symlink_to(root/'outside',target_is_directory=True)
                ans=phone.answer(card,mode!='deny')
                if mode=='no_uv':ans.pop('fa',None)
                if mode=='unsigned':ans.pop('sig',None)
                if mode=='revoke':st.remove_device(phone.did)
                if mode=='wrong_hash':ans=phone.answer(card,True,summary=card['summary']+'changed')
                await host._app(session,ans)
    server=await asyncio.start_unix_server(host.on_ctl,path=str(st.sock_path))
    watcher=asyncio.create_task(answers())
    print('READY',flush=True)
    try:
        await server.serve_forever()
    finally:
        watcher.cancel();await diskq.aflush()

if __name__=='__main__':asyncio.run(main())
