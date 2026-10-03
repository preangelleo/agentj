"""Fixtures for the skill & workflow plaza tests (test_market*.py, test_bundle.py, test_minisign.py): small package folders,
a fake control plane for `/v1/host/plaza/pkg/<route>` + the signed download / upload URLs, and a minisign signer (test keys
only — the plaza secret key never leaves Leo's machine). Offline: nothing here opens a socket."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import pathlib
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentj import bundle, cloud, minisign, wire  # noqa: E402
from agentj.state import State  # noqa: E402

API = "http://127.0.0.1:9"
URL_PART = "eyJ2IjoxLCJ2aWQiOiJwdl9BQUFBIn0" + "." + "c2lnbmF0dXJlLXRva2Vu"
# the official catalog, when it sits next to this repo (or $AGENTJARVIS_CATALOG); tests that need it skip otherwise
CATALOG = pathlib.Path(os.environ.get("AGENTJARVIS_CATALOG") or pathlib.Path(__file__).resolve().parents[4] / "agentjarvis-catalog")


# ------------------------------------------------------------------ package folders
def write_files(root: pathlib.Path, files: dict) -> pathlib.Path:
    for p, data in files.items():
        f = root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return root


def skill_manifest(name="demo-skill", **over) -> dict:
    m = {"schema_version": "agentj.catalog/v1", "name": name, "type": "skill", "version": "1.0.0",
         "title_zh": "示例技能", "title_en": "Demo skill", "summary_zh": "一个用于测试的技能。", "summary_en": "A skill for tests.",
         "tags": ["测试", "demo"], "category": "data", "entry": "SKILL.md",
         "requires": {"harness": ["claude_code", "codex", "opencode"], "os": ["linux"],
                      "cli": [{"name": "python3", "version": ">=3.10"}], "python_requirements": None,
                      "env": [{"name": "DEMO_TOKEN", "required": True, "secret": True, "description_zh": "演示用", "how_to_get": "example.com"}],
                      "accounts": [{"name": "Example 账号", "url": "https://example.com/signup"}], "skills": []},
         "install": {"skill_dir_name": name, "post_install": ["python3 -m pip install -r requirements.txt"],
                     "verify": "python3 scripts/demo.py --self-test"},
         "license": "MIT", "upstream": None, "author": "tests", "certified": False, "files": []}
    m.update(over)
    return m


SKILL_FILES = {
    "SKILL.md": "---\nname: demo-skill\ndescription: demo\n---\n\nRead CLAUDE.md and .claude/skills/x first.\n",
    "README.md": "# Demo\n\n中文说明。English summary.\n",
    "scripts/demo.py": "import sys\nif '--self-test' in sys.argv:\n    print('self-test OK')\n",
    ".env.example": "DEMO_TOKEN=\n",
    "references/示例-退换货规则.md": "示例\n",
    "tests/.gitkeep": "",
    ".gitignore": "*.pyc\n",
}


def make_skill(root: pathlib.Path, name="demo-skill", files=None, **over) -> pathlib.Path:
    d = root / name
    d.mkdir(parents=True)
    write_files(d, files if files is not None else SKILL_FILES)
    (d / "manifest.json").write_text(json.dumps(skill_manifest(name, **over), ensure_ascii=False, indent=1))
    return d


def workflow_manifest(name="demo-flow", **over) -> dict:
    m = {"schema_version": "agentj.catalog/v1", "name": name, "type": "workflow", "version": "1.2.0",
         "title_zh": "示例工作流", "title_en": "Demo workflow", "summary_zh": "测试用工作流。", "summary_en": "A workflow for tests.",
         "tags": ["workflow"], "category": "workflow", "entry": "scaffold/CLAUDE.md",
         "requires": {"harness": ["claude_code", "codex", "opencode"], "skills": ["demo-skill", "secret-scan"]},
         "install": {"post_install": ["python3 tools/check.py"], "verify": "python3 scaffold/tools/check.py --self-test"},
         "roles": [{"name": "writer", "file": "scaffold/.claude/agents/writer.md", "summary_zh": "写稿"}],
         "automations": [{"id": "daily", "schedule": "0 8 * * *", "approval": "review", "default": "dormant", "summary_zh": "每天"}],
         "params_schema": "params.schema.json", "license": "MIT", "author": "tests", "certified": False, "files": []}
    m.update(over)
    return m


PARAMS_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "additionalProperties": False,
    "required": ["brand", "chairman"],
    "properties": {
        "brand": {"type": "string", "minLength": 1, "default": "青禾家居", "title": "品牌"},
        "chairman": {"type": "string", "default": "owner"},
        "agent_name": {"type": "string", "default": "小青"},
        "tone": {"type": "string", "enum": ["warm", "plain"], "default": "warm"},
        "platforms": {"type": "array", "items": {"type": "string", "enum": ["douyin", "youtube"]}, "uniqueItems": True,
                      "minItems": 1, "default": ["douyin"]},
        "daily_cap": {"type": "integer", "minimum": 0, "default": 3},
        "price": {"type": "number", "minimum": 0, "default": 19},
        "auto_send": {"type": "boolean", "default": False},
        "slug": {"type": "string", "pattern": "^[a-z0-9-]+$", "default": "greenleaf"},
        "contact": {"type": "string", "format": "email", "default": "you@example.com"},
        "install_date": {"type": "string", "format": "date", "x-auto": "today", "readOnly": True, "default": "2026-01-01"},
        "install_timestamp": {"type": "string", "format": "date-time", "x-auto": "now", "readOnly": True, "default": "2026-01-01T00:00:00Z"},
        "review_date": {"type": "string", "format": "date", "x-auto": "today+90d", "readOnly": True, "default": "2026-04-01"},
        "harness": {"type": "string", "enum": ["claude_code", "codex", "opencode"], "x-auto": "harness", "readOnly": True,
                    "default": "claude_code"},
    },
}

WORKFLOW_FILES = {
    "README.md": "# Demo workflow\n",
    "params.schema.json": json.dumps(PARAMS_SCHEMA, ensure_ascii=False, indent=1),
    "scaffold/CLAUDE.md": "# {{brand}} — {{agent_name}}\nharness={{harness}}\n",
    "scaffold/AGENTS.md": "# {{brand}} — {{agent_name}}\n",
    "scaffold/documentation/CONSTITUTION.md": "---\nverified: [{ by: human:{{chairman}}, at: {{install_timestamp}} }]\nstale_after: {{review_date}}\n---\n\n"
                                             "品牌 {{brand}}；平台 {{platforms}}；自动发送 {{auto_send}}；上限 {{daily_cap}}；价格 {{price}}\n",
    "scaffold/documentation/ROLES.md": "---\nverified: [{ by: human:{{chairman}}, at: {{install_date}} }]\n---\n董事长 {{chairman}}\n",
    "scaffold/documentation/configuration.json": '{\n "brand": "{{brand}}",\n "tone": "{{tone}}",\n "platforms": "{{platforms}}",\n'
                                                 ' "chairman": "human:{{chairman}}",\n "date": "{{install_date}}"\n}\n',
    "scaffold/site/index.html": '<title>{{brand}}</title><meta content="{{brand}}">\n',
    "scaffold/config.toml": 'brand = "{{brand}}"\n',
    "scaffold/.claude/agents/writer.md": "---\nname: writer\ndescription: {{brand}} 的写手 \"quoted\" \\ back\n---\n\n你是 {{brand}} 的写手。\n",
    "scaffold/.claude/skills/start-session/SKILL.md": "---\nname: start-session\n---\nstart\n",
    "scaffold/tools/check.py": "import sys\nprint('ok' if '--self-test' in sys.argv else 'run')\n",
    "scaffold/reports/.gitkeep": "",
    "scaffold/.gitignore": ".env\n",
    "scaffold/.handoff": "handoff\n",
}


def make_workflow(root: pathlib.Path, name="demo-flow", files=None, **over) -> pathlib.Path:
    d = root / name
    d.mkdir(parents=True)
    write_files(d, files if files is not None else WORKFLOW_FILES)
    (d / "manifest.json").write_text(json.dumps(workflow_manifest(name, **over), ensure_ascii=False, indent=1))
    return d


# ------------------------------------------------------------------ minisign (test keys)
class TestKey:
    def __init__(self):
        self.sk = Ed25519PrivateKey.generate()
        self.kid = os.urandom(8)
        pk = self.sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.pub_line = base64.b64encode(b"Ed" + self.kid + pk).decode()
        self.key_id = minisign.key_id_hex(self.kid)
        self.keyring = {self.key_id: self.pub_line}

    def sign(self, message: bytes, tc: str, alg=b"ED", global_tc: str | None = None) -> str:
        digest = hashlib.blake2b(message, digest_size=64).digest() if alg == b"ED" else message
        sig = self.sk.sign(digest)
        g = self.sk.sign(sig + (global_tc if global_tc is not None else tc).encode())
        return (f"untrusted comment: signature from minisign secret key\n{base64.b64encode(alg + self.kid + sig).decode()}\n"
                f"trusted comment: {tc}\n{base64.b64encode(g).decode()}\n")

    def sign_package(self, data: bytes, m: dict) -> str:
        return self.sign(data, minisign.trusted_comment(m["name"], m["version"], m["type"], bundle.sha256(data)))


# ------------------------------------------------------------------ the fake control plane
def item_of(m: dict, *, track="community", certified=False, author=None) -> dict:
    return {"name": m["name"], "type": m["type"], "track": track, "certified": certified, "version": m["version"],
            "title": {"zh": m["title_zh"], "en": m["title_en"]}, "summary": {"zh": m["summary_zh"], "en": m["summary_en"]},
            "tags": m.get("tags", []), "category": m.get("category"), "likes": 2, "installs": 5, "installs_week": 1, "liked": False,
            "state": "visible", "author": author or {"kind": "agent", "company": "co-abc123", "agent_name": None, "mine": False},
            "verification": None, "created_at": 1790000000000, "updated_at": 1790000000000}


def detail_of(data: bytes, m: dict, files: dict, *, signature=None, **kw) -> dict:
    d = item_of(m, **kw)
    d.update({"readme": files.get("README.md", b"").decode("utf-8", "replace"), "requires": m.get("requires"),
              "install": m.get("install"), "roles": m.get("roles"), "automations": m.get("automations"),
              "files": [{"path": f["path"], "bytes": f["bytes"]} for f in m["files"]], "license": m.get("license"),
              "upstream": m.get("upstream"), "sha256": bundle.sha256(data), "bytes": len(data), "signature": signature,
              "versions": [{"version": m["version"], "created_at": 1790000000000}]})
    return d


class PkgServer:
    """`post(url, env, max_response)` for the signed routes, `get` for the download URL, `put` for the upload URL."""

    def __init__(self):
        self.calls, self.gets, self.puts = [], [], []
        self.packages: dict = {}       # name → {"data", "manifest", "files", "detail"}
        self.answers: dict = {}        # route → (status, obj) overrides
        self.download_url = f"{API}/v1/plaza/dl/{URL_PART}"
        self.upload_url = f"{API}/v1/plaza/up/{URL_PART}"
        self.serve_bytes = None        # override what the download returns
        self.put_answer = (200, {"name": "x", "version": "1.0.0", "state": "live"})

    def add(self, data: bytes, *, signature=None, detail_over=None, **kw) -> dict:
        m, files = bundle.parse(data)
        d = detail_of(data, m, files, signature=signature, **kw)
        d.update(detail_over or {})
        self.packages[m["name"]] = {"data": data, "manifest": m, "files": files, "detail": d}
        return d

    def __call__(self, url, env, max_response=None, **kw):
        inner = json.loads(wire.unb64u(env["body"]))
        route = url.rsplit("/", 1)[1]
        pkg = "/v1/host/plaza/pkg/" in url
        self.calls.append({"url": url, "inner": inner, "env": env, "route": route, "pkg": pkg, "max_response": max_response})
        key = ("pkg_" if pkg else "qa_") + route
        if key in self.answers:
            return self.answers[key]
        if not pkg:
            return 200, {"items": []}
        if route == "get":
            p = self.packages.get(inner.get("name"))
            self.last_get = inner.get("name")
            if not p:
                return 404, {"error": "not_found"}
            return 200, {"package": p["detail"], "download": {"url": self.download_url, "expires_at": 1}}
        if route in ("search", "mine"):
            return 200, {"items": [p["detail"] for p in self.packages.values()], "total": len(self.packages), "tags": []}
        if route == "installed":
            return 200, {"name": inner["name"], "installs": 6}
        if route == "like":
            return 200, {"name": inner["name"], "liked": inner["on"], "likes": 3 if inner["on"] else 2}
        if route == "report":
            return 201, {"name": inner["name"], "reported": True, "hidden": False}
        if route == "publish":
            return 201, {"id": "pk_" + "A" * 22, "version_id": "pv_" + "B" * 22, "upload": {"url": self.upload_url, "expires_at": 1}}
        return 400, {"error": "bad_request"}

    def get(self, url, max_bytes=None, timeout=None):
        self.gets.append(url)
        if self.serve_bytes is not None:
            return 200, self.serve_bytes
        return 200, self.packages[self.last_get]["data"]

    def put(self, url, body, timeout=None):
        self.puts.append((url, body))
        return self.put_answer


class Env:
    """A temp HOME / workspace / state dir, linked to the fake API; restores os.environ afterwards."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.st = State(self.root / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        cloud.write_cloud(self.st, {"api": API, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                                    "linked_at": 1, "last_seq": 0})
        self.st.set_agent_name("助理一号")
        env = {k: v for k, v in os.environ.items() if k not in ("XDG_CONFIG_HOME", "AGENT_WORKSPACE", "AGENTJ_API_URL")}
        env["HOME"] = str(self.home)
        self._patch = mock.patch.dict(os.environ, env, clear=True)
        self._patch.start()

    def close(self):
        self._patch.stop()
        self.tmp.cleanup()

    def tree(self, base: pathlib.Path | None = None) -> list[str]:
        base = base or self.home
        return sorted(str(p.relative_to(base)) for p in base.rglob("*"))


@contextlib.contextmanager
def env():
    e = Env()
    try:
        yield e
    finally:
        e.close()
