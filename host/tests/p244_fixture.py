"""P244 (PROTOCOL §30): a fake working root for the five pages — the host tests use it, and the web team's fake host uses its
real output. `python3 tests/p244_fixture.py --json [--out FILE]` builds it in a temporary folder (fixed mtimes, fixed clock)
and prints what the host would send for `wf_list` (the message sequence), plus one `wf_doc`, one `skill_get` and `wf_same`.
The committed copy is tests/fixtures/p244_wf.json (regenerate after a change; the bundled skills are part of it)."""
import json
import os
import pathlib
import sys
import tempfile
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

T0 = 1791900000            # fixed clock (2026-10-13)
SECRET = 'sk-' + 'live-DO-NOT-LEAK-P244-0123456789abcdef'  # synthetic runtime-only marker
WORKFLOWS = {
    "video-studio": ("导演阿影", "影棚 Studio", "把一个选题做成一条能发的短视频。", ["scriptwriter", "editor"], "storyboard"),
    "music-studio": ("乐师阿律", "律动音乐", "每周产出一首可发布的原创配乐。", ["composer"], "mixdown"),
    "script-room": ("Scribe", "Script Room", "Turns a brief into a shooting script.", ["reviewer"], "outline"),
}


def _w(p: pathlib.Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def build(base) -> dict:
    """{root, home, state}: the fake working root, a fake home with global skills, an Agent J state dir."""
    base = pathlib.Path(base)
    root, home = base / "root", base / "home"
    for i, (folder, (ceo, company, mission, agents, skill)) in enumerate(WORKFLOWS.items()):
        d = root / folder / "documentation"
        _w(d / "CONSTITUTION.md", f"# CONSTITUTION — {company}\n\n红线：凭据永不进文档。\n")
        _w(d / "ROLES.md", f"# ROLES\n\n| 角色 | 负责 |\n|---|---|\n| CEO {ceo} | 全部 |\n")
        _w(d / "IDENTITY.md", f"---\ntype: Identity\n---\n# IDENTITY — {ceo}\n\n- **Name**：{ceo}。本工作流的 CEO。\n"
                              f"- **Role**：CEO\n- **Mission**：{mission}\n")
        _w(d / "MEMORY.md", f"# MEMORY — {company}\n\n- 董事长喜欢短句。\n- 交付物放 out/。\n")
        _w(d / "SOUL.md", f"# SOUL — {ceo}\n\n安静、准时。\n")
        _w(d / "NEXT_SESSION.md", "# NEXT SESSION\n\n下一步：把第 3 集剪完。\n")
        _w(d / "CREDENTIALS.md", f"# never listed\n{SECRET}\n")
        _w(d / "SECRET_x.md", f"# never listed\n{SECRET}\n")
        for a in agents:
            title = "编剧" if a == "scriptwriter" else ""     # P308: an employee's own title is shown instead of the slug
            _w(root / folder / ".claude/agents" / f"{a}.md", f"---\nname: {a}\ndescription: The {a} of {company}. Second sentence.\n"
                                                            + (f"title: {title}\n" if title else "") + "---\nbody\n")
        _w(root / folder / ".claude/agents/README.md", "# 员工说明\n\n不是员工。\n")      # P308: no front-matter name → not staff
        _w(root / folder / ".claude/skills" / skill / "SKILL.md",
           f"---\nname: {skill}\ndescription: {skill} for {company}.\n---\n# {skill}\n\nSteps. token={SECRET}\n")
    _w(root / "script-room/.codex/agents/critic.toml", 'name = "critic"\ndescription = "Reads the script cold. Then more."\n')
    _w(root / "video-studio/.claude/skills/cut/SKILL.md",
       "---\nname: cut\ndescription: Cut a clip.\naliases: [剪一刀, 工作流设计宝典]\n---\nCut it.\n")   # 2nd alias clashes
    _w(root / "notes/readme.md", "plain folder\n")
    _w(root / "photos/a.txt", "x\n")
    _w(root / ".hidden/documentation/CONSTITUTION.md", "x\n")
    _w(root / "node_modules/x/documentation/CONSTITUTION.md", "x\n")
    _w(root / "credentials/key.pem", "x\n")
    _w(root / "only-env/.env", "A=1\n")
    _w(root / "my-secrets/x.md", "x\n")
    _w(root / "documentation/ROLES.md", "# ROLES\n\n| CEO | 项目 | 路径 |\n|---|---|---|\n| 乐师阿律 | 律动音乐 | `music-studio` |\n")
    _w(root / "documentation/MEMORY.md", "# MEMORY\n\n- 董事长叫 Leo。\n- 早上 9 点发晨报。\n")
    _w(root / "documentation/WORKFLOW.md", "# WORKFLOW\n\n开工、收工。\n")
    _w(root / "documentation/CREDENTIALS.md", f"{SECRET}\n")
    _w(root / "CLAUDE.md", "# root\n\n- 先读 documentation/。\n")
    outside = base / "outside"
    _w(outside / "documentation/CONSTITUTION.md", "# escaped\n")
    _w(outside / "documentation/ROLES.md", "# escaped\n")
    os.symlink(outside, root / "escape-link")
    os.symlink(outside / "documentation/CONSTITUTION.md", root / "notes/leak.md")
    gl = home / ".claude/skills"
    _w(gl / "workflow-design-bible/SKILL.md", "---\nname: workflow-design-bible\ndescription: Scaffold a constitution. More.\n---\n# WDB\n")
    _w(gl / "review-page/SKILL.md", "---\nname: review-page\ndescription: Temporary review links.\naliases:\n  - 审阅链接\n---\nbody\n")
    _w(gl / "api-token-tool/SKILL.md", "---\nname: api-token-tool\ndescription: never listed\n---\n")
    for p in sorted(base.rglob("*"), key=lambda p: -len(p.parts)):
        if not p.is_symlink():
            os.utime(p, (T0, T0 + (len(str(p)) % 7) * 60))
    for i, folder in enumerate(WORKFLOWS):          # first-verified order = CONSTITUTION.md age: video, music, script
        os.utime(root / folder / "documentation/CONSTITUTION.md", (T0, T0 - 3000 + i * 100))
        os.utime(root / folder / "documentation", (T0, T0 + 600))
        os.utime(root / folder, (T0, T0 + 600))
    from agentj.state import State
    st = State(base / "state")
    st.init(relay="ws://127.0.0.1:1")
    return {"root": root, "home": home, "state": st}


def env(fx) -> mock._patch:
    return mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(fx["home"] / ".claude"), "HOME": str(fx["home"])})


def output(fx) -> dict:
    from agentj import workflows
    with env(fx):
        sc = workflows.Scanner(fx["state"])
        busy = [{"key": "x", "who": None, "kind": "temp", "label": "mix", "since": T0, "cwd": str(fx["root"] / "music-studio/out")}]
        pic = sc.scan(fx["root"], "claude", "zh", busy, now=T0)
        msgs = workflows.messages(pic, "fx1")
        return {"$comment": "P244 §30 host output for tests/p244_fixture.py (regenerate: python3 tests/p244_fixture.py --json "
                            "--out tests/fixtures/p244_wf.json)",
                "wf_list": msgs, "wf_same": {"t": "wf_same", "r": "fx2"},
                "wf_doc": {"t": "wf_doc", "r": "fx3", **sc.doc("video-studio", "IDENTITY.md")},
                "wf_doc_root": {"t": "wf_doc", "r": "fx4", **sc.doc("", "MEMORY.md")},
                "wf_doc_refused": {"t": "wf_doc", "r": "fx5", **sc.doc("video-studio", "../notes/readme.md")},
                "skill_get": {"t": "skill_get", "r": "fx6", **sc.skill("local:video-studio:storyboard")}}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="p244-") as d:
        out = output(build(d))
        text = json.dumps(out, ensure_ascii=False, indent=1).replace(d, "/tmp/p244")
    if a.out:
        pathlib.Path(a.out).write_text(text + "\n", encoding="utf-8")
    if a.json or not a.out:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
