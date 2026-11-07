import unittest
from agentj import latency

class Observation(unittest.TestCase):
    def test_first_once_and_unknown_metrics(self):
        clock = iter((1000, 1200, 1700))
        obs = latency.Observer(clock=lambda: next(clock))
        obs.begin(harness='claude', fresh=True, short=True)
        obs.first_output()
        obs.first_output()
        row = obs.finish('done')
        self.assertEqual(row['t_first_out'] - row['t_recv'], 200)
        self.assertEqual(row['t_done'] - row['t_recv'], 700)
        m = latency.summary([row])['metrics']
        self.assertEqual(m['ttft_first']['n'], 1)
        self.assertIsNone(m['t0']['p95'])
        self.assertIsNone(m['t1']['p95'])
        self.assertIsNone(obs.finish('done'))
    def test_no_output_unknown_not_zero(self):
        obs = latency.Observer(clock=lambda: 1000)
        obs.begin(harness='codex')
        row = obs.finish('failed')
        self.assertNotIn('t_first_out', row)
        self.assertIsNone(latency.summary([row])['metrics']['ttft_later']['p95'])
    def test_no_model_or_body_metadata(self):
        obs = latency.Observer(clock=lambda: 1000)
        obs.begin(harness='claude')
        row = obs.finish('done')
        self.assertNotIn('model', row)
        self.assertNotIn('text', row)
    def test_zero_clock_first_output_is_valid(self):
        row = latency.make(t_recv=0, t_first_out=0, t_done=1, harness='claude', model=None,
                           n=1, fresh=True, short=True)
        self.assertEqual(latency.summary([row])['metrics']['ttft_first']['p95'], 0)
