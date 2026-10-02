#!/usr/bin/env python3
"""Deterministic app icons for the web client (no image libraries): a dark tile with a light ring. Writes
public/icon-192.png, icon-512.png, apple-touch-icon.png (180). Run: python3 web/tools/icons.py"""
import pathlib
import struct
import zlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "public"
BG, FG = (15, 17, 21), (232, 236, 241)


def png(size: int) -> bytes:
    c, r_out, r_in = (size - 1) / 2, size * 0.34, size * 0.22
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            t = max(0.0, min(1.0, min(r_out - d, d - r_in) + 0.5))   # 1-pixel anti-aliased ring
            row += bytes(round(BG[i] + (FG[i] - BG[i]) * t) for i in range(3))
        rows.append(bytes(row))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
    (OUT / name).write_bytes(png(size))
    print(name, size)
