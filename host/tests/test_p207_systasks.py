"""P207 §2: the computer's own scheduled tasks on the phone's 「定时任务」 page (systasks.py, PROTOCOL §8 `systask_list`).

Every source is fed fake tool output (no real system call): systemd --user timers (JSON + the text fallback), crontab,
macOS LaunchAgents plists + `launchctl list`, Windows `schtasks /query /fo csv /v` (English and localized headers). Redaction:
no argument, environment line, ExecStart or "Task To Run" text ever reaches a row. Bounds: ≤ MAX rows, a missing tool =
no rows, cache. Serve: the capability and the chunked read-only answer.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import systasks, wire  # noqa: E402

from test_l1 import Phone, _host, _ready, _state  # noqa: E402

SECRET = "sk-live-DO-NOT-LEAK-123"

TIMERS_JSON = json.dumps([
    {"next": 1791860400000000, "left": 1, "last": 1791849600444774, "passed": 1, "unit": "backup.timer", "activates": "backup.service"},
    {"next": 1791858554738013, "left": 1, "last": 1791858494736439, "passed": 1, "unit": "status.timer", "activates": "status.service"},
    {"next": None, "left": None, "last": None, "passed": None, "unit": "old.timer", "activates": "old.service"},
    {"next": 1791860000000000, "left": 1, "last": 1791850000000000, "passed": 1, "unit": "broken.timer", "activates": "broken.service"},
    {"next": 1791860000000000, "left": 1, "last": 0, "passed": 1, "unit": "busy.timer", "activates": "busy.service"},
])
SHOW = f"""Id=backup.timer
Description=Timer for backup
ActiveState=active
UnitFileState=enabled
TimersCalendar={{ OnCalendar=*-*-* 03:00:00 ; next_elapse=Wed 2026-10-14 03:00:00 JST }}
Result=success

Id=backup.service
Description=Nightly backup of photos
ActiveState=inactive
UnitFileState=static
Result=success
ExecStart={{ path=/usr/bin/rclone ; argv[]=/usr/bin/rclone sync --password {SECRET} ; }}

Id=status.timer
Description=status every minute
ActiveState=active
UnitFileState=enabled
TimersMonotonic={{ OnUnitActiveUSec=1min ; next_elapse=1d 11h }}
TimersMonotonic={{ OnBootUSec=30s ; next_elapse=30s }}
Result=success

Id=status.service
Description=
ActiveState=inactive
Result=success

Id=old.timer
Description=Old thing
ActiveState=inactive
UnitFileState=disabled
TimersCalendar={{ OnCalendar=Mon *-*-* 09:00:00 ; next_elapse=n/a }}
Result=success

Id=old.service
Description=Old job
ActiveState=inactive
Result=success

Id=broken.timer
Description=Broken timer
ActiveState=active
Result=success

Id=broken.service
Description=Broken job
ActiveState=failed
Result=exit-code

Id=busy.timer
Description=Busy timer
ActiveState=active
Result=success

Id=busy.service
Description=Busy job
ActiveState=activating
Result=success
"""
TIMERS_TEXT = """Tue 2026-10-13 11:29:14 JST 15s Tue 2026-10-13 11:28:14 JST 44s ago backup.timer   backup.service
-                            -   -                              -      old.timer      old.service
"""
CRONTAB = f"""# m h dom mon dow command
SHELL=/bin/bash
API_TOKEN={SECRET}
MAILTO=me@example.com

0 9 * * 1-5 /opt/me/bin/report.sh --token {SECRET} > /tmp/log 2>&1
*/15 * * * * cd /srv/app && ./sync.py --key={SECRET}
@daily   TOKEN={SECRET} nice -n 10 /usr/local/bin/cleanup --all
@reboot  bash /opt/me/scripts/start-tunnel.sh {SECRET}
30 2 1 * * timeout 30 python3 -u /opt/x/monthly.py {SECRET}
5 4 * * * curl -s https://example.com/hook?secret={SECRET}%some stdin
# 0 1 * * * /usr/bin/disabled-job {SECRET}
not a cron line at all
@hourly
"""
LAUNCHCTL = """PID\tStatus\tLabel
-\t0\tcom.me.backup
812\t0\tcom.me.running
-\t78\tcom.me.failing
-\t0\tcom.apple.something
"""
SCHTASKS_EN = f'''"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time","Last Result","Author","Task To Run","Start In","Comment","Scheduled Task State","Idle Time","Power Management","Run As User","Delete Task If Not Rescheduled","Stop Task If Runs X Hours and X Mins","Schedule","Schedule Type","Start Time","Start Date"
"PC","\\Backup Photos","10/14/2026 3:00:00 AM","Ready","Interactive only","10/13/2026 3:00:01 AM","0","PC\\me","C:\\tools\\backup.exe --password {SECRET}","C:\\tools","Copies photos","Enabled","Disabled","","PC\\me","Disabled","72:00:00","Scheduling data","Daily ","3:00:00 AM","1/1/2026"
"PC","\\Backup Photos","10/14/2026 3:00:00 AM","Ready","Interactive only","10/13/2026 3:00:01 AM","0","PC\\me","C:\\tools\\backup.exe --password {SECRET}","C:\\tools","Copies photos","Enabled","Disabled","","PC\\me","Disabled","72:00:00","Scheduling data","At logon time","N/A","N/A"
"PC","\\Microsoft\\Windows\\Defrag\\ScheduledDefrag","N/A","Ready","Interactive/Background","N/A","0","Microsoft","defrag.exe -c","N/A","N/A","Enabled","","","SYSTEM","","","","Weekly","1:00:00 AM","1/1/2005"
"PC","\\Vendor\\Broken","N/A","Ready","Interactive only","10/12/2026 1:00:00 AM","1","PC\\me","C:\\v\\x.exe {SECRET}","N/A","N/A","Enabled","","","PC\\me","","","","Weekly","1:00:00 AM","1/1/2026"
"PC","\\Off","N/A","Disabled","Interactive only","11/30/1999 12:00:00 AM","267011","PC\\me","C:\\off.exe","N/A","N/A","Disabled","","","PC\\me","","","","One Time Only","9:00:00 AM","1/1/2026"
"PC","\\OtherUser","N/A","Ready","Interactive only","N/A","0","PC\\bob","C:\\b.exe","N/A","N/A","Enabled","","","PC\\bob","","","","Daily","9:00:00 AM","1/1/2026"
"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time","Last Result","Author","Task To Run","Start In","Comment","Scheduled Task State","Idle Time","Power Management","Run As User","Delete Task If Not Rescheduled","Stop Task If Runs X Hours and X Mins","Schedule","Schedule Type","Start Time","Start Date"
"PC","\\Running","N/A","Running","Interactive only","10/13/2026 9:00:00 AM","267009","PC\\me","C:\\r.exe","N/A","N/A","Enabled","","","PC\\me","","","","Daily","9:00:00 AM","1/1/2026"
'''
SCHTASKS_ZH = f'''"主机名","任务名","下次运行时间","模式","登录状态","上次运行时间","上次结果","创建者","要运行的任务","起始于","注释","计划任务状态","空闲时间","电源管理","作为用户运行","删除没有计划的任务","如果运行了 X 小时 X 分钟，停止任务","计划","计划类型","开始时间","开始日期"
"PC","\\每日报告","2026/10/14 9:00:00","就绪","只使用交互方式","2026/10/13 9:00:00","0","PC\\me","C:\\r.exe {SECRET}","N/A","N/A","已启用","","","PC\\me","","","","每天 ","9:00:00","2026/1/1"
'''


def fake_run(table):
    calls = []

    def run(argv):
        calls.append(list(argv))
        key = " ".join(argv[:3])
        for k, v in table.items():
            if key.startswith(k) or " ".join(argv).startswith(k):
                if isinstance(v, Exception):
                    raise v
                return v
        return None
    run.calls = calls
    return run


def blob(rows) -> str:
    return json.dumps(rows, ensure_ascii=False)


class Systemd(unittest.TestCase):
    def test_json_timers_with_descriptions_schedule_and_states(self):
        run = fake_run({"systemctl --user list-timers": TIMERS_JSON, "systemctl --user show": SHOW})
        rows = {r["id"]: r for r in systasks.scan_systemd(run)}
        self.assertEqual(len(rows), 5)
        b = rows["backup.timer"]
        self.assertEqual(b["name"], "Nightly backup of photos", "the service's Description wins")
        self.assertEqual((b["src"], b["state"], b["next"], b["last"]), ("systemd", "waiting", 1791860400, 1791849600))
        self.assertEqual(b["when"], [{"k": "cal", "v": "*-*-* 03:00:00"}])
        s = rows["status.timer"]
        self.assertEqual(s["name"], "status every minute", "empty service Description → the timer's")
        self.assertEqual(s["when"], [{"k": "every", "v": 60}, {"k": "boot", "v": 30}])
        self.assertEqual(rows["old.timer"]["state"], "disabled")
        self.assertNotIn("next", rows["old.timer"])
        self.assertEqual(rows["broken.timer"]["state"], "failed")
        self.assertEqual(rows["busy.timer"]["state"], "active")
        self.assertNotIn("last", rows["busy.timer"])
        self.assertNotIn(SECRET, blob(list(rows.values())))
        show = [c for c in run.calls if c[2] == "show"][0]
        self.assertNotIn("ExecStart", " ".join(show), "the command line is never asked for")
        self.assertIn("--", show, "unit names cannot be read as options")

    def test_text_fallback_when_json_output_is_unsupported(self):
        run = fake_run({"systemctl --user list-timers --all --output=json": None,
                        "systemctl --user list-timers --all --no-pager --no-legend": TIMERS_TEXT,
                        "systemctl --user show": SHOW})

        def run2(argv):
            if "--output=json" in argv:
                return "Unknown output 'json'"            # not JSON: treated like an old systemd
            return run(argv)
        rows = {r["id"]: r for r in systasks.scan_systemd(run2)}
        self.assertEqual(set(rows), {"backup.timer", "old.timer"})
        self.assertEqual(rows["backup.timer"]["name"], "Nightly backup of photos")
        self.assertNotIn("next", rows["backup.timer"])

    def test_missing_systemctl_is_empty(self):
        self.assertEqual(systasks.scan_systemd(lambda argv: None), [])
        self.assertEqual(systasks.scan("linux", run=lambda argv: None), [])

    def test_span_parser(self):
        self.assertEqual(systasks._span_secs("1h 30min"), 5400)
        self.assertEqual(systasks._span_secs("1d 11h 57min 934.185ms"), 129420)
        self.assertIsNone(systasks._span_secs("bogus"))
        self.assertIsNone(systasks._span_secs("5parsecs"))


class Cron(unittest.TestCase):
    def test_schedule_and_redacted_command_names_only(self):
        rows = systasks.parse_crontab(CRONTAB)
        self.assertEqual([(r["id"], r["when"]) for r in rows], [
            ("report.sh", [{"k": "cron", "v": "0 9 * * 1-5"}]),
            ("sync.py", [{"k": "cron", "v": "*/15 * * * *"}]),
            ("cleanup", [{"k": "cron", "v": "@daily"}]),
            ("start-tunnel.sh", [{"k": "boot", "v": 0}]),
            ("monthly.py", [{"k": "cron", "v": "30 2 1 * *"}]),
            ("curl", [{"k": "cron", "v": "5 4 * * *"}]),
        ])
        self.assertTrue(all(r["src"] == "cron" and r["state"] == "waiting" and r["name"] == r["id"] for r in rows))
        text = blob(rows)
        for leak in (SECRET, "--token", "/opt/me", "example.com", "MAILTO", "SHELL", "disabled-job", "-n 10", "/srv/app"):
            self.assertNotIn(leak, text)

    def test_odd_lines(self):
        self.assertEqual(systasks.parse_crontab(""), [])
        self.assertEqual(systasks.parse_crontab("* * * *\n@weekly\nFOO = bar baz qux one two\n"), [])
        self.assertEqual(systasks._cmd_name("'/opt/My App/run' --pw x"), "run")
        self.assertEqual(systasks._cmd_name("A=1 B=2 env C=3 /x/y/z.sh a b"), "z.sh")
        self.assertEqual(systasks._cmd_name("cd ~ ; ./go"), "go")
        self.assertEqual(systasks._cmd_name("$HOME/bin/job --x"), "job")
        # review P207: combined flags carry inline code — never a word of it reaches the phone
        self.assertEqual(systasks._cmd_name('bash -ec "export TOKEN=sk-abc; run"'), "bash")
        self.assertEqual(systasks._cmd_name("python3 -uc 'import os; print(1)'"), "python3")
        self.assertEqual(systasks._cmd_name("bash -x ~/bin/job.sh arg"), "job.sh")
        self.assertEqual(systasks._cmd_name("bash -c 'echo hi'"), "bash")
        self.assertEqual(systasks._cmd_name("   "), "?")
        rows = systasks.parse_crontab("\n".join(f"* * * * * /bin/j{i}" for i in range(500)))
        self.assertEqual(len(rows), systasks.MAX)

    def test_missing_crontab_tool(self):
        self.assertEqual(systasks.scan_cron(lambda argv: None), [])
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("crontab")):
            self.assertIsNone(systasks._run(["crontab", "-l"]))
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("crontab", 5)):
            self.assertIsNone(systasks._run(["crontab", "-l"]))

    def test_run_never_uses_a_shell_and_has_a_timeout(self):
        done = subprocess.CompletedProcess(["x"], 0, b"ok", b"")
        with mock.patch.object(subprocess, "run", return_value=done) as m:
            self.assertEqual(systasks._run(["crontab", "-l"]), "ok")
        args, kw = m.call_args
        self.assertEqual(args[0], ["crontab", "-l"])
        self.assertNotIn("shell", kw)
        self.assertEqual(kw["timeout"], systasks.TIMEOUT)
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(["x"], 1, b"no crontab", b"")):
            self.assertIsNone(systasks._run(["crontab", "-l"]))


class Launchd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        d = pathlib.Path(self.home, "Library", "LaunchAgents")
        d.mkdir(parents=True)

        def put(name, pl):
            (d / name).write_bytes(plistlib.dumps(pl))
        put("com.me.backup.plist", {"Label": "com.me.backup", "ProgramArguments": ["/bin/backup", "--key", SECRET],
                                    "StartCalendarInterval": {"Hour": 3, "Minute": 15}})
        put("com.me.running.plist", {"Label": "com.me.running", "Program": "/x", "StartInterval": 600,
                                     "EnvironmentVariables": {"TOKEN": SECRET}})
        put("com.me.failing.plist", {"Label": "com.me.failing", "StartCalendarInterval": [{"Weekday": 1, "Hour": 9, "Minute": 0},
                                                                                            {"Weekday": 5, "Hour": 17, "Minute": 30}]})
        put("com.me.unloaded.plist", {"Label": "com.me.unloaded", "StartInterval": 3600, "Disabled": True})
        put("com.me.daemon.plist", {"Label": "com.me.daemon", "KeepAlive": True, "ProgramArguments": ["/d"]})
        (d / "broken.plist").write_text("not a plist")
        (d / "notes.txt").write_text("ignored")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scheduled_agents_with_loaded_state(self):
        run = fake_run({"launchctl list": LAUNCHCTL})
        rows = {r["id"]: r for r in systasks.scan_launchd(run, self.home)}
        self.assertEqual(set(rows), {"com.me.backup", "com.me.running", "com.me.failing", "com.me.unloaded"})
        self.assertEqual(rows["com.me.backup"]["when"], [{"k": "cron", "v": "15 3 * * *"}])
        self.assertEqual(rows["com.me.backup"]["state"], "waiting")
        self.assertEqual(rows["com.me.running"]["when"], [{"k": "every", "v": 600}])
        self.assertEqual(rows["com.me.running"]["state"], "active")
        self.assertEqual(rows["com.me.failing"]["when"], [{"k": "cron", "v": "0 9 * * 1"}, {"k": "cron", "v": "30 17 * * 5"}])
        self.assertEqual(rows["com.me.failing"]["state"], "failed")
        self.assertEqual(rows["com.me.unloaded"]["state"], "disabled")
        self.assertTrue(all(r["src"] == "launchd" for r in rows.values()))
        self.assertNotIn(SECRET, blob(list(rows.values())))
        self.assertNotIn("/bin/backup", blob(list(rows.values())))

    def test_darwin_scan_adds_crontab_and_survives_missing_tools(self):
        rows = systasks.scan("darwin", run=fake_run({"launchctl list": None, "crontab -l": "0 1 * * * /x/nightly"}), home=self.home)
        self.assertEqual(sorted({r["src"] for r in rows}), ["cron", "launchd"])
        self.assertTrue(all(r["state"] == "disabled" for r in rows if r["src"] == "launchd"), "launchctl missing → not loaded")
        self.assertEqual(systasks.scan_launchd(lambda a: None, self.home + "/nowhere"), [])


class Windows(unittest.TestCase):
    def test_english_csv_current_user_only_no_command(self):
        rows = systasks.parse_schtasks(SCHTASKS_EN, "me")
        by = {r["id"]: r for r in rows}
        self.assertEqual(list(by), ["Backup Photos", "Broken", "Off", "Running"], "deduped, \\Microsoft\\ and other users dropped")
        b = by["Backup Photos"]
        self.assertEqual((b["name"], b["state"], b["src"]), ("Copies photos", "waiting", "schtasks"))
        self.assertEqual(b["when"], [{"k": "raw", "v": "Daily 3:00:00 AM"}])
        self.assertEqual((b["next_s"], b["last_s"]), ("10/14/2026 3:00:00 AM", "10/13/2026 3:00:01 AM"))
        self.assertEqual(by["Broken"]["state"], "failed")
        self.assertEqual(by["Off"]["state"], "disabled")
        self.assertNotIn("last_s", by["Off"], "the never-run 1999 date is not shown")
        self.assertEqual(by["Running"]["state"], "active")
        text = blob(rows)
        for leak in (SECRET, "backup.exe", "C:\\\\tools", "defrag"):
            self.assertNotIn(leak, text)

    def test_localized_headers_by_column_order(self):
        rows = systasks.parse_schtasks(SCHTASKS_ZH, "me")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["id"], rows[0]["when"][0]["v"], rows[0]["next_s"]), ("每日报告", "每天 9:00:00", "2026/10/14 9:00:00"))
        self.assertNotIn(SECRET, blob(rows))

    def test_scan_win32_and_garbage(self):
        rows = systasks.scan("win32", run=fake_run({"schtasks /query": SCHTASKS_EN}))
        self.assertTrue(rows and all(r["src"] == "schtasks" for r in rows))
        self.assertEqual(systasks.parse_schtasks("garbage,\n\"a\",\"b\""), [])
        self.assertEqual(systasks.scan("win32", run=lambda a: None), [])


class Bounds(unittest.TestCase):
    def test_one_failing_source_does_not_hide_the_other(self):
        def run(argv):
            if argv[0] == "systemctl":
                raise RuntimeError("surprise")
            return "0 9 * * * /bin/daily"
        rows = systasks.scan("linux", run=run)
        self.assertEqual([r["id"] for r in rows], ["daily"])

    def test_total_bound_and_string_bounds(self):
        many = "\n".join(f"* * * * * /bin/j{i}" for i in range(300))
        big = json.dumps([{"unit": f"t{i}.timer", "activates": f"t{i}.service", "next": 1, "last": 1} for i in range(300)])
        rows = systasks.scan("linux", run=fake_run({"systemctl --user list-timers": big, "systemctl --user show":
                                                    "Id=t0.service\nDescription=" + "长" * 500 + "\n", "crontab -l": many}))
        self.assertEqual(len(rows), systasks.MAX)
        self.assertLessEqual(max(len(r["name"]) for r in rows), 120)

    def test_cache(self):
        systasks._cache.clear()
        with mock.patch.object(systasks, "scan", return_value=[{"src": "cron", "id": "a"}]) as m:
            self.assertEqual(systasks.rows(now=1000.0)[0]["id"], "a")
            systasks.rows(now=1030.0)
            self.assertEqual(m.call_count, 1)
            systasks.rows(now=1000.0 + systasks.TTL + 1)
            self.assertEqual(m.call_count, 2)
        systasks._cache.clear()


class Serve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.sent = []
        systasks._cache.clear()

    def tearDown(self):
        systasks._cache.clear()
        self.tmp.cleanup()

    def test_capability_and_chunked_read_only_answer(self):
        ph, host = Phone(self.st), _host(self.st, self.sent)
        s = _ready(host, ph)
        self.assertIn("systasks", host._caps()["caps"])
        fake = [systasks._row("systemd", f"t{i}.timer", "定时" * 50, [{"k": "cal", "v": "*-*-* 03:00:00"}], "waiting", 1791860400, 1791849600)
                for i in range(systasks.MAX)]

        async def send_app(sess, obj):
            wire.pad_json(obj)                         # raises if a message would not fit
            self.sent.append((sess.cid, obj))
            return True
        host.send_app = send_app

        async def go():
            with mock.patch.object(systasks, "scan", return_value=fake):
                await host._app(s, {"t": "systask_list", "r": "s1"})
        asyncio.run(go())
        pages = [o for _, o in self.sent if o.get("t") == "systasks" and o.get("r") == "s1"]
        self.assertGreater(len(pages), 1)
        self.assertEqual(sum(len(p["items"]) for p in pages), systasks.MAX)
        self.assertEqual((pages[-1]["more"], pages[-1]["max"]), (False, systasks.MAX))
        self.assertTrue(all(set(it) <= {"src", "id", "name", "when", "next", "last", "next_s", "last_s", "state"}
                            for p in pages for it in p["items"]))


if __name__ == "__main__":
    unittest.main()
