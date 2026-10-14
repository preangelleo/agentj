"""P244 read-only catalogue, with a fake working root and no real credentials or state."""
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock
from p244_fixture import build, env, SECRET, T0
from agentj import workflows as wf

class Catalogue(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='p244-test-'); self.addCleanup(self.tmp.cleanup)
  self.fx=build(self.tmp.name); self.root=self.fx['root']; self.e=env(self.fx); self.e.start(); self.addCleanup(self.e.stop)
  self.sc=wf.Scanner(self.fx['state'])
 def scan(self): return self.sc.scan(self.root,'claude','zh',now=T0)
 def test_three_examples_and_no_credentials(self):
  p=self.scan(); self.assertEqual(p['tail']['counts']['wdb'],3)
  wire=json.dumps(wf.messages(p,'test'),ensure_ascii=False); self.assertNotIn(SECRET,wire)
  self.assertNotIn('CREDENTIALS',wire); self.assertNotIn('escape-link',wire); self.assertNotIn('only-env',wire)
  self.assertFalse(self.sc.doc('video-studio','CREDENTIALS.md')['ok'])
  self.assertFalse(self.sc.doc('video-studio','../notes/readme.md')['ok'])
  self.assertTrue(self.sc.doc('@core','core')['ok']); self.assertTrue(self.sc.doc('','MEMORY.md')['ok'])
 def test_skill_redacted_aliases(self):
  self.scan(); s=self.sc.skill('local:video-studio:storyboard'); self.assertTrue(s['ok']); self.assertNotIn(SECRET,s['text']); self.assertNotIn('description:',s['text'])
  self.assertTrue(self.sc.conflicts); self.assertFalse(self.sc.skill('global::api-token-tool')['ok'])
 def test_colour_stability_and_rename(self):
  p=self.scan(); old={w['folder']:w['ci'] for w in p['items'] if w['wdb']}
  (self.root/'video-studio').rename(self.root/'new-video'); p=self.scan()
  self.assertEqual(next(w['ci'] for w in p['items'] if w['folder']=='new-video'),old['video-studio'])
  self.assertEqual(next(w['ci'] for w in p['items'] if w['folder']=='music-studio'),old['music-studio'])
 def test_cache_edit_and_symlink_replacement(self):
  p=self.scan(); path=self.root/'video-studio/documentation/IDENTITY.md';path.write_text('# IDENTITY — New CEO\n');os.utime(path,ns=(T0*10**9,(T0+1000)*10**9))
  q=self.scan();self.assertNotEqual(p['tail']['etag'],q['tail']['etag']); self.assertEqual(next(w['ceo'] for w in q['items'] if w['folder']=='video-studio'),'New CEO')
  path.unlink();path.symlink_to(pathlib.Path(self.tmp.name)/'outside/documentation/CONSTITUTION.md');self.assertFalse(self.sc.doc('video-studio','IDENTITY.md')['ok'])
 def test_external_documentation_not_certified(self):
  p=self.root/'escape-doc';p.mkdir();(p/'documentation').symlink_to(pathlib.Path(self.tmp.name)/'outside/documentation');q=self.scan();w=next(w for w in q['items'] if w['folder']=='escape-doc');self.assertFalse(w['wdb']);self.assertEqual(w['docs'],[])
 def test_busy_and_chunks(self):
  q=self.sc.scan(self.root,'claude','zh',[{'cwd':str(self.root/'music-studio/out')}],now=T0)
  self.assertEqual(q['items'][0]['folder'],'music-studio');self.assertTrue(q['items'][0]['busy'])
  for m in wf.messages(q,'test'): self.assertLessEqual(len(json.dumps(m,ensure_ascii=False).encode()),wf.CHUNK)
 def test_working_root_change_invalidates_file_cache(self):
  self.scan()
  other=pathlib.Path(self.tmp.name)/'other-root';other.mkdir()
  q=self.sc.scan(other,None,now=T0)
  self.assertEqual(q['items'],[]);self.assertEqual(q['tail']['memory'][0]['files'],[])
  self.assertFalse(self.sc.doc('video-studio','IDENTITY.md')['ok'])
 def test_read_limit_and_metadata_redaction(self):
  p=self.root/'video-studio/documentation/large.md';p.write_text('# '+SECRET+'\n'+'大'*30000);q=self.scan()
  self.assertNotIn(SECRET,json.dumps(q,ensure_ascii=False));d=self.sc.doc('video-studio','large.md');self.assertTrue(d['cut']);self.assertLessEqual(len(d['text'].encode()),wf.DOC_MAX)


class RobotMetadata(unittest.TestCase):
 def test_task_sources_keep_actual_model(self):
  from agentj.busy import Board
  b=Board(None);b.set_context('own',harness='claude',cwd='/tmp/studio')
  b.stream_event({'type':'assistant','message':{'content':[{'type':'tool_use','name':'Agent','id':'one','input':{'description':'test','subagent_type':'writer'}}]}})
  self.assertEqual(b.items()[0]['harness'],'claude');self.assertNotIn('model',b.items()[0]);self.assertEqual(b.items()[0]['cwd'],'/tmp/studio')
  b.stream_event({'type':'assistant','parent_tool_use_id':'one','message':{'model':'fixture-workflow-title','content':[]}})
  self.assertEqual(b.items()[0]['model'],'fixture-workflow-title')
  b.codex_event('thread/started',{'thread':{'id':'child','model':'gpt-6.1-sol','cwd':'/tmp/project','source':{'thread_spawn':{'agent_role':'writer'}}}})
  b.codex_event('turn/started',{'threadId':'child'})
  c=next(x for x in b.items() if x['harness']=='codex');self.assertEqual(c['model'],'gpt-6.1-sol')
 def test_outside_metadata_roundtrip_and_redaction(self):
  from agentj.busy import item,write_item,read_items
  with tempfile.TemporaryDirectory(prefix='p244-busy-') as root:
   write_item(root,item('cli:test','writer','temp',harness='opencode',model='deepseek/deepseek-chat',workflow='studio',role='writer'))
   x=read_items(root)[0];self.assertEqual(x['model'],'deepseek/deepseek-chat');self.assertEqual(x['workflow'],'studio')
 def test_org_assignment_and_unknown(self):
  pic={'root':'/tmp/root','items':[{'folder':'studio','wdb':True,'ceo':'director','company':'Studio','agents':[{'name':'writer'}]}]}
  self.assertEqual(wf.task_assignment({'cwd':'/tmp/root/studio/out','who':'writer'},pic)['parent'],'director')
  self.assertEqual(wf.task_assignment({'who':'director'},pic)['role'],'CEO')
  self.assertEqual(wf.task_assignment({'cwd':'/tmp/root/studio-other','who':'unknown'},pic)['workflow'],'')
