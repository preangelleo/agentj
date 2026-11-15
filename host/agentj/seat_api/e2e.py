"""seat-api-v1 E2E envelope (SEAT_API.md §3). Independent domain from owner Noise and bots.

The edge only routes ciphertext. Per-endpoint Ed25519/X25519 keys never leave the host except inside an
encrypted migration bundle. Customers pin the manifest out of band; there is no trust-on-first-use path.
"""
import hashlib
import json
import os
import re
from cryptography.exceptions import InvalidSignature,InvalidTag
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey,X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from ..bots.store import BotError,canonical
from ..wire import b64u,unb64u

DOMAIN='agentjarvis/seat-api-v1'
HEADER={'v','endpoint','epoch','key_id','request_id','eph','nonce','deadline'}
MAX_SKEW=300
RAW=serialization.Encoding.Raw,serialization.PublicFormat.Raw
PRIV=serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()

def new_keys():return {'sign':Ed25519PrivateKey.generate().private_bytes(*PRIV),'kem':X25519PrivateKey.generate().private_bytes(*PRIV)}
def public(keys):
    return {'sign':b64u(Ed25519PrivateKey.from_private_bytes(keys['sign']).public_key().public_bytes(*RAW)),
            'kem':b64u(X25519PrivateKey.from_private_bytes(keys['kem']).public_key().public_bytes(*RAW))}
def pin(endpoint,sign,kem):
    return 'ajpin1_'+hashlib.sha256(('agentjarvis/seat-api-pin-v1\n'+endpoint+'\n'+sign+'\n'+kem).encode()).hexdigest()[:40]
def sign(keys,context,obj):
    return b64u(Ed25519PrivateKey.from_private_bytes(keys['sign']).sign((context+'\n'+canonical(obj)).encode()))
def verify(public_sign,context,obj,sig):
    try:Ed25519PublicKey.from_public_bytes(unb64u(public_sign)).verify(unb64u(sig),(context+'\n'+canonical(obj)).encode());return True
    except (InvalidSignature,ValueError,TypeError):return False
def manifest(keys,endpoint,now,ttl=86400):
    pub=public(keys)
    m={'domain':'agentj-seat-api-manifest-v1','endpoint':endpoint['id'],'public_id':endpoint['public_id'],'epoch':endpoint['epoch'],
       'contract':endpoint['version'],'privacy':endpoint['privacy'],'kem':pub['kem'],'sign':pub['sign'],'issued':now,'expires':now+ttl}
    return {'manifest':m,'sig':sign(keys,'agentjarvis/seat-api-manifest-v1',m),'pin':pin(endpoint['id'],pub['sign'],pub['kem'])}

def _keys(shared,aad):
    salt=hashlib.sha256(aad).digest()
    def k(label):return HKDF(hashes.SHA256(),32,salt,(DOMAIN+' '+label).encode()).derive(shared)
    return k('request'),k('response')
def aad(header):return (DOMAIN+'\n'+canonical(header)).encode()

def seal_request(kem_pub,header,inner):
    """Client side (SDK/tests): returns the outer envelope and the response key."""
    eph=X25519PrivateKey.generate();header={**header,'eph':b64u(eph.public_key().public_bytes(*RAW)),'nonce':b64u(os.urandom(12))}
    a=aad(header);req,resp=_keys(eph.exchange(X25519PublicKey.from_public_bytes(unb64u(kem_pub))),a)
    return {**header,'ct':b64u(AESGCM(req).encrypt(unb64u(header['nonce']),canonical(inner).encode(),a))},resp

def check_header(env,endpoint,now):
    if not isinstance(env,dict) or set(env)!=HEADER|{'ct'} or env['v']!=1:raise BotError('invalid_envelope')
    if env['endpoint']!=endpoint['id']:raise BotError('invalid_envelope')
    if env['epoch']!=endpoint['epoch']:raise BotError('route_epoch')
    for k in ('key_id','request_id'):
        if not isinstance(env[k],str) or not re.fullmatch(r'[a-f0-9]{32}',env[k]):raise BotError('invalid_envelope')
    if type(env['deadline']) is not int or not now<env['deadline']<=now+MAX_SKEW:raise BotError('deadline')
    if not all(isinstance(env[k],str) for k in ('eph','nonce','ct')):raise BotError('invalid_envelope')

def open_request(keys,env):
    """Host side. Returns (inner, response_key). AEAD failure is a plain stable code, never content."""
    header={k:env[k] for k in HEADER};a=aad(header)
    try:
        nonce=unb64u(env['nonce']);eph=unb64u(env['eph'])
        if len(nonce)!=12 or len(eph)!=32:raise ValueError()
        req,resp=_keys(X25519PrivateKey.from_private_bytes(keys['kem']).exchange(X25519PublicKey.from_public_bytes(eph)),a)
        inner=json.loads(AESGCM(req).decrypt(nonce,unb64u(env['ct']),a))
    except (InvalidTag,ValueError,TypeError,UnicodeDecodeError):raise BotError('invalid_envelope') from None
    if not isinstance(inner,dict):raise BotError('invalid_envelope')
    return inner,resp

def seal_response(resp_key,request_id,status,body):
    nonce=os.urandom(12)
    return {'v':1,'request_id':request_id,'nonce':b64u(nonce),
            'ct':b64u(AESGCM(resp_key).encrypt(nonce,canonical({'status':status,'body':body}).encode(),(DOMAIN+' response\n'+request_id).encode()))}
def open_response(resp_key,env):
    try:return json.loads(AESGCM(resp_key).decrypt(unb64u(env['nonce']),unb64u(env['ct']),(DOMAIN+' response\n'+env['request_id']).encode()))
    except (InvalidTag,ValueError,TypeError,KeyError):raise BotError('invalid_envelope') from None

def seal_to(recipient_pub,label,obj):
    """One-way sealed box for E2E callbacks and migration bundles: X25519 + HKDF + AES-GCM."""
    eph=X25519PrivateKey.generate();epub=eph.public_key().public_bytes(*RAW);nonce=os.urandom(12)
    key=HKDF(hashes.SHA256(),32,epub,(DOMAIN+' sealed '+label).encode()).derive(eph.exchange(X25519PublicKey.from_public_bytes(unb64u(recipient_pub))))
    return {'v':1,'eph':b64u(epub),'nonce':b64u(nonce),'ct':b64u(AESGCM(key).encrypt(nonce,canonical(obj).encode(),label.encode()))}
def open_sealed(private,label,box):
    try:
        epub=unb64u(box['eph']);key=HKDF(hashes.SHA256(),32,epub,(DOMAIN+' sealed '+label).encode()).derive(X25519PrivateKey.from_private_bytes(private).exchange(X25519PublicKey.from_public_bytes(epub)))
        return json.loads(AESGCM(key).decrypt(unb64u(box['nonce']),unb64u(box['ct']),label.encode()))
    except (InvalidTag,ValueError,TypeError,KeyError):raise BotError('invalid_bundle') from None
def x25519_pair():
    k=X25519PrivateKey.generate();return k.private_bytes(*PRIV),b64u(k.public_key().public_bytes(*RAW))
