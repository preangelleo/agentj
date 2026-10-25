"""P513k operator/redirection matrix; classify only, never run shell inputs."""
import _hermetic
import unittest
from agentj import approval_jev, danger
from agentj.static_shell import resolve


# command, trusted launch snapshot, expected gray floor
MATRIX = [
    (prefix + '$PY -m pytest' + suffix, env, gray)
    for prefix, suffix in (
        ('', '; git status'), ('', ' && git status'), ('', ' || git status'),
        ('', ' | tail -20'), ('', ' & git status'), ('', ' > out.log'),
        ('', ' >> out.log'), ('', ' 2>&1'), ('', ' < input.txt'),
        ('', ' -q 2>&1 | tail -20'), ('cd x && ', ''),
        ('', ' \\\n-q'), ('', ' > "out; && | log"'),
        ('> out.log ', ''), ('', ' 2>> errors.log < input.txt'),
    )
    for env, gray in (({'PY': 'python3'}, False), ({}, True))
] + [
    ('PY=python3; printf ok; $PY -m pytest', {}, False),
    ('PY=python3; echo start >> log; $PY a.py 2>&1 | tail -5', {}, False),
    ('export PY=python3; cd x && $PY -m pytest -q', {}, False),
    ('PY=python3; printf "%s\\n" ok | tail -5; $PY -m pytest', {}, False),
    ('PY=python3 > /dev/null; $PY -m pytest', {}, True),  # known conservative item
    ('PY=python3; printf -v PY rm; $PY a.py', {}, True),
    ('PY=python3; printf -vPY rm; $PY a.py', {}, True),
    ('PY=python3; printf "%n" PY; $PY a.py', {}, True),
    ('PY=python3; printf "%*n" 1 PY; $PY a.py', {}, True),
    ('PY=python3; printf "$FMT" PY; $PY a.py', {}, True),
    ("PY=py'thon3'; $PY -m pytest > out.log", {}, False),
    ('PY=py\\thon3; $PY -m pytest', {}, False),
    ('PY=python3; $PY a.py > out.log', {}, False),
    ('PY=python3; $PY -m pytest && $PY -m pytest', {}, False),
    ('PY=python3; "$PY" -m pytest 2>&1 | tail -20', {}, False),
    ('PY=python3; $PY -m pytest # $UNKNOWN ignored', {}, False),
    ('PY=python3; PY=rm | cat; $PY old', {}, True),
    ('false && PY=python3; $PY a.py', {}, True),
    ('PY=python3; PY=rm & $PY old', {}, True),
    ('PY=python3; PY=rm && true & $PY old', {}, True),
    ('PY=python3; false &&\n PY=rm; $PY a.py', {}, True),
    ('PY=python3; false ||\n PY=rm; $PY a.py', {}, True),
    ('PY=python3; PY=rm > out; $PY a.py', {}, True),
    ('PY=python3; echo ${PY:=rm}; $PY a.py', {}, True),
    ('PY=python3; read PY < input; $PY a.py', {}, True),
    ('PY=python3; PY=$(which python3); $PY a.py', {}, True),
    ('PY=python3; PY=`which python3`; $PY a.py', {}, True),
    ('if true; then PY=python3; fi; $PY a.py', {}, True),
    ('for x in a; do PY=python3; done; $PY a.py', {}, True),
    ('(PY=python3); $PY a.py', {}, True),
    ('PY=python3; ${!PY} a.py', {}, True),
    ('PY=python3; eval "$UNKNOWN"; $PY a.py', {}, True),
]


class ShellMatrix(unittest.TestCase):
    def test_matrix(self):
        for command, env, gray in MATRIX:
            with self.subTest(command=command, env=env):
                v = danger.classify('Bash', {'command': command}, env=env)
                self.assertEqual(approval_jev.gray(v), gray, (resolve(command, env), v.rules))
                if not gray:
                    self.assertFalse(v.danger, v.rules)

    def test_danger_survives_each_operator(self):
        for suffix in ('; pwd', ' && pwd', ' || pwd', ' | cat', ' & pwd',
                       ' > out', ' >> out', ' 2>&1', ' < input'):
            command = 'X=rm; $X old' + suffix
            with self.subTest(command=command):
                self.assertIn('delete.rm', danger.classify('Bash', {'command': command}).rules)

    def test_spans_quotes_and_continuations(self):
        for raw, expected in (
            ('OUT=.env; PY=python3 > $OUT', 'OUT=.env; PY=python3 > .env'),
            ('PY=python3; $PY > "$PY.log"', 'PY=python3; python3 > "python3.log"'),
            ('PY=python3; $P\\\nY -m pytest', 'PY=python3; python3 -m pytest'),
            ("PY=python3; echo '$PY; && |' \\$PY", "PY=python3; echo '$PY; && |' \\$PY"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(resolve(raw), expected)


class ClassifyCLI(unittest.TestCase):
    def test_module_is_read_only_and_ignores_ambient_interpreter(self):
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'must-not-exist'
            command = f'PY=python3; printf ok > {marker}; $PY -m pytest'
            def probe(cmd):
                result = subprocess.run(
                    [sys.executable, '-m', 'agentj.static_shell', '--classify', cmd],
                    cwd=Path(__file__).resolve().parents[1],
                    env={**os.environ, 'PY': 'python3'}, capture_output=True, text=True, check=True)
                return json.loads(result.stdout)
            result = probe(command)
            self.assertFalse(result['gray'])
            self.assertFalse(result['danger'])
            self.assertIn('python3 -m pytest', result['resolved'])
            self.assertFalse(marker.exists())
            self.assertTrue(probe('$PY -m pytest')['gray'])
