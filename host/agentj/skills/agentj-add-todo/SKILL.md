---
name: agentj-add-todo
description: Add an item to the owner's To-do card (the official Agent J card 「待办」) — the phone's + on that card puts `/agentj-add-todo ` in the message box and the owner says what to add (「/agentj-add-todo 周三交房租」, "add to my to-do: …", 「加个待办…」「提醒我要…」 as a list item). Appends one open item to the list's JSON file, atomically, then says so in one line.
---

The To-do list is an Agent J card (id `todo`, template `checklist`, one JSON file `{"items":[{"id","text","done"}]}` on
this computer). Everything after `/agentj-add-todo` is the item. Nothing after it → ask in one short line what to add.
Several things in one message → one item each. A key, password, card number or code is never an item — say so in one
line and suggest `agentj secret request`.

1. `agentj card list --json` → the `todo` row: `on` and its `data` path. Off → ask the owner whether to switch it on
   (Settings → Cards, or `agentj card on todo` once they agree); stop until they answer.
2. Read the file (missing → `{"items": []}`). Add `{"id": "t<next number>", "text": "<the owner's words>", "done": false}`
   — a new id no other item has (letters / digits / - only), text one line ≤ 300 characters. Keep every other item, its
   id and order as they are; at most 200 items (full → tell the owner, add nothing).
3. Write atomically: write `<file>.tmp` in the same folder, then rename it over the file; it must stay valid JSON.
4. Answer in one line: added, and `card/todo#<the new id>` (the phone links straight to the row).
