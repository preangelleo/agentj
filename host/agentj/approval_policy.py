"""0.26.9 #473 / #469 (E1): the default approval policy — the owner's task is the authorisation for its follow-up steps.

One table (ROWS) is the single source: every card the host raises names its row (class) and why the owner's task does
not cover it; a row with tier `none` runs without a card. Face ID by default only for spending the host cannot prove is
inside the owner's budget and for a secret shown / copied / sent out; Agent J's own floors (its hook and approval files,
its code and settings, the trust roots) keep Face ID. Rows marked provisional wait for the chairman's signature
(ADR-0269-w3-policy); until then irreversible steps keep a one-tap card without Face ID.

In force only at the default level (`in_force`): an owner who picked 「安全」/「中等」 on a paired phone (a signed record in
approval_signers.json) keeps that level exactly; a signed 「全放行」 keeps its own table. Decided from the real call and
host-held facts only — never from the model's words. Standard library + Agent J modules only.
"""
from __future__ import annotations

import re

NONE, TAP, FACE = "none", "tap", "face"

# id: (tier, provisional, zh name, en name, zh why, en why). `why` = why the owner's task scope does / does not cover it.
ROWS = {
    "secret_out": (FACE, False, "显示或外发密钥", "Shows or sends a secret",
                   "密钥会显示出来、被复制或发出 Agent J 主机：主人的任务不包含交出密钥，只有主人本人刷 Face ID 才行",
                   "a secret would be shown, copied or leave this computer: the owner's task never includes handing a key "
                   "over, so only the owner's Face ID allows it"),
    "spend_over_budget": (FACE, False, "预算外花钱", "Spending outside the budget",
                          "要花钱，而 Agent J 无法确认金额在主人设的预算以内（没设预算、超出，或金额主机核实不了）",
                          "it spends money and Agent J cannot confirm the amount is inside the owner's budget (no budget, "
                          "over it, or an amount the host cannot verify)"),
    "self_protect": (FACE, False, "改 Agent J 自己的安全", "Changes Agent J's own safety",
                     "动的是 Agent J 自己的审批 / 钩子文件、代码或配对与席位：Agent 不能替自己放宽权限",
                     "it touches Agent J's own approval / hook files, its code, or pairing and seats: an Agent never "
                     "widens its own permissions"),
    "fetch_run": (TAP, False, "下载后直接运行", "Runs what it downloads",
                  "同一条命令里下载并运行别人的代码，主机看不到要运行什么",
                  "it downloads code and runs it in one command; the host cannot see what will run"),
    "delete_permanent": (TAP, True, "删除", "Deletes",
                         "删除不是移到回收站，主人的任务没说可以删；远端强推 / 删分支或任务文件夹外的批量删除要 Face ID",
                         "a delete that is not a move to the trash — the owner's task did not say to delete it; a remote force "
                         "push / branch delete or a bulk delete outside the task folder needs Face ID"),
    "send_new": (TAP, True, "发给白名单外的对象", "Sends outside the allowlist",
                 "收件人不是主人本人的地址，也不在主人在手机上设的白名单里，发出后收不回",
                 "a recipient is neither the owner's own address nor on the allowlist the owner set on the phone; it cannot "
                 "be taken back"),
    "publish": (TAP, True, "公开发布", "Publishes publicly",
                "发布后所有人都能看到，撤不回已被看到的内容",
                "once published anyone can see it; what was seen cannot be taken back"),
    "secret_read": (FACE, True, "读取、复制或修改密钥文件", "Reads, copies or changes a key file",
                    "要读出、复制或改写密钥 / 密码文件（不是按变量名正常使用），只有主人本人刷 Face ID 才行",
                    "it reads out, copies or rewrites a key / password file (not ordinary use by variable name)"),
    "opaque": (TAP, True, "看不清要做什么", "Unclear what it does",
               "命令是编码或拼出来的，主机判断不了它会做什么",
               "the command is encoded or assembled; the host cannot tell what it will do"),
    "admin": (TAP, True, "管理员操作", "Administrator action",
              "要用管理员权限、控制 Agent J 主机或开沙箱外的入口，主人的任务没有说到这一步",
              "it needs administrator rights, controls the computer or opens a way outside the sandbox"),
    "other": (TAP, True, "其他需要确认的一步", "Another step to confirm",
              "主机没法确认这一步在主人这件任务的范围内", "the host cannot confirm this step is inside the owner's task"),
    # ---- no card
    "spend_in_budget": (NONE, False, "预算内花钱", "Spending inside the budget",
                        "金额主机核实过，在主人设的日 / 月预算以内", "the host verified the amount is inside the owner's budget"),
    "ceo_dispatch": (NONE, False, "派花名册 CEO", "Dispatches a roster CEO",
                     "主机核验过的派单命令，是主人安排的任务本身", "a host-verified dispatch: the owner's task itself"),
    "deps": (NONE, False, "补依赖", "Installs dependencies", "装依赖是完成任务的常规步骤", "installing dependencies is routine"),
    "own_config": (NONE, False, "改 Agent J 自己的普通设置", "Changes Agent J's ordinary settings",
                   "普通设置可随时改回；安全设置仍要主人批准", "ordinary settings can be changed back; security settings still ask"),
    "send_reversible": (TAP, True, "推送或上传副本", "A push or a new remote copy",
                        "主机能撤回，但看不出远端是不是公开的、是不是第一次推送",
                        "the host can undo it but cannot tell whether the remote is public or this is a first push"),
    "delete_reversible": (NONE, False, "移到回收站", "Moves to the trash",
                          "只是移到回收站，随时可以还原", "only moved to the trash; it can be restored any time"),
    "mail_known": (NONE, True, "发给主人或白名单", "Mails the owner or the allowlist",
                   "每个收件人都是主人本人的地址或主人在手机上设的白名单",
                   "every recipient is the owner's own address or the allowlist the owner set on the phone"),
    "routine": (NONE, False, "常规步骤", "Routine step", "不属于任何危险类别", "in no danger class"),
}
TIERS = {k: v[0] for k, v in ROWS.items()}
PROVISIONAL = tuple(k for k, v in ROWS.items() if v[1])

PUBLISH_RULES = frozenset({"send.publish", "send.gh_release", "send.deploy", "send.gh_gist", "send.gh_repo"})
_SHELL = ("Bash", "exec_command", "shell_command", "bash")
MAIL_TOOLS = ("agentj mail", "agentj mail send", "agentj mail notes", "agentj mail reply")

# security round 5 (CEO ruling "remove, don't rewrite"): there is NO key-use no-card path for curl / wget. A shell command that
# names a secret-like variable, dumps the environment or reads a keychain / password manager is `secret_out` → Face ID.
# A program that reads its key from the environment itself (an SDK, `python3 script.py`) names nothing and is judged as usual.
# A curl / wget vendor allow-list is a follow-up for the chairman, not shipped.
_KEYREF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
_SECRET_PART = frozenset({"KEY", "KEYS", "APIKEY", "TOKEN", "TOKENS", "SECRET", "SECRETS", "PASS", "PASSWD", "PASSWORD", "PWD_HASH",
                          "CRED", "CREDS", "CREDENTIAL", "CREDENTIALS", "AUTH", "BEARER", "PAT", "DSN", "PRIVATE"})
_SECRET_NAMES = frozenset({"DATABASE_URL", "REDIS_URL", "MONGODB_URI", "MONGO_URL", "SENTRY_DSN", "STRIPE_SK", "MYSQL_PWD",
                           "DB_URL", "POSTGRES_URL", "PGPASSWORD"})
_SECRET_END = ("KEY", "TOKEN", "SECRET", "PASSWORD", "_WEBHOOK", "_WEBHOOK_URL", "_DATABASE_URL", "_COOKIE", "_PWD", "_SK")
_ENV_READ = re.compile(r"process\.env|os\.environ|os\.getenv|getenv\(|ENV\[|\$env:|System\.getenv|/proc/[^\s/]+/environ|"
                       r"\bcompgen\s+-[ev]\b|\b(?:declare|typeset)\s+-[a-zA-Z]*[px]|\bexport\s+-p\b|"
                       r"(?:^|[\s|;&(`])(?:printenv|env|set)(?=\s*(?:$|[|;&>)`]))", re.M)
# r6 R1 (cheap subset, no new parser; best-effort lexical list): more environment / token readers
_ENV_READ2 = re.compile(r"\$\{!|\bps\b[^|;&\n]*(?:\s-[A-Za-z]*E\b|\se\b|\s[a-z]*e[wx]*\b|\saux[a-z]*e)|"
                        r"\bsystemctl\s+(?:--user\s+)?show-environment\b|\blaunchctl\s+getenv\b|"
                        r"(?:^|[\s|;&(`])export(?=\s*(?:$|[|;&>)`]))|\bcompgen\s+-A\s*variable\b|\bENVIRON\b|"
                        r"\bruby\b[^|;&\n]*\bENV\b|\$_ENV\b|\bjq\b[^|;&\n]*\$ENV\b|/proc/[^;&|\n]*environ|"
                        r"\bgit\s+credential-store\b[^|;&\n]*\bget\b|\bgcloud\s+auth\s+(?:print-access-token|print-identity-token|"
                        r"application-default\s+print-access-token)\b|\baz\s+account\s+get-access-token\b|"
                        r"\bkubectl\b[^|;&\n]*\bget\s+secrets?\b|\bdocker\s+(?:container\s+)?inspect\b|"
                        r"\bwrangler\b[^|;&\n]*\bsecret\b|\bkwallet-query\b", re.M)
# r6 R2: curl / wget options that pull variables or a config in — never judged, always Face ID
_CURL_PULL = re.compile(r"(?:^|[\s;&|(])(?:curl|wget)\b[^;&|\n]*\s(?:--variable\b|--expand-[a-z-]+|-K\b|-[A-Za-z]*K|--config\b)|"
                        r"(?:^|[\s;&|(])(?:WGETRC|CURL_HOME|CURLRC)=", re.M)
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)|%([A-Za-z_][A-Za-z0-9_]*)")
_EXEC_FLAG = re.compile(r"(?:^|[\s|;&(`])(?:python[0-9.]*|node|deno|bun|perl|ruby|php|osascript|pwsh)\s+(?:-[A-Za-z]*[ceE]\b|--eval)")
_RC_FILES = re.compile(r"(?:^|[\s/'\"=>])(?:\.curlrc|_curlrc|\.wgetrc|\.netrc|_netrc)\b|/etc/wgetrc\b")


_GH_KEYFILE = re.compile(r"\bgh\s+api\b[^;&|\n]*\s(?:-[fF]\s*|--(?:raw-)?field[=\s]+)\S*=@\S*(?:\.env\b|\.pem\b|\.key\b|id_[a-z0-9]+|credential|"
                         r"secret|token|password|\.netrc|auth\.json|\.npmrc|\.pypirc)", re.I)
_REMOTE_DELETE = re.compile(r"\bgh\s+repo\s+delete\b|\bgh\s+api\b[^;&|\n]*(?:-X\s*DELETE|--method[=\s]+DELETE)[^;&|\n]*\brepos/|"
                            r"\bgh\s+api\b[^;&|\n]*\brepos/[^;&|\n]*(?:-X\s*DELETE|--method[=\s]+DELETE)|"
                            r"\bflarectl\b[^;&|\n]*\bzone\s+(?:delete|rm)\b|\bwrangler\b[^;&|\n]*\b(?:zone|dns)\b[^;&|\n]*\bdelete\b",
                            re.I)


_INTERP_WRITE = re.compile(r"(?:^|[\s|;&(`])(?:python[0-9.]*|node|deno|bun|perl|ruby|php|osascript|pwsh)\b[^|;&\n]*"
                           r"(?:\s-[A-Za-z]*[ceE]\b|--eval|<<)")
_WRITE_WORDS = re.compile(r"\bopen\(|write|unlink|remove|rename|replace|rmtree|copy|move|truncate|mkdir|chmod|symlink|"
                          r"\bfs\.|\bos\.|\bshutil\b|Path\(|>>?", re.I)
_CD_THEN_WRITE = re.compile(r"\b(?:cd|pushd)\s+[\"']?\S*AJDIR[^\n]*?(?:&&|;|\|\|)[^\n]*(?:>>?|\btee\b|\bsed\s+-i|\b(?:cp|mv|rm|ln|"
                            r"chmod|chown|truncate|install|rsync|unlink|touch|dd|python[0-9.]*|node|perl|ruby)\b)")


def aj_state_write(text: str) -> bool:
    """r7 (defence in depth beside the fence): an inline interpreter / heredoc interpreter whose code names Agent J's own
    config / state folders and writes, or `cd <that folder> && <write>`. goal.suspect already covers direct writes."""
    from .goal import _AJ_DIRS
    if not re.search(_AJ_DIRS, text):
        return False
    if _INTERP_WRITE.search(text) and _WRITE_WORDS.search(text):
        return True
    return bool(re.search(_CD_THEN_WRITE.pattern.replace("AJDIR", _AJ_DIRS), text))


def secret_name(n: str) -> bool:
    n = n.upper()
    return (n in _SECRET_NAMES or any(p in _SECRET_PART for p in n.split("_")) or n.endswith(_SECRET_END)
            or "SERVICE_ROLE" in n or "WEBHOOK" in n or n.endswith("COOKIE"))


def shows_secret(text: str) -> bool:
    """F3 / r5 N5: a secret put where it can be seen — any simple command naming a secret-like variable (echo, printf,
    here-strings, assignment-then-output, logger, curl / wget headers or URLs …), environment dumps (env, printenv, set,
    declare / typeset -p / -x, export -p, compgen -e / -v, /proc/*/environ), an inline interpreter reading the environment,
    keychain / password-manager / git-credential / gh token reads."""
    from . import confirm_tier
    if not isinstance(text, str) or not text:
        return False
    if any(secret_name(n) for n in _KEYREF.findall(text)) or _ENV_READ.search(text) or _ENV_READ2.search(text) \
            or _CURL_PULL.search(text) or any(secret_name(a or b) for a, b in _TEMPLATE.findall(text)):
        return True
    if _EXEC_FLAG.search(text) and re.search(r"environ|getenv|process\.env|ENV\[|\$ENV|%ENV", text):
        return True
    try:
        simple = confirm_tier._simple(text)
    except Exception:  # noqa: BLE001 — cannot read it: a secret-ish word is enough
        return bool(re.search(r"\b(?:dump-keychain|credential\s+fill|show-token|find-(?:generic|internet)-password)\b", text))
    for c in simple:
        a = list(c.args)
        if c.name in ("printenv", "env") and (not a or any(secret_name(x) for x in a if not x.startswith("-") and "=" not in x)):
            return True
        if c.name == "security" and a[:1] and (a[0] == "dump-keychain" or a[0].startswith("find-") and any(x in ("-w", "-g") for x in a)):
            return True
        if c.name == "git" and "credential" in a[:3] and any(x in ("fill", "get") for x in a):
            return True
        if c.name == "gh" and a[:2] == ["auth", "token"] or c.name == "gh" and a[:2] == ["auth", "status"] and any(
                x in ("-t", "--show-token") for x in a):
            return True
        if (c.name, a[:1]) in (("secret-tool", ["lookup"]), ("vw", ["get"]), ("bw", ["get"]), ("op", ["read"]), ("keyring", ["get"]),
                              ("gopass", ["show"]), ("pass", ["show"])) or (c.name == "op" and a[:2] == ["item", "get"]):
            return True
    return False


def in_force(prefs, st) -> bool:
    """The default level: `standard` in force and the owner never chose a level on a paired phone. Any record — 安全 / 中等,
    or a 「全放行」 whose signature lapsed (rekeyed / unpaired: P552 falls back to 中等) — keeps the old 中等 rules."""
    from . import approval_level
    if st is None or approval_level.level(prefs, st) != "standard":
        return False
    return not isinstance(approval_level.signers(st).get(approval_level.KEY), dict)


def _text(tool, tool_input) -> str:
    from . import goal
    try:
        return goal.action_text(tool, tool_input) or ""
    except Exception:  # noqa: BLE001
        return ""


def decide(tool, tool_input, verdict, *, requirement, dispatch=False, workdir=None, env=None, classify=None,
           amount=None, budget=None, known_recipients=False, chain=False):
    """→ (tier, row). tier None = the host's other cards decide (their own card, class still `row`)."""
    from . import approval_level, confirm_tier, goal, policy_registry
    inp = tool_input if isinstance(tool_input, dict) else {}
    cats, rules = set(getattr(verdict, "cats", []) or []), set(getattr(verdict, "rules", []) or [])
    action = policy_registry.tool_action(tool)
    tier, row = _decide(tool, inp, cats, rules, action, verdict, requirement, dispatch, workdir, env, classify, amount, budget,
                        known_recipients, approval_level, confirm_tier, goal, policy_registry)
    if chain and tier == NONE and (cats or row in ("mail_known", "spend_in_budget")):
        tier = TAP      # E2: a follow-up in a background turn — its dangerous steps still raise the owner's card
    return tier, row


def _decide(tool, inp, cats, rules, action, verdict, requirement, dispatch, workdir, env, classify, amount, budget,
            known_recipients, approval_level, confirm_tier, goal, policy_registry):
    if dispatch:
        return NONE, "ceo_dispatch"
    if tool in approval_level.HOST_CARDS or tool in approval_level.HARNESS_KEEPS:
        return None, ("own_config" if tool == "agentj config" else "admin")   # their own card, their own rules
    if action in ("device.pair", "device.revoke"):
        return FACE, "self_protect"
    try:
        keep = confirm_tier.harness_keeps(tool, inp, env=env, workdir=workdir)
        floor = approval_level.harness_floor(tool, inp, verdict)
    except Exception:  # noqa: BLE001 — cannot tell → the strictest step
        return FACE, "self_protect"
    if "opaque" in cats and "cred.service" in rules:
        return FACE, "opaque"         # #469: a dispatch the host could not prove (peer_origin.unproven) — Face ID, as before
    if keep == "seat" or floor in ("hooks", "settings") or (rules & {"cred.hooks", "cred.service"}):
        return FACE, "self_protect"
    text = _text(tool, inp)
    if tool in _SHELL:
        from . import danger
        text = danger.executable_text(text)
    if tool in _SHELL and aj_state_write(text or ""):
        return FACE, "self_protect"               # r7 defence in depth: an interpreter / cd-then-write into Agent J's own dirs
    if tool in _SHELL and _GH_KEYFILE.search(text or ""):
        return FACE, "secret_read"                # r6 R3: `gh api -F/-f x=@<key-named file>` posts a key file
    if tool in _SHELL and _REMOTE_DELETE.search(text or ""):
        return FACE, "delete_permanent"           # r6 R3: a remote repo / zone deleted (provisional Face ID row)
    if floor == "credentials" or shows_secret(text):
        return FACE, "secret_out"
    if floor == "injection":
        return TAP, "fetch_run"
    if keep == "secret_out":
        return FACE, "secret_out"                 # r5: no key-use exception (removed)
    if keep == "spend" or cats & {"spend", "price"} or action == "payment":
        from . import budget as budget_mod
        return (NONE, "spend_in_budget") if budget_mod.verdict(budget, amount) == "in" else (FACE, "spend_over_budget")
    if "credentials" in cats or _shared_cred(tool, inp, env, workdir) or (tool in _SHELL and bool(text) and confirm_tier._names_cred(text)):
        return FACE, "secret_read"                # CEO r4: reading / rewriting / copying a key file — Face ID (provisional)
    rc_path = " " + str(inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or "")
    if (tool in _SHELL and _RC_FILES.search(text or "")) or (tool not in _SHELL and _RC_FILES.search(rc_path)):
        return TAP, "other"                       # r5 N2: ~/.curlrc / ~/.wgetrc / .netrc steer every later request — not routine
    rev = None
    if cats and cats <= {"delete", "send"}:
        try:
            rev = goal.reversible(tool, inp, verdict, workdir, classify)
        except Exception:  # noqa: BLE001
            rev = None
    if keep == "delete" or "delete" in cats:
        # CEO ruling (0.26.9 release, chairman: 「删除文件」 is a reason to interrupt him): only a move to the trash is card-free;
        # every other delete — a plain `rm` in the task folder, temp / generated files, git-recoverable files, local refs —
        # raises at least one tap
        if rev == "trash":
            return NONE, "delete_reversible"
        return (FACE if _bulk_or_remote_delete(tool, inp, rules, text, workdir) else TAP), "delete_permanent"
    if "opaque" in cats:
        return TAP, "opaque"
    if "send" in cats:
        if rules & PUBLISH_RULES or action == "publish":
            return TAP, "publish"
        if rev in ("git_push", "new_remote_file"):
            return TAP, "send_reversible"         # F4: a public remote / a first push cannot be told apart — one tap
        return TAP, "send_new"
    if tool in MAIL_TOOLS or action == "mail.send":
        return (NONE, "mail_known") if known_recipients else (TAP, "send_new")
    if action == "publish":
        return TAP, "publish"
    if requirement in ("hold", "uv"):
        if action in ("secrets.read", "secrets.write"):
            return FACE, "secret_read"
        if action in ("admin", "computer.control"):
            return None, "admin"          # sudo / Computer Use / admin tools: their existing button
        return None, "other"
    return NONE, "routine"


def _bulk_or_remote_delete(tool, inp, rules, text, workdir) -> bool:
    """CEO r4: a remote `git push --force / --delete`, or a recursive / wildcard / multi-target delete reaching outside the
    task folder → Face ID; a single file outside it, or anything inside → one tap."""
    import os
    from . import goal
    if "delete.git_push" in rules or re.search(r"\bgit\b[^;&|\n]*\bpush\b[^;&|\n]*(?:\s--delete\b|\s-d\b|\s:\S)", text or ""):
        return True
    try:
        t = goal.delete_targets(tool, inp, workdir)
    except Exception:  # noqa: BLE001
        t = None
    if t is None:
        return False
    targets = t[0]
    wd = os.path.realpath(workdir).rstrip("/") + "/" if workdir else None
    outside = [p for p in targets if not wd or not (os.path.realpath(p) + "/").startswith(wd)]
    bulk = len(targets) > 1 or "*" in (text or "") or bool(re.search(r"\s-[A-Za-z]*[rR]|--recursive", text or ""))
    return bool(outside) and bulk


def _shared_cred(tool, inp, env=None, workdir=None) -> bool:
    """The strict shared rule sees a credential file / environment read (what makes `cat ~/.env` a one-button card)."""
    from . import danger
    try:
        return "credentials" in danger.classify_shared(tool, inp, cwd=workdir, env=env).cats
    except Exception:  # noqa: BLE001
        return False


def card_row(tool, verdict, requirement=None, require_passkey=False, tool_input=None) -> str:
    """The class a card shows when the table did not pick its row (other levels, the host's own cards)."""
    from . import approval_level, policy_registry
    cats = set(getattr(verdict, "cats", []) or [])
    rules = set(getattr(verdict, "rules", []) or [])
    action = policy_registry.tool_action(tool)
    if tool in approval_level.HOST_CARDS or tool in approval_level.HARNESS_KEEPS:
        return "own_config" if tool == "agentj config" else "admin"
    if "opaque" in cats and "cred.service" in rules:
        return "opaque"
    if rules & {"cred.hooks", "cred.service"} or action in ("device.pair", "device.revoke"):
        return "self_protect"
    if cats & {"spend", "price"} or action == "payment":
        return "spend_over_budget"
    if "delete" in cats:
        return "delete_permanent"
    if "credentials" in cats:
        return "secret_out" if require_passkey else "secret_read"
    if "opaque" in cats:
        return "opaque"
    if "send" in cats or action == "mail.send":
        return "publish" if rules & PUBLISH_RULES or action == "publish" else "send_new"
    if action in ("secrets.read", "secrets.write") or (isinstance(tool_input, dict) and _shared_cred(tool, tool_input)):
        return "secret_read"
    if action in ("admin", "computer.control"):
        return "admin"
    return "other"


# the web's card classes (lane w3-cards, PROTOCOL §42.1): `cls` + `why_scope`; every other row reads as `irreversible`
WEB_CLS = {"spend_over_budget": "spend_over_budget", "secret_out": "secret_reveal", "send_new": "new_recipient",
           "publish": "public_publish", "delete_permanent": "permanent_delete"}


def card_fields(row: str, lang) -> dict:
    """The phone's card class fields: `cls` + `why_scope` (what the page renders) and the finer `policy_class` (row id),
    `policy_name`, `policy_provisional`."""
    row = row if row in ROWS else "other"
    r = ROWS[row]
    en = str(lang or "").lower().startswith("en")
    return {"cls": WEB_CLS.get(row, "irreversible"), "why_scope": (r[5] if en else r[4])[:300], "policy_class": row,
            "policy_name": r[3] if en else r[2], "policy_provisional": bool(r[1])}


def table(lang="zh") -> list:
    """For docs, tests and the ADR: every row with its tier and whether it waits for the chairman's signature."""
    return [{"class": k, "tier": v[0], "provisional": v[1], **card_fields(k, lang)} for k, v in ROWS.items()]
