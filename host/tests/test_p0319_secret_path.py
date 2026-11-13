"""Host-private plaza target approvals; synthetic keys and hermetic HOME only."""
import _hermetic  # noqa: F401
import json
import os
from unittest import mock

from agentj import market
import test_plaza_host_install as host_fixture
from test_plaza_install_card import CardFixture


class SecretTarget(CardFixture):
    # Reuse the real phone/Host coordinator helpers, not HostRequest's unrelated
    # inherited test cases (the export gate discovers all unittest subclasses).
    run_host = host_fixture.HostRequest.run_host
    req = host_fixture.HostRequest.req
    package = CardFixture.package

    def setUp(self):
        super().setUp()
        from agentj import plaza
        patch = mock.patch.object(plaza, 'seat_gate', return_value=None)
        patch.start()
        self.addCleanup(patch.stop)
        self.notices = []
        self.host.agent_notice = lambda text, **kw: self.notices.append(text)
        from agentj import application_ingress
        patch = mock.patch.object(application_ingress, 'allowed', return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        live = mock.patch.object(application_ingress, 'current', return_value=type('Live', (), {'_transport_live': lambda self: True})())
        live.start()
        self.addCleanup(live.stop)
        self.plaza_inputs = []
        original_ask = self.host.ask

        async def capture_ask(tool, tool_input, *args, **kwargs):
            if tool == 'agentj plaza':
                self.plaza_inputs.append(tool_input)
            return await original_ask(tool, tool_input, *args, **kwargs)

        patch = mock.patch.object(self.host, 'ask', new=capture_ask)
        patch.start()
        self.addCleanup(patch.stop)

    def check_private(self, secret, workspace, *, outcome='allow', names=None):
        workspace.mkdir(parents=True, exist_ok=True)
        if names:
            for n in names:
                self.package(n)
            req = {'names': names, 'harness': 'codex', 'workspace': str(workspace)}
        else:
            self.package()
            req = self.req(workspace=str(workspace))
        with mock.patch.dict(os.environ, {'SYNTHETIC_API_KEY': secret}):
            result, _ = self.run_host(req, outcome)
        self.assertGreaterEqual(len(self.cards()), 1, result)
        card = self.cards()[-1]
        targets = self.plaza_inputs[-1]['packages'] if names else [self.plaza_inputs[-1]]
        for projected in targets:
            self.assertEqual(projected['target'], '位置已隐藏')
            self.assertGreaterEqual(len(projected['target_ref']), 32)
        self.assertIn('安装目录含疑似密钥片段，已隐藏完整路径', card['summary'])
        self.assertNotIn(str(workspace), json.dumps(card, ensure_ascii=False))
        for payload in (json.dumps(self.sent, ensure_ascii=False), result['out'], self.st.log_path.read_text()):
            self.assertNotIn(secret, payload)
        return result, card

    def test_false_positive_normal_paths_still_ask_and_install(self):
        for parts in [('coding', 'experimental', 'animation-styles', 'plaza-install-test'),
                      ('Documents', '项目测试'), ('coding', 'hyphen-long-normal-workspace')]:
            with self.subTest(parts=parts):
                ws = self.e.home.joinpath(*parts)
                secret = 'zz' + '/'.join(parts[:2]) + 'yyyyyyyy'
                result, card = self.check_private(secret, ws)
                self.assertTrue(result['ok'], result)
                self.assertTrue((ws / 'demo-flow').is_dir())
                self.assertNotIn('target": "' + str(ws), json.dumps(card, ensure_ascii=False))

    def test_true_slash_secret_and_group_are_private(self):
        ws = self.e.home / 'alpha' / 'bravo' / 'safe'
        result, card = self.check_private('alpha/bravo', ws, names=['demo-one', 'demo-two'])
        self.assertTrue(result['ok'], result)
        self.assertNotIn('alpha/bravo', json.dumps(card, ensure_ascii=False))
        self.assertEqual(card['summary'].count('安装目录含疑似密钥片段'), 2)

    def test_precard_protected_target_never_echoes_secret(self):
        from agentj import fence
        ws = self.e.home / 'synthetic' / 'key'
        self.package()
        ws.mkdir(parents=True)
        with mock.patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'synthetic/key'}), \
             mock.patch.object(fence, 'host_write_refusal', return_value='protected'):
            result, _ = self.run_host(self.req(workspace=str(ws)))
        self.assertFalse(result['ok'])
        self.assertNotIn('synthetic/key', json.dumps([result, self.sent, self.notices], ensure_ascii=False))

    def test_precard_existing_target_never_echoes_secret(self):
        ws = self.e.home / 'synthetic' / 'key'
        (ws / 'demo-flow').mkdir(parents=True)
        self.package()
        with mock.patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'synthetic/key'}):
            result, _ = self.run_host(self.req(workspace=str(ws)))
        self.assertFalse(result['ok'])
        self.assertEqual(result['out'], '没有安装 / NOT installed')
        self.assertEqual(self.cards(), [])

    def test_precard_symlink_target_error_never_echoes_secret(self):
        ws = self.e.home / 'synthetic' / 'key'
        ws.parent.mkdir(parents=True)
        ws.symlink_to(self.e.home / 'nonexistent-parent')
        self.package()
        with mock.patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'synthetic/key'}):
            result, _ = self.run_host(self.req(workspace=str(ws)))
        self.assertFalse(result['ok'])
        self.assertNotIn('synthetic/key', json.dumps([result, self.sent, self.notices], ensure_ascii=False))

    def test_precard_malformed_package_never_echoes_secret(self):
        from agentj import market
        ws = self.e.home / 'synthetic' / 'key'
        ws.mkdir(parents=True)
        self.package()
        def malformed(*args, **kwargs):
            raise market.PlazaError('malformed: ' + str(ws))
        with mock.patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'synthetic/key'}), \
             mock.patch.object(market, 'run_install', side_effect=malformed):
            result, _ = self.run_host(self.req(workspace=str(ws)))
        self.assertEqual(result['out'], '没有安装 / NOT installed')
        self.assertNotIn('synthetic/key', json.dumps([result, self.sent, self.notices], ensure_ascii=False))

    def test_truncated_partial_target_never_echoes_even_without_index_hit_in_output(self):
        from agentj import market
        ws = self.e.home / 'synthetic' / 'key'
        ws.mkdir(parents=True)
        self.package()
        def bad_install(*args, out=None, **kwargs):
            out.write(str(ws)[:12])
            return market.EXIT_ERROR
        with mock.patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'synthetic/key'}), \
             mock.patch.object(market, 'run_install', side_effect=bad_install):
            result, _ = self.run_host(self.req(workspace=str(ws)))
        self.assertEqual(result['out'], '没有安装 / NOT installed')

    def test_signed_approval_must_match_host_pending_rid_device_and_deadline(self):
        from agentj import plaza_perms
        original = plaza_perms.signed_card
        for change in ('rid', 'device', 'expired', 'replayed'):
            with self.subTest(change=change):
                name = 'case-' + change
                self.package(name)
                ws = self.e.home / 'synthetic' / 'key'
                ws.mkdir(parents=True, exist_ok=True)
                async def altered(host, label, inp, emit, gate, **kw):
                    result = await original(host, label, inp, emit, gate, **kw)
                    if result.get('ok'):
                        result = {**result, 'proof': dict(result['proof'])}
                        if change == 'rid':
                            result['proof']['rid'] = 'other-rid'
                        elif change == 'device':
                            result['proof']['device'] = 'other-device'
                        elif change == 'expired':
                            host.__dict__['_plaza_private_pending'].clear()
                        else:
                            result['proof']['rid'] = self.plaza_inputs[0].get('target_ref', 'replayed')
                    return result
                with mock.patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'synthetic/key'}), \
                     mock.patch.object(plaza_perms, 'signed_card', new=altered):
                    result, _ = self.run_host(self.req(name, workspace=str(ws)))
                self.assertFalse(result['ok'], result)
                self.assertFalse((ws / name).exists())

    def test_unmatched_target_keeps_full_visible_path(self):
        self.package()
        ws = self.e.home / 'Documents' / 'normal'
        ws.mkdir(parents=True)
        result, _ = self.run_host(self.req(workspace=str(ws)))
        self.assertTrue(result['ok'], result)
        self.assertIn(str(ws / 'demo-flow'), self.cards()[-1]['summary'])

    def test_symlink_target_hit_hides_resolved_target(self):
        real = self.e.home / 'alpha' / 'bravo'
        real.mkdir(parents=True)
        alias = self.e.home / 'nice'
        alias.symlink_to(real)
        result, card = self.check_private('alpha/bravo', alias)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('alpha/bravo', json.dumps(card, ensure_ascii=False))

    def test_deny_never_installs(self):
        ws = self.e.home / 'segment' / 'other'
        result, _ = self.check_private('segment/other', ws, outcome='deny')
        self.assertEqual(result['rc'], market.EXIT_PERMISSION)
        self.assertFalse((ws / 'demo-flow').exists())

    def test_secret_in_leaf_never_appears_in_any_outbound_projection(self):
        from agentj import activity
        secret = 'synthetic/key/leaf'
        ws = self.e.home / 'synthetic' / 'key' / 'leaf'
        result, card = self.check_private(secret, ws)
        self.assertTrue(result['ok'], result)
        outbound = [card, self.plaza_inputs, self.sent, result, self.notices,
                    self.st.log_path.read_text(),
                    ''.join(p.read_text() for p in activity._dir(self.st).glob('*.jsonl'))]
        for field in outbound:
            text = json.dumps(field, ensure_ascii=False)
            self.assertNotIn(secret, text)
            self.assertNotIn(str(ws), text)
            self.assertNotIn('"target": "' + str(ws), text)
        self.assertEqual(card['summary'].count('安装目录含疑似密钥片段'), 1)
        self.assertEqual(card['envelope']['details'].count('安装目录含疑似密钥片段'), 1)

    def test_private_target_timeout_never_installs(self):
        ws = self.e.home / 'synthetic' / 'key'
        ws.mkdir(parents=True)
        self.package()
        self.host.ask_ttl = 0.15
        with mock.patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'synthetic/key'}):
            result, _ = self.run_host(self.req(workspace=str(ws)), outcome='timeout')
        self.assertEqual((result['rc'], result['why']), (market.EXIT_PERMISSION, 'timeout'))
        self.assertFalse((ws / 'demo-flow').exists())
        self.assertNotIn('synthetic/key', json.dumps(self.sent, ensure_ascii=False))
        self.assertNotIn('synthetic/key', result['out'])

    def test_replayed_signature_cannot_install_private_target(self):
        ws = self.e.home / 'synthetic' / 'key'
        ws.mkdir(parents=True)
        self.package()
        with mock.patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'synthetic/key'}):
            result, _ = self.run_host(self.req(workspace=str(ws)), outcome='allow',
                                      answer=lambda card: self.phone.answer(card, True, rid='wrong-request'))
        self.assertFalse(result['ok'], result)
        self.assertFalse((ws / 'demo-flow').exists())

    def test_private_target_symlink_swap_after_signed_allow_is_refused(self):
        ws = self.e.home / 'synthetic' / 'key'
        ws.mkdir(parents=True)
        elsewhere = self.e.home / 'other'
        elsewhere.mkdir()
        self.package()
        def swap(card):
            ws.rename(ws.with_name('key-moved'))
            ws.symlink_to(elsewhere)
            return self.phone.answer(card, True)
        with mock.patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'synthetic/key'}):
            result, _ = self.run_host(self.req(workspace=str(ws)), answer=swap)
        self.assertFalse(result['ok'], result)
        self.assertFalse((elsewhere / 'demo-flow').exists())
        self.assertFalse((ws.with_name('key-moved') / 'demo-flow').exists())
        self.assertNotIn('synthetic/key', json.dumps(self.sent, ensure_ascii=False))
        self.assertNotIn('synthetic/key', result['out'])

    def test_group_denial_never_installs_any_private_target(self):
        ws = self.e.home / 'synthetic' / 'key'
        result, _ = self.check_private('synthetic/key', ws, names=['demo-one', 'demo-two'], outcome='deny')
        self.assertFalse(result['ok'])
        self.assertFalse((ws / 'demo-one').exists())
        self.assertFalse((ws / 'demo-two').exists())
