<!-- generated from https://agentj.app/docs/manual/en.md by agentjarvis/tools/manual_skill.mjs — do not edit -->
# User guide

This page walks through everything you can see on the phone screen, one thing at a time. If you have not installed or
paired yet, start with [Install](https://agentj.app/docs/install/) and [Using your phone](https://agentj.app/docs/phone/).

You can also just ask Agent J — "what does this colour mean?", "how do I send a file?". It reads this same guide.

## First use: two remotes and the first message

After the install, the agent that set things up takes you through adding this computer to your seat and then pairing two
remotes. Both are required:

1. **This computer's browser**: open m.agentj.app on this computer and pair it. It is the quickest, and the big screen
   makes every step easy to follow.
2. **Your main phone**: the one you carry every day. Add the page to the Home Screen first, then pair from that icon.

Other phones, tablets or computers can wait: when you want one, tell Agent J "pair another one" and it walks you through.

Every remote is connected to the same Agent and shows the same conversation: start on your phone, carry on in the browser on your computer. Remotes are for one person's devices; a colleague who wants in gets a seat of their own, the way everyone in an office gets their own desk. See [Seat, Agent and host](https://agentj.app/docs/what/#desk). Several people can share one computer too, one seat each: see [One computer, several people](https://agentj.app/docs/multi-seat/).

On the first remote, the first message comes from Agent J itself: it introduces itself as your chief-of-staff assistant,
the single entry point between you and every workflow and agent, and suggests you talk to it in this window from now on.
Then it shows you the window in a few small steps — the screen colours, the quota lines and the water level, voice and
attachments, the approval card — and finally which remote is still missing. Reply "ok" or "next" for the next step; say
"skip" to stop.

The welcome is sent only once. Every remote you pair later just gets one line: "This … is connected too". Until both
required remotes are paired, Agent J may mention the missing one in a sentence at the end of a reply; once both are done
it stops. On the computer, `agentj onboarding` shows the same.

## Background colour: what the Agent is doing

The colour of the whole screen tells you one thing: **the state of the main Agent on your computer**. Other agents it
runs in the background, and scheduled tasks, never change it.

| Colour | Meaning | What you do |
|---|---|---|
| Green | Waiting for you | Say something whenever you like |
| Blue, with a light running along the top | Working | Wait — or keep sending; messages queue |
| Orange, edges pulsing | An action needs your approval | Read the card, approve or deny |
| Purple | A question for you | Pick an answer, or cancel it and type |
| Grey | Not connected to the computer, state unknown, or everything is stopped | Read the line at the top |

（Screenshot：Green: waiting for you — https://agentj.app/docs/manual/idle.en.webp）
（Screenshot：Blue: working — https://agentj.app/docs/manual/working.en.webp）
（Screenshot：Orange: waiting for your approval — https://agentj.app/docs/manual/approval.en.webp）
（Screenshot：Purple: a question for you — https://agentj.app/docs/manual/question.en.webp）
（Screenshot：Grey: the computer is offline — https://agentj.app/docs/manual/offline.en.webp）
- The icon in the top-left corner follows the state too. Tap it to change the text size (medium → large → medium →
  small); hold it to jump to the first unread reply ("No unread" when there is none). The size is remembered on this phone and survives a reload.
- With the message box empty, press and hold the grey Send button: a send-sound volume slider grows up out of it. Slide
  up for louder, down for quieter, and let go to keep it.
- The small line at the top says the same state in words.
- The phone gives a short buzz whenever the state changes.

## Three lines: quotas and context

- **The thin line under the top bar** (marked weekly): how much of your AI plan's weekly quota is used. Longer = more used.
- **The thin line above the message box**: the 5-hour quota.
- **The rising water with waves in the background**: how much of the context — what the Agent can keep in mind at
  once — this conversation uses. Higher water = closer to full. After a compaction the water drops to the bottom.

Tap either line (or the number next to it) and the "Usage" box opens in the middle of the screen with the exact numbers,
for example week 58 %, 5 hours 34 %, context 64 %, and how long ago they were reported. "Got it", a tap outside or Esc
closes it.

The day in brackets after the weekly figure is where you are in the 7-day reset cycle. The Usage box also has a "Reset cycle" bar with how long until the reset and when, and a small line under each quota with how much you will have used by the reset at the current pace. The figures do not glow normally: the weekly one glows faint blue when it is used faster than the even pace (about 14% a day), and its used part turns light blue in the box; anything at 80% or more glows soft orange, at 90% or more orange-red — little is left. Providers that do not report a reset time show no day.

5H or WEEKLY showing "—" usually means one of two things: ① your plan has no limit for that window (for example ChatGPT Pro at $200: OpenAI reports only the weekly window and a credit balance, no 5-hour window), or ② the figure is temporarily unavailable. Neither affects normal use.

（Screenshot：Water past half: the level in the background is above the middle — https://agentj.app/docs/manual/water-high.en.webp）
### The water is past half — what now?

When the context fills up, the Agent starts to forget the earliest details. Once it is past half, Agent J mentions it
once at the end of a reply (once per conversation).

1. When it suits you, say "compact", tap `/compact` in ≡ (all commands), or send `/compact`.
2. **You don't need to prepare anything.** Before compacting, Agent J has the Agent write a handover file — what it is
   working on, decisions made, to-dos and the file paths it needs — and only then compacts.
3. After the compaction it reads the handover first and carries on.

If the handover could not be written (say the Agent was stuck), the compaction still happens and the phone tells you in
one line.

The handoff path is fixed and copyable from the phone: `~/.local/state/agentj/handoff/compact.md` in shared mode,
`<work folder>/.agentj/handover/compact.md` for Agent J's own main conversation. If you send nothing after a compaction,
Agent J itself reminds the Agent to read the handoff and carry on, and the phone shows one line about it. See the
compaction and handoff page.

`/clear` starts a new conversation. It asks first, and you can undo it until you send your next message; once the new
conversation has begun, the button turns into a short "Too late to undo: the new conversation has started. The old one is saved on this computer." line and the old one stays on your computer. To keep
your progress, use `/compact`, not `/clear`. Status lines such as the new-conversation greeting address you as "Chairman" by
default; change it with `agentj config set owner.address Alex`.

## Heartbeat: the Agent checks in by itself

After a quiet spell (default 60 minutes) Agent J wakes the Agent on your computer to look at background work and your
to-dos. All fine → nothing reaches the phone. Something to tell you → a short page labelled **Heartbeat check**. Say
"check every 30 minutes", "wake up every morning at 7:30" or "stop waking up on a timer" to change it, or use Settings →
Heartbeat: pick the idle time from a drop-down (30 minutes to 24 hours, or custom) and turn on "Also wake up every day
at". Every heartbeat costs
one model call. See the Heartbeat page in the docs.

## One message per page — swipe through them

Each page shows what you said at the top and the Agent's reply below. Swipe right for older pages, left for newer ones;
1 / 1 in the corner is the page number and total. A page turn is a quick flick that starts the moment your finger lands;
hold still for a third of a second and then drag, and you are selecting text instead — that never turns the page, nor does
dragging a selection's handles. While a long reply scrolls, the name, the load line and the robots stay pinned at the top of
the card.

The header is split into three equal parts: double-tap the left third to go to the first (oldest) message, double-tap the right third (the page number) for the latest; the middle third is for unread replies. When replies arrived after you last looked, a small pulsing round badge in the middle of the header shows how many; tap it to jump to the first unread reply and page forward from there — the badge goes away once you reach the latest. With nothing unread there is no badge and a tap in the middle does nothing. Pressing `u` on a computer also jumps to the first unread reply. Unread replies sync across all your remotes: seeing a reply on any remote clears that reply and earlier unread hints on the others. An offline remote syncs before showing unread hints when it returns. After an upgrade the replies already there count as read, so no remote opens at 99+; a read that did not reach the computer is sent again by itself. Silent turns do not count.

The buttons under a reply, left to right:

| Button | What it does |
|---|---|
| Book | Full-screen reading (or double-tap the reply), with a text-size control |
| Speaker | Read it aloud with the phone's own voice — the text goes nowhere else |
| Arrow | Share it through the phone's share sheet |
| Two pages | Copy the whole reply |
| Curved arrow | Reply to this one; the quote appears above the message box |

Select some text in a reply to quote just that part. Selecting somewhere else adds another quote instead of replacing it — up to 5, from any replies, also while paging or in the full-screen reader — side by side above the message box; tap a quote's words to read it in full, its time to jump back to it (highlighted), its ✕ to remove it; the ✕ at the end or Esc clears them all. The clipboard always holds your latest selection.

On its own at the bottom left of the reply card is a white round button (a ring with a gap and an arrow leaving it): Share as a web report — it turns what you worked out in the chat into a good-looking web page you can send to others. Tap it and the message box gets `/web-page-report` with the cursor after it; nothing is sent. Add a line if you like — "only the pricing part, for people at my company, open for a week" — or send it as it is and the Agent asks first: the whole conversation, a few topics or just this reply; who may open it (anyone with the link, an access code, only your company's e-mail domain, any e-mail sign-in); how long it stays up; whether to hide names and numbers. Once you have said it, it does not ask again. Tap it on an older reply and that reply goes along as a quote, so the Agent knows where to start. If the web report skill is not on your computer yet, the Agent asks whether to install it from the plaza first.

Hold the round button and the Hot key sheet rises from the bottom: what it is bound to now and what a tap does, then every command and skill of the phone's 「/」 menu (one-tap commands, your own commands, the Agent's skills), with a search box. Bind it to anything you use often: pick one, tap Bind it, and from then on the button is that — the icon stays the same. Bound to a command (`/status`, `/compact` …) a tap runs it at once; one that wipes the conversation such as `/clear` still asks you first — a hot key never skips that. Bound to a skill, a tap puts it in the message box; add a line and send, and the Agent runs it on that reply. Back to default at the top of the list makes it Share as a web report again. You can also ask the Agent to change it, or run `agentj config set ui.card_hot_action status` on the computer.

The name at the top left of the reply card is the name you gave your Agent. Press and hold it to rename the Agent — the same as `agentj name` on your computer; the new name shows on the phone and the computer. Sometimes a few little pixel robots dance at the top right: each one is an Agent working for you in the background — a workflow CEO, or a helper the main Agent sent out. New work brings a new robot; a robot whose work is done takes a bow and floats away. A phone shows up to 6 side by side, then +N. Tap the robots and a square opens in the middle of the screen: how many Agents are at work, who is watching them for you, and all of them listed — each row its robot, who, and what it is doing (the whole task title, with an ellipsis if it is too long). Tap a row to open that Agent's own card: where it runs, how long it has been working, its latest step, and for a background dispatch the report path and the report's first line; the card is only for looking, and Back returns to the list. Sub-agents sent out by Claude Code, Codex and OpenCode all show up this way. They show whether or not the main Agent is replying; work running with no robot means it was not registered — ask the Agent to wrap it in `agentj busy run`. At its bottom, "Change the look" sends the Agent a message from you asking to customize the look, and it asks how you want the robots, colours, logo or send button to change, then validates the theme before applying it. Pressing and holding the robots does the same. ESC or a tap outside closes it. The robot gallery and the display rules: [Background robots](https://agentj.app/docs/robots/).

The thin grey line under the name is the load line: a light-coloured segment glides back and forth along it, and the faster it glides, the busier your computer is — if it is racing and your message can wait, wait a little. Tap the line and a square in the middle of the screen explains it: the overall load right now, then five rows — CPU, memory, GPU, disk and network: disk is how busy reads and writes are plus the space left, network is what goes in and out per second. Without a graphics card, or when it cannot be read, the row says "— (not detected)". The five are measured every 2–3 s only while the square is open and stop when it closes, so they cost your computer nothing otherwise. Last, how fast idle, normal and busy look (about 6 s, 3 s and 1 s a sweep). It stands still while the phone is disconnected.

Replies are laid out for you:

- **Code** is syntax-highlighted and scrolls sideways when wide; a code block has a copy button at its top right. A short
  shaded command inside the text (inline code) copies with one tap ("Copied"), ready to paste into a terminal.
- **Maths** (LaTeX) shows as formulas.
- **Diagrams** (mermaid) are drawn as pictures.
- **Images, audio, video, PDFs, web pages and other files the Agent mentions** show up right in the reply, as long as
  they are inside its working folder: tap an image to enlarge it, play audio and video in place, preview web pages in a
  safe frame (no scripts run there). Under every file card is one row of icons: preview, download, share, forward by email,
  forward to Telegram. Small files load by themselves once they are on screen; big ones load on a tap (the button shows the
  size), and nothing loads by itself on data saver or a cellular connection. Preview opens full screen: PDFs, Word (docx),
  Excel (xlsx), CSV tables, Markdown, code and text show right there; kinds it can't show yet, such as PowerPoint, offer the
  download. On a computer, select a card and press Space to preview; Space or Esc closes it. While a preview is open, the left and right arrow keys (a sideways swipe on the phone) move to the previous or next file of the same reply, and the top bar shows which one of how many; on a zoomed picture one finger moves the picture instead. Download saves the file under its own name (Chinese, spaces and emoji included) and a short note confirms it went to the browser's downloads; in the iPhone Home Screen app it opens the share sheet — choose Save to Files.
- Files that are too large or outside the working folder are not sent to the phone — the reply gets one line saying why.
  Audio, video and pictures always go, whatever their names (Chinese, underscores, long names are fine). Credential files
  (such as `.env`, `.pem`, private keys) and text files holding a real key are not sent as ordinary files: a secret pickup
  card opens on the phone instead (Face ID to open); when no card opens, the line says why and how to get the file. Image
  links to the web are never opened automatically.

（Screenshot：A picture, a formula and highlighted code in a reply — https://agentj.app/docs/manual/rich.en.webp）
（Screenshot：Further down: a diagram, a file to download and a line about a file that was not sent — https://agentj.app/docs/manual/rich-more.en.webp）
### Search the chat

The magnifier left of Settings at the top right (`⌘K` / `Ctrl+K` on a computer) opens the search: type words into the dark
card in the middle of the screen and it searches what you sent, quotes, attachment names and the Agent's replies at once.
Separate words with spaces; a page matches when all of them appear. Case, full-width / half-width and Traditional /
Simplified Chinese do not matter.

Results come in best-match order — more hits, hits in what you sent, at the start or in the first line, the whole phrase or
an exact match rank higher, and recent pages a little higher — or switch to "Newest". Each result shows who sent it, when,
and the matching snippet with the words marked. Pages already on the phone show at once; older pages that are only on the
computer are added a moment later.

Tap a result: the card closes and the chat turns to that page, which flashes once. Tap the magnifier again to get back to
the same results at the same scroll position. Every search is kept as a page (up to 20): swipe sideways in the card (`←`
`→` or the `‹` `›` buttons on a computer) to go back to earlier searches; the top line says "Search N of M: words". These
searches stay on this phone, encrypted like your drafts, and are cleared with the conversation (`/clear`) or when you
unpair.

On the computer: `agentj history search <words>`.

## Sending messages

- Type and tap send, or press Enter. Until the Agent has it, you can cancel the send.
- You can send while the Agent is busy (blue); messages wait their turn.
- **It works offline, too.** With no network, send as usual: the message is kept encrypted on this phone and the page
  says "Will send when you're back online". When the network returns, queued messages go out in order, never twice.
  Even if the computer restarts, a message is never run twice: one it hadn't finished is marked on the phone with a line
  saying so — send it again yourself if you still need it.

（Screenshot：Two messages sent offline wait above the message box — https://agentj.app/docs/manual/offline-queue.en.webp）
- Type `/` at the start of the box to see the commands you can use.

### Sharing a Codex App session

While the Codex App holds a session, the phone follows its actual model from local records and shows read-only status. A native active-writer refusal leaves the message undelivered and never automatically starts another thread. Quit the Codex App completely, then resend to try continuing the same thread; switching to New chat or waiting for a reply does not guarantee release. Do not promise concurrent writes from separate clients, delete writer locks, inject the App’s private pipes or change permissions.

When the phone says another program is using the session, it says which one: an Agent J leftover background process is cleaned up automatically and your message goes on; the Codex inside the ChatGPT App, the Codex App or a `codex` in a terminal must be quit first; if Agent J cannot find out, it tells you so.

### Codex permissions: directly on your computer by default

With Codex, as long as you never wrote `sandbox_mode` at the top of `~/.codex/config.toml`, Agent J lets it work like you do in your terminal: network, installs, files in your home folder. Risky actions (spending, deleting, sending, keys …) still wait for your approval on the phone.

- If you set `sandbox_mode` yourself (for example `workspace-write`), it stays yours; anything beyond it is refused, and the notice says how to open it up.
- `sandbox_mode` must be at the top of the file (before the first `[ ]`). Appended at the end it usually lands inside the last `[ ]` table, where Codex ignores it. `/status` on the phone or `agentj doctor` on the computer then says "written but not in effect". Just ask the Agent: it uses `agentj codex-sandbox`, which writes the right place; after an Agent J restart the same conversation follows the new setting at once.
- When continuing a Codex App session, that conversation's own permissions in the Codex App apply; if you never chose any there (still the default), the rule above applies. Refused because of permissions? Set this conversation to Full access in the Codex App, then resend from the phone.

## Attachments: files, photos, camera

（Screenshot：Attachments and a quote above the message box — https://agentj.app/docs/manual/attach.en.webp）
- Three buttons above the message box: file, photo library, camera. You can also paste an image, or drag files in on a
  computer browser.
- Hold "File" to paste the clipboard's text at the cursor; longer than 2,000 characters, it becomes a .txt attachment. Hold "Photos" to add the clipboard's image as an attachment. An iPhone may first show its own small paste bubble — tap it.
- Up to 10 attachments at a time, 200 MB each, of any file type. Recordings and videos are transcribed on your computer and the Agent gets the original too. Very long pasted text becomes an attachment automatically.
- Attachments travel encrypted straight to your computer, into the Agent's working folder, and are removed after 30 days.

## Voice: tap to lock, hold to talk and release to send

（Screenshot：Hold to talk: recording — https://agentj.app/docs/manual/voice.en.webp）
- Hold the microphone and speak; release to transcribe and send automatically. Long recordings send as attachments. To cancel, slide clearly off the button (about 80 px away, or up by more than a button's height) and let go there; a shaky hand or a small move does not cancel, and sliding back keeps recording.
- Tap or double-tap the microphone to lock recording. Wait half a second, then tap again to finish; review the words in the message box before sending. Keyboard m still releases to text and double-m still locks.
- Speech is turned into text **on your own computer**, never by us. If the computer has no speech model yet, your phone
  keyboard's dictation works too.
- To have every reply read aloud, turn on auto-read in Settings.

## ≡ All commands

（Screenshot：All commands — https://agentj.app/docs/manual/commands.en.webp）
≡ to the left of the message box opens all commands; tap one to run it:

| Command | What it does |
|---|---|
| `/compact` | Compact the context (writes the handover first, see above) |
| `/clear` | Clear the conversation (can be undone) |
| `/model` | See which models you can switch to; `/model name` switches |
| `/context` | How much context is used |
| `/cost` | What this conversation cost |
| `/usage` | How much of your plan is used |
| `/status` | The current state |
| `/stop` | Interrupt the current turn |
| `/help` | List these commands |

The two commands in the "Friends" group are put into the message box when tapped; finish them and send:

| Command | What it does |
|---|---|
| `/my-agent-id` | Your computer answers with your Agent ID (tap the code block to copy it), the share link and an "Open my card" button; never goes to the AI, costs no tokens |
| `/add-friend ID [note]` | Add a friend: signed by this phone like "Add friend" on the friends page; a mistyped character is caught at once; without an ID it opens "Add friend" |

In the "Run now" group, the command to switch the AI coding tool (`/switch-harness`) goes to your main Agent with one tap; you can also type it in the message box, add "switch to Codex" after it and send it. It does not switch at once; it goes to your main Agent, which says which kind of tool it means, scans which ones this computer has, tries each one's sign-in with a quick headless call and tells you which ones you can switch to. It switches only after you confirm. The phone disconnects for a few seconds and reconnects by itself, then the new tool introduces itself; if it does not come up, Agent J switches back by itself and tells you why. See [Switch AI coding tool](https://agentj.app/docs/switch-harness/).
Want commands of your own? Tap `/customize` in the list, or press and hold ≡: it sends the Agent a message from you asking to customize your slash commands; it lists the current ones, asks what to add, remove or change, and only switches the change on after it validates.

Before switching, the tool you use now writes the conversation's key points into a handoff file; the new tool reads it first, then carries on. If none was written the switch still happens, and the phone says "No handoff this time".

The menu also has "Stop everything" and the Agent's own skills. Other commands, such as changing settings, are for the
computer. See [Agent friends](https://agentj.app/docs/friends/).

The model and effort at the top: tap to switch to the next one, hold to go back to the default. Taps only change the label: a half is switched once you leave it alone for 5 seconds (meanwhile it fades, with a thin line running out underneath); tapping back to the one in use sends nothing. Holding for the default and typing `/model name` take effect at once.

## Cards that need you

### Approval card (orange)

When the Agent wants to do something that needs your yes, the screen turns orange and a card slides up saying what it
wants to do. To approve, **press and hold** the approve button for about a second, until it fills; it comes first (on top on a phone,
on the left on a computer). Tap deny to refuse. The card goes away as soon as you answer, and comes back to explain if
the answer did not arrive or the request had timed out. A long command shows two lines: "Show all" opens it, and the
copy button at the right of the title copies the whole command. On a computer, `⌘↩` (Mac) or `Ctrl↩`
(Windows / Linux) approves, the same as the hold; `Esc` tucks the card away.
No answer within 2 minutes counts as a no. You can tuck the card away; a small tag at the top keeps reminding you.

E-mail: mail to yourself, or to people who are all on your e-mail allowlist, goes out without a card. On a card for
anyone else, each new recipient has an unticked "Don't ask again for mail to them (add to the allowlist)"; tick it and
hold to approve, and they join the allowlist — their mail is handled directly and mail to them no longer asks you.

In a shared session a card marked "Shows passwords or keys" would print a credential on the screen, and one marked "Changes passwords or keys" would change it; a program reading `.env` by name, or a note that mentions `.env`, no longer asks. For the stricter old rule run `agentj config approvals strict` on the computer (`agentj config approvals standard` switches back). Cards use only your language setting.

Only real danger asks: the five kinds ask on a clear match (`git push`, a deploy, a payment or messaging API, deleting cloud data, writing a credential file); writing an ordinary note (words like deploy or key inside a heredoc don't count), `--dry-run`, `list`, `status`, `systemctl --user` for your own background jobs and `sleep` wait loops don't. A command Agent J can't read clearly (the program is a variable like `$PY`) is first checked by Jev when it is set up (`OPENROUTER_API_KEY` on the computer): at or above "Jev threshold" in Settings (default 0.70, 0.50–0.95) it runs without a card and is logged in Activity. The threshold never affects the five clear dangers — they always ask. Too many cards? Tell the Agent; it explains and runs `agentj config set approval.jev_threshold 0.6` (one confirmation card on the phone).

Goal mode (`/goal`): tap `/goal` in the ≡ menu and add what to finish and by when. The Agent first sends one opening card with the goal, how you'll know it's done, the deadline and the dangerous actions it needs (by kind, with limits, e.g. "push to repository X", "spend: one domain only"); approve it once and those actions stop asking for the rest of the goal, each one logged in Activity. Outside the grant, a step the computer can undo shows a 10-second countdown card that counts as yes if you don't tap; anything that can't be undone or looks like an attack waits for you and is listed in the result card at the end. `/goal status` shows progress, `/goal stop` withdraws the grant at once; a goal ends after 8 hours by default. For a company or team, switch off "Goal mode: approve in 10 s" under Settings → Safety.

（Screenshot：Tucked away: a small tag stays at the top — https://agentj.app/docs/manual/approval-later.en.webp）
### Question card (purple)

The Agent wants you to choose: tap an answer and confirm. If none fits, cancel the question and type your answer. The card works whichever Agent runs on the computer — Claude Code, Codex, OpenCode, Kimi or Qwen (`agentj ask`): single or multiple choice, sometimes with a short preview under an option. Pick "Other (I'll type it in the chat)", or "OK, I'll answer in the chat" on an open question, then type your answer in the message box. If the card did not really appear, the Agent never says it did.

### Admin-password card

When the Agent needs to run one command as administrator (installing a system package, say), this card shows the
command, why, and its effect. Type your computer's admin password on the card. It travels encrypted and is used for that
one command; **the Agent never sees your password**.

（Screenshot：Admin-password card — https://agentj.app/docs/manual/sudo.en.webp）
### Key card

When the Agent needs an API key, this card says which key, what for, and where it will be stored. Paste the key; it is
written straight there — **not into the chat, and the Agent never sees it**.

（Screenshot：Key card — https://agentj.app/docs/manual/secret.en.webp）
Tapping save (or Enter) with nothing pasted just asks you to paste the key first — it never counts as a no. "Don't provide"
sits on its own line below and needs a second tap. The card stays valid for 10 minutes, even if the Agent's command is
interrupted meanwhile: submit as usual and it is saved.

### Getting your own keys or configs on the phone (pickup card)

To use something set up on the computer on your phone — a proxy ss:// link, a config file with a password — just ask the
Agent ("send me the SS link"). Ordinary replies hide passwords, so the Agent sends a **secret pickup card** instead:

- The card stays hidden and shows only the name, purpose and size. Tap "View with Face ID"; only after that does the
  computer send the contents.
- Copy text with "Copy", save a file with "Download file". Tap "Done" when finished and it is cleared from the page
  (automatically after 2 minutes too); the chat keeps a single "picked up" line.
- It can be picked up once within 10 minutes; tap "Not needed" if you don't want it.
- If this phone hasn't set up Face ID yet, tap "Set up Face ID" on the card first.
- The card goes only to your own paired phones. Telegram, groups and friends still can't get these secrets.

### Start card (long tasks)

When you ask for something that keeps running — "set up a weekly competitor brief" — your Agent checks what it can do and
puts everything that is yours to decide on **one start card**: what it will do, where it delivers, how often it runs and at
most five choices. Confirm and start confirms the brief and, when needed, turns the schedule on; Change something starts
nothing. No keys go on this card, and confirming never approves paying, deleting or sending outside — those still ask you
one by one. See [Long tasks](https://agentj.app/docs/long-tasks/).

More in [Admin rights and keys from your phone](https://agentj.app/docs/phone-admin/).

## Stop everything

In ≡, tap "Stop everything" and confirm: the current work stops at once, every pending approval is denied, batch
approvals are withdrawn and scheduled tasks pause. The screen turns grey with a banner on top until you resume.

（Screenshot：Everything stopped — https://agentj.app/docs/manual/stopped.en.webp）
## Settings

（Screenshot：Settings — https://agentj.app/docs/manual/settings.en.webp）
The gear in the top-right corner opens Settings, full screen (on a computer browser: `s`, or `⌘,` / `Ctrl+,`; Esc
closes it). It is the only menu: Agent & remotes (the Agent's name, Remotes, Face ID, unpairing); Agent tools (Models & Key,
Memory — rules the Agent keeps, you can delete them —, Activity — what it did recently and who approved what —, Schedules,
Friends, Bots, the first-run checklist); language and appearance; reply text size, send sound volume, auto-read, wake word;
lock-screen alerts; Channels (re-pair Telegram, Email, Contacts — your friends' e-mails and phones, one tap onto the e-mail allowlist — and My contact details); safety options (high-risk warnings, session mode, isolation, Docker); the web app (how updates happen, upgrade Agent J on the computer,
versions, update and reload, Add to Home Screen, keyboard shortcuts); account, help, and the security, privacy and terms
pages. The phone must be connected to the computer to change settings.

Every menu — Settings and the pages it opens, ≡ All commands, keyboard shortcuts, silent history, "How safe is this web app?" — is a full-screen dark page with the same header, icons and buttons. The back arrow at the top left does what Esc does: one level up, so a page opened from Settings returns to Settings. Confirmations and risky actions still ask in a centred card.

### AI login rescue and renewing before expiry

When the computer's Claude Code / Codex login expires, the Agent cannot reply. Open Settings → AI login rescue and sign in
again from the phone, without going back to the computer: Codex shows a link and a device code; Claude Code shows an
authorize link, and its code goes only into the secret card.

With a long-lived Claude token, Agent J writes down the day it stored the token and counts one year from it. The section
shows how many days the Claude login has left. On day 7, 3 and 1 before the expiry you get one reminder each: a note in the
chat, a push, and a red banner at the top of the chat. Tap the banner (or Renew now in the section) → passkey → open the authorize link
in the phone's browser and approve → paste the code into the secret card. The new token replaces the old one as soon as a
few-second live check passes, and a new one-year countdown starts; if the check fails, the old one stays and you approve
again. The authorize link lasts 10 minutes (the page counts down); when it expires or the code does not work, the computer
opens a new link by itself (up to 3 rounds). The link goes only to paired phones, never to Telegram.

Subscription /login and Codex's ChatGPT sign-in renew themselves, and provider keys usually do not expire, so they have no
fixed date. Once a day the computer sends each signed-in AI one very short, tool-free test message. If the login no longer
works (for example you signed out of all devices on the web), you are told at once to rescue it; running out of balance or
hitting a rate limit never asks you to sign in again.

## Remotes

At the bottom of Settings, "Download Agent J Brand Kit" opens the [official Brand Kit](https://agentj.app/docs/robots/#brand-kit) on the
website in a new tab: the main logo and the 16 robots as transparent PNGs (256, 512 or 1024 pixels; 512 is recommended),
ready to use as the avatar of your Telegram bot, your email or any other bot.

（Screenshot：Remotes — https://agentj.app/docs/manual/remotes.en.webp）
Settings → Remotes is its own page: how many of the 10 remotes are in use, every paired phone or browser (online or not,
when paired and last used, how it was added; this one marked "This one"), Rename and Unbind on each row, and "Add a remote":
a one-time QR / link for 5 minutes; the new device shows a 6-digit code, you type it here and tap Approve (one try).

The scan icon at the top right (or holding the camera button) reads QR codes: a new device's pairing code adds it as a remote (both screens show the same 6 digits; tap "They match — add it" only if they do); any other code goes into the message field as text, never opened or sent by itself. A new device needs no computer: open m.agentj.app and the code at the top is for a paired phone to scan. The "Web app" tag now lives in Settings → "How safe is this web app?".

## Keyboard shortcuts

（Screenshot：Keyboard shortcuts — https://agentj.app/docs/manual/keys.en.webp）
On a computer browser or a tablet with a keyboard, press `?` for the full list (it is in Settings too). Every tap, hold and swipe with its key: [Keyboard shortcuts and gestures](https://agentj.app/docs/shortcuts/). The ones you will use most:

| Key | What it does |
|---|---|
| `Alt+Shift+J` · `j` · `←` | Previous page (older) |
| `k` · `→` | Next page (newer) |
| `Alt+Shift+G` · `g` | Jump to the newest page |
| `G` | Jump to the earliest page (older ones are fetched from the computer) |
| `u` | Jump to the first unread reply (read status syncs across your remotes), then page forward |
| `o` · `↑` · `↓` | Expand / fold the message (↑ expands, ↓ folds; with nothing to toggle ↑ ↓ scroll the page) |
| `f` | Read this reply full screen |
| `Alt+Shift+Y` · `y` | Copy the whole reply |
| `Alt+Shift+A` · `r` | Reply to this one (the quote shows above the message box, focus stays in it; Esc cancels); Alt+Shift+A also works while typing |
| `Alt+Shift+O` · `s` · `⌘+,` / `Ctrl+,` | Open Settings |
| `Alt+Shift+K` · `⌘+K` / `Ctrl+K` | Search the chat, or settings while Settings is open (inside the message box Ctrl+K still deletes to the end); press again to close |
| `i` · `Esc` | Into the message box; Esc in it leaves (the draft stays) |
| `/` | Commands: / outside the message box jumps in with / typed and the command menu open; not while a dialog or Settings is open |
| `a` | Pick a file for the Agent |
| `p` | Pick pictures from the photo library |
| `c` | Take a photo |
| hold `m` | Hold to talk, release to send (slide out to cancel); keyboard m still releases to text |
| `Esc` `Esc` | Esc twice outside the message box clears text and attachments (✕ clears the text only, with undo). Esc never interrupts the Agent; to stop the current task, use the stop button or send /stop |
| `⌘+Enter` / `Ctrl+Enter` | Approve, the same as holding the approve button (identity checks as usual); the card wins over the message box |

## Pairing and Face ID

（Screenshot：Not paired yet: scan the QR code on your computer — https://agentj.app/docs/manual/pair.en.webp）
The first time: scan the QR code on your computer, then type the phone's 6-digit code and your remote pairing password on
the computer (see [Using your phone](https://agentj.app/docs/phone/)).

（Screenshot：The 6-digit code during pairing — https://agentj.app/docs/manual/pair-code.en.webp）
### If the code will not scan

- Let the QR code fill most of the middle of the picture and hold the phone steady, not too close (too close and it
  cannot focus). Turn the computer's screen brighter and avoid reflections.
- After 10 seconds without a result the scanner suggests another way:
  - In a browser tab: scan the code on the computer with the phone's own Camera app; it opens the pairing page.
  - In the page opened from the Home Screen icon: run `agentj pair --link` on the computer, send the printed link to
    this phone, tap Back and paste it into the box on the pairing screen.
- That link is the pairing key: valid 5 minutes, works once, never send it to anyone else.

**Pairing once is enough.** As long as your computer hasn't unpaired this phone, opening the page connects by itself. Safari on iPhone clears what websites store; from 0.17.4 the page then reconnects to the same computer with an automatic sign-in token kept on the phone (only your computer recognises it; it is replaced on each use) — no QR code, no Face ID. You only scan again when the computer has unpaired this phone (for example after you lost it); the pairing screen then says that automatic sign-in no longer works.

After a successful pairing the phone asks once whether to remember this phone with Face ID. Save it and confirm with
Face ID. From then on, when you open Agent J somewhere else on the same phone — from the Home Screen, in a new private
tab, after clearing browser data — the pairing screen offers to reconnect with Face ID: **no QR code, no approval on the
computer**. The computer's device list keeps counting this phone once.

- Not now? Skip it and pair with the QR code as usual.
- Face ID only proves "this is the phone you already approved"; it grants nothing more. Unpairing the phone on the
  computer cancels its Face ID record too.
- An iPhone syncs this record through iCloud Keychain to your other Apple devices on the same Apple ID. They can
  reconnect with Face ID as well, but take this phone's place rather than adding another.
- Browsers without Face ID / passkey support never show the offer; pairing works by QR code as before.

## Things one sentence does

Agent J is the only Agent you deal with, so most things are a sentence, not a button:

- "Pick up where we left off yesterday", "Did that report get finished?": it searches this computer's history and
  continues.
- "Redo that", "Say it differently": it redoes the last answer another way.
- "Open the xx project": it finds that project in your work folder.
- "Compact": handover first, then compaction.
- "Switch to Codex", "back to Claude Code": switches the AI coding tool — checked first, switched only if it works. See [Switch AI coding tool](https://agentj.app/docs/switch-harness/).
- "I want to report a problem": it asks what you want to report; once you provide the content, it redacts, sends and gives you the ticket ID. Agent-initiated reports need your agreement on the draft first.
- "Send my card to Kai", "What did Kai say lately?": it manages your Agent friends, see [Agent friends](https://agentj.app/docs/friends/).
- On long jobs it sends you a one-line progress note now and then.


- "Set up a weekly competitor brief" and other recurring work: it checks its capabilities and sends one start card for
  you to confirm, see [Long tasks](https://agentj.app/docs/long-tasks/).
- "What can you do": a capability page opens on your phone — what is ready and what needs setup.

Silent turns do not take up chat pages. Open ≡ → View silent history to read them. Esc or the backdrop closes the read-only panel; /clear clears it too.

## Add a remote for another computer

Once the target computer is installed, account-bound and online, find its seat card on your account page and choose Add a remote. Confirm it's you, then scan with the phone; the target computer's terminal isn't needed. Confirm either way: with your passkey (none on this computer? choose Use a phone or tablet in the prompt and confirm on your phone), or with that computer's remote pairing password. After a passkey confirmation, the same session doesn't ask again for ten minutes; the QR expires after five minutes and adds one device. Pending devices can also be reviewed and approved on the seat card. Under More on the seat card, See what the computer is waiting on you for is only for two cases: resuming after Stop everything, or enabling a scheduled task the Agent created. It has nothing to do with seats or billing; an unpaid bill never stops the tasks on your computer.

Account-page pairing is on by default. The target computer’s owner can disable it with `agentj remote-pair off`. Offline hosts are never queued; turn them on and try again. Older hosts must run `agentj update apply` or update from a paired phone. Devices record “Added from account page” and other remotes receive a notice.

This route trusts the signing control plane. Pairing material is encrypted to an ephemeral browser key and its plaintext is never stored by the backend; compromise of the signing service could still add a device. The remote pairing password is stored only on that computer, as a hash. When you type it on the account page, the browser first encrypts it to a one-time public key that computer just handed out; only that computer can open and check it. Our servers pass on ciphertext they can't read and keep nothing. Five wrong tries in a row lock it on that computer for a while.


For Codex failures, follow the phone guidance: tap the top model name to replace an account-unsupported model (temporarily disabled until an account refresh); check your proxy/VPN for ChatGPT connection failures; run `codex login` on the computer when login expires. Redacted native errors remain under Details.


### Shared Codex model and provider (0.16.4a1)
For a phone turn, an explicit `agent.model` / `agent.effort` or the latest phone selection is sent as `turn/start.model` / `effort`. Without an explicit phone choice, Codex keeps the thread choice. A desktop turn uses the desktop actor’s own last explicit selection; Agent J only observes it. The top bar shows the active actor’s model, not a promise that a queued phone choice is already running. Selecting Default sends the current native defaults once.

Agent J reads effective native `model_provider` from Codex `config/read` and names it as `thread/resume.modelProvider` when the phone resumes an existing thread. It never copies provider keys or rewrites your Codex configuration. If a different program holds the writer, the phone stays read-only and identifies the program/PID when known. Quit that program before retrying.

For older Agent J versions that keep an old model/provider, start a new Codex conversation in the same folder using your current native defaults, then let Agent J follow the new conversation (clear any explicitly selected old shared thread). Or switch Agent J to an independent session. A new conversation has separate history. Do not ask the owner to paste keys into chat.


Shared Claude makes the phone-message setting Claude Code really uses in the work folder accept (organization policy > the folder's private `.claude/settings.local.json` > its `.claude/settings.json` > `~/.claude/settings.json`), at selection, startup and upgrade, backing up the file first; the phone is told once. A hold in user or private local settings is changed where it is; a hold in the shared project file is overridden from the private local layer. Organization policy, `refuse` and an explicit `agentj config claude-inbound off` are only reported. Enabled: phone messages no longer need individual confirmation on the computer; to disable, run `agentj config claude-inbound off`. A session started before the change needs /clear or a new session. Only Claude Code is affected; Codex/OpenCode have no equivalent mechanism. When a message is not acknowledged, the phone names the reason (organization policy, project settings, turned off, needs /clear, computer busy); check the computer before resending. The phone's model/quota bars (`agentj config claude-statusline`) are turned on at the same moment unless the owner turned them off, chained at the status-line layer in effect; `agentj doctor` reports a layer that hides them.

Shared mode: Claude `/clear` and `/compact` wait for the current desktop turn and confirm native hook receipts. A successful clear archives phone pages and starts at zero, including clears typed on the desktop. Context uses only new-session/post-compaction measurements; pending readings show “—”, never the previous value. A timeout reports an unconfirmed change; check the desktop before retrying. Shared Claude has no native undo-clear button: history remains archived locally; use desktop `/resume` to restore a conversation. Independent mode retains undo. Attached Codex cannot switch the desktop thread: use desktop `/new` and select the new thread. Attached OpenCode supports `/compact`; use desktop `/new` and select the new session to clear. Agent J's own Claude PTY accepts controls directly; an existing terminal requires an exact-session Herdr input route, otherwise the phone explains the reason and native command. Shared `/context`, `/usage`, `/status` and `/model` without an argument read measured data; unavailable values show “—”.

The 5h line remains visible at 1%; 0% has a start marker. “5h —” means no reported measurement. Expanding the input card reduces the reply reading area while keeping the action row and composer fixed; long inputs and replies scroll within their cards.

## Shared sessions

New installs use an independent session: Agent J starts its own and leaves your terminal tools alone. Sharing the session you already have open on the computer is optional — Settings → Session mode, or `agentj config set agent.session_mode shared` (back: `… independent`).

When you enter or switch to a shared Claude Code, Codex or OpenCode session, the phone shows a one-time notice that fades after 3.5 seconds. Refreshing or reconnecting to the same session does not repeat it. Read-only conflicts, failed sends and requests that need your action remain visible.

Which session the phone talks to: by default the Claude Code session open in your work folder. If several are open there, run `agentj agent claude` on the computer: it lists them with their names and first messages and lets you pick one. Nothing changes on the phone.


The pairing page offers browser and storage guidance. Use a regular Safari / Chrome window, add to Home Screen and pair from that same icon. The menu retains Set up Face ID after Later; Copy page link excludes the one-use pairing secret.


## Sharing screenshots

See [Android sharing and the iPhone clipboard Shortcut](https://agentj.app/docs/share-screenshot/). Shared content enters the composer; add a note before sending.

## Updating the phone page

Host and page updates are checked separately. In other cases, when a newer web version is detected, a square opens in the middle of the screen: **Server upgraded**. Tap **Refresh** to use the new version; **Later**, ESC or a tap outside closes it, and you can still tap the settings icon at the top right → **Update and reload** later. When the host and page server both have the same newer version, the page reloads after ten seconds without input, preserving the draft; recording and unsubmitted card actions block it. Otherwise only offer a refresh. Keyboard: `Alt+Shift+U` upgrades the host (phone confirmation unchanged); `Alt+Shift+R` updates and reloads the page.


When a daily, weekly or monthly model allowance runs out, the phone tells you to wait for its reset or upgrade your plan with the model provider. A temporary rate limit still asks you to retry later. Provider error details are never displayed.

If shared Claude completes a reply while your phone is disconnected, reconnecting updates its existing input page. Reloading first fetches the current computer history before claiming that a message is still waiting. `/update` receipts use the current language setting, including changes made while Agent J is running. `agentj doctor` includes the websockets version for troubleshooting.

Short network blips are repaired quietly: when the phone's connection drops it is usually back within a second, and the page only turns grey with "Connection lost. Retrying in N s…" if reconnecting takes longer than about 6 seconds. A busy computer that answers late is not treated as a disconnect. If your Telegram channel can't reach Telegram three times in a row (or for two minutes), one line under the chat says so and disappears by itself once Telegram works again. For the first minute after Agent J starts, `agentj doctor` shows the relay as "connecting" rather than a problem.

Local read-aloud uses only voices on this phone. Host/cloud voices stay in `voice.tts.voice`; choose a phone voice separately with `voice.tts.phone_voice` (empty selects the local default for the reply language). Switching modes keeps both choices. An unavailable phone voice falls back with one notice. On first opening, iPhone waits for local voices to load before reporting that no voice is available.


Shared Claude ingress: startup and doctor repair the effective writable layer to accept; managed policies are read-only. Sessions started before the change need a new session or /clear on the computer. Shared startup re-enables ingress; check the desktop before resending unacknowledged messages.


0.18.1: Valid older Telegram enrollment continues with its local bot environment name. Huawei HarmonyOS devices have accurate names; account owners can rename a remote with a passkey while its host is online.

Renaming a remote: press and hold the source at the top left of the input card (half a second) to rename the remote in your hand; other remotes and the account page show the new name right away. Only the owner can rename other remotes, on the account page. A remote the owner renamed shows that name and its device type as the message source (for example "Alex (boss) · iOS Safari"), marked "(name set by the owner)"; a tap shows the name the device gave itself. Remotes nobody renamed are marked "(name set by the device)". The Agent uses the owner's name to tell who is speaking, but the name is only a label, not proof of identity. A double tap at the top left still goes to the first message.

## The first message is slow and the phone shows nothing

On a new computer it is usually one of four, and the phone says which: the model service cannot be reached (Claude Code retries for about three minutes; the phone shows a card when it is still failing after about 10 seconds); bubblewrap is missing (every start shows a "Sandbox unavailable" card, whose "Run this launch without isolation" button needs the owner's passkey; install `sudo apt-get install -y bubblewrap socat` on the computer); running as root with bypassPermissions (Claude Code refuses to start; Agent J starts it in the default permission mode and says so); a model the provider does not serve (400; sent again once on the default Opus 5.5 · medium).

`agentj agent claude` (or codex) answers the tool's one-time setup ahead (onboarding, theme, folder trust, bypass confirmation) — only what is missing, no key read; the `first-setup` row of `agentj doctor` checks it. When the Agent really waits at a terminal prompt, the phone gets a "Handle it on the computer" card quoting the terminal's words: answer it at the computer, or run `agentj agent claude`, then send again.

## Sandbox failures and admin commands

If an independent session's sandbox cannot start, the Agent stays stopped. The phone card explains the cause (such as missing bubblewrap) and repair. Tap **Run this launch without isolation** and verify with the owner's passkey to allow this launch only; the next launch checks isolation again. This launch may access local files and credentials. Set up a passkey in the phone menu if needed. Denial or timeout never starts the Agent.

The sudo card labels the reason and effect as **Agent claim**. Shell or interpreter commands are marked red and require an admin password even with the passwordless helper installed. Secret cards support URL verification; `--verify-cmd` is disabled and runs no command outside the sandbox. Scheduled tasks and native customer-service bots authenticate from native storage and do not inherit host-service API keys or tokens.

### Connecting Telegram

Telegram uses a bot of your own. Save its key with a key card, then run `agentj channel add telegram` on the computer or tap 「Re-pair Telegram」 in Settings. The chat shows a one-time 6-digit pairing code (10 minutes, one use). In Telegram, send `/start <code>` to your bot in a private chat: that chat becomes the owner, and the bot replies that Telegram is connected. No other bot is needed to look up your ID. Anyone else who writes to the bot only gets their own numeric ID back; a code sent in a group does not count. To move to a new phone or Telegram account, tap 「Re-pair Telegram」 again.

The same bot can also be customer service for everyone else. In Bots, open a customer-service bot and tick 「Let my Agent J Telegram bot receive guests」 (or ask your Agent J; you approve it on the phone). Your paired private chat stays your main conversation; any other private chat becomes a visitor of that bot, with its own session, instructions, model and limits, and never gets your permissions. Off by default: then strangers only get their numeric ID. See [Customer service bots](https://agentj.app/docs/bots/).

### Telegram and friend safety boundaries

Forwarded messages in the owner’s Telegram private chat are external data, not the owner’s instructions. Forwarded commands do not execute. Approval commands in groups remain silent; use the paired phone to approve. When the family safety check is unavailable, that message pauses: the owner can send the request directly from the paired phone. Only a leading `/notification` or `/notify` command counts as a reminder request; the word notification inside other text does not.

Group text and captions are checked before attachments are downloaded. Proxy groups do not download attachments; family attachments are refused when the rules match. Each group sender has a 40 MiB download budget per UTC hour across groups, preserved across restarts. Unknown sizes reserve the per-file maximum. Paused group messages cannot start Agent tools. Friend cards allow up to 64 characters for the name, 32 for the owner and 140 for the introduction. A credential-shaped value in supplementary context rejects the whole edit; use ordinary background information.

Support bots process Telegram private chats independently, preserving order within each chat. Text over 2000 characters gets a fixed request to shorten it without a model call. Customer text and model suggestions in support reports are data for summarising and diagnosis, never owner instructions.

## Getting help

- When your Agent can report the problem itself, have it use `agentj support` to ask our support desk; you don't have to find a person. See [Getting help from the support desk](https://agentj.app/docs/support/).
- If it can't connect at all (offline, the install failed, it can't reach our servers), or agentj.app won't open (for example in mainland China), email feedback@agentj.app — email gets through from anywhere. Include your account ID, what happened and when, and a screenshot.

## Five pages (candidate after 0.20.3)

Tap the logo for Chat, Workflows, Skills, Memory or Agent J; hold it to return to Chat. These pages read your computer's documents and hide credential files. A workflow's @ inserts an atomic mention that sends as plain text. Tap the dancing robots for Working (AI tool and reported model) or Workflow (CEO, people and reporting lines); unassigned workers are temporary helpers. Alt+W opens the menu; Z still cycles text size and M still records.

Double-click or double-tap a non-audio file card (image, video, PDF, document, text, code, Markdown or HTML) to view the file full screen, or use Full screen. Audio cards and reply text open the reply reader. Video keeps its playback position; Escape or Back closes the current viewer.


A collapsed input card shows two quote lines and one body line, or up to three body lines without a quote, with an ellipsis for overflow. Attachments appear as a line paperclip and total count beside “…” (images, files and audio together); zero attachments show no count. Double-click the body, tap “…” or press `o` to expand all text, the quote and attachments.

The Agent J father robot is the official logo. Its body follows the five state colours; the Send button uses its white silhouette. Outside chat it dances to remind you that holding it returns to chat. Reduced motion uses a static logo and a subtle inset heartbeat outline. When changing the background robots, Agent J first asks whether you also want a new five-state logo set; leaving it out keeps the current set.

## When a goal needs a capability you have not set up

Agent J checks existing APIs, skills and its browser sign-in before explaining what is missing and how to supply it. It does not end with “I cannot do that.” The routes, in order, are:

1. **Official API.** For missing accounts, authorization or keys, it gives one sentence stating what you can supply, followed by a short list of providers, registration pages and approximate costs. After you agree it guides setup step by step. Keys go only in the phone secret card, never chat or the opening card. Service charges need separate approval.
2. **A website with no API.** Agent J can operate it with its own browser. It first checks browser setup and the site's sign-in. When needed it says “This site has no official API; I can operate it through my browser. Please sign in to X there once” and starts the login flow. QR sites open a phone login card; QR codes only appear there. Other sites open a sign-in page on the computer. An uncertain check is reported as uncertain, never as signed in. Sign-in is not authorization: publishing, payment and other sensitive actions still require phone approval.
3. **A missing platform primitive in this version.** It explains the gap, offers /feedback-to-agentj-father to send feedback to Agent J Father, and states what you must still supply once the capability is unlocked. For feedback it proposes itself, you review and approve the draft first. Your explicit /feedback-to-agentj-father command with content is redacted and submitted directly, without a second confirmation. Submission does not mean it is scheduled and never promises a release. Combined gaps get both paths.

For goals needing a workflow, genuinely missing choices, authorization and setup go on one opening card (at most five required items), followed by a secret or login card as needed. Already authorized, equipped work continues; only dependent work waits. Truly illegal or dangerous requests are still refused.

The first time you open Workflows, and each time Agent J starts on your computer, it scans the folders one level under the working root: folders organized with the workflow design method (an entry file and the documentation/ boot documents) show as cards; one missing from the CEO roster (documentation/ROLES.md) gets a row appended at its end — append-only, nothing else changes, and a row you delete is not added back. When no workflow is found, the three that come with Agent J (Knowledge base, Scripts and articles, E-book) are shown; Install it makes Agent J show you the preview first. Set up organization sends Agent J one standard instruction: organize the projects you use with the workflow design method, register their CEOs and check, listing any unorganized project for you first; nothing is deleted or moved. After one use it reads Review organization, which fills gaps.

Open a workflow card and its staff are listed under the CEO: only employee files with a name in `.claude/agents/` (on the Codex side, the .toml files in `.codex/agents/`) count — a README or another note there does not; an employee file with a title (for example title: Copywriter) shows that name. When a CEO has no staff yet, the card carries a small line "Only a CEO, no staff yet", and the Review organization button adds the count, for example "Review organization (2 with only a CEO)". Tap it and Agent J reads that company's latest reports, task.json and RUN.md, lists 3–7 proposed staff roles for you first, and writes them only after you agree (the Codex copy is made with them), never over an employee file you changed. When work is sent to a company that has only a CEO, Agent J also mentions it once in its reply — once per company.

## Full-screen history and file forwarding

In the reader, swipe right on text for an older reply and left for a newer reply. Desktop ← / → do the same. A small bounce marks either end. Vertical scrolling, long-press selection, horizontally scrolling tables/code, file controls and edge-back gestures keep their actions.

The file card’s paper plane sends the original file as a document to your Telegram private chat. It immediately shows “Sending…” and keeps “Sent to Telegram” or a failure reason. If Telegram is not configured, it tells you to configure it first. If delivery times out, check Telegram before trying again. Credential files remain blocked.

## Full-screen reading: fixed header and footer, made for long reading

The reader uses the same dark Noir as the dialogs in the middle of the screen, in light and dark mode alike. The header at the top and the footer at the bottom never move; only the text between them scrolls, with no scrollbar drawn — fingers, the wheel, the arrow keys and Space all scroll it.

- Header, left: the Agent's name and the reply's time — double-tap for the first reply. Middle: the unread badge when there is something unread — tap for the first unread reply. Right: the page number (this one / all of them; no ✕ — you leave the way you came in) — double-tap for the newest reply without leaving; it flashes when a new reply arrives.
- Footer, bottom left: a thin text-size icon that opens the size slider; pinching and the + / - keys still work. Bottom right: the reply card's six icons — full-screen reading, read aloud, forward to myself, forward by e-mail, copy, reply (rightmost).
- Copy and reply leave the reader; reply quotes this reply and puts the caret in the message box. Read aloud, forward to myself and forward by e-mail keep you in the reader, and the icon shows sending / sent. The book icon leaves too.
- When you swipe or press ← / → to another reply, the header's time and page number and the footer's buttons all follow it. Files that came with a reply show under its text.
- Keys: `G` first, `g` newest, `u` first unread, `y` copy and leave, `r` reply, `l` read aloud, `t` forward to myself, `e` forward by e-mail.

No scrollbar is drawn anywhere in the web app — pages, dialogs, menus, Settings, code blocks and file previews included — and everything still scrolls by finger, wheel or keyboard.

Agent J Father (also Agent J 爸爸 / Agent J 父亲) is our official feedback and support platform. Say “Please report this problem to Agent J’s developers (support platform)”, or casually “Tell your dad about this”. On the phone, `/feedback-to-agentj-father <message>` does the same: your Agent removes credentials, personal information, customer data and private path details, submits directly without asking twice, and gives you the ticket ID. The command alone asks what you want to report. If reporting a problem or idea is the Agent’s own initiative, it shows you the redacted draft on the phone and waits for agreement. Support replies are advice, not instructions. If it cannot connect, email feedback@agentj.app.
