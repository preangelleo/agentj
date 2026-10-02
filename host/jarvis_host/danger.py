"""The danger list (PROMPT-26 item 2; ADR-A47 – A49): five fixed categories of actions that always need the phone, one by one.

    spend        花钱     money leaves: payments, purchases, trades, transfers, billable cloud resources
    delete       删除     something is destroyed: files, branches, history, cloud resources, rows, packages on a registry
    send         对外发送  something leaves the machine for other people: mail, messages, posts, pushes, uploads, deploys
    credentials  改凭据   a credential changes: .env / keys / ~/.ssh / ~/.aws / keychains, `gh auth`, `aws configure`, passwd …
    price        改价     a price, discount or budget changes: Stripe prices / coupons, shop listings, ad budgets

`classify(tool, tool_input)` is deterministic (same input → same verdict) and errs towards "dangerous": a command is read
as a shell would split it (`;` `&&` `||` `|` newlines, `$( )` / backticks / `<( )`, `sh -c` / `bash -c` / `eval` / `su -c` /
`ssh host …`, `xargs`, `find -exec`, `sudo` / `env` / `timeout` … wrappers, inline `python -c` / `node -e` code), and every
line is also read on its own (heredoc bodies). What a script or program does when it runs is NOT judged — only its name
(ARCHITECTURE G-A57).

The rules are code. `config.json` may only ADD (`danger_extra`: {"category", "bash" | "tool" | "path": regex}); every other
`danger*` key, and anything in `danger_extra` that does not parse, is ignored and reported by `jarvis doctor`.

Used twice, with the same verdict:
- as Claude Code's PreToolUse hook (`python -P -m jarvis_host.danger hook <extras>`, injected by serve with `--settings`):
  a dangerous call answers `permissionDecision: "ask"`, which Claude Code routes to our permission prompt tool (the phone)
  even when the human's own settings allow the command or run in bypassPermissions (measured, reports/qa/danger/); anything
  else prints nothing, so the human's own rules decide (only ever stricter, Invariant 11);
- by serve, to label the card and to keep dangerous calls out of batch approval.

Standard library only (the hook starts once per tool call).
"""
from __future__ import annotations

import base64
import json
import os
import posixpath
import re
import sys
from dataclasses import dataclass, field

CATEGORIES = ("spend", "delete", "send", "credentials", "price")
LABEL = {"spend": "花钱 · Spend", "delete": "删除 · Delete", "send": "对外发送 · Send", "credentials": "改凭据 · Credentials",
         "price": "改价 · Price"}
LOW_LABEL = "低风险 · Low risk"
MAX_EXTRA = 64
MAX_DEPTH = 6
MAX_CMD = 64 * 1024          # longer commands are judged on the first 64 KB plus "too long" → dangerous


@dataclass
class Verdict:
    cats: list = field(default_factory=list)    # sorted categories, [] = low risk
    rules: list = field(default_factory=list)   # rule ids that fired, in order
    why: str = ""                               # one sentence for the phone (Chinese)

    @property
    def danger(self) -> bool:
        return bool(self.cats)


class _Hits:
    def __init__(self):
        self.items: list[tuple[str, str, str]] = []   # (category, rule id, why)

    def add(self, cat: str, rule: str, why: str) -> None:
        if not any(r == rule for _, r, _ in self.items):
            self.items.append((cat, rule, why))

    def verdict(self) -> Verdict:
        cats = sorted({c for c, _, _ in self.items}, key=CATEGORIES.index)
        rules = [r for _, r, _ in self.items]
        why = "；".join(dict.fromkeys(w for _, _, w in self.items))
        return Verdict(cats, rules, why[:200])


# ================================================================ shell lexing
@dataclass
class Cmd:
    words: list
    redirs: list = field(default_factory=list)   # redirection targets (> >> &> < …), heredoc delimiters excluded
    heredoc: bool = False                        # fed by << / <<<
    piped: bool = False                          # right side of a pipe
    subst: bool = False                          # came from $( ) / backticks / <( ) (or contains one)


def _match_paren(s: str, i: int) -> int:
    """Index just past the ')' closing the '(' at s[i-1]; quotes respected roughly. len(s) when unbalanced."""
    depth, q = 1, None
    while i < len(s):
        c = s[i]
        if q:
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == "\\":
            i += 2
            continue
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(s)


def lex(s: str, depth: int = 0) -> list[Cmd]:
    """Split a shell command line into simple commands. Substitutions are lexed recursively and returned as their own
    commands (marked subst); the word that held one keeps a placeholder. Never raises: unbalanced quotes run to the end."""
    out: list[Cmd] = []
    if depth > MAX_DEPTH:
        return out
    cur = Cmd([])
    word: list[str] | None = None
    redir_next = heredoc_next = False
    pipe_next = False
    i, n = 0, len(s)

    def end_word():
        nonlocal word, redir_next, heredoc_next
        if word is None:
            return
        w = "".join(word)
        word = None
        if heredoc_next:
            heredoc_next = False                     # the delimiter: not a word, not a target
        elif redir_next:
            cur.redirs.append(w)
            redir_next = False
        else:
            cur.words.append(w)

    def end_cmd(op=""):
        nonlocal cur, pipe_next
        end_word()
        if cur.words or cur.redirs:
            out.append(cur)
        cur = Cmd([], piped=pipe_next)
        pipe_next = op in ("|", "|&")
        cur.piped = pipe_next

    def sub(inner: str):
        for c in lex(inner, depth + 1):
            c.subst = True
            out.append(c)
        cur.subst = True

    while i < n:
        c = s[i]
        if c == "\\":
            if i + 1 < n and s[i + 1] == "\n":
                i += 2
                continue
            (word := word if word is not None else []).append(s[i + 1] if i + 1 < n else "")
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            j = n if j < 0 else j
            (word := word if word is not None else []).append(s[i + 1:j])
            i = j + 1
            continue
        if c == "$" and s.startswith("$'", i):
            j = s.find("'", i + 2)
            j = n if j < 0 else j
            (word := word if word is not None else []).append(s[i + 2:j])
            i = j + 1
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    buf.append(s[j + 1])
                    j += 2
                    continue
                if s.startswith("$(", j):
                    k = _match_paren(s, j + 2)
                    sub(s[j + 2:k - 1])
                    buf.append("$(…)")
                    j = k
                    continue
                if s[j] == "`":
                    k = s.find("`", j + 1)
                    k = n if k < 0 else k
                    sub(s[j + 1:k])
                    buf.append("`…`")
                    j = k + 1
                    continue
                buf.append(s[j])
                j += 1
            (word := word if word is not None else []).append("".join(buf))
            i = j + 1
            continue
        if c == "`":
            k = s.find("`", i + 1)
            k = n if k < 0 else k
            sub(s[i + 1:k])
            (word := word if word is not None else []).append("`…`")
            i = k + 1
            continue
        if c == "$" and s.startswith("$(", i):
            k = _match_paren(s, i + 2)
            sub(s[i + 2:k - 1])
            (word := word if word is not None else []).append("$(…)")
            i = k
            continue
        if c in "<>" and s.startswith("(", i + 1):          # process substitution <( ) >( )
            k = _match_paren(s, i + 2)
            sub(s[i + 2:k - 1])
            (word := word if word is not None else []).append("<(…)")
            i = k
            continue
        if c == "#" and word is None:
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in " \t\r":
            end_word()
            i += 1
            continue
        if c in "<>":
            # an fd number right before (2>, 1>>) belongs to the operator, not to a word
            if word is not None and "".join(word).isdigit():
                word = None
            elif word is not None and "".join(word) == "&":
                word = None
            end_word()
            if s.startswith("<<<", i):
                cur.heredoc = True
                i += 3
                continue                               # a here-string: the next word is data, keep it as a word
            if s.startswith("<<", i):
                cur.heredoc, heredoc_next = True, True
                i += 2
                if i < n and s[i] == "-":
                    i += 1
                continue
            j = i + 1
            while j < n and s[j] in ">|&":
                j += 1
            if s.startswith(">&", i) or s.startswith("<&", i):
                k = j
                while k < n and (s[k].isdigit() or s[k] == "-"):
                    k += 1
                if k > j or (k < n and s[k] in " \t\n;|&)"):   # 2>&1, >&-: an fd, not a file
                    i = k
                    continue
            redir_next = True
            i = j
            continue
        if c == "&" and s.startswith("&>", i):
            end_word()
            redir_next = True
            i += 3 if s.startswith("&>>", i) else 2
            continue
        two = s[i:i + 2]
        if two in ("&&", "||", "|&", ";;"):
            end_cmd(two)
            i += 2
            continue
        if c in ";&|\n(){}" and (c not in "{}" or (word is None and (i + 1 >= n or s[i + 1] in " \t\n;"))):
            end_cmd(c)
            i += 1
            continue
        (word := word if word is not None else []).append(c)
        i += 1
    end_cmd()
    return out


# ================================================================ command unwrapping
_WRAP_ARG = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "--user", "--group", "--chdir"},
    "doas": {"-u", "-C"}, "run0": {"-u", "--user", "-D"}, "pkexec": {"--user"},
    "nice": {"-n", "--adjustment"}, "ionice": {"-c", "-n", "-p", "-t"}, "stdbuf": {"-i", "-o", "-e"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"}, "gtimeout": {"-s", "-k", "--signal", "--kill-after"},
    "watch": {"-n", "--interval", "-d"}, "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "flock": {"-w", "--timeout", "-E", "--conflict-exit-code"}, "setsid": set(), "nohup": set(), "time": {"-f", "-o"},
    "chronic": set(), "caffeinate": {"-t", "-w"}, "command": set(), "builtin": set(), "exec": {"-a"}, "unbuffer": set(),
    "strace": {"-o", "-e", "-p", "-s", "-E"}, "ltrace": {"-o", "-e", "-p", "-s"}, "torsocks": set(), "proxychains": {"-f"},
    "proxychains4": {"-f"}, "firejail": set(), "noglob": set(), "nocorrect": set(), "catchsegv": set(), "valgrind": set(),
    "systemd-run": {"-p", "--property", "-u", "--unit", "-E", "--setenv", "--uid", "--gid", "--slice", "-M", "--machine",
                    "--description", "--working-directory"},
    "xargs": {"-I", "-i", "-n", "-P", "-L", "-l", "-d", "-E", "-e", "-s", "-a", "--arg-file", "--delimiter", "--max-args",
              "--max-procs", "--replace"},
    "parallel": {"-j", "-n", "-I", "-S", "--jobs", "-a", "--arg-file"},
    "npx": {"-p", "--package", "-c", "--call"}, "pnpx": {"-p", "--package"}, "bunx": {"-p", "--package"},
    "uvx": {"--from", "--with", "-p", "--python", "--with-requirements"}, "dotenv": {"-e", "-c", "-v"},
    "busybox": set(), "then": set(), "do": set(), "else": set(), "elif": set(), "if": set(), "while": set(),
    "until": set(), "!": set(), "time!": set(),
}
_RUNNERS = {("pnpm", "dlx"), ("pnpm", "exec"), ("yarn", "dlx"), ("yarn", "exec"), ("npm", "exec"), ("pipx", "run"),
            ("uv", "run"), ("poetry", "run"), ("pdm", "run"), ("hatch", "run"), ("rye", "run"), ("bundle", "exec"),
            ("pipenv", "run"), ("conda", "run"), ("mise", "exec"), ("mise", "x"), ("asdf", "exec"), ("direnv", "exec"),
            ("bun", "x"), ("deno", "run")}
_WRAP_POS = {"timeout": 1, "gtimeout": 1, "flock": 1}   # fixed positional arguments before the command
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish", "ash", "yash", "tcsh", "csh"}
_INTERP = {"python", "python2", "python3", "pypy", "pypy3", "node", "nodejs", "deno", "bun", "perl", "ruby", "php", "lua",
           "osascript", "pwsh", "powershell", "Rscript", "julia", "tclsh", "awk", "gawk"}
_INLINE_FLAGS = {"-c", "-e", "-E", "--eval", "-p", "--print", "-Command", "-command", "-r", "eval"}
_SSH_ARG = {"-p", "-i", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-b", "-c", "-E", "-e", "-m", "-O", "-Q", "-S", "-W", "-w",
            "-B", "-I"}


def _base(w: str) -> str:
    b = w.lstrip("\\").rsplit("/", 1)[-1]
    for suf in (".exe", ".cmd", ".bat"):
        if b.lower().endswith(suf):
            b = b[:-len(suf)]
    m = re.fullmatch(r"(python|pypy|perl|ruby|php|node)([0-9][0-9.]*)", b)
    if m:
        return "python3" if m.group(1) == "python" else m.group(1)
    return b


_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass
class Simple:
    name: str
    args: list
    raw: list            # the words from the name on (as typed)
    redirs: list
    flags: set           # "shell_c", "eval", "inline", "heredoc", "piped_shell", "subst", "wrapped", "remote"


def unwrap(cmd: Cmd, depth: int = 0) -> list[Simple]:
    """The real command(s) behind one simple command: assignments and wrappers stripped; `sh -c S` / `eval` / `su -c S` /
    `ssh host CMD` / `xargs CMD` / `find -exec CMD ;` expanded (recursively)."""
    words = list(cmd.words)
    flags = set()
    if cmd.heredoc:
        flags.add("heredoc")
    if cmd.subst:
        flags.add("subst")
    out: list[Simple] = []
    while words and _ASSIGN.match(words[0]):
        words.pop(0)
    guard = 0
    while words and guard < 16:
        guard += 1
        name = _base(words[0])
        if len(words) > 1 and (name, words[1]) in _RUNNERS:   # pnpm dlx / uv run / poetry run / bundle exec …
            flags.add("wrapped")
            rest, k = words[2:], 0
            while k < len(rest) and rest[k].startswith("-"):
                k += 2 if rest[k] in ("--with", "--from", "-p", "--python", "--package", "--env-file") else 1
            words = rest[k:]
            continue
        if name in _WRAP_ARG:
            flags.add("wrapped")
            opts, rest = _WRAP_ARG[name], words[1:]
            k = 0
            while k < len(rest):
                w = rest[k]
                if name == "env" and _ASSIGN.match(w):
                    k += 1
                elif w == "--":
                    k += 1
                    break
                elif w.startswith("-") and len(w) > 1:
                    k += 2 if (w in opts and "=" not in w) else 1
                else:
                    break
            k += _WRAP_POS.get(name, 0)
            if name in ("xargs", "parallel") and k >= len(rest):
                rest, k = ["echo"], 0            # xargs alone runs echo
            words = rest[k:]
            continue
        if name == "eval":
            flags.add("eval")
            inner = " ".join(words[1:])
            for c in lex(inner, depth + 1):
                for s in unwrap(c, depth + 1):
                    s.flags |= flags | {"eval"}
                    out.append(s)
            return out
        if name in _SHELLS and len(words) > 1:
            rest = words[1:]
            k = 0
            has_c = False
            while k < len(rest) and rest[k].startswith("-") and rest[k] not in ("-", "--"):
                if rest[k] in ("-o", "+o", "-O", "+O"):
                    k += 2
                    continue
                if "c" in rest[k][1:] and not rest[k].startswith("--"):
                    has_c = True
                k += 1
            if has_c and k < len(rest):
                for c in lex(rest[k], depth + 1):
                    for s in unwrap(c, depth + 1):
                        s.flags |= flags | {"shell_c"}
                        out.append(s)
                return out
            if cmd.piped or cmd.heredoc or k >= len(rest) or rest[k] in ("-", "-s"):
                flags.add("piped_shell")           # a shell reading its program from stdin: content unknown here
        if name == "su":
            rest = words[1:]
            for k, w in enumerate(rest):
                if w in ("-c", "--command") and k + 1 < len(rest):
                    for c in lex(rest[k + 1], depth + 1):
                        for s in unwrap(c, depth + 1):
                            s.flags |= flags | {"shell_c"}
                            out.append(s)
                    return out
        if name in ("ssh", "mosh"):
            rest, k = words[1:], 0
            while k < len(rest) and rest[k].startswith("-"):
                k += 2 if rest[k] in _SSH_ARG else 1
            remote = rest[k + 1:]
            if remote:
                out.append(Simple(name, words[1:], words, list(cmd.redirs), flags | {"remote"}))
                for c in lex(" ".join(remote), depth + 1):
                    for s in unwrap(c, depth + 1):
                        s.flags |= flags | {"remote"}
                        out.append(s)
                return out
        if name == "find":
            args = words[1:]
            k = 0
            while k < len(args):
                if args[k] in ("-exec", "-execdir", "-ok", "-okdir"):
                    j = k + 1
                    while j < len(args) and args[j] not in (";", "+", "\\;"):
                        j += 1
                    if j > k + 1:
                        for s in unwrap(Cmd(args[k + 1:j]), depth + 1):
                            s.flags |= flags | {"wrapped"}
                            out.append(s)
                    k = j + 1
                    continue
                k += 1
        out.append(Simple(name, words[1:], words, list(cmd.redirs), flags))
        return out
    return out


# ================================================================ rules
_CRED_DIRS = re.compile(
    r"(^|/)(\.ssh|\.aws|\.gnupg|\.azure|\.password-store|\.kube|\.config/gh|\.config/gcloud|\.config/op|\.config/rclone|"
    r"\.terraform\.d|\.cargo|\.gem|\.docker|Library/Keychains|\.local/share/keyrings|\.wrangler/config|\.config/hub|"
    r"\.config/doctl|\.config/hcloud|\.fly|\.netlify|\.vercel|\.stripe|\.config/stripe|\.config/heroku)(/|$)")
_CRED_FILES = re.compile(
    r"^(\.env(\.(?!example$|sample$|template$|dist$)[^/]*)?|\.envrc|\.netrc|_netrc|\.npmrc|\.yarnrc(\.yml)?|\.pypirc|"
    r"\.git-credentials|\.htpasswd|\.pgpass|\.my\.cnf|\.s3cfg|\.boto|\.vault-token|\.dockercfg|credentials(\.json|\.toml|\.ya?ml)?|"
    r"client_secrets?[^/]*\.json|service[-_]?account[^/]*\.json|secrets?\.(json|ya?ml|toml|env|txt)|auth\.json|token\.json|"
    r"id_(rsa|dsa|ecdsa|ed25519)(_sk)?(\.pub)?|authorized_keys2?|known_hosts|shadow|gshadow|passwd|sudoers|master\.key|"
    r"\.credentials\.json|keystore\.jks)$", re.I)
_CRED_EXT = re.compile(r"\.(pem|key|p12|pfx|jks|keystore|ppk|kdbx|keychain(-db)?|gpg|asc|crt|cer|der|csr|ovpn|mobileprovision)$",
                       re.I)
_CRED_ETC = re.compile(r"^/etc/(shadow|gshadow|passwd|group|sudoers(\.d/.*)?|ssh/.*|security/.*|pam\.d/.*)$")


def cred_path(p: str) -> str | None:
    """A why-string when the path is a credential store (credential category), else None."""
    if not isinstance(p, str) or not p.strip():
        return None
    q = p.strip().strip("'\"")
    if q.startswith("~"):
        q = "/~" + q[1:]                  # a home-relative path: only its tail decides (credential dirs / names below)
    q = posixpath.normpath(q)
    base = q.rsplit("/", 1)[-1]
    if _CRED_ETC.match(q):
        return f"系统账号 / 认证文件 {base}"
    if _CRED_FILES.match(base):
        return f"凭据文件 {base}"
    if _CRED_EXT.search(base):
        return f"密钥 / 证书文件 {base}"
    if _CRED_DIRS.search(q):
        return f"凭据目录里的文件 {base}"
    return None


_WRITERS = {"tee", "cp", "mv", "install", "ln", "rsync", "dd", "sed", "perl", "truncate", "chmod", "chown", "touch", "rm",
            "ed", "ex", "vi", "vim", "nvim", "nano", "emacs", "sponge", "patch", "shred", "unlink", "cat", "echo", "printf",
            "openssl", "ssh-keygen", "scp", "gpg", "base64", "chattr", "setfacl", "git"}

_DELETE_WORDS = re.compile(r"^(delete|del|remove|rm|rmi|rmdir|destroy|purge|prune|drop|terminate|uninstall|unpublish|wipe|erase|"
                           r"rb|truncate|deletefile|cleanup|clear|flush|flushall|flushdb|unset)$|^(delete|remove|terminate|destroy|"
                           r"deregister|purge|drop|release-address|disassociate|detach)-", re.I)
_RESOURCE_CLIS = {"aws", "gcloud", "gsutil", "az", "kubectl", "oc", "helm", "gh", "glab", "tea", "doctl", "hcloud", "heroku",
                  "vercel", "netlify", "wrangler", "firebase", "fly", "flyctl", "stripe", "shopify", "docker", "podman",
                  "nerdctl", "terraform", "tofu", "pulumi", "rclone", "b2", "s3cmd", "mc", "oci", "linode-cli", "vultr-cli",
                  "supabase", "railway", "render", "bw", "op", "vault", "doppler", "redis-cli", "eksctl", "ibmcloud",
                  "aliyun", "tccli", "scw", "exo", "upcloud", "civo", "ollama", "pm2", "systemctl", "launchctl", "crontab",
                  "npm", "pnpm", "yarn", "bun", "cargo", "gem", "twine", "pip", "uv", "conda", "brew", "apt", "apt-get",
                  "dnf", "yum", "pacman", "snap", "flatpak", "lxc", "incus", "virsh", "multipass", "vagrant", "minikube",
                  "kind", "k3d", "nomad", "consul", "etcdctl", "mongosh", "mongo", "influx", "gcutil"}
_PKG_LOCAL = {"pip", "uv", "conda", "brew", "apt", "apt-get", "dnf", "yum", "pacman", "snap", "flatpak", "npm", "pnpm", "yarn",
              "bun", "cargo", "gem", "systemctl", "launchctl", "pm2", "ollama"}   # local package / service removal: not "delete"

_SEND_URL = re.compile(
    r"(api\.telegram\.org/bot|hooks\.slack\.com|slack\.com/api/(chat\.|files\.|conversations\.)|discord(app)?\.com/api/webhooks|"
    r"api\.mailgun\.net|api\.eu\.mailgun\.net|api\.sendgrid\.com|api\.resend\.com|api\.postmarkapp\.com|api\.brevo\.com|"
    r"api\.sparkpost\.com|graph\.facebook\.com|graph\.instagram\.com|api\.twitter\.com|api\.x\.com|upload\.twitter\.com|"
    r"api\.linkedin\.com|api\.twilio\.com|outlook\.office\.com/webhook|webhook\.office\.com|api\.pushover\.net|ntfy\.sh/|"
    r"gmail\.googleapis\.com/.*/send|api\.line\.me|qyapi\.weixin\.qq\.com|oapi\.dingtalk\.com|open\.feishu\.cn/open-apis/bot|"
    r"api\.bsky\.app|bsky\.social/xrpc/com\.atproto\.repo\.createRecord|mastodon[^ ]*/api/v1/statuses|api\.medium\.com|"
    r"api\.github\.com/repos/[^ ]*/(issues|pulls|comments|releases|dispatches)|api\.notion\.com/v1/pages)", re.I)
_SPEND_URL = re.compile(
    r"(api\.stripe\.com/v1/(charges|payment_intents|payouts|transfers|refunds|invoices/[^/ ]+/pay|subscriptions|"
    r"checkout/sessions|issuing|topups|payment_links|setup_intents)|api(-m)?\.paypal\.com/v\d/(payments|checkout|billing)|"
    r"api\.binance\.[a-z]+/(api|sapi)/v\d/(order|margin/order|withdraw|convert|asset/transfer|capital/withdraw)|"
    r"fapi\.binance\.[a-z]+/fapi/v\d/order|dapi\.binance\.[a-z]+/dapi/v\d/order|api\.coinbase\.com/(v2/accounts/[^ ]*/"
    r"(transactions|buys|sells)|api/v3/brokerage/orders)|api\.kraken\.com/0/private/(AddOrder|Withdraw)|"
    r"api\.(name|namecheap|porkbun|godaddy|dynadot)\.[a-z]+/[^ ]*(purchase|register|renew|create)|domains:purchase|"
    r"/v\d+/domains/[^ ]*/(register|renew)|api\.openai\.com/v1/organization/[^ ]*billing)", re.I)
_PRICE_URL = re.compile(
    r"(api\.stripe\.com/v1/(prices|products|coupons|promotion_codes|plans|shipping_rates|tax_rates|billing/meters)|"
    r"/admin/api/[^ ]*/(products|variants|price_rules|discount_codes|inventory_levels|marketing_events)|"
    r"/listings/20\d\d-\d\d-\d\d/items|/products/pricing/|/sell/(inventory|marketing|account)/|/wp-json/wc/v\d/(products|coupons)|"
    r"openapi\.etsy\.com/v\d/application/shops/[^ ]*/listings|googleads\.googleapis\.com/[^ ]*(campaignBudgets|campaigns|"
    r"adGroups|adGroupAds):mutate|graph\.facebook\.com/[^ ]*(adsets|campaigns|ads)\b|/v\d+/billing/budgets|"
    r"api\.lemonsqueezy\.com/v\d/(variants|discounts|products)|api\.paddle\.com/(prices|products|discounts))", re.I)
_PRICE_WORD = re.compile(r"(price|pricing|budget|coupon|discount|promotion|promo_code|daily_budget|lifetime_budget|bid_amount|"
                         r"compare_at_price|sale_price|regular_price)", re.I)
_SQL_DELETE = re.compile(r"\b(drop\s+(table|database|schema|index|view|user|role|collection)|truncate\s+(table\s+)?\w|"
                         r"delete\s+from\b|alter\s+table\s+\S+\s+drop\b|flushall\b|flushdb\b|dropDatabase\s*\(|"
                         r"\.drop\s*\(\s*\)|deleteMany\s*\(|deleteOne\s*\(|remove\s*\(\s*\{\s*\}\s*\))", re.I)
_SQL_CRED = re.compile(r"\b(alter|create)\s+(user|role)\b[^;]*\b(password|identified)\b|\bset\s+password\b|\bgrant\s+",
                       re.I)
_SQL_PRICE = re.compile(r"\bupdate\s+\S*(price|product|plan|coupon|discount|budget)\S*\s+set\b|\bset\s+\S*(price|budget)\S*\s*=",
                        re.I)
# code text (python -c / node -e / perl -e / ruby -e / osascript): what libraries reveal
_CODE_DELETE = re.compile(r"(shutil\.rmtree|os\.(remove|unlink|rmdir|removedirs)\b|Path\([^)]*\)\.(unlink|rmdir)|\.unlink\(|"
                          r"rmSync|unlinkSync|rmdirSync|fs\.(rm|unlink|rmdir)\b|fs\.promises\.(rm|unlink)|rimraf|del\s*\(|"
                          r"FileUtils\.rm|File\.delete|Remove-Item|\bunlink\s*[(\"'$]|\brmtree\b|Files\.delete|deleteRecursively|trash\()",
                          re.I)
_CODE_SEND = re.compile(r"(smtplib|sendmail|send_mail|send_message\(|requests\.(post|put|patch|delete)|httpx\.(post|put|patch|"
                        r"delete)|aiohttp[^\n]*\.(post|put|patch)|urllib[^\n]*(data=|method=)|method\s*[:=]\s*['\"](POST|PUT|"
                        r"PATCH|DELETE)|axios\.(post|put|patch|delete)|\.post\(|nodemailer|sendgrid|mailgun|twilio|"
                        r"Net::SMTP|Mail\.deliver|tell application \"Messages\"[^\n]*send|tell application \"Mail\"|"
                        r"Invoke-(RestMethod|WebRequest)[^\n]*-Method\s+(Post|Put|Patch|Delete)|Send-MailMessage)", re.I)
_CODE_SHELL = re.compile(r"(os\.system|subprocess|os\.popen|child_process|execSync|spawnSync|exec\(|system\(|`[^`]+`|"
                         r"%x\{|IO\.popen|Open3|do shell script)", re.I)
_STR_LIT = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"|`([^`]*)`")


def _code(text: str, hits: _Hits, depth: int) -> None:
    if _CODE_DELETE.search(text):
        hits.add("delete", "delete.code", "代码里删除文件")
    if _CODE_SEND.search(text):
        hits.add("send", "send.code", "代码里对外发送请求 / 邮件 / 消息")
    _text_rules(text, hits)
    if _CODE_SHELL.search(text) and depth < MAX_DEPTH:
        for m in _STR_LIT.finditer(text):
            lit = next(g for g in m.groups() if g is not None)
            if lit.strip():
                _bash(lit, hits, depth + 1)


def _text_rules(text: str, hits: _Hits, write: bool = True) -> None:
    """Raw-text rules that apply wherever the text appears (a whole command, inline code, MCP input)."""
    if _SEND_URL.search(text):
        hits.add("send", "send.url", "调用了发消息 / 发帖 / 发邮件的接口")
    if _SPEND_URL.search(text):
        hits.add("spend", "spend.url", "调用了付款 / 下单 / 交易的接口")
    if write and _PRICE_URL.search(text):
        hits.add("price", "price.url", "调用了改价格 / 商品 / 预算的接口")
    if _SQL_DELETE.search(text):
        hits.add("delete", "delete.sql", "删除数据（DROP / DELETE / TRUNCATE）")
    if _SQL_CRED.search(text):
        hits.add("credentials", "cred.sql", "改数据库账号 / 密码 / 权限")
    if _SQL_PRICE.search(text):
        hits.add("price", "price.sql", "改数据库里的价格 / 预算")


def _positionals(args: list, opts_with_arg: set = frozenset()) -> list:
    out, k = [], 0
    while k < len(args):
        a = args[k]
        if a == "--":
            out += args[k + 1:]
            break
        if a.startswith("-") and len(a) > 1:
            k += 2 if (a in opts_with_arg and "=" not in a) else 1
            continue
        out.append(a)
        k += 1
    return out


def _sub(args: list, opts_with_arg: set = frozenset()) -> tuple[str, list]:
    p = _positionals(args, opts_with_arg)
    return (p[0] if p else ""), p[1:]


def _http_write(name: str, args: list) -> tuple[bool, str | None]:
    """(is a write request, method) for curl / wget / httpie / xh."""
    if name == "curl":
        method = None
        for k, a in enumerate(args):
            if a in ("-X", "--request") and k + 1 < len(args):
                method = args[k + 1].upper()
            elif a.startswith("-X") and len(a) > 2 and not a.startswith("--"):
                method = a[2:].upper()
            elif a.startswith("--request="):
                method = a.split("=", 1)[1].upper()
        data = any(a in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--data-ascii", "-F", "--form",
                         "--form-string", "-T", "--upload-file", "--json") or a.startswith(("--data", "--form", "--json",
                                                                                            "--upload-file"))
                   or (a.startswith(("-d", "-F", "-T")) and len(a) > 2 and not a.startswith("--"))
                   or re.fullmatch(r"-[a-zA-Z]*[dFT]", a) is not None for a in args)
        if method is None:
            if data and any(a in ("-G", "--get") for a in args):
                return False, "GET"
            return data, ("POST" if data else None)
        return method not in ("GET", "HEAD", "OPTIONS"), method
    if name == "wget":
        m = None
        for a in args:
            if a.startswith("--method="):
                m = a.split("=", 1)[1].upper()
        data = any(a.startswith(("--post-data", "--post-file", "--body-data", "--body-file")) for a in args)
        return (data or (m not in (None, "GET", "HEAD"))), (m or ("POST" if data else None))
    if name in ("http", "https", "xh", "xhs", "httpie"):
        p = _positionals(args)
        m = p[0].upper() if p and p[0].upper() in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS") else None
        items = any(re.match(r"^[^=:/]+(:=|==?|@|:=@)", x) and "=" in x and not x.startswith("http") for x in p[1:] if x)
        if m:
            return m not in ("GET", "HEAD", "OPTIONS"), m
        return items, ("POST" if items else None)
    return False, None


def _git(args: list, hits: _Hits) -> None:
    k = 0
    while k < len(args) and args[k].startswith("-"):
        k += 2 if args[k] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path") else 1
    rest = args[k:]
    if not rest:
        return
    sub, a = rest[0], rest[1:]
    if sub == "push":
        hits.add("send", "send.git_push", "git push：把提交推到远端")
        if any(x in ("-f", "--force", "--force-with-lease", "--mirror", "--prune", "-d", "--delete") or x.startswith(
                ("--force", "+")) or (x.startswith(":") and len(x) > 1) for x in a):
            hits.add("delete", "delete.git_push", "强推 / 删除远端分支会丢掉远端历史")
    elif sub in ("send-email", "request-pull", "imap-send"):
        hits.add("send", "send.git_mail", f"git {sub}：把补丁发出去")
    elif sub == "clean":
        hits.add("delete", "delete.git_clean", "git clean：删除未跟踪的文件")
    elif sub == "rm":
        hits.add("delete", "delete.git_rm", "git rm：删除文件")
    elif sub == "reset" and any(x in ("--hard", "--merge", "--keep") for x in a):
        hits.add("delete", "delete.git_reset", "git reset --hard：丢弃未提交的改动")
    elif sub == "branch" and any(x in ("-D", "-d", "--delete") or x.startswith("-D") for x in a):
        hits.add("delete", "delete.git_branch", "删除分支")
    elif sub == "tag" and any(x in ("-d", "--delete") for x in a):
        hits.add("delete", "delete.git_tag", "删除标签")
    elif sub == "stash" and a[:1] in (["drop"], ["clear"]):
        hits.add("delete", "delete.git_stash", "丢弃 stash")
    elif sub in ("checkout", "switch") and any(x in ("-f", "--force", "--discard-changes", "-B") for x in a):
        hits.add("delete", "delete.git_checkout", "强制切换会丢弃未提交的改动")
    elif sub == "checkout" and "--" in a:
        hits.add("delete", "delete.git_checkout", "git checkout -- 会丢弃文件的未提交改动")
    elif sub == "restore" and not (set(a) & {"--staged", "-S"} and not set(a) & {"--worktree", "-W"}):
        hits.add("delete", "delete.git_restore", "git restore 会丢弃未提交的改动")
    elif sub in ("filter-branch", "filter-repo", "replace"):
        hits.add("delete", "delete.git_rewrite", "改写历史")
    elif sub == "worktree" and a[:1] in (["remove"], ["prune"]):
        hits.add("delete", "delete.git_worktree", "删除 worktree")
    elif sub == "update-ref" and "-d" in a:
        hits.add("delete", "delete.git_ref", "删除引用")
    elif sub == "reflog" and a[:1] in (["expire"], ["delete"]):
        hits.add("delete", "delete.git_reflog", "清理 reflog")
    elif sub == "gc" and any(x.startswith("--prune") for x in a):
        hits.add("delete", "delete.git_gc", "清理不可达的提交")
    elif sub == "config" and any(re.search(r"credential|signingkey|\.token|password|extraheader", x, re.I) for x in a):
        hits.add("credentials", "cred.git_config", "改 git 的凭据设置")
    elif sub == "credential" and a[:1] in (["approve"], ["reject"], ["store"], ["erase"]):
        hits.add("credentials", "cred.git_credential", "改 git 保存的凭据")
    elif sub == "remote" and a[:1] in (["remove"], ["rm"]):
        hits.add("delete", "delete.git_remote", "删除 remote")


def _gh(args: list, hits: _Hits) -> None:
    sub, rest = _sub(args, {"-R", "--repo", "--hostname"})
    verb = _positionals(rest, {"-R", "--repo", "-b", "--body", "-t", "--title", "-F", "--body-file", "-m", "--milestone"})
    v = verb[0] if verb else ""
    if sub == "auth" and v in ("login", "logout", "refresh", "token", "setup-git", "switch"):
        hits.add("credentials", "cred.gh_auth", f"gh auth {v}：改 GitHub 登录凭据")
    elif sub in ("secret", "variable") and v in ("set", "delete", "remove"):
        hits.add("credentials", "cred.gh_secret", f"gh {sub} {v}：改仓库机密")
    elif sub in ("ssh-key", "gpg-key") and v in ("add", "delete"):
        hits.add("credentials", "cred.gh_key", f"gh {sub} {v}：改账号密钥")
    elif sub in ("pr", "issue") and v in ("create", "new", "comment", "review", "merge", "close", "reopen", "edit", "ready",
                                          "transfer", "lock", "unlock", "develop", "pin"):
        hits.add("send", "send.gh", f"gh {sub} {v}：在 GitHub 上公开发布")
    elif sub == "release" and v in ("create", "upload", "edit"):
        hits.add("send", "send.gh_release", f"gh release {v}：发布版本")
    elif sub == "gist" and v in ("create", "new", "edit"):
        hits.add("send", "send.gh_gist", "gh gist：公开发布代码片段")
    elif sub == "repo" and v in ("create", "fork", "rename", "archive", "edit", "sync"):
        hits.add("send", "send.gh_repo", f"gh repo {v}：改 GitHub 上的仓库")
    elif sub == "workflow" and v in ("run", "enable", "disable"):
        hits.add("send", "send.gh_workflow", f"gh workflow {v}：触发远端流程")
    elif sub == "api":
        method = None
        for k, a in enumerate(args):
            if a in ("-X", "--method") and k + 1 < len(args):
                method = args[k + 1].upper()
            elif a.startswith("--method="):
                method = a.split("=", 1)[1].upper()
        fields = any(a in ("-f", "-F", "--field", "--raw-field", "--input") or a.startswith(("--field=", "--raw-field="))
                     for a in args)
        if (method and method not in ("GET", "HEAD")) or (fields and method in (None, "POST", "PUT", "PATCH")):
            hits.add("send", "send.gh_api", "gh api 写操作：改 GitHub 上的内容")
            if method == "DELETE":
                hits.add("delete", "delete.gh_api", "gh api DELETE")
    if sub == "sponsor" or (sub == "api" and any("sponsor" in a for a in args)):
        hits.add("spend", "spend.gh_sponsor", "GitHub 赞助")


def _cloud_spend(name: str, args: list, hits: _Hits) -> None:
    p = [x.lower() for x in _positionals(args)]
    s = " ".join(p)
    if name == "aws":
        if re.search(r"\b(run-instances|request-spot-instances|request-spot-fleet|purchase-[a-z-]+|allocate-hosts|"
                     r"create-capacity-reservation|register-domain|renew-domain|transfer-domain|create-db-instance|"
                     r"create-db-cluster|create-cluster|create-nat-gateway|create-instances|buy-[a-z-]+|"
                     r"accept-reserved-[a-z-]+|modify-instance-attribute)\b", s):
            hits.add("spend", "spend.aws", "创建会计费的 AWS 资源 / 购买")
        if re.search(r"\b(configure|create-access-key|update-access-key|delete-access-key|create-login-profile|"
                     r"update-login-profile|change-password|put-secret-value|create-secret|update-secret|delete-secret|"
                     r"put-parameter|create-service-specific-credential|attach-[a-z]+-policy|put-[a-z]+-policy|"
                     r"create-key|schedule-key-deletion|import-key-material)\b", s):
            hits.add("credentials", "cred.aws", "改 AWS 凭据 / 权限 / 机密")
        if re.search(r"\b(create-budget|update-budget|delete-budget|put-budget)\b", s) or (p[:1] == ["budgets"]
                                                                                           and len(p) > 1 and not p[1].startswith(("describe", "list", "view"))):
            hits.add("price", "price.aws_budget", "改 AWS 预算")
        if p[:1] == ["s3"] and len(p) > 1 and p[1] in ("cp", "sync", "mv") and any(x.startswith("s3://") for x in p[-1:]):
            hits.add("send", "send.s3", "上传到 S3")
        if p[:1] == ["s3"] and len(p) > 1 and p[1] in ("rm", "rb"):
            hits.add("delete", "delete.s3", "删除 S3 对象 / 桶")
        if p[:1] == ["s3"] and "--delete" in args:
            hits.add("delete", "delete.s3_sync", "aws s3 sync --delete")
        if p[:1] in (["ses"], ["sesv2"], ["sns"], ["sqs"]) and re.search(r"\b(send-|publish)", s):
            hits.add("send", "send.aws_msg", "经 AWS 发邮件 / 消息")
    elif name in ("gcloud",):
        if re.search(r"\b(instances|clusters|tpus|vms|registrations|reservations|commitments|node-pools)\s+(create|register|"
                     r"purchase)\b", s) or re.search(r"\bcommitments\s+create\b", s):
            hits.add("spend", "spend.gcloud", "创建会计费的 GCP 资源 / 购买")
        if p[:1] == ["auth"] and len(p) > 1 and p[1] in ("login", "activate-service-account", "revoke", "application-default"):
            hits.add("credentials", "cred.gcloud_auth", "改 gcloud 登录凭据")
        if re.search(r"\b(keys|secrets|versions)\s+(create|add|delete|destroy|disable)\b", s):
            hits.add("credentials", "cred.gcloud_keys", "改 GCP 密钥 / 机密")
        if re.search(r"\bbudgets\s+(create|update|delete)\b", s):
            hits.add("price", "price.gcloud_budget", "改 GCP 预算")
        if re.search(r"\b(deploy)\b", s):
            hits.add("send", "send.deploy", "部署上线")
    elif name == "az":
        if re.search(r"\b(vm|aks|vmss|sql\s+server|webapp|functionapp|cosmosdb)\s+create\b", s):
            hits.add("spend", "spend.az", "创建会计费的 Azure 资源")
        if p[:1] in (["login"], ["logout"]) or re.search(r"\b(credential|secret|keys?)\s+(reset|set|create|delete|renew)\b", s):
            hits.add("credentials", "cred.az", "改 Azure 凭据 / 机密")
        if re.search(r"\b(deploy|up)\b", s) and p[:1] in (["webapp"], ["functionapp"], ["staticwebapp"], ["containerapp"]):
            hits.add("send", "send.deploy", "部署上线")
        if re.search(r"\bconsumption\s+budget\s+(create|update|delete)\b", s):
            hits.add("price", "price.az_budget", "改 Azure 预算")
    elif name in ("doctl", "hcloud", "linode-cli", "vultr-cli", "civo", "scw", "exo", "upcloud", "oci", "ibmcloud", "aliyun",
                  "tccli", "multipass", "eksctl"):
        if re.search(r"\b(create|run|launch|resize|scale|upgrade)\b", s):
            hits.add("spend", "spend.cloud", f"{name}：创建 / 扩容会计费的资源")
    elif name in ("fly", "flyctl"):
        if p[:1] in (["scale"], ["machine"], ["machines"], ["volumes"], ["postgres"], ["redis"]) and re.search(
                r"\b(count|vm|memory|run|create|clone|extend)\b", s):
            hits.add("spend", "spend.fly", "fly：扩容 / 新建计费资源")
        if p[:1] == ["deploy"] or p[:1] == ["launch"]:
            hits.add("send", "send.deploy", "部署上线")
        if p[:1] == ["secrets"] and len(p) > 1 and p[1] in ("set", "unset", "import"):
            hits.add("credentials", "cred.fly", "改 fly 机密")
    elif name == "heroku":
        if p and re.match(r"^(ps:scale|ps:resize|ps:type|addons:create|addons:upgrade|apps:create|create)$", p[0]):
            hits.add("spend", "spend.heroku", "heroku：扩容 / 加计费插件")
        if p and re.match(r"^(config:set|config:unset|config:add|auth:token|authorizations:create|login|logout)$", p[0]):
            hits.add("credentials", "cred.heroku", "改 heroku 配置 / 凭据")
        if p and p[0] in ("apps:destroy", "destroy", "addons:destroy", "pg:reset"):
            hits.add("delete", "delete.heroku", "heroku：删除应用 / 数据")
    elif name in ("terraform", "tofu"):
        if p[:1] == ["apply"]:
            hits.add("spend", "spend.terraform", "terraform apply：创建 / 改动计费的云资源")
            if "-destroy" in args:
                hits.add("delete", "delete.terraform", "terraform apply -destroy")
        if p[:1] == ["destroy"]:
            hits.add("delete", "delete.terraform", "terraform destroy：删除云资源")
    elif name == "pulumi":
        if p[:1] in (["up"], ["update"]):
            hits.add("spend", "spend.pulumi", "pulumi up：创建 / 改动计费的云资源")
        if p[:1] == ["destroy"]:
            hits.add("delete", "delete.pulumi", "pulumi destroy：删除云资源")
    elif name in ("cdk", "sam", "serverless", "sls", "amplify", "eb", "copilot", "sst"):
        if p[:1] in (["deploy"], ["up"], ["publish"], ["push"]):
            hits.add("send", "send.deploy", "部署上线")
            hits.add("spend", "spend.deploy_infra", "部署会创建计费的云资源")
        if p[:1] in (["destroy"], ["remove"], ["delete"], ["terminate"]):
            hits.add("delete", "delete.cloud", f"{name}：删除云资源")


def _stripe(args: list, hits: _Hits) -> None:
    p = [x.lower() for x in _positionals(args, {"-d", "--data", "--api-key", "--stripe-account", "-c", "--color"})]
    if not p:
        return
    res, verb = p[0], (p[1] if len(p) > 1 else "")
    if res in ("login", "logout") or (res == "config" and "--set" in args):
        hits.add("credentials", "cred.stripe", "改 Stripe 登录 / 密钥")
    if res in ("post", "delete") and len(p) > 1:
        url = p[1]
        if re.search(r"/v1/(prices|products|coupons|promotion_codes|plans|shipping_rates|tax_rates)", url):
            hits.add("price", "price.stripe", "改 Stripe 价格 / 商品 / 优惠")
        elif re.search(r"/v1/(charges|payment_intents|payouts|transfers|refunds|invoices|subscriptions|checkout|topups)", url):
            hits.add("spend", "spend.stripe", "Stripe 收付款 / 退款 / 订阅")
        elif re.search(r"/v1/(api_keys|webhook_endpoints|apps/secrets)", url):
            hits.add("credentials", "cred.stripe", "改 Stripe 密钥 / webhook")
        else:
            hits.add("spend", "spend.stripe", "Stripe 写操作")
        if res == "delete":
            hits.add("delete", "delete.stripe", "Stripe 删除")
        return
    writes = verb in ("create", "update", "delete", "confirm", "capture", "pay", "cancel", "finalize", "void", "attach",
                      "reverse", "approve", "expire", "archive", "deactivate", "send")
    if not writes:
        return
    if res in ("prices", "products", "coupons", "promotion_codes", "plans", "shipping_rates", "tax_rates", "billing_meters"):
        hits.add("price", "price.stripe", f"stripe {res} {verb}：改价格 / 商品 / 优惠")
    elif res in ("charges", "payment_intents", "payouts", "transfers", "refunds", "invoices", "subscriptions", "checkout",
                 "topups", "subscription_schedules", "payment_links", "setup_intents", "invoiceitems", "credit_notes"):
        hits.add("spend", "spend.stripe", f"stripe {res} {verb}：收付款 / 退款 / 订阅")
    elif res in ("webhook_endpoints", "api_keys", "secrets"):
        hits.add("credentials", "cred.stripe", f"stripe {res} {verb}：改密钥 / webhook")
    else:
        hits.add("spend", "spend.stripe", f"stripe {res} {verb}")
    if verb == "delete":
        hits.add("delete", "delete.stripe", "Stripe 删除")


_PUBLISH = {("npm", "publish"), ("pnpm", "publish"), ("yarn", "publish"), ("bun", "publish"), ("cargo", "publish"),
            ("twine", "upload"), ("poetry", "publish"), ("uv", "publish"), ("gem", "push"), ("docker", "push"),
            ("podman", "push"), ("nerdctl", "push"), ("helm", "push"), ("mvn", "deploy"), ("gradle", "publish"),
            ("vsce", "publish"), ("ovsx", "publish"), ("hatch", "publish"), ("flit", "publish"), ("deno", "publish"),
            ("jsr", "publish"), ("pdm", "publish"), ("rye", "publish"), ("swift", "package-registry"), ("dart", "pub"),
            ("flutter", "pub"), ("npm", "unpublish"), ("npm", "deprecate"), ("npm", "dist-tag"), ("expo", "publish"),
            ("eas", "submit"), ("eas", "update"), ("fastlane", "deliver"), ("fastlane", "pilot"), ("gradle", "publishToMavenCentral"),
            ("dotnet", "nuget"), ("nuget", "push"), ("conan", "upload"), ("cocoapods", "trunk"), ("pod", "trunk")}
_DEPLOY = {("vercel", None), ("netlify", "deploy"), ("wrangler", "deploy"), ("wrangler", "publish"), ("wrangler", "pages"),
           ("wrangler", "versions"), ("firebase", "deploy"), ("fly", "deploy"), ("flyctl", "deploy"), ("surge", None),
           ("railway", "up"), ("render", "deploy"), ("now", None), ("gh-pages", None), ("kamal", "deploy"),
           ("caprover", "deploy"), ("dokku", None), ("supabase", "deploy"), ("supabase", "functions"), ("deployctl", None),
           ("cf", "push"), ("appcenter", None)}
_MAIL = {"mail", "mailx", "sendmail", "mutt", "neomutt", "msmtp", "ssmtp", "swaks", "s-nail", "nail", "aerc", "himalaya",
         "telegram-send", "signal-cli", "twilio", "slack", "slackcat", "ntfy", "apprise", "notify-send-remote", "wacli",
         "imessage", "toot", "bsky", "tweet", "twurl", "gmail", "mailgun", "sendgrid"}
_CRED_CMDS = {"passwd", "chpasswd", "chage", "usermod", "ssh-keygen", "ssh-add", "ssh-copy-id", "secret-tool", "htpasswd",
              "keyring", "pass", "gopass", "certbot", "mkcert", "cloudflared"}


def _simple(sc: Simple, hits: _Hits, depth: int, text: str) -> None:
    name, args = sc.name, sc.args
    low = [a.lower() for a in args]
    # ---- delete
    if name in ("rm", "rmdir", "unlink", "shred", "srm", "wipe", "trash", "trash-put", "trash-rm", "trash-empty", "rmtrash",
                "del", "erase", "rd", "Remove-Item", "wipefs", "sfdisk", "fdisk", "parted", "blkdiscard") or name.startswith(
            "mkfs"):
        hits.add("delete", f"delete.{'rm' if name in ('rm', 'rmdir', 'unlink') else name.replace('.', '_')}",
                 f"{name}：删除文件 / 数据" if not name.startswith("mkfs") else "格式化磁盘")
    if name == "gio" and low[:1] in (["trash"], ["remove"]):
        hits.add("delete", "delete.gio", "gio trash / remove：删除文件")
    if name == "find" and any(a in ("-delete",) for a in args):
        hits.add("delete", "delete.find", "find -delete：删除找到的文件")
    if name == "truncate":
        hits.add("delete", "delete.truncate", "truncate：清空文件")
    if name == "dd" and any(a.startswith("of=") for a in args):
        hits.add("delete", "delete.dd", "dd of=：覆盖写入")
    if name == "crontab" and any(a in ("-r", "-ir") for a in args):
        hits.add("delete", "delete.crontab", "crontab -r：删除定时任务")
    if name == "rsync" and any(a.startswith(("--delete", "--remove-source-files", "--del")) for a in args):
        hits.add("delete", "delete.rsync", "rsync --delete：删除目标端多出的文件")
    if name == "rclone" and low[:1] and low[0] in ("delete", "deletefile", "purge", "rmdir", "rmdirs", "sync", "move", "moveto",
                                                   "cleanup", "dedupe"):
        hits.add("delete", "delete.rclone", f"rclone {low[0]}：会删除文件")
    if name in ("mv",) and any(a in ("/dev/null",) for a in args):
        hits.add("delete", "delete.mv_null", "mv 到 /dev/null")
    if name == "redis-cli" and any(a.upper() in ("FLUSHALL", "FLUSHDB", "DEL", "UNLINK", "SHUTDOWN") for a in args):
        hits.add("delete", "delete.redis", "redis：删除数据")
    if name in _RESOURCE_CLIS and name not in _PKG_LOCAL:
        pos = _positionals(args)
        for a in pos[:4]:
            if _DELETE_WORDS.match(a):
                hits.add("delete", f"delete.{name}", f"{name} {a}：删除资源")
                break
    if name in ("npm", "pnpm", "yarn") and low[:1] == ["unpublish"]:
        hits.add("delete", "delete.unpublish", "撤下已发布的包")
    if name in ("docker", "podman", "nerdctl") and (low[:1] in (["rm"], ["rmi"]) or "prune" in low[:3] or
                                                     (len(low) > 1 and low[1] in ("rm", "remove", "prune"))):
        hits.add("delete", "delete.container", f"{name}：删除容器 / 镜像 / 卷")
    if name in ("kubectl", "oc") and low[:1] == ["delete"]:
        hits.add("delete", "delete.kubectl", "kubectl delete")
    # ---- send
    w, method = _http_write(name, args)
    if w:
        hits.add("send", "send.http", f"{name} {method or 'POST'}：向外部发送数据")
        if method == "DELETE":
            hits.add("delete", "delete.http", "HTTP DELETE")
        joined = " ".join(args)
        if _PRICE_URL.search(joined) or _PRICE_WORD.search(joined):
            hits.add("price", "price.http", "改价格 / 预算 / 优惠的接口")
    if name == "curl" and any(a in ("-T", "--upload-file") or a.startswith("--upload-file") for a in args):
        hits.add("send", "send.upload", "curl 上传文件")
    if name == "git":
        _git(args, hits)
    if name == "gh":
        _gh(args, hits)
    if name in ("glab", "tea", "hub") and re.search(r"\b(create|note|comment|merge|close|approve|release|delete)\b",
                                                    " ".join(low[:3])):
        hits.add("send", "send.forge", f"{name}：在代码托管平台上发布")
    if name in _MAIL:
        hits.add("send", "send.mail", f"{name}：发邮件 / 消息")
    if name in ("osascript",) and re.search(r"(Messages|Mail)\b.*\bsend|send\b.*\b(Messages|Mail)", text, re.I):
        hits.add("send", "send.osascript", "osascript：用 Messages / Mail 发送")
    if name in ("scp", "sftp", "ftp", "lftp", "tftp", "rcp", "smbclient", "croc", "wormhole", "magic-wormhole", "ffsend",
                "transfer"):
        hits.add("send", "send.upload", f"{name}：把文件传到别处")
    if name == "rsync":
        pos = _positionals(args, {"-e", "--rsh", "--exclude", "--include", "--filter", "-f", "--files-from", "--port",
                                  "--chmod", "--chown", "--rsync-path", "--log-file", "--password-file", "-T", "--temp-dir"})
        if len(pos) >= 2 and re.match(r"^([^/\s]*@)?[A-Za-z0-9._-]+::?|^rsync://", pos[-1]):
            hits.add("send", "send.upload", "rsync 上传到远端")
    if name in ("rclone",) and low[:1] and low[0] in ("copy", "copyto", "sync", "move", "moveto", "copyurl", "rcat"):
        pos = _positionals(args)
        if pos and ":" in pos[-1] and not re.match(r"^[A-Za-z]:\\", pos[-1]):
            hits.add("send", "send.upload", "rclone 上传到远端")
    if name in ("gsutil",) and low[:1] and low[0] in ("cp", "mv", "rsync") and _positionals(args)[-1:] and \
            _positionals(args)[-1].startswith("gs://"):
        hits.add("send", "send.upload", "上传到 Google Cloud Storage")
    if name in ("s3cmd",) and low[:1] and low[0] in ("put", "sync", "cp", "mv"):
        hits.add("send", "send.upload", "上传到 S3")
    if name in ("b2", "mc", "azcopy") and re.search(r"\b(upload|cp|mirror|sync|put|copy)\b", " ".join(low[:3])):
        hits.add("send", "send.upload", f"{name}：上传到云存储")
    if name in ("nc", "ncat", "netcat", "socat") and (sc.flags & {"subst"} or any("<" in r for r in sc.redirs) or
                                                       " | " in text or re.search(r"\|\s*(nc|ncat|netcat|socat)\b", text)):
        hits.add("send", "send.netcat", f"{name}：把数据发到网络")
    sub1 = _positionals(args)[:1]
    for (tool, verb) in _PUBLISH:
        if name == tool and sub1 == [verb]:
            hits.add("send", "send.publish", f"{name} {verb}：发布到公开仓库")
            break
    for (tool, verb) in _DEPLOY:
        if name == tool and (verb is None or sub1 == [verb] or (verb == "pages" and low[:2] == ["pages", "deploy"])):
            if tool == "vercel" and sub1 and sub1[0] in ("env", "login", "logout", "whoami", "ls", "list", "inspect", "logs",
                                                           "pull", "dev", "link", "domains", "dns", "certs", "project", "teams",
                                                           "switch", "help", "rm", "remove"):
                continue
            if tool == "wrangler" and verb == "pages" and low[:2] != ["pages", "deploy"]:
                continue
            hits.add("send", "send.deploy", f"{name} {verb or ''}：部署上线".replace("  ", " "))
            break
    if name == "twilio" or (name in ("aws",) and low[:1] in (["ses"], ["sesv2"], ["sns"])):
        pass   # covered in _MAIL / _cloud_spend
    # ---- credentials
    if name in _CRED_CMDS:
        sub = low[:1]
        if not (name == "ssh-keygen" and any(a in ("-l", "-lf", "-F", "-y", "-Q", "-L", "-E") for a in args)) and \
                not (name == "ssh-add" and any(a in ("-l", "-L", "-T") for a in args)) and \
                not (name in ("pass", "gopass") and (not sub or sub[0] in ("show", "ls", "list", "find", "grep", "git"))) and \
                not (name == "keyring" and sub[:1] == ["get"]) and \
                not (name == "secret-tool" and sub[:1] in (["lookup"], ["search"])) and \
                not (name == "cloudflared" and not re.search(r"\b(login|token|create|delete|cert)\b", " ".join(low))) and \
                not (name == "usermod" and not any(a in ("-p", "--password", "-L", "-U") for a in args)):
            hits.add("credentials", f"cred.{name}", f"{name}：改账号密码 / 密钥 / 证书")
    if name == "security" and low[:1] and re.match(r"^(add-|delete-|set-|import|create-keychain|unlock-keychain|"
                                                   r"change-keychain-password|authorizationdb|trust-settings-import|"
                                                   r"add-trusted-cert|remove-trusted-cert)", low[0]):
        hits.add("credentials", "cred.keychain", "改 macOS 钥匙串")
    if name == "gpg" and any(re.match(r"^--(gen-key|full-gen-key|full-generate-key|quick-gen-key|quick-generate-key|import|"
                                      r"delete-|edit-key|passwd|change-passphrase|quick-add-key|quick-set-expire|"
                                      r"quick-revoke|gen-revoke|generate-revocation|send-keys|import-ownertrust)", a)
                             for a in args):
        hits.add("credentials", "cred.gpg", "改 GPG 密钥")
    if name in ("vault",) and low[:1] and low[0] in ("write", "delete", "login", "token", "auth", "kv", "operator", "policy",
                                                     "secrets"):
        if not (low[0] == "kv" and len(low) > 1 and low[1] in ("get", "list", "metadata")):
            hits.add("credentials", "cred.vault", "改 Vault 机密")
    if name in ("op",) and re.search(r"\b(item|document|vault|account|user|connect|service-account)\s+(create|edit|delete|"
                                     r"add|forget|remove|move|share)\b|\bsignin\b|\baccount add\b", " ".join(low)):
        hits.add("credentials", "cred.1password", "改 1Password 条目")
    if name in ("bw", "rbw") and low[:1] and low[0] in ("create", "edit", "delete", "login", "logout", "restore", "move",
                                                         "register", "config", "add", "remove", "rm", "generate"):
        hits.add("credentials", "cred.bitwarden", "改 Bitwarden 条目")
    if name in ("doppler", "infisical") and re.search(r"\b(secrets?\s+(set|delete|upload)|login|logout|configure)\b",
                                                      " ".join(low)):
        hits.add("credentials", "cred.secrets_mgr", f"{name}：改机密")
    if name in ("wrangler",) and re.search(r"\bsecret\s+(put|delete|bulk)\b|\blogin\b|\blogout\b", " ".join(low)):
        hits.add("credentials", "cred.wrangler", "改 Cloudflare 机密 / 登录")
    if name in ("vercel",) and re.search(r"\benv\s+(add|rm|remove|pull)\b|\blogin\b|\blogout\b", " ".join(low)):
        hits.add("credentials", "cred.vercel", "改 Vercel 环境变量 / 登录")
    if name in ("netlify",) and re.search(r"\benv:(set|unset|import)\b|\blogin\b|\blogout\b", " ".join(low)):
        hits.add("credentials", "cred.netlify", "改 Netlify 环境变量 / 登录")
    if name == "firebase" and re.search(r"\bsecrets:(set|destroy)|\blogin\b|\blogout\b", " ".join(low)):
        hits.add("credentials", "cred.firebase", "改 Firebase 机密 / 登录")
    if name in ("docker", "podman", "nerdctl", "helm", "oras", "crane", "skopeo") and low[:1] in (["login"], ["logout"]) or \
            (name in ("docker", "podman") and low[:2] in (["secret", "create"], ["secret", "rm"])) or \
            (name in ("helm",) and low[:2] in (["registry", "login"],)):
        hits.add("credentials", "cred.registry_login", f"{name} login：改登录凭据")
    if name in ("npm", "pnpm", "yarn") and (low[:1] in (["login"], ["logout"], ["adduser"], ["token"]) or
                                            (low[:2] == ["npm", "login"]) or (low[:1] == ["config"] and any(
                "_authtoken" in a or "_auth" in a for a in low))):
        hits.add("credentials", "cred.npm", f"{name}：改 npm 登录凭据")
    if name in ("kubectl", "oc") and (low[:2] in (["create", "secret"], ["edit", "secret"], ["delete", "secret"],
                                                  ["patch", "secret"]) or (low[:2] == ["config", "set-credentials"])):
        hits.add("credentials", "cred.kubectl", "改 Kubernetes 机密 / 凭据")
    if name == "git":
        pass   # cred.git_config inside _git
    if name in ("cargo",) and low[:1] in (["login"], ["logout"], ["owner"]):
        hits.add("credentials", "cred.cargo", "改 crates.io 凭据")
    if name in ("aws", "gcloud", "az", "doctl", "hcloud", "heroku", "fly", "flyctl", "terraform", "tofu", "pulumi", "cdk", "sam",
                "serverless", "sls", "amplify", "eb", "copilot", "sst", "linode-cli", "vultr-cli", "civo", "scw", "exo",
                "upcloud", "oci", "ibmcloud", "aliyun", "tccli", "multipass", "eksctl"):
        _cloud_spend(name, args, hits)
    if name in ("doctl", "hcloud", "linode-cli", "vultr-cli") and re.search(r"\b(auth|token|ssh-key|api-key)\b",
                                                                            " ".join(low[:2])):
        hits.add("credentials", "cred.cloud", f"{name}：改账号凭据 / 密钥")
    # ---- credential paths written by a command (redirect targets and writer arguments)
    targets = list(sc.redirs)
    if name in _WRITERS:
        if name in ("sed", "perl") and not any(a.startswith("-i") or a == "--in-place" for a in args):
            pass
        elif name in ("echo", "printf", "cat", "base64"):
            pass                                     # they only write through a redirect (collected above)
        elif name == "git" and not (low[:1] in (["checkout"], ["restore"], ["mv"], ["rm"])):
            pass
        else:
            targets += _positionals(args)
        targets += [a.split("=", 1)[1] for a in args if a.startswith(("of=", "--output=", "--out="))]
        for k, a in enumerate(args):
            if a in ("-out", "-o", "--output", "-f") and k + 1 < len(args) and name in ("openssl", "ssh-keygen", "gpg",
                                                                                      "base64"):
                targets.append(args[k + 1])
    for t in targets:
        why = cred_path(t)
        if why:
            hits.add("credentials", "cred.path", f"写入{why}")
            break
    # ---- spend / price CLIs
    if name == "stripe":
        _stripe(args, hits)
    if name in ("binance", "binance-cli") and re.search(r"\b(order|new-order|buy|sell|convert|withdraw|transfer|trade|"
                                                        r"place|margin|futures)\b", " ".join(low)):
        if not re.search(r"\b(query|get|list|history|open-orders|all-orders|info|status)\b", " ".join(low[:3])):
            hits.add("spend", "spend.trade", "下单 / 交易 / 提币")
    if name in ("cast",) and low[:1] in (["send"], ["publish"]):
        hits.add("spend", "spend.crypto", "发送链上交易")
    if name in ("solana", "spl-token") and low[:1] in (["transfer"], ["pay"], ["stake"], ["delegate-stake"]):
        hits.add("spend", "spend.crypto", "转账 / 质押")
    if name in ("bitcoin-cli", "electrum", "lncli", "lightning-cli") and re.search(
            r"\b(send|sendtoaddress|sendmany|payto|payinvoice|pay|sendpayment|sendcoins|withdraw)\b", " ".join(low)):
        hits.add("spend", "spend.crypto", "付款 / 转账")
    if name in ("shopify",) and re.search(r"\b(product|products|price|discount|app\s+deploy|theme\s+(push|publish))\b",
                                          " ".join(low)):
        hits.add("price", "price.shop", "改店铺商品 / 价格 / 主题")
    if name in ("ads", "googleads", "meta-ads") or (name in ("gcloud",) and "budgets" in low):
        hits.add("price", "price.ads", "改广告预算 / 出价")
    # ---- inline code
    if name in _INTERP:
        for k, a in enumerate(args):
            if (a in _INLINE_FLAGS or re.fullmatch(r"-[a-zA-Z]*[ce]", a)) and k + 1 < len(args):
                sc.flags.add("inline")
                _code(args[k + 1], hits, depth)
                break
            if name == "osascript" and a == "-e" and k + 1 < len(args):
                sc.flags.add("inline")
                _code(args[k + 1], hits, depth)
        if name in ("awk", "gawk") and args:
            p = _positionals(args, {"-f", "-v", "-F"})
            if p and re.search(r"\bsystem\s*\(|\|\s*\"|print\s*>\s*\"", p[0]):
                sc.flags.add("inline")
                _code(p[0], hits, depth)
    if name in ("sqlite3", "psql", "mysql", "mariadb", "mongosh", "mongo", "redis-cli", "duckdb", "clickhouse-client",
                "cqlsh", "influx", "sqlcmd", "bq", "snowsql", "d1") or (name == "wrangler" and "d1" in low[:2]):
        _text_rules(" ".join(args), hits)
        if sc.flags & {"heredoc"} or any("<" in r for r in sc.redirs):
            sc.flags.add("inline")


def _bash(cmd: str, hits: _Hits, depth: int = 0, collect: list | None = None) -> None:
    if depth > MAX_DEPTH:
        return
    text = cmd[:MAX_CMD]
    if len(cmd) > MAX_CMD:
        hits.add("delete", "too_long", "命令太长，无法完整检查")
    _text_rules(text, hits)
    seen: set[tuple] = set()
    chunks = [text] + ([ln for ln in text.splitlines() if ln.strip()] if "\n" in text else [])
    for ch in chunks:
        for c in lex(ch):
            for sc in unwrap(c, depth):
                key = (sc.name, tuple(sc.args), tuple(sc.redirs))
                if key in seen:
                    continue
                seen.add(key)
                _simple(sc, hits, depth, text)
                if collect is not None:
                    collect.append(sc)


# ================================================================ MCP tools
_READ_VERBS = {"get", "list", "read", "search", "fetch", "find", "query", "describe", "show", "view", "lookup", "check",
               "count", "download", "export", "preview", "inspect", "retrieve", "browse", "status", "info", "stats",
               "validate", "verify", "analyze", "analyse", "summarize", "estimate", "calculate", "explain", "parse", "render",
               "convert", "translate", "screenshot", "snapshot", "navigate", "scroll", "hover", "wait", "evaluate", "resolve",
               "ping", "health", "whoami", "me", "current", "load", "open", "watch", "listen", "subscribe_events", "test",
               "dry", "draft", "compose"}
_STRONG = {"delete", "remove", "send", "pay", "purchase", "charge", "publish", "transfer", "refund", "destroy", "drop", "purge",
           "post", "buy"}
_MCP_SEND = {"send", "post", "publish", "reply", "comment", "tweet", "retweet", "email", "mail", "notify", "upload", "push",
             "invite", "forward", "broadcast", "submit", "share", "dm", "message", "announce", "tag", "mention", "react",
             "like", "repost", "schedule_post", "deploy", "release", "sms", "call", "dial", "text"}
_MCP_DELETE = {"delete", "remove", "rm", "destroy", "drop", "purge", "erase", "wipe", "trash", "unlink", "truncate",
               "unpublish", "uninstall", "terminate", "clear", "flush", "reset", "revoke", "kill", "empty", "discard", "cancel"}
_MCP_SPEND = {"pay", "payment", "payments", "purchase", "buy", "charge", "checkout", "order", "orders", "subscribe",
              "transfer", "withdraw", "refund", "tip", "donate", "bid", "trade", "sell", "swap", "stake", "mint", "invoice",
              "payout", "fund", "topup", "book", "reserve", "rent", "hire", "renew"}
_MCP_CRED_NOUN = {"password", "passwd", "passphrase", "secret", "secrets", "credential", "credentials", "token", "tokens",
                  "apikey", "api_key", "key", "keys", "auth", "login", "keychain", "ssh", "oauth", "mfa", "2fa", "totp",
                  "certificate", "cert", "permission", "permissions", "role", "roles", "access", "env", "vault"}
_MCP_CRED_VERB = {"set", "update", "create", "rotate", "change", "reset", "add", "put", "write", "store", "revoke", "delete",
                  "remove", "generate", "new", "regenerate", "issue", "grant", "edit", "modify", "assign", "import", "save"}
_MCP_PRICE = {"price", "prices", "pricing", "budget", "budgets", "discount", "discounts", "coupon", "coupons", "promo",
              "promotion", "promotions", "bid", "bids", "cpc", "cpm", "tariff", "markup"}


def _words(name: str) -> list[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return [w for w in re.split(r"[^A-Za-z0-9]+", s.lower()) if w]


def _mcp(tool: str, tool_input, hits: _Hits) -> None:
    part = tool.split("__", 2)[2] if tool.count("__") >= 2 else tool
    w = _words(part)
    if not w:
        return
    ws = set(w)
    read = w[0] in _READ_VERBS and not (ws & _STRONG)
    if not read:
        if ws & _MCP_SEND:
            hits.add("send", "send.mcp", f"{part}：对外发送 / 发布")
        if ws & _MCP_DELETE:
            hits.add("delete", "delete.mcp", f"{part}：删除 / 撤销")
        if ws & _MCP_SPEND:
            hits.add("spend", "spend.mcp", f"{part}：付款 / 下单 / 交易")
        if (ws & _MCP_CRED_NOUN) and (ws & _MCP_CRED_VERB):
            hits.add("credentials", "cred.mcp", f"{part}：改凭据 / 权限")
        if ws & _MCP_PRICE:
            hits.add("price", "price.mcp", f"{part}：改价格 / 预算 / 优惠")
    try:
        text = json.dumps(tool_input, ensure_ascii=False)[:MAX_CMD]
    except (TypeError, ValueError):
        text = str(tool_input)[:MAX_CMD]
    _text_rules(text, hits, write=not read)
    if isinstance(tool_input, dict):        # an MCP tool that runs a shell command / writes a file
        for k in ("command", "cmd", "script", "shell"):
            if isinstance(tool_input.get(k), str):
                _bash(tool_input[k], hits, 1)
        for k in ("path", "file_path", "file", "filename", "target", "destination"):
            v = tool_input.get(k)
            if isinstance(v, str) and cred_path(v) and not read:
                hits.add("credentials", "cred.path", "写入" + cred_path(v))


# ================================================================ entry points
_FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def classify(tool: str, tool_input, extra: list | None = None) -> Verdict:
    hits = _Hits()
    inp = tool_input if isinstance(tool_input, dict) else {}
    tool = tool if isinstance(tool, str) else ""
    if tool == "Bash":
        cmd = inp.get("command")
        if isinstance(cmd, str):
            _bash(cmd, hits)
    elif tool == "Delete":                  # a file deleted by a patch (Codex apply_patch, ADR-A70)
        paths = [p for p in [inp.get("file_path"), *(inp.get("files") or [])] if isinstance(p, str)]
        hits.add("delete", "delete.patch", "删除文件 " + (paths[0].rsplit("/", 1)[-1] if paths else "?")[:120])
        for p in paths:
            why = cred_path(p)
            if why:
                hits.add("credentials", "cred.path", "删除" + why)
                break
    elif tool in _FILE_TOOLS:
        p = inp.get("file_path") or inp.get("notebook_path")
        why = cred_path(p) if isinstance(p, str) else None
        if why:
            hits.add("credentials", "cred.path", "写入" + why)
        if tool == "NotebookEdit" and inp.get("edit_mode") == "delete":
            hits.add("delete", "delete.notebook_cell", "删除 notebook 单元格")
    elif tool.startswith("mcp__"):
        _mcp(tool, tool_input, hits)
    # WebFetch / WebSearch (GET), Read / Glob / Grep / LS, Task, TodoWrite …: no category of their own
    for r in extra or []:
        try:
            if r.get("tool") and re.search(r["tool"], tool):
                hits.add(r["category"], f"extra.{r['category']}", r.get("why") or "本机加的危险规则")
            if r.get("bash") and tool == "Bash" and re.search(r["bash"], str(inp.get("command", ""))):
                hits.add(r["category"], f"extra.{r['category']}", r.get("why") or "本机加的危险规则")
            if r.get("path"):
                p = inp.get("file_path") or inp.get("notebook_path") or inp.get("path")
                if isinstance(p, str) and re.search(r["path"], p):
                    hits.add(r["category"], f"extra.{r['category']}", r.get("why") or "本机加的危险规则")
                elif tool == "Bash" and re.search(r["path"], str(inp.get("command", ""))):
                    hits.add(r["category"], f"extra.{r['category']}", r.get("why") or "本机加的危险规则")
        except (re.error, TypeError, KeyError):
            continue
    return hits.verdict()


# ---------------------------------------------------------------- config: only additions
def parse_extra(raw) -> tuple[list, list]:
    """(valid rules, issues). A rule = {"category": one of CATEGORIES, and one or more of "bash" / "tool" / "path": regex,
    optional "why"}. Anything else is ignored (and reported)."""
    rules, issues = [], []
    if raw is None:
        return rules, issues
    if not isinstance(raw, list):
        return rules, ["danger_extra 必须是列表 / must be a list — ignored"]
    for i, r in enumerate(raw[:MAX_EXTRA]):
        if not isinstance(r, dict) or r.get("category") not in CATEGORIES:
            issues.append(f"danger_extra[{i}]: category 必须是 {'/'.join(CATEGORIES)} — ignored")
            continue
        keys = [k for k in ("bash", "tool", "path") if isinstance(r.get(k), str) and r[k]]
        if not keys or set(r) - {"category", "bash", "tool", "path", "why"}:
            issues.append(f"danger_extra[{i}]: 只能有 category + bash / tool / path (+ why) — ignored")
            continue
        try:
            for k in keys:
                if len(r[k]) > 300:
                    raise re.error("too long")
                re.compile(r[k])
        except re.error:
            issues.append(f"danger_extra[{i}]: 正则无效或太长 / bad or too long regex — ignored")
            continue
        rule = {"category": r["category"], **{k: r[k] for k in keys}}
        if isinstance(r.get("why"), str):
            rule["why"] = r["why"][:80]
        rules.append(rule)
    if len(raw) > MAX_EXTRA:
        issues.append(f"danger_extra: 只用前 {MAX_EXTRA} 条 / only the first {MAX_EXTRA} are used")
    return rules, issues


def config_issues(cfg: dict) -> list[str]:
    """What `jarvis doctor` reports: any attempt to switch the list off or narrow it is ignored."""
    out = []
    for k in sorted(cfg or {}):
        if k.lower().startswith("danger") and k != "danger_extra":
            out.append(f"config.json 的 {k} 被忽略：危险清单只能加严，不能关闭或减少 / ignored: the danger list can only grow")
    out += parse_extra((cfg or {}).get("danger_extra"))[1]
    return out


def encode_extra(rules: list) -> str:
    return base64.urlsafe_b64encode(json.dumps(rules, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=") \
        or "-"


def decode_extra(s: str) -> list:
    if not s or s == "-":
        return []
    try:
        raw = json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
    except (ValueError, TypeError):
        return []
    return parse_extra(raw)[0]


# ---------------------------------------------------------------- batch approval scope (ADR-A48)
_TWO_WORD = {"npm", "npx", "pnpm", "yarn", "bun", "git", "cargo", "go", "make", "uv", "pip", "pip3", "poetry", "docker",
             "podman", "kubectl", "gh", "apt", "brew", "systemctl", "dotnet", "mvn", "gradle", "rake", "bundle", "composer",
             "deno", "rustup", "conda", "pnpx", "just", "task", "turbo", "nx", "hatch", "pdm",
             "rye", "swift", "xcodebuild", "flutter", "dart", "mix", "stack", "cabal", "zig", "bazel", "cmake", "ninja"}
_NO_BATCH_FLAGS = {"shell_c", "eval", "inline", "heredoc", "piped_shell", "subst", "wrapped", "remote"}
_PATH_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Read", "Glob", "Grep", "LS", "NotebookRead"}


def _under(p: str, root: str) -> bool:
    return p == root or p.startswith(root.rstrip("/") + "/")


def batch_scope(tool: str, tool_input, workdir: str | None, extra: list | None = None) -> tuple[str, tuple, str] | None:
    """(kind, key, text) of "the same kind of action" for a low-risk request, or None when it cannot be batched. Bash: the
    set of command names (with the sub-command for npm / git / cargo …, the script for an interpreter); a wrapper, an
    inline program, a substitution, `sh -c`, `eval`, `ssh host …` or a heredoc → None. File tools: the same tool inside the
    Agent's folder. WebFetch: the same host. Anything else (incl. MCP tools): the same tool name."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    if classify(tool, tool_input, extra).danger:
        return None
    if tool == "Bash":
        cmd = inp.get("command")
        if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 4000 or "\n" in cmd.strip():
            return None
        hits, simple = _Hits(), []
        _bash(cmd, hits, collect=simple)
        if not simple or any(s.flags & _NO_BATCH_FLAGS for s in simple):
            return None
        names = set()
        for s in simple:
            if s.name in ("cd", "pushd", "popd", "true", ":"):
                continue
            if s.name in _SHELLS or s.name in _INTERP or s.name in ("source", "."):
                p = _positionals(s.args)
                if not p:
                    return None                    # a REPL / stdin program: unknown
                names.add(f"{s.name} {p[0]}")
            elif s.name in _TWO_WORD:
                p = _positionals(s.args, {"-C", "-c", "-f", "--prefix", "-w", "--workspace", "--filter", "-F", "--manifest-path",
                                          "-p", "--package", "--project", "--directory", "--cwd"})
                names.add(f"{s.name} {p[0]}" if p else s.name)
            else:
                names.add(s.name)
        if not names or len(names) > 3 or any(not re.fullmatch(r"[\w.+@/:=-]+( [\w.+@/:=-]+)?", n) for n in names):
            return None
        key = tuple(sorted(names))
        return ("bash", key, "Bash：" + "、".join(key))
    if tool in _PATH_TOOLS:
        if not workdir:
            return None
        p = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or workdir
        if not isinstance(p, str):
            return None
        root = os.path.realpath(workdir)
        real = os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p))
        if not _under(real, root):
            return None
        return ("path", (tool, root), f"{tool}：工作目录 {root} 里的文件")
    if tool == "WebFetch":
        from urllib.parse import urlsplit
        try:
            host = urlsplit(str(inp.get("url", ""))).hostname or ""
        except ValueError:
            host = ""
        if not host:
            return None
        return ("host", (tool, host), f"WebFetch：{host}")
    if not tool or len(tool) > 64:
        return None
    return ("tool", (tool,), f"{tool}（任何参数）")


def same_scope(grant: tuple[str, tuple], req: tuple[str, tuple]) -> bool:
    """Does a request's scope fall inside a granted one? Bash: its command names ⊆ the granted names."""
    gk, gkey = grant
    rk, rkey = req
    if gk != rk:
        return False
    if gk == "bash":
        return set(rkey) <= set(gkey)
    return gkey == rkey


# ---------------------------------------------------------------- read-only run profile (scheduled `mode: research`, ADR-A53)
READ_TOOLS = frozenset({"Read", "Glob", "Grep", "LS", "NotebookRead", "WebFetch", "WebSearch", "TodoWrite", "BashOutput",
                        "ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool"})
READ_CMDS = frozenset({
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "ag", "fd", "stat", "file", "du", "df", "pwd", "echo",
    "printf", "date", "cal", "jq", "sort", "uniq", "cut", "tr", "nl", "column", "diff", "cmp", "comm", "basename", "dirname",
    "realpath", "readlink", "tree", "which", "whereis", "type", "true", "false", "test", "[", "seq", "md5sum", "sha1sum",
    "sha256sum", "sha512sum", "shasum", "b2sum", "xxd", "hexdump", "od", "strings", "uname", "whoami", "id", "hostname", "free",
    "uptime", "cd", "pushd", "popd", "sleep", "bat", "eza", "exa", "fold", "fmt", "expand", "unexpand", "paste", "join", "rev",
    "tac", "look", "numfmt", "env", "printenv", "locale", "nproc", "getconf", "find", "git", "sed", "curl"})
_GIT_READ = frozenset({"status", "log", "diff", "show", "rev-parse", "ls-files", "ls-tree", "blame", "grep", "describe",
                       "shortlog", "cat-file", "rev-list", "merge-base", "name-rev", "whatchanged"})
_FIND_WRITE = frozenset({"-delete", "-fprint", "-fprint0", "-fprintf", "-fls"})
_CURL_WRITE = frozenset({"-o", "--output", "-O", "--remote-name", "--remote-name-all", "-T", "--upload-file", "-K", "--config",
                         "-c", "--cookie-jar", "-D", "--dump-header", "--create-dirs", "--output-dir", "--trace", "--trace-ascii",
                         "--libcurl", "--etag-save", "--hsts", "--alt-svc"})
_SAFE_REDIR = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})


def readonly(tool: str, tool_input) -> tuple[bool, str]:
    """Is this call read-only? (deterministic; anything not provably read-only is not). Used by the research hook."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    tool = tool if isinstance(tool, str) else ""
    if tool in READ_TOOLS:
        return True, ""
    if tool.startswith("mcp__"):
        w = _words(tool.split("__")[-1])
        if w and w[0] in _READ_VERBS and not (set(w) & _STRONG) and not classify(tool, tool_input).danger:
            return True, ""
        return False, f"工具 {tool[:64]} 不是只读的"
    if tool != "Bash":
        return False, f"{tool[:64] or '这个工具'} 会写入或改动东西"
    cmd = inp.get("command")
    if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 16000:
        return False, "命令为空或太长"
    if classify("Bash", inp).danger:
        return False, "命令在危险清单里"
    simple: list = []
    _bash(cmd, _Hits(), collect=simple)
    if not simple:
        return False, "看不懂的命令"
    for s in simple:
        name = s.name
        if s.flags & {"inline", "eval", "piped_shell", "remote"}:
            return False, f"{name}：内联程序 / eval / 远程命令不算只读"
        if name in _SHELLS:
            if "shell_c" in s.flags or not s.args:
                continue
            return False, f"{name}：运行脚本不算只读"
        if name not in READ_CMDS:
            return False, f"{name} 不在只读命令清单里"
        for r in s.redirs:
            if r not in _SAFE_REDIR:
                return False, f"重定向到 {r[:80]} = 写文件"
        if name == "find" and any(a in _FIND_WRITE for a in s.args):
            return False, "find 带了删除 / 写文件的参数"
        if name == "git":
            p = _positionals(s.args, {"-C", "-c", "--git-dir", "--work-tree"})
            if not p or p[0] not in _GIT_READ or any(a in ("--output", "-o") or a.startswith("--output=") for a in s.args):
                return False, f"git {p[0] if p else ''} 不是只读的".replace("  ", " ")
        if name == "sed" and (any(a == "-i" or a.startswith(("-i", "--in-place")) for a in s.args)
                              or not any(a in ("-n", "--quiet", "--silent") for a in s.args)
                              or any(re.search(r"(^|[;}\s])\s*[wWe]\b|/[wWe]\s", a) for a in s.args)):
            return False, "sed 只允许 -n 打印（不能 -i 改文件、不能 w 写文件）"
        if name == "curl":
            write, _ = _http_write("curl", s.args)
            if write or any(a in _CURL_WRITE or a.split("=", 1)[0] in _CURL_WRITE for a in s.args):
                return False, "curl 只允许读取（GET），不能上传或写文件"
        if name in ("env", "printenv") and s.args and not all(a.startswith("-") for a in s.args):
            return False, "env 带命令不算只读"
        why = _ro_flag_write(name, s.args)
        if why:
            return False, why
    return True, ""


def _ro_flag_write(name: str, args: list) -> str | None:
    """A listed read command that still writes a file or runs another program through one of its own flags / operands."""
    if name == "git":
        it = iter(args)                                  # global options only (before the sub-command): `git log -c` is fine
        for a in it:
            if a == "-c" or a.startswith(("--config-env", "--exec-path")):
                return "git -c 可以让 git 运行任意程序（pager / alias），不算只读"
            if a in ("-C", "--git-dir", "--work-tree", "--namespace"):
                next(it, None)
            elif not a.startswith("-"):
                break
    if name == "sort" and any(a == "-o" or a.startswith(("-o", "--output", "--compress-program")) for a in args):
        return "sort -o 会写文件"
    if name in ("uniq", "xxd") and len(_positionals(args)) > 1:
        return f"{name} 的第二个文件名是输出文件"
    if name == "tree" and any(a == "-o" or a.startswith("-o") for a in args):
        return "tree -o 会写文件"
    if name == "fd" and any(a in ("-x", "-X") or a.startswith(("--exec", "-x", "-X")) for a in args):
        return "fd -x 会对结果运行命令"
    if name in ("rg", "bat") and any(a.startswith("--pre") for a in args):
        return f"{name} --pre 会运行别的程序"
    if name == "date" and any(a in ("-s", "--set") or a.startswith(("-s", "--set")) for a in args):
        return "date -s 会改系统时间"
    if name == "hostname" and any(not a.startswith("-") for a in args):
        return "hostname 带参数会改主机名"
    return None


# ---------------------------------------------------------------- the hook
def hook_main(argv: list[str]) -> int:
    """Claude Code PreToolUse hook. Prints {"hookSpecificOutput": {"permissionDecision": "ask"}} for a dangerous call,
    nothing otherwise. Any failure → exit 2 (Claude Code treats that as a blocking error: the call does not run).
    `hook <extras> research` (a scheduled read-only run, ADR-A53): anything not read-only is denied outright."""
    try:
        ev = json.load(sys.stdin)
        extra = decode_extra(argv[0]) if argv else []
        if argv[1:2] == ["research"]:
            ok, why = readonly(ev.get("tool_name"), ev.get("tool_input"))
            if not ok:
                sys.stdout.write(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": "agentjarvis 只读运行（定时任务 mode: research）：" + why + "。这次运行只能读取，"
                                                "把要做的事写进报告，由人决定。"}}, ensure_ascii=False))
                return 0
        v = classify(ev.get("tool_name"), ev.get("tool_input"), extra)
        if v.danger:
            reason = "agentjarvis 危险动作清单（" + "、".join(LABEL[c].split(" · ")[0] for c in v.cats) + "）：" + v.why + \
                     "——必须在手机上逐条批准。"
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                                "permissionDecisionReason": reason}}, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001 — fail closed
        sys.stderr.write(f"agentjarvis danger hook failed ({type(e).__name__}): blocked\n")
        return 2


def main() -> None:
    a = sys.argv[1:]
    if a[:1] == ["hook"]:
        sys.exit(hook_main(a[1:]))
    if a[:1] == ["check"] and len(a) >= 2:            # `python -m jarvis_host.danger check 'rm -rf x'` (debugging)
        v = classify("Bash", {"command": " ".join(a[1:])})
        print(json.dumps({"cats": v.cats, "rules": v.rules, "why": v.why}, ensure_ascii=False))
        return
    sys.exit("usage: python -m jarvis_host.danger hook [extras] | check <command>")


if __name__ == "__main__":
    main()
