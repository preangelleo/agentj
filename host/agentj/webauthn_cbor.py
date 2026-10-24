"""Bounded definite-length CBOR for WebAuthn; mirrors dashboard/src/cbor.ts.

No tags, floats, indefinite containers, duplicate keys or ambiguous bool/int keys.
"""
from __future__ import annotations


def decode(data: bytes, offset: int = 0) -> tuple[object, int]:
    pos, items = offset, 0

    def take(n):
        nonlocal pos
        if n < 0 or pos + n > len(data):
            raise ValueError('cbor truncated')
        b = data[pos:pos+n]; pos += n
        return b

    def item(depth):
        nonlocal items
        items += 1
        if depth > 8 or items > 256:
            raise ValueError('cbor limit')
        b = take(1)[0]; major, ai = b >> 5, b & 31
        if major == 7:
            if ai in (20, 21, 22):
                return {20:False, 21:True, 22:None}[ai]
            raise ValueError('cbor simple')
        if ai < 24:
            n = ai
        elif ai in (24, 25, 26, 27):
            n = int.from_bytes(take(1 << (ai-24)), 'big')
        else:
            raise ValueError('cbor indefinite')
        if n > 2**53-1:
            raise ValueError('cbor integer')
        if major == 0: return n
        if major == 1: return -1-n
        if major == 2: return take(n)
        if major == 3: return take(n).decode('utf-8')
        if major in (4, 5) and n <= 256:
            if major == 4: return [item(depth+1) for _ in range(n)]
            result = {}
            for _ in range(n):
                k = item(depth+1)
                if type(k) not in (int, str) or k in result:
                    raise ValueError('cbor key')
                result[k] = item(depth+1)
            return result
        raise ValueError('cbor type')

    value = item(0)
    return value, pos
