"""Model presets (AGENTJ-CORE-SPEC 2.3): which provider / model a session uses, read from presets.json.

A model is named `<provider>/<model>` (`ark/doubao-seed-2.1-pro`); a bare `<provider>` takes that provider's role model.
The key is looked up by its NAME in the environment the caller passes (the service environment) and is never returned
anywhere but to the HTTP client (llm.py). Standard library only.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROLES = ("main", "ceo", "sub", "tiny")


class PresetError(ValueError):
    """code: unknown_provider | unknown_model | no_key."""


@lru_cache(maxsize=1)
def data() -> dict:
    return json.loads(Path(__file__).with_name("presets.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Preset:
    provider: str
    model: str
    base_url: str
    key_env: str
    protocol: str = "openai-chat"
    tier: str = "large"
    context: int = 131072
    edit_mode: str = "str_replace"
    plan: bool = False
    replay_reasoning: bool = False
    fallback: tuple = field(default_factory=tuple)

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model}"

    def key(self, environ=None) -> str | None:
        v = (os.environ if environ is None else environ).get(self.key_env, "")
        return v or None


def providers() -> dict:
    return data()["providers"]


def resolve(spec: str | None, role: str = "main", environ=None) -> Preset:
    """`provider/model`, `provider`, or None (the first provider in the recommended order whose key is set)."""
    env = os.environ if environ is None else environ
    ps = providers()
    if not spec:
        for name in data()["order"]:
            if env.get(ps[name]["key_env"]):
                spec = name
                break
        else:
            raise PresetError("no_key")
    prov, _, model = spec.partition("/")
    p = ps.get(prov)
    if p is None:
        raise PresetError("unknown_provider")
    model = model or p["roles"].get(role if role in ROLES else "main") or next(iter(p["models"]))
    m = p["models"].get(model)
    if m is None:
        raise PresetError("unknown_model")
    return Preset(provider=prov, model=model, base_url=p["base_url"], key_env=p["key_env"], protocol=p["protocol"],
                  tier=m.get("tier", "large"), context=int(m.get("context", 131072)), edit_mode=m.get("edit_mode", "str_replace"),
                  plan=bool(p.get("plan")), replay_reasoning=bool(m.get("replay_reasoning")),
                  fallback=tuple(x for x in p.get("fallback", ()) if x != model and x in p["models"]))


def for_role(base: Preset, role: str) -> Preset:
    """The same provider's model for another role (a sub-agent runs the provider's `sub` model)."""
    return resolve(f"{base.provider}/{providers()[base.provider]['roles'].get(role, base.model)}", role)


def sibling(base: Preset, model: str) -> Preset:
    return resolve(f"{base.provider}/{model}")


def listing(environ=None) -> list[dict]:
    """[{name, label, key_env, has_key, verified, models}] — names only, never a value."""
    env = os.environ if environ is None else environ
    out = []
    for name in data()["order"]:
        p = providers()[name]
        out.append({"name": name, "label": p["label"], "key_env": p["key_env"], "has_key": bool(env.get(p["key_env"])),
                    "verified": bool(p.get("verified")), "plan": bool(p.get("plan")), "models": list(p["models"])})
    return out


def record(environ=None) -> dict:
    """harness.detect's row for Agent J Harness: built in (no binary); logged_in = some preset's key NAME is set."""
    from . import LABEL
    keys = [p for p in listing(environ) if p["has_key"]]
    return {"name": "core", "installed": True, "logged_in": bool(keys), "supported": True, "version": "built-in",
            "executable": None, "resolution": {"path": None, "reason": "built_in"}, "installations": [],
            "note": f"{LABEL} (preview): " + (f"model keys set: {', '.join(p['name'] for p in keys)}" if keys else
                                              "no model key yet (Volcano Ark ARK_API_KEY first)")}
