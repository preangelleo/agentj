"""Private queue handoff outside its transient order unit; no command runs in the old cgroup."""
from __future__ import annotations

import math
from pathlib import Path
import subprocess
import sys
import time

from . import dispatcher, orders
from ._guardian import bound


def run(path, generation):
    o = orders.Order(path)
    binding = dispatcher._record(o, "unit-execution.json") or {}
    if binding.get("generation") != generation:
        return 97
    wall = o.meta["budget"]["wall_min"]
    if type(wall) is not int or not 1 <= wall <= orders.WALL_MAX:
        return 97
    end = time.monotonic() + wall * 60 + 75
    while time.monotonic() < end:
        if not bound(dispatcher._record(o, "unit-execution.json") or {}, binding):
            return 0  # a newer generation owns further scheduling
        unit = dispatcher._Unit(o, subprocess.run)
        unit.deadline = min(end, time.monotonic() + 2)
        if unit.quiet():
            hard = dispatcher._record(o, "unit-hard-stop.json")
            if hard is not None and bound(hard, binding):
                deadline = hard.get("deadline")
                if isinstance(deadline, bool) or not isinstance(deadline, (float, int)) or not math.isfinite(deadline):
                    return 97
                return 0  # explicit hard-stop preserves immediate owner resume
            if not bound(dispatcher._record(o, "unit-execution.json") or {}, binding):
                return 0
            dispatcher.drain()
            return 0
        time.sleep(0.2)
    return 97


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or not args[1].isdigit():
        return 97
    try:
        return run(Path(args[0]), args[1])
    except Exception:
        return 97


if __name__ == "__main__":
    sys.exit(main())
