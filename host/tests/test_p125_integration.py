"""P125 cross-branch contracts: one truthful capability registry and first-run browser evidence."""
import _hermetic  # noqa: F401,I001
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch
from agentj import browser, browser_sites as sites, capability, first_run, google, ltschema
from agentj.state import State


class Capabilities(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p125-contract-', dir='/var/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / 'state'); self.st.init()
        self.now = int(time.time())
        self.runtime = patch.object(browser, 'runtime', return_value={'pid': 123, 'port': 456})
        self.runtime.start(); self.addCleanup(self.runtime.stop)
        p = patch.object(capability, 'discover', return_value=[])
        p.start(); self.addCleanup(p.stop)
        sites.add(self.st, 'gmail')
        d = sites.load(self.st)
        d['sites']['gmail'].update(status='logged_in', checked_at=self.now, reason='signed_in')
        sites._save(self.st, d)

    def sync(self, **kw):
        return capability.sync(self.st, None, now=kw.pop('now', self.now), probe=kw.pop('probe', False), **kw)

    def test_closed_schema_and_idempotent_business_projection(self):
        a = self.sync(); b = self.sync(now=self.now + 1)
        self.assertEqual(ltschema.problems('registry', a), [])
        self.assertEqual(a['revision'], b['revision'])
        entries = {e['id']: e for e in a['entries']}
        self.assertEqual(entries['browser-gmail']['status'], 'ready')
        purposes = [e for e in a['entries'] if e['kind'] == 'credential']
        self.assertTrue(purposes)
        self.assertTrue(all(e['status'] != 'ready' and e['check']['status'] == 'unsupported' for e in purposes))

    def test_stale_disabled_revoked_and_removed_are_never_ready(self):
        self.sync()
        stale = {e['id']: e for e in self.sync(now=self.now + sites.FRESH + 1)['entries']}
        self.assertEqual(stale['browser-gmail']['status'], 'stale')
        with patch.object(browser, 'settings', return_value={'enabled': False}):
            disabled = self.sync()
            self.assertEqual(ltschema.problems('registry', disabled), [])
            self.assertTrue(all(e['status'] == 'unavailable' for e in disabled['entries'] if e['kind'] == 'browser'))
        with patch.object(browser, 'runtime', return_value=None):
            offline = {e['id']: e for e in self.sync()['entries']}
            self.assertEqual(offline['browser-gmail']['status'], 'unavailable')
        sites.remove(self.st, 'gmail')
        gone = {e['id']: e for e in self.sync()['entries']}
        self.assertEqual(gone['browser-gmail']['status'], 'missing')

    def test_google_expiry_is_bound_to_check_not_inventory_reads(self):
        s = google.status(self.st)
        s['tool'].update(state='installed', fresh=True, checked_at=capability.iso(self.now))
        with patch.object(google, 'status', return_value=s):
            a = self.sync(); b = self.sync(now=self.now + 10)
            self.assertEqual(a['revision'], b['revision'])
            row = next(e for e in a['entries'] if e['id'] == 'google-cli')
            self.assertEqual(capability.parse_iso(row['check']['expires_at']), self.now + google.CLI_TTL)
            expired = next(e for e in self.sync(now=self.now + google.CLI_TTL + 1)['entries'] if e['id'] == 'google-cli')
            self.assertEqual(expired['status'], 'stale')

    def test_first_run_uses_site_evidence_without_starting_checks_or_downloads(self):
        with patch.object(sites, 'check', side_effect=AssertionError('must not log in or check a site')), \
             patch.object(browser, 'setup', side_effect=AssertionError('must not download')):
            self.assertEqual(first_run.probe(self.st, 'gmail', {'system': 'linux'}), ('verified', None))
            self.assertEqual(first_run.probe(self.st, 'youtube', {'system': 'linux'}), ('waiting_local', 'site_not_added'))
            d = sites.load(self.st)
            d['sites']['gmail']['checked_at'] = self.now - sites.FRESH - 5
            sites._save(self.st, d)
            self.assertEqual(first_run.probe(self.st, 'gmail', {'system': 'linux'}), ('attention', 'site_check_stale'))
            d['sites']['gmail'].update(status='auth_required', checked_at=self.now)
            sites._save(self.st, d)
            self.assertEqual(first_run.probe(self.st, 'gmail', {'system': 'linux'}), ('waiting_local', 'site_signin_required'))
        with patch.object(browser, 'runtime', return_value=None):
            self.assertEqual(first_run.probe(self.st, 'browser', {'system': 'linux'}), ('attention', 'browser_not_running'))

    def test_wechat_requires_both_independent_sites(self):
        sites.add(self.st,'mp-weixin')
        d=sites.load(self.st);d['sites']['mp-weixin'].update(status='logged_in',checked_at=self.now)
        sites._save(self.st,d)
        self.assertEqual(first_run.probe(self.st,'wechat',{'system':'linux'}),('waiting_local','site_not_added'))
        sites.add(self.st,'channels-weixin')
        d=sites.load(self.st);d['sites']['channels-weixin'].update(status='auth_required',checked_at=self.now)
        sites._save(self.st,d)
        self.assertEqual(first_run.probe(self.st,'wechat',{'system':'linux'}),('waiting_local','site_signin_required'))
        d['sites']['channels-weixin'].update(status='logged_in',checked_at=self.now);sites._save(self.st,d)
        self.assertEqual(first_run.probe(self.st,'wechat',{'system':'linux'}),('verified',None))

    def test_required_stale_site_refreshes_through_trusted_checker_without_owner_card(self):
        d=sites.load(self.st)
        d['sites']['gmail']['checked_at']=self.now-sites.FRESH-1
        sites._save(self.st,d)
        def refresh(st, sid, **kwargs):
            self.assertEqual(sid,['gmail'])
            d=sites.load(st);d['sites']['gmail'].update(checked_at=self.now,status='logged_in')
            sites._save(st,d)
        with patch.object(sites,'check',side_effect=refresh) as check, \
             patch.object(browser,'setup',side_effect=AssertionError('never install')):
            rows={e['id']:e for e in self.sync(probe=True,ids=['browser-gmail'])['entries']}
            self.assertEqual(rows['browser-gmail']['status'],'ready')
            check.assert_called_once()

    def test_probe_uses_real_site_checker_list_contract(self):
        from unittest.mock import Mock
        d=sites.load(self.st);d['sites']['gmail']['checked_at']=self.now-sites.FRESH-5
        sites._save(self.st,d)
        with patch.object(browser,'runtime',return_value={'ws':'fake-local-endpoint'}), \
             patch.object(sites,'CDP',return_value=Mock()), patch.object(sites,'probe',return_value=('logged_in','signed_in')):
            rows={e['id']:e for e in self.sync(probe=True,ids=['browser-gmail'])['entries']}
            self.assertEqual(rows['browser-gmail']['status'],'ready')
            self.assertEqual(sites.load(self.st)['sites']['gmail']['status'],'logged_in')

    def test_invalid_business_projection_fails_closed_before_write(self):
        good = self.sync()
        rows = sites.registry(self.st, now=self.now)
        rows[0]['check']['status'] = 'disabled'
        with patch.object(sites, 'registry', return_value=rows):
            with self.assertRaises(capability.CapError): self.sync()
        self.assertEqual(capability.read_registry(self.st), good)
