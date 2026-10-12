"""P181 「负载卡五项」 (PROTOCOL §22.1): CPU, memory, GPU, disk and network of this computer, for the load card on the phone.

Cost rule (owner, 2026-10-12: 「在不影响后台、不增加后台负担的情况下」): nothing here runs unless a phone has the load card
open. The page sends `{"t":"res_watch","on":true}` when the card opens (again every RENEW s while it stays open) and
`on:false` when it closes; serve samples every EVERY s while at least one ready session watches and stops the moment the last
watch ends or expires (WATCH_TTL — a phone that vanished never keeps it running). Every sample is cheap reads:

- Linux: /proc/stat (CPU), /proc/meminfo (memory), /proc/diskstats io_ticks (disk busy), /proc/net/dev on interfaces with
  a real device (network), statvfs of the home folder (free space); GPU = /sys/class/drm/*/device/gpu_busy_percent (AMD /
  some Intel) or `nvidia-smi` (at most every GPU_EVERY s, and never when the NVIDIA card is runtime-suspended: asking would
  wake it up);
- macOS: host_statistics through ctypes (CPU), `vm_stat` + hw.memsize (memory), `ioreg` IOBlockStorageDriver statistics
  (disk busy), `netstat -ib` (network), `ioreg` IOAccelerator 「Device Utilization %」 (GPU, Apple silicon; no sudo — not
  powermetrics, which needs root);
- Windows: GetSystemTimes (CPU), GlobalMemoryStatusEx (memory), free space; disk busy / network / GPU (unless
  nvidia-smi is on PATH) are None.

Every field is None when this computer cannot say (the phone shows 「—」, never a guessed number). Rates need two samples, so
the very first answer after a watch starts carries memory, GPU and free space only; the next one (≤ EVERY s later) has all.
Display only: nothing here changes anything on the computer.
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

EVERY = 2.5                 # s between samples while a card is open
FIRST = 0.6                 # s from the first (baseline) sample to the first full one
WATCH_TTL = 30              # s a watch lives without being renewed
RENEW = 10                  # s between the page's renewals while the card is open
GPU_EVERY = 5.0             # s between two nvidia-smi calls (the slowest read here)
SUB_TIMEOUT = 2.0
SKIP_NET = re.compile(r"^(lo|veth|docker|br-|virbr|vnet|tap|tun|zt|wg|tailscale|utun|awdl|llw|bridge|gif|stf|anpi|ap\d)")
DISK_RE = re.compile(r"^(sd[a-z]+|hd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")


def _r(path) -> str | None:
    try:
        return Path(path).read_text(encoding="ascii", errors="replace")
    except OSError:
        return None


def _run(cmd: list[str], timeout: float = SUB_TIMEOUT) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _frac(v) -> float | None:
    if v is None or v != v:
        return None
    return round(min(1.0, max(0.0, float(v))), 3)


# ------------------------------------------------------------------------------------------- parsers (pure, tested)
def parse_proc_stat(text: str) -> tuple[int, int] | None:
    """/proc/stat → (busy, total) jiffies of the aggregate cpu line (iowait counts as idle)."""
    for line in (text or "").splitlines():
        if line.startswith("cpu "):
            v = [int(x) for x in line.split()[1:] if x.isdigit()]
            if len(v) < 4:
                return None
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            total = sum(v[:8])                      # guest time is already inside user / nice
            return total - idle, total
    return None


def parse_meminfo(text: str) -> float | None:
    """/proc/meminfo → used share (1 − MemAvailable / MemTotal)."""
    kv = {}
    for line in (text or "").splitlines():
        k, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            kv[k.strip()] = int(parts[0])
    total = kv.get("MemTotal")
    avail = kv.get("MemAvailable")
    if avail is None and total:
        avail = kv.get("MemFree", 0) + kv.get("Buffers", 0) + kv.get("Cached", 0)
    if not total:
        return None
    return _frac(1 - avail / total)


def parse_diskstats(text: str, names=None) -> dict[str, int]:
    """/proc/diskstats → {disk: io_ticks ms} for whole physical disks (no partitions, loop, ram, zram, dm)."""
    out = {}
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) < 13 or not DISK_RE.match(p[2]):
            continue
        if names is not None and p[2] not in names:
            continue
        with contextlib.suppress(ValueError):
            out[p[2]] = int(p[12])
    return out


def parse_net_dev(text: str, keep=None) -> tuple[int, int] | None:
    """/proc/net/dev → (rx, tx) bytes summed over the kept interfaces (default: everything but SKIP_NET)."""
    rx = tx = 0
    seen = False
    for line in (text or "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        v = rest.split()
        if len(v) < 9 or (keep is not None and name not in keep) or (keep is None and SKIP_NET.match(name)):
            continue
        with contextlib.suppress(ValueError):
            rx += int(v[0])
            tx += int(v[8])
            seen = True
    return (rx, tx) if seen else None


def parse_nvidia(text: str) -> tuple[float, str] | None:
    """`nvidia-smi --query-gpu=utilization.gpu,name --format=csv,noheader,nounits` → (busiest share, its name)."""
    best = None
    for line in (text or "").splitlines():
        a, _, name = line.partition(",")
        try:
            v = float(a.strip()) / 100
        except ValueError:
            continue
        if best is None or v > best[0]:
            best = (v, name.strip()[:40])
    return best


def parse_vm_stat(text: str, memsize: int | None) -> float | None:
    """macOS `vm_stat` + hw.memsize → used share = (active + wired + compressor) pages / total (Activity Monitor's 「已使用」
    minus the file cache)."""
    if not memsize:
        return None
    m = re.search(r"page size of (\d+) bytes", text or "")
    page = int(m.group(1)) if m else 4096
    kv = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        v = v.strip().rstrip(".")
        if v.isdigit():
            kv[k.strip().strip('"')] = int(v)
    used = kv.get("Pages active", 0) + kv.get("Pages wired down", 0) + kv.get("Pages occupied by compressor", 0)
    if not used:
        return None
    return _frac(used * page / memsize)


def parse_netstat_ib(text: str) -> tuple[int, int] | None:
    """macOS `netstat -ib` → (rx, tx) bytes over en*/ethernet-like interfaces, one row per interface (the <Link#n> row)."""
    rx = tx = 0
    seen = False
    lines = (text or "").splitlines()
    if not lines:
        return None
    head = lines[0].split()
    try:
        ib, ob = head.index("Ibytes"), head.index("Obytes")
    except ValueError:
        return None
    for line in lines[1:]:
        p = line.split()
        if len(p) < len(head) or "<Link#" not in line or SKIP_NET.match(p[0]):
            continue
        # a row without an address column has one field fewer: index from the right
        off = len(p) - len(head)
        with contextlib.suppress(ValueError, IndexError):
            rx += int(p[ib + off])
            tx += int(p[ob + off])
            seen = True
    return (rx, tx) if seen else None


def parse_ioreg_gpu(text: str) -> float | None:
    """macOS `ioreg -r -d 1 -c IOAccelerator` → the busiest 「Device Utilization %」."""
    vals = [int(x) for x in re.findall(r'"Device Utilization %"\s*=\s*(\d+)', text or "")]
    return _frac(max(vals) / 100) if vals else None


def parse_ioreg_disk(text: str) -> int | None:
    """macOS `ioreg -r -c IOBlockStorageDriver -k Statistics` → total read + write time (ns) over all disks."""
    v = [int(x) for x in re.findall(r'"Total Time \((?:Read|Write)\)"\s*=\s*(\d+)', text or "")]
    return sum(v) if v else None


# ------------------------------------------------------------------------------------------- platform reads
def _phys_ifaces() -> set[str] | None:
    try:
        names = os.listdir("/sys/class/net")
    except OSError:
        return None
    phys = {n for n in names if os.path.exists(f"/sys/class/net/{n}/device") and not SKIP_NET.match(n)}
    return phys or None


def _nvidia_asleep() -> bool:
    """True when an NVIDIA PCI display device is runtime-suspended (asking nvidia-smi would wake it and cost power)."""
    base = "/sys/bus/pci/devices"
    try:
        devs = os.listdir(base)
    except OSError:
        return False
    for d in devs:
        if (_r(f"{base}/{d}/vendor") or "").strip() == "0x10de" and (_r(f"{base}/{d}/class") or "").startswith("0x03"):
            if (_r(f"{base}/{d}/power/runtime_status") or "").strip() == "suspended":
                return True
    return False


def _mac_cpu_ticks():
    """(busy, total) ticks from host_statistics(HOST_CPU_LOAD_INFO) — one mach call, no process."""
    try:
        lib = ctypes.CDLL("/usr/lib/libSystem.dylib")
        host = lib.mach_host_self()
        info = (ctypes.c_uint * 4)()
        count = ctypes.c_uint(4)
        if lib.host_statistics(host, 3, ctypes.byref(info), ctypes.byref(count)) != 0:
            return None
        user, system, idle, nice = info[0], info[1], info[2], info[3]
        return user + system + nice, user + system + idle + nice
    except (OSError, AttributeError, ValueError):
        return None


def _win_cpu_ticks():
    class FT(ctypes.Structure):
        _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]
    try:
        idle, kern, user = FT(), FT(), FT()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):  # type: ignore[attr-defined]
            return None
        v = lambda f: (f.hi << 32) | f.lo   # noqa: E731
        total = v(kern) + v(user)           # kernel time includes idle
        return total - v(idle), total
    except (OSError, AttributeError):
        return None


def _win_mem():
    class MS(ctypes.Structure):
        _fields_ = [("len", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [(f"f{i}", ctypes.c_uint64) for i in range(7)]
    try:
        m = MS()
        m.len = ctypes.sizeof(MS)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):  # type: ignore[attr-defined]
            return None
        return _frac(m.load / 100)
    except (OSError, AttributeError):
        return None


class Sampler:
    """Keeps the previous counters so CPU / disk busy / network can be turned into shares and rates."""

    def __init__(self, platform: str | None = None, home: str | None = None, now=time.monotonic):
        self.plat = platform or ("linux" if sys.platform.startswith("linux") else "mac" if sys.platform == "darwin"
                                 else "win" if sys.platform.startswith("win") else "other")
        self.home = home or os.path.expanduser("~")
        self.now = now
        self.prev: dict = {}
        self.gpu_at = -1e9
        self.gpu_last = None
        self.memsize = None
        self.ifaces = _phys_ifaces() if self.plat == "linux" else None
        self.has_smi = shutil.which("nvidia-smi") is not None

    # -- one sample
    def sample(self) -> dict:
        t = self.now()
        dt = t - self.prev["t"] if "t" in self.prev else None
        out = {"cpu": self._cpu(dt), "mem": self._mem(), "gpu": self._gpu(t), "disk": self._disk(dt), "net": self._net(dt)}
        self.prev["t"] = t
        return out

    def _delta(self, key, cur):
        """(this − previous) for a tuple/int counter, remembering this one; None on the first sample or a reset counter."""
        old = self.prev.get(key)
        self.prev[key] = cur
        if old is None or cur is None:
            return None
        if isinstance(cur, tuple):
            d = tuple(a - b for a, b in zip(cur, old))
            return None if any(x < 0 for x in d) else d
        return None if cur < old else cur - old

    def _cpu(self, dt):
        if self.plat == "linux":
            cur = parse_proc_stat(_r("/proc/stat"))
        elif self.plat == "mac":
            cur = _mac_cpu_ticks()
        elif self.plat == "win":
            cur = _win_cpu_ticks()
        else:
            cur = None
        d = self._delta("cpu", cur)
        if not d or d[1] <= 0:
            return None
        return _frac(d[0] / d[1])

    def _mem(self):
        if self.plat == "linux":
            return parse_meminfo(_r("/proc/meminfo"))
        if self.plat == "mac":
            if self.memsize is None:
                with contextlib.suppress(TypeError, ValueError):
                    self.memsize = int((_run(["sysctl", "-n", "hw.memsize"]) or "").strip())
            return parse_vm_stat(_run(["vm_stat"]), self.memsize)
        if self.plat == "win":
            return _win_mem()
        return None

    def _gpu(self, t):
        if self.plat == "linux":
            vals = []
            with contextlib.suppress(OSError):
                for c in os.listdir("/sys/class/drm"):
                    if re.fullmatch(r"card\d+", c):
                        s = (_r(f"/sys/class/drm/{c}/device/gpu_busy_percent") or "").strip()
                        if s.isdigit():
                            vals.append(int(s) / 100)
            if vals:
                return {"pct": _frac(max(vals))}
        if self.plat == "mac":
            v = parse_ioreg_gpu(_run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"]))
            return {"pct": v} if v is not None else None
        if self.has_smi:
            if self.plat == "linux" and _nvidia_asleep():
                return {"pct": 0.0, "sleep": True}
            if t - self.gpu_at >= GPU_EVERY:
                self.gpu_at = t
                got = parse_nvidia(_run(["nvidia-smi", "--query-gpu=utilization.gpu,name", "--format=csv,noheader,nounits"]))
                self.gpu_last = {"pct": _frac(got[0]), "name": got[1]} if got else None
            return self.gpu_last
        return None

    def _disk(self, dt):
        out = {}
        with contextlib.suppress(OSError):
            du = shutil.disk_usage(self.home)
            if du.total > 0:
                out["free"] = _frac(du.free / du.total)
                out["free_gb"] = round(du.free / 1e9, 1)
        busy = None
        if self.plat == "linux":
            ticks = parse_diskstats(_r("/proc/diskstats"))
            old = self.prev.get("disk")
            self.prev["disk"] = ticks
            if old is not None and dt and dt > 0 and ticks:
                d = [ticks[k] - old[k] for k in ticks if k in old and ticks[k] >= old[k]]
                busy = _frac(max(d) / 1000 / dt) if d else None
        elif self.plat == "mac":
            d = self._delta("disk", parse_ioreg_disk(_run(["ioreg", "-r", "-c", "IOBlockStorageDriver", "-k", "Statistics"])))
            if d is not None and dt and dt > 0:
                busy = _frac(d / 1e9 / dt)
        out["busy"] = busy
        return out if out.get("free") is not None or busy is not None else None

    def _net(self, dt):
        if self.plat == "linux":
            cur = parse_net_dev(_r("/proc/net/dev"), self.ifaces)
        elif self.plat == "mac":
            cur = parse_netstat_ib(_run(["netstat", "-ib"]))
        else:
            cur = None
        d = self._delta("net", cur)
        if d is None or not dt or dt <= 0:
            return None
        return {"rx": int(d[0] / dt), "tx": int(d[1] / dt)}


def message(sample: dict, load=None) -> dict:
    """The wire message (§22.1): `{"t":"res","cpu","mem","gpu","disk","net","load"}`, absent = unknown."""
    m = {"t": "res"}
    for k in ("cpu", "mem", "gpu", "disk", "net"):
        if sample.get(k) is not None:
            m[k] = sample[k]
    if load is not None:
        m["load"] = load
    return m


def measure(n: int = 20, every: float = 0.0) -> dict:
    """CPU time per sample (this process + children, e.g. nvidia-smi), for the report and `agentj doctor`-style checks."""
    s = Sampler()
    s.sample()
    c0 = os.times()
    w0 = time.perf_counter()
    for _ in range(n):
        if every:
            time.sleep(every)
        s.sample()
    c1 = os.times()
    cpu = (c1.user - c0.user) + (c1.system - c0.system) + (c1.children_user - c0.children_user) + (c1.children_system - c0.children_system)
    return {"n": n, "cpu_ms_per_sample": round(cpu / n * 1000, 2), "wall_ms_per_sample": round((time.perf_counter() - w0 - n * every) / n * 1000, 2),
            "last": s.sample()}


if __name__ == "__main__":  # python -m agentj.resources → one measurement (the P181 report numbers)
    import json
    print(json.dumps(measure(int(sys.argv[1]) if len(sys.argv) > 1 else 20, float(sys.argv[2]) if len(sys.argv) > 2 else 0.0), ensure_ascii=False))
