"""Independent HTTPS ciphertext format: no file bytes or keys in the control transport."""
import _hermetic
import unittest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from agentj import private_cipher as C

class CipherContract(unittest.TestCase):
    def test_cross_runtime_nonce_aad_and_authentication(self):
        key=bytes(range(32));prefix=bytes(range(8));channel='c'*22;offer='a'*32;transfer='b'*32
        aad=C.aad(channel,offer,transfer,7,50*1024*1024)
        self.assertEqual(aad,('agentj-private-file-v1\n'+channel+'\n'+offer+'\n'+transfer+'\n7\n52428800').encode())
        self.assertEqual(C.nonce(prefix,7),prefix+b'\0\0\0\7')
        data=b'%PDF-1.7\n'+b'x'*100
        encrypted=C.encrypt(key,prefix,channel,offer,transfer,7,50*1024*1024,data)
        self.assertNotIn(data,encrypted)
        self.assertEqual(AESGCM(key).decrypt(C.nonce(prefix,7),encrypted,aad),data)
        for wrong in [C.aad('z'*22,offer,transfer,7,50*1024*1024), C.aad(channel,'e'*32,transfer,7,50*1024*1024), C.aad(channel,offer,'e'*32,7,50*1024*1024), C.aad(channel,offer,transfer,8,50*1024*1024), C.aad(channel,offer,transfer,7,1)]:
            with self.assertRaises(InvalidTag): AESGCM(key).decrypt(C.nonce(prefix,7),encrypted,wrong)
    def test_bounds(self):
        for bad in [-1,True,2**32]:
            with self.assertRaises(ValueError): C.nonce(b'x'*8,bad)
        with self.assertRaises(ValueError): C.nonce(b'x'*7,0)
        with self.assertRaises(ValueError): C.encrypt(b'x'*32,b'x'*8,'c'*22,'a'*32,'b'*32,0,65537,b'x'*65537)
        for size in [0,True,C.MAX_BYTES+1]:
            with self.assertRaises(ValueError): C.aad('c'*22,'a'*32,'b'*32,0,size)

class CrossLanguage(unittest.TestCase):
    def test_python_host_aead_and_signature_node_device_get_proof(self):
        import base64
        import hashlib
        import json
        from pathlib import Path
        import subprocess
        import tempfile
        from types import SimpleNamespace as NS
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from agentj import private_http, wire
        hostkey=Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        pub=lambda key:key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        host=NS(sk=hostkey,st=NS(_ed_pub=pub))
        channel=wire.channel_id(pub(hostkey));offer='a'*32;transfer='b'*32;data=b'%PDF-1.7\nsynthetic\n'
        key=bytes(range(32));prefix=bytes(range(8));index=0
        path=f'/v1/file/{channel}/{offer}/chunk/0'
        encrypted=C.encrypt(key,prefix,channel,offer,transfer,index,len(data),data)
        headers=private_http.headers(host,'PUT',path,encrypted,now=1234,nonce=wire.b64u(b'n'*16))
        fixture={'synthetic':True,'channel':channel,'offer':offer,'transfer':transfer,'index':index,'total':len(data),
                 'key_bytes':list(key),'prefix_bytes':list(prefix),'ciphertext':wire.b64u(encrypted),
                 'plaintext_sha256':hashlib.sha256(data).hexdigest(),'path':path,'host_headers':headers}
        script=r'''
import { webcrypto as crypto } from 'node:crypto';
import { readFileSync } from 'node:fs';
const v=JSON.parse(readFileSync(process.argv[1],'utf8'));
const enc=new TextEncoder(),b=s=>new Uint8Array(Buffer.from(s,'base64url'));
const h=v.host_headers;
const host=await crypto.subtle.importKey('raw',b(h['X-AJ-File-Key']),{name:'Ed25519'},false,['verify']);
const canonical=`agentj-file-host-v1\nPUT\n${v.path}\n${h['X-AJ-File-Time']}\n${h['X-AJ-File-Nonce']}\n${h['X-AJ-File-Hash']}`;
if(!await crypto.subtle.verify('Ed25519',host,b(h['X-AJ-File-Signature']),enc.encode(canonical)))throw Error('host_signature');
const nonce=new Uint8Array(12);nonce.set(v.prefix_bytes);new DataView(nonce.buffer).setUint32(8,v.index,false);
const aad=enc.encode(`agentj-private-file-v1\n${v.channel}\n${v.offer}\n${v.transfer}\n${v.index}\n${v.total}`);
const key=await crypto.subtle.importKey('raw',new Uint8Array(v.key_bytes),'AES-GCM',false,['decrypt']);
const plaintext=await crypto.subtle.decrypt({name:'AES-GCM',iv:nonce,additionalData:aad,tagLength:128},key,b(v.ciphertext));
const sha=Buffer.from(await crypto.subtle.digest('SHA-256',plaintext)).toString('hex');
if(sha!==v.plaintext_sha256)throw Error('plaintext_sha');
const device=await crypto.subtle.generateKey('Ed25519',true,['sign','verify']);
const time='1234',nonceProof=Buffer.from(new Uint8Array(16).fill(100)).toString('base64url');
const get=`agentj-file-get-v1\nGET\n${v.path}\n${time}\n${nonceProof}\n${v.transfer}`;
const publicKey=Buffer.from(await crypto.subtle.exportKey('raw',device.publicKey)).toString('base64url');
const signature=Buffer.from(await crypto.subtle.sign('Ed25519',device.privateKey,enc.encode(get))).toString('base64url');
process.stdout.write(JSON.stringify({canonical:get,device_headers:{'X-AJ-File-Key':publicKey,'X-AJ-File-Signature':signature,'X-AJ-File-Time':time,'X-AJ-File-Nonce':nonceProof,'X-AJ-File-Transfer':v.transfer},sha256:sha}));
'''
        with tempfile.TemporaryDirectory() as temp:
            vector=Path(temp)/'vector.json';vector.write_text(json.dumps(fixture))
            result=subprocess.run(['node','--input-type=module','-e',script,str(vector)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        proof=json.loads(result.stdout);dh=proof['device_headers']
        expected=f"agentj-file-get-v1\nGET\n{path}\n1234\n{dh['X-AJ-File-Nonce']}\n{transfer}"
        self.assertEqual(proof['canonical'],expected)
        Ed25519PublicKey.from_public_bytes(wire.unb64u(dh['X-AJ-File-Key'])).verify(wire.unb64u(dh['X-AJ-File-Signature']),expected.encode())
        fixture['node_proof']=proof
        out=Path(__file__).resolve().parents[3]/'reports/qa/578-host/cross-language-vector.json'
        out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(fixture,indent=2)+'\n')
