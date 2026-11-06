"""Official wheel build: standard dense DEFLATE without changing installed bytes or ZIP semantics."""
from copy import copy
import os
from pathlib import Path
import stat
import tempfile
import zipfile
import zlib


from hatchling.builders.hooks.plugin.interface import BuildHookInterface


_METADATA = ('filename', 'date_time', 'compress_type', 'comment', 'extra',
             'create_system', 'create_version', 'extract_version', 'reserved',
             'flag_bits', 'volume', 'internal_attr', 'external_attr', 'CRC', 'file_size')


def _verify_equivalent(original, rewritten):
    with zipfile.ZipFile(original) as before, zipfile.ZipFile(rewritten) as after:
        old, new = before.infolist(), after.infolist()
        if before.comment != after.comment or len(old) != len(new):
            raise ValueError('wheel archive metadata changed')
        for left, right in zip(old, new):
            if before.read(left) != after.read(right):
                raise ValueError('wheel member content changed')
            if any(getattr(left, k) != getattr(right, k) for k in _METADATA):
                raise ValueError('wheel member metadata changed')


def _dense_compress(data):
    from zopfli.zlib import compress
    return compress(data, numiterations=5)[2:-4]


class _DenseDeflater:
    """Per-entry compressor; no process-wide zipfile changes and no alternate ZIP method."""
    def __init__(self):
        self.parts = []

    def compress(self, data):
        self.parts.append(data)
        return b''

    def flush(self):
        data = b''.join(self.parts)
        self.parts.clear()
        standard = zlib.compressobj(level=9, wbits=-15)
        ordinary = standard.compress(data) + standard.flush()
        dense = _dense_compress(data)
        if zlib.decompress(dense, -15) != data:
            raise ValueError('dense DEFLATE content changed')
        return dense if len(dense) < len(ordinary) else ordinary


class _DenseZipFile(zipfile.ZipFile):
    def _open_to_write(self, zinfo, force_zip64=False):
        writer = super()._open_to_write(zinfo, force_zip64=force_zip64)
        if zinfo.compress_type == zipfile.ZIP_DEFLATED and zinfo.filename.endswith(
                ('.py', '.md', '.json', '.json5', '.js', '.html', '.css', '.txt')):
            # zipfile still owns headers, flags, CRC, size accounting and central directory.
            writer._compressor = _DenseDeflater()
        return writer


class CustomBuildHook(BuildHookInterface):
    def finalize(self, version, build_data, artifact_path):
        artifact = Path(artifact_path)
        mode = artifact.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError('wheel artifact is not a regular file')
        fd, name = tempfile.mkstemp(prefix=artifact.name + '.compression-', dir=artifact.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            with zipfile.ZipFile(artifact) as before, _DenseZipFile(temporary, 'w') as after:
                entries = before.infolist()
                if len({i.filename for i in entries}) != len(entries):
                    raise ValueError('duplicate wheel member')
                after.comment = before.comment
                for info in entries:
                    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                        raise ValueError('unsupported wheel compression method')
                    after.writestr(copy(info), before.read(info), compress_type=info.compress_type, compresslevel=9)
            _verify_equivalent(artifact, temporary)
            os.chmod(temporary, stat.S_IMODE(mode))
            os.replace(temporary, artifact)
        finally:
            temporary.unlink(missing_ok=True)
