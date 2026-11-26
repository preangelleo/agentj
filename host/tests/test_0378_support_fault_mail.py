"""Offline Host consent and signature contract for the fixed support mailbox."""
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

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentj import cloud, contacts, controls, mail, preferences, support_config_fault, support_fault_mail as fm, support_incident, wire
from agentj.state import State
from test_p57_passkey import Authenticator, pk_record


class FaultMailConsent(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.enterContext(patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}))
        self.st = State(root / 'state')
        self.st.init(relay='ws://127.0.0.1:1')
        self.link = {'api': 'http://127.0.0.1:1', 'host_id': 'host_test', 'tenant': {'slug': 'test', 'name': 'QA'},
                     'linked_at': 17, 'last_seq': 0}
        cloud.write_cloud(self.st, self.link)
        self.address = 'owner@example.test'
        self.write_owner([self.address])
        self.cfg = {**mail.blank_config(), 'enabled': True, 'address': self.address, 'username': self.address,
                    'imap': {'host': 'imap.example.test', 'port': 993, 'security': 'ssl'},
                    'smtp': {'host': 'smtp.example.test', 'port': 465, 'security': 'ssl'},
                    'generation': 'a' * 32, 'allow': [{'addr': 'separate@example.test', 'role': 'owner'}]}
        mail.save_config(self.st, self.cfg)
        self.phone_key = Ed25519PrivateKey.generate()
        self.static = os.urandom(32)
        self.device = self.st.add_device(self.static, 'Test paired remote', self.phone_key.public_key().public_bytes_raw())
        self.auth = Authenticator()
        self.assertTrue(self.st.set_passkey(self.static, pk_record(self.auth)))
        self.desk = Ed25519PrivateKey.generate()
        self.pin = self.desk.public_key().public_bytes_raw()
        self.consent = fm.MailConsent(self.st, self.pin)
        prefs = preferences.path()
        prefs.parent.mkdir(mode=0o700, parents=True)
        prefs.write_text('{"heartbeat":{"interval_minutes":1},"support":{"auto_report":true}}')
        prefs.chmod(0o600)
        self.host = SimpleNamespace(st=self.st, config_problem={'key': support_config_fault.SETTING},
                                    stopping=asyncio.Event(), stopped=lambda: False,
                                    preferences=preferences.validate({}))
        self.event = 'fault_' + 'h' * 22
        self.journal()

    def write_owner(self, addresses):
        book = contacts.read(self.st)
        contacts.set_owner(book, {'name': 'QA', 'emails': addresses})
        contacts.write(self.st, book)

    def journal(self, **overrides):
        account = support_incident.account(self.st)
        key = 'host-preferences/' + account + '/config/invalid-config/' + support_config_fault.SETTING
        row = {'at': int(time.time()), 'pending': True, 'nonce': self.event, 'pending_thread': None}
        self.st.write_private(self.st.root / 'support-product-faults.json', json.dumps({key: row | overrides}).encode())

    def sign(self, key, context, body):
        return cloud.envelope(context, json.loads(controls.canonical_json(body)), key)

    def prove(self, offer, *, context=fm.PROOF_CONTEXT, key=None, **extras):
        proof = {'v': 1, 't': 'fault_mailbox_proof', 'aud': fm.AUDIENCE, 'channel': offer['channel'],
                 'binding': offer['binding'], 'address_sha256': fm.mailbox_address_digest(offer['sender'], offer['binding']),
                 'issued_at': offer['issued_at'], 'expires_at': offer['expires_at']}
        return self.sign(key or self.desk, context, proof | extras)

    def approve(self, offer, *, signed_offer=None, uv=True, **proof):
        signature = self.phone_key.sign(fm.owner_approval_bytes(signed_offer or offer))
        fa = self.auth.assert_(fm.owner_approval_challenge(offer), b'test', nonce=b'test', flags=5 if uv else 1)
        return self.consent.approve(self.device, self.prove(offer, **proof), wire.b64u(signature), fa)

    def enable(self):
        offer = self.consent.begin(self.device)
        self.assertEqual(self.approve(offer), offer['binding'])
        return offer

    def receipt(self, offer, status, **extras):
        row = {'v': 1, 't': 'fault_mail_receipt', 'channel': offer['channel'], 'ts': int(time.time()),
               'binding': offer['binding'], 'event': self.event, 'status': status}
        return self.sign(self.desk, fm.RECEIPT_CONTEXT, row | extras)

    def test_paired_signed_consent_is_consumed_and_restores_only_locally(self):
        offer = self.enable()
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_offer'):
            self.approve(offer)
        path = self.st.root / 'support-fault-mail-consent.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        private = wire.unb64u(json.loads(path.read_text())['body'])
        self.assertNotIn(self.address.encode(), private)
        restarted = fm.MailConsent(self.st, self.pin)
        draft = restarted.prepare_fault(self.host, self.event)
        self.assertEqual((draft.sender, draft.recipient), (self.address, 'feedback@agentj.app'))
        sig = wire.unb64u(draft.envelope['sig'])
        self.st.signing_key().public_key().verify(sig, f'{fm.FAULT_CONTEXT}\n{draft.envelope["body"]}'.encode())
        body = json.loads(wire.unb64u(draft.envelope['body']))
        self.assertEqual(set(body), {'v', 't', 'channel', 'ts', 'binding', 'event', 'diagnostic'})
        self.assertEqual(set(body['diagnostic']), {'version', 'platform', 'component', 'stage', 'reason'})
        self.assertEqual(body['diagnostic']['reason'], 'invalid-heartbeat-interval')
        self.assertEqual((body['binding'], body['event']), (offer['binding'], self.event))
        self.assertNotIn(self.address, json.dumps(body))
        self.assertNotIn('separate@example.test', json.dumps(body))

    def test_valid_desk_pin_cannot_enable_production_enrollment_yet(self):
        with patch.dict(os.environ, {fm.DESK_KEY_ENV: wire.b64u(self.pin)}):
            self.assertIsNone(fm.configured(self.st))

    def test_no_desk_pin_or_purpose_specific_proof_cannot_grant_mail(self):
        with self.assertRaisesRegex(fm.MailConsentError, 'no_desk_pin'):
            fm.MailConsent(self.st, b'')
        offer = self.consent.begin(self.device)
        for forged in ({'key': Ed25519PrivateKey.generate()}, {'context': fm.RECEIPT_CONTEXT},
                       {'aud': 'agentj-news'}, {'channel': 'another-host'}, {'binding': 'f' * 32},
                       {'address_sha256': fm.mailbox_address_digest('other@example.test', offer['binding'])},
                       {'verified': True}, {'expires_at': offer['issued_at']}, {'issued_at': offer['expires_at']}):
            with self.subTest(forged=forged), self.assertRaises(fm.MailConsentError):
                self.approve(offer, **forged)
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_consent'):
            self.consent.prepare_fault(self.host, self.event)

    def test_changing_shown_destination_or_missing_uv_cannot_enable(self):
        offer = self.consent.begin(self.device)
        with self.assertRaisesRegex(fm.MailConsentError, 'owner_approval_invalid'):
            self.approve(offer, signed_offer={**offer, 'recipient': 'other@example.test'})
        with self.assertRaisesRegex(fm.MailConsentError, 'owner_approval_invalid'):
            self.approve(offer, uv=False)
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_consent'):
            self.consent.prepare_fault(self.host, self.event)

    def test_other_owner_addresses_and_smtp_alias_do_not_count(self):
        mail.save_config(self.st, {**self.cfg, 'username': 'separate@example.test'})
        with self.assertRaisesRegex(fm.MailConsentError, 'mailbox_route_changed'):
            self.consent.begin(self.device)
        self.write_owner([self.address, 'separate@example.test'])
        with self.assertRaisesRegex(fm.MailConsentError, 'no_single_owner_mailbox'):
            self.consent.begin(self.device)

    def test_contact_and_config_rebinds_invalidate_even_if_old_values_return(self):
        self.enable()
        self.write_owner(['separate@example.test'])
        self.write_owner([self.address])
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_consent'):
            self.consent.prepare_fault(self.host, self.event)
        self.enable()
        mail.save_config(self.st, {**self.cfg, 'enabled': False})
        mail.save_config(self.st, self.cfg)
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_consent'):
            self.consent.prepare_fault(self.host, self.event)

    def test_account_rebind_pairing_revision_and_revocation_invalidate(self):
        self.enable()
        cloud.write_cloud(self.st, {**self.link, 'last_seq': 10})
        self.consent.prepare_fault(self.host, self.event)
        cloud.write_cloud(self.st, {**self.link, 'linked_at': 18})
        self.journal()
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_consent'):
            self.consent.prepare_fault(self.host, self.event)
        self.enable()
        self.st.add_device(self.static, 'Re-paired', self.phone_key.public_key().public_bytes_raw())
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_consent'):
            self.consent.prepare_fault(self.host, self.event)
        self.enable()
        self.consent.revoke()
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_consent'):
            fm.MailConsent(self.st, self.pin).prepare_fault(self.host, self.event)

    def test_switched_or_completed_fault_never_becomes_a_draft(self):
        self.enable()
        for proposed in ('fault_' + 'j' * 22, 'customer text'):
            with self.assertRaisesRegex(fm.MailConsentError, 'no_current_fault'):
                self.consent.prepare_fault(self.host, proposed)
        self.journal(thread='st_' + 't' * 22)
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_fault'):
            self.consent.prepare_fault(self.host, self.event)
        self.journal()
        self.host.config_problem = {}
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_fault'):
            self.consent.prepare_fault(self.host, self.event)

    def test_draft_is_rechecked_against_current_binding_and_recipient(self):
        self.enable()
        draft = self.consent.prepare_fault(self.host, self.event)
        self.consent.check_draft(self.host, self.event, draft)
        with self.assertRaisesRegex(fm.MailConsentError, 'bad_draft'):
            bad = fm.FaultMailDraft(draft.sender, 'alternate@example.test', draft.envelope)
            self.consent.check_draft(self.host, self.event, bad)
        sent_at = json.loads(wire.unb64u(draft.envelope['body']))['ts']
        with patch.object(fm.time, 'time', return_value=sent_at + 121):
            with self.assertRaisesRegex(fm.MailConsentError, 'stale_draft'):
                self.consent.check_draft(self.host, self.event, draft)
        self.consent.revoke()
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_consent'):
            self.consent.check_draft(self.host, self.event, draft)

    def test_queued_and_ticketed_receipts_have_distinct_exact_shapes(self):
        offer = self.enable()
        queued = self.receipt(offer, 'queued')
        self.assertEqual(self.consent.read_receipt(self.host, self.event, queued), {'status': 'queued'})
        ticket = {'thread': 'st_' + 't' * 22, 'message': 'sm_' + 'm' * 22, 'seq': 1}
        signed = self.receipt(offer, 'ticketed', **ticket)
        self.assertEqual(self.consent.read_receipt(self.host, self.event, signed), {'status': 'ticketed', **ticket})
        self.host.config_problem = {}
        self.assertEqual(self.consent.read_receipt(self.host, self.event, signed), {'status': 'ticketed', **ticket})
        for bad in (self.receipt(offer, 'queued', **ticket), self.receipt(offer, 'ticketed', **(ticket | {'seq': True})),
                    self.receipt(offer, 'queued', event='fault_' + 'j' * 22), self.receipt(offer, 'queued', binding='f' * 32),
                    self.receipt(offer, 'queued', ts=int(time.time()) - 172800), self.prove(offer),
                    {**signed, 'sig': wire.b64u(bytes(64))}):
            with self.assertRaises(fm.MailConsentError):
                self.consent.read_receipt(self.host, self.event, bad)
        self.consent.revoke()
        with self.assertRaisesRegex(fm.MailConsentError, 'no_current_consent'):
            self.consent.read_receipt(self.host, self.event, signed)

    def test_smtp_requires_desk_activation_and_keeps_an_uncertain_write_one_shot(self):
        import smtplib
        from unittest.mock import Mock
        from agentj import privacy
        offer = self.enable()
        with patch.object(privacy, 'layer2', return_value=privacy.Verdict('ok')), \
                patch.object(fm.seatcert, 'refusal', return_value=None), patch.object(mail, 'load_secret', return_value='synthetic'):
            conn = Mock()
            open_smtp = Mock(return_value=conn)
            self.assertEqual(self.consent.send_fault(self.host, self.event, smtp_open=open_smtp), 'unavailable')
            open_smtp.assert_not_called()
            active = {'v': 1, 't': 'fault_mailbox_active', 'aud': fm.AUDIENCE, 'channel': offer['channel'],
                      'binding': offer['binding'], 'ts': int(time.time())}
            self.consent.confirm_activation(self.sign(self.desk, fm.ACTIVE_CONTEXT, active))
            conn.send_message.side_effect = smtplib.SMTPServerDisconnected()
            self.assertEqual(self.consent.send_fault(self.host, self.event, smtp_open=open_smtp), 'unconfirmed')
            self.assertEqual(fm.MailConsent(self.st, self.pin).send_fault(self.host, self.event, smtp_open=open_smtp), 'unconfirmed')
            conn.send_message.assert_called_once()
            sent = conn.send_message.call_args
            self.assertEqual(sent.kwargs, {'from_addr': self.address, 'to_addrs': ['feedback@agentj.app']})
            self.assertEqual(sent.args[0]['Subject'], fm.MAIL_MARKER)
            self.assertIsNone(sent.args[0]['Reply-To'])
            self.assertEqual(len(self.consent.withdraw()), 1)
            self.assertEqual(self.consent.send_fault(self.host, self.event, smtp_open=open_smtp), 'unavailable')

    def test_signed_mail_ticket_replaces_queued_receipt_in_the_same_second(self):
        from email.message import EmailMessage
        offer = self.enable()
        stamp = int(time.time())

        def message(status, **extra):
            m = EmailMessage()
            m['Subject'], m['Auto-Submitted'] = fm.RECEIPT_SUBJECT, 'auto-generated'
            m.set_content(json.dumps(self.receipt(offer, status, ts=stamp, **extra)))
            return m

        self.assertEqual(self.consent.cache_receipt(self.host, message('queued')), {'status': 'queued'})
        self.assertIsNone(self.consent.cached_ticket(self.host, self.event))
        confirmed = {'thread': 'st_' + 't' * 22, 'message': 'sm_' + 'm' * 22, 'seq': 1}
        self.assertEqual(self.consent.cache_receipt(self.host, message('ticketed', **confirmed)), {'status': 'ticketed', **confirmed})
        self.assertEqual(self.consent.cached_ticket(self.host, self.event), confirmed)
        self.assertIsNone(self.consent.cache_receipt(self.host, message('queued')))
        self.consent.withdraw()
        self.assertIsNone(self.consent.cached_ticket(self.host, self.event))
