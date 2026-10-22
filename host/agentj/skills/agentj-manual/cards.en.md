<!-- generated from https://agentj.app/docs/cards/en.md by agentjarvis/tools/manual_skill.mjs — do not edit -->
# Using cards

## Open and switch cards {#open}

Tap the top-left logo to unfold the stack, then pick a card. Chat, Workflows, Skills, Memory and Agent J are the first five pages. Enabled optional cards and your own cards follow them.

Open Settings → Agent tools → Cards to switch cards on or off. Notebook, To-do and Automation come with Agent J and start on; switch off any you do not want. A card you never switched off shows after an upgrade too. The first five system pages cannot be switched off. Switching a card off hides its entry while keeping its data. Upgrades keep that data too.
Open Settings → Agent features → Cards to switch cards on or off. Notebook, To-do and Automation come with Agent J and start on; switch off any you do not want. Files and Obsidian also come with Agent J but start off. A card you never switched off shows after an upgrade too. The first five system pages cannot be switched off. Switching a card off hides its entry while keeping its data. Upgrades keep that data too.

## Notebook: one entry per card {#notebook}

Keep useful addresses, phone numbers, meeting places and memos here. Each entry has its own small card with copy, edit and delete icons at the top right.

- Copy puts that entry on the clipboard.
- Edit opens the text for changes. Save when finished; cancel or press Escape to leave.
- Delete asks before removing that entry.
- The page's + and Add a note open a multiline dialog. Type or tap Paste; ⌘/Ctrl+Enter saves and Esc closes. Your dated note goes directly to the computer, without AI or tokens. “Saved (without AI)” confirms it and the card opens at the newest entry. Key-shaped content is refused.
- Collaborate with Agent J puts `/agentj-add-note ` in the message box with the caret at the end. Describe what to add, edit, delete or organize, then send. This collaboration uses AI.

To find an entry, tap the magnifier at the top right and type a word. Only the entries containing it stay, with the word highlighted; press Esc to see them all again. To-do and Automation cards work the same way.

Existing notebooks still use the same notes.md on the Agent J host. Opening the card does not rewrite existing text or introductory material. Keep passwords and API keys out of notes; use the phone's secret-entry card instead.

If another remote has changed an entry, refresh before editing again so you do not overwrite its changes. If the host is offline or a save fails, read the notice and retry after reconnecting.

## To-do {#todo}

You and Agent J keep this list together. Whatever Agent J promises to do later — tomorrow, next week, once something else is finished — goes on the card at once, so you can see it and hold it to it. It also adds the things it should remind you of.

Each item shows who is responsible (Agent J or you), when it may start and when it is due. A start or due point is a time, another item being finished (written `after:t3`), or a condition in words. Overdue, Due soon and Can start items carry a mark and come first, then items in progress and the rest; finished items are struck through and move to the end, cancelled ones last.

The filter at the top — All, Agent J's, Mine — shows one side at a time, so you can check what Agent J still owes you.

The + and Add an item open the same direct-save dialog: type or paste, then save without AI. Edit or delete individual items directly; deletion asks for confirmation. Collaborate with Agent J puts `/agentj-add-todo ` in the message box; describe the owner, start, deadline or organization you want, then send it to the AI.

Tap an item to mark it done or reopen it. Collaborate with Agent J asks the Agent to go through the open items and fill in a missing owner, start or due point; the card then orders itself. These actions use the same management skill, with a chance to review and confirm before sending.

Each new conversation, recovery after a compaction, heartbeat and morning briefing starts with `agentj card todo due`: Agent J handles what is due and marks it done itself. To-do lists from older versions open unchanged; their items count as yours, with no start or due point.

## View automations {#automation}

Enable Automation under Cards in Settings, then open it from the logo's stack. Each background automation has a small card showing its unique name, purpose, run time and repeat schedule. The AI coding tool and model appear when known; missing information is shown as unavailable.

This is a read-only view. It does not start, stop or reschedule jobs. Tap an entry's @ to bring its unique name into the composer, add your question and send it to Agent J. The list covers the scheduled jobs the host can read: user systemd / cron on Linux, user launchd / cron on macOS, Task Scheduler on Windows, and Agent J’s own planned tasks; an empty list does not prove that no other background programs exist.

## Files {#files}

Turn on Files under Cards in Settings; it starts off. It browses folders on your Agent J host: the Agent's working folder, plus folders you add on the computer with `agentj card files add <folder>`. Tap a folder to open it and use the path at the top to go back. Hidden items, links and anything that looks like a key never appear.

Tap the download icon to save a document to your phone. Pictures, PDFs, office files, text and similar documents can be downloaded; a file that looks like a key or contains one is refused. Tap @ to put the file's path in the composer instead: add your question and send, and Agent J reads it on the host.

## Obsidian {#obsidian}

If you keep notes in Obsidian, turn on the Obsidian card under Cards in Settings; it starts off. Tap + or Pick notes and tell Agent J which notes to show: it finds your vault on the Agent J host (or installs Obsidian with your approval) and keeps the selection. Each picked note appears as its own small card.

To change a note, tap its @ and say what to change, then send; Agent J edits the note in your vault on the host. The phone only reads the notes; links and hidden folders inside the vault are never followed, and anything that looks like a key is hidden.

## A colour easter egg {#colour}

Press and hold the top-right corner of a card in the stack, with a finger or mouse. An unmarked area opens the colour picker in the centre of the screen. The square sets how light and how vivid, the bar under it the hue — any colour at all; the Suggested row holds Agent J's palette, one tap each. Or type HEX / RGB values; both stay in step. Then save. Cancel or press Escape to close it.

Your choices are saved separately in your configuration and survive upgrades. This works for official and custom cards. Card titles and icons use light words; on a fill lighter than the main menu's cards they turn dark by themselves, and it still saves. While a value is not valid, Save is grey with the reason written above it. Restore default removes only that card’s custom colour.

## Open a card from the keyboard {#keys}

On a computer, press Alt+Shift (⌥⇧ on a Mac) and a letter of the card's English name to open that card: Notebook is Alt+Shift+N, To-do is Alt+Shift+T. The first letter comes first; if another shortcut already has it, the next letter; when all its letters are taken, a digit 1–9 or 0. It works while you type in the message box, too. Press it again, or Esc, to go back to the chat.

On the card stack, the English title underlines its letter, and a Chinese title shows the key beside it; a touch-only phone shows neither. Every card's key right now is in the Cards section at the top of the keyboard shortcuts page, and a new card gets one by itself.

## Make your own card {#create}

Use the card-creation button in Settings → Cards, or tell the Agent what you want to see on your phone. It asks what to show and which actions you need, builds the card, validates it and installs it.

A stack of small cards, one per record, is the recommended layout; another layout is fine when it fits better. Every new card has a management skill. Its +, add and organize buttons call that skill, so you can work in ordinary words.

## Share a card, or use someone else's {#share}

To share one of your cards, ask Agent J to put it in the plaza. It packs the card with its sample data only — never your own data — and checks it first: the card is valid, its buttons read correctly, a card that needs a program on the host names that program's source and exact version. You see the whole package before it is published.

To use a card someone shared, ask Agent J to find it in the plaza and install it; you see what it can do before it is installed. A shared card arrives switched off and marked as coming from the plaza. When you switch it on in Settings → Cards, the phone first shows everything it can do: where its data comes from, each button's full message or the program it calls, its links and its program's source. It turns on only after you confirm. A newer version arrives switched off again, and Agent J can bring the previous version back.

## A card backed by a program on your host {#server}

Some data lives in a program that runs on your Agent J host — a local dashboard, a scheduler, a tool the Agent installed for you. Ask the Agent to make a card for it: the card can take its list or notes straight from that program, and its buttons can ask the program to do one thing, such as refresh.

The phone never connects to the program. Agent J asks it on the host's own loopback address, only on the port and paths the card declares, and shows the answer only when the program identifies itself as that card's program. Answers are size-limited, time-limited and filtered for keys like any card data. A button that talks to the program first shows where it goes; it runs only when you tap Run.

If the card says the service is not running, ask the Agent to start it again.

## Mention or open a card {#mention}

Type @ in chat, select a card and ask about it. A unique address such as `card/notes` distinguishes cards with the same name. Card addresses in replies are links you can tap. You can also ask the Agent to open your notebook or a particular entry.

External links show their full address before opening a new tab. Navigation from the Agent does not interrupt an open confirmation window or Settings.

For other phone controls see the [full guide](https://agentj.app/docs/manual/). For creating scheduled tasks and their permissions see [Long tasks](https://agentj.app/docs/long-tasks/).

## Calendar: managed with Agent J

The official Calendar card defaults open and can be disabled in Settings → Cards. It works without a connected service.
The month view includes an agenda for the selected date. Tap an empty date to add an event; the native dialog saves directly
on your computer, bypassing the model. Edit/delete local entries, or press ESC to close. “Ask Agent J to help” drafts
`/agentj-calendar ` for complex changes. Text and icons use Lotion like the other cards.
All-day end dates are exclusive. Timed input uses the phone timezone and stores an explicit offset. On conflict close the
dialog and reopen the calendar before editing. Open timed to-dos appear read-only; edit them on the To-do card.
Agent J checks today's schedule and reminder windows with `agentj card cal due --json` on natural wake-up. Reminders are
not punctual alarms: sleep, offline time or a busy Agent can delay them.

Optional ICS subscriptions default off. Store the private feed URL in the existing key card as CALENDAR_ICS_SECRET;
ask Agent J to subscribe by that name and refresh explicitly. HTTPS ICS feeds from Google, Apple or other providers are
read-only. Remote updates win for that source; failure keeps the last snapshot. Recurring feeds and DURATION-based feeds are rejected as a whole,
keeping previous data. Unsubscribe removes only that source's cache. For two-way sync with Google Calendar or iCloud,
see the next section. CLI: `agentj card cal add|edit|delete|list|due --json`;
see agentj-calendar. Data stays on the host and is displayed through the paired encrypted channel.

### Sync with Google Calendar or iCloud

Sync is optional; the calendar works the same without it. Ask Agent J to sync your Google Calendar or iCloud calendar and
it walks you through the setup. Nothing is connected until you ask.

- **Google**: give access once from your own Google project (see [Connect Google](https://agentj.app/docs/google/)); Agent J then connects
  the card. Google only offers full calendar access for this tool; Agent J uses it for calendar events only.
- **iCloud**: create an app-specific password at appleid.apple.com (Sign-In and Security → App-Specific Passwords). Save
  your Apple ID and that password on the secret card Agent J sends you, never in chat.

Once connected, events from both sides show in the card with a small Google or iCloud tag, and you edit them like any
other event. Changes go out within seconds; offline they show “Waiting to sync” and are sent later. The sync row above the
month shows the last sync time and a “Sync” button. If an event changed on both sides, the card shows the other side's
version for now and keeps yours: below the month, tap “Keep mine” or keep theirs. If one side deleted it and the other
changed it, the buttons say what happens: deleted there, “Delete here too” or “Keep it”; deleted by you, “Delete it
anyway” or “Keep Google's”. After you save or delete on the phone,
“Undo” stays for 10 seconds. The same event on both sides is linked, not copied. Recurring events from Google or iCloud are
read-only here. Disconnecting keeps the events in your calendar; changes not yet sent are dropped. Agent J commands:
`connect`, `sync`, `queue`, `undo`, `resolve`, `disconnect` (see agentj-calendar).

## Palette generator and My palettes {#palette}

Palette is a built-in card that starts off. Enable it in Settings → Agent tools → Cards. The same generator is also inside the background-color dialog: hold the top-right corner of any card to open it.

Enter a seed HEX, choose Analogous, Complementary, Triadic, Split complementary or Monochromatic, and generate 5, 8 or 10 related colors. Hue, saturation and lightness sliders update the result immediately. Each swatch shows its HEX, suggested light or dark text and the contrast ratio for that text. Tap to select; hold to copy HEX. A focused swatch also supports Ctrl+C / ⌘C. Your palettes may use any color.

Save to My palettes stores the whole set in a shared library on your computer, up to 32 sets. These are color sets, not complete themes for the busy indicator or other screens. Every card's background dialog can read this library. Choose a saved color, then tap Save at the bottom of that dialog to change that card's background. Light backgrounds use dark text automatically. Load a saved palette to continue adjusting it; Delete removes the saved set while keeping existing card backgrounds.

Switching the Palette card off or upgrading keeps the library. Other paired remotes read the same library when opening the component or tapping Refresh library. It is stored on your computer over the existing end-to-end encrypted connection. Saving requires a connection; if it fails, follow the message and try again.
