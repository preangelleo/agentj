import io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from agentj import installer
class OwnerIssuerTests(unittest.TestCase):
 def value(self):return {'code':'AJI-'+'a'*32,'expires_at':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),'quota_usd':10,'untrusted_extra':'ignored'}
 def test_private_output_and_retries_same_owner_capability(self):
  requests=[]
  def opener(req,timeout):requests.append(req);return io.BytesIO(json.dumps(self.value()).encode())
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'INSTALL_ASSISTANT_OWNER_ISSUER_TOKEN':'fixture-owner-issuer'}):
   out=Path(d)/'code.json';installer.issue_owner(out,opener=opener)
   self.assertEqual(out.stat().st_mode & 0o777,0o600);self.assertEqual(set(json.loads(out.read_text())),{'code','expires_at','quota_usd','command'})
   request_id=json.loads(requests[0].data)['entitlement_id'];out.unlink();installer.issue_owner(out,opener=opener)
   self.assertEqual(request_id,json.loads(requests[1].data)['entitlement_id']);self.assertEqual(requests[0].get_header('Authorization'),'Bearer fixture-owner-issuer')
 def test_limits_and_parent_permissions_fail_closed(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'INSTALL_ASSISTANT_OWNER_ISSUER_TOKEN':'fixture'}):
   out=Path(d)/'code';v=self.value();v['expires_at']=(datetime.now(timezone.utc)+timedelta(hours=4)).isoformat()
   with self.assertRaises(ValueError):installer.issue_owner(out,opener=lambda req,timeout:io.BytesIO(json.dumps(v).encode()))
   self.assertFalse(out.exists());Path(d).chmod(0o755)
   with self.assertRaises(ValueError):installer.issue_owner(out)
 def test_missing_issuer_never_requests_network(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{},clear=True):
   with self.assertRaises(ValueError):installer.issue_owner(Path(d)/'code',opener=lambda *a,**k:self.fail('network'))
class OwnerTokenNameTests(unittest.TestCase):
 def test_both_names_env_then_file(self):
  for name in installer.OWNER_TOKEN_NAMES:
   self.assertEqual(installer.owner_token({name:' fixture-owner '}),'fixture-owner')
  self.assertEqual(installer.owner_token({'AGENTJ_INSTALLER_OWNER_ISSUER_TOKEN':'canonical','INSTALL_ASSISTANT_OWNER_ISSUER_TOKEN':'legacy'}),'canonical')
  self.assertEqual(installer.owner_token({'INSTALL_ASSISTANT_OWNER_ISSUER_TOKEN':''},{'AGENTJ_INSTALLER_OWNER_ISSUER_TOKEN':'from-file'}.get),'from-file')
  self.assertEqual(installer.owner_token({},{'INSTALL_ASSISTANT_OWNER_ISSUER_TOKEN':'legacy-file'}.get),'legacy-file')
  self.assertEqual(installer.owner_token({'INSTALL_ASSISTANT_OWNER_ISSUER_TOKEN':'env'},{'AGENTJ_INSTALLER_OWNER_ISSUER_TOKEN':'file'}.get),'env')
  self.assertIsNone(installer.owner_token({},{}.get))
 def test_issue_owner_reads_shared_store_name(self):
  requests=[]
  def opener(req,timeout):requests.append(req);return io.BytesIO(json.dumps(OwnerIssuerTests.value(None)).encode())
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'AGENTJ_INSTALLER_OWNER_ISSUER_TOKEN':'fixture-shared-name'},clear=True):
   Path(d).chmod(0o700);installer.issue_owner(Path(d)/'code.json',opener=opener)
  self.assertEqual(requests[0].get_header('Authorization'),'Bearer fixture-shared-name')
