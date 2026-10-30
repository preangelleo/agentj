"""P373: `agentj report templates | schema | example | validate | render | send | new-template | publish` — the template
publishing protocol for the owner's automations: the model only fills a JSON file, the template owns the look.

    data JSON → strict JSON Schema check (field-path errors, exit 2, nothing rendered or sent) → web page | mail-safe e-mail

The engine and the 15 templates live in the plaza package web-page-report (≥ 1.3; its references/protocol.md is the
protocol text); this module only finds the installed package and runs its CLI, so the host carries no templates.
`send` renders the mail version and hands it to `agentj mail send --format html` in-process: recipients, the allowlist and
final passkey approval are exactly mail's, including `--to-owner`. Invalid data → the engine's exit 2 and nothing is sent.
No --template = the package's default (R15 Agent J Noir). A custom template is a folder (template + schema + example)
by path, or by id under $AGENT_WORKSPACE/report-templates/; the engine refuses scripts and external loads in it.

Exit codes: 0 ok · 1 the mail did not go out · 2 data / argument error · 3 the package is missing (the message says how to
install it) · 4 network / cloud error (publish).
"""
from __future__ import annotations

import json
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

PACKAGE = "web-page-report"
ENGINE = pathlib.Path("scripts") / "web_report.py"
PASS = ("templates", "schema", "example", "recommend", "validate", "render", "new-template", "publish")
INSTALL_HINT = {
    "zh": "这台 Agent J 主机还没装广场包 web-page-report（≥ 1.3，含模板化发布协议）。问过主人后安装：agentj plaza install web-page-report "
          "--harness <claude_code|codex|opencode>，装好再运行本命令。",
    "en": "The plaza package web-page-report (≥ 1.3, with the template publishing protocol) is not installed here. With the "
          "owner's OK: agentj plaza install web-page-report --harness <claude_code|codex|opencode>, then run this again.",
}
OLD_HINT = {
    "zh": "已装的 web-page-report 太旧（没有模板化发布协议）。问过主人后升级：agentj plaza install web-page-report --harness …",
    "en": "The installed web-page-report predates the template protocol. With the owner's OK: agentj plaza install web-page-report --harness …",
}


def _candidates():
    env = os.environ.get("AGENTJ_REPORT_ENGINE")
    if env:
        yield pathlib.Path(env).expanduser()
        return
    try:
        from .market import read_installed, skills_root
        from .state import State
        for r in read_installed(State()):
            if r.get("name") == PACKAGE and isinstance(r.get("target"), str):
                yield pathlib.Path(r["target"])
        for h in ("claude_code", "codex", "opencode"):
            yield skills_root(h) / PACKAGE
    except Exception:   # a broken state dir must not hide a package that is plainly there
        home = pathlib.Path(os.path.expanduser("~"))
        yield from (home / ".claude" / "skills" / PACKAGE, home / ".agents" / "skills" / PACKAGE)


def find_engine() -> pathlib.Path | None:
    for d in _candidates():
        if (d / ENGINE).is_file() and not (d / ENGINE).is_symlink():
            return d
    return None


def _template_path(engine: pathlib.Path, name: str) -> pathlib.Path | None:
    """Resolve the package's v1.3 template contract without running unsigned code.

    Built-ins are already inside the frozen package. Custom IDs must become
    explicit private paths, never be resolved again against the live workspace.
    """
    raw = name.strip()
    registry = json.loads((engine / "templates" / "registry.json").read_text(encoding="utf-8"))
    for row in registry["templates"]:
        if raw.lower() in (row["id"].lower(), row["ref"].lower(), row["slug"]):
            return None
    path = pathlib.Path(raw).expanduser()
    if "/" not in raw and not raw.startswith((".", "~")) and not path.is_dir():
        workspace = pathlib.Path(os.environ.get("AGENT_WORKSPACE") or "~/agent-workspace").expanduser()
        path = workspace / "report-templates" / raw
    if not raw or not path.is_dir():
        raise ValueError("report template does not exist")
    return path


def _run(engine: pathlib.Path, args: list[str], capture=False) -> subprocess.CompletedProcess:
    # A preflight over path names cannot authorize a later, mutable script/data
    # read. Execute only our private byte snapshot after the final action card.
    from . import action_policy
    with tempfile.TemporaryDirectory(prefix="agentj-report-exec-") as tmp:
        root = pathlib.Path(tmp)
        manifest = []
        total = 0
        def freeze(src, dest, content=False):
            nonlocal total
            src = pathlib.Path(src).expanduser()
            if src.is_symlink():
                raise ValueError("report input symlink refused")
            if src.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                for child in sorted(src.iterdir()):
                    if child.name == "__pycache__": continue
                    freeze(child, dest / child.name, content)
                return
            if not src.is_file():
                raise ValueError("report input is not a regular file")
            data = src.read_bytes()
            total += len(data)
            if total > 8 * 1024 * 1024 or len(manifest) >= 1000:
                raise ValueError("report input too large")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            dest.chmod(0o700 if src.stat().st_mode & 0o111 else 0o600)
            row = {"path": str(src.resolve()), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            if content:
                row["content"] = data.decode("utf-8")
            manifest.append(row)
        frozen = root / "engine"
        freeze(engine, frozen)
        final = list(args)
        if args and args[0] in ("validate", "render", "publish"):
            # Missing inputs are errors now, not paths a later engine may read.
            if not args[1] or not pathlib.Path(args[1]).expanduser().is_file():
                raise ValueError("report data file does not exist or is not a regular file")
            target = root / "input-data" / pathlib.Path(args[1]).name
            freeze(args[1], target, content=True)
            final[1] = str(target)
        template_index = (args.index("--template") + 1) if "--template" in args else (
            1 if args and args[0] in ("schema", "example") else None)
        template_rows = []
        if template_index is not None:
            path = _template_path(frozen, args[template_index])
            if path is not None:
                first = len(manifest)
                target = root / "input-template" / path.name
                freeze(path, target, content=True)
                template_rows = manifest[first:]
                final[template_index] = str(target)
        env = dict(os.environ)
        request = {"argv": [sys.executable, "-I", str(engine / ENGINE), *args], "cwd": os.getcwd(),
                   "inputs": manifest, "environment_sha256": hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()}
        if not action_policy.confirm_cli("broker.execute", request):
            return subprocess.CompletedProcess(args, 1, "", "report execution was not confirmed")
        # A changed custom template invalidates this approval. Execution still
        # reads only the snapshot, including if the original changes after here.
        for row in template_rows:
            path = pathlib.Path(row["path"])
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                return subprocess.CompletedProcess(args, 2, "", "report template changed after approval")
        return subprocess.run([sys.executable, "-I", str(frozen / ENGINE), *final], cwd=os.getcwd(), env=env, text=True,
                              capture_output=capture)



def _data_arg(a) -> str | None:
    return a.data_opt or a.data


def _engine_args(a, cmd: str) -> list[str]:
    out = [cmd]
    if cmd in ("schema", "example"):
        out.append(a.data or a.template or "")
    elif cmd == "recommend":
        out.append(a.data or "")
    elif cmd == "new-template":
        out.append(a.data or "")
        if a.id:
            out += ["--id", a.id]
    elif cmd in ("validate", "render", "publish"):
        out.append(_data_arg(a) or "")
        if a.template:
            out += ["--template", a.template]
        if cmd == "render":
            out += ["--out", a.out or "./web-report-preview"] + (["--email"] if a.email else [])
        if cmd == "publish":
            for flag in ("expires", "access"):
                if getattr(a, flag):
                    out += [f"--{flag}", getattr(a, flag)]
            if a.dry_run:
                out.append("--dry-run")
    if a.json:
        out.append("--json")
    return out


def send(a, engine: pathlib.Path, lang: str) -> int:
    data = _data_arg(a)
    if not data:
        print("✗ send 需要数据文件 / send needs the data file", file=sys.stderr)
        return 2
    if not (a.to_owner or a.to):
        print("✗ 发给谁？--to-owner（主人本人）或 --to <地址|通讯录名字> / who to? --to-owner or --to", file=sys.stderr)
        return 2
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="agentj-report-"))
    try:
        mail_file = tmp / "report-email.html"
        args = ["render", data, "--email", "--out", str(mail_file)] + (["--template", a.template] if a.template else [])
        r = _run(engine, args, capture=True)
        if r.returncode != 0:          # invalid data / web-only template: the engine's own words, nothing is sent
            sys.stderr.write(r.stderr or r.stdout)
            print("✗ 没有发送：数据没通过模板校验，按上面的字段路径改好再发 / not sent: fix the fields above and send again"
                  if r.returncode == 2 else "✗ 没有发送 / not sent", file=sys.stderr)
            return r.returncode
        subject = a.subject
        if not subject:
            try:
                subject = str(json.loads(pathlib.Path(data).expanduser().read_text(encoding="utf-8")).get("title") or "")[:200]
            except (OSError, ValueError):
                subject = ""
        if a.dry_run:
            keep = pathlib.Path(a.out).expanduser() if a.out else None
            if keep:
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(mail_file, keep)
            print(json.dumps({"ok": True, "dry_run": True, "subject": subject, "to_owner": bool(a.to_owner), "to": a.to or [],
                              "bytes": mail_file.stat().st_size, "file": str(keep) if keep else None}, ensure_ascii=False))
            return 0
        from . import mail
        ns = SimpleNamespace(action="send", to=a.to, cc=a.cc, bcc=a.bcc, to_owner=a.to_owner, subject=subject or "Agent J",
                             body=None, body_file=str(mail_file), attach=a.attach, format="html", address=None,
                             json=a.json, en=lang == "en")
        return mail.cmd(ns)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd(a) -> int:
    lang = "en" if a.en else "zh"
    if a.action is None:
        print("✗ 要做什么？agentj report templates | validate | render | send … / which action? (alone, `agentj report` reports "
              "this computer's status to the account dashboard)", file=sys.stderr)
        return 2
    engine = find_engine()
    if engine is None:
        print("✗ " + INSTALL_HINT[lang], file=sys.stderr)
        return 3
    if not (engine / "scripts" / "wr_email.py").is_file():
        print("✗ " + OLD_HINT[lang], file=sys.stderr)
        return 3
    try:
        if a.action == "send":
            return send(a, engine, lang)
        result = _run(engine, _engine_args(a, a.action))
        if result.returncode and result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.returncode
    except (OSError, ValueError) as exc:
        print("✗ 报告输入不可用 / report input refused: " + str(exc), file=sys.stderr)
        return 2


def add_parser(sub, status_fn=None) -> None:   # the choices below are spelled out (= PASS + send) for the docs checker
    """`agentj report` alone keeps its old meaning — report this computer's status to the account dashboard now (status_fn,
    cli.cmd_report); with an action it is the template publishing protocol (it runs inside the fence: no state migration)."""
    p = sub.add_parser("report", help="不带参数：马上向账号后台报一次这台 Agent J 主机的状态 · 带动作：模板化发布 templates · schema · example · validate · "
                                      "render [--email] · send · new-template · publish / alone: report this computer's status to the account "
                                      "dashboard now · with an action: template publishing (data JSON → schema check → web page or mail)",
                       description="自动化里发报告/邮件：模型只填 JSON，按模板严格校验（不过关报字段路径、退出码 2、不发送），再渲染成网页或邮件安全版。"
                                   "不写 --template 就用默认模板 R15 Agent J Noir；引擎是广场包 web-page-report。 / Reports and mails from "
                                   "automations: the model fills JSON, the template's schema checks it (field paths, exit 2, nothing sent), "
                                   "then a web page or a mail-safe e-mail. Default template: R15 Agent J Noir.")
    p.add_argument("action", nargs="?", choices=["templates", "schema", "example", "recommend", "validate", "render", "new-template",
                                                  "publish", "send"], help="不写 = 向账号后台上报状态 / none = the dashboard status report")
    p.add_argument("data", nargs="?", help="数据 JSON（schema / example：模板；new-template：目录）/ the data JSON (schema / example: "
                                           "a template; new-template: a folder)")
    p.add_argument("--data", dest="data_opt", help="同上，写成选项 / the data JSON as an option")
    p.add_argument("--template", "-t", help="模板：R01–R15、短名，或自定义模板的目录 / id（默认 R15）/ template id or folder (default R15)")
    p.add_argument("--email", action="store_true", help="render：邮件安全版（R15 / R02 / R14 与带 email.html 的自定义模板）/ mail-safe version")
    p.add_argument("--out", "-o", help="render：输出目录或 .html 文件；send --dry-run：把邮件存到这里 / output folder or .html file")
    p.add_argument("--to", action="append", help="send：收件人——邮箱或通讯录名字（可多次；规则同 agentj mail send）/ recipient")
    p.add_argument("--to-owner", action="store_true", help="send：发给主人本人 / to the owner's own addresses")
    p.add_argument("--cc", action="append", help="send：抄送 / Cc")
    p.add_argument("--bcc", action="append", help="send：密送 / Bcc")
    p.add_argument("--subject", help="send：标题（默认 = 数据里的 title）/ subject (default: the data's title)")
    p.add_argument("--attach", action="append", help="send：附件 / attachment")
    p.add_argument("--id", help="new-template：模板 id（默认用目录名）/ the template id")
    p.add_argument("--expires", help="publish：有效期 / expiry (7d, 30d, never …)")
    p.add_argument("--access", help="publish：访问方式 / access (public, code, email-domain:…, email-any)")
    p.add_argument("--dry-run", action="store_true", help="send：只校验和渲染，不发 · publish：不上传 / check and render only")
    p.add_argument("--en", action="store_true", help="English messages")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: status_fn(a) if a.action is None and status_fn else sys.exit(cmd(a)))
