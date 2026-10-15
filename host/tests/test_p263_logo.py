"""P263: five-logo validation, old packages, retention and actual approved pixels."""
import _hermetic
import json, pathlib, tempfile, unittest
from agentj import theme
import theme_fixtures as fx

STATES = ('idle','working','waiting','question','offline')
class Logos(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name);self.folder=self.root/'theme'
        fx.make_theme(self.folder)
        self.doc=json.loads((self.folder/'theme.json').read_text())
        self.doc['schema']=2
        self.doc['logo']={'states':{s:s+'.png' for s in STATES},'disc':True}
        for s in STATES:(self.folder/(s+'.png')).write_bytes(fx.disc(128))
        self.write()
    def write(self): (self.folder/'theme.json').write_text(json.dumps(self.doc))
    def codes(self): return {e['code'] for e in theme.validate(self.folder)['errors']}
    def test_valid_group_and_wire(self):
        self.assertTrue(theme.validate(self.folder)['ok'])
        self.assertTrue(theme.apply_dir(self.root/'state',self.folder)['ok'])
        self.assertEqual(set(theme.phone_message(self.root/'state')['logo']['states']),set(STATES))
    def test_legacy_apply(self):
        self.doc['schema']=1;self.doc['logo']={'image':'logo.png'};self.write()
        self.assertTrue(theme.apply_dir(self.root/'state',self.folder)['ok'])
        self.assertIn('png',theme.phone_message(self.root/'state')['logo'])
    def test_missing_extra_and_ambiguous(self):
        del self.doc['logo']['states']['offline'];self.write();self.assertIn('schema.required',self.codes())
        self.doc['logo']['states']['offline']='offline.png';self.doc['logo']['states']['surprise']='idle.png';self.write();self.assertIn('schema.unknown',self.codes())
        del self.doc['logo']['states']['surprise'];self.doc['logo']['image']='logo.png';self.write();self.assertIn('schema.oneOf',self.codes())
    def test_sizes_static_empty_limits_and_contrast(self):
        for data,code in [(fx.disc(256),'logo.dimensions'),(fx.disc(100),'png.size'),(fx.png(128,128,lambda x,y:(0,0,0,0)),'logo.empty'),(fx.disc(128,(250,249,245,255)),'logo.contrast'),(fx.png(128,128,lambda x,y:(44,43,38,255),extra_chunks=[(b'acTL',b'12345678')]),'png.animated'),(b'x'*65537,'file.size')]:
            with self.subTest(code=code):
                (self.folder/'working.png').write_bytes(data)
                r=self.codes();self.assertIn(code,r)
    def test_disc_off_uses_state_background(self):
        self.doc['logo']['disc']=False;self.write()
        (self.folder/'working.png').write_bytes(fx.disc(128,tuple(bytes.fromhex(self.doc['colors']['state']['working'][1:]))+(255,)))
        self.assertIn('logo.contrast',self.codes())
    def test_keep_current_group_if_robot_only(self):
        self.assertTrue(theme.apply_dir(self.root/'state',self.folder)['ok'])
        nxt=self.root/'next';fx.make_theme(nxt,id='next-theme')
        doc=json.loads((nxt/'theme.json').read_text());del doc['logo'];(nxt/'theme.json').write_text(json.dumps(doc))
        self.assertTrue(theme.apply_dir(self.root/'state',nxt)['ok'])
        m=theme.phone_message(self.root/'state');self.assertEqual(set(m['logo']['states']),set(STATES))
    def test_factory_both_have_the_same_group(self):
        for kind in theme.FACTORY:
            r=theme.validate(theme.preset(kind),factory_ok=True);self.assertTrue(r['ok'],r['errors'])
            self.assertEqual(set(r['theme']['logo']['states']),set(STATES))
            for s in STATES:self.assertEqual((theme.preset(kind)/(s.join(['logo-','.png']))).read_bytes(),(theme.preset('robots')/('logo-'+s+'.png')).read_bytes())
