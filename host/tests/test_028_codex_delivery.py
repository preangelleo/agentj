"""Real Codex notification/retry functions with synthetic accepted-turn events."""
import asyncio
import unittest
from unittest.mock import Mock, AsyncMock
from agentj.agent_codex import CodexAgent

class Delivery(unittest.IsolatedAsyncioTestCase):
    def agent(self):
        a=object.__new__(CodexAgent)
        a.tid='thread';a.tail_cut=None;a.halting=False;a.rate_retry=None
        a.turn_done=asyncio.Event();a.thread={};a.touch=Mock();a.first_output=Mock()
        a.fail_notice=Mock();a.call=AsyncMock();a.collect=[];a.quiet=True
        return a
    async def test_accepted_quota_turn_does_not_submit_continue(self):
        a=self.agent()
        a._on_note('turn/completed',{'threadId':'thread','turn':{'id':'accepted','status':'failed',
            'error':{'message':'usage_limit_reached','codexErrorInfo':'usageLimitExceeded'}}})
        self.assertTrue(a.turn_done.is_set());self.assertIs(a.rate_retry,False)
        a.fail_notice.assert_called_once();a.call.assert_not_awaited()
    async def test_accepted_transient_rate_turn_is_not_replayed(self):
        a=self.agent()
        a._on_note('turn/completed',{'threadId':'thread','turn':{'id':'accepted','status':'failed',
            'error':{'message':'429 rate limit'}}})
        self.assertIs(a.rate_retry,False);a.call.assert_not_awaited()
    async def test_legacy_retry_unknown_result_never_starts_turn(self):
        a=self.agent();await a._retry_rate('unknown result')
        a.call.assert_not_awaited();a.fail_notice.assert_called_once_with('unknown result')
    async def test_halting_does_not_restart_or_replay(self):
        a=self.agent();a.halting=True;await a._retry_rate('quota')
        a.call.assert_not_awaited();a.fail_notice.assert_not_called()

if __name__=='__main__':unittest.main()

class LegacyGuidance(unittest.TestCase):
    def test_old_approval_is_still_denied_with_phone_terminal_guidance(self):
        for method in ('execCommandApproval','applyPatchApproval'):
            a=object.__new__(CodexAgent)
            a.touch=Mock();a._answer=Mock();a.local_only=Mock();a.research=False
            a._on_request({'method':method,'id':1,'params':{}})
            a._answer.assert_called_once_with(1,{'decision':'denied'})
            self.assertTrue(a._quota_evidence)
            text=a.local_only.call_args.args[0]
            self.assertIn('手机网页终端',text)
            self.assertIn('paired phone',text)
            self.assertIn('host online',text)
