"""Only synthetic pure inputs; no state, credentials or network."""
import unittest
from agentj.quota_pace import pace
from agentj.quota import analyse

class PaceAudit(unittest.TestCase):
    def test_elapsed_not_amount(self):
        now, period = 1000000, 604800
        self.assertAlmostEqual(pace(10, now+period*.99, now)['speed'], 10)
        self.assertEqual(pace(10, now+period*.4, now)['fast'], False)
        self.assertEqual(pace(10, now+period*.99, now)['tier'], 'fast')

    def test_boundaries(self):
        now = 1000000
        for used in (-1,101,None,True,float('nan')):
            self.assertIsNone(pace(used,now+100,now)['speed'])
        for reset in (None,0,-1,now,now-1,now+604801,float('inf')):
            self.assertIsNone(pace(10,reset,now)['speed'])
        self.assertIsNone(pace(10,now+604800,now)['speed'])
        self.assertEqual(pace(0,now+100,now)['speed'], 0)
        self.assertIsNone(pace(10,now+100,now,stale=True)['speed'])
        for used,tier in ((80,'tight'),(90,'empty')):
            self.assertEqual(pace(used,now+604799,now)['tier'],tier)

    def test_agent_uses_same_pace(self):
        now=1000000
        src={'harness':'codex','status':'ok','used_pct':10,'reset_at':now+604800*.99,'source_at':now}
        self.assertEqual(analyse(src,[],now)['pace'], pace(10,src['reset_at'],now))

    def test_stale_and_missing_agent_readings(self):
        now = 1000000
        base = {'harness':'codex', 'status':'ok', 'used_pct':10,
                'reset_at':now+604000, 'source_at':now-301}
        self.assertIsNone(analyse(base,[],now)['pace']['speed'])
        for field in ('reset_at', 'used_pct'):
            src = dict(base)
            src.pop(field)
            self.assertEqual(analyse(src,[],now)['status'], 'unknown')
