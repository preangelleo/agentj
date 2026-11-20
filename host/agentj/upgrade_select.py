"""Run the standalone website repair selector in the interpreter whose wheels we need.

repair_artifacts is a byte-for-byte mirror of support/installation/installer.py.
Its parity test also uses the copy exported in the public repository.
"""
import json
import re
import sys
import tomllib
from pathlib import Path

REPAIR_CDN_URL = re.compile(r'https://files\.pythonhosted\.org/packages/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}/([A-Za-z0-9_+.\-]+\.whl)')


class InstallError(ValueError):
    pass


def repair_artifacts(lock, tag_order, marker, parse_name):
    """Offer only the target interpreter's locked runtime closure to uv's resolver."""
    packages = {p['name']: p for p in lock['package']
                if not p.get('resolution-markers') or any(marker(m).evaluate() for m in p['resolution-markers'])}
    pending = ['agentj']
    seen = set()
    selected = {}
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        row = packages.get(name)
        if not row:
            raise InstallError('repair_dependency_lock_incomplete')
        dependencies = list(row.get('dependencies', []))
        for optional in row.get('optional-dependencies', {}).values():
            dependencies.extend(optional)
        pending.extend(dep['name'] for dep in dependencies if not dep.get('marker') or marker(dep['marker']).evaluate())
        if name == 'agentj':
            continue
        matches = []
        for item in row.get('wheels', []):
            url_match = REPAIR_CDN_URL.fullmatch(item['url'])
            if not url_match or not re.fullmatch(r'sha256:[0-9a-f]{64}', item['hash']):
                raise InstallError('repair_dependency_lock_invalid')
            filename = url_match[1]
            dist, version, _, tags = parse_name(filename)
            if dist != name or str(version) != row['version']:
                raise InstallError('repair_dependency_lock_invalid')
            ranks = [tag_order[t] for t in tags if t in tag_order]
            if ranks:
                matches.append((min(ranks), filename, item))
        if not matches:
            raise InstallError('repair_dependency_platform_unsupported:' + name)
        _, filename, item = min(matches, key=lambda matched: matched[:2])
        selected[filename] = (item['url'], item['hash'][7:], item['size'])
    return selected



if __name__ == '__main__':
    # Invoked with -IS: only this hash-checked wheel provides packaging.
    helper, lock = map(Path, sys.argv[1:])
    sys.path.insert(0, str(helper))
    from packaging.markers import Marker
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename
    try:
        selected = repair_artifacts(tomllib.loads(lock.read_text()), {t: i for i, t in enumerate(sys_tags())},
                                    Marker, parse_wheel_filename)
    except InstallError as error:
        sys.exit(3 if str(error).startswith('repair_dependency_platform_unsupported:') else 2)
    print(json.dumps(sorted(selected)))
