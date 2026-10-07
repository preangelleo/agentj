"""F29 / ADR-A177 fixture: the QR module matrices of one pairing (fixed fake keys, long-expired) in both link formats, made
by the host's own code (wire.pairing_link / pairing_link_v1 / pairing_qr). web/test/p73_scan.test.mjs renders them into
simulated screen photos and decodes them with jsQR; host/tests/test_p73_pair_qr.py fails if this file drifts from the host.

  host/.venv/bin/python web/test/fixtures/p73/gen_pair_qr.py   # rewrites pair_qr.json
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "host"))

from agentj import wire  # noqa: E402

WEB, RELAY, EXPIRES = "https://m.agentj.app", "wss://relay.agentj.app", 1_790_000_000   # 2026-09-21: expired on purpose


def fake(label: str, n: int) -> bytes:       # not keys of anything: fixed bytes so the fixture is reproducible
    return hashlib.sha256(b"agentj-f29-fixture/" + label.encode()).digest()[:n]


def build() -> dict:
    args = (WEB, RELAY, wire.channel_id(fake("ed", 32)), fake("x25519", 32), fake("pid", 16), fake("psk", 32), EXPIRES)
    out = {}
    for name, link in (("v2", wire.pairing_link(*args)), ("v1", wire.pairing_link_v1(*args))):
        qr = wire.pairing_qr(link)
        rows = ["".join("1" if m else "0" for m in row) for row in qr.matrix_iter(scale=1, border=0)]
        out[name] = {"link": link, "chars": len(link), "version": qr.version, "error": qr.error, "modules": len(rows),
                     "rows": rows}
    return out


def render() -> str:
    return json.dumps(build(), indent=1) + "\n"


if __name__ == "__main__":
    (HERE / "pair_qr.json").write_text(render())
    for k, v in build().items():
        print(k, v["chars"], "chars, version", v["version"], v["error"], v["modules"], "modules")
