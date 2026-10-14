"""P253: owner slash intent for every harness; never submit from the host itself."""
import re

NAME = "feedback-to-agentj-father"

def agent_text(text, lang="zh"):
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"/feedback-to-agentj-father(?:\s+(.*))?", text.strip(), re.S | re.I)
    if not m:
        return None
    body = (m.group(1) or "").strip()
    if lang == "en":
        head = "[Owner command /feedback-to-agentj-father] Read the bundled feedback-to-agentj-father skill. "
        return head + (("The owner explicitly requests feedback: redact it and necessary context, submit with agentj support feedback --attach-doctor --wait 0 without a second confirmation, then report the ticket ID. Owner text:\n" + body) if body else "No content provided: ask what they want to report; do not send anything yet.")
    head = "〔主人命令 /feedback-to-agentj-father〕先读随包 feedback-to-agentj-father 技能。"
    return head + (("主人明确下令反馈：自行脱敏正文与必要上下文，用 agentj support feedback --attach-doctor --wait 0 直接提交，不再二次确认，回已提交和编号。主人原话：\n" + body) if body else "没有附带内容：先问主人想反馈什么，现在不要发送。")
