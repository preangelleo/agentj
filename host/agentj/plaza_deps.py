"""#466: a missing dependency never blocks a plaza install; see ARCHITECTURE ADR-#466."""
from __future__ import annotations

import glob
import hashlib
import os
import re
import shutil
import sys

PENDING = ("已安装·依赖待补", "Installed · dependencies pending")
PASSED = ("自检通过", "self-check passed")
ACCEPT = ("功能验收：主人第一次真正用过后再确认", "acceptance: confirmed after the owner's first real use")
_PROG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_TAIL = re.compile(r"(?:^|[\s'\"`])([A-Za-z0-9][A-Za-z0-9._+-]{0,63})['\"`]?: (?:No such file or directory|not found|command not found)",
                   re.M)
_USER_DIRS = ("/opt/homebrew/bin", os.path.join(os.sep, "home", "linuxbrew", ".linuxbrew", "bin"), "~/.local/share/mise/shims", "~/.local/bin",
              "~/.volta/bin", "~/.asdf/shims", "~/.bun/bin", "~/.deno/bin", "~/.cargo/bin")
_USER_GLOBS = ("~/.nvm/versions/node/*/bin", "~/.local/share/mise/installs/*/*/bin",
               "~/.local/share/fnm/node-versions/*/installation/bin")


def _short(p: str) -> str:
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if home not in ("", "/") and (p == home or p.startswith(home + "/")) else p


def user_copies(prog: str) -> list[str]:
    dirs = [os.path.expanduser(d) for d in _USER_DIRS]
    for g in _USER_GLOBS:
        dirs += sorted(glob.glob(os.path.expanduser(g)))[-3:]
    found = []
    for d in dirs:
        p = os.path.join(d, prog)
        if os.path.isfile(p) and os.access(p, os.X_OK) and _short(d) not in found:
            found.append(_short(d))
    return found[:4]


# #478: a declared browser is there when any of its usual names or macOS app bundles is (Google Chrome counts for chromium)
_CHROME = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")
ALIASES = {n: _CHROME for n in _CHROME}
APPS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium")


def found(n: str, path: str) -> bool:
    if n not in ALIASES:
        return bool(shutil.which(n, path=path))
    return any(shutil.which(a, path=path) for a in ALIASES[n]) or any(os.access(a, os.X_OK) for a in APPS)


def missing(names, path: str) -> list[dict]:
    return [{"name": n, "user": user_copies(n)} for n in dict.fromkeys(n for n in names if _PROG.fullmatch(n or ""))
            if not found(n, path)]


def wanted(s: dict, cmds) -> list[str]:
    return [c["name"] for c in s.get("cli") or () if not c.get("optional")] + [a[0] for a in cmds or () if "/" not in a[0]]


def from_tail(tail: str) -> list[str]:
    return [n for n in dict.fromkeys(_TAIL.findall(tail or "")) if n not in ("env", "sh", "bash", "zsh", "1")][:6]


def _howto(en: bool) -> str:   # round 4 N4-5: never a link to a user path / shim (the sandbox can write those)
    pm = "the official installer package, or Homebrew" if sys.platform == "darwin" else "e.g. `sudo apt install <package>`"
    pm_zh = "官方安装包或 Homebrew" if sys.platform == "darwin" else "例如 `sudo apt install <包名>`"
    return (f"install it with the system package manager ({pm}). Only if there is no package: copy the real binary — "
            "`sudo install -o root -m 0755 <real binary> /usr/local/bin/<name>` — never `ln -s` to a file in your home, and "
            "never a mise / nvm / asdf / volta shim or a script (those point back into your home); install such tools with the "
            "package manager instead" if en else
            f"用系统包管理器装（{pm_zh}）。确实没有安装包时才复制真正的程序文件："
            "`sudo install -o root -m 0755 <程序真实路径> /usr/local/bin/<名字>`——不要用 `ln -s` 链到用户目录里的文件，"
            "也不要这样装 mise / nvm / asdf / volta 的 shim 或脚本（它们会跳回用户目录），这类工具请用包管理器装")


def label(n: str, declared, en: bool) -> str:   # N4-6: only a name the manifest declares under `cli` is shown bare
    n = n if _PROG.fullmatch(n or "") else "?"
    return n if n in (declared or ()) else n + (" (reported by the package)" if en else "（包自检报告的）")


def task_text(name: str, version: str, deps: list[dict], en: bool, path: str, declared=()) -> str:
    lines = [("[Agent J system task] Plaza package {n} {v} is installed, but its self-check is missing dependencies. "
              "Status: {s}.") if en else "〔Agent J 系统任务〕广场包 {n} {v} 已装好，但自检缺依赖。状态：{s}。"]
    lines[0] = lines[0].format(n=name, v=version, s=PENDING[1 if en else 0])
    for d in deps or [{"name": "?", "user": []}]:
        rc = re.fullmatch(r"\? \((exit \d{1,3})\)", d["name"] or "")
        d = {**d, "name": label(d["name"], declared, en)}
        if rc:
            lines.append(f"- the self-test failed ({rc.group(1)}) without naming a missing program: run `agentj plaza verify {name}` "
                         "to see its output." if en else f"- 自检没通过（{rc.group(1)}），没说缺什么：先运行 `agentj plaza verify {name}` 看输出。")
        elif any(p.startswith(("/opt/homebrew/", "/home/linuxbrew/")) for p in d["user"]):
            from .market import brew_problem
            reason = "; ".join(filter(None, (brew_problem(p) for p in d["user"] if p.startswith(("/opt/homebrew/", "/home/linuxbrew/"))))) or "self-check PATH differs / 自检 PATH 不同"
            lines.append(f"- {d['name']}: a Homebrew copy exists ({', '.join(d['user'])}), but it is not on the self-check PATH: {reason}. Inspect directory permissions with the owner; do not change them automatically. The official installer in /usr/local/bin is another option." if en else
                         f"- {d['name']}：Homebrew 目录里有（{'、'.join(d['user'])}），但未进入自检 PATH：{reason}。这不是用户目录不可见；先和主人检查目录权限，不自动改权限，也可使用装到 /usr/local/bin 的官方安装包。")
        elif d["user"]:
            lines.append(f"- {d['name']}: not on the self-check PATH ({path}); a user-level copy exists in {', '.join(d['user'])}, "
                         "but the sandbox never sees user directories." if en else
                         f"- {d['name']}：自检的系统 PATH（{path}）里没有；你的用户目录里有（{'、'.join(d['user'])}），但自检沙箱看不到用户目录。")
        else:
            lines.append(f"- {d['name']}: not found on this computer." if en else f"- {d['name']}：本机没找到。")
    lines.append(("Fix it with your normal permissions (sudo goes through the sudo card): " + _howto(True)
                  + f". Then run `agentj plaza verify {name}` — the same package and digest, no new signature. When it passes, "
                  "Agent J updates the install message on the owner's phone itself: say nothing more. Only if you give up, tell "
                  "the owner in one sentence what is still missing.") if en else
                 ("用你的常规权限补齐（要 sudo 的走 sudo 确认卡）：" + _howto(False)
                  + f"。然后运行 `agentj plaza verify {name}` 重跑自检（同一个包、同一个 digest，不用再签名）。通过后 Agent J 会自己更新"
                  "主人手机上那条安装消息，你不用再说；只有补不齐、放弃时，才用一句话告诉主人还缺什么。"))
    return "\n".join(lines)


def tree_sha(rendered: dict) -> str:
    h = hashlib.sha256()
    for p in sorted(rendered, key=lambda x: x.encode()):
        b = rendered[p] if isinstance(rendered[p], bytes) else str(rendered[p]).encode()
        h.update(p.encode() + b"\0" + hashlib.sha256(b).digest())
    return h.hexdigest()


def read_tree(target, paths) -> dict | None:
    out = {}
    try:
        root = os.open(str(target), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        for rel in paths:
            parts, fd = rel.split("/"), os.dup(root)
            try:
                for part in parts[:-1]:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = nxt
                f = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(f, "rb") as fh:
                    out[rel] = fh.read(64 << 20)
            except OSError:
                return None
            finally:
                os.close(fd)
    finally:
        os.close(root)
    return out
