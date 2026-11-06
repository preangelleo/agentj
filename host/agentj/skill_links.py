"""Update bundled skill entries automatically, preserving old copies outside discovery.

History is pinned to public release tags, not names or writable ownership markers.
A receipt only proves a now-dangling link that this installer previously created.
All replacement renames the old entry first; external link destinations stay untouched.
"""
import hashlib
import json
import os
import stat
import shutil
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
        if own or managed(source,target):return 'installed',None
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
        return 'conflict',digest
    except (OSError,ValueError,RuntimeError):return 'conflict',None

LOGIC_SUFFIXES = {'.md', '.py', '.sh', '.js', '.mjs', '.ts'}

def logic_files(source):
    try:
        inventory=json.loads(Path(__file__).with_name('bundled_skills.json').read_text())['logic_files']
        if source.name in inventory:return [Path(rel) for rel in inventory[source.name]]
    except (OSError,ValueError,KeyError):pass
    return sorted(p.relative_to(source) for p in source.rglob('*')
                  if p.is_file() and p.suffix in LOGIC_SUFFIXES)

def points_to(path, source):
    try:return path.is_symlink() and path.resolve()==source.resolve()
    except (OSError,RuntimeError):return False

def managed(source, target):
    """Only exact package logic paths are managed; user configuration is not evidence."""
    try:
        return target.is_dir() and all((target/rel).is_symlink() and
            (target/rel).resolve()==(source/rel).resolve() for rel in logic_files(source))
    except (OSError, RuntimeError):return False

def _seed(source,target):
    """Durable local root: package deletion must never delete future user configuration."""
    target.mkdir(mode=0o700)
    for entry in source.rglob('*'):
        path=target/entry.relative_to(source)
        if entry.is_dir():path.mkdir(parents=True,exist_ok=True)
        elif entry.is_file():
            path.parent.mkdir(parents=True,exist_ok=True)
            if entry.relative_to(source) in set(logic_files(source)):path.symlink_to(entry)
            else:shutil.copy2(entry,path)

def _durable_overlay(source,original,destination):
    """Materialize config/data before replacing an old package-owned root link."""
    destination.mkdir(mode=0o700)
    def copy_dir(old,new):
        for entry in old.iterdir():
            path=new/entry.name
            if entry.is_symlink():
                raw=os.readlink(entry);resolved=Path(os.path.abspath(entry.parent/raw))
                try:inside=resolved.relative_to(original.absolute())
                except ValueError:path.symlink_to(resolved)
                else:path.symlink_to(raw if not Path(raw).is_absolute() else destination/inside)
            elif entry.is_dir():path.mkdir();copy_dir(entry,path)
            elif entry.relative_to(original) not in set(logic_files(source)):shutil.copy2(entry,path)
    copy_dir(original,destination)
    for rel in logic_files(source):
        cursor=destination
        for part in rel.parts[:-1]:
            child=cursor/part
            if child.is_symlink():
                origin=child.resolve();child.unlink();_overlay_empty(origin,child)
            else:child.mkdir(exist_ok=True)
            cursor=child
        path=destination/rel
        if os.path.lexists(path):path.unlink()
        path.symlink_to(source/rel)

def _overlay(source, original, destination):
    """Local view of external trees; never write through the user's root symlink."""
    destination.mkdir(mode=0o700)
    for entry in original.iterdir():
        (destination/entry.name).symlink_to(entry.absolute())
    # Replace only the directories that contain packaged logic, preserving their siblings.
    for rel in logic_files(source):
        cursor=destination
        for part in rel.parts[:-1]:
            child=cursor/part
            if child.is_symlink():
                origin=child.resolve();child.unlink();_overlay_empty(origin, child)
            else:child.mkdir(exist_ok=True)
            cursor=child
        path=destination/rel
        if os.path.lexists(path):path.unlink()
        path.symlink_to(source/rel)

def _overlay_empty(original, destination):
    destination.mkdir(mode=0o700)
    for entry in original.iterdir():
        (destination/entry.name).symlink_to(entry.absolute())

def link(name,source,target,replace=False):
    """Update exact packaged logic paths. Config/data retain their paths and bytes."""
    before,digest=classify(name,source,target)
    receipt=_read(target.parent).get(name,{})
    if not isinstance(receipt,dict):receipt={}
    if before=='installed' and not points_to(target,source):
        row={**receipt,'source':str(source),'digest':fingerprint(source)}
        if row!=receipt:_save(target.parent,name,row)
        return False
    target.parent.mkdir(parents=True,exist_ok=True)
    if before=='installed':before='legacy';digest=fingerprint(target)
    if before=='missing':
        _seed(source,target)
        _save(target.parent,name,{'source':str(source),'digest':fingerprint(source)})
        return True
    backup_root=target.parent.parent/'.agentj-skill-backups'
    if backup_root.is_symlink():raise OSError('backup_link')
    backup_root.mkdir(mode=0o700,exist_ok=True);os.chmod(backup_root,0o700)
    same_logic=all((target/rel).is_file() and (target/rel).read_bytes()==(source/rel).read_bytes() for rel in logic_files(source)) if digest is not None and target.is_dir() else False
    reason='unreadable_or_unverified' if digest is None else 'released_copy' if before=='legacy' or same_logic else 'modified'
    label='你改过的旧版本' if reason!='released_copy' else '内置技能旧版本'
    backup=backup_root/(name+'-'+label+'-'+uuid.uuid4().hex)
    saved_links=[]
    original_link=os.readlink(target) if target.is_symlink() else None
    original=(target.parent/original_link).absolute() if original_link is not None else None
    logic_backup=None
    directory=target.is_dir() and not target.is_symlink()
    if directory:
        # Never scan or move unknown user files. Renaming a chmod-000 logic file needs no read.
        backup.mkdir(mode=0o700)
        for rel in logic_files(source):
            path=target/rel
            # A nested linked directory belongs to the user too: materialize a local view first.
            cursor=target
            for part in rel.parts[:-1]:
                child=cursor/part
                if child.is_symlink():
                    origin=child.resolve();view=child.with_name('.agentj-view-'+uuid.uuid4().hex)
                    _overlay_empty(origin,view)
                    # Directory links must never become ancestors of saved logic paths.
                    links_root=backup.with_name(backup.name+'-directory-links');links_root.mkdir(mode=0o700,exist_ok=True)
                    saved=links_root/uuid.uuid4().hex
                    saved_links.append({'path':str(child),'link':os.readlink(child),'resolved_from':str(origin),'backup':str(saved)})
                    child.rename(saved);view.rename(child)
                else:child.mkdir(exist_ok=True)
                cursor=child
            if points_to(path,source/rel):continue
            saved=backup/rel;saved.parent.mkdir(parents=True,exist_ok=True)
            if os.path.lexists(path):
                if path.is_symlink():saved_links.append({'path':str(path),'link':os.readlink(path),'resolved_from':str((path.parent/os.readlink(path)).absolute()),'backup':str(saved)})
                path.rename(saved)
            try:path.symlink_to(source/rel)
            except OSError:
                if os.path.lexists(saved) and not os.path.lexists(path):saved.rename(path)
                raise
        # Packaged non-logic examples are defaults: seed absent files only, never replace.
        for entry in source.rglob('*'):
            if entry.is_file() and entry.relative_to(source) not in set(logic_files(source)):
                path=target/entry.relative_to(source)
                if not os.path.lexists(path):
                    path.parent.mkdir(parents=True,exist_ok=True)
                    try:
                        with path.open('xb') as stream:stream.write(entry.read_bytes())
                    except FileExistsError:pass
    else:
        view=None
        if target.is_symlink() and target.is_dir():
            view=backup.with_name(backup.name+'-current')
            old_receipt=receipt.get('source')
            if points_to(target,source) or old_receipt==str(original):
                _durable_overlay(source,original,view)
                logic_backup=backup.with_name(backup.name+'-旧逻辑');logic_backup.mkdir(mode=0o700)
                for rel in logic_files(source):
                    old=original/rel;saved=logic_backup/rel;saved.parent.mkdir(parents=True,exist_ok=True)
                    if old.is_file():
                        if old.is_symlink():shutil.copy2(old,saved)
                        else:
                            try:shutil.copy2(old,saved)
                            except PermissionError:os.link(old,saved)
            else:_overlay(source,original,view)
        observed=target.lstat()
        target.rename(backup)
        moved=backup.lstat()
        if (observed.st_dev,observed.st_ino)!=(moved.st_dev,moved.st_ino):
            if not os.path.lexists(target):target.symlink_to(original_link or backup)
            raise OSError('skill_entry_changed')
        try:target.symlink_to(view or source)
        except OSError:
            if not os.path.lexists(target):target.symlink_to(original_link or backup)
            raise
        if original is not None:
            backup.with_name(backup.name+'.resolved').symlink_to(original)
    info={'path':str(target),'reason':reason,'modified':reason!='released_copy','saved_links':saved_links,'logic_backup':str(logic_backup) if logic_backup else ''}
    if original_link is not None:info.update(link=original_link,resolved_from=str(original))
    meta=backup.with_name(backup.name+'.json')
    meta.write_text(json.dumps(info,ensure_ascii=False));os.chmod(meta,0o600)
    _save(target.parent,name,{'source':str(source),'digest':fingerprint(source),'updated':True,
          'backup':str(backup),'modified':reason!='released_copy','reason':reason})
    return True
