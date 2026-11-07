"""H1 execution authority is narrower than the historical research classification."""
import _hermetic  # noqa: F401
import unittest
from unittest.mock import patch
from test_p584_run import Run
from agentj import terminal


class Profile(unittest.TestCase):
    def test_literal_profile_and_shell_metadata(self):
        from agentj.term_direct import pristine_env
        for line in ("printf '%s\\n' hello", 'echo hello', 'pwd', 'pwd -P',
                     "printf '%s' a; printf '%s\\n' b"):
            self.assertTrue(terminal._readonly(line), line)
        for line in ('printf %n x', 'printf %b x', 'echo $HOME', 'echo *', 'pwd x',
                     'echo hi > out', 'printf -- x', 'echo hi && echo bye', 'echo hi\necho bye'):
            self.assertFalse(terminal._readonly(line), line)
        self.assertTrue(pristine_env({'PATH': '/usr/bin', 'HOME': '/synthetic'}))
        for key in ('BASH_FUNC_printf%%', 'PS0', 'PROMPT_COMMAND', 'SHELLOPTS', 'BASH_ENV'):
            self.assertFalse(pristine_env({key: 'synthetic'}))

    def test_existing_review_counterexamples_require_confirmation(self):
        for line in ('curl -sI https://example.invalid -o/tmp/p584-probe',
                     'curl -sI https://example.invalid -D/tmp/p584-probe',
                     'sed -n "1e touch /tmp/p584-probe" /dev/null',
                     'find /tmp -execdir printf x \\;', 'git diff --ext-diff',
                     'cat ~/.config/shell/api_keys.env', 'curl file:///tmp/data',
                     'printf -v PATH /tmp', 'jq --run-tests /tmp/p584-probe',
                     'ls -la', 'git status', 'cat README.md', 'df -h'):
            with self.subTest(line=line):
                self.assertFalse(terminal._readonly(line))

    def test_historical_research_result_never_grants_h1(self):
        with patch('agentj.danger.readonly', return_value=(True, 'historical')):
            self.assertFalse(terminal._readonly('cat README.md'))
            self.assertFalse(terminal._readonly('git status'))
        with patch('agentj.danger.readonly', side_effect=AssertionError('must not consult')):
            self.assertTrue(terminal._readonly('echo hello'))


class Boundary(Run):
    def test_startup_configuration_forces_confirmation(self):
        async def go():
            with patch('agentj.term_direct.pristine_env', return_value=False):
                await self.open()
            self.assertFalse(self.desk.term.h1_pristine)
            n = await self.run_line('echo hello')
            self.assertTrue(any(o.get('t') == 'term_confirm' for _, o in self.sent[n:]))
        self.run_async(go)

    def test_unknown_shell_state_never_gets_the_exception(self):
        async def go():
            await self.open()
            self.desk.term.h1_pristine = False
            n = await self.run_line('echo hello')
            self.assertTrue(any(o.get('t') == 'term_confirm' for _, o in self.sent[n:]))
        self.run_async(go)

    def test_confirmed_non_profile_line_permanently_invalidates_exception(self):
        async def go():
            await self.open()
            n = await self.run_line('touch ~/synthetic-marker')
            await self.confirmed('touch ~/synthetic-marker', n)
            self.assertFalse(self.desk.term.h1_pristine)
            n = await self.run_line('echo hello')
            self.assertTrue(any(o.get('t') == 'term_confirm' for _, o in self.sent[n:]))
        self.run_async(go)

    def test_existing_local_effects_are_held_before_execution(self):
        inp = self.home / 'public-input'
        inp.write_text('synthetic public input\n')
        target = self.home / 'effect-marker'
        lines = [f'curl -s file://{inp} -o{target}', f'curl -sI file://{inp} -D{target}',
                 f'sed -n "1e touch {target}" {inp}']

        async def go():
            await self.open()
            for line in lines:
                n = await self.run_line(line)
                self.assertTrue(any(o.get('t') == 'term_confirm' for _, o in self.sent[n:]))
                self.assertFalse(target.exists())
        self.run_async(go)


if __name__ == '__main__':
    unittest.main()
