"""0.26.10: data is not a credential operation; every CEO launch is proved separately."""
import _hermetic  # noqa: F401
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import approval_level, approval_policy, danger, peer_origin, policy_registry


class ClassifierHotfix(unittest.TestCase):
    def check(self, command):
        inp = {'command': command}
        v = danger.classify_shared('Bash', inp)
        return v, approval_level.redline('Bash', inp, v)

    def test_report_names_in_substitution_are_files_not_directories(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td)
            (p/'report.md').write_text('status only')
            cmd = f'cd {td} && R=$(ls -t report.md missing.md | head -1); head -30 $R; tail -2 $R'
            v, red = self.check(cmd)
            self.assertFalse(v.danger, v)
            self.assertIsNone(red)
            self.assertEqual(policy_registry.main_requirement('Bash', {'command':cmd}), 'allow')

    def test_real_directory_and_symlink_still_reveal_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td)
            d = p/'report.md'; d.mkdir(); (d/'token.json').write_text('synthetic')
            (p/'link.md').symlink_to(d/'token.json')
            for name in ('report.md', 'link.md'):
                cmd = f'cd {td} && R=$(ls {name} | head -1); cat $R'
                self.assertIn('credentials', self.check(cmd)[0].cats, cmd)

    def test_quoted_document_is_data(self):
        body = '$API_KEY cat ~/coding/.env\npython3 -c \'open("~/.local/state/agentj/approvals.json","w").write("x")\''
        command = "cat > .jarvis/PROMPT-x.md <<'EOF'\n" + body + '\nEOF'
        v, red = self.check(command)
        self.assertFalse(v.danger, v)
        self.assertIsNone(red)
        self.assertEqual(approval_policy.decide('Bash', {'command':command}, v, requirement='allow'), ('none','routine'))
        self.assertEqual(policy_registry.main_requirement('Bash', {'command':command}), 'allow')

    def test_unquoted_data_expansions_stay_sensitive(self):
        bodies = ['$API_KEY', '${API_KEY:-fallback}', '${API_KEY:0:3}', '${API_KEY//x/y}', '${!API_KEY}',
                  r'\\$API_KEY', '$(cat ~/coding/.env)', '`cat ~/coding/.env`']
        for body in bodies:
            cmd = 'cat > .jarvis/result.md <<EOF\n' + body + '\nEOF'
            self.assertEqual(self.check(cmd)[1], 'credentials', body)

    def test_program_heredocs_and_later_execution_stay_sensitive(self):
        for cmd in ("bash <<'EOF'\ncat ~/coding/.env\nEOF", "cat > x.sh <<'EOF'\ncat ~/coding/.env\nEOF\nbash x.sh",
                    "python3 - <<'EOF'\nopen('~/.local/state/agentj/approvals.json','w').write('x')\nEOF",
                    "python3 - <<'EOF'\nprint(open('/tmp/.env').read())\nEOF"):
            self.assertIsNotNone(self.check(cmd)[1], cmd)

    def test_standard_delete_requirement_keeps_existing_face_id_floor(self):
        with tempfile.TemporaryDirectory() as td:
            (pathlib.Path(td)/'build').mkdir()
            self.assertEqual(policy_registry.main_requirement('Bash',{'command':'rm -rf build'},cwd=td), 'uv')

    def test_real_sensitive_paths_reach_shared_guard_card(self):
        for command in ('cat ~/coding/.env', "printf x > ~/.local/state/agentj/approvals.json"):
            self.assertEqual(policy_registry.main_requirement('Bash', {'command':command}), 'uv', command)

    def launch(self, root, inner='codex exec ok', prefix='agentj', extra=''):
        return (f'systemd-run --user --collect --unit=agentj-test -p WorkingDirectory={root} '
                f'{prefix} busy run --kind ceo -- bash -c "{inner}"{extra}')

    def batch(self, cmd, root):
        # Only executable identity evidence is simulated. All grammar, path,
        # nested command and remaining-action checks are production functions.
        with mock.patch.object(peer_origin._Proof, 'word', return_value=True):
            return peer_origin.check_batch(cmd, root)

    def test_batch_proves_each_launch_and_checks_the_remainder(self):
        with tempfile.TemporaryDirectory() as root:
            cmd = "cat > .jarvis/PROMPT-x.md <<'EOF'\n凭据 密钥\nEOF\n" + self.launch(root) + '\n' + self.launch(root)
            self.assertEqual(self.batch(cmd, root), ('agentj-test.service', 'ok'))
            for tail in ('cat ~/coding/.env', 'printenv', 'printf x > ~/.local/state/agentj/approvals.json', 'sudo true'):
                self.assertNotEqual(self.batch(cmd + '\n' + tail, root)[1], 'ok', tail)

    def test_batch_cannot_hide_nested_sensitive_commands(self):
        with tempfile.TemporaryDirectory() as root:
            for inner in ('cat ~/coding/.env', 'printenv', 'codex exec ok; cat ~/coding/.env',
                          'printf x > ~/.local/state/agentj/approvals.json'):
                self.assertNotEqual(self.batch('echo ready\n' + self.launch(root,inner), root)[1], 'ok', inner)
            self.assertNotEqual(self.batch('echo ready\n' + self.launch(root,prefix="'$HOME/bin/agentj'"), root)[1], 'ok')
            self.assertNotEqual(self.batch('echo ready\n' + self.launch(root,extra=' > .jarvis/log'), root)[1], 'ok')
            self.assertNotEqual(self.batch('echo ready\n' + self.launch(root,extra=' "'), root)[1], 'ok')

    def test_batch_cannot_mutate_shell_lookup_before_proof(self):
        with tempfile.TemporaryDirectory() as root:
            prefixes = ('read PATH <<EOF\n/tmp\nEOF', 'printf -v PATH /tmp', 'unset PATH',
                        'read -r HOME <<EOF\n/tmp\nEOF', 'read BASH_ENV <<EOF\n/tmp/evil.sh\nEOF',
                        'function systemd-run { printf harmless; }', 'systemd-run() { printf harmless; }',
                        "alias systemd-run='printf harmless'\nshopt -s expand_aliases",
                        'enable -f /tmp/evil.so systemd-run', 'hash -p /tmp/other systemd-run',
                        'printf -vPATH /tmp', 'printf -vHOME /tmp', 'printf -vBASH_ENV /tmp/evil.sh',
                        "printf '%n' PATH", 'systemctl start arbitrary.service', 'systemctl import-environment',
                        "echo '${PATH:=/tmp}'",
                        "python3 -c 'open(\"/tmp/earlier/systemd-run\",\"w\").write(\"x\")'",
                        'cat > /tmp/earlier/systemd-run <<\'EOF\'\nx\nEOF')
            for prefix in prefixes:
                self.assertNotEqual(self.batch(prefix + '\n' + self.launch(root), root)[1], 'ok', prefix)

    def test_only_finite_markdown_python_grammar_can_precede_launch(self):
        with tempfile.TemporaryDirectory() as root:
            body = "p='.jarvis/PROMPT-x.md'; s=open(p).read(); s=s.replace('before','after'); open(p,'w').write(s)"
            cmd = "python3 -I -S - <<'EOF'\n" + body + "\nEOF\n" + self.launch(root)
            self.assertEqual(self.batch(cmd,root)[1], 'ok')
            for program in ("import os; os.system('id')", "open('/tmp/earlier/systemd-run','w').write('x')",
                            "p='.jarvis/PROMPT-x.md'; open=print; open(p,'w')",
                            "open('~/.local/state/agentj/approvals.json','w').write('x')",
                            "open('/tmp/.env').read()"):
                cmd = "python3 -I -S - <<'EOF'\n" + program + "\nEOF\n" + self.launch(root)
                self.assertNotEqual(self.batch(cmd,root)[1], 'ok', program)

    def test_python_companion_rejects_unisolated_and_noncanonical_argv(self):
        with tempfile.TemporaryDirectory() as root:
            body = "open('.jarvis/PROMPT-safe.md','w').write('plain note')"
            for flags in ('-', '-I -', '-S -', '-S -I -', '-IS -', '--isolated -S -',
                          '-I -S -u -', '-I -S - --', '-I -S -c pass'):
                command = f"python3 {flags} <<'EOF'\n{body}\nEOF\n" + self.launch(root)
                self.assertNotEqual(self.batch(command, root)[1], 'ok', flags)

    def test_isolated_note_companion_ignores_inherited_startup_without_rewriting_command(self):
        import os
        import subprocess
        from _testtmp import TEST_TMP
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            path = pathlib.Path(root)
            (path/'.jarvis').mkdir()
            marker = path/'startup-marker'
            (path/'sitecustomize.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("synthetic")\n')
            env = {**os.environ, 'PYTHONPATH': root}
            control = subprocess.run([sys.executable, '-'], input='pass\n', text=True, cwd=root,
                                     env=env, capture_output=True, timeout=10)
            self.assertEqual(control.returncode, 0)
            self.assertTrue(marker.exists())
            marker.unlink()
            body = "open('.jarvis/PROMPT-safe.md','w').write('plain note')"
            child = subprocess.run([sys.executable, '-I', '-S', '-'], input=body+'\n', text=True,
                                   cwd=root, env=env, capture_output=True, timeout=10)
            self.assertEqual(child.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertEqual((path/'.jarvis/PROMPT-safe.md').read_text(), 'plain note')
            command = "python3 -I -S - <<'EOF'\n"+body+"\nEOF\n"+self.launch(root)
            original = command
            self.assertEqual(self.batch(command, root)[1], 'ok')
            self.assertEqual(command, original)
            with mock.patch.object(peer_origin._Proof, 'word', side_effect=lambda tok,name,side: name != 'python3'):
                self.assertNotEqual(peer_origin.check_batch(command, root)[1], 'ok')

    def test_batch_normalizes_pathlike_state(self):
        with tempfile.TemporaryDirectory() as root:
            cmd = "cat > .jarvis/PROMPT-x.md <<'EOF'\nnote\nEOF\n" + self.launch(root)
            with mock.patch.object(peer_origin._Proof, 'word', return_value=True):
                self.assertEqual(peer_origin.check_batch(cmd,root,pathlib.Path(root)/'state')[1], 'ok')

    def test_remainder_executables_need_separate_identity_proof(self):
        with tempfile.TemporaryDirectory() as root:
            cmd = "cat > .jarvis/PROMPT-x.md <<'EOF'\nnote\nEOF\n" + self.launch(root)
            with mock.patch.object(peer_origin._Proof, 'word', side_effect=lambda tok,name,side: name != 'cat'):
                self.assertNotEqual(peer_origin.check_batch(cmd,root)[1], 'ok')
            self.assertNotEqual(self.batch('/tmp/echo ready\n'+self.launch(root),root)[1], 'ok')

    def test_stop_is_only_exempt_for_a_host_proven_absent_unit(self):
        with tempfile.TemporaryDirectory() as root:
            cmd = self.launch(root) + '\nsystemctl --user stop agentj-after-test'
            with mock.patch.object(peer_origin, '_absent_user_unit', return_value=True):
                self.assertEqual(self.batch(cmd,root)[1], 'ok')
            with mock.patch.object(peer_origin, '_absent_user_unit', return_value=False):
                self.assertNotEqual(self.batch(cmd,root)[1], 'ok')

    def test_absent_unit_requires_complete_fixed_host_evidence(self):
        import subprocess
        complete = {'Id':'agentj-after-test.service', 'Names':'agentj-after-test.service', 'LoadState':'not-found',
                    'ActiveState':'inactive', 'SubState':'dead', 'Job':''}
        def query(props, rc=0):
            return subprocess.CompletedProcess([],rc,'\n'.join(k+'='+v for k,v in props.items()),'')
        with mock.patch('shutil.which',return_value='/usr/bin/systemctl'), mock.patch.object(peer_origin,'_pin',return_value=True):
            with mock.patch('subprocess.run',return_value=query(complete)):
                self.assertTrue(peer_origin._absent_user_unit('agentj-after-test'))
                self.assertFalse(peer_origin._absent_user_unit('agentj-after-test',['agentj-after-test.service']))
            for key in complete:
                bad = {k:v for k,v in complete.items() if k != key}
                with mock.patch('subprocess.run',return_value=query(bad)):
                    self.assertFalse(peer_origin._absent_user_unit('agentj-after-test'),key)
            for changed in ({'LoadState':'loaded'}, {'ActiveState':'active'}, {'Job':'42'}, {'ExecStop':'configured'},
                            {'Id':'other.service'}, {'Names':'agentj-after-test.service agentj-new.service'}):
                with mock.patch('subprocess.run',return_value=query({**complete,**changed})):
                    self.assertFalse(peer_origin._absent_user_unit('agentj-after-test',['agentj-new.service']),changed)
            with mock.patch('subprocess.run',return_value=query(complete,1)):
                self.assertFalse(peer_origin._absent_user_unit('agentj-after-test'))
            with mock.patch('subprocess.run',side_effect=subprocess.TimeoutExpired('systemctl',2)):
                self.assertFalse(peer_origin._absent_user_unit('agentj-after-test'))

    def test_remainder_proof_reasons_are_not_discarded(self):
        with tempfile.TemporaryDirectory() as root:
            cmd = "cat > .jarvis/PROMPT-x.md <<'EOF'\nnote\nEOF\n" + self.launch(root)
            def evidence(proof, token, name, side):
                if name == 'cat':
                    proof.why.append('cat:not_sealed')
                return True
            with mock.patch.object(peer_origin._Proof, 'word', evidence):
                self.assertNotEqual(peer_origin.check_batch(cmd,root)[1], 'ok')

    def test_batch_executable_proof_failure_is_not_whitelisted(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(peer_origin._Proof, 'word', return_value=False):
                self.assertNotEqual(peer_origin.check_batch('echo ready\n'+self.launch(root), root)[1], 'ok')


if __name__ == '__main__':
    unittest.main()
