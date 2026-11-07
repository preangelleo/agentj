"""Explicit source surface inventory: visible in a shot is weaker than a dedicated scene."""
from html.parser import HTMLParser
from pathlib import Path
import json,sys
class Inventory(HTMLParser):
 def __init__(self):super().__init__();self.items=[];self.stack=[];self.ancestors={}
 def handle_starttag(self,tag,attrs):
  a=dict(attrs);ident=a.get('id');classes=a.get('class','').split()
  if ident:self.ancestors[ident]=[i for t,i in self.stack if i]
  if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:self.stack.append((tag,ident))
  if ident and (a.get('role')=='dialog' or 'view' in classes or ident.startswith('set-') and (tag=='section' or 'setsub' in classes)):
   self.items.append({'id':ident,'tag':tag,'role':a.get('role'),'classes':classes})
 def handle_endtag(self,tag):
  for i in range(len(self.stack)-1,-1,-1):
   if self.stack[i][0]==tag:self.stack=self.stack[:i];break
source=Path(sys.argv[1]);out=Path(sys.argv[2]);p=Inventory();p.feed((source/'index.html').read_text());result=json.loads((out/'result.json').read_text());visible={x for r in result['rows'] if r.get('ok') for x in r.get('visible',[])}
targets={r.get('target','').removeprefix('#') for r in result['rows'] if r.get('ok') and r.get('target')}
# A real foreground child target proves this containing surface, never sibling pages or owners.
for ident in list(targets):targets.update(p.ancestors.get(ident,[]))
for i in p.items:i['observedVisible']=i['id'] in visible;i['dedicatedSceneRequired']=True;i['dedicatedSceneCaptured']=i['id'] in targets;i['coverage']='covered-scene' if i['id'] in targets else 'uncovered'
report={'source':str(source),'surfaceCount':len(p.items),'observedCount':len([i for i in p.items if i['observedVisible']]),'surfaces':p.items,'note':'Observed does not prove topmost scene, populated detail/empty/error states or all interaction states. Unobserved IDs remain explicit gaps. Dynamic JS-created overlays require registry completion.'}
(out/'coverage.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print({k:report[k] for k in ['surfaceCount','observedCount']})
