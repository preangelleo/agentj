"""Recognize exact released skill trees; preserve edits and unknown origins.

History is pinned to public release tags, not names or writable ownership markers.
A receipt only proves a now-dangling link that this installer previously created.
All replacement renames the old entry first; external link destinations stay untouched.
"""
import errno
import hashlib
import json
import os
import stat
import uuid
from functools import lru_cache
from pathlib import Path

RECEIPT = '.agentj-skill-links.json'

@lru_cache(maxsize=1)
def history():
    try:
        return json.loads(Path(__file__).with_name('skill_history.json').read_text())['skills']
    except (OSError, ValueError, KeyError):
        return {}

def fingerprint(root, choice=False):
    """Bounded, complete relative file inventory. Never follow nested links."""
    rows=[]
    total=entries=0
    def walk(path, prefix='', depth=0):
        nonlocal total,entries
        if depth>32:raise ValueError('depth')
        for entry in sorted(path.iterdir(), key=lambda p:p.name):
            entries+=1
            if entries>512:raise ValueError('count')
            rel=prefix+entry.name
            mode=entry.lstat().st_mode
            if stat.S_ISLNK(mode):
                if not choice:raise ValueError('nested_link')
                rows.append([rel,'link:'+os.readlink(entry)])
            elif stat.S_ISDIR(mode):
                # Empty extra directories are modifications too.
                if not any(entry.iterdir()): rows.append([rel+'/', 'directory'])
                walk(entry,rel+'/',depth+1)
            elif stat.S_ISREG(mode):
                size=entry.stat().st_size
                total+=size
                if size>2*1024*1024 or total>8*1024*1024: raise ValueError('size')
                with entry.open('rb') as stream:
                    data=stream.read(2*1024*1024+1)
                if len(data)!=size: raise ValueError('changed')
                rows.append([rel,hashlib.sha256(data).hexdigest()])
            else: raise ValueError('special_file')
            if len(rows)>512: raise ValueError('count')
    try:
        walk(root)
        rows.sort()
        return hashlib.sha256(json.dumps(rows,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
    except (OSError,ValueError,RuntimeError):
        return None

def choice_fingerprint(root):
    """Acknowledgement only: link text is data, never evidence of package ownership."""
    try:
        mode=root.lstat().st_mode
        if stat.S_ISLNK(mode):
            raw=os.readlink(root)
            try:resolved=root.resolve(strict=True)
            except FileNotFoundError:parts=['dangling',raw]
            except RuntimeError:parts=['loop',raw]
            except OSError as exc:
                if exc.errno!=errno.ELOOP:raise
                parts=['loop',raw]
            else:
                digest=choice_fingerprint(resolved)
                if digest is None:return None
                parts=['link',raw,digest]
        elif stat.S_ISREG(mode):
            if root.stat().st_size>2*1024*1024:return None
            with root.open('rb') as stream:data=stream.read(2*1024*1024+1)
            if len(data)>2*1024*1024:return None
            parts=['file',hashlib.sha256(data).hexdigest()]
        else:return fingerprint(root,choice=True)
        return hashlib.sha256(json.dumps(parts,separators=(',',':')).encode()).hexdigest()
    except (OSError,ValueError,RuntimeError):return None

def _read(parent):
    try:
        path=parent/RECEIPT
        if path.is_symlink() or path.stat().st_size>256*1024:return {}
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError,RuntimeError):return {}

def _save(parent,name,value):
    rows=_read(parent);rows[name]=value
    tmp=parent/(RECEIPT+'.'+uuid.uuid4().hex)
    try:
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream:json.dump(rows,stream,separators=(',',':'))
        os.replace(tmp,parent/RECEIPT)
    finally:
        if tmp.exists():tmp.unlink()

def classify(name,source,target):
    try:
        try:own=target.is_symlink() and target.resolve()==source.resolve()
        except (OSError,RuntimeError):own=False
        if own:return 'installed',None
        if not os.path.lexists(target):return 'missing',None
        digest=fingerprint(target)
        current=fingerprint(source)
        known=digest is not None and (digest==current or digest in history().get(name,{}))
        if known:return 'legacy',digest
        receipt=_read(target.parent).get(name,{})
        if not isinstance(receipt,dict):receipt={}
        try:dangling=target.is_symlink() and not target.exists()
        except (OSError,RuntimeError):dangling=False
        if dangling:
            link=os.readlink(target)
            old=receipt.get('digest')
            if receipt.get('source')==link and old and (old==current or old in history().get(name,{})):
                return 'legacy',old
        choice=choice_fingerprint(target)
        if choice and receipt.get('keep')==choice:return 'kept',digest
        return 'conflict',digest
    except (OSError,ValueError,RuntimeError):return 'conflict',None

def link(name,source,target,replace=False):
    """One entry only; keep original under a unique sibling name on replacement."""
    before,digest=classify(name,source,target)
    if before=='installed':
        receipt={'source':str(source),'digest':fingerprint(source)}
        if _read(target.parent).get(name)!=receipt:_save(target.parent,name,receipt)
        return False
    if before not in ('missing','legacy') and not replace:return False
    target.parent.mkdir(parents=True,exist_ok=True)
    if before=='missing':
        target.symlink_to(source)
        _save(target.parent,name,{'source':str(source),'digest':fingerprint(source)})
        return True
    backup=None
    old_link=None
    def restore():
        if backup is None:return
        # Exclusive creation: never replace a concurrently created owner entry.
        try:target.symlink_to(old_link if old_link is not None else backup)
        except FileExistsError:pass
    try:
        if os.path.lexists(target):
            backup_root=target.parent.parent/'.agentj-skill-backups'
            if backup_root.is_symlink():raise OSError('backup_link')
            backup_root.mkdir(mode=0o700,exist_ok=True)
            os.chmod(backup_root,0o700)
            backup=backup_root/(name+'-'+uuid.uuid4().hex)
            observed=target.lstat()
            identity=(observed.st_dev,observed.st_ino,stat.S_IFMT(observed.st_mode))
            old_link=os.readlink(target) if stat.S_ISLNK(observed.st_mode) else None
            original=(target.parent/old_link).absolute() if old_link is not None else None
            was_dangling=old_link is not None and not target.exists()
            target.rename(backup)
            moved=backup.lstat()
            if identity!=(moved.st_dev,moved.st_ino,stat.S_IFMT(moved.st_mode)):
                old_link=os.readlink(backup) if stat.S_ISLNK(moved.st_mode) else None
                restore()
                raise OSError('skill_entry_changed')
            if old_link is not None:
                # Retain raw link bytes for restore and its original resolution base for review.
                meta=backup.with_name(backup.name+'.json')
                meta.write_text(json.dumps({'path':str(target),'link':old_link,'resolved_from':str(original)}))
                os.chmod(meta,0o600)
                backup.with_name(backup.name+'.resolved').symlink_to(original)
            if before=='legacy' and not replace:
                after=fingerprint(original if original is not None else backup)
                known_dangling=was_dangling and old_link is not None and os.readlink(backup)==old_link
                if after!=digest and not known_dangling:
                    restore()
                    raise OSError('skill_changed')
        try:target.symlink_to(source)
        except OSError:
            restore()
            raise
        _save(target.parent,name,{'source':str(source),'digest':fingerprint(source)})
        return True
    except OSError:
        restore()
        raise

def keep(name,source,target):
    kind,_=classify(name,source,target)
    digest=choice_fingerprint(target)
    if kind not in ('conflict','kept') or not digest:return False
    _save(target.parent,name,{'keep':digest})
    return True
