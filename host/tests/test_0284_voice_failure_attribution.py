"""A failed transcription service cannot instruct the model to blame a silent recording."""
import _hermetic
import unittest
from agentj import compose

class Attribution(unittest.TestCase):
    def render(self,reason,lang='zh',text=''):
        return compose.render(text,[{'path':'/synthetic/recording.wav','mime':'audio/wav','bytes':1000,
                                     'asr':{'ok':False,'why':reason,'secs':61}}],lang)

    def test_service_failures_explain_failed_transcription_and_preserved_audio(self):
        for reason in ('broken','timeout','busy','not_installed','off','format','bad_audio'):
            for lang in ('zh','en'):
                with self.subTest(reason=reason,lang=lang):
                    result=self.render(reason,lang)
                    self.assertNotIn('请简短告诉对方没听清',result)
                    self.assertNotIn('did not catch it',result)
                    self.assertIn('转写失败' if lang=='zh' else 'transcription failed',result)
                    self.assertIn('不要推断录音没有声音' if lang=='zh' else 'Do not infer that the recording is silent',result)
                    self.assertIn('/synthetic/recording.wav',result)

    def test_genuine_no_speech_keeps_existing_no_speech_result(self):
        self.assertIn('没有听到说话',self.render('no_speech'))
        self.assertIn('no speech heard',self.render('no_speech','en'))

    def test_text_plus_failed_audio_does_not_ask_owner_to_repeat_entire_message(self):
        result=self.render('broken',text='Please review the document.')
        self.assertIn('Please review the document.',result)
        self.assertNotIn('不要推断录音没有声音',result)
