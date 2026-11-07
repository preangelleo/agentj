"""Legacy state liveness probe shared by path selection and migration.

No State, preferences, migration or configuration imports.
"""
import os
import socket

def serve_running(root) -> bool:
    """Is a serve using this directory? = its control socket accepts a connection. A socket file that refuses (a crashed
    serve) is not running; anything unclear counts as running (never move a directory under a live process)."""
    sock = os.path.join(str(root), "control.sock")
    if not os.path.exists(sock):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(sock)
        return True
    except (FileNotFoundError, ConnectionRefusedError):
        return False
    except OSError:
        return True
    finally:
        s.close()


