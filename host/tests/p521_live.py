"""Opt-in Linux desktop/systemd P521 qualification. Usage: AGENTJ_TEST_CHROME=/path/chrome python tests/p521_live.py systemd /tmp/evidence.json. Only a transient test unit and temporary profile are used."""
import concurrent.futures, contextlib, json, os, pathlib, signal, subprocess, sys, tempfile, time
from unittest.mock import patch
from agentj import browser
from agentj.browser_cdp import CDP
from agentj.state import State

exe = os.environ['AGENTJ_TEST_CHROME']
mode = sys.argv[1]
out = pathlib.Path(sys.argv[2])
with tempfile.TemporaryDirectory(prefix='aj-p521-live-') as td:
    root = pathlib.Path(td)
    st = State(root / 'state')
    st.root.mkdir(mode=0o700)
    env = dict(os.environ, PYTHONPATH=str(pathlib.Path(__file__).resolve().parents[1]), AGENTJ_STATE_DIR=str(st.root), AGENTJ_TEST='1', XDG_CONFIG_HOME=str(root / 'config'), AGENTJ_BROWSER_DETECT='off')
    worker = root / 'worker.py'
    worker.write_text('''import pathlib, sys\nfrom unittest.mock import patch\nfrom agentj import browser\nfrom agentj.state import State\nst=State(pathlib.Path(sys.argv[1]))\nwith patch.object(browser, 'installed', return_value={'path':sys.argv[2], 'version':'local-test'}):\n    raise SystemExit(browser.run(st))\n''')
    argv = [sys.executable, str(worker), str(st.root), exe]
    unit = 'agentj-p521-test-' + str(os.getpid())
    rec = None
    children = []
    log = (root / 'supervisor.log').open('wb')
    result = {'mode':mode}
    def ctl(*args, check=True):
        return subprocess.run(['systemctl','--user',*args], check=check, capture_output=True, text=True)
    try:
        if mode == 'systemd':
            cmd = ['systemd-run','--user','--unit='+unit,'--property=Restart=no','--property=KillMode=process','--property=RemainAfterExit=yes']
            for key in ('PYTHONPATH','AGENTJ_STATE_DIR','AGENTJ_TEST','XDG_CONFIG_HOME','DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS'):
                if env.get(key): cmd += ['--setenv='+key+'='+env[key]]
            subprocess.run(cmd + argv, env=env, check=True, capture_output=True)
        else:
            children.append(subprocess.Popen(argv, env=env, stdout=log, stderr=log))
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            rec=browser.runtime(st)
            if rec: break
            time.sleep(.2)
        assert rec, (root/'supervisor.log').read_text()
        result['initial_pid']=rec['pid']; result['headless']=rec['headless']
        assert not rec['headless'], 'requires real Linux display'
        # Simultaneous additional service/CLI invocations must return 0, never forward another welcome URL.
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            returns=list(pool.map(lambda _:subprocess.run(argv, env=env, stdout=log, stderr=log, timeout=30).returncode,range(5)))
        assert returns==[0]*5, returns
        with CDP(rec['ws']) as cdp:
            pages=[t for t in cdp.call('Target.getTargets')['targetInfos'] if t['type']=='page']
            windows={cdp.call('Browser.getWindowForTarget',{'targetId':t['targetId']})['windowId'] for t in pages}
        result.update(concurrent_returns=returns, pages=len(pages), windows=len(windows))
        assert len(pages)==len(windows)==1, result
        if mode=='systemd':
            ctl('restart',unit)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                state=ctl('show',unit,'-p','MainPID','--value').stdout.strip()
                if state=='0': break
                time.sleep(.2)
            assert state=='0', 'reused observer did not finish'
        else:
            children[0].terminate(); assert children[0].wait(10)==0
            p=subprocess.Popen(argv,env=env,stdout=log,stderr=log); children.append(p)
            assert p.wait(10)==0
        after=browser.runtime(st)
        assert after and (after['pid'],after['starttime'],after['port'])==(rec['pid'],rec['starttime'],rec['port']), {'before':rec, 'after':after, 'pid_now':browser.proc_start_time(rec['pid']), 'unit':ctl('show',unit,'-p','NRestarts','-p','ExecMainStatus','-p','Restart','-p','KillMode','-p','MainPID').stdout, 'journal':subprocess.run(['journalctl','--user','-u',unit,'-n','15','--no-pager'],capture_output=True,text=True).stdout}
        result['restart_reused']=True
        # Close only this temporary browser's sole window, analogous to the owner's close button.
        with CDP(rec['ws']) as cdp:
            with contextlib.suppress(Exception): cdp.call('Target.closeTarget',{'targetId':pages[0]['targetId']})
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and browser.runtime(st): time.sleep(.2)
        assert browser.runtime(st) is None, 'window close did not exit browser'
        began=time.monotonic()
        while time.monotonic()-began<65:
            assert browser.runtime(st) is None, 'browser reopened'
            time.sleep(1)
        result['closed_observed_seconds']=round(time.monotonic()-began,1)
        events=[json.loads(line) for line in (st.root/'host.log').read_text().splitlines()]
        result['starts']=sum(e.get('ev')=='browser_started' for e in events)
        assert result['starts']==1, events
        if mode=='systemd':
            props=ctl('show',unit,'-p','NRestarts','-p','ExecMainStatus','-p','Restart','-p','KillMode').stdout
            result['systemd']=props
            assert 'NRestarts=0' in props and 'ExecMainStatus=0' in props and 'KillMode=process' in props
        result['ok']=True
        out.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    finally:
        if mode=='systemd':
            ctl('stop',unit,check=False); ctl('reset-failed',unit,check=False)
        for p in children:
            if p.poll() is None: p.terminate()
            p.wait(10)
        current=browser._read_json(browser.runtime_path(st))
        for owned in (rec,current):
            if isinstance(owned,dict) and owned.get('starttime') and browser.proc_start_time(owned['pid'])==owned['starttime']:
                with contextlib.suppress(ProcessLookupError): os.killpg(owned['pid'],signal.SIGTERM)
        log.close()
