"""The idle heartbeat's work offer and pickup receipt. Never put task text in scheduling logs."""
import datetime as dt
import secrets

from . import jcards, jcards_todo as todo, jcards_todo_archive as archive


MISSED_LIMIT = 3


def offer(root, now):
    items, problem, _ = todo.read(root)
    ready, review = ({}, {}) if problem else todo._heartbeat_candidates(root, items, dt.datetime.fromtimestamp(now, dt.timezone.utc))
    # Active work still needs the ordinary progress review. It cannot be claimed again.
    active = any(item['owner'] == 'agent' and item['status'] == 'active' for item in items)
    return {"ready": list(ready), "review": list(review), "active": active, "beat": secrets.token_hex(8), "problem": problem}


def instructions(plan, lang):
    if not (plan["ready"] or plan["review"]):
        return ""
    ready = ", ".join(plan["ready"]) or "—"
    review = ", ".join(plan["review"]) or "—"
    command = "agentj card todo start --entry <ID> --heartbeat " + plan["beat"]
    if lang == "en":
        return (f"Ready IDs: {ready}. Check the start condition yourself: {review}. Read agentj card todo due --json. "
                f"Choose one ready, authorized task; verify any written condition first. Run {command}, work on it, "
                "verify the outcome and mark done. If blocked, record why and request the existing approval. "
                "Report the result or blocker when there is ready work. Stay silent if nothing is actionable.")
    return (f"可开工编号：{ready}。需要核实文字条件的编号：{review}。用 agentj card todo due --json 读条目。"
            f"按已有授权挑一件开工；文字条件要先核实。运行 {command}，执行并验收后标 done。"
            "受阻时记录原因，需要审批就走现有卡；有活可做要报告结果或阻塞，无可办事项才静默。")


def picked(root, plan):
    card = jcards.find(root, todo.CID)
    if not card:
        return None
    raw, problem, cut = jcards._read(root, card)
    if problem not in (None, "empty") or cut:
        return None
    _, rows, problem = todo._parse(raw)
    if problem:
        return None
    offered = set(plan["ready"]) | set(plan["review"])
    for row in rows:
        if isinstance(row, dict) and row.get("id") in offered and row.get("heartbeatPickup") == plan["beat"]:
            return row["id"]
    # Completed work may already have moved out of the live card.
    for iid in offered & archive.ids(root):
        if archive.get(root, iid).get("heartbeatPickup") == plan["beat"]:
            return iid
    return None
