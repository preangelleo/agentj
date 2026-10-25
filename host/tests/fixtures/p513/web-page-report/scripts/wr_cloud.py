"""Cloudflare 一次性部署（init）与日常管理客户端（publish/list/revoke/extend）。纯标准库。

凭据分两级（最小权限）：
- CLOUDFLARE_API_TOKEN：只在 init / upgrade 用，建 R2 桶、KV、D1（统计）、上传 Worker、绑二级域名。用完可在控制台删掉。
- 管理令牌：init 本地生成，Worker 只存它的 SHA-256；日常发布只用它，不再需要 Cloudflare token。
所有输出都不打印令牌、密钥本体。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API = "https://api.cloudflare.com/client/v4"
PKG = Path(__file__).resolve().parent.parent
WORKER_JS = PKG / "worker" / "worker.js"
COMPAT_DATE = "2026-09-01"
CRON = "17 3 * * *"


class CloudError(RuntimeError):
    pass


def workspace() -> Path:
    return Path(os.environ.get("AGENT_WORKSPACE") or Path.home() / "agent-workspace").expanduser()


def config_path() -> Path:
    return Path(os.environ.get("WEB_REPORT_CONFIG") or workspace() / ".web-report" / "config.json").expanduser()


def read_env_file(path: Path, names: set[str]) -> dict:
    """把 dotenv 当数据解析（不 source），只取需要的键；文件对组/其他人可读时拒绝。"""
    out = {}
    if not path.is_file():
        return out
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise CloudError(f"凭据文件权限过宽，请先 chmod 600：{path.name}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        if k in names:
            out[k] = v.strip().strip('"').strip("'")
    return out


def env_values(names: list[str], env_file: str | None = None) -> dict:
    vals = {n: os.environ[n] for n in names if os.environ.get(n)}
    missing = set(names) - set(vals)
    if missing:
        f = Path(env_file).expanduser() if env_file else workspace() / ".env"
        vals.update({k: v for k, v in read_env_file(f, missing).items() if v})
    return vals


def load_config() -> dict:
    p = config_path()
    if not p.is_file():
        raise CloudError("还没初始化：先运行 web_report.py init（见 references/cloudflare-setup.md）")
    if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise CloudError("配置文件权限过宽，请 chmod 600")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    tok = os.environ.get("WEB_REPORT_ADMIN_TOKEN")
    if tok:
        cfg["admin_token"] = tok
    return cfg


def save_config(cfg: dict) -> Path:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return p


def _send(req: urllib.request.Request, timeout=60):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Cloudflare:
    def __init__(self, token: str, transport=_send):
        self.token, self.transport = token, transport

    def call(self, method, path, body=None, *, raw=None, ctype="application/json", ok_codes=()):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": ctype,
                                              "User-Agent": "web-page-report/1.0"})
        status, out = self.transport(req)
        try:
            js = json.loads(out or b"{}")
        except ValueError:
            js = {}
        if status >= 400 or js.get("success") is False:
            codes = [e.get("code") for e in js.get("errors") or []]
            if any(c in ok_codes for c in codes):
                return {"_exists": True, "errors": codes}
            msg = "; ".join(f'{e.get("code")}: {e.get("message")}' for e in js.get("errors") or []) or f"HTTP {status}"
            raise CloudError(f"Cloudflare API {method} {path.split('?')[0]} 失败：{msg}")
        return js.get("result", js)

    def verify(self):
        return self.call("GET", "/user/tokens/verify")

    def zones(self):
        return self.call("GET", "/zones?per_page=50")

    def ensure_bucket(self, account, name):
        r = self.call("POST", f"/accounts/{account}/r2/buckets", {"name": name}, ok_codes=(10004,))
        return "exists" if isinstance(r, dict) and r.get("_exists") else "created"

    def ensure_kv(self, account, title):
        for ns in self.call("GET", f"/accounts/{account}/storage/kv/namespaces?per_page=100") or []:
            if ns.get("title") == title:
                return ns["id"], "exists"
        return self.call("POST", f"/accounts/{account}/storage/kv/namespaces", {"title": title})["id"], "created"

    def ensure_d1(self, account, name):
        found = self.call("GET", f"/accounts/{account}/d1/database?name={urllib.parse.quote(name)}") or []
        for db in found:
            if db.get("name") == name:
                return db["uuid"], "exists"
        return self.call("POST", f"/accounts/{account}/d1/database", {"name": name})["uuid"], "created"

    def upload_worker(self, account, script, bindings, keep_secrets=False):
        meta = {"main_module": "worker.js", "compatibility_date": COMPAT_DATE, "bindings": bindings}
        if keep_secrets:
            meta["keep_bindings"] = ["secret_text"]
        boundary = "----wr" + uuid.uuid4().hex
        parts = [("metadata", "blob", "application/json", json.dumps(meta).encode()),
                 ("worker.js", "worker.js", "application/javascript+module", WORKER_JS.read_bytes())]
        body = b""
        for name, fname, ctype, data in parts:
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{fname}\"\r\n"
                     f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        return self.call("PUT", f"/accounts/{account}/workers/scripts/{script}", raw=body,
                         ctype=f"multipart/form-data; boundary={boundary}")

    def attach_domain(self, account, zone_id, hostname, script):
        return self.call("PUT", f"/accounts/{account}/workers/domains",
                         {"environment": "production", "hostname": hostname, "service": script, "zone_id": zone_id})

    def set_cron(self, account, script):
        return self.call("PUT", f"/accounts/{account}/workers/scripts/{script}/schedules", [{"cron": CRON}])


def mail_bindings(provider: str, env: dict) -> tuple[list, list[str]]:
    """返回 (bindings, 缺失变量名)。provider: mailgun | resend | none。"""
    b = [{"type": "plain_text", "name": "MAIL_PROVIDER", "text": provider}]
    if provider == "none":
        return b, []
    need = {"mailgun": ["MAILGUN_API_KEY", "MAILGUN_DOMAIN", "MAILGUN_FROM"], "resend": ["RESEND_API_KEY", "RESEND_FROM"]}[provider]
    missing = [n for n in need if not env.get(n)]
    if missing:
        return b, missing
    if provider == "mailgun":
        b += [{"type": "plain_text", "name": "MAIL_FROM", "text": env["MAILGUN_FROM"]},
              {"type": "plain_text", "name": "MAILGUN_DOMAIN", "text": env["MAILGUN_DOMAIN"]},
              {"type": "plain_text", "name": "MAILGUN_REGION", "text": env.get("MAILGUN_REGION") or "us"},
              {"type": "secret_text", "name": "MAILGUN_API_KEY", "text": env["MAILGUN_API_KEY"]}]
    else:
        b += [{"type": "plain_text", "name": "MAIL_FROM", "text": env["RESEND_FROM"]},
              {"type": "secret_text", "name": "RESEND_API_KEY", "text": env["RESEND_API_KEY"]}]
    return b, []


def new_admin_token() -> tuple[str, str]:
    tok = "wra_" + secrets.token_urlsafe(32)
    return tok, hashlib.sha256(tok.encode()).hexdigest()


def random_key() -> str:
    """新的 48 字节随机串（URL 安全），用作 Worker 会话签名密钥。"""
    return secrets.token_urlsafe(48)


def secret_bindings(admin_digest: str) -> list:
    """Worker 需要的两个密钥绑定：管理令牌的 SHA-256 摘要 + 新生成的会话签名密钥。"""
    names = ("ADMIN_TOKEN_HASH", "SESSION_SECRET")
    values = (admin_digest, random_key())
    return [{"type": "secret_text", "name": n, "text": v} for n, v in zip(names, values)]


def attach_admin(cfg: dict, value: str) -> None:
    """把本机管理令牌放进配置（配置文件权限 600，绝不打印）。"""
    cfg["admin_token"] = value


def hash_code(code: str, iterations: int = 100_000) -> dict:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", code.encode(), salt, iterations)
    b = lambda x: base64.urlsafe_b64encode(x).decode().rstrip("=")
    return {"alg": "pbkdf2-sha256", "iter": iterations, "salt": b(salt), "hash": b(dk)}


def owner_email_from_agentj() -> str | None:
    """读 Agent J 主人联系卡 0 号位的邮箱（`agentj contacts owner show --json`）；命令不存在或没填就返回 None。"""
    import shutil
    import subprocess
    exe = shutil.which("agentj")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "contacts", "owner", "show", "--json"], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if isinstance(data, dict):
        data = data.get("owner", data)
        for k in ("email", "primary_email", "main_email"):
            if isinstance(data.get(k), str) and "@" in data[k]:
                return data[k].strip()
    return None


class Admin:
    """调用自己 Worker 的 /_wr/admin/*（只需管理令牌）。"""

    def __init__(self, cfg: dict, transport=_send):
        self.base = cfg["base_url"].rstrip("/")
        self.token = cfg.get("admin_token") or ""
        self.transport = transport
        if not self.token:
            raise CloudError("缺少管理令牌（配置文件或 WEB_REPORT_ADMIN_TOKEN）")

    def call(self, method, path, body=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        req = urllib.request.Request(self.base + "/_wr/admin" + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": ctype,
                                              "User-Agent": "web-page-report/1.0"})
        status, out = self.transport(req)
        try:
            js = json.loads(out or b"{}")
        except ValueError:
            js = {"error": f"HTTP {status}"}
        if status >= 400:
            raise CloudError(f"报告服务 {method} {path} 失败：{js.get('error') or status}")
        return js

    def ping(self):
        return self.call("GET", "/ping")

    def put_file(self, rid, rel, data: bytes, ctype):
        return self.call("PUT", f"/r/{rid}/file/{urllib.parse.quote(rel)}", raw=data, ctype=ctype)

    def put_meta(self, rid, meta):
        return self.call("PUT", f"/r/{rid}/meta", meta)

    def get(self, rid):
        return self.call("GET", f"/r/{rid}")

    def stats(self, rid):
        return self.call("GET", f"/r/{rid}/stats")

    def notify_owner(self, rid, subject, text):
        return self.call("POST", f"/r/{rid}/notify-owner", {"subject": subject, "text": text})

    def patch(self, rid, patch):
        return self.call("PATCH", f"/r/{rid}/meta", patch)

    def list(self):
        return self.call("GET", "/r").get("reports", [])

    def revoke(self, rid):
        return self.call("DELETE", f"/r/{rid}")
