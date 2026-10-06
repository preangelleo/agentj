import _hermetic
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from agentj.serve import Host
from agentj.silent import MARKER
from agentj import main_identity

class Silent(unittest.TestCase):
    def test_marker_is_history_only_without_live_message_or_push_flag(self):
        h=SimpleNamespace(cur_turn=7,hist=SimpleNamespace(get=Mock(return_value={'id':7})),turn_text=False,
            hist_update=Mock(return_value=[{'id':7}]),emit=Mock(),_remember=Mock(),_post=Mock())
        Host.agent_text(h,MARKER)
        h.hist_update.assert_called_once_with(7,append=MARKER)
        self.assertFalse(h.turn_text);h.emit.assert_not_called();h._remember.assert_not_called()
    def test_bilingual_prompt_explains_exact_marker_and_boundaries(self):
        for lang in ('en','zh'):
            text=main_identity.prompt({'language':lang})
            self.assertIn(MARKER,text)
            self.assertIn('agentj-config',text)
