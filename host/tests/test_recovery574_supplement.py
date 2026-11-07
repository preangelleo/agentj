import _hermetic
"""Additional real-HOME and fake-transport runtime checks. Original failed tests stay intact."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agentj import main_identity, working_root
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from test_promo import promo, Fake, pc, COUPON, INTERNAL

class Supplemental(unittest.TestCase):
    def test_99_real_environment_home_alias_and_existing_config_are_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);alias=home/'alias';alias.symlink_to(home,target_is_directory=True)
            personal=home/'personal.json';personal.write_bytes(b'{"dir":"~"}\r\n')
            with patch.dict(os.environ,{'HOME':d}):
                st=SimpleNamespace(exists=lambda:True,config=lambda:{'agent':{'dir':'~'}})
                for root in ('~',str(home),str(alias)):
                    with self.subTest(root=root):
                        with self.assertRaisesRegex(ValueError,'Home root'):
                            working_root.select(st,root)
                        with self.assertRaisesRegex(main_identity.IdentityError,'reselect'):
                            main_identity.validate_working_root({'dir':root},st)
                self.assertEqual(personal.read_bytes(),b'{"dir":"~"}\r\n')
                self.assertTrue(alias.is_symlink())

    def test_399_real_cli_function_new_prefix_and_legacy_revoke_with_fake_transport(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);cfg=p/'promo.json';env=p/'.env'
            cfg.write_text(json.dumps({'coupon':COUPON,'api_version':'fixture','internal_coupon':INTERNAL}))
            env.write_text('AGENTJARVIS_STRIPE_KEY=sk_test_'+'X'*40+'\n')
            fake=Fake({('POST','/v1/promotion_codes'):lambda v:(200,pc(v['code'],coupon=INTERNAL))})
            out,err=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err),patch.dict(os.environ,{},clear=True):
                rc=promo.main(['new','--internal'],transport=fake,env_path=str(env),config_path=str(cfg),now=1790000000)
            self.assertEqual(rc,0,err.getvalue());self.assertRegex(out.getvalue(),r'^AJTST-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}\n$')
            params=fake.calls[0]['params'];self.assertEqual(params['promotion[coupon]'],INTERNAL)
            self.assertEqual(params['expires_at'],str(1790000000+86400));self.assertEqual(params['max_redemptions'],'1')
            legacy='AJI-ABCD-EFGH'
            fake=Fake({('GET','/v1/promotion_codes'):lambda v:(200,{'data':[pc(legacy,coupon=INTERNAL)],'has_more':False}),
                       ('POST','/v1/promotion_codes/promo_'+legacy):lambda v:(200,pc(legacy,False,coupon=INTERNAL))})
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()),patch.dict(os.environ,{},clear=True):
                rc=promo.main(['revoke',legacy],transport=fake,env_path=str(env),config_path=str(cfg),now=1790000000)
            self.assertEqual(rc,0);self.assertTrue(any(c['method']=='POST' for c in fake.calls))
