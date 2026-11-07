"""Pure classification of bounded dispatch wrappers; never run the supplied text."""
import _hermetic
import os
import tempfile
import unittest
from pathlib import Path
from agentj import routine_uv


class DispatchScripts(unittest.TestCase):
    def setUp(self):
        root=Path(os.environ.get('TEST_TMP') or tempfile.gettempdir());root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.cwd=Path(self.temp.name)

    def check(self,text):
        return routine_uv.proven_routine('Bash',{'command':text},cwd=str(self.cwd))

    def test_polling_and_literal_file_copy_domains(self):
        self.assertTrue(self.check('while systemctl --user is-active agentj-test; do sleep 1; done; claude -p "for x in prose; PATH=literal is data"'))
        self.assertTrue(self.check('for p in report-one.md report-two.md; do cp "$p" notes/; done'))
        for text in ('for p in "$unknown"; do cp "$p" notes/; done',
                     'for p in ~/.ssh/id_rsa; do cp "$p" notes/; done',
                     'while true; do cat "$unknown"; done',
                     'for p in report.md; do cat "$p"; done',
                     'for p in report.md; do "$p"; done',
                     'for p in report.md; do p=~/.ssh/id_rsa; cp "$p" notes/; done',
                     "for p in report.md; do bash -c 'p=~/.ssh/id_rsa; cp \"$p\" notes/'; done"):
            with self.subTest(shape=text.split(';')[0]):self.assertFalse(self.check(text))

    def test_fixed_metadata_substitutions(self):
        self.assertTrue(self.check('stamp=$(date +%s); printf "done %s" "$(date +%H:%M)"; sleep 1'))
        for text in ('stamp=$(cat .env); echo "$stamp"','stamp=$(date --file=.env); echo "$stamp"',
                     'printf "$(unknown_program)"','stamp=$(printf "$API_TOKEN"); echo "$stamp"'):
            with self.subTest(shape=text.split(';')[0]):self.assertFalse(self.check(text))

    def test_owner_regular_shell_program(self):
        script=self.cwd/'dispatch.sh'
        script.write_text('while systemctl --user is-active agentj-test; do sleep 1; done\nsystemd-run --user codex exec --dangerously-bypass-approvals-and-sandbox - < PROMPT.md')
        self.assertFalse(self.check('systemd-run --user bash '+str(script)))
        script.write_text('cat ~/.ssh/id_rsa')
        self.assertFalse(self.check('systemd-run --user bash '+str(script)))
        script.write_text('unknown_program --run')
        self.assertFalse(self.check('systemd-run --user bash '+str(script)))
        script.chmod(0o666)
        self.assertFalse(self.check('systemd-run --user bash '+str(script)))

    def test_literal_producer_and_nested_effect(self):
        text="cat > dispatch.sh <<'DOC'\nsystemd-run --user codex exec --dangerously-bypass-approvals-and-sandbox - < PROMPT.md\nDOC\nsystemd-run --user bash dispatch.sh"
        self.assertFalse(self.check(text))
        self.assertFalse(self.check(text.replace(' - < PROMPT.md',' - < ~/.ssh/id_rsa')))
        self.assertFalse(self.check(text.replace("<<'DOC'",'<<DOC')))
        self.assertFalse(self.check(text.replace('cat >','cat >>')))
        self.assertFalse(self.check(text+'; printf evil > dispatch.sh; bash dispatch.sh'))
        self.assertFalse(self.check('printf evil > dispatch.sh; bash dispatch.sh'))
        script=self.cwd/'loop.sh';script.write_text('bash '+str(script))
        self.assertFalse(self.check('bash '+str(script)))

    def test_unique_literal_path_binding_is_not_an_unknown_read(self):
        self.assertTrue(self.check('p=/tmp/agentj-test/report.log; cat "$p"'))
        self.assertTrue(self.check("p=/tmp/agentj-test/report.log; bash -c 'cat \"$p\"'"))
        self.assertFalse(self.check('p=/tmp/agentj-test/report.log; systemd-run --user --on-active=2 agentj service restart; tail "$p"'))
        for text in ('p=~/.ssh/id_rsa; cat "$p"',
                     'p=/tmp/report.log; p=~/.ssh/id_rsa; cat "$p"',
                     'cat "$p"; p=/tmp/report.log',
                     'if false; then p=/tmp/report.log; fi; cat "$p"',
                     'while false; do p=/tmp/report.log; done; cat "$p"',
                     'p=$(unknown); cat "$p"',
                     'API_TOKEN=/tmp/notes; echo "$API_TOKEN"',
                     'HOME=/tmp; p=/tmp/notes; cat "$p"',
                     'IFS=/; p=/tmp/notes; cat $p',
                     'p=/tmp/notes; printf -v p ~/.ssh/id_rsa; cat "$p"',
                     "p=/tmp/notes; printf '-v' p ~/.ssh/id_rsa; cat \"$p\"",
                     r'''p=/tmp/notes; bash -c $'\x70=~/.ssh/id_rsa; cat "$p"' ''',
                     r'''bash -c $'true; \x48OME=/tmp/secret; cat "$HOME/id_rsa"' ''',
                     "bash -c 'true; HOME=/tmp/secret; cat \"$HOME/id_rsa\"'",
                     'p=/tmp/notes; for p in ~/.ssh/id_rsa; do cat "$p"; done',
                     "p=/tmp/notes; bash -c 'p=~/.ssh/id_rsa; cat \"$p\"'",
                     "p=/tmp/notes; eval 'p=~/.ssh/id_rsa; cat \"$p\"'"):
            self.assertFalse(self.check(text))

    def test_stdin_json_is_data_and_never_a_path_or_program(self):
        import shlex
        from agentj import uv_effect
        program="import json,sys; d=json.load(sys.stdin); print({k:d.get(k) for k in ('state','task')})"
        tail='python3 -c '+shlex.quote(program)
        self.assertTrue(self.check('agentj status --json | '+tail))
        self.assertFalse(self.check('cat ~/.ssh/id_rsa | '+tail))
        for program in ('import json,sys; d=json.load(sys.stdin); print(open(d["path"]).read())',
                        'import json,sys; json.load(sys.stdin.buffer)',
                        'import json,sys; sys.stdin.write("x")',
                        'import json,sys; d=json.load(sys.stdin); exec(d["code"])'):
            self.assertIsNone(uv_effect._python_file_program('python3 -c '+shlex.quote(program),str(self.cwd)))

    def test_script_symlinks_and_startup_mutations_stay_unknown(self):
        script=self.cwd/'real.sh';script.write_text('echo okay')
        link=self.cwd/'link.sh';link.symlink_to(script)
        self.assertFalse(self.check('bash '+str(link)))
        for text in ('env BASH_ENV=evil bash '+str(script), 'HOME=/tmp bash '+str(script), 'bash '+str(script)+'; rm -rf ~/Documents'):
            self.assertFalse(self.check(text))
