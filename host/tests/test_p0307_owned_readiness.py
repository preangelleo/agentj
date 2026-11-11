"""#0307: owned-path preflight and honest native readiness (isolated filesystem)."""
import _hermetic  # noqa: F401
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from agentj import agent, auto_update, rescue_kit, update, upgrade_owner


class OwnedPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMP'))
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.home.mkdir()
        self.tool = self.home / '.local/share/uv/tools/agentj'
        self.tool.mkdir(parents=True)
        self.state = self.home / '.local/state/agentj'
        self.state.mkdir(parents=True)
        (self.state / 'agentperm').mkdir()
        self.env = {'XDG_STATE_HOME': str(self.home / '.local/state'),
                    'XDG_CONFIG_HOME': str(self.home / '.config'),
                    'XDG_CACHE_HOME': str(self.home / '.cache')}

    def test_inventory_covers_state_and_external_owned_files(self):
        from agentj import owned_paths
        paths = owned_paths.inventory(str(self.home), self.env, str(self.state))
        names = {str(p) for p in paths}
        self.assertIn(str(self.state / 'agentperm'), names)
        self.assertIn(str(self.state / 'host.log'), names)
        self.assertIn(str(self.home / '.config/systemd/user/net.agentj.host.service'), names)
        self.assertIn(str(self.home / '.cache/agentj-upgrade'), names)
        self.assertIn(str(self.home / '.claude/settings.json'), names)
        self.assertIn(str(self.home / '.codex/config.toml'), names)

    def test_foreign_state_blocks_without_chowning_home_or_following_links(self):
        from agentj import owned_paths
        root = str(self.state / 'agentperm')
        real_lstat = os.lstat
        def fake(p):
            s = list(real_lstat(p))
            if str(p) == root:
                s[4] = 0
            return os.stat_result(s)
        with mock.patch.object(upgrade_owner, 'HOME_OF', return_value=str(self.home)):
            result = upgrade_owner.blocked(str(self.tool), uid=os.getuid(), environ=self.env,
                                            lstat=fake, user='test', state_root=self.state)
        self.assertEqual(result['paths'], [str(self.state)])
        self.assertNotIn(str(self.home) + ' ', result['fix'])

    def test_shared_settings_fix_is_only_the_file(self):
        config = self.home / '.claude/settings.json'
        config.parent.mkdir()
        config.write_text('{}')
        real_lstat = os.lstat
        def fake(p):
            s = list(real_lstat(p))
            if str(p) == str(config):
                s[4] = 0
            return os.stat_result(s)
        with mock.patch.object(upgrade_owner, 'HOME_OF', return_value=str(self.home)):
            result = upgrade_owner.blocked(str(self.tool), uid=os.getuid(), environ=self.env,
                                            lstat=fake, user='test', state_root=self.state)
        self.assertEqual(result['paths'], [str(config)])
        self.assertNotIn(str(config.parent) + ' ', result['fix'])

    def test_verified_plan_refuses_before_cache_or_download(self):
        with mock.patch.object(upgrade_owner, 'blocked', return_value={'owner_kind':'root', 'fix':'fix-line'}), \
             mock.patch.object(auto_update, 'cached_wheel', side_effect=AssertionError('download')):
            with self.assertRaisesRegex(ValueError, 'not_owner'):
                update.verified_plan(None, {'kind':'uv'}, '9.9.9')


class Readiness(unittest.TestCase):
    def test_base_is_unverified_and_rescue_warns(self):
        base = object.__new__(agent.Agent)
        self.assertEqual(base.readiness(), {'ready': False, 'code': 'unverified'})
        report = rescue_kit.Report()
        with mock.patch.object(rescue_kit, 'agentj_bin', return_value='agentj'), \
             mock.patch.object(rescue_kit, 'run', return_value=(0, '{"ok":true,"harness_readiness":{"ready":false,"code":"unverified"}}')):
            rescue_kit.check_harness(report)
        self.assertEqual(report.get('harness'), rescue_kit.WARN)
