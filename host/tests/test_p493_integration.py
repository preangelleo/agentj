"""P493 keeps both independently added diagnostic command families."""
import _hermetic  # noqa: F401
import argparse
import contextlib
import ast
import inspect
import io
import unittest
from agentj import cli, field_tools, layout_diag


class DiagnosticCommands(unittest.TestCase):
    def parser(self):
        # Execute only the real parser assembly: no startup, credential loading or command handler.
        main = ast.parse(inspect.getsource(cli.main)).body[0]
        start = next(i for i, node in enumerate(main.body) if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'p' for target in node.targets))
        end = next(i for i, node in enumerate(main.body) if isinstance(node, ast.Assign)
                   and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                   and node.value.func.attr == 'parse_args')
        namespace = dict(vars(cli))
        exec(compile(ast.Module(body=main.body[start:end], type_ignores=[]), cli.__file__, 'exec'), namespace)
        return namespace['p']

    def test_root_keeps_export_layout_and_preflight_handlers(self):
        parser = self.parser()
        export = parser.parse_args(['diag', 'export'])
        self.assertIs(export.fn, field_tools.command)
        self.assertEqual(export.output, 'agentj-diagnostic.json')
        layout = parser.parse_args(['diag', 'layout', '--last'])
        self.assertTrue(layout.last)
        self.assertEqual(layout.fn.__module__, layout_diag.__name__)
        flight = parser.parse_args(['preflight', '--offline'])
        self.assertTrue(flight.offline)
        self.assertIs(flight.fn, field_tools.command)

    def test_layout_last_is_required_and_diagnostics_never_migrate(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.parser().parse_args(['diag', 'layout'])
        self.assertEqual(error.exception.code, 2)
        self.assertIn('diag', cli.NO_MIGRATE)
        self.assertIn('preflight', cli.NO_MIGRATE)

    def test_legacy_layout_parser_entry_still_works(self):
        parser = argparse.ArgumentParser()
        layout_diag.add_parser(parser.add_subparsers(dest='cmd'))
        self.assertTrue(parser.parse_args(['diag', 'layout', '--last']).last)
