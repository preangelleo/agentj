"""Resolve only the selected public npm Codex entry and its same-version native package."""
import _hermetic  # noqa: F401

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


class NpmTree:
    def __init__(self, root):
        self.root = Path(root)
        self.base = self.root / "node_modules" / "@openai" / "codex"
        self.js = self.base / "bin" / "codex.js"
        self.js.parent.mkdir(parents=True)
        self.js.write_text("#!/usr/bin/env node\n")
        self.js.chmod(0o700)
        self.wrapper = self.root / "codex"
        self.wrapper.symlink_to(self.js)
        self.main = {"name": "@openai/codex", "version": "0.167.0", "bin": {"codex": "bin/codex.js"},
                     "optionalDependencies": {"@openai/codex-linux-x64": "npm:@openai/codex@0.167.0-linux-x64"}}
        self.meta = {"name": "@openai/codex", "version": "0.167.0-linux-x64", "os": ["linux"], "cpu": ["x64"]}
        self.write_meta(self.base, self.main)

    def launcher_hash(self):
        return hashlib.sha256(self.js.read_bytes()).hexdigest()

    def write_meta(self, root, value):
        (root / "package.json").write_text(json.dumps(value))

    def install_target(self, scope):
        package = scope / "codex-linux-x64"
        exe = package / "vendor" / "x86_64-unknown-linux-musl" / "bin" / "codex"
        exe.parent.mkdir(parents=True)
        shutil.copy2(Path(sys.executable).resolve(), exe)
        self.write_meta(package, self.meta)
        return package, exe



@unittest.skipUnless(sys.platform == "linux", "Linux native test fixture")
class CodexNpmLaunch(unittest.TestCase):
    def setUp(self):
        from agentj import codex_npm
        self.launch = codex_npm.launch
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-npm-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.npm = NpmTree(self.tmp.name)
        self.enterContext(patch.object(codex_npm, "LAUNCHERS", {self.npm.launcher_hash()}))
        self.enterContext(patch.object(codex_npm.platform, "machine", return_value="x86_64"))

    def check_target(self, exe):
        self.assertEqual(self.launch(str(self.npm.wrapper), {}), (str(exe), {
            "CODEX_MANAGED_BY_NPM": "1",
            "CODEX_MANAGED_PACKAGE_ROOT": str(self.npm.base),
        }))

    def test_nested_and_hoisted_published_layouts(self):
        nested, exe = self.npm.install_target(self.npm.base / "node_modules" / "@openai")
        self.check_target(exe)
        shutil.rmtree(nested)
        _, exe = self.npm.install_target(self.npm.base.parent)
        self.check_target(exe)

    def test_wrong_version_and_platform_metadata_fall_back(self):
        package, _ = self.npm.install_target(self.npm.base.parent)
        self.npm.write_meta(package, {**self.npm.meta, "version": "0.168.0-linux-x64"})
        self.assertIsNone(self.launch(str(self.npm.wrapper), {}))
        self.npm.write_meta(package, {**self.npm.meta, "cpu": ["arm64"]})
        self.assertIsNone(self.launch(str(self.npm.wrapper), {}))
        self.npm.write_meta(package, self.npm.meta)
        self.npm.write_meta(self.npm.base, {**self.npm.main, "optionalDependencies": {}})
        self.assertIsNone(self.launch(str(self.npm.wrapper), {}))

    def test_other_owner_launchers_and_path_escape_are_unchanged(self):
        package, exe = self.npm.install_target(self.npm.base.parent)
        custom = self.npm.root / "user-codex-launch"
        custom.write_text("#!/bin/sh\nexit 0\n")
        custom.chmod(0o700)
        self.assertIsNone(self.launch(str(custom)))
        outside = self.npm.root / "other-native-binary"
        exe.rename(outside)
        exe.symlink_to(outside)
        self.assertIsNone(self.launch(str(self.npm.wrapper), {}))
        shutil.rmtree(package)
        self.assertIsNone(self.launch(str(self.npm.wrapper), {}))


    def test_custom_node_environment_and_changed_js_preserve_the_launcher(self):
        self.npm.install_target(self.npm.base.parent)
        for env in ({"NODE_OPTIONS": "--conditions=fixture"}, {"NODE_PATH": "/synthetic/library"}):
            self.assertIsNone(self.launch(str(self.npm.wrapper), env))
        self.npm.js.write_text("#!/usr/bin/env node\n// Owner customization.\n")
        self.assertIsNone(self.launch(str(self.npm.wrapper), {}))
