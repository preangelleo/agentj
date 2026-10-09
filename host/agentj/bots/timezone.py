"""Read an IANA host zone; never silently guess a browser's zone."""
from pathlib import Path
import os
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

def host_timezone():
    candidates=[os.environ.get('TZ','')]
    try:candidates.append(Path('/etc/timezone').read_text().strip())
    except OSError:pass
    try:candidates.append(str(Path('/etc/localtime').resolve()).split('/zoneinfo/',1)[1])
    except (OSError,IndexError):pass
    for c in candidates:
        try:ZoneInfo(c);return c
        except (ValueError,ZoneInfoNotFoundError):pass
    return 'UTC'
