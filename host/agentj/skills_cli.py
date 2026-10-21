"""P426 #181-3 / #181-4: `agentj skills health | archive | restore | suggest` — skill health and the "save it as a skill"
suggestions (skill_usage.py, skill_ops.py; PROTOCOL §39). The ledgers live in the state directory, outside the Agent fence:
the CLI asks serve on elevate.sock (`{"t":"skills","op",…}`) and only without a running serve reads / writes them itself."""
from __future__ import annotations

import json
import time

from . import skill_ops, skill_usage

OPS = ("health", "archive", "restore", "suggest", "suggest_yes", "suggest_no")
WHY = {
    "official": ("Agent J 自带 / 官方技能不建议归档，也不能在这里归档", "Agent J's own / official skills are not archived"),
    "not_found": ("没找到这个技能（或它不在 harness 的用户技能文件夹里）", "no such skill (or it is not in the harness's user skills folder)"),
    "exists": ("原来的位置已经有同名技能，先改名或删掉那个再恢复", "a skill with that name is back in its place; rename or remove it first"),
    "shape": ("名字不对", "bad name"),
    "link": ("技能文件夹或归档文件夹是符号链接（或刚被换成链接），为安全不移动", "a link in the way (skills or archive folder): not moved"),
    "protected": ("这个技能文件夹在 Agent J 的受保护目录里或包着它，不移动", "the folder is inside / around a protected directory: not moved"),
    "fenced": ("Agent J 没在运行，Agent 里读不到技能账本（agentj service status）",
               "Agent J is not running and the Agent cannot read the ledger (agentj service status)"),
}


def execute(st, req: dict, cfg: dict | None = None, lang: str = "zh", prefs=None) -> dict:
    """One fixed operation (serve's elevate.sock handler, or the CLI without serve). Never raises for a bad request."""
    cfg = cfg if isinstance(cfg, dict) else {}
    kind = cfg.get("kind") if isinstance(cfg.get("kind"), str) else None
    op = req.get("op") if isinstance(req, dict) else None
    if op not in OPS:
        return {"ok": False, "why": "shape"}
    try:
        if op == "health":
            from . import main_identity
            root = main_identity.working_root(cfg) if cfg else None
            return skill_usage.health(st, kind, root, lang, prefs)
        if op in ("archive", "restore"):
            res = (skill_usage.archive if op == "archive" else skill_usage.restore)(st, req.get("name"), kind)
            st.log("skill_" + op, result="ok", by=str(req.get("by") or "cli")[:16])
            return res
        if op == "suggest":
            return {"ok": True, "on": skill_ops.enabled(prefs, st), "suggestions": skill_ops.suggestions(st, everything=bool(req.get("all")))}
        return skill_ops.answer(st, req.get("id"), op == "suggest_yes")
    except skill_usage.ArchiveError as e:
        st.log("skill_" + op, result="refused", why=e.why)
        return {"ok": False, "why": e.why}
    except PermissionError:
        return {"ok": False, "why": "fenced"}
    except OSError as e:
        return {"ok": False, "why": "os", "detail": type(e).__name__}


def _ask(req: dict) -> dict:
    from . import elevate
    from .names import elevate_sock
    from .state import State
    st = State()
    res = elevate.client_request(st, {"t": "skills", **req}, timeout=60, path=elevate_sock(st))
    if res.get("result") != "unavailable":
        return res
    try:
        cfg = st.agent_config() or {}
    except (OSError, ValueError):
        return {"ok": False, "why": "fenced"}
    from . import main_identity
    return execute(st, req, cfg, main_identity.language_of(cfg))


def _say(res: dict) -> str:
    w = WHY.get(res.get("why"), (str(res.get("why") or "失败"), str(res.get("why") or "failed")))
    return f"✗ {w[0]} / {w[1]}"


def _health_text(res: dict) -> str:
    now = time.time()
    out = [f"技能健康度 / skill health · harness {res.get('harness') or '—'} · 很久没用 = {res['days']} 天 / long unused = {res['days']} days"]
    if not res["tracked"]:
        out.append("（这个 harness 不报告技能使用，只统计手机上的 /命令；拿不到的显示 — / this harness reports no skill runs: "
                   "only / commands from a remote count; — = not known）")
    for s in res["skills"]:
        if s["official"]:
            continue
        use = "—" if s["uses"] is None else f"{skill_usage.ago(s['last'], now)} · {s['uses']} 次 / {skill_usage.ago(s['last'], now, 'en')} · {s['uses']}×"
        mark = "  ⚠ 很久没用了 / long unused" if s["stale"] else ""
        mark += "  ↪ 链接，不能归档 / a link: not archivable" if s.get("link") else ""
        out.append(f"  /{s['name']:<28} {use}{mark}")
    n = sum(1 for s in res["skills"] if s["official"])
    out.append(f"  （另有 {n} 个 Agent J 自带 / 官方技能，不建议归档 / plus {n} bundled or official skills, never archived）")
    if res["archived"]:
        out.append("已归档（agentj skills restore <名字> 恢复）/ archived (restore with agentj skills restore <name>):")
        out += [f"  /{a['name']}  {skill_usage.ago(a['at'], now)}" for a in res["archived"]]
    return "\n".join(out)


def _suggest_text(res: dict) -> str:
    if not res["suggestions"]:
        return ("现在没有提炼建议（同类事 14 天内做满 3 次才会出现）/ no suggestions now (the same task 3 times within 14 days)"
                + ("" if res.get("on", True) else "\n提议已关闭：skills.suggest = false / offers are off"))
    rows = [f"  {s['id']}  {s['kind']:<3}  ×{s['count']}  {s['state']:<5}  {s['sig']}" for s in res["suggestions"]]
    return "提炼建议 / suggestions (id · ask=主人的话 cmd=命令 · 次数 · 状态 · 归一化后的样子):\n" + "\n".join(rows)


def cmd_skills(a) -> int:
    op = a.op
    if op == "health":
        req = {"op": "health"}
    elif op in ("archive", "restore"):
        req = {"op": op, "name": a.name, "by": "cli"}
    else:
        if a.action in ("yes", "no"):
            if not a.id:
                print("✗ 要给建议的 id（agentj skills suggest 列出）/ give the suggestion id (agentj skills suggest lists them)")
                raise SystemExit(2)
            req = {"op": "suggest_" + a.action, "id": a.id}
        else:
            req = {"op": "suggest", "all": bool(a.all)}
    res = _ask(req)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    elif not res.get("ok"):
        print(_say(res))
    elif op == "health":
        print(_health_text(res))
    elif op == "archive":
        print(f"✓ 已归档 /{res['name']} → {res['to']}（随时 agentj skills restore {res['name']} 恢复）/ archived; restore any time")
    elif op == "restore":
        print(f"✓ 已恢复 /{res['name']} → {res['to']} / restored")
    elif "suggestions" in res:
        print(_suggest_text(res))
    else:
        print(f"✓ {res['id']}: {'记下了：主人同意存成技能' if res['state'] == 'yes' else '记下了：不再提这件事'} / "
              f"{'noted: the owner said yes' if res['state'] == 'yes' else 'noted: never offered again'}")
    if not res.get("ok"):
        raise SystemExit(1)
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("skills", help="技能健康度（最近使用、很久没用、归档 / 恢复）和「存成技能」建议 / skill health "
                                      "(last use, long unused, archive / restore) and save-as-skill suggestions")
    ps = p.add_subparsers(dest="op", required=True)
    h = ps.add_parser("health", help="每个技能最近一次使用、次数、是否很久没用 / each skill's last use, count, long unused")
    h.add_argument("--json", action="store_true")
    for name, text in (("archive", "把一个很久没用的技能移到归档文件夹（不删除，可恢复）/ move a skill to the archive folder (never deleted)"),
                       ("restore", "把归档的技能放回原处 / put an archived skill back")):
        x = ps.add_parser(name, help=text)
        x.add_argument("name", help="技能名 / skill name")
        x.add_argument("--json", action="store_true")
    s = ps.add_parser("suggest", help="同类事做满 3 次后的「存成技能」建议；yes / no 记下主人的回答 / save-as-skill suggestions; "
                                      "yes / no records the owner's answer")
    s.add_argument("action", nargs="?", choices=["list", "yes", "no"], default="list")
    s.add_argument("id", nargs="?", help="建议的 id / the suggestion id")
    s.add_argument("--all", action="store_true", help="也列出已回答和还在计数的 / also answered and still counting")
    s.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_skills)


__all__ = ["execute", "add_parser"]
