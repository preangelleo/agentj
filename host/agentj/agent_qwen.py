"""P310: Qwen Code (`qwen`, Alibaba / QwenLM, Apache-2.0, 0.25+) as Agent J's fifth harness — the second implementation on
P305's generic ACP adapter (acp.py). 「百炼的 CLI」 for coding is this one: `bl` (the Bailian CLI) manages cloud resources and
writes Qwen Code's settings (`bl config agent --agent qwen-code`), it is not a coding agent itself.

Measured on 0.25.1 in an isolated HOME (reports/qa/p310/): `qwen --acp` answers `initialize` at once (agentInfo qwen-code,
loadSession, session list / resume, image prompts, one auth method "openai" = OPENAI_API_KEY); with no key `session/new` is
refused with ACP `auth_required` (-32000). New sessions start in Qwen's "auto" mode (an LLM classifier approves what it thinks
is safe) — Agent J switches every session to "default" (asks before edits and shell commands) and switches it back whenever
it changes (`current_mode_update`, or Qwen's own `qwen/notify/session/mode-update`). Tool calls carry an ACP `kind`
(execute / edit / read …) and `rawInput` with `command` / `file_path`, so acp.tool_of maps them onto the danger list.
Qwen's free OAuth tier ended on 2026-04-15: a key is the only login.

Logins (the owner's, never the Agent's):
- the owner's own Qwen settings (`~/.qwen/settings.json` with an auth type or model providers, e.g. written by `bl config
  agent`): used as they are; Agent J adds nothing.
- otherwise a key from the phone's key card: BAILIAN_TOKEN_PLAN_API_KEY (Token Plan, personal — recommended; Qwen 3.8 with
  image input), BAILIAN_CODING_PLAN_API_KEY (Coding Plan) or DASHSCOPE_API_KEY (pay as you go). Agent J hands it to Qwen as
  its OpenAI-compatible variables (OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL) for this process only — nothing is written
  to Qwen's settings — and never to scheduled tasks or bots (the Token Plan terms forbid unattended calls). With a Token Plan
  key Qwen's own scheduler is switched off (QWEN_CODE_DISABLE_CRON) for the same reason; Agent J's tasks never get the key.
- `/model` with a key: the model is the process's OPENAI_MODEL, so a switch restarts Qwen with the new one and resumes the
  same session.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from .acp import AcpAgent
from .slash import Result
from .text import clean_line

LABEL = "Qwen Code"
MIN_VERSION = (0, 25, 0)
KEYS = (  # (key variable, OpenAI-compatible base, default model, models offered) — first one set wins
    ("BAILIAN_TOKEN_PLAN_API_KEY", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "qwen3.8-max",
     ("qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus", "qwen3.6-flash", "qwen3.7-max", "glm-5.3", "deepseek-v4-pro")),
    ("BAILIAN_CODING_PLAN_API_KEY", "https://coding.dashscope.aliyuncs.com/v1", "qwen3.7-plus",
     ("qwen3.7-plus", "qwen3.6-plus", "qwen3.5-plus", "kimi-k2.5", "glm-5")),
    ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.7-plus",
     ("qwen3.7-plus", "qwen3.7-max", "qwen3.6-plus")),
)
KEY_NAMES = tuple(k[0] for k in KEYS)
VISION = ("qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus", "qwen3.6-flash", "qwen3.6-plus", "qwen3.5-plus", "kimi-k2.5")


def home(environ=None) -> Path:
    env = os.environ if environ is None else environ
    h = env.get("QWEN_HOME")
    return Path(h).expanduser() if h else Path(env.get("HOME") or os.path.expanduser("~")) / ".qwen"


def settings_facts(environ=None) -> dict:
    """What ~/.qwen/settings.json says that matters here — never a value: {"own_auth": bool, "providers": int,
    "mode": str | None (tools.approvalMode), "allow_rules": int (permissions.allow entries)}."""
    try:
        p = home(environ) / "settings.json"
        d = json.loads(p.read_text(encoding="utf-8")[:500_000]) if p.is_file() and not p.is_symlink() else {}
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    sec = d.get("security") if isinstance(d.get("security"), dict) else {}
    auth = sec.get("auth") if isinstance(sec.get("auth"), dict) else {}
    prov = d.get("modelProviders") if isinstance(d.get("modelProviders"), dict) else {}
    tools = d.get("tools") if isinstance(d.get("tools"), dict) else {}
    perm = d.get("permissions") if isinstance(d.get("permissions"), dict) else {}
    mode = tools.get("approvalMode")
    return {"own_auth": bool(auth.get("selectedType")) or bool(prov),
            "providers": sum(len(v) for v in prov.values() if isinstance(v, list)),
            "mode": mode if isinstance(mode, str) and len(mode) <= 16 else None,
            "allow_rules": len(perm["allow"]) if isinstance(perm.get("allow"), list) else 0}


def version_ok(version: str | None) -> bool:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", version or "")
    return bool(m) and tuple(int(x) for x in m.groups()) >= MIN_VERSION


def fresh_keys(base: dict) -> dict:
    """The key names from the service env file as it is NOW (a key card saved after serve started), else `base`."""
    out = {k: base[k] for k in KEY_NAMES if base.get(k)}
    try:
        from . import service
        lines = Path(service.env_file(service.name())).read_text().splitlines()
    except Exception:  # noqa: BLE001 — no service: what serve's own environment has
        return out
    for line in lines:
        k, sep, v = line.strip().partition("=")
        if sep and k in KEY_NAMES:
            try:
                parts = shlex.split(v, comments=False)
            except ValueError:
                continue
            if len(parts) == 1 and parts[0]:
                out[k] = parts[0]
    return out


def key_choice(env: dict, source: dict | None = None) -> tuple | None:
    """The KEYS row whose key is set (Token Plan first), or None — None too when Qwen has the owner's own settings."""
    if settings_facts(env)["own_auth"]:
        return None
    keys = fresh_keys(source if source is not None else os.environ)
    return next(((row, keys[row[0]]) for row in KEYS if keys.get(row[0])), None)


def key_environment(env: dict, model: str | None, source: dict | None = None) -> dict:
    """Qwen's OpenAI-compatible variables for a key-card key (never logged); {} when the owner's settings log Qwen in."""
    got = key_choice(env, source)
    if got is None:
        return {}
    (name, base, default, models), key = got
    out = {"OPENAI_API_KEY": key, "OPENAI_BASE_URL": base, "OPENAI_MODEL": model if model in models else default}
    if name == "BAILIAN_TOKEN_PLAN_API_KEY":
        out["QWEN_CODE_DISABLE_CRON"] = "1"      # Token Plan: no unattended calls (its terms) — Agent J's tasks never get it
    return out


def login(environ=None) -> tuple[str, str]:
    """(ok | env | warn, where) — existence hints only, like the other harnesses; key validity is not checked here."""
    from .service import tilde
    env = os.environ if environ is None else environ
    if settings_facts(env)["own_auth"]:
        return "ok", f"settings {tilde(str(home(env) / 'settings.json'))} (key validity unverified)"
    keys = fresh_keys(env)
    for k in KEY_NAMES:
        if keys.get(k):
            return "env", f"env {k} (name only; Agent J hands it to Qwen Code)"
    return "warn", "no key (key card: BAILIAN_TOKEN_PLAN_API_KEY)"


def login_text(lang: str = "zh") -> str:
    if lang == "en":
        return ("Qwen Code has no model key (its free sign-in ended on 2026-04-15). Recommended: an Alibaba Cloud Bailian Token "
                "Plan (personal) — `agentj agent route` shows prices and terms; the Agent sends a phone key card "
                "(`agentj secret request --name BAILIAN_TOKEN_PLAN_API_KEY …`). Never paste the key into chat.")
    return ("Qwen Code 没有模型 key（它的免费登录 2026-04-15 已停）。推荐阿里百炼 Token Plan 个人版（价格和条款：`agentj agent route`）；"
            "让 Agent 发手机密钥卡（`agentj secret request --name BAILIAN_TOKEN_PLAN_API_KEY …`），不要把 key 发在对话里。")


class QwenAgent(AcpAgent):
    """`qwen --acp`, one session per conversation, approvals to the phone."""
    kind = "qwen"
    LABEL = LABEL
    BIN_ENV, BIN_NAME = "AGENTJ_QWEN_BIN", "qwen"
    MODE_DEFAULT = "default"                  # Qwen: asks before file edits and shell commands
    MODES_LOOSER = ("auto", "auto-edit", "yolo")

    def argv(self) -> list[str]:
        from . import agent as _agentmod
        return [_agentmod._bin(self.BIN_ENV, self.BIN_NAME) or self.BIN_NAME, "--acp"]

    def keyed(self) -> tuple | None:
        return None if self.cfg.get("_workflow_ceo") else key_choice(dict(os.environ))

    def child_env(self, env: dict) -> dict:
        env = dict(env)
        if not self.cfg.get("_workflow_ceo"):            # tasks use native settings only (fence.auxiliary_environment)
            env.update(key_environment(env, self.cfg.get("model")))
        return env

    def login_hint(self) -> str:
        return login_text(getattr(self.host, "lang", "zh"))

    def _on_note(self, method: str, p: dict) -> None:
        if method == "qwen/notify/session/mode-update" and p.get("sessionId") == self.sid:
            mode = p.get("currentModeId")
            self.session.setdefault("modes", {})["currentModeId"] = mode
            if isinstance(mode, str) and mode in self.MODES_LOOSER:   # never keep a mode that approves by itself
                self.host.st.log("agent_mode_reset", agent=self.kind, mode=clean_line(mode, 20))
                self._bg(self._strict_mode())
            return
        super()._on_note(method, p)

    # ---- the model with a key-card key: OPENAI_MODEL of the process (Qwen lists only that one)
    def _key_models(self) -> list[str]:
        k = self.keyed()
        return list(k[0][3]) if k else []

    def cur_model(self) -> str | None:
        k = self.keyed()
        if k:
            m = self.cfg.get("model")
            return m if m in k[0][3] else k[0][2]
        return super().cur_model()

    def refresh_models_now(self) -> None:
        names = self._key_models()
        if not names:
            return super().refresh_models_now()
        self.models_cache = [{"id": m, "name": m + (" · 看图" if m in VISION else ""), "efforts": None, "default": False}
                             for m in names]
        fn = getattr(self.host, "models_changed", None)
        if fn:
            fn()

    async def _apply_choice(self) -> None:
        if not self._key_models():
            await super()._apply_choice()

    async def _restart_for_model(self) -> None:
        p = self.proc
        if p is not None and self.prompt_fut is None:     # idle: the next message starts Qwen with the new OPENAI_MODEL
            await self._kill(p)

    async def apply_model(self, model: str | None, effort: str | None, default: bool = False) -> str | None:
        names = self._key_models()
        if not names:
            return await super().apply_model(model, effort, default)
        if model is not None and model not in names:
            return "unsupported"
        why = await super(AcpAgent, self).apply_model(model, effort, default)
        await self._restart_for_model()
        return why

    async def cmd_model(self, arg: str) -> Result:
        names = self._key_models()
        if not names:
            return await super().cmd_model(arg)
        cur = self.cur_model() or ""
        if not arg:
            return Result(f"当前模型：{cur}。点一个切换（之后的对话都用它；带「看图」的能读截图）：", "ok",
                          models=[{"id": m, "name": m, "desc": "看图 / reads images" if m in VISION else "", "cur": m == cur}
                                  for m in names])
        if arg not in names:
            return Result(f"没有这个模型：{clean_line(arg, 100)}", "error")
        self.host.set_model(arg)
        self.meter(model=arg, model_name=arg)
        await self._restart_for_model()
        return Result(f"已切换到 {arg}：下一条消息起生效（Qwen Code 换模型要重开进程，接着原对话），写进了 config.json。")


def probe(exe: str, env: dict, timeout: float = 60, model: str | None = None) -> str:
    """The switch gate: one tool-free greeting over ACP in plan mode, every permission refused (agent_kimi.probe's loop,
    `qwen --acp`, the key-card key as Qwen's OpenAI variables). → the reply text, or agent_kimi.ProbeError(code)."""
    from . import agent_kimi
    env = dict(env)
    keyed = key_environment(env, model, source=env)
    env.update(keyed)
    return agent_kimi.probe(exe, env, timeout, None if keyed else model, argv=[exe, "--acp"], key_env=False)
