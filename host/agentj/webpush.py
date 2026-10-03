"""Web Push from the host itself (PROTOCOL §9): VAPID (RFC 8292) + aes128gcm message encryption (RFC 8291).

PR1: the push carries no content — only the kind ("reply" | "ask"), padded to a fixed size and encrypted to the
browser's subscription keys, so the push service (Google / Apple / Mozilla) sees an opaque blob of constant length.
Our cloud is not involved: the subscription travels to the host inside the Noise session and the host posts directly.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .envcompat import getenv

KINDS = ("reply", "ask")
PAYLOAD_LEN = 32               # every push plaintext is padded to this many bytes before encryption
RS = 4096
TTL = 3600
SUB = "https://agentj.app"
HTTP_TIMEOUT = 10
# Push services the host will post to (https, default port). A paired device chooses the endpoint, so this list is what
# keeps a device from turning the host into an HTTP client for arbitrary URLs.
ALLOWED_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com", "web.push.apple.com")
ALLOWED_SUFFIXES = (".push.apple.com", ".notify.windows.com")


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    h = hmac.HMAC(salt, hashes.SHA256())
    h.update(ikm)
    return h.finalize()


def _hkdf_expand(prk: bytes, info: bytes, n: int) -> bytes:
    h = hmac.HMAC(prk, hashes.SHA256())
    h.update(info + b"\x01")
    return h.finalize()[:n]


def _pub_bytes(pub: ec.EllipticCurvePublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(plaintext: bytes, ua_public: bytes, auth_secret: bytes, *, as_private: ec.EllipticCurvePrivateKey | None = None,
            salt: bytes | None = None) -> bytes:
    """RFC 8291 §3 + RFC 8188: one record, aes128gcm. as_private / salt are parameters only for the RFC test vector."""
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    ua = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_public = _pub_bytes(as_private.public_key())
    ecdh = as_private.exchange(ec.ECDH(), ua)
    ikm = _hkdf_expand(_hkdf_extract(auth_secret, ecdh), b"WebPush: info\x00" + ua_public + as_public, 32)
    prk = _hkdf_extract(salt, ikm)
    cek = _hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12)
    ct = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + RS.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + ct


def payload(kind: str) -> bytes:
    if kind not in KINDS:
        raise ValueError("bad kind")
    j = json.dumps({"k": kind}, separators=(",", ":")).encode()
    return j + b" " * (PAYLOAD_LEN - len(j))       # JSON tolerates trailing whitespace; every kind is the same length


# ------------------------------------------------------------------ VAPID
def load_vapid(st) -> ec.EllipticCurvePrivateKey:
    try:
        raw = st.vapid_path.read_bytes()
        return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    except (FileNotFoundError, ValueError):
        k = ec.generate_private_key(ec.SECP256R1())
        st.write_private(st.vapid_path, k.private_numbers().private_value.to_bytes(32, "big"))
        return k


def vapid_public(key: ec.EllipticCurvePrivateKey) -> bytes:
    return _pub_bytes(key.public_key())


def vapid_jwt(key: ec.EllipticCurvePrivateKey, endpoint: str, now: float | None = None) -> str:
    u = urllib.parse.urlsplit(endpoint)
    aud = f"{u.scheme}://{u.netloc}"
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": aud, "exp": int((now or time.time()) + 12 * 3600), "sub": SUB},
                             separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{head}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# ------------------------------------------------------------------ subscriptions
def endpoint_ok(endpoint: str) -> bool:
    if not isinstance(endpoint, str) or len(endpoint) > 1024:
        return False
    try:
        u = urllib.parse.urlsplit(endpoint)
        port = u.port
    except ValueError:
        return False
    test = getenv("AGENTJ_TEST_PUSH_ORIGIN")   # tests: one exact extra origin (a local fake push service)
    if test and f"{u.scheme}://{u.netloc}" == test:
        return True
    host = (u.hostname or "").lower()
    if u.scheme != "https" or port not in (None, 443) or u.username or u.password:
        return False
    return host in ALLOWED_HOSTS or any(host.endswith(sfx) and len(host) > len(sfx) for sfx in ALLOWED_SUFFIXES)


def parse_sub(obj: dict) -> dict | None:
    """A device's push_sub message → {endpoint, p256dh, auth} or None."""
    ep, p, a = obj.get("endpoint"), obj.get("p256dh"), obj.get("auth")
    if not endpoint_ok(ep) or not isinstance(p, str) or not isinstance(a, str) or len(p) > 128 or len(a) > 64:
        return None
    try:
        pb, ab = unb64u(p), unb64u(a)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pb)
    except ValueError:
        return None
    if len(pb) != 65 or len(ab) != 16:
        return None
    return {"endpoint": ep, "p256dh": p, "auth": a}


def load_subs(st) -> dict:
    try:
        d = json.loads(st.push_path.read_text())
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict) and parse_sub(v)} if isinstance(d, dict) else {}


def save_subs(st, subs: dict) -> None:
    st.write_private(st.push_path, json.dumps(subs).encode())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def send(key: ec.EllipticCurvePrivateKey, sub: dict, kind: str, urgency: str = "normal") -> int:
    """POST one push. Returns the HTTP status (201 = accepted; 404 / 410 = subscription gone) or 0 on a network error.
    Honours HTTPS_PROXY like the rest of the host; never follows redirects."""
    if not endpoint_ok(sub.get("endpoint", "")):
        return 0
    body = encrypt(payload(kind), unb64u(sub["p256dh"]), unb64u(sub["auth"]))
    headers = {"content-encoding": "aes128gcm", "content-type": "application/octet-stream", "ttl": str(TTL),
               "urgency": urgency, "authorization": f"vapid t={vapid_jwt(key, sub['endpoint'])}, k={b64u(vapid_public(key))}"}
    u = urllib.parse.urlsplit(sub["endpoint"])
    handlers: list = [_NoRedirect()]
    if u.hostname in ("127.0.0.1", "localhost", "::1"):
        handlers.append(urllib.request.ProxyHandler({}))
    else:
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers=headers)
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=HTTP_TIMEOUT) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, ValueError):
        return 0
