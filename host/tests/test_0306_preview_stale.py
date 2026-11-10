"""0.30.6 preview, chairman true-device step 1 (2026-11-10 11:52): `agentj automation grant` → allow on the phone →
automation_grant_stale. The serve Agent works in a directory whose .env holds the owner's own mailbox / SMTP host, so
the card display projection (action_policy.display_normalizer → SecretIndex) masks the 发信账号 line; the phone signs
that projected text and proof_ok compared it to the raw shown(core). Real projector, real Ed25519 + WebAuthn proofs.
"""
import _hermetic  # noqa: F401,I001
import os
import tempfile

from agentj import action_policy, approvals, approval_review, automation_grant as AG, passkey, wire
from agentj.privacy import R
from test_0304_automation_grant import ALLY, Base
from test_p194_owner_direct import AGENT


class ProjectedCard(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.wd = tempfile.mkdtemp(prefix='aj0306-')
        with open(os.path.join(self.wd, '.env'), 'w') as fh:
            fh.write('SMTP_AUTH_HOST=127.0.0.1\n')   # only the 发信账号 line carries it
        self.ask.side_effect = self.sign_projected
        self.v2 = False

    def project(self, text, lang='zh'):
        disp = action_policy.display_normalizer(lang=lang, workdir=self.wd)
        return approval_review.display_text(disp({'summary': text})['summary'], disp)

    async def sign_projected(self, tool, inp, **kw):
        """What list_approval.ask hands the phone for a host-summary card: the display projection of the summary."""
        if tool != AG.TOOL:
            return {'behavior': 'allow'}
        summary, channel, rid = self.project(inp['summary']), self.st.config()['channel'], 'request-id'
        self.cards.append(summary)
        info = {'device': 'dev1', 'rid': rid, 'channel': channel, 'summary': summary, 'kind': 'sandbox'}
        if self.v2:
            front = kw['_front']
            env = {'v': 2, 'channel': channel, 'request_id': rid, 'action': AG.TOOL, 'details': summary,
                   'input_sha256': approvals.input_digest(inp), 'plan': None,
                   **{k: self.project(front[k]) for k in ('title', 'title_en', 'reason', 'reason_en')}}
            d = approval_review.digest(env)
            msg = approvals.signed_message(channel, 'dev1', rid, 'allow', d).replace(
                (approvals.CONTEXT + '\n').encode(), (approval_review.CONTEXT + '\n').encode(), 1)
            info.update(approval_v=2, envelope=env)
        else:
            d = approvals.shown_digest(tool, summary)
            msg = approvals.signed_message(channel, 'dev1', rid, 'allow', d)
        fa = self.auth.assert_(passkey.elevate_challenge(channel, 'dev1', rid, 'sandbox', rid, d), b'', nonce=b'')
        info.update(sig=wire.b64u(self.ed.sign(msg)), fa={k: fa[k] for k in ('id', 'cd', 'ad', 'sig')})
        return {'behavior': 'allow', 'grant_info': info}

    async def _issue_ok(self):
        g = await self.grant(ref=self.hook['id'])
        self.assertIn(R, self.cards[0], 'control: the projector really masked the sending account')
        row = self.ledger()['grants'][g['grant']]
        self.assertNotEqual(row['proof']['summary'], AG.shown(row['core']))
        self.assertTrue(AG.proof_ok(self.st, row['core'], row['proof']))
        self.hook_turn()
        res = await self.send(ALLY)
        self.assertTrue(res.get('ok'), res)
        return row

    async def test_masked_account_line_v1_issues_and_sends(self):
        await self._issue_ok()

    async def test_masked_account_line_v2_issues_and_sends(self):
        self.v2 = True
        await self._issue_ok()

    async def test_mask_outside_the_account_line_is_still_stale(self):
        row = await self._issue_ok()
        core, proof = row['core'], row['proof']
        lines = proof['summary'].split('\n')
        for n, line in enumerate(lines):
            if line == AG.account_line(core) or R in line:
                continue
            for bad in (R, line[:2] + R, line + '\n' + R):
                with self.subTest(line=n, bad=bad[:12]):
                    forged = '\n'.join(lines[:n] + [bad] + lines[n + 1:])
                    self.assertFalse(AG.signed_text_ok(core, forged))
        with self.subTest('account line rewritten, not masked'):
            raw = AG.shown(core).split('\n')
            i = raw.index(AG.account_line(core))
            forged = raw[:i] + [raw[i].replace(AGENT, 'evil@example.com')] + raw[i + 1:]
            self.assertFalse(AG.signed_text_ok(core, '\n'.join(forged)))
        with self.subTest('tampered account, SHA line copied (security N3)'):
            evil = {**core, 'account': {**core['account'], 'address': 'evil@example.com'}}
            self.assertFalse(AG.signed_text_ok(evil, proof['summary']))
            self.assertFalse(AG.proof_ok(self.st, evil, proof))
        with self.subTest('extra line'):
            self.assertFalse(AG.signed_text_ok(core, AG.shown(core) + '\n' + R))

    async def test_projector_masking_the_recipients_stays_refused(self):
        """Fail closed: the owner never saw whom the grant allows (8-char fragments of the mailbox also hit a
        recipient of the same domain) → the signed card is not this core's card."""
        with open(os.path.join(self.wd, '.env'), 'w') as fh:
            fh.write(f'SMTP_AUTH_USER={AGENT}\n')
        from unittest.mock import patch
        with patch.object(AG, 'owner_turn', return_value=True):
            res = await AG.issue(self.host, {'kind': 'hook', 'ref': self.hook['id'], 'to': [ALLY], 'domains': []}, None)
        self.assertNotIn('允许的收件人：' + ALLY, self.cards[0])
        self.assertEqual(res, {'ok': False, 'why': 'automation_grant_stale'})

    def test_masked_wildcard(self):
        raw = '发信账号：a@example.com（smtp.example.com:465）'
        for ok in (R, '发信账号：' + R, '发信账号：' + R + '（' + R + '）', raw):
            self.assertTrue(AG._masked(ok, raw) or ok == raw, ok)
        for bad in ('发信账号：' + R + '（x.example.com:465）', R + R + 'zzz', '其它：' + R, raw + R, ''):
            self.assertFalse(AG._masked(bad, raw), bad)
