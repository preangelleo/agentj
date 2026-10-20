"""Bot commercial policy: validated owner configuration, never credentials or authority."""
import copy
import re
from urllib.parse import urlsplit
from .. import privacy
from .store import BotError

LIMIT_MAX={'visitor_messages':10000,'bot_messages':100000,'host_messages':100000,'host_tokens':50000000,'concurrency':16,'max_chars':4000,'per_minute':600}
DEFAULTS={
 'privacy':{'retention_days':30},
 'memory':{'history_hours':24,'max_turns':40,'max_chars':28000},
 'outbound':{'block_commands':True,'link_domains':[]},
 'knowledge':{'cite':True,'max_chars':6000,'sources':[]},
 'safety':{'provider':'openrouter','criteria':'customer_service'},
 'service':{'after_hours':'refuse'},
 'messages':{},
 'handoff':{'reply_window_h':48,'email_enabled':False},
 'widget':{'position':'br','accent':'#629887','welcome':'','quick_replies':[],'open_on_load':False,'queue_merge':True},
}
PHRASES={
 'unavailable':'Please contact the owner or try again later. / 请联系主人或稍后再试。',
 'outside_hours':'Outside business hours. Please leave a message for the owner. / 非营业时间，请给主人留言。',
 'limit':'Message limit reached. Please try later. / 已达到消息限额，请稍后再试。',
 'offtopic':'Please contact the owner or try again later. / 请联系主人或稍后再试。',
 'handoff_sent':'A human has been notified. / 已通知人工。',
 'welcome':'Hello! How can I help? / 你好，有什么可以帮你？',
}

def plain(value,maximum):
 if not isinstance(value,str) or len(value)>maximum or any(ord(c)<32 and c not in '\n\t' for c in value):raise BotError('invalid_config')
 if any(p.search(value) for _,p,_ in privacy._SECRETS):raise BotError('config_contains_secret')
 return value

def sections(out):
 for name,defaults in DEFAULTS.items():
  raw=out.get(name,{})
  if not isinstance(raw,dict) or set(raw)-set(defaults if name!='messages' else PHRASES):raise BotError('invalid_'+name)
  out[name]={**copy.deepcopy(defaults),**raw}
 for name,key,lo,hi in [('handoff','reply_window_h',1,720),('privacy','retention_days',1,3650),('memory','history_hours',1,8760),('memory','max_turns',1,1000),('memory','max_chars',28000,1000000),('knowledge','max_chars',1,100000)]:
  v=out[name][key]
  if type(v) is not int or not lo<=v<=hi:raise BotError('invalid_'+name)
 if type(out['handoff']['email_enabled']) is not bool:raise BotError('invalid_handoff')
 if type(out['knowledge']['cite']) is not bool or type(out['outbound']['block_commands']) is not bool:raise BotError('invalid_config')
 if out['safety']['provider'] not in ('openrouter','typesafe') or out['safety']['criteria'] not in ('customer_service','strict'):raise BotError('invalid_safety')
 if out['service']['after_hours'] not in ('refuse','ai_only','leave_message'):raise BotError('invalid_service')
 sources=out['knowledge']['sources']
 if not isinstance(sources,list) or len(sources)>20:raise BotError('invalid_knowledge')
 names=set()
 for source in sources:
  if not isinstance(source,dict) or set(source)!={'url','name','cite_url','resync'} or source['resync'] not in ('none','daily','weekly'):raise BotError('invalid_knowledge')
  from .knowledge import filename
  filename(source['name'])
  if source['name'] in names:raise BotError('invalid_knowledge')
  names.add(source['name'])
  for link in (source['url'],source['cite_url']):
   plain(link,1000)
   try:u=urlsplit(link)
   except ValueError:raise BotError('unsafe_url') from None
   if u.scheme!='https' or not u.hostname or u.username or u.password or any(c.isspace() for c in link):raise BotError('unsafe_url')
 domains=out['outbound']['link_domains']
 if not isinstance(domains,list) or len(domains)>20 or any(not isinstance(d,str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?',d) or '.' not in d for d in domains):raise BotError('invalid_domains')
 for v in out['messages'].values():
  if isinstance(v,dict):
   if not v or set(v)-{'en','zh'}:raise BotError('invalid_messages')
   for s in v.values():plain(s,1000)
  else:plain(v,1000)
 w=out['widget']
 if w['position'] not in ('br','bl') or w['accent'] not in ('#629887','#141414','#827dbd','#d97757'):raise BotError('invalid_widget')
 if type(w['open_on_load']) is not bool or type(w['queue_merge']) is not bool:raise BotError('invalid_widget')
 plain(w['welcome'],300)
 if not isinstance(w['quick_replies'],list) or len(w['quick_replies'])>6:raise BotError('invalid_widget')
 for q in w['quick_replies']:plain(q,200)
 p=out['provider'];chain=p.get('chain',[])
 if not isinstance(chain,list) or len(chain)>2:raise BotError('invalid_provider')
 from .provider import VENDORS
 for entry in chain:
  if not isinstance(entry,dict) or set(entry)!={'vendor','model'} or not isinstance(entry['vendor'],str) or entry['vendor'] not in VENDORS or not isinstance(entry['model'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}',entry['model']):raise BotError('invalid_provider')
  plain(entry['model'],120)
 links=p.get('docs_links',[])
 if not isinstance(links,list) or len(links)>6:raise BotError('invalid_provider')
 for link in links:
  plain(link,1000)
  try:u=urlsplit(link)
  except ValueError:raise BotError('invalid_provider') from None
  if u.scheme!='https' or not u.hostname or u.username or u.password or any(c.isspace() for c in link):raise BotError('invalid_provider')
 if p.get('thinking','off') not in ('off','auto'):raise BotError('invalid_provider')
 return out

def phrase(c,name):
 v=c['messages'].get(name,PHRASES[name])
 if isinstance(v,dict):return v.get(c['language'],v.get('en',next(iter(v.values()))))
 return v

def inbound(text):
 # Mandatory secret stripping precedes storage, context and all model/safety calls.
 for _,pattern,_ in privacy._SECRETS:text=pattern.sub('[redacted]',text)
 text=re.sub(r'(?<!\d)(?:\d[ -]?){13,19}(?!\d)','[redacted]',text)
 return text

def problems(text,c):
 if inbound(text)!=text:return ['secret_shape']
 if not c['outbound']['block_commands']:return []
 t=re.sub(r'\n\s*\|',' |',text.replace('\\\n',' '))
 # P400 destructive command, download-and-execute and shell-rc secret filters.
 patterns=[r'\bsudo\s+rm\b|\bmkfs\b|\bdd\s+if=.*\bof=/dev/',
  r'\brm\s+(?:-[a-zA-Z]+\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(?:-[a-zA-Z]+\s+)*(?:/|/\*|~/?\*?|\$HOME/?\*?|"\$HOME"/?\*?)(?=\s|$|;|&)',
  r'\b(?:curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n]*(?:\|\s*(?:sudo\s+)?(?:bash|sh|zsh|dash|ksh|fish|python[0-9.]*|node|perl|ruby|iex|Invoke-Expression)\b|&&\s*(?:bash|sh|zsh)\b)',
  r'(?:<\(|\$\(|`)[^\n]*\b(?:curl|wget|iwr|irm)\b',
  r'\biex\s*\([^\n]*\b(?:curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b',
  r'(?:>>?|tee\s+-a)\s*[^\n]*\.(?:bashrc|zshrc|profile|bash_profile|zprofile)\b[^\n]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=',
  r'(?:KEY|TOKEN|SECRET|PASSWORD)\s*=[^\n]*(?:>>?|tee\s+-a)[^\n]*\.(?:bashrc|zshrc|profile|bash_profile|zprofile)\b']
 return ['unsafe_command'] if any(re.search(p,t,re.I) for p in patterns) else []
