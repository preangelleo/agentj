"""Local consent and wire objects for verified support mail. No transport or default trust root."""
from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
import hashlib
import json
import os
import re
import secrets
import smtplib
import stat
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__, cloud, contacts, controls, mail, passkey, privacy, privacy_host, seatcert, support, support_config_fault, support_incident, wire
from .rescue_kit import os_kind

AUDIENCE = 'agentj-support-fault-mail-v1'
PROOF_CONTEXT = 'agentj-support-mailbox-proof-v1'
OWNER_CONTEXT = 'agentj-phone-fault-mail-consent-v1'
FAULT_CONTEXT = 'agentj-host-fault-mail-v1'
RECEIPT_CONTEXT = 'agentj-support-fault-mail-receipt-v1'
VERIFY_CONTEXT = 'agentj-support-mailbox-verify-v1'
ACTIVATE_CONTEXT = 'agentj-support-mailbox-activate-v1'
ACTIVE_CONTEXT = 'agentj-support-mailbox-active-v1'
REVOKE_CONTEXT = 'agentj-support-mailbox-revoke-v1'
MAIL_MARKER = 'Agent J fixed fault v1'
RECEIPT_SUBJECT = 'Agent J fixed fault receipt v1'
# Enable only after the production desk can verify direct delivery.
ENABLED = False
DESK_KEY_ENV = 'AGENTJ_SUPPORT_FAULT_MAIL_DESK_PUBLIC_KEY'
_STATE_CONTEXT = 'agentj-local-fault-mail-consent-v1'
_ADDRESS_LABEL = 'agentj-support-fault-mail-address-v1'
_APPROVAL_KIND = 'fault_mail_consent'
_FILE = 'support-fault-mail-consent.json'
_OFFER_TTL = 600
_DRAFT_TTL = 120
_RECEIPT_TTL = 86400
_RECIPIENT = 'feedback@agentj.app'
_HEX_32 = re.compile(r'[0-9a-f]{32}')
_EVENT = re.compile(r'fault_[A-Za-z0-9_-]{22}')
_THREAD = re.compile(r'st_[A-Za-z0-9_-]{22}')
_MESSAGE = re.compile(r'sm_[A-Za-z0-9_-]{22}')
_VERSION = re.compile(r'[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:(?:a|b|rc)[0-9]{1,5})?(?:\.dev[0-9]{1,6})?')


class MailConsentError(ValueError):
    """Closed reason for a refused local operation. No address or envelope is included."""


def _canonical(value) -> bytes:
    return controls.canonical_json(value).encode('utf-8')


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def mailbox_address_digest(address: str, binding: str) -> str:
    return _sha(f'{_ADDRESS_LABEL}\n{binding}\n{address}'.encode())


def _b64(value, length: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 12000:
        raise MailConsentError('bad_envelope')
    raw = wire.unb64u(value)
    if (length is not None and len(raw) != length) or wire.b64u(raw) != value:
        raise MailConsentError('bad_envelope')
    return raw


def _open(envelope: dict, key: bytes, context: str) -> dict:
    if not isinstance(envelope, dict) or set(envelope) != {'pk', 'body', 'sig'}:
        raise MailConsentError('bad_envelope')
    try:
        if _b64(envelope['pk'], 32) != key:
            raise MailConsentError('bad_signature')
        raw = _b64(envelope['body'])
        sig = _b64(envelope['sig'], 64)
        Ed25519PublicKey.from_public_bytes(key).verify(sig, f'{context}\n{envelope["body"]}'.encode('ascii'))
        value = json.loads(raw)
        if not isinstance(value, dict) or _canonical(value) != raw:
            raise MailConsentError('bad_envelope')
    except InvalidSignature:
        raise MailConsentError('bad_signature') from None
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, MailConsentError):
            raise
        raise MailConsentError('bad_envelope') from None
    return value


def _seal(st, payload: dict, context: str) -> dict:
    return cloud.envelope(context, json.loads(_canonical(payload)), st.signing_key())


def owner_approval_bytes(offer: dict) -> bytes:
    return OWNER_CONTEXT.encode('ascii') + b'\n' + _canonical(offer)


def owner_approval_challenge(offer: dict) -> bytes:
    binding = offer['binding']
    return passkey.elevate_challenge(offer['channel'], offer['device'], binding, _APPROVAL_KIND,
                                    binding, _sha(_canonical(offer)))


def _private_stat(path, *, dir_ok=False) -> os.stat_result:
    info = path.lstat()
    file_kind = stat.S_ISDIR(info.st_mode) if dir_ok else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not file_kind or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise MailConsentError('private_state_unavailable')
    return info


def _file_generation(info: os.stat_result) -> list[str]:
    # Slot 0 has only second-resolution `updated`. The file identity also rejects an unseen A→B→A.
    return [str(info.st_dev), str(info.st_ino), str(info.st_ctime_ns)]


@dataclass(frozen=True, repr=False)
class _Scope:
    address: str
    channel: str
    device: str
    signing_key: bytes
    passkey: dict
    generation: str


@dataclass(frozen=True, repr=False)
class FaultMailDraft:
    """Trusted local inputs for a future authorized, one-recipient SMTP adapter."""

    sender: str
    recipient: str
    envelope: dict


def configured(st):
    """No fallback is enabled unless the running service has the dedicated desk pin."""
    pin = os.environ.get(DESK_KEY_ENV) if ENABLED else None
    if not pin:
        return None
    try:
        return MailConsent(st, _b64(pin, 32))
    except (TypeError, ValueError):
        return None


class MailConsent:
    def __init__(self, st, desk_key: bytes):
        if not isinstance(desk_key, bytes) or len(desk_key) != 32:
            raise MailConsentError('no_desk_pin')
        self.st = st
        self._desk_key = desk_key

    def _scope(self, device: str) -> _Scope:
        st = self.st
        if not isinstance(device, str) or not device or controls.estop_state(st)['on']:
            raise MailConsentError('no_live_binding')
        try:
            path = st.root / contacts.DIR / contacts.FILE
            _private_stat(path.parent, dir_ok=True)
            before = _file_generation(_private_stat(path))
            book = contacts.read(st)
            matches = [c for c in book['contacts'] if c['id'] == contacts.OWNER_ID]
            if (len(matches) != 1 or matches[0] is not book['contacts'][0]
                    or before != _file_generation(_private_stat(path))):
                raise MailConsentError('no_single_owner_mailbox')
            addresses = matches[0].get('emails')
            if not isinstance(addresses, list) or len(addresses) != 1 or not isinstance(addresses[0], dict):
                raise MailConsentError('no_single_owner_mailbox')
            address = addresses[0].get('v')
            if not isinstance(address, str) or not address or contacts.norm_email(address) != address:
                raise MailConsentError('no_single_owner_mailbox')
            mail_path = st.root / mail.DIR / mail.CONFIG
            _private_stat(mail_path.parent, dir_ok=True)
            mail_before = _file_generation(_private_stat(mail_path))
            cfg = mail.read_config(st)
            if (not cfg['enabled'] or cfg['address'] != address or cfg['username'] not in ('', address)
                    or cfg['smtp']['security'] not in ('ssl', 'starttls') or not _HEX_32.fullmatch(cfg['generation'])
                    or mail_before != _file_generation(_private_stat(mail_path))):
                raise MailConsentError('mailbox_route_changed')
            account = support_incident.account(st)
            record = st.devices().get(device) or {}
            revision, key, pk = record.get('pair_revision'), st.sign_key(device), st.passkey_of_device(device)
            if not account or not key or not pk or not isinstance(revision, str) or not _HEX_32.fullmatch(revision):
                raise MailConsentError('no_live_binding')
            generation = _sha(_canonical((account, before, address, mail_before, cfg['generation'],
                                           mail.credential_destination(cfg), revision, wire.b64u(key), pk['id'], pk['spki'])))
            return _Scope(address, cloud.channel_of(st), device, key, pk, generation)
        except (OSError, KeyError, TypeError, ValueError, cloud.CloudError) as exc:
            if isinstance(exc, MailConsentError):
                raise
            raise MailConsentError('private_state_unavailable') from None

    def _read(self) -> dict:
        path = self.st.root / _FILE
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {}
        except OSError:
            raise MailConsentError('private_state_unavailable') from None
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or info.st_mode & 0o077 or info.st_size > 12000):
                raise MailConsentError('private_state_unavailable')
            try:
                envelope = json.loads(stream.read(12001))
            except (ValueError, UnicodeError, RecursionError):
                raise MailConsentError('private_state_unavailable') from None
        return _open(envelope, self.st.signing_pub(), _STATE_CONTEXT)

    def _write(self, state: dict) -> None:
        self.st.write_private(self.st.root / _FILE, _canonical(_seal(self.st, state, _STATE_CONTEXT)))

    def _offer(self, scope: _Scope, binding: str, issued: int) -> dict:
        return {'v': 1, 'decision': 'enable', 'aud': AUDIENCE, 'channel': scope.channel, 'device': scope.device,
                'binding': binding, 'sender': scope.address, 'recipient': _RECIPIENT, 'generation': scope.generation,
                'issued_at': issued, 'expires_at': issued + _OFFER_TTL}

    def begin(self, device: str) -> dict:
        """Prepare one target for future mailbox verification and paired approval. Sends nothing."""
        with self.st.file_lock('support-fault-mail'):
            state = self._read()
            scope = self._scope(device)
            binding, issued = secrets.token_hex(16), int(time.time())
            self._write({**state, 'pending': {'binding': binding, 'issued_at': issued,
                                             'device': device, 'generation': scope.generation}})
            return self._offer(scope, binding, issued)

    def _proof(self, proof: dict, offer: dict, now: int) -> None:
        body = _open(proof, self._desk_key, PROOF_CONTEXT)
        issued, expires = body.get('issued_at'), body.get('expires_at')
        if (type(issued) is not int or type(expires) is not int or not offer['issued_at'] <= issued <= now < expires
                or expires > offer['expires_at']):
            raise MailConsentError('stale_mailbox_proof')
        expected = {'v': 1, 't': 'fault_mailbox_proof', 'aud': AUDIENCE, 'channel': offer['channel'],
                    'binding': offer['binding'], 'address_sha256': mailbox_address_digest(offer['sender'], offer['binding']),
                    'issued_at': issued, 'expires_at': expires}
        if _canonical(body) != _canonical(expected):
            raise MailConsentError('wrong_mailbox_proof')

    def approve(self, device: str, proof: dict, owner_signature: str, assertion: dict) -> str:
        """Consume the current purpose-specific proof and signed paired decision together."""
        with self.st.file_lock('support-fault-mail'):
            state = self._read()
            scope = self._scope(device)
            pending = state.get('pending', {})
            if pending.get('device') != device or pending.get('generation') != scope.generation:
                raise MailConsentError('no_current_offer')
            offer = self._offer(scope, pending['binding'], pending['issued_at'])
            now = int(time.time())
            if not offer['issued_at'] <= now < offer['expires_at']:
                raise MailConsentError('no_current_offer')
            self._proof(proof, offer, now)
            try:
                Ed25519PublicKey.from_public_bytes(scope.signing_key).verify(_b64(owner_signature, 64), owner_approval_bytes(offer))
                fields = passkey.approval_fields(assertion)
                if fields['id'] != scope.passkey['id']:
                    raise MailConsentError('wrong_passkey')
                count = passkey.verify_assertion(scope.passkey, fields, owner_approval_challenge(offer))
            except (InvalidSignature, passkey.PasskeyError, ValueError):
                raise MailConsentError('owner_approval_invalid') from None
            current = self._scope(device)
            if current.generation != scope.generation:
                raise MailConsentError('no_current_offer')
            if count and (count <= current.passkey.get('sc', 0) or not self.st.passkey_count(device, fields['id'], count)):
                raise MailConsentError('owner_approval_invalid')
            self._write({'active': {'binding': offer['binding'], 'device': device, 'generation': scope.generation,
                                    'desk': _sha(self._desk_key)},
                         'withdrawn': state.get('withdrawn', [])})
            self.st.log('support_mail_consent', result='enabled', device=device)
            return offer['binding']

    def revoke(self) -> None:
        """Invalidate both active consent and outstanding verification, including after restart."""
        return self.withdraw()

    def _bound(self) -> tuple[_Scope, str]:
        active = self._read().get('active') or {}
        if not active.get('device') or active.get('desk') != _sha(self._desk_key):
            raise MailConsentError('no_current_consent')
        scope = self._scope(active['device'])
        if scope.generation != active.get('generation'):
            raise MailConsentError('no_current_consent')
        return scope, active['binding']

    def _pending_event(self, host, event):
        if host.st is not self.st or not isinstance(event, str) or not _EVENT.fullmatch(event):
            raise MailConsentError('no_current_fault')
        row = support_incident._observed_issue(self.st, support_config_fault.SETTING)
        if (row.get('pending') is not True or row.get('nonce') != event or row.get('thread') is not None
                or row.get('pending_thread') is not None):
            raise MailConsentError('no_current_fault')

    def _fault_live(self, host, event):
        self._pending_event(host, event)
        if support_config_fault.observed(host) != 'fault' or not support_incident.enabled(self.st, 'config'):
            raise MailConsentError('no_current_fault')

    def _fault_payload(self, scope, binding, event, timestamp):
        if not _VERSION.fullmatch(__version__):
            raise MailConsentError('version_unrecognized')
        platform_kind = os_kind()
        if platform_kind not in ('linux', 'macos', 'wsl', 'windows'):
            platform_kind = 'other'
        diagnostic = {'version': __version__, 'platform': platform_kind, 'component': 'config',
                      'stage': 'validate', 'reason': 'invalid-heartbeat-interval'}
        return {'v': 1, 't': 'fault_mail', 'channel': scope.channel, 'ts': timestamp,
                'binding': binding, 'event': event, 'diagnostic': diagnostic}

    def prepare_fault(self, host, event: str) -> FaultMailDraft:
        """Create a signed draft for the existing pending witnessed fault. Sends nothing."""
        with self.st.file_lock('support-fault-mail'):
            self._fault_live(host, event)
            scope, binding = self._bound()
            payload = self._fault_payload(scope, binding, event, int(time.time()))
            return FaultMailDraft(scope.address, _RECIPIENT, _seal(self.st, payload, FAULT_CONTEXT))

    def check_draft(self, host, event: str, draft: FaultMailDraft) -> None:
        """Repeat the local checks for a future adapter immediately before SMTP DATA."""
        with self.st.file_lock('support-fault-mail'):
            self._fault_live(host, event)
            scope, binding = self._bound()
            if not isinstance(draft, FaultMailDraft) or (draft.sender, draft.recipient) != (scope.address, _RECIPIENT):
                raise MailConsentError('bad_draft')
            body = _open(draft.envelope, self.st.signing_pub(), FAULT_CONTEXT)
            timestamp = body.get('ts')
            if type(timestamp) is not int or not 0 <= int(time.time()) - timestamp < _DRAFT_TTL:
                raise MailConsentError('stale_draft')
            if _canonical(body) != _canonical(self._fault_payload(scope, binding, event, timestamp)):
                raise MailConsentError('bad_draft')

    def read_receipt(self, host, event: str, envelope: dict) -> dict:
        """Verify and project a receipt. Only a signed ticketed receipt carries a ticket."""
        with self.st.file_lock('support-fault-mail'):
            self._pending_event(host, event)
            scope, binding = self._bound()
            body = _open(envelope, self._desk_key, RECEIPT_CONTEXT)
            timestamp, status = body.get('ts'), body.get('status')
            if type(timestamp) is not int or not 0 <= int(time.time()) - timestamp <= _RECEIPT_TTL:
                raise MailConsentError('stale_receipt')
            want = {'v': 1, 't': 'fault_mail_receipt', 'channel': scope.channel, 'ts': timestamp,
                    'binding': binding, 'event': event, 'status': status}
            result = {'status': status}
            if status == 'ticketed':
                thread, message, seq = body.get('thread'), body.get('message'), body.get('seq')
                if (not isinstance(thread, str) or not _THREAD.fullmatch(thread) or not isinstance(message, str)
                        or not _MESSAGE.fullmatch(message) or type(seq) is not int or not 1 <= seq < 2 ** 53):
                    raise MailConsentError('bad_receipt')
                result.update(thread=thread, message=message, seq=seq)
                want.update(thread=thread, message=message, seq=seq)
            elif status != 'queued':
                raise MailConsentError('bad_receipt')
            if _canonical(body) != _canonical(want):
                raise MailConsentError('bad_receipt')
            return result

    def _request(self, context, channel, binding, **fields):
        body = {'v': 1, 'aud': AUDIENCE, 'channel': channel, 'binding': binding, 'ts': int(time.time()), **fields}
        return _seal(self.st, body, context)

    def verification_request(self, device, code=None):
        """Sign only for the current paired device and its own pending mailbox."""
        with self.st.file_lock('support-fault-mail'):
            scope = self._scope(device)
            pending = self._read().get('pending', {})
            expires = pending.get('issued_at', 0) + _OFFER_TTL
            if (pending.get('device') != device or pending.get('generation') != scope.generation
                    or time.time() >= expires):
                raise MailConsentError('no_current_offer')
            if code is None:
                fields = {'address': scope.address, 'expires_at': expires}
            elif isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_-]{32}', code):
                fields = {'code': code}
            else:
                raise MailConsentError('bad_mailbox_code')
            return self._request(VERIFY_CONTEXT, scope.channel, pending['binding'], **fields)

    def activation_request(self):
        with self.st.file_lock('support-fault-mail'):
            scope, binding = self._bound()
            return self._request(ACTIVATE_CONTEXT, scope.channel, binding)

    def confirm_activation(self, signed):
        """Record remote enablement only when the pinned authority confirms the exact binding."""
        with self.st.file_lock('support-fault-mail'):
            scope, binding = self._bound()
            state = self._read()
            value = _open(signed, self._desk_key, ACTIVE_CONTEXT)
            timestamp = value.get('ts')
            if (type(timestamp) is not int or not 0 <= int(time.time()) - timestamp < _OFFER_TTL
                    or value != {'v': 1, 't': 'fault_mailbox_active', 'aud': AUDIENCE, 'channel': scope.channel,
                                 'binding': binding, 'ts': timestamp}):
                raise MailConsentError('bad_activation_receipt')
            self._write({**state, 'active': {**state['active'], 'desk_active': True}})

    def withdraw(self):
        """Disable locally first and retain the exact generation for a retryable desk revocation."""
        with self.st.file_lock('support-fault-mail'):
            state = self._read()
            pending = state.get('pending') or {}
            active = state.get('active') or {}
            bindings = sorted(set(state.get('withdrawn', ())) | {
                row['binding'] for row in (active, pending) if row.get('binding')
            })
            self._write({'withdrawn': bindings})
            self.st.log('support_mail_consent', result='revoked')
            return [self._request(REVOKE_CONTEXT, cloud.channel_of(self.st), binding) for binding in bindings]

    def send_fault(self, host, event, *, smtp_open=None):
        """Submit a fixed diagnostic once. An uncertain SMTP result is held for reconciliation."""
        try:
            draft = self.prepare_fault(host, event)
            body = _canonical(draft.envelope).decode('ascii')
            diagnostic = _open(draft.envelope, self.st.signing_pub(), FAULT_CONTEXT)['diagnostic']
            text = _canonical(diagnostic).decode('ascii')
            review, hits = support.prepare(text, None)
            if hits or review != {'body': text} or privacy_host.send_refusal() or seatcert.refusal(self.st, 'support'):
                return 'refused'
            verdict = privacy.layer2(review, gate='feedback', _host=True)
            if verdict.status != 'ok' and not privacy.optional_review(verdict):
                return 'refused'
            with self.st.file_lock('support-fault-mail'):
                self._fault_live(host, event)
                scope, binding = self._bound()
                state = self._read()
                if not state['active'].get('desk_active'):
                    return 'unavailable'
                prior = state.get('dispatch') or {}
                if prior.get('event') == event:
                    return prior['status']
                cfg = mail.read_config(self.st)
                if cfg['address'] != scope.address:
                    return 'refused'
                password = mail.load_secret(self.st, scope.address)
            if not password:
                return 'unavailable'
            msg = EmailMessage(policy=SMTP)
            msg['From'], msg['To'], msg['Subject'] = draft.sender, draft.recipient, MAIL_MARKER
            msg['Auto-Submitted'] = 'auto-generated'
            msg.set_content(body, cte='quoted-printable')
            client = (smtp_open or mail.smtp_open)(cfg, password)
            try:
                with self.st.file_lock('support-fault-mail'):
                    self._fault_live(host, event)
                    current, current_binding = self._bound()
                    state = self._read()
                    prior = state.get('dispatch') or {}
                    if prior.get('event') == event:
                        return prior['status']
                    if (current.generation != scope.generation or current_binding != binding
                            or int(time.time()) - _open(draft.envelope, self.st.signing_pub(), FAULT_CONTEXT)['ts'] >= _DRAFT_TTL
                            or privacy_host.send_refusal() or seatcert.refusal(self.st, 'support')):
                        return 'refused'
                    dispatch = {'event': event, 'status': 'unconfirmed'}
                    self._write({**state, 'dispatch': dispatch})
                    try:
                        refused = client.send_message(msg, from_addr=draft.sender, to_addrs=[draft.recipient])
                        status = 'refused' if refused else 'handed_off'
                    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
                        status = 'refused'
                    except (OSError, smtplib.SMTPException):
                        return 'unconfirmed'
                    self._write({**state, 'dispatch': {**dispatch, 'status': status}})
                    self.st.log('support_fault_mail', result=status)
                    return status
            finally:
                with suppress(OSError, smtplib.SMTPException):
                    client.quit()
        except (MailConsentError, mail.MailError, support.SupportError, OSError, cloud.CloudError):
            return 'unavailable'

    def cache_receipt(self, host, message):
        """Accept only the desk's fixed, signed receipt, isolated from the ordinary mail Agent."""
        if (message.get_all('Subject') != [RECEIPT_SUBJECT]
                or message.get_all('Auto-Submitted') != ['auto-generated']
                or message.is_multipart() or message.get_content_type() != 'text/plain'):
            return None
        try:
            raw = message.get_content()
            if len(raw) > 4096:
                return None
            envelope = json.loads(raw)
            result_body = _open(envelope, self._desk_key, RECEIPT_CONTEXT)
            event = result_body.get('event')
            result = self.read_receipt(host, event, envelope)
            with self.st.file_lock('support-fault-mail'):
                self._pending_event(host, event)
                scope, binding = self._bound()
                if result_body.get('binding') != binding or result_body.get('channel') != scope.channel:
                    return None
                state = self._read()
                prior = state.get('receipt') or {}
                if prior.get('event') == event and (prior.get('status') == 'ticketed'
                        or prior.get('ts', 0) > result_body['ts']
                        or (prior.get('ts') == result_body['ts'] and prior.get('status') == result['status'])):
                    return None
                self._write({**state, 'receipt': {**result_body, 'envelope': envelope}})
                self.st.log('support_fault_mail', result=result['status'])
            return result
        except (MailConsentError, OSError, TypeError, ValueError, LookupError, RecursionError):
            return None

    def cached_ticket(self, host, event):
        try:
            row = self._read().get('receipt') or {}
            if row.get('event') == event and row.get('status') == 'ticketed':
                result = self.read_receipt(host, event, row['envelope'])
                return {k: result[k] for k in ('thread', 'message', 'seq')}
        except (MailConsentError, OSError):
            pass
        return None
