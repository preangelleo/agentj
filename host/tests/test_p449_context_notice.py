"""#449: the heartbeat never pauses for context or model; at >= 50 % of the window the main Agent (not the owner) gets one
"write your own handoff, then compact" line inside the beat, at most once per session per day."""
import _hermetic  # noqa
import asyncio
import time
import unittest
from unittest.mock import Mock, patch
from test_p156_heartbeat import Decide
from agentj import heartbeat_check as check

ZH = "找空闲时自写交接再压缩"
EN = "When idle, write your own handoff, then compact."


def noon(days=0):
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday + days, 12, 0, 0, 0, 0, -1))


class ContextNotice(Decide):
    def setUp(self):
        super().setUp()
        self.host.preferences = {'heartbeat': {'quiet_enabled': False}, 'updates': {'auto_install': False}}
        from agentj import heartbeat
        heartbeat.ensure_prompt('zh')
        self.now = noon()
        self.h.started = self.h.last_activity = self.now - 7200
        p = patch.object(check, 'collect', return_value=(['todo_due'], []))
        p.start()
        self.addCleanup(p.stop)
        self.host.agent_notice = Mock()

    def beat(self, at):
        r = asyncio.run(self.h.tick(at))
        if self.agent.got:
            self.agent.got[-1].state = 'delivered'        # the beat finished; the next one may go
        return r

    def owner_never_pushed(self):
        self.host.agent_notice.assert_not_called()
        for send in self.agent.got:
            for old in ('请发送 /compact', 'Send /compact', '心跳已暂停', 'Heartbeat paused'):
                self.assertNotIn(old, send.text)
            self.assertEqual(self.host.hist.get(send.turn)['src'].get('text'), '', 'beat body stays off the owner page')

    def test_30_percent_nothing(self):
        self.host.meter_state = {'ctx': {'used': 60000, 'max': 200000}, 'model': 'claude-opus-4-1'}
        r = self.beat(self.now)
        self.assertTrue(r['ok'], r)
        self.assertNotIn(ZH, self.agent.got[-1].text)
        self.owner_never_pushed()

    def test_explicit_high_cost_profile_does_not_pause_or_notify_owner(self):
        self.host.meter_state = {'ctx': {'used': 60000, 'max': 200000},
                                 'model': 'fixture-expensive', 'cost_profile': 'high'}
        self.assertTrue(self.beat(self.now)['ok'])
        self.assertNotIn(ZH,self.agent.got[-1].text)
        self.owner_never_pushed()

    def test_old_absolute_and_expensive_rules_gone(self):
        # 120k used, but a 1M window (12 %) on an "expensive" model: the old guard paused here
        self.host.meter_state = {'ctx': {'used': 120000, 'max': 1000000}, 'model': 'gpt-6'}
        self.assertTrue(self.beat(self.now)['ok'])
        self.assertNotIn(ZH, self.agent.got[-1].text)
        self.owner_never_pushed()

    def test_55_percent_once_per_session_per_day(self):
        self.host.meter_state = {'ctx': {'used': 110000, 'max': 200000}, 'model': 'claude-opus-4-1'}
        r = self.beat(self.now)
        self.assertTrue(r['ok'], r)
        self.assertIn(ZH, self.agent.got[-1].text)
        self.assertEqual(len(self.agent.got), 1)
        r2 = self.beat(self.now + 3700)
        self.assertTrue(r2['ok'], 'heartbeat continues')
        self.assertEqual(len(self.agent.got), 2)
        self.assertNotIn(ZH, self.agent.got[-1].text, 'same session, same day: no second notice')
        from agentj.heartbeat import Heartbeat         # the marker survives a serve restart
        other = Heartbeat(self.host)
        other.started = other.last_activity = self.now
        self.h.inflight = None
        self.assertTrue(asyncio.run(other.tick(self.now + 7400))['ok'])
        self.assertNotIn(ZH, self.agent.got[-1].text, 'persisted marker')
        self.owner_never_pushed()

    def test_next_day_notice_again(self):
        self.host.meter_state = {'ctx': {'used': 140000, 'max': 200000}}     # 70 %: the half notice (75 % has its own)
        self.assertTrue(self.beat(self.now)['ok'])
        self.assertIn(ZH, self.agent.got[-1].text)
        self.h.last_activity = noon(1) - 7200
        self.assertTrue(self.beat(noon(1))['ok'])
        self.assertIn(ZH, self.agent.got[-1].text)
        self.owner_never_pushed()

    def test_english(self):
        self.host.lang = 'en'
        self.host.meter_state = {'ctx': {'used': 100000, 'max': 200000}}
        self.assertTrue(self.beat(self.now)['ok'])
        self.assertIn(EN, self.agent.got[-1].text)
        self.owner_never_pushed()

    def test_unknown_window_no_pause_no_notice(self):
        for meter in ({'ctx': {'used': 150000}, 'model': 'claude-opus-4-1'}, {'ctx': {'used': 150000, 'max': 0}},
                      {'ctx': {'used': True, 'max': 2}}, {'ctx': None, 'model': 'gpt-6-pro'}, {}):
            self.agent.got.clear()
            self.host.meter_state = meter
            self.h.last_activity = self.h.started = self.now - 7200
            self.h._save({'beats': {}})
            self.h.last_checked = {}
            r = self.beat(self.now)
            self.assertTrue(r['ok'], (meter, r))
            self.assertNotIn(ZH, self.agent.got[-1].text, meter)
        self.owner_never_pushed()


MUST = "上下文已用到四分之三"


class ContextNotice0269(ContextNotice):
    """0.26.9 #449 b/d + the self-compact invite (the beat that asked may compact without an owner turn)."""

    def test_absolute_cap_on_a_large_window(self):
        self.host.meter_state = {'ctx': {'used': 410000, 'max': 1000000}}     # 41 %: over the 400k default cap
        self.assertTrue(self.beat(self.now)['ok'])
        self.assertIn(ZH, self.agent.got[-1].text)
        self.owner_never_pushed()

    def test_cap_preference(self):
        self.host.preferences['heartbeat']['context_max_tokens'] = 50000
        self.host.meter_state = {'ctx': {'used': 60000, 'max': 200000}}       # 30 % but over the owner's 50k
        self.assertTrue(self.beat(self.now)['ok'])
        self.assertIn(ZH, self.agent.got[-1].text)

    def test_75_percent_required_once_more_then_heartbeat_goes_on(self):
        self.host.meter_state = {'ctx': {'used': 110000, 'max': 200000}}
        self.assertTrue(self.beat(self.now)['ok'])
        self.assertIn(ZH, self.agent.got[-1].text)
        self.host.meter_state = {'ctx': {'used': 160000, 'max': 200000}}      # 80 %, still not compacted
        self.assertTrue(self.beat(self.now + 3700)['ok'])
        self.assertIn(MUST, self.agent.got[-1].text)
        self.assertTrue(self.beat(self.now + 7400)['ok'], 'heartbeat never stops')
        self.assertNotIn(MUST, self.agent.got[-1].text)
        self.assertNotIn(ZH, self.agent.got[-1].text)
        self.owner_never_pushed()

    def test_only_the_noticed_beat_carries_a_one_shot_invite(self):
        from agentj.heartbeat import session_key
        self.host.meter_state = {'ctx': {'used': 60000, 'max': 200000}}
        self.assertTrue(self.beat(self.now)['ok'])
        self.assertIsNone(getattr(self.agent.got[-1], 'compact_invite', None))
        self.assertIsNone(self.h.compact_invite)
        self.host.meter_state = {'ctx': {'used': 110000, 'max': 200000}}
        self.assertTrue(self.beat(self.now + 3700)['ok'])
        self.assertIn('agentj capability compact --json', self.agent.got[-1].text)
        tok = self.agent.got[-1].compact_invite
        key = session_key(self.host.agent)
        t = self.now + 3710
        self.assertFalse(self.h.take_invite('wrong', key, t))
        self.assertFalse(self.h.take_invite(tok, 'other-session', t))
        self.assertFalse(self.h.take_invite(tok, key, t + 4000), 'expires')
        self.assertTrue(self.h.take_invite(tok, key, t))
        self.assertFalse(self.h.take_invite(tok, key, t), 'one shot')


# Fixture only; the inherited delivery tests run in test_p156_heartbeat.
for name in dir(Decide):
    if name.startswith('test_') and name not in ContextNotice.__dict__:
        setattr(ContextNotice, name, None)
for name in dir(ContextNotice):
    if name.startswith('test_') and name not in ContextNotice0269.__dict__:
        setattr(ContextNotice0269, name, None)
del Decide

if __name__ == '__main__':
    unittest.main()
