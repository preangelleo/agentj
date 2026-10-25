"""Opt-in Linux/Hyprland qualification: four isolated seats, no owner services/profiles.

AGENTJ_TEST_CHROME=/path/chrome PYTHONPATH=. python tests/p521_multiseat_live.py /tmp/evidence.json
"""
import concurrent.futures
import contextlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from agentj import browser, browser_sites
from agentj.browser_cdp import CDP
from agentj.state import State


def main():
    result = {}
    with tempfile.TemporaryDirectory(prefix='aj-p521-seats-') as td:
        root = pathlib.Path(td)
        worker = root / 'worker.py'
        worker.write_text('''import pathlib, sys
from unittest.mock import patch
from agentj import browser
from agentj.state import State
with patch.object(browser, 'installed', return_value={'path':sys.argv[2], 'version':'test'}):
    raise SystemExit(browser.run(State(pathlib.Path(sys.argv[1]))))
''')
        children, records = [], []
        seats = [State(root / str(n)) for n in range(1, 5)]
        exe = os.environ['AGENTJ_TEST_CHROME']
        env = dict(os.environ, PYTHONPATH=str(pathlib.Path(__file__).resolve().parents[1]),
                   AGENTJ_TEST='1', XDG_CONFIG_HOME=str(root / 'config'), AGENTJ_BROWSER_DETECT='off')
        commands = [[sys.executable, str(worker), str(st.root), exe] for st in seats]
        envs = [dict(env, AGENTJ_INSTANCE=str(n), AGENTJ_STATE_DIR=str(st.root)) for n, st in enumerate(seats, 1)]
        def visible_count():
            # Read desktop window inventory, retain only PIDs belonging to these temporary profiles.
            clients = json.loads(subprocess.check_output(['hyprctl', 'clients', '-j']))
            pids = {r['pid'] for r in records}
            return sum(c.get('pid') in pids and c.get('mapped', True) for c in clients)
        log = (root / 'workers.log').open('wb')
        try:
            for cmd, e in zip(commands, envs):
                children.append(subprocess.Popen(cmd, env=e, stdout=log, stderr=log))
            end = time.monotonic() + 40
            while time.monotonic() < end:
                records = [browser.runtime(st) for st in seats]
                if all(records):
                    break
                time.sleep(.2)
            assert all(records), (root / 'workers.log').read_text()
            assert [r['headless'] for r in records] == [False, True, True, True], records
            assert len({r['pid'] for r in records}) == len({r['port'] for r in records}) == 4
            end = time.monotonic() + 10
            while time.monotonic() < end and visible_count() == 0:
                time.sleep(.2)
            result['default_visible_windows'] = visible_count()
            assert result['default_visible_windows'] == 1, result
            # Background tabs must not create visible windows. Allow the 30-second display-arrival path to run.
            for r in records[1:]:
                with CDP(r['ws']) as cdp:
                    tab = cdp.new_tab('about:blank', background=True)
                    cdp.close_tab(tab)
            began = time.monotonic()
            while time.monotonic() - began < 36:
                assert visible_count() == 1
                time.sleep(1)
            result['quiet_observed_seconds'] = round(time.monotonic() - began, 1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                returns = list(pool.map(lambda pair: subprocess.run(pair[0], env=pair[1], stdout=log, stderr=log, timeout=30).returncode,
                                        zip(commands, envs)))
            assert returns == [0] * 4
            assert [browser.runtime(st)['pid'] for st in seats] == [r['pid'] for r in records]
            result['restart_reused_all_four'] = True
            # Explicit open may show this seat, preserving its profile. No external website is visited.
            marker = seats[1].root / 'browser' / 'profile' / 'p521-profile-marker'
            marker.write_text('kept')
            with patch.object(browser, 'installed', return_value={'path':exe, 'version':'test'}):
                shown = browser_sites.open_url(seats[1], 'http://127.0.0.1:1')
            records[1] = browser.runtime(seats[1])
            assert shown['ok'] and not shown['headless'] and marker.read_text() == 'kept'
            end = time.monotonic() + 10
            while time.monotonic() < end and visible_count() != 2:
                time.sleep(.2)
            assert visible_count() == 2, 'explicit secondary open must show a window'
            result['explicit_open_visible_windows'] = 2
            result['same_profile_after_show'] = records[1]['profile'] == str(browser.profile_dir(seats[1]))
            assert result['same_profile_after_show']
            # Close the explicitly shown seat, verify no service/observer reopens it for more than a minute.
            with CDP(records[1]['ws']) as cdp:
                for tab in cdp.call('Target.getTargets')['targetInfos']:
                    if tab['type'] == 'page':
                        cdp.close_tab(tab['targetId'])
            end = time.monotonic() + 10
            while browser.runtime(seats[1]) and time.monotonic() < end:
                time.sleep(.2)
            assert browser.runtime(seats[1]) is None
            began = time.monotonic()
            while time.monotonic() - began < 65:
                assert browser.runtime(seats[1]) is None
                assert visible_count() <= 1
                time.sleep(1)
            result['secondary_close_observed_seconds'] = round(time.monotonic() - began, 1)
            result['ok'] = True
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.wait(10)
            for st in seats:
                rec = browser._read_json(browser.runtime_path(st))
                if isinstance(rec, dict):
                    records.append(rec)
            for rec in records:
                if isinstance(rec, dict) and rec.get('starttime') and browser.proc_start_time(rec['pid']) == rec['starttime']:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(rec['pid'], signal.SIGTERM)
            end = time.monotonic() + 10
            while time.monotonic() < end and any(isinstance(r, dict) and browser.proc_start_time(r['pid']) == r['starttime'] for r in records):
                time.sleep(.2)
            assert not any(isinstance(r, dict) and browser.proc_start_time(r['pid']) == r['starttime'] for r in records), 'temporary browser cleanup failed'
            log.close()
            result['temporary_profiles_cleaned'] = True
    pathlib.Path(sys.argv[1]).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
