"""Host-bound rescue report-only path with an isolated signed support transport."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agentj import cloud, preferences, privacy, rescue_kit, rescue_report, support, support_config_fault, support_incident, wire
from agentj.state import State


class FixedRescue(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.enterContext(patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}))
        self.st = State(root / 'state')
        self.st.init(relay='ws://127.0.0.1:1')
        self.binding = {'api': 'http://127.0.0.1:1', 'host_id': 'host_test', 'tenant': {'slug': 'test', 'name': 'QA'},
                        'linked_at': 17, 'last_seq': 0}
        cloud.write_cloud(self.st, self.binding)
        self.recipient = support_incident.account(self.st)
        self.nonce = 'fault_' + 'b' * 22
        self.ticket = 'st_' + 't' * 22
        self.message = 'sm_' + 'm' * 22
        self.path = self.st.root / 'support-product-faults.json'
        self.journal_key = 'host-preferences/' + self.recipient + '/config/invalid-config/' + support_config_fault.SETTING
        self.journal()
        self.prefs = preferences.path()
        self.prefs.parent.mkdir(mode=0o700, parents=True)
        self.write_prefs()
        self.host = SimpleNamespace(st=self.st, config_problem={'key': support_config_fault.SETTING},
                                    stopping=asyncio.Event(), stopped=lambda: False,
                                    preferences=preferences.validate({}))
        self.seat = self.enterContext(patch('agentj.seatcert.refusal', return_value=None))
        self.review = self.enterContext(patch.object(privacy, 'layer2', return_value=privacy.Verdict('ok')))
        self.post = self.enterContext(patch.object(cloud, 'post_json', return_value=(201, {
            'thread': self.ticket, 'message': self.message, 'seq': 1,
            'advice': 'AGENTJ-ACTIONS: restart,reconnect',
        })))
        self.install = self.enterContext(patch.object(support, '_install_id', side_effect=AssertionError('install lane')))
        for name in ('repair', 'diagnose', 'compose', 'session_token', 'wait_reply'):
            self.enterContext(patch.object(rescue_kit, name, side_effect=AssertionError(name)))

    def write_prefs(self, enabled=True):
        self.prefs.write_text(json.dumps({'heartbeat': {'interval_minutes': 1}, 'support': {'auto_report': enabled}}))
        self.prefs.chmod(0o600)

    def journal(self, **extra):
        row = {'at': int(time.time()), 'pending': True, 'nonce': self.nonce, 'pending_thread': None, **extra}
        self.st.write_private(self.path, json.dumps({self.journal_key: row}).encode())

    def rescue(self, **extra):
        fields = {'recipient': self.recipient, 'nonce': self.nonce,
                  'native_failure': 'native_adapter_unavailable', 'lang': 'en'}
        return rescue_report.report_fixed_fault(self.host, **(fields | extra))

    def test_same_event_is_signed_and_confirms_official_receipt_without_actions(self):
        self.assertEqual(self.rescue(), (201, {'thread': self.ticket, 'message': self.message, 'seq': 1}))
        self.post.assert_called_once()
        url, envelope = self.post.call_args.args
        self.assertTrue(url.endswith('/v1/support/messages'))
        Ed25519PublicKey.from_public_bytes(self.st.signing_pub()).verify(
            wire.unb64u(envelope['sig']), (support.CTX_MESSAGE + '\n' + envelope['body']).encode('ascii'))
        body = json.loads(wire.unb64u(envelope['body']))
        self.assertEqual(set(body), {'v', 't', 'channel', 'ts', 'kind', 'body', 'nonce', 'lang'})
        self.assertEqual(body['nonce'], self.nonce)
        self.assertEqual(body['kind'], 'bug')
        lines = body['body'].splitlines()
        self.assertEqual(lines[:4], ['AGENTJ-RESCUE v1 [report-only]', 'component: config',
                                    'stage: validate', 'reason: invalid-heartbeat-interval'])
        self.assertEqual(len(lines), 6)
        self.install.assert_not_called()

    def test_native_refusal_and_ambiguous_send_have_no_rescue_dispatch(self):
        for why in ('network', 'source_denied', 'expired', 'privacy_refused', 'rate_limited', None):
            with self.subTest(why=why):
                self.assertEqual(self.rescue(native_failure=why), (0, {'error': 'rescue_not_authorized'}))
        self.assertEqual(self.rescue(nonce='fault_' + 'c'*22), (0, {'error': 'rescue_not_authorized'}))
        self.journal(pending_thread=self.ticket, thread=self.ticket)
        self.assertEqual(self.rescue(), (0, {'error': 'rescue_not_authorized'}))
        self.review.assert_not_called()
        self.post.assert_not_called()

    def test_opt_out_stop_and_lost_binding_block_even_after_review(self):
        for change in (lambda: self.host.stopping.set(), lambda: self.write_prefs(False),
                       lambda: cloud.write_cloud(self.st, {**self.binding, 'linked_at': 18})):
            with self.subTest(change=change):
                self.review.side_effect = lambda *a, **k: (change(), privacy.Verdict('ok'))[1]
                self.assertEqual(self.rescue(), (0, {'error': 'rescue_not_authorized'}))
                self.host.stopping.clear()
                self.write_prefs()
                cloud.write_cloud(self.st, self.binding)
        self.post.assert_not_called()

    def test_seat_refusal_and_mandatory_privacy_failure_cannot_switch_transport(self):
        self.seat.return_value = 'seat unavailable'
        self.assertEqual(self.rescue(), (0, {'error': 'rescue_seat_required'}))
        self.seat.return_value = None
        for verdict in (privacy.Verdict('blocked', kind='credential'),
                        privacy.Verdict('unavailable', reason='privacy-check/peer_unverified')):
            self.review.return_value = verdict
            self.assertEqual(self.rescue(), (0, {'error': 'rescue_privacy_refused'}))
        self.seat.side_effect = (None, 'seat revoked during review')
        self.review.return_value = privacy.Verdict('ok')
        self.assertEqual(self.rescue(), (0, {'error': 'rescue_seat_required'}))
        self.post.assert_not_called()
        self.install.assert_not_called()

    def test_independent_fixed_report_survives_optional_model_outage(self):
        self.review.return_value = privacy.Verdict('unavailable', reason='privacy-check/backend_unavailable')
        self.assertEqual(self.rescue(), (201, {'thread': self.ticket, 'message': self.message, 'seq': 1}))
        self.post.assert_called_once()

    def test_transport_uncertainty_malformed_receipt_and_rate_limit_never_resend(self):
        for status, result, expected in ((201, {'thread': self.ticket, 'seq': 1}, 'rescue_delivery_unconfirmed'),
                                         (429, {'error': 'rate_limited'}, 'rescue_rate_limited'),
                                         (422, {'error': 'secret_detected'}, 'rescue_refused'),
                                         (503, {}, 'rescue_delivery_unconfirmed')):
            self.post.reset_mock()
            self.post.return_value = (status, result)
            self.assertEqual(self.rescue()[1], {'error': expected})
            self.post.assert_called_once()
        self.post.reset_mock()
        self.post.side_effect = cloud.CloudError('timeout')
        self.assertEqual(self.rescue(), (0, {'error': 'rescue_delivery_unconfirmed'}))
        self.post.assert_called_once()
