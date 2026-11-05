"""#503: bounded retries only for package-detail reads, never mutation requests."""
import _hermetic  # noqa
import copy
import json
import unittest
from unittest import mock
from agentj import cloud, market, wire
from market_fixtures import env as fixture_env


class ReadRetry(unittest.TestCase):
    def setUp(self):
        self.st = mock.Mock()
        self.detail = {'package': {'name': 'svg-animation-skill', 'version': '1.0.1', 'sha256': 'a' * 64}}

    def test_transient_detail_get_recovers_without_changing_identity(self):
        fields = {'name': 'svg-animation-skill', 'version': '1.0.1'}
        unchanged = copy.deepcopy(fields)
        for status in (500, 502, 503, 504):
            with self.subTest(status=status), mock.patch.object(cloud, 'plaza_pkg_call', side_effect=[
                    (status, {'error': 'temporarily_unavailable'}), (200, self.detail)]) as call, mock.patch.object(market.time, 'sleep') as sleep:
                result = market._call(self.st, 'get', fields)
                self.assertEqual(result, self.detail)
                self.assertEqual(result['package']['version'], '1.0.1')
                self.assertEqual(result['package']['sha256'], 'a' * 64)
                self.assertEqual(call.call_args_list, [mock.call(self.st, 'get', fields)] * 2)
                sleep.assert_called_once_with(.5)
                self.assertEqual(fields, unchanged)

    def test_persistent_five_xx_stops_at_three_attempts(self):
        with mock.patch.object(cloud, 'plaza_pkg_call', return_value=(500, {'error': 'server_error'})) as call, mock.patch.object(market.time, 'sleep') as sleep:
            with self.assertRaisesRegex(market.PlazaError, 'HTTP 500'):
                market._call(self.st, 'get', {'name': 'svg-animation-skill'})
            self.assertEqual(call.call_count, 3)
            self.assertEqual(sleep.call_args_list, [mock.call(.5), mock.call(1.5)])
            self.st.log.assert_any_call('plaza_get_retry_exhausted', count=3, status=500)

    def test_non_retry_status_and_transport_error_are_immediate(self):
        for status in (400, 401, 403, 404, 408, 409, 410, 429, 501, 505):
            with self.subTest(status=status), mock.patch.object(cloud, 'plaza_pkg_call', return_value=(status, {'error': 'fixed'})) as call, mock.patch.object(market.time, 'sleep') as sleep:
                with self.assertRaises(market.PlazaError):
                    market._call(self.st, 'get', {'name': 'svg-animation-skill'})
                self.assertEqual(call.call_count, 1); sleep.assert_not_called()
        with mock.patch.object(cloud, 'plaza_pkg_call', side_effect=cloud.CloudError('network')) as call, mock.patch.object(market.time, 'sleep') as sleep:
            with self.assertRaises(market.PlazaError):
                market._call(self.st, 'get', {'name': 'svg-animation-skill'})
            self.assertEqual(call.call_count, 1); sleep.assert_not_called()

    def test_every_non_get_route_has_no_automatic_retry(self):
        for route in set(cloud.PKG_ROUTES) - {'get'}:
            with self.subTest(route=route), mock.patch.object(cloud, 'plaza_pkg_call', return_value=(503, {'error': 'fixed'})) as call, mock.patch.object(market.time, 'sleep') as sleep:
                with self.assertRaises(market.PlazaError):
                    market._call(self.st, route, {'nonce': 'synthetic'})
                self.assertEqual(call.call_count, 1); sleep.assert_not_called()
        with mock.patch.object(cloud, 'plaza_pkg_call', return_value=(503, {'error': 'fixed'})) as call, mock.patch.object(market.time, 'sleep') as sleep:
            with self.assertRaises(market.PlazaError):
                market._publish_call(self.st, {'nonce': 'synthetic'}, {}, None, None)
            self.assertEqual(call.call_count, 1); sleep.assert_not_called()

    def test_each_attempt_reenters_existing_signed_transport(self):
        with fixture_env() as env:
            seen = []
            def post(url, envelope, **kw):
                seen.append((url, envelope, kw))
                return (502, {'error': 'temporary'}) if len(seen) < 3 else (200, self.detail)
            with mock.patch.object(cloud, 'envelope', wraps=cloud.envelope) as signing, mock.patch.object(market.time, 'sleep'):
                self.assertEqual(market._call(env.st, 'get', {'name': 'svg-animation-skill', 'version': '1.0.1'}, post), self.detail)
            self.assertEqual(len(seen), 3); self.assertEqual(signing.call_count, 3)
            events = [json.loads(line) for line in env.st.log_path.read_text().splitlines()
                      if json.loads(line).get('ev', '').startswith('plaza_get_retry')]
            self.assertEqual([event['count'] for event in events], [1, 2, 3])
            self.assertEqual([event['status'] for event in events], [502, 502, 200])
            for url, envelope, kw in seen:
                self.assertTrue(url.endswith('/v1/host/plaza/pkg/get'))
                body = json.loads(wire.unb64u(envelope['body']))
                self.assertEqual(body['name'], 'svg-animation-skill'); self.assertEqual(body['version'], '1.0.1')
                self.assertEqual(envelope['pk'], wire.b64u(env.st.signing_pub()))
                env.st.signing_key().public_key().verify(wire.unb64u(envelope['sig']),
                    (cloud.CTX_PKG['get'] + '\n' + envelope['body']).encode('ascii'))
