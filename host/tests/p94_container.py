"""P94 real-UID qualification. Run ONLY as root in a disposable Ubuntu container.
Mount this file as /test.py, candidate wheel at /candidate, ASR cache at /cache.
No systemd/linger or physical macOS acceptance is inferred from this fixture.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import pwd
import select
import subprocess
import sys
import time
import urllib.request


def run(args, **kw):
    return subprocess.run(args, check=True, **kw)


async def seat():
    from agentj.state import State
    from agentj.serve import Host
    from agentj.admin import AdminServer
    from agentj import asr, elevate_helper as eh
    st = State()  # CLI init and verified ASR installation precede concurrent startup
    host = Host(st, events='quiet', read_stdin=False)
    task = asyncio.create_task(host.run())
    admin = None; worker = None
    try:
        for _ in range(200):
            if task.done():
                await task
                raise RuntimeError('host exited early')
            if st.sock_path.exists() and (st.perm_dir / 'elevate.sock').exists():
                break
            await asyncio.sleep(.05)
        else:
            raise RuntimeError('serve control/elevate sockets not ready')
        admin = AdminServer(st).start()
        d = st.root / 'asr'
        worker = asr._Resident(d, threads=1)
        reply = await asyncio.to_thread(worker.request, {'wav': str(d / 'model/zh.wav')}, time.monotonic()+60)
        assert reply['ok'] and '开饭时间' in reply['text'], reply
        user = pwd.getpwuid(os.getuid()).pw_name
        source = Path.home() / 'helper-source'; source.mkdir(mode=0o700)
        files = eh.files(st, user)
        for name, data in files.items():
            (source / name).write_bytes(data)
        (Path.home() / 'helper-install.sh').write_text(eh.install_script(str(source), {k:eh.sha(v) for k,v in files.items()}))
        (Path.home() / 'helper-uninstall.sh').write_text(eh.uninstall_script())
        print(json.dumps({'user': user, 'uid': os.getuid(), 'pid': os.getpid(), 'asr_pid': worker.proc.pid,
            'asr_decode': True, 'port': admin.port, 'root':str(st.root), 'socket': str(st.sock_path),
            'helper':eh.HELPER, 'keys':eh.KEYS, 'nonce':eh.STATE, 'sudoers':eh.SUDOERS}), flush=True)
        await asyncio.to_thread(sys.stdin.readline)
    finally:
        if worker: worker.stop()
        if admin: admin.stop()
        host.stopping.set()
        await asyncio.wait_for(task, 15)


def root():
    assert os.getuid() == 0 and Path('/.dockerenv').exists(), 'disposable container only'
    users = ['ajp94a','ajp94b','ajp94c']; procs=[]; rows=[]; created=[]
    result={'passed':False, 'systemd_linger':'not tested: PID 1 is not systemd', 'host_users_modified':False}
    try:
        for user in users:
            run(['useradd','-m','-s','/bin/bash',user]); created.append(user)
            home=Path('/home')/user
            run(['runuser','-u',user,'--','python3','-m','venv',str(home/'venv')])
            wheel=next(Path('/candidate').glob('*.whl'))
            run(['runuser','-u',user,'--',str(home/'venv/bin/pip'),'install','--quiet',str(wheel)])
        downloads=Path('/downloads');downloads.mkdir()
        for f in Path('/cache').iterdir(): (downloads/f.name).symlink_to(f)
        # The candidate's own pin selects the actual Ubuntu Python ABI, not the host ABI.
        sys.path.insert(0,str(Path('/home')/users[0]/'venv/lib/python3.12/site-packages'))
        from agentj import asr
        for abi in ('cp312','py3'):
            rel,size,sha=asr.WHEELS[('linux','x86_64',abi)]
            f=downloads/rel.split('/')[-1]
            if not f.exists(): urllib.request.urlretrieve(asr.PYPI+rel,f)
            assert f.stat().st_size==size and hashlib.sha256(f.read_bytes()).hexdigest()==sha
        for user in users:
            home=Path('/home')/user
            env=dict(os.environ, AGENTJ_ASR_DOWNLOADS=str(downloads),AGENTJ_SKILL_LINK='off')
            with (home/'asr-install.log').open('w') as log:
                run(['runuser','-u',user,'--',str(home/'venv/bin/agentj'),'init','--relay','ws://127.0.0.1:9','--web','http://127.0.0.1:9'],env=env,stdout=log,stderr=subprocess.STDOUT)
                run(['runuser','-u',user,'--',str(home/'venv/bin/agentj'),'asr','install','--yes'],env=env,stdout=log,stderr=subprocess.STDOUT)
            p=subprocess.Popen(['runuser','-u',user,'--',str(home/'venv/bin/python'),'/test.py','seat'],
                env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            procs.append(p)
        for p in procs:
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                if select.select([p.stdout],[],[],1)[0]:
                    line=p.stdout.readline()
                    if not line: raise RuntimeError(p.stderr.read())
                    if line.startswith('{'): rows.append(json.loads(line));break
                if p.poll() is not None: raise RuntimeError(p.stderr.read())
            else: raise RuntimeError('seat readiness deadline')
        for key in ('uid','pid','asr_pid','port','root','socket','helper','keys','nonce','sudoers'):
            assert len({r[key] for r in rows})==3, key
        for p in procs: assert p.poll() is None
        for r in rows:
            assert (Path(r['root']).stat().st_mode & 0o777)==0o700
            assert Path(r['socket']).stat().st_uid==r['uid']
            other=next(x for x in users if x!=r['user'])
            denial=subprocess.run(['runuser','-u',other,'--','cat',str(Path(r['root'])/'config.json')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            assert denial.returncode!=0
            run(['sh',str(Path('/home')/r['user']/'helper-install.sh')],stdout=subprocess.DEVNULL)
        for r in rows:
            for k in ('helper','keys','nonce','sudoers'): assert Path(r[k]).exists()
        run(['sh',str(Path('/home')/users[0]/'helper-uninstall.sh')])
        for r in rows[1:]:
            for k in ('helper','keys','nonce','sudoers'): assert Path(r[k]).exists()
        result.update(passed=True,rows=rows,all_three_live=True,cross_user_state_denied=True,helper_uninstall_preserves_others=True,
            wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
    finally:
        for p in procs:
            try: p.communicate('stop\n',timeout=20)
            except subprocess.TimeoutExpired: p.kill();p.communicate()
        for user in created:
            script=Path('/home')/user/'helper-uninstall.sh'
            if script.exists(): subprocess.run(['sh',str(script)],check=True)
            run(['userdel','-r',user],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        result['created']=len(created);result['deleted']=len(created)
        result['remaining']=[u for u in users if subprocess.run(['getent','passwd',u],stdout=subprocess.DEVNULL).returncode==0]
        assert not result['remaining']
        print('P94_RESULT='+json.dumps(result),flush=True)
    assert result['passed']


if __name__=='__main__':
    if sys.argv[1:] == ['seat']: asyncio.run(seat())
    else: root()
