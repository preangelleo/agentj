"""P181: the load card's five resources (PROTOCOL §22.1) and the header rename (§22.2).

Resources: the parsers (Linux /proc, macOS vm_stat / netstat -ib / ioreg, nvidia-smi) on fixed text; a real Linux sample has
every field in range; the rates need two samples; no field is ever invented (None = 「—」). Serve: nothing is sampled until a
ready p33 session watches; only watching sessions get `res`; the loop ends when the last watch is withdrawn or expires.
Rename: `agent_name` is a signed phone control — unsigned / other key / re-pointed do nothing; the short-name rule of
`agentj name` applies; an accepted one changes the stored name, is logged in controls.log + activity and pushes a status.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import activity, controls, resources, serve  # noqa: E402

from test_controls import sign  # noqa: E402
from test_l1 import Phone, _host, _ready, _state  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent

STAT1 = "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3 4\n"
STAT2 = "cpu  160 0 70 880 70 0 0 0 0 0\n"
MEMINFO = "MemTotal:       16000000 kB\nMemFree:  1000000 kB\nMemAvailable:    4000000 kB\nCached: 3000000 kB\n"
DISKSTATS = ("   7       0 loop0 1 0 2 0 0 0 0 0 0 999999 0\n"
             " 259       0 nvme0n1 100 0 200 30 50 0 80 20 0 5000 50\n"
             " 259       1 nvme0n1p1 100 0 200 30 50 0 80 20 0 4000 50\n"
             " 253       0 dm-0 1 0 2 0 0 0 0 0 0 7777 0\n"
             "   8       0 sda 1 0 2 0 0 0 0 0 0 1200 0\n")
NETDEV = ("Inter-|   Receive                                                |  Transmit\n"
          " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
          "    lo: 9999999     10    0    0    0     0          0         0  9999999      10    0    0    0     0       0          0\n"
          "  wlo1: 1000000   100    0    0    0     0          0         0   200000     50    0    0    0     0       0          0\n"
          "docker0: 5555555  1    0    0    0     0          0         0  5555555      1    0    0    0     0       0          0\n"
          "  eth0:  500000    10    0    0    0     0          0         0   100000     10    0    0    0     0       0          0\n")
VM_STAT = ('Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:                               10000.\n'
           'Pages active:                            200000.\nPages inactive:                          190000.\n'
           'Pages wired down:                        100000.\n"Translation faults":                 1.\n'
           'Pages occupied by compressor:             50000.\n')
NETSTAT_IB = ("Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll\n"
              "lo0        16384 <Link#1>                        100     0      99999      100     0      99999     0\n"
              "en0        1500  <Link#6>    aa:bb:cc:dd:ee:ff   1000     0    5000000      900     0    1000000     0\n"
              "en0        1500  10.20.30      10.20.30.10       1000     -    5000000      900     -    1000000     -\n"
              "utun0      1380  <Link#9>                        10     0       7777       10     0       7777     0\n")
IOREG_GPU = '| "PerformanceStatistics" = {"In use system memory"=1,"Device Utilization %"=37,"Renderer Utilization %"=30}\n'
IOREG_DISK = ('"Statistics" = {"Total Time (Read)"=1000000000,"Total Time (Write)"=500000000,"Bytes (Read)"=1}\n'
              '"Statistics" = {"Total Time (Read)"=200,"Total Time (Write)"=300}\n')


class Parsers(unittest.TestCase):
    def test_linux(self):
        self.assertEqual(resources.parse_proc_stat(STAT1), (150, 1000))
        self.assertEqual(resources.parse_proc_stat(STAT2), (230, 1180))
        self.assertAlmostEqual(resources.parse_meminfo(MEMINFO), 0.75)
        self.assertEqual(resources.parse_diskstats(DISKSTATS), {"nvme0n1": 5000, "sda": 1200})
        self.assertEqual(resources.parse_net_dev(NETDEV), (1500000, 300000), "lo / docker never count")
        self.assertEqual(resources.parse_net_dev(NETDEV, {"wlo1"}), (1000000, 200000))
        self.assertIsNone(resources.parse_proc_stat(""))
        self.assertIsNone(resources.parse_meminfo("garbage"))

    def test_nvidia(self):
        self.assertEqual(resources.parse_nvidia("12, NVIDIA RTX A\n87, NVIDIA RTX B\n"), (0.87, "NVIDIA RTX B"))
        self.assertIsNone(resources.parse_nvidia("[N/A], X\n"))
        self.assertIsNone(resources.parse_nvidia(None))

    def test_macos(self):
        self.assertAlmostEqual(resources.parse_vm_stat(VM_STAT, 16 * 2**30), 350000 * 16384 / (16 * 2**30), places=3)
        self.assertIsNone(resources.parse_vm_stat(VM_STAT, None))
        self.assertEqual(resources.parse_netstat_ib(NETSTAT_IB), (5000000, 1000000), "one Link row per interface, lo/utun out")
        self.assertEqual(resources.parse_ioreg_gpu(IOREG_GPU), 0.37)
        self.assertIsNone(resources.parse_ioreg_gpu("nothing"))
        self.assertEqual(resources.parse_ioreg_disk(IOREG_DISK), 1500000500)

    def test_message_drops_unknowns(self):
        m = resources.message({"cpu": 0.5, "mem": None, "gpu": None, "disk": {"free": 0.4, "free_gb": 100.0, "busy": None},
                               "net": {"rx": 1, "tx": 2}}, 0.3)
        self.assertEqual(m, {"t": "res", "cpu": 0.5, "disk": {"free": 0.4, "free_gb": 100.0, "busy": None},
                             "net": {"rx": 1, "tx": 2}, "load": 0.3})


class Sampler(unittest.TestCase):
    def test_rates_need_two_samples(self):
        clock = iter([0.0, 2.5])
        files = {"/proc/stat": [STAT1, STAT2], "/proc/meminfo": [MEMINFO, MEMINFO], "/proc/diskstats": [DISKSTATS, DISKSTATS.replace(" 5000 ", " 6000 ")],
                 "/proc/net/dev": [NETDEV, NETDEV.replace("1000000   100", "1250000   100")]}

        def read(p):
            v = files.get(str(p))
            return v.pop(0) if v else None
        with mock.patch.object(resources, "_r", side_effect=read), mock.patch.object(resources.shutil, "which", return_value=None), \
                mock.patch.object(resources, "_phys_ifaces", return_value={"wlo1"}):
            s = resources.Sampler(platform="linux", home=tempfile.gettempdir(), now=lambda: next(clock))
            a = s.sample()
            b = s.sample()
        self.assertIsNone(a["cpu"])
        self.assertIsNone(a["net"])
        self.assertIsNone(a["disk"]["busy"])
        self.assertAlmostEqual(a["mem"], 0.75)
        self.assertAlmostEqual(b["cpu"], 80 / 180, places=3)
        self.assertAlmostEqual(b["disk"]["busy"], 0.4)              # 1000 ms busy in 2.5 s
        self.assertEqual(b["net"], {"rx": 100000, "tx": 0})
        self.assertIsNone(b["gpu"], "no GPU reader → None, never a guess")
        self.assertGreater(b["disk"]["free_gb"], 0)

    def test_nvidia_asleep_is_not_woken(self):
        calls = []
        with mock.patch.object(resources.shutil, "which", return_value="/usr/bin/nvidia-smi"), \
                mock.patch.object(resources, "_nvidia_asleep", return_value=True), \
                mock.patch.object(resources, "_run", side_effect=lambda *a, **k: calls.append(a)):
            s = resources.Sampler(platform="linux", home=tempfile.gettempdir())
            with mock.patch.object(resources.os, "listdir", side_effect=OSError):
                g = s._gpu(0.0)
        self.assertEqual(g, {"pct": 0.0, "sleep": True})
        self.assertEqual(calls, [])

    def test_nvidia_at_most_every_gpu_every(self):
        calls = []

        def run(cmd, timeout=None):
            calls.append(cmd)
            return "42, NVIDIA X\n"
        with mock.patch.object(resources.shutil, "which", return_value="/usr/bin/nvidia-smi"), \
                mock.patch.object(resources, "_nvidia_asleep", return_value=False), mock.patch.object(resources, "_run", side_effect=run), \
                mock.patch.object(resources.os, "listdir", side_effect=OSError):
            s = resources.Sampler(platform="linux", home=tempfile.gettempdir())
            got = [s._gpu(t) for t in (0.0, 2.5, 5.0, 7.5, 10.0)]
        self.assertEqual(len(calls), 3)
        self.assertEqual(got[1], {"pct": 0.42, "name": "NVIDIA X"})

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux only")
    def test_real_linux_sample_in_range(self):
        s = resources.Sampler()
        s.sample()
        time.sleep(0.3)
        x = s.sample()
        self.assertTrue(0 <= x["cpu"] <= 1)
        self.assertTrue(0 < x["mem"] < 1)
        self.assertTrue(0 <= x["disk"]["free"] <= 1)
        self.assertGreaterEqual(x["net"]["rx"], 0)

    def test_cheap(self):
        m = resources.measure(10)
        self.assertLess(m["cpu_ms_per_sample"], 60, m)


class Serve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def got(self, t, r=None, cid=None):
        return [o for s, o in self.sent if o["t"] == t and (r is None or o.get("r") == r) and (cid is None or s == cid)]

    def host(self):
        ph, other = Phone(self.st, "iPhone"), Phone(self.st, "Pixel")
        host = _host(self.st, self.sent)
        s = _ready(host, ph)
        s.p33 = True
        s2 = _ready(host, other, cid=31)
        s2.p33 = True
        return ph, host, s, s2

    def test_watch_only_while_open(self):
        ph, host, s, s2 = self.host()
        n = []

        class Fake:
            def sample(self):
                n.append(1)
                return {"cpu": 0.25, "mem": 0.5, "gpu": None, "disk": {"free": 0.5, "free_gb": 9.0, "busy": 0.1}, "net": {"rx": 10, "tx": 20}}

        async def go():
            await asyncio.sleep(0.05)
            self.assertIsNone(host.res_task, "nothing runs before a card opens")
            await host._app(s, {"t": "res_watch", "on": True})
            await asyncio.sleep(0.25)
            self.assertTrue(self.got("res", cid=s.cid))
            self.assertFalse(self.got("res", cid=s2.cid), "only the watching session")
            await host._app(s, {"t": "res_watch", "on": False})
            await asyncio.sleep(0.25)
            self.assertTrue(host.res_task.done(), "the last watch ended → sampling stopped")
            before = len(n)
            await asyncio.sleep(0.2)
            self.assertEqual(len(n), before)
            await host._app(s2, {"t": "res_watch", "on": True})
            host.res_watch[s2.cid] = time.monotonic() + 0.1      # an expiring watch (the phone vanished)
            await asyncio.sleep(0.4)
            self.assertTrue(host.res_task.done(), "an expired watch stops it too")
        with mock.patch.object(resources, "Sampler", Fake), mock.patch.object(resources, "EVERY", 0.05), \
                mock.patch.object(resources, "FIRST", 0.01):
            asyncio.run(go())
        m = self.got("res", cid=s.cid)[0]
        self.assertEqual((m["cpu"], m["mem"], m["net"]), (0.25, 0.5, {"rx": 10, "tx": 20}))
        self.assertNotIn("gpu", m)

    def test_old_page_not_served(self):
        ph, host, s, s2 = self.host()
        s.p33 = False

        async def go():
            await host._app(s, {"t": "res_watch", "on": True})
            await asyncio.sleep(0.05)
        asyncio.run(go())
        self.assertEqual(host.res_watch, {})

    def controls_log(self):
        return [json.loads(x) for x in self.st.controls_path.read_text().splitlines()] if self.st.controls_path.exists() else []

    def test_rename_signed_only(self):
        ph, host, s, s2 = self.host()
        other = Phone(self.st, "x")
        old = self.st.agent_name()
        tgt = {"name": "小助"}

        async def go():
            await host._app(s, {"t": "agent_name", "r": "u", **tgt})
            await host._app(s, sign(ph, "agent_name", tgt, {"t": "agent_name", "r": "k", **tgt}, key=other.sk))
            await host._app(s, sign(ph, "agent_name", {"name": "别名"}, {"t": "agent_name", "r": "p", **tgt}))
            long = {"name": "一二三四五六七八九"}
            await host._app(s, sign(ph, "agent_name", long, {"t": "agent_name", "r": "long", **long}))
            await host._app(s, sign(ph, "agent_name", tgt, {"t": "agent_name", "r": "ok", **tgt}))
            await asyncio.sleep(0.05)
        asyncio.run(go())
        res = {o["r"]: o for o in self.got("ctl_res")}
        self.assertEqual([res[r].get("why") for r in ("u", "k", "p")], ["shape", "bad_signature", "bad_signature"])
        self.assertEqual(res["long"]["why"], "name_too_long", "the same short-name rule as `agentj name`")
        self.assertTrue(res["ok"]["ok"])
        self.assertEqual(res["ok"]["name"], "小助")
        self.assertNotEqual(old, "小助")
        self.assertEqual(self.st.agent_name(), "小助")
        self.assertEqual([c["result"] for c in self.controls_log()],
                         ["refused:shape", "refused:bad_signature", "refused:bad_signature", "name_too_long", "ok"])
        self.assertIn("小助", [r.get("text") for r in activity.all_since(self.st) if r["k"] == "decision"])
        self.assertTrue([o for _, o in self.sent if o["t"] == "status" and o.get("name") == "小助"], "every phone sees the new name")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_and_python_sign_the_same_bytes(self):
        cases = [("agent_name", {"name": "小助 J"})]
        for wire_js in (HERE.parents[1] / "protocol" / "wire.js", HERE.parents[1] / "web" / "public" / "proto" / "wire.js"):
            js = (f"import {{ controlMessage }} from {json.dumps(wire_js.as_uri())};\n"
                  f"const out = [];\nfor (const [a, o] of {json.dumps(cases, ensure_ascii=False)}) out.push(Buffer.from(await "
                  f"controlMessage('CH', 'DEV', a, '{'b' * 32}', 1790000000123, o)).toString('hex'));\nconsole.log(JSON.stringify(out));")
            r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            want = [controls.signed_message("CH", "DEV", a, "b" * 32, 1790000000123, controls.object_digest(a, o)).hex() for a, o in cases]
            self.assertEqual(json.loads(r.stdout), want, wire_js)


if __name__ == "__main__":
    unittest.main()
