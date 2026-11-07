"""#578 ciphertext format shared with the browser's WebCrypto implementation.

File contents/keys remain at the paired endpoints; the HTTPS relay receives only
bounded independently authenticated ciphertext records and opaque metadata.
"""
import re
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CHUNK = 65536
MAX_BYTES = 100 * 1024 * 1024
_ID = re.compile(r'[0-9a-f]{32}')
_CHANNEL = re.compile(r'[A-Za-z0-9_-]{22}')


def nonce(prefix, index):
    if not isinstance(prefix, bytes) or len(prefix) != 8 or type(index) is not int or not 0 <= index < 2**32:
        raise ValueError('cipher_shape')
    return prefix + index.to_bytes(4, 'big')


def aad(channel, offer, transfer, index, size):
    if (not isinstance(channel, str) or not _CHANNEL.fullmatch(channel)
            or not isinstance(offer, str) or not _ID.fullmatch(offer)
            or not isinstance(transfer, str) or not _ID.fullmatch(transfer)
            or type(index) is not int or not 0 <= index < 2**32
            or type(size) is not int or not 0 < size <= MAX_BYTES):
        raise ValueError('cipher_shape')
    return f'agentj-private-file-v1\n{channel}\n{offer}\n{transfer}\n{index}\n{size}'.encode()


def encrypt(key, prefix, channel, offer, transfer, index, size, data):
    if not isinstance(key, bytes) or len(key) != 32 or not isinstance(data, bytes) or not 0 < len(data) <= CHUNK:
        raise ValueError('cipher_shape')
    return AESGCM(key).encrypt(nonce(prefix, index), data, aad(channel, offer, transfer, index, size))
