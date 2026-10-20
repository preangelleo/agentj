<!-- generated from https://agentj.app/docs/cards/en.md by agentjarvis/tools/manual_skill.mjs — do not edit -->
# Using cards

## Open and switch cards {#open}

Tap the top-left logo to unfold the stack, then pick a card. Chat, Workflows, Skills, Memory and Agent J are the first five pages. Enabled optional cards and your own cards follow them.

Open Settings → Agent features → Cards to switch cards on or off. Notebook, To-do and Automation come with Agent J and start on; switch off any you do not want. A card you never switched off shows after an upgrade too. The first five system pages cannot be switched off. Switching a card off hides its entry while keeping its data. Upgrades keep that data too.

## Notebook: one entry per card {#notebook}

Keep useful addresses, phone numbers, meeting places and memos here. Each entry has its own small card with copy, edit and delete icons at the top right.

- Copy puts that entry on the clipboard.
- Edit opens the text for changes. Save when finished; cancel or press Escape to leave.
- Delete asks before removing that entry.
- The page's + and Add a note both return to the composer with `/agentj-add-note`. Add your words and send. After saving, the Agent opens the Notebook at the newest entry.
- Tidy it up asks the notebook's management skill to organize it. Review the confirmation before sending; you can also ask for a different organization in chat.

To find an entry, tap the magnifier at the top right and type a word. Only the entries containing it stay, with the word highlighted; press Esc to see them all again. To-do and Automation cards work the same way.

Existing notebooks still use the same notes.md on the Agent J host. Opening the card does not rewrite existing text or introductory material. Keep passwords and API keys out of notes; use the phone's secret-entry card instead.

If another remote has changed an entry, refresh before editing again so you do not overwrite its changes. If the host is offline or a save fails, read the notice and retry after reconnecting.

## To-do {#todo}

You and Agent J keep this list together. Whatever Agent J promises to do later — tomorrow, next week, once something else is finished — goes on the card at once, so you can see it and hold it to it. It also adds the things it should remind you of.

Each item shows who is responsible (Agent J or you), when it may start and when it is due. A start or due point is a time, another item being finished (written `after:t3`), or a condition in words. Overdue, Due soon and Can start items carry a mark and come first, then items in progress and the rest; finished items are struck through and move to the end, cancelled ones last.

The filter at the top — All, Agent J's, Mine — shows one side at a time, so you can check what Agent J still owes you.

The + and Add an item both use `/agentj-add-todo`; add your task and send, for example who does it and by when. A successful addition opens the To-do card at the new item. Long text wraps inside the card.

Tap an item to mark it done or reopen it. Prioritise asks the Agent to go through the open items and fill in a missing owner, start or due point; the card then orders itself. These actions use the same management skill, with a chance to review and confirm before sending.

Each new conversation, recovery after a compaction, heartbeat and morning briefing starts with `agentj card todo due`: Agent J handles what is due and marks it done itself. To-do lists from older versions open unchanged; their items count as yours, with no start or due point.

## View automations {#automation}

Enable Automation under Cards in Settings, then open it from the logo's stack. Each background automation has a small card showing its unique name, purpose, run time and repeat schedule. The AI coding tool and model appear when known; missing information is shown as unavailable.

This is a read-only view. It does not start, stop or reschedule jobs. Tap an entry's @ to bring its unique name into the composer, add your question and send it to Agent J. The list covers the scheduled jobs the host can read: user systemd / cron on Linux, user launchd / cron on macOS, Task Scheduler on Windows, and Agent J’s own planned tasks; an empty list does not prove that no other background programs exist.

## A colour easter egg {#colour}

Press and hold the top-right corner of a card in the stack, with a finger or mouse. An unmarked area opens the hue-ring picker in the centre of the screen. Choose a colour on the ring, or enter HEX / RGB values, then save. Cancel or press Escape to close it.

Your choices are saved separately in your configuration and survive upgrades. This works for official and custom cards. Card titles and icons always use light words; only the fill changes. A colour too light for those words (lighter than the main menu's cards) shows a notice and cannot be saved. Restore default removes only that card’s custom colour.

## Make your own card {#create}

Use the card-creation button in Settings → Cards, or tell the Agent what you want to see on your phone. It asks what to show and which actions you need, builds the card, validates it and installs it.

A stack of small cards, one per record, is the recommended layout; another layout is fine when it fits better. Every new card has a management skill. Its +, add and organize buttons call that skill, so you can work in ordinary words.

## Mention or open a card {#mention}

Type @ in chat, select a card and ask about it. A unique address such as `card/notes` distinguishes cards with the same name. Card addresses in replies are links you can tap. You can also ask the Agent to open your notebook or a particular entry.

External links show their full address before opening a new tab. Navigation from the Agent does not interrupt an open confirmation window or Settings.

For other phone controls see the [full guide](https://agentj.app/docs/manual/). For creating scheduled tasks and their permissions see [Long tasks](https://agentj.app/docs/long-tasks/).
