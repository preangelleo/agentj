"""Single HTTPS boundary: DNS checked and pinned, no redirects, bounded read.

Even owner-defined tools cannot reach loopback, metadata services or private networks.
No exception from http.client / upstream body leaves this wrapper.
"""
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit
from .store import BotError

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,hostname,ip,port,timeout):
        super().__init__(hostname,port,timeout=timeout,context=ssl.create_default_context());self.ip=ip
    def connect(self):
        sock=socket.create_connection((self.ip,self.port),self.timeout)
        try:self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except BaseException:sock.close();raise

def request(url,method='GET',body=None,headers=None,timeout=10,limit=262144,domains=None):
    try:
        u=urlsplit(url)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment or u.port not in (None,443):raise BotError('unsafe_url')
        if domains is not None and u.hostname not in domains:raise BotError('domain_not_allowed')
        addrs=socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM)
        ips=list(dict.fromkeys(a[4][0] for a in addrs))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):raise BotError('unsafe_address')
        conn=PinnedHTTPS(u.hostname,ips[0],443,timeout)
        try:
            h={'User-Agent':'agentj-bots/1','Accept':'application/json',**(headers or {})}
            conn.request(method,(u.path or '/')+('?' + u.query if u.query else ''),body=body,headers=h)
            response=conn.getresponse()
            if not 200<=response.status<300:raise BotError('upstream_http_'+str(response.status))
            raw=response.read(limit+1)
            if len(raw)>limit:raise BotError('response_too_large')
            return raw
        finally:conn.close()
    except BotError:raise
    except Exception:raise BotError('upstream_failed') from None

def request_json(url,method='GET',body=None,headers=None,**kw):
    try:return json.loads(request(url,method,json.dumps(body,ensure_ascii=False).encode() if body is not None else None,{'Content-Type':'application/json',**(headers or {})},**kw))
    except (ValueError,TypeError):raise BotError('invalid_response') from None
