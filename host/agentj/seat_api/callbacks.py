"""Owner-registered signed callbacks (SEAT_API.md §7). Callers pick a callback_id, never a URL.

Every attempt re-resolves DNS, refuses any non-global address and connects the socket to the vetted address itself, so a
DNS rebinding between check and connect cannot reach a private network. No redirects; TLS verifies the hostname.
"""
import hashlib
import hmac
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit
from ..bots.store import BotError

TIMEOUT=10
def check_url(url):
    try:u=urlsplit(url)
    except (ValueError,TypeError):raise BotError('invalid_callback') from None
    if not isinstance(url,str) or len(url)>512 or u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment or any(c.isspace() for c in url):raise BotError('invalid_callback')
    try:port=u.port
    except ValueError:raise BotError('invalid_callback') from None
    if port not in (None,443):raise BotError('invalid_callback')
    try:ipaddress.ip_address(u.hostname.strip('[]'));raise BotError('invalid_callback')
    except ValueError:pass
    if '.' not in u.hostname or u.hostname.endswith(('.local','.localhost','.internal','.lan','.home.arpa')) or u.hostname=='localhost':raise BotError('invalid_callback')
    return url
def public_address(ip):
    a=ipaddress.ip_address(ip)
    if isinstance(a,ipaddress.IPv6Address) and (a.ipv4_mapped or a.sixtofour or a.teredo):return False
    if a in ipaddress.ip_network('100.64.0.0/10') or a in ipaddress.ip_network('192.0.0.0/24') or a in ipaddress.ip_network('198.18.0.0/15'):return False
    return a.is_global and not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified)
def resolve(host,resolver=socket.getaddrinfo):
    try:infos=resolver(host,443,type=socket.SOCK_STREAM)
    except (OSError,UnicodeError):raise BotError('callback_dns') from None
    addrs=[i[4][0] for i in infos]
    # Any non-global answer refuses the whole name: no "pick the public one" games.
    if not addrs or not all(public_address(a) for a in addrs):raise BotError('callback_address')
    return addrs[0]
def signature(secret,ts,event_id,body):
    return 'v1='+hmac.new(secret,(str(ts)+'.'+event_id+'.').encode()+body,hashlib.sha256).hexdigest()

class _Pinned(http.client.HTTPSConnection):
    def __init__(self,host,address,context):super().__init__(host,443,timeout=TIMEOUT,context=context);self.address=address
    def connect(self):
        sock=socket.create_connection((self.address,443),TIMEOUT)
        self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        if not self._before_send():
            self.close();raise OSError('callback_authority_changed')
def post(url,secret,event_id,body,now=None,resolver=socket.getaddrinfo,context=None,connection=_Pinned,before_send=lambda:True):
    """One signed attempt. Returns True only for a 2xx; 3xx (redirect) is a failure."""
    u=urlsplit(check_url(url));address=resolve(u.hostname,resolver)
    ts=int(now if now is not None else time.time())
    headers={'content-type':'application/json','user-agent':'AgentJ-Callback/1','x-agentj-timestamp':str(ts),'x-agentj-event':event_id,'x-agentj-signature':signature(secret,ts,event_id,body)}
    if not before_send():return False
    c=connection(u.hostname,address,context or ssl.create_default_context())
    c._before_send=before_send
    try:
        c.request('POST',(u.path or '/')+('?'+u.query if u.query else ''),body=body,headers=headers)
        r=c.getresponse();r.read(4096);return 200<=r.status<300
    except (OSError,http.client.HTTPException,ssl.SSLError):return False
    finally:c.close()
