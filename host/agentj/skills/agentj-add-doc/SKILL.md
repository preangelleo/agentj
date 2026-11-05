---
name: agentj-add-doc
description: Add a document to Agent J's documentation — the phone's + on the 「Agent J」 page puts `/agentj-add-doc ` in the message box and the owner says what the document is about (「/agentj-add-doc 家里网络的设置说明」, "write a doc about …", 「加一份文档…」「把这个写成说明」). Writes one Markdown file in the working root's documentation/ (the Agent J page lists it), then says so in one line.
---

主 Agent 是董事长助理：只派常驻 CEO 或临时子 Agent、写验收、跑验收命令、汇报；除立等可取的一句话小事，绝不亲自干活，主会话随时接主人消息。
Main Agent = chief of staff: dispatch to a resident CEO or temporary sub-Agent, write and run acceptance checks, report. Only immediate one-sentence tasks are yours; keep the main session ready for owner messages.

The phone's 「Agent J」 page lists the working root's `documentation/*.md` (beside Agent J's own core document, which
nobody edits). Everything after `/agentj-add-doc` says what the document is about. Nothing after it → ask in one line.

1. Unclear what goes in it → ask at most two short questions in one message (what is it for, what must it contain);
   stop until the owner answers. Clear enough → go on.
2. Pick a short file name: upper-case words with `_`, `.md` (e.g. `HOME_NETWORK.md`); never a name that looks like a
   credential (key, token, secret, password, .env). Never touch the boot documents (CONSTITUTION, IDENTITY, SOUL,
   WORKFLOW, ROLES, NEXT_SESSION, MEMORY, STRUCTURE). Exists already → ask the owner before changing it.
3. Write it: `# <title>` first, then what the owner asked for, plain Markdown, no keys, passwords or codes (point to
   where they live instead). Write `<name>.md.tmp`, then rename it into `documentation/`.
4. Answer in one line: written, and `doc/<name>.md` (the phone links it; it shows on the Agent J page).
