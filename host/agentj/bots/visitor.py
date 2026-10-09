"""Independent visitor-bot-v1 handshake/cipher; never owner Noise or control messages.

The admission issuer sees public keys and routing metadata only. Each accepted visitor
has a host-ephemeral key, signed transcript, direction keys, counters and bound bot.
"""
import hashlib
import json
import secrets
import time
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey,X25519PublicKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from ..wire import b64u,unb64u
from .store import BotError,canonical,ident

DOMAIN=b'agentjarvis/visitor-bot-v1\n'
MAX=16384
ALLOWED={'say','identity','human'}

def public(k):return k.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)

def issue_claim(signing,bot,host,visitor,epoch,expires):
    claim={'bot':ident(bot),'host':host,'visitor':visitor,'epoch':epoch,'expires':expires,'domain':'visitor-bot-v1'}
    return {'claim':claim,'sig':b64u(signing.sign(DOMAIN+canonical(claim).encode()))}

def verify_claim(ticket,issuer,bot,host,visitor,epoch,now=None):
    now=int(time.time()) if now is None else now
    try:
        if not isinstance(ticket,dict) or set(ticket)!={'claim','sig'}:raise ValueError()
        c=ticket['claim']
        if set(c)!={'bot','host','visitor','epoch','expires','domain'} or c['domain']!='visitor-bot-v1' or (c['bot'],c['host'],c['visitor'],c['epoch'])!=(bot,host,visitor,epoch) or type(c['expires']) is not int or not now<c['expires']<=now+300:raise ValueError()
        if len(unb64u(visitor))!=32:raise ValueError()
        Ed25519PublicKey.from_public_bytes(issuer).verify(unb64u(ticket['sig']),DOMAIN+canonical(c).encode())
        return c
    except Exception:raise BotError('admission_refused') from None

class Cipher:
    def __init__(self,shared,transcript,host):
        self.binding=hashlib.sha256(DOMAIN+canonical(transcript).encode()).digest()
        keys=HKDF(algorithm=hashes.SHA256(),length=64,salt=self.binding,info=DOMAIN).derive(shared)
        self.tx=AESGCM(keys[:32] if host else keys[32:]);self.rx=AESGCM(keys[32:] if host else keys[:32]);self.sent=0;self.received=0
    def seal(self,obj):
        data=canonical(obj).encode()
        if len(data)>MAX-2:raise BotError('visitor_frame_limit')
        data=(len(data).to_bytes(2,'big')+data);data=data.ljust(((len(data)+255)//256)*256,b'\0')
        nonce=self.sent.to_bytes(12,'big');self.sent+=1
        return self.tx.encrypt(nonce,data,self.binding)
    def open(self,raw):
        if not isinstance(raw,bytes) or len(raw)>MAX+16:raise BotError('visitor_frame_limit')
        try:
            data=self.rx.decrypt(self.received.to_bytes(12,'big'),raw,self.binding)
            if len(data)<256 or len(data)%256:raise ValueError()
            n=int.from_bytes(data[:2],'big')
            if n>len(data)-2 or any(data[n+2:]):raise ValueError()
            obj=json.loads(data[2:n+2])
            if not isinstance(obj,dict):raise ValueError()
        except Exception:raise BotError('visitor_cipher_refused') from None
        self.received+=1;return obj

class VisitorSession:
    def __init__(self,bot,epoch,host_signing,issuer_pub):
        self.bot=ident(bot);self.epoch=epoch;self.signing=host_signing;self.issuer=issuer_pub;self.cipher=None;self.visitor=None;self.expires=0
    def accept(self,ticket):
        if self.cipher is not None:raise BotError('already_admitted')
        host=b64u(public(self.signing));visitor=ticket.get('claim',{}).get('visitor')
        c=verify_claim(ticket,self.issuer,self.bot,host,visitor,self.epoch)
        ephemeral=X25519PrivateKey.generate()
        transcript={'claim':c,'ephemeral':b64u(public(ephemeral)),'nonce':secrets.token_hex(16)}
        shared=ephemeral.exchange(X25519PublicKey.from_public_bytes(unb64u(visitor)))
        self.cipher=Cipher(shared,transcript,True);self.visitor=hashlib.sha256(DOMAIN+unb64u(visitor)).hexdigest()[:32];self.expires=c['expires']
        return {'transcript':transcript,'sig':b64u(self.signing.sign(DOMAIN+canonical(transcript).encode()))}
    def decode(self,frame):
        if self.cipher is None or time.time()>=self.expires:raise BotError('visitor_expired')
        obj=self.cipher.open(frame)
        kind=obj.get('t')
        if kind not in ALLOWED:raise BotError('visitor_kind_refused')
        keys={'say':{'t','r','text'},'identity':{'t','r','identity'},'human':{'t','r'}}[kind]
        if set(obj)!=keys:raise BotError('visitor_kind_refused')
        ident(obj['r']);return obj

def client_finish(private,answer,host_pub):
    try:
        t=answer['transcript']
        if t['claim']['visitor']!=b64u(public(private)):raise ValueError()
        Ed25519PublicKey.from_public_bytes(host_pub).verify(unb64u(answer['sig']),DOMAIN+canonical(t).encode())
        return Cipher(private.exchange(X25519PublicKey.from_public_bytes(unb64u(t['ephemeral']))),t,False)
    except Exception:raise BotError('host_binding_refused') from None
