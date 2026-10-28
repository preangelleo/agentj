"""P170: a local fake IMAP4rev1 + SMTP server pair on 127.0.0.1 (plain TCP) for the mail channel tests. Never a real mailbox.

FakeIMAP: LOGIN, CAPABILITY, ID, SELECT/EXAMINE INBOX, UID SEARCH ALL, UID FETCH (RFC822.SIZE | BODY.PEEK[] |
BODY.PEEK[HEADER.FIELDS (…)] | INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER] (P206)), LOGOUT, NOOP. `fetched` lists the full
bodies handed out (uids); flags are never changed by the check. `require_id=True` behaves like NetEase 163: SELECT says "Unsafe Login" until ID.
P565 (`agentj mail trash`): LIST over `folders` [(flags, name)], UID MOVE (only with "MOVE" in `caps`) / COPY / STORE /
EXPUNGE, and the id fetch `BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)] BODY.PEEK[]<0.2048>`; `boxes` keeps what left the INBOX.
FakeSMTP: EHLO/HELO, AUTH PLAIN / LOGIN, MAIL, RCPT, DATA, RSET, NOOP, QUIT; `sent` keeps (from, [to], bytes).
"""
from __future__ import annotations

import base64
import re
import socket
import socketserver
import threading
import time


def _tokens(s: str) -> list[str]:
    out, i = [], 0
    while i < len(s):
        if s[i] == " ":
            i += 1
        elif s[i] == '"':
            j, buf = i + 1, []
            while j < len(s) and s[j] != '"':
                if s[j] == "\\" and j + 1 < len(s):
                    j += 1
                buf.append(s[j])
                j += 1
            out.append("".join(buf))
            i = j + 1
        else:
            j = s.find(" ", i)
            j = len(s) if j < 0 else j
            out.append(s[i:j])
            i = j
    return out


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeIMAP:
    def __init__(self, user="agent@example.cn", password="right-code", require_id=False, caps="IMAP4rev1 ID", folders=()):
        self.user, self.password, self.require_id = user, password, require_id
        self.caps, self.folders = caps, list(folders)
        self.boxes: dict[str, list[tuple[int, bytes]]] = {}
        self.flags: dict[int, set] = {}
        self.uidvalidity = 7
        self.mails: list[tuple[int, bytes]] = []
        self.next_uid = 1
        self.dates: dict[int, float] = {}
        self.fetched: list[int] = []
        self.logins = 0
        self.commands: list[str] = []
        fake = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                authed = ident = False
                self.wfile.write(f"* OK [CAPABILITY {fake.caps}] fake ready\r\n".encode())
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    text = line.decode("utf-8", "replace").rstrip("\r\n")
                    tag, _, rest = text.partition(" ")
                    cmd, _, args = rest.partition(" ")
                    cmd = cmd.upper()
                    fake.commands.append(cmd if cmd != "LOGIN" else "LOGIN ***")
                    w = self.wfile.write
                    if cmd == "CAPABILITY":
                        w(f"* CAPABILITY {fake.caps}\r\n".encode() + tag.encode() + b" OK done\r\n")
                    elif cmd == "LIST":
                        w(b"".join(f'* LIST ({fl}) "/" "{name}"\r\n'.encode() for fl, name in fake.folders) + tag.encode() + b" OK done\r\n")
                    elif cmd == "ID":
                        ident = True
                        w(b'* ID ("name" "fake")\r\n' + tag.encode() + b" OK ID done\r\n")
                    elif cmd == "LOGIN":
                        t = _tokens(args)
                        if len(t) == 2 and t[0] == fake.user and t[1] == fake.password:
                            authed = True
                            fake.logins += 1
                            w(tag.encode() + f" OK [CAPABILITY {fake.caps}] logged in\r\n".encode())
                        else:
                            w(tag.encode() + b" NO [AUTHENTICATIONFAILED] login failed\r\n")
                    elif cmd in ("SELECT", "EXAMINE"):
                        if not authed:
                            w(tag.encode() + b" BAD not authenticated\r\n")
                        elif fake.require_id and not ident:
                            w(tag.encode() + b" NO SELECT Unsafe Login. Please contact kefu@188.com for help\r\n")
                        else:
                            w(f"* {len(fake.mails)} EXISTS\r\n* OK [UIDVALIDITY {fake.uidvalidity}] ok\r\n".encode()
                              + tag.encode() + (b" OK [READ-ONLY] done\r\n" if cmd == "EXAMINE" else b" OK [READ-WRITE] done\r\n"))
                    elif cmd == "UID":
                        sub, _, a2 = args.partition(" ")
                        sub = sub.upper()
                        if sub == "SEARCH":
                            w(("* SEARCH " + " ".join(str(u) for u, _ in fake.mails)).rstrip().encode() + b"\r\n"
                              + tag.encode() + b" OK done\r\n")
                        elif sub in ("MOVE", "COPY") and (sub == "COPY" or "MOVE" in fake.caps.split()):
                            u, _, box = a2.partition(" ")
                            box = _tokens(box)[0] if box else ""
                            hit = [(x, raw) for x, raw in fake.mails if str(x) == u]
                            if not hit or box not in [n for _, n in fake.folders]:
                                w(tag.encode() + b" NO no such message or mailbox\r\n")
                            else:
                                fake.boxes.setdefault(box, []).append(hit[0])
                                if sub == "MOVE":
                                    fake.mails = [m for m in fake.mails if str(m[0]) != u]
                                w(tag.encode() + b" OK done\r\n")
                        elif sub == "STORE":
                            u, _, rest2 = a2.partition(" ")
                            if "\\Deleted" in rest2:
                                fake.flags.setdefault(int(u), set()).add("\\Deleted")
                            w(tag.encode() + b" OK done\r\n")
                        elif sub == "EXPUNGE":
                            gone = {int(x) for x in a2.split(",") if x.isdigit() and "\\Deleted" in fake.flags.get(int(x), set())}
                            fake.mails = [m for m in fake.mails if m[0] not in gone]
                            w(tag.encode() + b" OK done\r\n")
                        elif sub == "FETCH":
                            want, _, item = a2.partition(" ")
                            rows = [(i + 1, u, raw) for i, (u, raw) in enumerate(fake.mails) if str(u) in want.split(",")]
                            for seq, u, raw in rows:
                                if "MESSAGE-ID" in item.upper():
                                    head = raw.split(b"\r\n\r\n", 1)[0]
                                    keep = b"".join(ln + b"\r\n" for ln in re.split(rb"\r?\n(?![ \t])", head)
                                                    if ln.lower().startswith(b"message-id:")) + b"\r\n"
                                    w(f"* {seq} FETCH (UID {u} BODY[HEADER.FIELDS (MESSAGE-ID)] {{{len(keep)}}}\r\n".encode() + keep
                                      + f" BODY[]<0> {{{len(raw[:2048])}}}\r\n".encode() + raw[:2048] + b")\r\n")
                                elif "INTERNALDATE" in item.upper():
                                    head = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                                    when = time.strftime("%d-%b-%Y %H:%M:%S +0000", time.gmtime(fake.dates.get(u, time.time())))
                                    w(f'* {seq} FETCH (UID {u} INTERNALDATE "{when}" RFC822.SIZE {len(raw)} BODY[HEADER] {{{len(head)}}}\r\n'.encode()
                                      + head + b")\r\n")
                                elif "RFC822.SIZE" in item.upper():
                                    w(f"* {seq} FETCH (UID {u} RFC822.SIZE {len(raw)})\r\n".encode())
                                elif "HEADER.FIELDS" in item.upper():
                                    head = raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0]
                                    keep = b"".join(ln + b"\r\n" for ln in re.split(rb"\r?\n(?![ \t])", head)
                                                    if ln.lower().startswith(b"authentication-results:")) + b"\r\n"
                                    w(f"* {seq} FETCH (UID {u} BODY[HEADER.FIELDS (AUTHENTICATION-RESULTS)] {{{len(keep)}}}\r\n".encode()
                                      + keep + b")\r\n")
                                else:
                                    fake.fetched.append(u)
                                    w(f"* {seq} FETCH (UID {u} BODY[] {{{len(raw)}}}\r\n".encode() + raw + b")\r\n")
                            w(tag.encode() + b" OK done\r\n")
                        else:
                            w(tag.encode() + b" BAD unsupported\r\n")
                    elif cmd == "NOOP":
                        w(tag.encode() + b" OK\r\n")
                    elif cmd == "LOGOUT":
                        w(b"* BYE bye\r\n" + tag.encode() + b" OK done\r\n")
                        return
                    else:
                        w(tag.encode() + b" BAD unknown\r\n")

        self.server = _Server(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, raw: bytes | str, at: float | None = None) -> int:
        if isinstance(raw, str):
            raw = raw.replace("\r\n", "\n").replace("\n", "\r\n").encode()
        uid = self.next_uid
        self.next_uid += 1
        self.mails.append((uid, raw))
        self.dates[uid] = time.time() if at is None else at
        return uid

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeSMTP:
    def __init__(self, user="agent@example.cn", password="right-code"):
        self.user, self.password = user, password
        self.sent: list[tuple[str, list[str], bytes]] = []
        self.refuse: set[str] = set()          # P295: RCPT TO these → 550 (the rest of the mail still goes)
        self.data_reply = b"250 queued"        # P295: what the server says after DATA (Gmail: `250 2.0.0 OK <ts> <id> - gsmtp`)
        fake = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                w = self.wfile.write
                w(b"220 fake smtp\r\n")
                authed, frm, to = False, None, []
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    text = line.decode("utf-8", "replace").rstrip("\r\n")
                    up = text.upper()
                    if up.startswith(("EHLO", "HELO")):
                        w(b"250-fake\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n")
                    elif up.startswith("AUTH PLAIN"):
                        parts = text.split(" ", 2)
                        blob = parts[2] if len(parts) > 2 else None
                        if blob is None:
                            w(b"334 \r\n")
                            blob = self.rfile.readline().decode().strip()
                        try:
                            _, u, p = base64.b64decode(blob).decode().split("\0")
                        except Exception:   # noqa: BLE001
                            u = p = ""
                        authed = u == fake.user and p == fake.password
                        w(b"235 ok\r\n" if authed else b"535 bad credentials\r\n")
                    elif up.startswith("AUTH LOGIN"):
                        parts = text.split(" ", 2)
                        if len(parts) > 2:
                            u = base64.b64decode(parts[2]).decode()
                        else:
                            w(b"334 VXNlcm5hbWU6\r\n")
                            u = base64.b64decode(self.rfile.readline().strip()).decode()
                        w(b"334 UGFzc3dvcmQ6\r\n")
                        p = base64.b64decode(self.rfile.readline().strip()).decode()
                        authed = u == fake.user and p == fake.password
                        w(b"235 ok\r\n" if authed else b"535 bad credentials\r\n")
                    elif up.startswith("MAIL FROM:"):
                        if not authed:
                            w(b"530 auth required\r\n")
                            continue
                        frm, to = text[10:].strip().strip("<>").split(">")[0], []
                        w(b"250 ok\r\n")
                    elif up.startswith("RCPT TO:"):
                        rc = text[8:].strip().strip("<>").split(">")[0]
                        if rc in fake.refuse:
                            w(b"550 no such user\r\n")
                            continue
                        to.append(rc)
                        w(b"250 ok\r\n")
                    elif up == "DATA":
                        w(b"354 go\r\n")
                        buf = bytearray()
                        while True:
                            ln = self.rfile.readline()
                            if ln in (b".\r\n", b".\n", b""):
                                break
                            buf += ln[1:] if ln.startswith(b"..") else ln
                        fake.sent.append((frm, list(to), bytes(buf)))
                        w(fake.data_reply + b"\r\n")
                    elif up in ("RSET", "NOOP"):
                        w(b"250 ok\r\n")
                    elif up == "QUIT":
                        w(b"221 bye\r\n")
                        return
                    else:
                        w(b"502 no\r\n")

        self.server = _Server(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def mail(frm="boss@corp.example", to="agent@example.cn", subject="请整理周报", body="帮我把这周的周报整理一下。",
         ar="mx.example.cn; spf=pass smtp.mailfrom=corp.example; dkim=pass header.d=corp.example; dmarc=pass header.from=corp.example",
         msgid="<m1@corp.example>", extra="", parts=None) -> str:
    head = (f"Return-Path: <{frm}>\n" + (f"Authentication-Results: {ar}\n" if ar else "")
            + f"Received: from mail.corp.example by mx.example.cn\nFrom: Boss <{frm}>\nTo: {to}\nSubject: {subject}\n"
            f"Message-ID: {msgid}\nDate: Mon, 12 Oct 2026 10:00:00 +0800\nMIME-Version: 1.0\n{extra}")
    if parts is None:
        return head + "Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n" + body + "\n"
    return head + parts


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
