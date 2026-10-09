"""Owner-only multi-file snapshots. No visitor-controlled paths or fetches."""
import csv
import io
import os
import stat
from pathlib import Path
import re
from ..provider_profiles import atomic,secure_read
from .. import privacy
from .store import BotError
from .network import request

EXT={'.md','.txt','.pdf','.csv'}
MAX_BYTES=2*1024*1024

def filename(name):
    if not isinstance(name,str) or not re.fullmatch(r'[\w.-]{1,100}',name) or name.startswith('.') or Path(name).suffix.lower() not in EXT:raise BotError('invalid_knowledge_name')
    return name

def extract(name,data):
    if len(data)>MAX_BYTES:raise BotError('knowledge_too_large')
    if Path(name).suffix.lower()=='.pdf':
        # Pure parser, no executable/URL actions or external programs.
        try:
            from pypdf import PdfReader
            reader=PdfReader(io.BytesIO(data))
            if len(reader.pages)>200:raise BotError('pdf_page_limit')
            text='\n'.join(p.extract_text() or '' for p in reader.pages[:200])
        except Exception:raise BotError('pdf_extract_failed') from None
    else:
        try:text=data.decode('utf-8-sig')
        except UnicodeError:raise BotError('knowledge_not_utf8') from None
        if Path(name).suffix.lower()=='.csv':text='\n'.join(' | '.join(row) for row in csv.reader(io.StringIO(text)))
    if len(text)>MAX_BYTES or any(ord(x)<32 and x not in '\n\r\t' for x in text):raise BotError('invalid_knowledge')
    if any(pattern.search(text) for _,pattern,_ in privacy._SECRETS):raise BotError('knowledge_contains_secret')
    return text

class Knowledge:
    def __init__(self,store,bid):self.root=store.directory(bid)/'knowledge'
    def add(self,name,data):
        name=filename(name);text=extract(name,data)
        files=[p for p in self.root.iterdir() if not p.name.endswith('.text')]
        if len(files)>=100 and not (self.root/name).exists():raise BotError('knowledge_file_limit')
        if sum(p.stat().st_size for p in files if not p.is_symlink())+len(data)>20*MAX_BYTES:raise BotError('knowledge_size_limit')
        # Materialized text has a separate suffix; source retained locally for audit/removal.
        atomic(self.root/name,data);atomic(self.root/(name+'.text'),text.encode())
        return {'name':name,'characters':len(text)}
    def remove(self,name):
        name=filename(name)
        for p in [self.root/name,self.root/(name+'.text')]:
            if p.is_symlink():raise BotError('unsafe_knowledge')
            p.unlink(missing_ok=True)
    def sync_directory(self,directory):
        root=Path(directory).expanduser()
        if root.is_symlink() or root.resolve()!=root.absolute() or not root.is_dir():raise BotError('unsafe_knowledge_directory')
        result=[]
        for p in sorted(root.iterdir()):
            if p.suffix.lower() in EXT:
                if p.is_symlink() or not p.is_file() or p.stat().st_size>MAX_BYTES:raise BotError('unsafe_knowledge_file')
                fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise BotError('unsafe_knowledge_file')
                    data=stream.read(MAX_BYTES+1)
                result.append(self.add(p.name,data))
        return result
    def sync_url(self,url,name):
        # Only the signed owner command invokes this. DNS pinning includes each new sync.
        filename(name);return self.add(name,request(url,limit=MAX_BYTES))
    def retrieve(self,query,limit=6000):
        words=set(re.findall(r'[\w]+',query.lower()));scored=[]
        for p in sorted(self.root.glob('*.text')):
            text=secure_read(p,MAX_BYTES).decode()
            for i in range(0,len(text),1000):
                part=text[i:i+1200];score=sum(part.lower().count(w) for w in words if len(w)>1)
                if score:scored.append((score,p.name,i,part))
        selected=[];remaining=limit
        for _,name,_,part in sorted(scored,key=lambda x:(-x[0],x[1],x[2]))[:6]:
            piece=part[:remaining];selected.append({'file':name.removesuffix('.text'),'text':piece});remaining-=len(piece)
            if remaining<=0:break
        return selected
