"""Official wheel build: DEFLATE9 without changing installed bytes or ZIP semantics."""
from copy import copy
import os
from pathlib import Path
import stat
import tempfile
import zipfile

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
            with zipfile.ZipFile(artifact) as before, zipfile.ZipFile(temporary, 'w') as after:
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
