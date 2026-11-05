"""Long voice routing regressions; no real network or personal audio."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _hermetic
import unittest
from unittest.mock import Mock, patch
from agentj import asr, voice, preferences as p

class LongVoice(unittest.TestCase):
    def test_voxtype_network_failure_falls_back_without_changing_selection(self):
        remote={'ok':False,'reason':'engine_failed','detail':'No route to host'}
        local={'ok':True,'text':'本机完整文字','engine':'sherpa'}
        with patch.object(asr,'settings',return_value={'engine':'voxtype','threads':2}), patch.object(asr,'_transcribe_once',side_effect=[remote,local],create=True) as run, patch.object(asr,'shutdown') as shutdown:
            result=asr.transcribe('/sample',timeout_s=894,state_dir='/isolated')
        self.assertTrue(result['ok']);self.assertEqual(result['notice'],'local_fallback')
        self.assertEqual(run.call_args.kwargs['engine_override'],'sensevoice')
        self.assertGreater(run.call_args.kwargs['timeout_s'],700)
        shutdown.assert_called_once()

    def test_remote_duration_allowance_and_local_time_reservation(self):
        from agentj import compose
        for seconds in (5,60,119,180,600):
            with self.subTest(seconds=seconds),patch.object(asr,'check_wav',return_value={'seconds':seconds}):
                budget=compose.asr_budget(seconds)
                remote=min(budget/2,asr.remote_timeout('/sample'))
                self.assertLessEqual(remote,budget/2)
                if seconds>=60:self.assertGreater(remote,30)
                self.assertGreater(budget-remote,seconds)

    def test_voxtype_success_and_explicit_off_do_not_fallback(self):
        for selected in ('voxtype','off'):
            primary={'ok':True,'text':'文字'} if selected=='voxtype' else {'ok':False,'reason':'off'}
            with patch.object(asr,'_transcribe_once',return_value=primary) as run,patch.object(asr,'shutdown') as shutdown:
                self.assertEqual(asr.transcribe('/sample',timeout_s=60,engine_override=selected),primary)
            self.assertEqual(run.call_count,1);shutdown.assert_not_called()

    def test_legacy_state_voxtype_and_both_fail_have_fixed_notice(self):
        with patch.object(asr,'settings',return_value={'engine':'voxtype','threads':2}),patch.object(asr,'_transcribe_once',side_effect=[{'ok':False,'reason':'timeout'},{'ok':False,'reason':'not_installed'}]) as run,patch.object(asr,'shutdown'):
            result=asr.transcribe('/sample',timeout_s=60,engine_override='auto')
        self.assertFalse(result['ok']);self.assertEqual(result['notice'],'remote_unavailable')
        self.assertEqual(run.call_args.kwargs['engine_override'],'sensevoice')

    def test_unexpected_engine_error_still_recovers_and_hides_details(self):
        with patch.object(asr,'_transcribe_once',side_effect=[ValueError('PRIVATE_SENTINEL'),{'ok':True,'text':'文字'}]),patch.object(asr,'shutdown'):
            result=asr.transcribe('/sample',timeout_s=60,engine_override='voxtype')
        self.assertTrue(result['ok']);self.assertEqual(result['notice'],'local_fallback')
        self.assertNotIn('PRIVATE_SENTINEL',str(result))

    def test_cloud_failure_uses_same_local_fallback(self):
        cfg=p.validate({'voice':{'asr':{'mode':'cloud','provider':'openai','key_env':'OPENAI_API_KEY'}}})
        local=Mock();local.transcribe.return_value={'ok':True,'text':'完整文字','engine':'sherpa'}
        with patch.object(voice,'cloud_asr',return_value={'ok':False,'reason':'engine_failed'}):
            result=voice.transcribe('/sample',cfg,894,local_asr=local)
        self.assertTrue(result['ok']);self.assertEqual(result['notice'],'local_fallback')
        self.assertEqual(local.transcribe.call_args.kwargs['engine_override'],'sensevoice')

    def test_long_custom_request_has_more_than_thirty_seconds(self):
        cfg=p.validate({'voice':{'asr':{'mode':'custom','base_url':'http://127.0.0.1:1234','model':'test'}}})
        local=Mock()
        with patch.object(asr,'remote_timeout',return_value=150,create=True), patch.object(voice,'cloud_asr',return_value={'ok':True,'text':'文字'}) as request:
            self.assertTrue(voice.transcribe('/sample',cfg,894,local_asr=local)['ok'])
        self.assertEqual(request.call_args.args[2],150)
        local.transcribe.assert_not_called()
