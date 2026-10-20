"""Owner-only multi-file snapshots. No visitor-controlled paths or fetches."""
import json
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
    def __init__(self,store,bid):self.store=store;self.bid=bid;self.root=store.directory(bid)/'knowledge'
    def add(self,name,data,cite_url=None):
        if cite_url is not None:
            from .policy import plain
            from urllib.parse import urlsplit
            plain(cite_url,1000)
            try:u=urlsplit(cite_url)
            except ValueError:raise BotError('unsafe_url') from None
            if u.scheme!='https' or not u.hostname or u.username or u.password or any(c.isspace() for c in cite_url):raise BotError('unsafe_url')
        name=filename(name);text=extract(name,data)
        files=[p for p in self.root.iterdir() if not p.name.endswith(('.text','.source.json','.sync.json'))]
        if len(files)>=100 and not (self.root/name).exists():raise BotError('knowledge_file_limit')
        if sum(p.stat().st_size for p in files if not p.is_symlink())+len(data)>20*MAX_BYTES:raise BotError('knowledge_size_limit')
        # Materialized text has a separate suffix; source retained locally for audit/removal.
        atomic(self.root/name,data);atomic(self.root/(name+'.text'),text.encode())
        if cite_url is not None:atomic(self.root/(name+'.source.json'),json.dumps({'url':cite_url}).encode())
        else:(self.root/(name+'.source.json')).unlink(missing_ok=True)
        return {'name':name,'characters':len(text)}
    def remove(self,name):
        name=filename(name)
        for p in [self.root/name,self.root/(name+'.text'),self.root/(name+'.source.json'),self.root/(name+'.sync.json')]:
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
        filename(name)
        result=self.add(name,request(url,limit=MAX_BYTES),cite_url=url)
        return result
    async def resync(self,fetch=request):
        """Only signed configuration supplies sources; each fetch retains DNS pinning and no redirects."""
        import asyncio
        for source in self.store.get(self.bid)['knowledge']['sources']:
            interval={'none':None,'daily':86400,'weekly':604800}[source['resync']]
            if interval is None:continue
            receipt=self.root/(source['name']+'.sync.json')
            try:old=json.loads(secure_read(receipt,4096))
            except (OSError,ValueError):old={}
            if old.get('source')==source and self.store.now()-old.get('attempt',0)<interval:continue
            # Reserve a bounded attempt before network; failed refresh retains the previous snapshot.
            atomic(receipt,json.dumps({'source':source,'attempt':self.store.now()}).encode())
            try:
                data=await asyncio.to_thread(fetch,source['url'],limit=MAX_BYTES)
                if source not in self.store.get(self.bid)['knowledge']['sources']:continue
                self.add(source['name'],data,source['cite_url'])
            except (BotError,OSError,ValueError):self.store.event(self.bid,'','knowledge_sync','unavailable')
    def retrieve(self,query,limit=None):
        limit=self.store.get(self.bid)['knowledge']['max_chars'] if limit is None else limit
        words=set(re.findall(r'[\w]+',query.lower()));scored=[]
        for p in sorted(self.root.glob('*.text')):
            text=secure_read(p,MAX_BYTES).decode()
            for i in range(0,len(text),1000):
                part=text[i:i+1200];score=sum(part.lower().count(w) for w in words if len(w)>1)
                if score:scored.append((score,p.name,i,part))
        selected=[];remaining=limit
        for _,name,_,part in sorted(scored,key=lambda x:(-x[0],x[1],x[2]))[:6]:
            piece=part[:remaining];item={'file':name.removesuffix('.text'),'text':piece}
            source=self.root/(name.removesuffix('.text')+'.source.json')
            if source.exists():item['url']=json.loads(secure_read(source,4096))['url']
            selected.append(item);remaining-=len(piece)
            if remaining<=0:break
        return selected
