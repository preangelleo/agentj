"""Actual Chromium lifecycle/auth/QR generation, isolated loopback fixture only."""
import _hermetic
import asyncio
import base64
import contextlib
import http.server
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agentj import browser, browser_sites as bs, browser_login as bl
from agentj.browser_cdp import CDP
from agentj.state import State
from test_p114 import FakeHost, FakeElev, Sess, chrome_path

class Site:
    def __init__(self):
        self.generation = 0
        self.login_mode = 'page'
        self.static_qr = False
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):pass
            def do_GET(self):
                path = self.path.split('?',1)[0]
                logged = 'fixture_auth=yes' in self.headers.get('Cookie','')
                if path in ('/auth','/logout'):
                    self.send_response(303)
                    self.send_header('Set-Cookie','fixture_auth=yes; Path=/' if path=='/auth' else 'fixture_auth=; Max-Age=0; Path=/')
                    self.send_header('Location','/app' if path=='/auth' else '/login');self.end_headers();return
                if path=='/login' and owner.login_mode=='abort':
                    self.connection.shutdown(2);self.connection.close();return
                if path=='/login' and owner.login_mode=='download':
                    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment; filename=fixture.txt');self.end_headers();self.wfile.write(b'fixture download only');return
                if path=='/login' and logged:
                    self.send_response(303);self.send_header('Location','/app');self.end_headers();return
                if path=='/app' and not logged:
                    self.send_response(303);self.send_header('Location','/login');self.end_headers();return
                if path=='/login':
                    owner.generation += 1
                    n = 1 if owner.static_qr else owner.generation
                    page = f'''<!doctype html><meta charset="utf-8"><title>Fixture sign in</title>
<canvas id="qr" width="200" height="200"></canvas><form id="signin"></form><script>
const c=document.querySelector('canvas').getContext('2d');c.fillStyle='#fff';c.fillRect(0,0,200,200);
c.fillStyle='rgb({n%250},40,80)';for(let i=0;i<20;i++)for(let j=0;j<20;j++)if((i*7+j*3+{n})%5<2)c.fillRect(i*10,j*10,10,10);
</script>'''
                else:page='<!doctype html><title>Fixture app</title><p>Local fixture only</p>'
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(page.encode())
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
    def template(self):
        return {'title':{'zh':'本地测试','en':'Local fixture'},'origins':[self.origin],
                'check_url':self.origin+'/app','login_url':self.origin+'/login','login_method':'qr',
                'in_url':['^'+re.escape(self.origin)+'/app$'],'out_url':['^'+re.escape(self.origin)+'/login$'],
                'out_dom':['#signin'],'qr':{'selectors':['#qr'],'ttl':120}}
    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

@unittest.skipUnless(chrome_path(),'requires real Chromium')
class RealParity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # A headless CI account still exercises the real headed Chromium path.
        # Own the display in this fixture: gate environments intentionally omit
        # the operator's DISPLAY, and wrapping the outer runner cannot supply it.
        if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
            xvfb=shutil.which('Xvfb')
            self.assertIsNotNone(xvfb,'headed Chromium fixture requires Xvfb')
            display=subprocess.Popen([xvfb,'-displayfd','1','-screen','0','1024x768x24','-nolisten','tcp'],
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            def stop_display():
                if display.poll() is None:display.terminate()
                try:display.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    display.kill();display.communicate(timeout=10)
            self.addCleanup(stop_display)
            ready,_,_=select.select([display.stdout],[],[],10)
            self.assertTrue(ready,'Xvfb did not publish a fixture display')
            number=display.stdout.readline().decode().strip()
            self.assertTrue(number.isdecimal(),'Xvfb fixture failed to allocate a display')
            display_env=patch.dict(os.environ,{'DISPLAY':':'+number,'WAYLAND_DISPLAY':''})
            display_env.start();self.addCleanup(display_env.stop)
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMP'));self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.st=State(self.root/'state');self.st.root.mkdir(mode=0o700)
        self.site=Site();self.addCleanup(self.site.close)
        templates={**bs.templates(),'fixture':self.site.template()}
        p=patch.object(bs,'templates',return_value=templates);p.start();self.addCleanup(p.stop)
        exe = chrome_path()
        banner = subprocess.check_output([exe, "--version"], text=True, timeout=10)
        # CI may use Chrome for Testing. Installed is mocked only for this real-browser fixture.
        self.fixture_version = re.search(r"\b[0-9]+(?:\.[0-9]+){3}\b", banner)[0]
        p = patch.object(browser, "installed", return_value={"version": self.fixture_version, "path": exe})
        p.start()
        self.addCleanup(p.stop)
        browser.ensure_dir(self.st);bs.add(self.st,'fixture')
        self.procs=[];self.cards=[]
        self.addAsyncCleanup(self.finish)
    async def finish(self):
        for cards in self.cards:cards.end_all('cancelled')
        for proc in self.procs:
            if proc.poll() is None:await asyncio.to_thread(browser.stop_proc,proc)
            else:proc.wait()
        await asyncio.sleep(0)
    def start(self,headless=True):
        proc,rt=browser.start_browser(self.st,headless=headless)
        if proc is not None:self.procs.append(proc)
        self.assertIsNotNone(browser.runtime(self.st));self.assertTrue(browser.loopback_only(browser.listen_addresses(rt['port'],rt['pid'])))
        return proc,browser.runtime(self.st)
    async def goto(self,url):
        rt=browser.runtime(self.st)
        with CDP(rt['ws']) as cdp:
            target=cdp.new_tab(url,background=True);session=cdp.attach(target)
            for _ in range(100):
                href,ready=cdp.location(session)
                if ready=='complete' and href.startswith(self.site.origin):break
                await asyncio.sleep(.05)
            return target
    def login_cards(self):
        host=FakeHost(self.st);cards=bl.LoginCards(FakeElev(host));self.cards.append(cards);return host,cards
    async def test_headless_and_headed_runtime_are_not_business_auth_and_logout_is_detected(self):
        for headless in (True,False):
            with self.subTest(headless=headless):
                proc,rt=self.start(headless)
                self.assertEqual(rt['headless'],headless)
                owner=await self.goto(self.site.origin+'/owner')
                check=await asyncio.to_thread(bs.check,self.st,['fixture'])
                self.assertEqual(check['sites'][0]['status'],'auth_required')
                rows={r['id']:r for r in bs.registry(self.st)}
                self.assertEqual(rows['browser-runtime']['status'],'ready')
                self.assertEqual(rows['browser-fixture']['status'],'unavailable')
                await self.goto(self.site.origin+'/auth')
                check=await asyncio.to_thread(bs.check,self.st,['fixture'])
                self.assertEqual(check['sites'][0]['status'],'logged_in')
                await self.goto(self.site.origin+'/logout')
                check=await asyncio.to_thread(bs.check,self.st,['fixture'])
                self.assertEqual(check['sites'][0]['status'],'auth_required')
                self.assertTrue(any(r['to']=='auth_required' for r in check['changed']))
                with CDP(rt['ws']) as cdp:self.assertTrue(cdp.tab_alive(owner))
                browser.stop_proc(proc)
    async def test_explicit_refresh_gets_new_page_qr_bytes_and_erases_old_card_image(self):
        self.start();host,cards=self.login_cards()
        first=await cards.request({'site':'fixture'})
        self.assertEqual(first['result'],'sent',first)
        old=cards.cards[first['id']];before=bytes(old['img']);generation=self.site.generation
        target=cards.tabs['fixture']
        repeated=await cards.request({'site':'fixture'})
        self.assertTrue(repeated['reused']);self.assertEqual(repeated['id'],first['id'])
        self.assertEqual(self.site.generation,generation)
        refreshed=await cards.request({'site':'fixture','refresh':True},by='dev1')
        self.assertEqual(refreshed['result'],'sent',refreshed)
        self.assertNotEqual(refreshed['id'],first['id']);self.assertEqual(old['img'],bytearray())
        self.assertEqual(cards.tabs['fixture'],target)
        self.assertGreater(self.site.generation,generation,'refresh must load a new QR page, not remint the same expiring image')
        self.assertNotEqual(bytes(cards.cards[refreshed['id']]['img']),before)
        self.assertTrue(bytes(cards.cards[refreshed['id']]['img']).startswith(b'\x89PNG'))
        metadata=(browser.bdir(self.st)/'login.log').read_text()+(browser.bdir(self.st)/'login-results.json').read_text()
        self.assertNotIn(base64.b64encode(before).decode(),metadata)
    async def test_crashed_login_tab_cannot_confirm_and_explicit_recovery_reopens_only_own_profile(self):
        proc,rt=self.start();host,cards=self.login_cards()
        first=await cards.request({'site':'fixture'});self.assertEqual(first['result'],'sent',first)
        card=cards.cards[first['id']]
        os.kill(proc.pid,signal.SIGKILL);await asyncio.to_thread(proc.wait)
        self.assertIsNone(browser.runtime(self.st))
        with patch.object(State,'sign_key',return_value=b'x'*32):
            await cards.on_phone(Sess('dev1'),{'t':'login_qr_check','id':card['id'],'n':card['nonce']})
        self.assertIn(card['id'],cards.cards)
        self.assertEqual(host.sent[-1]['why'],'browser')
        recovered,new=self.start()
        self.assertNotEqual(new['pid'],rt['pid']);self.assertEqual(new['profile'],rt['profile'])
        second=await cards.request({'site':'fixture','refresh':True})
        self.assertEqual(second['result'],'sent',second);self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(card['img'],bytearray())


    async def test_refresh_of_already_authenticated_tab_does_not_navigate_to_login(self):
        self.start();host,cards=self.login_cards()
        target=await self.goto(self.site.origin+'/auth');cards.tabs['fixture']=target
        generation=self.site.generation
        answer=await cards.request({'site':'fixture','refresh':True})
        self.assertEqual(answer['result'],'already',answer)
        self.assertEqual(self.site.generation,generation)
        self.assertFalse(cards.cards)
        self.assertEqual(bs.summary(self.st)[0]['status'],'logged_in')

    async def test_supervisor_clean_close_is_final_crash_is_visible_and_explicit_recovery_works(self):
        script = """import _hermetic,sys
from unittest.mock import patch
from agentj import browser
from agentj.state import State
from pathlib import Path
st=State(Path(sys.argv[1]))
with patch.object(browser,'installed',return_value={'version':sys.argv[3],'path':sys.argv[2]}), patch.object(browser,'_manager_has_display',return_value=False):
    raise SystemExit(browser.run(st))
"""
        for close in ('clean','crash'):
            with self.subTest(close=close):
                env={**os.environ,'DISPLAY':'','WAYLAND_DISPLAY':''}
                # unittest discover modifies only this interpreter's sys.path;
                # the real supervisor process must import the same safety guard.
                env['PYTHONPATH']=os.pathsep.join([str(Path(__file__).resolve().parent),
                    str(Path(__file__).resolve().parents[1]),env.get('PYTHONPATH','')])
                supervisor=subprocess.Popen([sys.executable,'-c',script,str(self.st.root),chrome_path(),self.fixture_version],env=env,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                rt=None
                try:
                    until=time.monotonic()+30
                    while browser.runtime(self.st) is None and supervisor.poll() is None and time.monotonic()<until:
                        await asyncio.sleep(.1)
                    rt=browser.runtime(self.st)
                    if rt is None:
                        stdout,stderr=await asyncio.to_thread(supervisor.communicate,timeout=10)
                        diagnostic=browser._read_json(browser.bdir(self.st)/'last-error.json') or {}
                        self.fail('supervisor fixture exited '+str(supervisor.returncode)+' '+stderr.decode()+' reason='+str(diagnostic.get('reason')))
                    if close=='clean':
                        with CDP(rt['ws']) as cdp:
                            with contextlib.suppress(Exception):cdp.call('Browser.close')
                    else:os.kill(rt['pid'],signal.SIGKILL)
                    _,err=await asyncio.to_thread(supervisor.communicate,timeout=15)
                    self.assertEqual(supervisor.returncode,0 if close=='clean' else 3,err.decode())
                    self.assertIsNone(browser.runtime(self.st));self.assertFalse(browser.runtime_path(self.st).exists())
                    await asyncio.sleep(1)
                    self.assertIsNone(browser.runtime(self.st),'observer must not silently respawn an intentionally closed browser')
                finally:
                    if supervisor.poll() is None:supervisor.terminate()
                    await asyncio.to_thread(supervisor.communicate,timeout=10)
                    if rt and browser.proc_start_time(rt['pid'])==rt['starttime']:
                        os.kill(rt['pid'],signal.SIGTERM)
        proc,new=self.start()
        self.assertIsNotNone(new)


    async def test_refresh_rechecks_cookie_login_completed_in_another_owned_tab(self):
        self.start();host,cards=self.login_cards()
        first=await cards.request({'site':'fixture'});self.assertEqual(first['result'],'sent',first)
        old=cards.cards[first['id']]
        await self.goto(self.site.origin+'/auth')
        result=await cards.request({'site':'fixture','refresh':True})
        self.assertEqual(result['result'],'already',result)
        self.assertEqual(old['img'],bytearray());self.assertFalse(cards.cards)
        self.assertEqual(bs.summary(self.st)[0]['status'],'logged_in')


    async def test_failed_or_download_navigation_never_reissues_the_previous_qr(self):
        from agentj.browser_cdp import CDP as RealCDP
        self.start();host,cards=self.login_cards()
        first=await cards.request({'site':'fixture'});self.assertEqual(first['result'],'sent',first)
        old=cards.cards[first['id']];before=bytes(old['img']);sent=len([m for m in host.sent if m.get('t')=='login_qr'])
        with CDP(browser.runtime(self.st)['ws']) as cdp:
            cdp.call('Browser.setDownloadBehavior',{'behavior':'allow','downloadPath':str(self.root/'downloads')})
        actual=RealCDP.call;observed=[]
        def observe(cdp,method,*args,**kwargs):
            result=actual(cdp,method,*args,**kwargs)
            if method=='Page.navigate':observed.append(result)
            return result
        for mode in ('download','abort'):
            self.site.login_mode=mode
            with patch.object(RealCDP,'call',observe):
                result=await cards.request({'site':'fixture','refresh':True})
            self.assertEqual(result['result'],'failed',result)
            self.assertEqual(len([m for m in host.sent if m.get('t')=='login_qr']),sent)
            self.assertEqual(set(cards.cards),{first['id']});self.assertEqual(bytes(old['img']),before)
        self.assertEqual(len(observed),2)
        self.assertTrue(any(r.get('isDownload') or r.get('errorText') for r in observed))

    async def test_actual_cdp_missing_new_loader_receipt_refuses_instead_of_guessing_new_qr(self):
        from agentj.browser_cdp import CDP as RealCDP
        self.start();host,cards=self.login_cards()
        first=await cards.request({'site':'fixture'});self.assertEqual(first['result'],'sent',first)
        old=cards.cards[first['id']];before=bytes(old['img']);actual=RealCDP.call
        def missing(cdp,method,*args,**kwargs):
            result=actual(cdp,method,*args,**kwargs)
            if method=='Page.navigate':return {k:v for k,v in result.items() if k!='loaderId'}
            return result
        with patch.object(RealCDP,'call',missing):
            result=await cards.request({'site':'fixture','refresh':True})
        self.assertEqual(result['result'],'failed',result)
        self.assertEqual(set(cards.cards),{first['id']});self.assertEqual(bytes(old['img']),before)
        self.assertEqual(len([m for m in host.sent if m.get('t')=='login_qr']),1)


    async def test_reload_with_unchanged_qr_never_extends_old_material_deadline(self):
        self.site.static_qr=True
        self.start();host,cards=self.login_cards()
        first=await cards.request({'site':'fixture'});self.assertEqual(first['result'],'sent',first)
        old=cards.cards[first['id']];before=bytes(old['img']);deadline=old['deadline'];generation=self.site.generation
        result=await cards.request({'site':'fixture','refresh':True})
        self.assertGreater(self.site.generation,generation,'the actual page was reloaded')
        self.assertEqual(result['result'],'failed',result)
        self.assertEqual(result['why'],'browser');self.assertIn('refresh later',result['hint'])
        self.assertEqual(set(cards.cards),{first['id']});self.assertEqual(old['deadline'],deadline)
        self.assertEqual(bytes(old['img']),before)
        self.assertEqual(len([m for m in host.sent if m.get('t')=='login_qr']),1)
        # The failed refresh replaced the original document. Its surviving card cannot attest the new page.
        with patch.object(bs, 'probe') as probe:
            status = await cards._confirm(old)
            self.assertEqual(status, 'unavailable')
            probe.assert_not_called()


    async def test_expired_card_cannot_remint_same_tab_material_from_retained_digest(self):
        self.site.static_qr=True
        self.start();host,cards=self.login_cards()
        first=await cards.request({'site':'fixture'});self.assertEqual(first['result'],'sent',first)
        old=cards.cards[first['id']];deadline=old['deadline'];history=dict(getattr(cards,'qr_history',{}).get('fixture',{}))
        # Time advances only for expiry handling: real Chrome/CDP keeps real I/O deadlines.
        with patch.object(bl.time,'monotonic',return_value=deadline+1):
            cards._end(old,'expired')
        self.assertFalse(cards.cards);self.assertEqual(old['img'],bytearray())
        if history:
            self.assertNotIn('png',cards.qr_history['fixture']);self.assertEqual(cards.qr_history['fixture'],history)
        result=await cards.request({'site':'fixture','refresh':True})
        self.assertEqual(result['result'],'failed',result)
        self.assertTrue(history)
        self.assertFalse(cards.cards);self.assertEqual(cards.qr_history['fixture'],history)
        self.assertEqual(len([m for m in host.sent if m.get('t')=='login_qr']),1)
        # A genuinely new owned target generation is independent; the previous tab is not silently closed.
        rt=browser.runtime(self.st);old_target=cards.tabs['fixture']
        with CDP(rt['ws']) as cdp:cdp.close_tab(old_target)
        result=await cards.request({'site':'fixture','refresh':True})
        self.assertEqual(result['result'],'sent',result);self.assertNotEqual(cards.tabs['fixture'],old_target)
        self.assertNotEqual(cards.qr_history['fixture']['generation'],history['generation'])
