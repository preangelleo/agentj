---
name: agentj-add-note
description: Add a note to the owner's Notebook card (the official Agent J card 「记事本」) — the phone's + on that card puts `/agentj-add-note ` in the message box and the owner says what to note (「/agentj-add-note 周五前给设计师回邮件」, "add a note: …", 「记一下…」「帮我记一条」). Writes one dated note at the top of the notebook's data file, atomically, then says so in one line.
---

The Notebook is an Agent J card (id `notes`, template `notes`, one Markdown file on this computer). Everything after
`/agentj-add-note` is the note — the owner's own words. Nothing after it → ask in one short line what to note, then stop.

A key, password, card number or code is never noted — say so in one line and suggest `agentj secret request`.

1. `agentj card list --json` → the `notes` row: `on` and its `data` path. Off → ask the owner whether to switch it on
   (Settings → Cards, or `agentj card on notes` once they agree); stop until they answer.
2. Read the file (missing or empty is fine). Put the note **at the top**, under the first heading if the file starts with
   one: `- <YYYY-MM-DD> <the note>` (today's date, the owner's words, nothing added; a long note stays one item).
   Do not reorder, merge or tidy anything else — that is the card's 「帮我整理」 button.
3. Write atomically: write `<file>.tmp` in the same folder, then rename it over the file.
4. Answer in one line: noted, and `card/notes` (the phone links it).
