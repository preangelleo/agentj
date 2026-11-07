"""Closed Linux/Darwin current-user crontab binary contract.

Root's OS installation is the trust authority, never PATH or a version banner.
No detection subprocess or installation probe is performed.
"""
import os
import stat


class UnsafeBinary(Exception):
    pass


def identity(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
            st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class NativeCrontab:
    def __init__(self, platform):
        if platform not in ('linux', 'darwin'):
            raise UnsafeBinary('unsupported_platform')
        self.platform = platform
        self.pinned = None

    def inspect(self, path):
        resolved = os.path.realpath(path)
        allowed = ('/usr/bin/crontab', '/usr/sbin/crontab') if self.platform == 'linux' else ('/usr/bin/crontab',)
        if resolved not in allowed:
            raise UnsafeBinary('binary_redirect')
        components = []
        for candidate in (path, resolved):
            parts = candidate.split('/')[1:]
            for index in range(len(parts) + 1):
                component = '/' + '/'.join(parts[:index])
                st = os.lstat(component)
                if st.st_uid != 0:
                    raise UnsafeBinary('binary_owner')
                if stat.S_ISLNK(st.st_mode):
                    # Symlink permission bits are immaterial; its root-owned parent
                    # and the resolved components are checked independently.
                    canonical = os.path.realpath(component)
                    if canonical not in ('/usr', '/usr/bin', '/usr/sbin', *allowed):
                        raise UnsafeBinary('binary_redirect')
                elif st.st_mode & 0o022:
                    raise UnsafeBinary('binary_writable')
                elif index < len(parts) and not stat.S_ISDIR(st.st_mode):
                    raise UnsafeBinary('binary_component')
                components.append((component, identity(st)))
        st = os.lstat(resolved)
        if not stat.S_ISREG(st.st_mode) or not st.st_mode & 0o111:
            raise UnsafeBinary('binary_not_executable')
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if identity(os.fstat(fd)) != identity(st):
                raise UnsafeBinary('binary_changed')
            magic = os.read(fd, 4)
            if self.platform == 'linux':
                valid = magic == b'\x7fELF'
            else:
                valid = magic in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca')
            if not valid:
                raise UnsafeBinary('binary_format')
            if identity(os.fstat(fd)) != identity(st):
                raise UnsafeBinary('binary_changed')
        finally:
            os.close(fd)
        return resolved, tuple(components)

    def executable(self):
        if self.pinned is None:
            paths = ('/usr/bin/crontab', '/usr/sbin/crontab') if self.platform == 'linux' else ('/usr/bin/crontab',)
            for path in paths:
                try:
                    result = self.inspect(path)
                except FileNotFoundError:
                    continue
                self.pinned = (path, result)
                break
            else:
                raise UnsafeBinary('binary_missing')
        path, expected = self.pinned
        if self.inspect(path) != expected:
            raise UnsafeBinary('binary_changed')
        return expected[0]

    def run(self, argv, stdin=None):
        from .native_scheduler_control import _crontab_run
        return _crontab_run(argv, stdin, native_crontab=self)
