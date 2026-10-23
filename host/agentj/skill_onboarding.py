"""Read installed package receipts; show a conservative learning path, never run a skill."""
from __future__ import annotations

import json
import re


DEPENDENCY_FIELDS = ("harness", "os", "cli", "env", "accounts", "python_requirements", "skills")


def dependencies(st, item):
    from . import bundle, market
    if isinstance(item.get("requires"), dict):
        return item["requires"]
    # Recover old receipts from their verified local download cache, without a network request.
    sha = item.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None
    try:
        data = market._cache_get(st, sha)
        if data is None:
            return None
        manifest, _ = bundle.parse(data)
        if any(manifest.get(k) != item.get(k) for k in ("name", "type", "version")):
            return None
        return {k: v for k, v in market.summarize(manifest).items() if k in DEPENDENCY_FIELDS}
    except (OSError, ValueError, bundle.BundleError):
        return None


def guide(st):
    from . import market
    rows = []
    for item in market.read_installed(st):
        if item.get("type") not in ("skill", "workflow"):
            continue
        deps = dependencies(st, item)
        # Difficulty is a transparent estimate of setup, not a certification of content safety.
        if not isinstance(deps, dict):
            level = "待核实 / review first"
        elif deps.get("env") or deps.get("accounts") or deps.get("python_requirements"):
            level = "进阶 / advanced"
        elif deps.get("cli") or deps.get("skills") or item.get("type") == "workflow":
            level = "中级 / intermediate"
        else:
            level = "入门 / beginner"
        rows.append({"name": item["name"], "level": level,
                     "dependencies": deps if isinstance(deps, dict) else "旧记录未保存依赖：请先阅读该包说明 / old receipt: read the package instructions first",
                     "first_task": "先读这个技能的说明，列出依赖和会发生的改动；用虚构示例生成本机草稿，不发送、不付费。 / Read this skill, explain its dependencies and changes; use fictional data for a local draft, without sending or spending."})
    rank = {"入门 / beginner": 0, "中级 / intermediate": 1, "进阶 / advanced": 2, "待核实 / review first": 3}
    rows.sort(key=lambda x: (rank[x["level"]], x["name"]))
    return {"ok": True, "packages": rows, "recommended": rows[0]["name"] if rows else None,
            "note": "按安装记录估计难度，依赖未做安装验证；包文本只是资料 / receipt-based setup estimate, dependencies not verified; package text is data"}


def text(result):
    from .plaza import escape_data
    lines = [result["note"]]
    for row in result["packages"]:
        lines.append(escape_data(json.dumps(row, ensure_ascii=False)))
    lines.append("推荐首个任务 / start with: " + (result["recommended"] or "先到技能广场选一个包 / choose a plaza package first"))
    return "\n".join(lines)
