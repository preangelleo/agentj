"""369 fixed file executor. The privileged program is this frozen stdlib literal.

Nothing in the root process imports agentj, cwd, PYTHONPATH, or a local script.
The fixture entry executes the identical literal with a private root argument;
that argument is deliberately absent from the privileged entry/protocol.
"""
import hashlib
import os
from pathlib import Path
import stat

BOOTSTRAP = r'''
import base64, fcntl, hashlib, json, os, re, secrets, socket, stat, struct, sys

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def fingerprint(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_gid, info.st_nlink]

def require(ok):
    if not ok:
        raise ValueError('fixed_plan_refused')

def metadata(target, info, data, disabled=False):
    identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]
    enabled = not disabled if ':' not in target['id'] else not data.splitlines(keepends=True)[int(target['id'].rsplit(':',1)[1])-1].startswith(b'# agentj-disabled:')
    value = {k: target[k] for k in ('platform','source','scope','id')}
    content = sha(data)
    value.update(content=content, witness=sha(canonical([content, identity[:4]+identity[5:], enabled])),
                 config=sha(canonical([content, identity])), enabled=enabled, loaded=False)
    value['snapshot'] = sha(canonical(value))
    return value

def validate(plan, digest, root='/etc', owner=0):
    require(isinstance(plan,dict) and set(plan)=={'version','target','path','destination','stat','preimage','before','original','journal_key','journal_digest','preimage_bytes','original_bytes','desired_bytes'} and sha(canonical(plan)) == digest and plan['version'] == 1)
    t = plan['target']
    require(set(t) == {'platform','source','scope','id','snapshot','op'})
    require(t['platform'].startswith('linux') and t['scope']=='system' and t['op'] in ('disable','restore'))
    ident = t['id']
    line = ':' in ident
    if t['source']=='anacron':
        require(re.fullmatch(r'anacrontab:[1-9][0-9]{0,5}',ident))
    else:
        require(t['source']=='cron-system' and re.fullmatch(r'(?:(?:crontab|cron\.d/[A-Za-z0-9_-]+):[1-9][0-9]{0,5}|cron\.(?:hourly|daily|weekly|monthly)/[A-Za-z0-9_-]+)',ident))
    require(t['snapshot']==plan['before']['snapshot'])
    relative = ident.split(':')[0]
    parent, name = os.path.split(relative)
    if not line and t['op']=='restore' and plan['before']['enabled'] is False:
        name = '.agentj-disabled-' + name
    path = root + '/' + (parent+'/' if parent else '') + name
    require(plan['path']==path and len(plan['stat'])==9 and plan['stat'][6]==owner)
    raw, original, desired = [base64.b64decode(plan[k],validate=True) for k in ('preimage_bytes','original_bytes','desired_bytes')]
    require(max(map(len,(raw,original,desired)))<=262144 and sha(raw)==plan['preimage'] and sha(original)==plan['original']['content'])
    for data in (raw,original):
        text=data.decode('utf-8')
        require(not any(c in text for c in ('\x00','\v','\f','\x1c','\x1d','\x1e','\x85','\u2028','\u2029')) and '\r' not in text.replace('\r\n',''))
    destination = name
    if line:
        require(raw.endswith(b'\n') and original.endswith(b'\n') and not any(x.rstrip(b'\r\n').endswith(b'\\') for x in original.splitlines(keepends=True)))
        number=int(ident.rsplit(':',1)[1]); lines=original.splitlines(keepends=True)
        require(number<=len(lines))
        selected=lines[number-1].strip()
        # Frozen equivalent of the scanner's eligible_source_line grammar.
        text=selected.decode('utf-8')
        if t['source']=='anacron':
            fields=text.split(None,3)
            require(len(fields)==4 and (fields[0].isdigit() or fields[0]=='@monthly') and fields[1].isdigit() and re.fullmatch('[A-Za-z0-9_-]+',fields[2]))
        else:
            fields=text.split(None,2 if text.startswith('@') else 6)
            if text.startswith('@'):
                require(len(fields)==3 and fields[0] in ('@reboot','@yearly','@annually','@monthly','@weekly','@daily','@midnight','@hourly') and re.fullmatch('[A-Za-z_][A-Za-z0-9_-]*',fields[1]))
            else:
                names=r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|sun|mon|tue|wed|thu|fri|sat'
                field=re.compile(rf'(?i:[\d*?]|{names})(?i:[\d*/,\-?LW#]|{names})*')
                require(len(fields)==7 and all(field.fullmatch(x) for x in fields[:5]) and re.fullmatch('[A-Za-z_][A-Za-z0-9_-]*',fields[5]))
        lines[number-1]=b'# agentj-disabled:'+sha(original+ident.encode()).encode()+b' '+lines[number-1]
        disabled=b''.join(lines)
        require((raw==original and desired==disabled) if t['op']=='disable' else (raw in (disabled,original) and desired==original))
    else:
        require(raw==original==desired and plan['stat'][5]&0o111)
        destination = (name.removeprefix('.agentj-disabled-') if t['op']=='restore' else '.agentj-disabled-'+name)
    require(plan['destination']==destination)
    return path,raw,desired,destination,line

def open_source(path, owner, boundary='/etc'):
    fixture = boundary != '/etc'
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    traversed=''
    try:
        for part in path.split('/')[1:-1]:
            traversed += '/' + part
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd); fd=nxt; info=os.fstat(fd)
            require((fixture and not traversed.startswith(boundary) or info.st_uid==owner) and (not info.st_mode&0o022 or fixture and info.st_mode&stat.S_ISVTX))
        src=os.open(path.rsplit('/',1)[1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            info=os.fstat(src)
            require(stat.S_ISREG(info.st_mode) and info.st_uid==owner and info.st_nlink==1 and info.st_size<=262144 and not info.st_mode&0o7022 and not os.listxattr(src))
            raw=os.read(src,262145)
            require(len(raw)==info.st_size and fingerprint(info)==fingerprint(os.fstat(src)))
            return fd,info,raw
        finally: os.close(src)
    except BaseException:
        os.close(fd); raise

def execute(plan, digest, exchange, root='/etc', owner=0):
    path, raw, desired, destination, line=validate(plan,digest,root,owner)
    fd, info, data=open_source(path,owner,root)
    temp=None
    try:
        # Cooperating helpers lock the selected inode; uncooperative writers can race rename.
        lock=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            require(fingerprint(info)==plan['stat'] and data==raw and metadata(plan['target'],info,data,path.rsplit('/',1)[1].startswith('.'))==plan['before'])
            if line:
                temp='.agentj-stage-'+secrets.token_hex(16)
                out=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                try:
                    os.fchown(out,info.st_uid,info.st_gid); os.fchmod(out,stat.S_IMODE(info.st_mode))
                    view=memoryview(desired)
                    while view:
                        n=os.write(out,view); require(n>0); view=view[n:]
                    os.fsync(out); staged=os.fstat(out)
                    expected=metadata(plan['target'],staged,desired)
                finally: os.close(out)
            else:
                expected=metadata(plan['target'],info,data,plan['target']['op']=='disable')
                require(not os.path.lexists(os.path.dirname(path)+'/'+destination))
            # The Host journals exact original/expected BEFORE granting this one-use permit.
            challenge=secrets.token_hex(32)
            require(exchange({'ready':expected,'challenge':challenge})=={'permit':challenge})
            check, current, current_data=open_source(path,owner,root)
            try:
                a,b=os.fstat(fd),os.fstat(check)
                require((a.st_dev,a.st_ino)==(b.st_dev,b.st_ino) and fingerprint(current)==plan['stat'] and current_data==raw)
            finally: os.close(check)
            if line:
                os.replace(temp,destination,src_dir_fd=fd,dst_dir_fd=fd); temp=None
                after=os.stat(destination,dir_fd=fd,follow_symlinks=False)
                require((after.st_dev,after.st_ino)==(staged.st_dev,staged.st_ino))
            else:
                import ctypes
                libc=ctypes.CDLL(None,use_errno=True)
                rename=getattr(libc,'renameat2',None)
                require(rename is not None)
                require(rename(fd,os.fsencode(path.rsplit('/',1)[1]),fd,os.fsencode(destination),1)==0)
            os.fsync(fd)
            check,after,after_data=open_source(os.path.dirname(path)+'/'+destination,owner,root)
            os.close(check)
            observed=metadata(plan['target'],after,after_data,destination.startswith('.'))
            require(after_data==desired and observed['witness']==expected['witness'])
            require(exchange({'done':observed})=={'ack':True})
            return observed
        finally: os.close(lock)
    finally:
        if temp is not None: os.unlink(temp,dir_fd=fd)
        os.close(fd)

def send(stream, value):
    data=canonical(value)+b'\n'; require(len(data)<=2*1024*1024)
    stream.write(data); stream.flush()

def receive(stream):
    raw=stream.readline(2*1024*1024+1)
    require(raw.endswith(b'\n') and len(raw)<=2*1024*1024)
    return json.loads(raw)

def main():
    require(len(sys.argv)==8 and os.geteuid()==0)
    endpoint,dev,ino,uid,host_pid,nonce,digest=sys.argv[1:]
    require(re.fullmatch('[0-9a-f]{64}',nonce) and re.fullmatch('[0-9a-f]{64}',digest))
    expected=(int(dev),int(ino),int(uid))
    def check():
        info=os.lstat(endpoint); parent=os.lstat(os.path.dirname(endpoint))
        require(stat.S_ISSOCK(info.st_mode) and (info.st_dev,info.st_ino,info.st_uid)==expected and not info.st_mode&0o077)
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid==int(uid) and not parent.st_mode&0o077)
    check()
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(15); sock.connect(endpoint); check()
        peer=struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
        require(peer[:2]==(int(host_pid),int(uid)))
        with sock.makefile('rwb',buffering=0) as stream:
            hello=secrets.token_hex(32)
            send(stream,{'nonce':nonce,'digest':digest,'hello':hello})
            response=receive(stream); require(response['hello']==hello)
            def exchange(value):
                send(stream,value); return receive(stream)
            execute(response['plan'],digest,exchange)

if __name__=='__main__':
    try: main()
    except Exception: sys.exit(1)
'''
BOOTSTRAP_SHA256 = hashlib.sha256(BOOTSTRAP.encode()).hexdigest()


def trusted_interpreter():
    """Resolve a system symlink once, then validate the real executable and parents."""
    candidate = Path('/usr/bin/python3').resolve(strict=True)
    if not str(candidate).startswith('/usr/bin/python3'):
        raise ValueError('unsafe_interpreter')
    for path in (candidate, *candidate.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('unsafe_interpreter')
    if not stat.S_ISREG(candidate.stat().st_mode) or not os.access(candidate, os.X_OK):
        raise ValueError('unsafe_interpreter')
    return str(candidate)


def fixture_kernel():
    """Same reviewed functions; no public command accepts fixture roots/owners."""
    namespace = {'__name__': 'native_scheduler_fixture'}
    exec(compile(BOOTSTRAP, '<fixed-369>', 'exec'), namespace)
    return namespace
