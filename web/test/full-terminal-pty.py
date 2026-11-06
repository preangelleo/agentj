"""Isolated browser fixture: JSON over stdio, never a network terminal."""
import codecs, fcntl, json, os, pty, select, signal, struct, sys, tempfile, termios
if '--menu' in sys.argv:
    import tty
    old=termios.tcgetattr(0)
    try:
        tty.setcbreak(0); index=0
        print('MENU alpha / beta', flush=True)
        while True:
            key=os.read(0,1)
            if key==b'\x1b':
                rest=os.read(0,2)
                if rest in (b'[B',b'OB'): index=1
                if rest in (b'[A',b'OA'): index=0
                print('\rMENU '+('alpha' if index==0 else 'beta')+'     ',end='',flush=True)
            if key in (b'\r',b'\n'):
                print('\r\nSELECTED='+('alpha' if index==0 else 'beta'),flush=True);break
    finally: termios.tcsetattr(0,termios.TCSADRAIN,old)
    sys.exit(0)
with tempfile.TemporaryDirectory(prefix='agentj-pty-') as home:
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(home)
        os.execve('/bin/bash', ['bash', '--noprofile', '--norc', '-i'], {'HOME':home,'PATH':'/usr/bin:/bin','TERM':'xterm-256color','COLORTERM':'truecolor','NVIM_NOTTYFAST':'1','LANG':'C.UTF-8','PS1':'fixture$ ','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','DISABLE_AUTOUPDATER':'1'})
    dec = codecs.getincrementaldecoder('utf-8')('replace')
    pending = b''
    try:
        while True:
            ready, _, _ = select.select([fd, sys.stdin], [], [])
            if fd in ready:
                try: data=os.read(fd, 65536)
                except OSError: break
                if not data: break
                print(json.dumps({'d':dec.decode(data)}), flush=True)
            if sys.stdin in ready:
                chunk=os.read(0,65536)
                if not chunk: break
                pending+=chunk
                while b'\n' in pending:
                    line,pending=pending.split(b'\n',1)
                    msg=json.loads(line)
                    if 'd' in msg: os.write(fd, msg['d'].encode())
                    if 'cols' in msg: fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH',msg['rows'],msg['cols'],0,0))
    finally:
        try: os.kill(pid, signal.SIGHUP)
        except ProcessLookupError: pass
        os.close(fd)
        os.waitpid(pid,0)
