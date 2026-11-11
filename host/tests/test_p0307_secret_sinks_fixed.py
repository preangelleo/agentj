"""0.30.7 approval round 2 — P541 leftover N5b: every secret card with a sink writes to a target fixed by host code.

Self-checking enumeration: the AST of every module under agentj/ is scanned for any construction of a card dict carrying a
'sink' (dict literal key, `x['sink'] = …`, `dict(sink=…)`; r1 B-4: also `.update(sink=…)` / any `sink=` keyword,
`.setdefault('sink', …)`, and any stray 'sink' literal that is neither a dict-literal key nor a plain read). Each site must
be registered below; a new sink site without a registration fails `test_every_sink_site_is_registered`. For each registered secret site the test then pins:
  * the exact set of calls inside the sink (and the helpers it delegates to) — a new write path in a sink is noticed;
  * the target arguments of each write call reference only host-held names (self / the host-built flow or process) or
    locals whose every assignment is the registered host expression — never the sealed value or a caller parameter;
  * the card's dest_spec is host-built (no caller parameter other than `self` / a constant name).
Agent-supplied input never carries a sink: `norm_secret` (the Agent's `agentj secret` path) builds a fresh dict without
one, and `Elevator.request` refuses a non-callable sink (anything off the wire).
"""
import _hermetic  # noqa: F401,I001
import ast
import asyncio
import functools
import pathlib
import tempfile
import unittest
from types import SimpleNamespace

AGENTJ = pathlib.Path(__file__).resolve().parents[1] / 'agentj'
VALUE_NAMES = {'value', 'pw', 'tok', 'code', 's'}     # the sealed value and its decodings inside a sink

# (module path relative to agentj/, enclosing qualname, kind literal or None for a wrapper) → registration
SECRET = {
    ('mail.py', 'Mail.secret_card', "'secret'"): {
        'why': 'owner mail app password → the encrypted mail secret for the configured address in the state dir',
        'sinks': {'Mail.secret_card.sink': {
            "''.join", 'any', 'bool', 'bytearray', 'bytes', 'bytes(value).decode', 'elevate.sink_write_gate', 'len', 'ord',
            'password_provider', 'pw.encode', 'pw.split', 'read_config', 'read_state', 'res.get', 'save_state',
            'self.wake.set', 'store_secret', 'test_connection'}},
        'writes': {'store_secret': (0, 2), 'save_state': (0,)},
        'origins': {'cfg': {'read_config(self.st)'}, 'path': {'str(root(self.st) / SECRET)'}, 'st2': {'read_state(self.st)'}},
        'gate_before': {'Mail.secret_card.sink': ('test_connection', 'store_secret')},
        'scopes': ('Mail.secret_card',),
    },
    ('auth_rescue.py', 'Rescue._card', "'secret'"): {
        'why': 'Claude setup-token / auth code → the Agent J service env file, or a NEW host-numbered account slot',
        'sinks': {
            'Rescue._claude_round.sink': {'bytes', 'bytes(value).decode', 'flow.submit', 'self._store_token', 'self.why'},
            'Rescue._claude_paste.sink': {'bytearray', 'bytes', 'bytes(value).strip', 'self._store_token'},
            'Rescue._store_token': {
                '(self.flow or {}).get', 'Path', 'Path(path).parent.mkdir', 'accounts.add_claude_token',
                'accounts.on_rescue_token', 'date.today', 'date.today().isoformat', 'elevate.fingerprint',
                'elevate.sink_write_gate', 'elevate.write_secret', 'int', 'isinstance', 'len', 'live_claude', 'meta.get',
                'outcome.pop', 'range', 're.fullmatch', 'read_meta', 'self.lang', 'self.why', 'service_env_path',
                'time.time', 'tok.decode', 'token_due', 'write_meta'}},
        # flow.submit: the host-built ClaudeFlow (Rescue._claude: ClaudeFlow(route)); its target is Claude's own login.
        'writes': {'flow.submit': (), 'elevate.write_secret': (0,), 'accounts.add_claude_token': (0, 1),
                   'accounts.on_rescue_token': (0,), 'write_meta': (0,), 'Path(path).parent.mkdir': ()},
        'origins': {'path': {'service_env_path()'}, 'acct': {'(self.flow or {}).get(\'account\')'},
                    'meta': {'read_meta(self.host.st)'}, 'flow': {'ClaudeFlow(route)'}},
        'gate_before': {'Rescue._store_token': ('live_claude', 'accounts.add_claude_token')},
        'callers_constant_name': 'self._card',
        'scopes': ('Rescue._store_token', 'Rescue._claude'),   # _claude builds the flow; _claude_round / _paste the sinks
    },
    ('setup_native.py', 'login', "'secret'"): {
        'why': 'owner Claude authorization code → typed into the host-started `claude` login PTY (not stored)',
        'sinks': {'login.sink': {'bytes', 'len', 'proc.write'}},
        'writes': {'proc.write': ()},
        'origins': {'proc': {'LoginProcess([exe] + LOGIN_ARGV[cli], env)'}},
        'scopes': ('login',),
    },
}
OTHER = {
    ('terminal.py', 'Desk._login', "'login'"): 'web-terminal unlock; sink = the host PTY of this signed term_open (0.30.1 rule)',
    ('term_vshare.py', 'VshareDesk._vshare', "'vshare'"): 'password share between the owner\'s own remotes; host vault sink',
    ('assist_terminal.py', '_Elevate.request', None): 'wrapper: re-guards the terminal Desk login card sink (no new target)',
    ('elevate.py', 'Elevator._request_scoped', None): 'review digest binding: replaces the callable by a private token',
}


@functools.lru_cache(maxsize=None)
def _parse(rel):
    return ast.parse((AGENTJ / rel).read_text(encoding='utf-8'))


def _functions(tree):
    out = {}

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = stack + [child.name]
                if not isinstance(child, ast.ClassDef):
                    out['.'.join(q)] = child
                walk(child, q)
            else:
                walk(child, stack)
    walk(tree, [])
    return out


def _is_read_use(const, parent):
    """A 'sink' literal that only READS a card's sink: c.get('sink') · c['sink'] (load) · 'sink' in c."""
    if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == 'get' \
            and parent.args and parent.args[0] is const:
        return True
    if isinstance(parent, ast.Subscript) and parent.slice is const and isinstance(parent.ctx, ast.Load):
        return True
    if isinstance(parent, ast.Compare) and parent.left is const and all(isinstance(o, (ast.In, ast.NotIn)) for o in parent.ops):
        return True
    return False


def scan_tree(tree, rel):
    """(sites, odd) for one module: dict-literal sink sites, and every other way a 'sink' could be attached to a dict —
    `x['sink'] = …`, `x.update(sink=…)` / any `f(sink=…)` keyword, `x.setdefault('sink', …)`, a computed key, or any
    stray 'sink' string literal that is neither a registered dict-literal key nor a plain read (r1 B-4)."""
    found, odd = set(), []
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    stack = []

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, n):
            stack.append(n.name); self.generic_visit(n); stack.pop()
        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Dict(self, n):
            keys = [k.value if isinstance(k, ast.Constant) else None for k in n.keys]
            if 'sink' in keys:
                kind = next((ast.unparse(v) for k, v in zip(keys, n.values) if k == 'kind'), None)
                found.add((rel, '.'.join(stack), kind))
            self.generic_visit(n)

        def visit_Call(self, n):
            for k in n.keywords:
                if k.arg == 'sink':
                    odd.append(f'{rel}:{n.lineno} {ast.unparse(n.func)}(sink=…)')
            self.generic_visit(n)

        def visit_Constant(self, n):
            if n.value != 'sink':
                return
            parent = parents.get(n)
            if isinstance(parent, ast.Dict) and any(k is n for k in parent.keys):
                return                                   # a dict-literal key: the site is registered (or flagged) above
            if _is_read_use(n, parent):
                return
            if isinstance(parent, ast.Subscript) and isinstance(parent.ctx, ast.Store):
                odd.append(f'{rel}:{n.lineno} subscript sink assignment')
            elif isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == 'setdefault':
                odd.append(f'{rel}:{n.lineno} setdefault(\'sink\', …)')
            else:
                odd.append(f'{rel}:{n.lineno} stray \'sink\' literal ({type(parent).__name__})')
    V().visit(tree)
    return found, odd


def sink_sites():
    """Every place under agentj/ that builds a dict carrying 'sink' → {(file, qualname, kind-literal or None)}."""
    found, odd = set(), []
    for path in sorted(AGENTJ.rglob('*.py')):
        rel = path.relative_to(AGENTJ).as_posix()
        f, o = scan_tree(_parse(rel), rel)
        found |= f
        odd += o
    return found, odd


class SinksFixed(unittest.TestCase):
    def test_every_sink_site_is_registered(self):
        found, odd = sink_sites()
        self.assertEqual(odd, [], 'register the sink in a card dict literal so this test can see it')
        self.assertEqual(found - set(SECRET) - set(OTHER), set(), 'new sink site: register it (and its fixed target) here')
        self.assertEqual((set(SECRET) | set(OTHER)) - found, set(), 'stale registration')

    def test_secret_sink_calls_and_targets_are_host_fixed(self):
        for (rel, where, _kind), reg in SECRET.items():
            with self.subTest(site=rel + ':' + where):
                tree = _parse(rel)
                fns = _functions(tree)
                for sink_name, calls in reg['sinks'].items():
                    fn = fns[sink_name]
                    got = {ast.unparse(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)}
                    self.assertEqual(got, calls, f'{sink_name}: calls changed — review the write target, then update')
                    for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
                        name = ast.unparse(call.func)
                        if name not in reg['writes']:
                            continue
                        exprs = [call.args[i] for i in reg['writes'][name] if i < len(call.args)]
                        if isinstance(call.func, ast.Attribute):
                            exprs.append(call.func.value)       # the receiver (proc / flow / Path(path)) is a target too
                        for expr in exprs:
                            for n in (x for x in ast.walk(expr) if isinstance(x, ast.Name)):
                                self.assertNotIn(n.id, VALUE_NAMES, f'{sink_name}: {name} target depends on the value')
                                if n.id in ('self', 'elevate', 'accounts', 'Path', 'str', 'root', 'SECRET', 'bytearray'):
                                    continue
                                self.assertIn(n.id, reg['origins'], f'{sink_name}: {name} target uses {n.id}')
                # every assignment of a target local (in the site's own scopes) is the registered host expression
                scoped = [fn for q, fn in fns.items() if q.startswith(reg['scopes'])]
                for local, allowed in reg['origins'].items():
                    seen = {ast.unparse(a.value) for fn in scoped for a in ast.walk(fn) if isinstance(a, ast.Assign)
                            for t in a.targets if isinstance(t, ast.Name) and t.id == local}
                    self.assertTrue(seen, f'{local}: no assignment found')
                    self.assertLessEqual(seen, allowed, f'{local} assigned from {seen - allowed}')

    def test_validating_sinks_gate_between_check_and_write(self):
        for (rel, _where, _kind), reg in SECRET.items():
            for sink_name, (check, write) in (reg.get('gate_before') or {}).items():
                fn = _functions(_parse(rel))[sink_name]
                lines = {}
                for c in (x for x in ast.walk(fn) if isinstance(x, ast.Call)):
                    lines.setdefault(ast.unparse(c.func), c.lineno)
                with self.subTest(sink=sink_name):
                    self.assertLess(lines[check], lines['elevate.sink_write_gate'])
                    self.assertLess(lines['elevate.sink_write_gate'], lines[write])

    def test_dest_spec_is_host_built(self):
        for (rel, where, kind), reg in SECRET.items():
            fns = _functions(_parse(rel))
            fn = fns[where]
            params = {a.arg for a in fn.args.args + fn.args.kwonlyargs} - {'self'}
            for d in (x for x in ast.walk(fn) if isinstance(x, ast.Dict)):
                keys = [k.value if isinstance(k, ast.Constant) else None for k in d.keys]
                if 'sink' not in keys or 'dest_spec' not in keys:
                    continue
                spec = d.values[keys.index('dest_spec')]
                used = {n.id for n in ast.walk(spec) if isinstance(n, ast.Name)} & params
                with self.subTest(site=rel + ':' + where):
                    # Rescue._card: 'key': name — every caller passes a constant name (checked below)
                    self.assertLessEqual(used, {'name'} if reg.get('callers_constant_name') else set())
            if reg.get('callers_constant_name'):
                tree = _parse(rel)
                calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and ast.unparse(c.func) == reg['callers_constant_name']]
                self.assertTrue(calls)
                for c in calls:
                    self.assertIsInstance(c.args[0], ast.Constant)

    def test_scanner_flags_every_other_way_to_attach_a_sink(self):
        """r1 B-4: the forms the dict-literal scan alone would miss."""
        for src, needle in (("def f(c, s):\n    c.update(sink=s)\n", 'c.update(sink=…)'),
                            ("def f(c, s):\n    c.setdefault('sink', s)\n", "setdefault('sink'"),
                            ("def f(c, s):\n    c['sink'] = s\n", 'subscript sink assignment'),
                            ("def f(c, s):\n    c.update({'sink': s})\n", None),     # a dict literal: a site to register
                            ("def f(c, s):\n    k = 'sink'\n    c[k] = s\n", "stray 'sink' literal"),
                            ("def f(c, s):\n    c.update([('sink', s)])\n", "stray 'sink' literal"),
                            ("def f(c, s):\n    return dict(sink=s)\n", 'dict(sink=…)')):
            with self.subTest(src=src):
                found, odd = scan_tree(ast.parse(src), 'fixture.py')
                if needle is None:
                    self.assertEqual((found, odd), ({('fixture.py', 'f', None)}, []))
                else:
                    self.assertTrue(any(needle in o for o in odd), odd)
        for src in ("def f(c):\n    return c.get('sink')\n", "def f(c):\n    return c['sink']\n",
                    "def f(c):\n    return 'sink' in c\n"):
            with self.subTest(read=src):
                self.assertEqual(scan_tree(ast.parse(src), 'fixture.py'), (set(), []))

    def test_validating_sinks_are_marked_by_host_code(self):
        """r1 B-2: the validating sinks (mail password, Claude setup-token / auth code) are marked in host code; the
        terminal login PTY sink and the vshare sink are not (they have no check-then-write gap)."""
        marks = {}
        for rel in ('mail.py', 'auth_rescue.py', 'setup_native.py', 'terminal.py', 'term_vshare.py'):
            fns = _functions(_parse(rel))
            marks[rel] = sorted(q for q, fn in fns.items() for c in ast.walk(fn) if isinstance(c, ast.Call)
                                and ast.unparse(c.func) in ('elevate.validating_sink', '_elevate.validating_sink'))
        self.assertEqual(marks, {'mail.py': ['Mail.secret_card'],
                                 'auth_rescue.py': ['Rescue._claude_paste', 'Rescue._claude_round'],
                                 'setup_native.py': [], 'terminal.py': [], 'term_vshare.py': []})

    def test_agent_input_never_carries_a_sink(self):
        from agentj import elevate
        with tempfile.TemporaryDirectory() as d:
            work, state = pathlib.Path(d) / 'work', pathlib.Path(d) / 'state'
            work.mkdir(); state.mkdir()
            card = elevate.norm_secret({'name': 'TEST_ONLY_KEY', 'purpose': 'Synthetic', 'dest': str(work / 'f'),
                                        'sink': 'agent-supplied', 'dest_spec': {'mode': 'env', 'path': '/etc/x'}}, state)
        self.assertNotIn('sink', card)
        self.assertNotEqual(card['dest_spec'].get('path'), '/etc/x')
        fake = SimpleNamespace(review_operations={}, review_requests=set(), __dict__={})
        res = asyncio.run(elevate.Elevator._request_scoped(fake, {'kind': 'secret', 'sink': {'path': '/etc/x'}}, None, None, None))
        self.assertEqual(res, {'result': 'failed', 'why': 'invalid_sink'})



if __name__ == '__main__':
    unittest.main()
