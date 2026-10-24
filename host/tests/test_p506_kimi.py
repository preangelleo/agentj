"""P506 #269: verified route IDs, existing login and unavailable-plan boundaries."""
import _hermetic  # noqa: F401
import unittest
from unittest.mock import patch
from agentj import agent_kimi as K, ai_route as A, provider_profiles as P

class KimiModels(unittest.TestCase):
    def env(self, keys, region='mainland-cn', model=None, own=False):
        with patch.object(K,'oauth_files',return_value=['owned'] if own else []), \
             patch.object(K,'config_facts',return_value={'providers':[]}), \
             patch.object(K,'fresh_keys',return_value=keys):
            return K.key_environment({},region,model,source={})

    def test_platform_default_native_id_both_regions(self):
        for region,domain in [('mainland-cn','moonshot.cn'),('global','moonshot.ai')]:
            result=self.env({'MOONSHOT_API_KEY':'synthetic-platform'},region)
            self.assertEqual(result['KIMI_MODEL_NAME'],'kimi-k3.1-pro')
            self.assertIn(domain,result['KIMI_MODEL_BASE_URL'])

    def test_existing_model_login_and_unknown_plan_are_preserved(self):
        for name in ('KIMI_API_KEY','MOONSHOT_API_KEY'):
            self.assertEqual(self.env({name:'synthetic-key'},model='owner-choice')['KIMI_MODEL_NAME'],'owner-choice')
            self.assertEqual(self.env({name:'synthetic-key'},own=True),{})
        self.assertEqual(self.env({'KIMI_API_KEY':'synthetic-coding'})['KIMI_MODEL_NAME'],'kimi-for-coding')

    def test_new_plus_recipe_and_bilingual_availability_guard(self):
        d=A.data()
        self.assertEqual(d['model'],'kimi-code-plan-cn/k3.1-pro')
        self.assertEqual(d['model_global'],'kimi-code-plan-global/k3.1-pro')
        for lang,word in [('zh','可用'),('en','available')]:
            text=' '.join(A.steps(lang,'/synthetic/keys.env'))
            self.assertIn(d['model'],text); self.assertIn(word,text)
            self.assertIn('K3.1 Pro',text)

    def test_go_requires_actual_catalog_and_keeps_compatible_fallbacks(self):
        for unknown in (None,[],{},'kimi-k3.1-pro'):
            self.assertEqual(P.model_candidates('opencode-go',unknown),[])
        self.assertEqual(P.model_candidates('opencode-go',['kimi-k3','k3.1-pro']),['kimi-k3'])
        self.assertEqual(P.model_candidates('opencode-go',['kimi-k3','kimi-k3.1-pro']),['kimi-k3.1-pro','kimi-k3'])
        self.assertEqual(A.primary('CN'),'ark')
        self.assertEqual(A.data()['model_light'],'kimi-code-plan-cn/kimi-for-coding')
