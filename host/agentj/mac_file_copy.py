"""P478 macOS boundary: Finder duplicate by ephemeral file-reference URL.

CFURLCreateFileReferenceURL binds a filesystem object, not a path or Finder alias.
Keep a metadata-only fd open until Finder replies so the inode cannot be recycled.
All native failures refuse; there is no path/AppleScript/GUI/sudo fallback.
"""
from __future__ import annotations

import ctypes as C
import os
import pathlib
import re
import sys


class NativeError(ValueError):
    pass


class Desc(C.Structure):
    _fields_ = [("type", C.c_uint32), ("data", C.c_void_p)]


def code(s):
    return int.from_bytes(s.encode("ascii"), "big")


class Native:
    def __init__(self):
        if sys.platform != "darwin":
            raise NativeError("unsupported")
        self.ae = C.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        d, v, u, size = C.POINTER(Desc), C.c_void_p, C.c_uint32, C.c_long
        self._bind(self.ae, "AECreateDesc", [u, v, size, d])
        self._bind(self.ae, "AEDisposeDesc", [d])
        self._bind(self.ae, "AEDeterminePermissionToAutomateTarget", [d, u, u, C.c_bool])
        self._bind(self.ae, "AECreateAppleEvent", [u, u, d, C.c_int16, C.c_int32, d])
        self._bind(self.ae, "AEPutParamPtr", [d, u, u, v, size])
        self._bind(self.ae, "AEGetParamPtr", [d, u, u, C.POINTER(u), v, size, C.POINTER(size)])
        self._bind(self.ae, "AESendMessage", [d, d, u, C.c_int32])
        self._bind(self.cf, "CFRelease", [v], None)
        self._bind(self.cf, "CFURLCreateFromFileSystemRepresentation", [v, v, size, C.c_bool], v)
        self._bind(self.cf, "CFURLCreateFileReferenceURL", [v, v, v], v)
        self._bind(self.cf, "CFURLCreateFilePathURL", [v, v, v], v)
        self._bind(self.cf, "CFURLCopyFileSystemPath", [v, C.c_int], v)
        self._bind(self.cf, "CFURLGetString", [v], v)
        self._bind(self.cf, "CFStringGetCString", [v, v, size, u], C.c_bool)

    @staticmethod
    def _bind(lib, name, args, result=C.c_int32):
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, result

    @staticmethod
    def ok(status):
        if status != 0:
            raise NativeError("native_refused")

    def target(self):
        d = Desc()
        b = b"com.apple.finder"
        self.ok(self.ae.AECreateDesc(code("bund"), b, len(b), C.byref(d)))
        return d

    def preflight(self, ask=False):
        target = self.target()
        try:
            return self.ae.AEDeterminePermissionToAutomateTarget(C.byref(target), code("core"), code("clon"), ask) == 0
        finally:
            self.ae.AEDisposeDesc(C.byref(target))

    def string(self, value):
        b = C.create_string_buffer(32768)
        if not value or not self.cf.CFStringGetCString(value, b, len(b), 0x08000100):
            raise NativeError("unverifiable_reference")
        return b.value.decode("utf-8")

    def reference(self, source):
        """Returns (opaque-bytes file reference URL, currently resolved path).

        No alias or bookmark fallback by pathname. Reference URLs are never stored.
        """
        owned = []
        try:
            raw = os.fsencode(source)
            url = self.cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
            if not url:
                raise NativeError("unverifiable_reference")
            owned.append(url)
            ref = self.cf.CFURLCreateFileReferenceURL(None, url, None)
            if not ref:
                raise NativeError("unverifiable_reference")
            owned.append(ref)
            resolved = self.cf.CFURLCreateFilePathURL(None, ref, None)
            if not resolved:
                raise NativeError("unverifiable_reference")
            owned.append(resolved)
            path = self.cf.CFURLCopyFileSystemPath(resolved, 0)
            if not path:
                raise NativeError("unverifiable_reference")
            owned.append(path)
            reference = self.string(self.cf.CFURLGetString(ref))
            if not reference.startswith("file:///.file/id="):
                raise NativeError("unverifiable_reference")
            return reference.encode(), self.string(path)
        finally:
            for item in reversed(owned):
                self.cf.CFRelease(item)

    def duplicate(self, reference, destination):
        target, event, reply = self.target(), Desc(), Desc()
        try:
            self.ok(self.ae.AECreateAppleEvent(code("core"), code("clon"), C.byref(target), -1, 0, C.byref(event)))
            for key, value in (("----", reference), ("insh", pathlib.Path(destination).as_uri().encode())):
                self.ok(self.ae.AEPutParamPtr(C.byref(event), code(key), code("furl"), value, len(value)))
            # kAEWaitReply | kAENeverInteract | kAEDoNotPromptForUserConsent.
            # No background OS prompt even if permission is reset after preflight.
            self.ok(self.ae.AESendMessage(C.byref(event), C.byref(reply), 3 | 0x10 | 0x80000, 120 * 60))
            error, actual, size = C.c_int32(), C.c_uint32(), C.c_long()
            status = self.ae.AEGetParamPtr(C.byref(reply), code("errn"), code("long"), C.byref(actual),
                                         C.byref(error), C.sizeof(error), C.byref(size))
            if status not in (0, -1701) or error.value:
                raise NativeError("finder_refused")
        finally:
            for item in (reply, event, target):
                self.ae.AEDisposeDesc(C.byref(item))


def automation(ask=False):
    try:
        return Native().preflight(ask)
    except (OSError, AttributeError, NativeError):
        return False


def duplicate(source, destination, expected, validate):
    """validate(resolved) checks the same host policy + source snapshot again.

    The metadata fd is mandatory. TCC may refuse even this open; then copying is
    refused rather than guessing an identity from paths or a Finder alias.
    """
    native = Native()
    if not native.preflight(False):
        raise NativeError("finder_needs_local_permission")
    fd = os.open(source, getattr(os, "O_EVTONLY", 0x8000) | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_mode) != expected:
            raise NativeError("changed")
        reference, resolved = native.reference(source)
        # Accept only Apple's file-reference URL form, with the exact pinned inode
        # and the same volume as a separately pinned mount root. A new/unknown URL
        # format refuses; never fall back to a path or a Finder alias.
        mount = pathlib.Path(source).parent
        while mount.parent != mount and mount.parent.stat().st_dev == info.st_dev:
            mount = mount.parent
        root_fd = os.open(mount, getattr(os, "O_EVTONLY", 0x8000) | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            root_info = os.fstat(root_fd)
            root_reference, _ = native.reference(mount)
            a = re.fullmatch(rb"file:///.file/id=(\d+)\.(\d+)/?", reference)
            b = re.fullmatch(rb"file:///.file/id=(\d+)\.(\d+)/?", root_reference)
            if (not a or not b or root_info.st_dev != info.st_dev or a[1] != b[1]
                    or int(a[2]) != info.st_ino or int(b[2]) != root_info.st_ino):
                raise NativeError("unverifiable_reference")
            validate(resolved)
            native.duplicate(reference, destination)
        finally:
            os.close(root_fd)
    finally:
        os.close(fd)
