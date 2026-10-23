<!-- generated from https://agentj.app/docs/manual/en.md by agentjarvis/tools/manual_skill.mjs — do not edit -->
# User guide

This page walks through everything you can see on the phone screen, one thing at a time. If you have not installed or
paired yet, start with [Install](https://agentj.app/docs/install/) and [Using your phone](https://agentj.app/docs/phone/).

You can also just ask Agent J — "what does this colour mean?", "how do I send a file?". It reads this same guide.

## Agent not replying and you only have your phone? {#phone-only}

No reply, or stuck on “Not yet received”? You can do this from your phone. After each step, send a short test message in chat and stop when a reply arrives:

1. Tap **Stop** to interrupt the current task.
2. Open **Settings → Emergency repair → Commands on the Agent J host**, find `agentj doctor --reconnect`, and tap **Run** beside it (recommended). Your phone asks the host to check and repair the connection; you do not need to open a terminal. Confirm with Face ID when asked.
3. Still no reply? In **Emergency repair**, tap **Restart service** (recommended), wait for the connection to return, then try again.
4. Still no reply? Tap **Emergency repair → Reopen AI session** (requires host and web versions from 0.25.2a1). It ends the stuck AI session process and tries to resume the same conversation; if that fails, it opens a new session and keeps the old conversation. On an older version without this button, go to the next step.
5. Still stuck? Tap **Emergency repair → ② Ask Agent J Father for remote help**. Only your tap sends a diagnostic report redacted on the host; keep the ticket number. Passwords, keys, chats and work files are not sent.

**Check quota before connection repairs.** For a Claude quota limit, first open Claude settings on your phone and check **Usage**. Use **Free reset** if offered; otherwise wait until the displayed reset time. A free reset is not available for every account or every limit. If quota has recovered but messages are still “Not yet received”, follow the five steps above. Restarting or reopening a session does not add quota. For an expired AI sign-in, use **AI login rescue**.

**A grey, disconnected phone takes a different path.** Check your phone’s network, tap “Try now”, then **Settings → Emergency repair → ① Reset connection**, followed by **② Ask Agent J Father for remote help** if needed. Give a brief drop a few seconds. Commands, restarts and session repair require a connection to the host or rescue helper; a phone cannot force them to run on a powered-off, sleeping or offline host. If it stays unreachable, open [the support chat](https://support.agentj.app) in your phone browser or email [feedback@agentj.app](mailto:feedback@agentj.app) with the symptom, versions and time, never credentials. You do not need to go to the computer.

Agent J Father is the official support platform run by the developers. Once chat works, `/feedback-to-agentj-father` can report the problem too; while there is no reply, use the phone support controls above.

## First use: two remotes and the first message

After the install, the agent that set things up takes you through adding the Agent J host to your seat and then pairing two
remotes. Both are required:

1. **the Agent J host's browser**: open [https://m.agentj.app/](https://m.agentj.app/) on the Agent J host and pair it. It is the quickest, and the big screen
   makes every step easy to follow.
2. **Your main phone**: the one you carry every day. Add the page to the Home Screen first, then pair from that icon.

Other phones, tablets or computers can wait: when you want one, tell Agent J "pair another one" and it walks you through.

Every remote is connected to the same Agent and shows the same conversation: start on your phone, carry on in the browser on the Agent J host. Remotes are for one person's devices; a colleague who wants in gets a seat of their own, the way everyone in an office gets their own desk. See [Seat, Agent and host](https://agentj.app/docs/what/#desk). Several people can share one computer too, one seat each: see [One computer, several people](https://agentj.app/docs/multi-seat/).

On the first remote, the first message comes from Agent J itself: it introduces itself as your chief-of-staff assistant,
the single entry point between you and every workflow and agent, and suggests you talk to it in this window from now on.
Then it shows you the window in a few small steps — the screen colours, the quota lines and the water level, voice and
attachments, the approval card — and finally which remote is still missing. Reply "ok" or "next" for the next step; say
"skip" to stop.

The welcome is sent only once. Every remote you pair later just gets one line: "This … is connected too". Until both
required remotes are paired, Agent J may mention the missing one in a sentence at the end of a reply; once both are done
it stops. On the Agent J host, `agentj onboarding` shows the same.

## Background colour: what the Agent is doing

The colour of the whole screen tells you one thing: **the state of the main Agent on the Agent J host**. Other agents it
runs in the background, and scheduled tasks, never change it.

| Colour | Meaning | What you do |
|---|---|---|
| Green | Waiting for you | Say something whenever you like |
| Blue, with a light running along the top | Working | Wait — or keep sending; messages queue |
| Orange, edges pulsing | An action needs your approval | Read the card, approve or deny |
| Purple | A question for you | Pick an answer, or cancel it and type |
| Grey | Not connected to the Agent J host, state unknown, or everything is stopped | Read the line at the top |

（Screenshot：Green: waiting for you — https://agentj.app/docs/manual/idle.en.webp）
（Screenshot：Blue: working — https://agentj.app/docs/manual/working.en.webp）
（Screenshot：Orange: waiting for your approval — https://agentj.app/docs/manual/approval.en.webp）
（Screenshot：Purple: a question for you — https://agentj.app/docs/manual/question.en.webp）
（Screenshot：Grey: the Agent J host is offline — https://agentj.app/docs/manual/offline.en.webp）
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

Both quota lines are always there, doubling as the page's top and bottom dividers. A line with no text beside it has no figure right now, usually because ① your plan has no limit for that window (for example ChatGPT Pro at $200: OpenAI reports only the weekly window and a credit balance, no 5-hour window), ② the provider offers no usage query (for example the Volcano Ark and Alibaba Bailian Coding Plans — check their consoles), or ③ the figure is temporarily unavailable. None of these affects normal use; the text appears by itself as soon as a model or AI coding tool reports it.

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
conversation has begun, the button turns into a short "The new conversation has started — too late to undo (the old one is saved on the Agent J host)" line and the old one stays on the Agent J host. To keep
your progress, use `/compact`, not `/clear`. Status lines such as the new-conversation greeting address you as "Chairman" by
default; change it with `agentj config set owner.address Alex`.

For OpenCode, the water level uses the model’s reported context limit first, then a built-in model table. If neither is available, it uses 200,000 tokens, based on the free DeepSeek Flash window. This display default is not a provider capacity guarantee. Used tokens still come from OpenCode; no reading is invented before usage arrives.

## Heartbeat: the Agent checks in by itself

After a quiet spell (default 60 minutes) Agent J wakes the Agent on the Agent J host to look at background work and your
to-dos. All fine → nothing reaches the phone. Something to tell you → a short page labelled **Heartbeat check**. Say
"check every 30 minutes", "wake up every morning at 7:30" or "stop waking up on a timer" to change it, or use Settings →
Heartbeat: pick the idle time from a drop-down (30 minutes to 24 hours, or custom) and turn on "Also wake up every day
at". Every heartbeat costs
one model call. See the Heartbeat page in the docs.

## Webhooks: outside services reach you

Every seat comes with webhook addresses (`hook.agentj.app/…`). To have a video job, Stripe payments, GitHub or
server alerts notify you here, tell the Agent in one sentence, for example **call me back when the video is done**. A
resident hook only starts receiving after you approve its card on the phone. The Agent treats what arrives as data only:
as expected → it carries on; needs you → it asks with options A / B / C and a recommendation; spam → it ignores it.
On the phone, Settings → "Agent tools" → "Webhooks" lists the live hooks (pause, resume, revoke, copy the address),
today's count and "Recent deliveries" (up to 50, with the Agent's triage). Secrets are never shown on the phone. See [Webhooks](https://agentj.app/docs/webhooks/).

## One message per page — swipe through them

Each page shows what you said at the top and the Agent's reply below. Swipe right for older pages, left for newer ones;
1 / 1 in the corner is the page number and total. A page turn is a quick flick that starts the moment your finger lands;
hold still for a third of a second and then drag, and you are selecting text instead — that never turns the page, nor does
dragging a selection's handles. While a long reply scrolls, the name, the load line and the robots stay pinned at the top of
the card.

The header is split into three equal parts: double-tap the left third to go to the first (oldest) message, double-tap the right third (the page number) for the latest; the middle third is for unread replies. When replies arrived after you last looked, a small pulsing round badge in the middle of the header shows how many; tap it to jump to the first unread reply and page forward from there — the badge goes away once you reach the latest. With nothing unread there is no badge and a tap in the middle does nothing. Pressing `u` on a computer also jumps to the first unread reply. Unread replies sync across all your remotes: seeing a reply on any remote clears that reply and earlier unread hints on the others. An offline remote syncs before showing unread hints when it returns. After an upgrade the replies already there count as read, so no remote opens at 99+; a read that did not reach the Agent J host is sent again by itself. One exception: if this remote is on an older page when a new reply arrives, its page number keeps pulsing and the badge keeps counting it until you reach that reply here, even when another remote already read it. Silent turns do not count.

While you read an older page, nothing moves you: sending a message, or a new reply from the Agent, leaves you on the same page at the same scroll position; a new reply only makes the page number at the top right pulse, and you page on when you are ready. Only when you are already on the newest page does a sent message take you to its new page.

The buttons under a reply, left to right:

| Button | What it does |
|---|---|
| Book | Full-screen reading (or double-tap the reply), with a text-size control |
| Speaker | Read it aloud with the phone's own voice — the text goes nowhere else |
| Arrow | Share it through the phone's share sheet |
| Two pages | Copy the whole reply |
| Curved arrow | Reply to this one; the quote appears above the message box |

Select some text in a reply to quote just that part. Selecting somewhere else adds another quote instead of replacing it — up to 5, from any replies, also while paging or in the full-screen reader — side by side above the message box; tap a quote's words to read it in full, its time to jump back to it (highlighted), its ✕ to remove it; the ✕ at the end or Esc clears them all. The clipboard always holds your latest selection.

When a message you sent carries two or more quotes, its card first folds them into one row — how many quotes there are, the start of the first one and how many more, the same look as above the message field before sending. Tap the row to show every quote (each still jumps back to its source); tap that top row again to fold them. A single quote shows as before.

On its own at the bottom left of the reply card is a white round button (a ring with a gap and an arrow leaving it): Share as a web report — it turns what you worked out in the chat into a good-looking web page you can send to others. Tap it and the message box gets `/web-page-report` with the cursor after it; nothing is sent. Add a line if you like — "only the pricing part, for people at my company, open for a week" — or send it as it is and the Agent asks first: the whole conversation, a few topics or just this reply; who may open it (anyone with the link, an access code, only your company's e-mail domain, any e-mail sign-in); how long it stays up; whether to hide names and numbers. Once you have said it, it does not ask again. Tap it on an older reply and that reply goes along as a quote, so the Agent knows where to start. If the web report skill is not on the Agent J host yet, the Agent asks whether to install it from the plaza first.

Hold the round button and the Hot key sheet rises from the bottom: what it is bound to now and what a tap does, then every command and skill of the phone's 「/」 menu (one-tap commands, your own commands, the Agent's skills), with a search box. Bind it to anything you use often: pick one, tap Bind it, and from then on the button is that — the icon stays the same. Bound to a command (`/status`, `/compact` …) a tap runs it at once; one that wipes the conversation such as `/clear` still asks you first — a hot key never skips that. Bound to a skill, a tap puts it in the message box; add a line and send, and the Agent runs it on that reply. Back to default at the top of the list makes it Share as a web report again. You can also ask the Agent to change it, or run `agentj config set ui.card_hot_action status` on the Agent J host.

The name at the top left of the reply card is the name you gave your Agent. Press and hold it to rename the Agent — the same as `agentj name` on the Agent J host; the new name shows on the phone and the Agent J host. Sometimes a few little pixel robots dance at the top right: each one is an Agent working for you in the background — a workflow CEO, or a helper the main Agent sent out. New work brings a new robot; a robot whose work is done takes a bow and floats away. A phone shows up to 6 side by side, then +N. Tap the robots and a square opens in the middle of the screen: how many Agents are at work, who is watching them for you, and all of them listed — each row its robot, who, and what it is doing (the whole task title, with an ellipsis if it is too long). Tap a row to open that Agent's own card: where it runs, how long it has been working, its latest step, and for a background dispatch the report path and the report's first line; the card is only for looking, and Back returns to the list. Sub-agents sent out by Claude Code, Codex and OpenCode all show up this way. They show whether or not the main Agent is replying; work running with no robot means it was not registered — ask the Agent to wrap it in `agentj busy run`. At its bottom, "Change the look" sends the Agent a message from you asking to customize the look, and it asks how you want the robots, colours, logo or send button to change, then validates the theme before applying it. Pressing and holding the robots does the same. ESC or a tap outside closes it. The robot gallery and the display rules: [Background robots](https://agentj.app/docs/robots/).

The thin grey line under the name is the load line: a light-coloured segment glides back and forth along it, and the faster it glides, the busier the Agent J host is — if it is racing and your message can wait, wait a little. Tap the line and a square in the middle of the screen explains it: the overall load right now, then five rows — CPU, memory, GPU, disk and network: disk is how busy reads and writes are plus the space left, network is what goes in and out per second. Without a graphics card, or when it cannot be read, the row says "— (not detected)". The five are measured every 2–3 s only while the square is open and stop when it closes, so they cost the Agent J host nothing otherwise. Last, how fast idle, normal and busy look (about 6 s, 3 s and 1 s a sweep). It stands still while the phone is disconnected.

Replies are laid out for you:

- **Code** is syntax-highlighted and scrolls sideways when wide; a code block has a copy button at its top right. A short
  shaded command inside the text (inline code) copies with one tap ("Copied"), ready to paste into a terminal.
- **Maths** (LaTeX) shows as formulas. Commands never turn into formulas: `$HOME` and `$(…)` stay as written, and system cards (such as a goal's result) show no formulas at all.
- **Diagrams** (mermaid) are drawn as pictures.
- **Images, audio, video, PDFs, web pages and other files the Agent mentions** show up right in the reply, as long as
  they are inside its working folder: tap an image to enlarge it, play audio and video in place, preview web pages in a
  safe frame (no scripts run there). Under every file card is one row of icons: preview, download, share, forward by email,
  forward to Telegram. Small files load by themselves once they are on screen; big ones load on a tap (the button shows the
  size), and nothing loads by itself on data saver or a cellular connection. Preview opens full screen: PDFs, Word (docx),
  Excel (xlsx), CSV tables, Markdown, code and text show right there; kinds it can't show yet, such as PowerPoint, offer the
  download. On a computer, select a card and press Space to preview; Space or Esc closes it. While a preview is open, the left and right arrow keys (a sideways swipe on the phone) move to the previous or next file of the same reply, and the top bar shows which one of how many; on a zoomed picture one finger moves the picture instead. Download saves the file under its own name (Chinese, spaces and emoji included) and a short note confirms it went to the browser's downloads; in the iPhone Home Screen app it opens the share sheet — choose Save to Files.
- Files that are too large or outside the working folder are not sent to the phone — the reply gets one line saying why.
  The working folder you set always counts, even after the Agent `cd`s into a sub-folder; the folder it is in right now counts too.
  Scripts and source files (`.sh`, `.py`, `.ps1` …) come as text cards: preview and download, never run. When a file is held back because it looks like a key, the note names the line it is on (showing only its first two characters).
  Audio, video and pictures always go, whatever their names (Chinese, underscores, long names are fine). Credential files
  (such as `.env`, `.pem`, private keys) and text files holding a real key are not sent as ordinary files: a secret pickup
  card opens on the phone instead (Face ID to open); when no card opens, the line says why and how to get the file. Image
  links to the web are never opened automatically.

（Screenshot：A picture, a formula and highlighted code in a reply — https://agentj.app/docs/manual/rich.en.webp）
（Screenshot：Further down: a diagram, a file to download and a line about a file that was not sent — https://agentj.app/docs/manual/rich-more.en.webp）
### Search the chat

The magnifier and search shortcut search the page you are on: chat history in the conversation, cards in Workflows, Skills, Memory and Agent J, or the current text in Settings, a menu page or an open document.

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

On the Agent J host: `agentj history search <words>`.

## Sending messages

- Type and tap send, or press Enter. Until the Agent has it, you can cancel the send.
- You can send while the Agent is busy (blue); messages wait their turn.
- **It works offline, too.** With no network, send as usual: the message is kept encrypted on this phone and the page
  says "Will send when you're back online". When the network returns, queued messages go out in order, never twice.
  Even if the Agent J host restarts, a message is never run twice: one it hadn't finished is marked on the phone with a line
  saying so — send it again yourself if you still need it.

（Screenshot：Two messages sent offline wait above the message box — https://agentj.app/docs/manual/offline-queue.en.webp）
- Type `/` at the start of the box to see the commands you can use; a few more letters filter the list. It also lists the current coding tool's own commands (Claude Code, Codex or OpenCode); a name Agent J also has shows once (Agent J's). All of them can be picked and typed: the ones marked “runs directly” go to that tool as typed (/init, /review); the ones marked “the Agent does it” change a setting or need the terminal screen (/config, /mcp), so Agent J hands your wish to the Agent, which carries it out through the usual confirmations and tells you how it went; the few grey ones (/exit, /login) can't be done from the web, and tapping one says why. Switch tools and the list follows. A picked command (or a card or workflow picked with @) gets one space after it with the cursor behind it; the box keeps the focus until a message has really been sent.

### The message box: always one line, full screen for long text

- The message box is one line tall and never grows; with several lines you can scroll inside it, and the fifth button above it (full screen) shows how many lines there are (for example 5 lines). Shift+Enter adds a line, Enter sends.

- In a wide window on a computer, the cards and the message box widen with the window instead of stopping at a fixed width.

- For long text open the **full-screen editor**: tap the fifth button, press Ctrl+G, or on a phone swipe up on the message box. Dark background, light text, the whole screen to write in; when the phone's keyboard opens, the text and the buttons below it move up above the keyboard.

- In full screen: the top-left icon leaves (so do Esc, Ctrl+G again, a double tap on blank space — on words a double tap still selects — and pulling the bar at the top centre down; not far enough and it springs back), and the words go back to the message box as they are; the top-right icon clears everything, with 5 seconds to undo; the slash icon at the bottom left opens the command list and puts the one you pick at the cursor; Send is at the bottom right, or press ⌘/Ctrl+Enter. In full screen Enter only adds a line.

- Terminal habits work in both the box and full screen: Ctrl+A / E line start and end, Ctrl+U / K delete to the start or end, Ctrl+W delete the previous word, Ctrl+Y paste back what was deleted, Ctrl+D delete the next character, Ctrl+J new line (a \ at the end of the line, then Enter, does the same). In the box, Ctrl+S puts the draft aside (press again to bring it back) and Ctrl+R finds a message you sent that contains these words. All shortcuts: [Keyboard shortcuts](https://agentj.app/docs/shortcuts/).

### Sharing a Codex App session

While the Codex App holds a session, the phone follows its actual model from local records and shows read-only status. A native active-writer refusal leaves the message undelivered and never automatically starts another thread. Quit the Codex App completely, then resend to try continuing the same thread; switching to New chat or waiting for a reply does not guarantee release. Do not promise concurrent writes from separate clients, delete writer locks, inject the App’s private pipes or change permissions.

When the phone says another program is using the session, it says which one: an Agent J leftover background process is cleaned up automatically and your message goes on; the Codex inside the ChatGPT App, the Codex App or a `codex` in a terminal must be quit first; if Agent J cannot find out, it tells you so.

### Codex permissions: directly on the Agent J host by default

With Codex, as long as you never wrote `sandbox_mode` at the top of `~/.codex/config.toml`, Agent J lets it work like you do in your terminal: network, installs, files in your home folder. Risky actions (spending, deleting, sending, keys …) still wait for your approval on the phone.

- If you set `sandbox_mode` yourself (for example `workspace-write`), it stays yours; anything beyond it is refused, and the notice says how to open it up.
- `sandbox_mode` must be at the top of the file (before the first `[ ]`). Appended at the end it usually lands inside the last `[ ]` table, where Codex ignores it. `/status` on the phone or `agentj doctor` on the Agent J host then says "written but not in effect". Just ask the Agent: it uses `agentj codex-sandbox`, which writes the right place; after an Agent J restart the same conversation follows the new setting at once.
- When continuing a Codex App session, that conversation's own permissions in the Codex App apply; if you never chose any there (still the default), the rule above applies. Refused because of permissions? Set this conversation to Full access in the Codex App, then resend from the phone.

## Attachments: files, photos, camera

（Screenshot：Attachments and a quote above the message box — https://agentj.app/docs/manual/attach.en.webp）
- Three buttons above the message box: file, photo library, camera. You can also paste an image, or drag files in on a
  computer browser.
- Hold "File" to paste the clipboard's text at the cursor. Into an empty box, text over 2,000 characters becomes a .txt attachment; if the box already has words, it goes in unless together they pass the single-message limit, and then only the pasted part becomes the attachment. Hold "Photos" to add the clipboard's image as an attachment. An iPhone may first show its own small paste bubble — tap it.
- Up to 10 attachments at a time, 200 MB each, of any file type. Recordings and videos are transcribed on the Agent J host and the Agent gets the original too. Pasted text follows the same rule: words already in the box → it goes in unless the total passes the single-message limit; an empty box → over 2,000 characters becomes an attachment.
- Attachments travel encrypted straight to the Agent J host, into the Agent's working folder, and are removed after 30 days.

## Voice: tap to lock, hold to talk and release to send

（Screenshot：Hold to talk: recording — https://agentj.app/docs/manual/voice.en.webp）
- Hold the microphone and speak; release to transcribe and send automatically. Long recordings send as attachments. To cancel, slide clearly off the button (about 80 px away, or up by more than a button's height) and let go there; a shaky hand or a small move does not cancel, and sliding back keeps recording.
- Tap or double-tap the microphone to lock recording. Wait half a second, then tap again to finish; review the words in the message box before sending. Keyboard m still releases to text and double-m still locks.
- Speech is turned into text **on your own computer**, never by us. If the Agent J host has no speech model yet, your phone
  keyboard's dictation works too.
- To have every reply read aloud, turn on auto-read in Settings.

When you send only a voice note, the computer's transcript comes back onto your message card once it is ready: folded, the card shows what you said; open, it sits under the voice attachment (it folds away, and a long press selects it). If transcription failed, the card says why. Every remote and your history show it too. The copy icon just left of expand / collapse at the card's bottom right copies the whole message in one tap (for a voice-only message: the transcript).

## ≡ All commands

（Screenshot：All commands — https://agentj.app/docs/manual/commands.en.webp）
≡ to the left of the message box opens all commands; tap one to run it:

| Command | What it does |
|---|---|
| `/compact` | Compact the context (writes the handover first, see above) |
| `/clear` | Clear the conversation (can be undone) |
| `/new` | Same as `/clear` (the name Codex uses) |
| `/model` | See which models you can switch to; `/model name` switches |
| `/context` | How much context is used |
| `/cost` | What this conversation cost |
| `/usage` | How much of your plan is used |
| `/status` | The current state |
| `/stop` | Interrupt the current turn |
| `/help` | List these commands |
| `/reset` | Reset connection: the same as Settings → Emergency repair → ① (below) |

The two commands in the "Friends" group are put into the message box when tapped; finish them and send:

| Command | What it does |
|---|---|
| `/my-agent-id` | the Agent J host answers with your Agent ID (tap the code block to copy it), the share link and an "Open my card" button; never goes to the AI, costs no tokens |
| `/add-friend ID [note]` | Add a friend: signed by this phone like "Add friend" on the friends page; a mistyped character is caught at once; without an ID it opens "Add friend" |

In the "Run now" group, the command to switch the AI coding tool (`/switch-harness`) goes to your main Agent with one tap; you can also type it in the message box, add "switch to Codex" after it and send it. It does not switch at once; it goes to your main Agent, which says which kind of tool it means, scans which ones the Agent J host has, tries each one's sign-in with a quick headless call and tells you which ones you can switch to. It switches only after you confirm. The phone disconnects for a few seconds and reconnects by itself, then the new tool introduces itself; if it does not come up, Agent J switches back by itself and tells you why. See [Switch AI coding tool](https://agentj.app/docs/switch-harness/).
Want commands of your own? Tap `/customize` in the list, or press and hold ≡: it sends the Agent a message from you asking to customize your slash commands; it lists the current ones, asks what to add, remove or change, and only switches the change on after it validates.

Before switching, the tool you use now writes the conversation's key points into a handoff file; the new tool reads it first, then carries on. If none was written the switch still happens, and the phone says "No handoff this time".

The menu also has the Agent's own skills. Other commands, such as changing settings, are for the
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
When none of your remotes is online, the card stays for 30 minutes instead: the Agent gets on with other work and runs the step once you approve.

E-mail: mail to yourself, or to people who are all on your e-mail allowlist, goes out without a card. On a card for
anyone else, each new recipient has an unticked "Don't ask again for mail to them (add to the allowlist)"; tick it and
hold to approve, and they join the allowlist — their mail is handled directly and mail to them no longer asks you.

In a shared session a card marked "Shows passwords or keys" would print a credential on the screen, and one marked "Changes passwords or keys" would change it; a program reading `.env` by name, or a note that mentions `.env`, no longer asks. For the stricter old rule run `agentj config approvals strict` on the Agent J host (`agentj config approvals standard` switches back). Cards use only your language setting.

Only real danger asks: the five kinds ask on a clear match (`git push`, a deploy, a payment or messaging API, deleting cloud data, writing a credential file); writing an ordinary note (words like deploy or key inside a heredoc don't count), `--dry-run`, `list`, `status`, `systemctl --user` for your own background jobs and `sleep` wait loops don't. A command Agent J can't read clearly (the program is a variable like `$PY`) is first checked by Jev when it is set up (`OPENROUTER_API_KEY` on the Agent J host): at or above "Jev threshold" in Settings (default 0.70, 0.50–0.95) it runs without a card and is logged in Activity. The threshold never affects the five clear dangers — they always ask. Too many cards? Tell the Agent; it explains and runs `agentj config set approval.jev_threshold 0.6` (one confirmation card on the phone).

Goal mode (`/goal`): tap `/goal` in the ≡ menu and add what to finish and by when. The Agent first sends one opening card with the goal, how you'll know it's done, the deadline and the dangerous actions it needs (by kind, with limits, e.g. "push to repository X", "spend: one domain only"); approve it once and those actions stop asking for the rest of the goal, each one logged in Activity. Outside the grant, a step the Agent J host can undo (including a new file copied with scp / rsync to a machine named in your `~/.ssh/config` that isn't there yet) shows a 10-second countdown card that counts as yes if you don't tap; anything that can't be undone or looks like an attack waits for you and is listed in the result card at the end. Cards that wait for you say in plain words who wants to do what, why it wasn't approved automatically, what happens if you don't tap (the step is skipped, everything else goes on) and how to approve; the raw command is folded under "Details". While the goal runs, pressing and holding "Hold: approve and add to the goal's grant" also lets the same kind of step through (up to 20 times, ending with the goal); the Agent can ask for one more grant with `agentj goal grant`, one card. `/goal status` shows progress, `/goal stop` withdraws the grant at once; a goal ends after 8 hours by default. For a company or team, switch off "Goal mode: approve in 10 s" under Settings → Safety.

Approval level: "Approval level" in Settings → Safety has three settings. Safe asks more — unclear commands, batch approvals and goal-mode countdowns all wait for your tap. Standard is the default and works as before. Full access is like Claude Code's `--dangerously-skip-permissions`: almost no cards, everything logged in Activity, while suspected attacks (downloading and running included), passwords or keys (sending them out included), spending and price changes still ask one at a time. The same page has "Goal mode: no cards" (off by default): when it's on, your own `/goal` starts without the opening card and runs without cards, the red lines still ask and the result card still comes; a goal that spends money or changes prices still shows the card; it counts only for the phone that turned it on, and switching it off ends a goal that started without a card. Both change only with a signed confirmation on a paired phone (plus Face ID when loosening, if the phone has a passkey), never by the Agent, and Full access written into the configuration file on the Agent J host works as Standard; if the Agent runs `agentj config set approval.level …`, you just get a confirmation card. More in [On the phone: approval level](https://agentj.app/docs/phone/#approval-level).

No remote online: the card stays on the phone and the lock-screen alert goes out as usual. With Telegram or the e-mail channel set up, one content-free line also reaches your private Telegram chat and your own inbox (at most once every 15 minutes). After 20 seconds the Agent knows you're away and does other work; the card stays for 30 minutes, and once you approve, the same requester's next try of that step runs; "Stop everything" cancels an approval not yet used. More in [When no remote is online](https://agentj.app/docs/phone/#no-remote-online).

（Screenshot：Tucked away: a small tag stays at the top — https://agentj.app/docs/manual/approval-later.en.webp）
### Question card (purple)

The Agent wants you to choose: tap an answer and confirm. If none fits, cancel the question and type your answer. The card works whichever Agent runs on the Agent J host — Claude Code, Codex, OpenCode, Kimi or Qwen (`agentj ask`): single or multiple choice, sometimes with a short preview under an option. Pick "Other (I'll type it in the chat)", or "OK, I'll answer in the chat" on an open question, then type your answer in the message box. If the card did not really appear, the Agent never says it did. The words under an option show two lines; tap "… More" to read the rest. The Agent keeps option names to about 24 letters (12 Chinese characters) and the words under them to 120 (60).

### One-tap answers: the quickest way to reply

A reply sometimes ends with a few rounded buttons, such as "Dispatch" and "Not yet". One tap is the same as typing those words and tapping Send: the message goes out at once as yours, and the Agent carries on. The group then greys out and says "Replied: Dispatch"; each group can be tapped only once. It also greys out when you have already typed an answer, or tapped on another remote. Talking about something else in between does not close it: page back and tap, and it goes as a reply that quotes that message, so the Agent knows which question you answered. When one reply has several groups of buttons (each line or list item is a group), you answer each group on its own; tapping one does not affect the others.

Buttons or a card:

- **One-tap buttons**: the Agent only needs a quick decision, with a few options (2–4), and it is not urgent; the Agent does not stop to wait.
- **Question card (purple)**: the Agent must have your answer before it can go on, or it needs several choices, previews or your own words.
- **Approval card (orange)**: a dangerous action, approved with a long press as before.

Buttons appear only in the Agent's own words: anything that looks like one inside a quote block, in code, or in a system notice or scheduled-task result from the computer stays plain text and sends nothing.

A `#tag` in a reply (such as #release) opens Search chat history with every message that carries that tag.

### Admin-password card

When the Agent needs to run one command as administrator (installing a system package, say), this card shows first
what it's for, then the command and its effect. Type the Agent J host's admin password on the card. It travels encrypted and is used for that
one command; **the Agent never sees your password**.

（Screenshot：Admin-password card — https://agentj.app/docs/manual/sudo.en.webp）
The Agent J host can also remember it: tick **Remember it with Face ID (next time, just scan your face)** on the card once, and next time tap **Fill in the computer password with Face ID**. It is stored only in encrypted form on the Agent J host, and each device unlocks it with its own Face ID; on another device, tap **Use it on this device too** under Settings → Safety → **Computer password**. What this protects against, and what it does not: [Computer password](https://agentj.app/docs/web-terminal/#computer-password).

### Key card

When the Agent needs an API key, this card says which key, what for, and where it will be stored. Paste the key; it is
written straight there — **not into the chat, and the Agent never sees it**.

（Screenshot：Key card — https://agentj.app/docs/manual/secret.en.webp）
Tapping save (or Enter) with nothing pasted just asks you to paste the key first — it never counts as a no. "Don't provide"
sits on its own line below and needs a second tap. The card stays valid for 10 minutes, even if the Agent's command is
interrupted meanwhile: submit as usual and it is saved.

### Getting your own keys or configs on the phone (pickup card)

To use something set up on the Agent J host on your phone — a proxy ss:// link, a config file with a password — just ask the
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

### Messages from your automations

Your own scripts and timers can post a page to the phone with `agentj notify`. The page is titled "From your
automation:" plus the automation's name; its images and files open as file cards. Each automation is off until you turn it on — in Settings under Automation
messages, or `agentj notify --allow <name>` on the computer. A long task your Agent makes decides this when it is created;
you can switch it in the same place; that page also has a master switch. Your Agent also gets a copy of each message
and treats it as information from a script, not as your instruction; with nothing to add it stays quiet. Your Agent itself
can never post this way. See
[Long tasks](https://agentj.app/docs/long-tasks/).

## Stop everything

In Settings → Safety, tap "Emergency stop all Agents" and confirm: the current work stops at once, every pending approval is denied, batch
approvals are withdrawn and scheduled tasks pause. The screen turns grey with a banner on top until you resume.

（Screenshot：Everything stopped — https://agentj.app/docs/manual/stopped.en.webp）
## Settings

（Screenshot：Settings — https://agentj.app/docs/manual/settings.en.webp）
The gear in the top-right corner opens Settings, full screen (on a computer browser: `s`, or `⌘,` / `Ctrl+,`; Esc
closes it). It is the only menu: Agent & remotes (the Agent's name, Remotes, Face ID, unpairing); Emergency repair (below); Agent tools (Models & API keys,
Memory — rules the Agent keeps, you can delete them —, Activity — what it did recently and who approved what —, Schedules,
Friends, Bots, the first-run checklist); language and appearance; reply text size, send sound volume, auto-read, wake word;
lock-screen alerts; Channels (re-pair Telegram, Email, Contacts — your friends' e-mails and phones, one tap onto the e-mail allowlist — and My contact details); safety options (high-risk warnings, session mode, isolation, Docker); the web app (how updates happen, upgrade Agent J on the Agent J host,
versions, update and reload, Add to Home Screen, keyboard shortcuts); account (with the Daily AI News email switch), help, and the security, privacy and terms
pages. The phone must be connected to the Agent J host to change settings.

Message box buttons (under language and appearance): the five buttons above the message box (File, Photos, Camera, the mic and Expand full screen) can go in any order — put the mic on the far left, say, to talk with the phone in your left hand. On a phone, hold a button for a moment, then drag it; or tap one, then tap where it should go. On a computer, drag it with the mouse, or focus it and press ← →. The new order shows at once and follows you to every remote; when the Agent J on the Agent J host is too old for that, it is saved on this device only. Restore default puts back the usual order. The Agent can change it for you too: `agentj config set ui.composer_buttons '["mic","file","image","camera","full"]'`.

Every menu — Settings and the pages it opens, ≡ All commands, keyboard shortcuts, silent history, "How safe is this web app?" — is a full-screen dark page with the same header, icons and buttons. The back arrow at the top left does what Esc does: one level up, so a page opened from Settings returns to Settings. Confirmations and risky actions still ask in a centred card.

### Emergency repair

Settings → Emergency repair is the second item. For no replies, follow the five-step phone-only section above. For a grey or offline phone, use the steps below:

- **① Reset connection**: tap this first. It reconnects, finds your conversation again, starts a new conversation if it has to, and as a last step restarts Agent J on the Agent J host; it stops at the first step that works and shows each one. A green card saying "Fixed. You can go back to the chat." means done. If it started a new conversation, the earlier one is kept: ask the Agent in the chat to bring it back. `/reset` in ≡ does the same.
- **② Ask Agent J Father for remote help**: when ① didn't help. the Agent J host checks itself, removes private details on the Agent J host and sends the report to Agent J Father; you get a ticket number. Support can only suggest a few fixed repairs, and the card shows each one the Agent J host runs. Nothing goes out until you tap.
- **③ AI account signed out**: for a Claude / Codex login that expired; it opens AI login rescue (next section).

When Agent J on the Agent J host stops answering, a small rescue helper there answers instead: a banner says "Agent J on the Agent J host isn't responding · rescue helper online", and ① and ② still work. The offline card has an **Emergency repair** button too. If the host stays unreachable, open [the support chat](https://support.agentj.app) on your phone. See [Phone grey or disconnected?](https://agentj.app/docs/reconnect/).

Under Emergency repair there is also Commands on the Agent J host: terminal commands that run on the Agent J host, such as `agentj doctor`, `agentj doctor --reconnect` and `agentj status`, each with Run. Tap it, confirm with Face ID, and the Agent J host runs it; the result shows below, with anything that looks like a password or key hidden on the Agent J host first. Every run is recorded. Tap × at the right of the result heading to close the result. Only commands that look or repair the connection are here; a phone without Face ID set up cannot run them.

### Web terminal

Settings → Safety → Web terminal gives you the Agent J host's command line in a computer browser: you type a whole line, the host runs it and the output shows in the browser. It works only in a computer browser; a phone says only **For safety and convenience, the web terminal can only be used from a computer browser.** It is off by default. In a computer browser, tap **Switch on and open**, confirm with Face ID and type the Agent J host's login password once: it is switched on for this browser and opens full screen right away. Or run `agentj terminal enable` in the host's terminal, check the 6-digit match code and confirm with Face ID on a remote that has it. The Agent and scripts cannot switch it on. A device without Face ID gets **Set up Face ID** first. After that, every tap on **Open web terminal** needs Face ID; while no computer login password is remembered, you type it once in the terminal page, encrypted before it leaves. A session lasts at most 15 minutes (up to 60) and closes after 5 minutes without input, after 60 seconds with the browser on another tab or minimised, or when the connection drops; it is recorded in full, and the recording stays on the host for 30 days. Never type passwords into the terminal: `sudo` asks for Face ID instead. Risky lines need Run plus Face ID once more — that only catches slips. It does not work while the connection is down; use Emergency repair above then. It works on Linux, macOS (zsh by default) and WSL hosts. In an emergency, scan-pair a friend's computer browser and unbind it in Remotes afterwards; its terminal closes the moment you do. Friends, web-version remotes, the three ways the password is kept and what each protects against: [Web terminal](https://agentj.app/docs/web-terminal/).

A message that starts with `!` (for example `!df -h`) runs as a line in the web terminal instead of going to the Agent as chat: a result card shows the output, then the command and its output go to the Agent. The terminal opens full screen; click the ✕ at the top right, press Esc or drag the handle at the top down to collapse it, and it keeps running behind **Terminal · N min left** above the message box. The Agent can open the terminal with a command filled in (you check it and press Enter yourself) and can read its output. That output, like the output of `!` commands, goes to your AI model provider after an attempt to hide common passwords and keys, so do not print secrets there. Details: [`!` commands](https://agentj.app/docs/web-terminal/#bang).

### Models & API keys

"Models & API keys" in Settings keeps the model picker at the top and two sections below it. "Base models" shows one dashed card per provider (Anthropic, OpenAI, Volcano Ark, Alibaba Bailian, Kimi, DeepSeek and more), each key tagged Subscription, Pay-as-you-go or Sign-in; "Third-party API keys" lists the other keys on the Agent J host (ElevenLabs, for example). A row shows only the name and "Not set" or a mask (at most the last 4 characters) — the key itself is never shown on the page. The icons on the right are Copy, Edit and Delete; an empty row only has Add, and "Add" at the bottom lets you name a new key first. Copy and Delete need Face ID first, and Delete asks once more; Copy puts the value straight on the clipboard. Edit and Add open the ordinary key card, so the Agent never sees what you type. Every copy, delete and edit is written to the audit log — the name, never the key. Keys already on the Agent J host are listed too: the systemd user environment, `~/.config/environment.d/`, and any .env file you list in `api_keys.extra_env_files` (none by default; just tell the Agent which file to add). Those rows say "Already on this computer · from X" with at most the last 4 characters; "Move into Agent J" plus Face ID copies the key into Agent J's own key file on the Agent J host, and from then on that copy counts — the old source stays unless you tick the box to comment its line out. Symlinks, other users' folders and files others can change are never read; the bottom of the page lists the places checked and any skipped. The Claude and Codex sign-in rows say which login is really in use: a subscription token (`CLAUDE_CODE_OAUTH_TOKEN`) wins over the account sign-in in `~/.claude/.credentials.json`. After installing, fill your keys in here and you can leave the terminal.

### AI login rescue and renewing before expiry

When the Agent J host's Claude Code / Codex login expires, the Agent cannot reply. Open Settings → Emergency repair → ③ AI account signed out → AI login rescue and sign in
again from the phone, without going back to the Agent J host: Codex shows a link and a device code; Claude Code shows an
authorize link, and its code goes only into the secret card.

With a long-lived Claude token, Agent J writes down the day it stored the token and counts one year from it. The section
shows how many days the Claude login has left. On day 7, 3 and 1 before the expiry you get one reminder each: a note in the
chat, a push, and a red banner at the top of the chat. Tap the banner (or Renew now in the section) → passkey → open the authorize link
in the phone's browser and approve → paste the code into the secret card. The new token replaces the old one as soon as a
few-second live check passes, and a new one-year countdown starts; if the check fails, the old one stays and you approve
again. The authorize link lasts 10 minutes (the page counts down); when it expires or the code does not work, the Agent J host
opens a new link by itself (up to 3 rounds). The link goes only to paired phones, never to Telegram.

Subscription /login and Codex's ChatGPT sign-in renew themselves, and provider keys usually do not expire, so they have no
fixed date. Once a day the Agent J host sends each signed-in AI one very short, tool-free test message. If the login no longer
works (for example you signed out of all devices on the web), you are told at once to rescue it; running out of balance or
hitting a rate limit never asks you to sign in again.

## Remotes

At the bottom of Settings, "Download Agent J Brand Kit" opens the [official Brand Kit](https://agentj.app/docs/robots/#brand-kit) on the
website in a new tab: the main logo and the 16 robots as transparent PNGs (256, 512 or 1024 pixels; 512 is recommended),
ready to use as the avatar of your Telegram bot, your email or any other bot.

（Screenshot：Remotes — https://agentj.app/docs/manual/remotes.en.webp）
Settings → Remotes is its own page: how many of the 10 remotes are in use, every paired phone or browser (online or not,
when paired and last used, how it was added; this one marked "This one"), Rename and Unbind on each row, and "Add a remote":
a one-time QR / link for 5 minutes. Compare the six digits shown on the new device and the paired remote’s confirmation card, then approve on a paired remote.

The scan icon at the top right (or holding the camera button) reads QR codes: a new device's pairing code adds it as a remote (both screens show the same 6 digits; tap "They match — add it" only if they do); any other code goes into the message field as text, never opened or sent by itself. A new device needs no computer: open [https://m.agentj.app/](https://m.agentj.app/) and the code at the top is for a paired phone to scan. The "Web app" tag now lives in Settings → "How safe is this web app?".

## Keyboard shortcuts

（Screenshot：Keyboard shortcuts — https://agentj.app/docs/manual/keys.en.webp）
On a computer browser or a tablet with a keyboard, press `?` for the full list (it is in Settings too); to find one, tap the magnifier at its top right or press `/` and type a word. Every tap, hold and swipe with its key: [Keyboard shortcuts and gestures](https://agentj.app/docs/shortcuts/). The ones you will use most:

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
| `Alt+Shift+K` · `⌘+K` / `Ctrl+K` | Search the page you are on: the chat history in the chat, the cards on Workflows / Skills / Memory / Agent J, the text of Settings, a menu page or an open document (inside the message box Ctrl+K still deletes to the end); press again to clear and close |
| `i` · `Esc` | Into the message box; Esc in it leaves (the draft stays) |
| `/` | Commands: / outside the message box jumps in with / typed and the command menu open; not while a dialog or Settings is open |
| `a` | Pick a file for the Agent |
| `p` | Pick pictures from the photo library |
| `c` | Take a photo |
| hold `m` | Hold to talk, release to send (slide out to cancel); keyboard m still releases to text |
| `Esc` `Esc` | Esc twice outside the message box clears text and attachments (✕ clears the text only, with undo). Esc never interrupts the Agent; to stop the current task, use the Stop button or send /stop |
| `⌘+Enter` / `Ctrl+Enter` | Approve, the same as holding the approve button (identity checks as usual); the card wins over the message box |

## Pairing and Face ID

**Start on any device by opening [https://m.agentj.app/](https://m.agentj.app/) in its browser.** The unpaired page displays a QR code. Scan it with another paired, connected remote, compare the six digits on both screens and confirm. The new device joins the conversation without visiting the host, and does not need a camera or Face ID. If its browser cannot generate this code, use scanning or a pasted link below.

For your first remote, run `agentj pair` on the host. Scan the host's code using the scanner inside the new device's page, then enter the six digits and remote pairing password at the host's prompt. Without a camera, run `agentj pair --link`, copy its pairing link into the new device's link box and start pairing. An online host linked to your account can also add a remote from the account page. See [Using your phone](https://agentj.app/docs/phone/) for the full steps.

An existing remote can also generate a code or copy a link from Remotes in Settings. Have the new device scan or paste it, compare the match codes and approve on a paired remote's confirmation card.

> **Screenshots pending:** the previous pairing images omit the new-device QR code and comparison on both screens. They are not shown as examples of the current interface; follow these steps and the live page.

### Pairing links and reconnecting

A pairing link is a key: it works once and expires after five minutes. Never send it through chat, a group or email, or give it to an AI. On iPhone, add the page to your Home Screen first, then scan or paste inside that icon. Opening the link with the system camera pairs a separate Safari page.

Opening the same browser or Home Screen icon normally reconnects automatically, with rotating renewal credentials where available. Recovery is not guaranteed after clearing website data or credentials, expiry, or unpairing on the host. See [Reconnecting](https://agentj.app/docs/reconnect/) and [Privacy](https://agentj.app/docs/privacy/).

After pairing, you can save a remote passkey: Face ID on iPhone, or fingerprint or system unlock on other devices. When the pairing page offers Face ID reconnect, verifying restores the existing device slot without a new scan or host approval. A passkey synced to another device can replace the original device's slot. Unpairing on the host invalidates this route.

### Remotes without Face ID or passkeys

A device without passkey support, or without a saved passkey, can pair by scanning, displaying its code or pasting a link. It can read conversations, send messages and attachments, use voice, approve ordinary operations with its signing key and manage remotes. This reduced capability does not make it read-only.

It cannot reconnect using a passkey or complete operations that explicitly require one: picking up a secret, copying or deleting API keys, importing keys discovered on the host, running host commands in Emergency repair, or approving a launch without isolation. Use a remote with a saved passkey, or set one up on a supported device. Administrator authorization and secret-entry cards still work as their prompts allow; a device that already has a passkey must verify its user for those cards and cannot skip verification to fall back.

A browser that also lacks signing support cannot approve operations or send controls that require signatures. The page asks you to update your browser and pair again. Ordinary signatures and passkeys are separate capabilities.

## Things one sentence does

Agent J is the only Agent you deal with, so most things are a sentence, not a button:

- "Pick up where we left off yesterday", "Did that report get finished?": it searches the Agent J host's history and
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
- Want the Daily AI News email every morning: say so and it runs `agentj news on` (the same switch as in Settings, see [Daily AI News](https://agentj.app/docs/account/#daily-ai-news)). The email goes to your own address; with no email yet, it asks you for one first and confirms it with the 6-digit code from the email.

Silent turns do not take up chat pages. Open ≡ → View silent history to read them. Esc or the backdrop closes the read-only panel; /clear clears it too.

## Add a remote for another computer

Once the target Agent J host is installed, account-bound and online, find its seat card on your account page and choose Add a remote. Confirm it's you, then scan with the phone. The account page automatically shows the waiting remote, six-digit code and Approve; compare and approve. Password fallback confirms again with a fresh sealed proof for that exact device and handshake; the target Agent J host's terminal isn't needed. Confirm either way: with your passkey (none on this device? choose Use a phone or tablet in the prompt and confirm on your phone), or with that computer's remote pairing password. After a passkey confirmation, the same session doesn't ask again for ten minutes; the QR expires after five minutes and adds one device. Pending devices can also be reviewed and approved on the seat card. Under More on the seat card, See what the Agent J host is waiting on you for is only for two cases: resuming after Stop everything, or enabling a scheduled task the Agent created. It has nothing to do with seats or billing; an unpaid bill never stops the tasks on the Agent J host.

Account-page pairing is on by default. The target Agent J host’s owner can disable it with `agentj remote-pair off`. Offline hosts are never queued; turn them on and try again. Older hosts must run `agentj update apply` or update from a paired phone. Devices record “Added from account page” and other remotes receive a notice.

This route trusts the signing control plane. Pairing material is encrypted to an ephemeral browser key and its plaintext is never stored by the backend; compromise of the signing service could still add a device. The remote pairing password is stored only on that computer, as a hash. When you type it on the account page, the browser first encrypts it to a one-time public key that computer just handed out; only that computer can open and check it. Our servers pass on ciphertext they can't read and keep nothing. Five wrong tries in a row lock it on that computer for a while.


For Codex failures, follow the phone guidance: tap the top model name to replace an account-unsupported model (temporarily disabled until an account refresh); check your proxy/VPN for ChatGPT connection failures; run `codex login` on the Agent J host when login expires. Redacted native errors remain under Details.


### Shared Codex model and provider (0.16.4a1)
For a phone turn, an explicit `agent.model` / `agent.effort` or the latest phone selection is sent as `turn/start.model` / `effort`. Without an explicit phone choice, Codex keeps the thread choice. A desktop turn uses the desktop actor’s own last explicit selection; Agent J only observes it. The top bar shows the active actor’s model, not a promise that a queued phone choice is already running. Selecting Default sends the current native defaults once.

Agent J reads effective native `model_provider` from Codex `config/read` and names it as `thread/resume.modelProvider` when the phone resumes an existing thread. It never copies provider keys or rewrites your Codex configuration. If a different program holds the writer, the phone stays read-only and identifies the program/PID when known. Quit that program before retrying.

For older Agent J versions that keep an old model/provider, start a new Codex conversation in the same folder using your current native defaults, then let Agent J follow the new conversation (clear any explicitly selected old shared thread). Or switch Agent J to an independent session. A new conversation has separate history. Do not ask the owner to paste keys into chat.


Shared Claude makes the phone-message setting Claude Code really uses in the work folder accept (organization policy > the folder's private `.claude/settings.local.json` > its `.claude/settings.json` > `~/.claude/settings.json`), at selection, startup and upgrade, backing up the file first; the phone is told once. A hold in user or private local settings is changed where it is; a hold in the shared project file is overridden from the private local layer. Organization policy, `refuse` and an explicit `agentj config claude-inbound off` are only reported. Enabled: phone messages no longer need individual confirmation on the Agent J host; to disable, run `agentj config claude-inbound off`. A session started before the change needs /clear or a new session. Only Claude Code is affected; Codex/OpenCode have no equivalent mechanism. When a message is not acknowledged, the phone names the reason (organization policy, project settings, turned off, needs /clear, computer busy); check the Agent J host before resending. The phone's model/quota bars (`agentj config claude-statusline`) are turned on at the same moment unless the owner turned them off, chained at the status-line layer in effect; `agentj doctor` reports a layer that hides them.

Shared mode: Claude `/clear` and `/compact` wait for the current desktop turn and confirm native hook receipts. A successful clear archives phone pages and starts at zero, including clears typed on the desktop. Context uses only new-session/post-compaction measurements; pending readings show “—”, never the previous value. A timeout reports an unconfirmed change; check the desktop before retrying. Shared Claude has no native undo-clear button: history remains archived locally; use desktop `/resume` to restore a conversation. Independent mode retains undo. Attached Codex clears from the phone with `/clear`: the next phone message opens a new Codex conversation, the phone archives its earlier pages as usual, and Undo clear works until that message is sent. Once the new conversation is open, keep chatting on your phone; no extra action is needed. An optional continuation section at the bottom of the clear-success card stays collapsed by default; no second message is sent. Expand it to copy a command only if you want to continue the same conversation in a terminal or the matching app on the Agent J host. The command follows your current AI tool, Claude Code, Codex or OpenCode; it is hidden when no resume command is available. While the desktop Codex is mid-answer nothing is cleared; clear again when it finishes. Attached OpenCode supports `/compact`; use desktop `/new` and select the new session to clear. Agent J's own Claude PTY accepts controls directly; an existing terminal requires an exact-session Herdr input route, otherwise the phone explains the reason and native command. Shared `/context`, `/usage`, `/status` and `/model` without an argument read measured data; unavailable values show “—”.

The 5h line remains visible at 1%; 0% has a start marker. No text beside it means no reported measurement. Expanding the input card reduces the reply reading area while keeping the action row and composer fixed; long inputs and replies scroll within their cards.

## Shared sessions

New installs use an independent session: Agent J starts its own and leaves your terminal tools alone. Sharing the session you already have open on the Agent J host is optional — Settings → Session mode, or `agentj config set agent.session_mode shared` (back: `… independent`).

When you enter or switch to a shared Claude Code, Codex or OpenCode session, the phone shows a one-time notice that fades after 3.5 seconds. Refreshing or reconnecting to the same session does not repeat it. Read-only conflicts, failed sends and requests that need your action remain visible.

Which session the phone talks to: by default the Claude Code session open in your work folder. If several are open there, run `agentj agent claude` on the Agent J host: it lists them with their names and first messages and lets you pick one. Nothing changes on the phone.


The pairing page offers browser and storage guidance. Use a regular Safari / Chrome window, add to Home Screen and pair from that same icon. The menu retains Set up Face ID after Later; Copy page link excludes the one-use pairing secret.


## Sharing screenshots

See [Android sharing and the iPhone clipboard Shortcut](https://agentj.app/docs/share-screenshot/). Shared content enters the composer; add a note before sending.

## Updating the phone page

Host and page updates are checked separately. In other cases, when a newer web version is detected, a square opens in the middle of the screen: **Server upgraded**. Tap **Refresh** to use the new version; **Later**, ESC or a tap outside closes it, and you can still tap the settings icon at the top right → **Update and reload** later. When the host and page server both have the same newer version, the page reloads after ten seconds without input, preserving the draft; recording and unsubmitted card actions block it. Otherwise only offer a refresh. Keyboard: `Alt+Shift+U` upgrades the host (phone confirmation unchanged); `Alt+Shift+R` updates and reloads the page.


When a daily, weekly or monthly model allowance runs out, the phone tells you to wait for its reset or upgrade your plan with the model provider. A temporary rate limit still asks you to retry later. Provider error details are never displayed.

If shared Claude completes a reply while your phone is disconnected, reconnecting updates its existing input page. Reloading first fetches the current computer history before claiming that a message is still waiting. `/update` receipts use the current language setting, including changes made while Agent J is running. `agentj doctor` includes the websockets version for troubleshooting.

Short network blips are repaired quietly: when the phone's connection drops it is usually back within a second, and the page only turns grey with "Connection lost. Retrying in N s…" if reconnecting takes longer than about 6 seconds. A busy computer that answers late is not treated as a disconnect. If your Telegram channel can't reach Telegram three times in a row (or for two minutes), one line under the chat says so and disappears by itself once Telegram works again. For the first minute after Agent J starts, `agentj doctor` shows the relay as "connecting" rather than a problem.

Local read-aloud uses only voices on this phone. Host/cloud voices stay in `voice.tts.voice`; choose a phone voice separately with `voice.tts.phone_voice` (empty selects the local default for the reply language). Switching modes keeps both choices. An unavailable phone voice falls back with one notice. On first opening, iPhone waits for local voices to load before reporting that no voice is available.


Shared Claude ingress: startup and doctor repair the effective writable layer to accept; managed policies are read-only. Sessions started before the change need a new session or /clear on the Agent J host. Shared startup re-enables ingress; check the desktop before resending unacknowledged messages.


0.18.1: Valid older Telegram enrollment continues with its local bot environment name. Huawei HarmonyOS devices have accurate names; account owners can rename a remote with a passkey while its host is online.

Renaming a remote: press and hold the source at the top left of the input card (half a second) to rename the remote in your hand; other remotes and the account page show the new name right away. Only the owner can rename other remotes, on the account page. A remote the owner renamed shows that name and its device type as the message source (for example "Alex (boss) · iOS Safari"), marked "(name set by the owner)"; a tap shows the name the device gave itself. Remotes nobody renamed are marked "(name set by the device)". The Agent uses the owner's name to tell who is speaking, but the name is only a label, not proof of identity. A double tap at the top left still goes to the first message.

## The first message is slow and the phone shows nothing

On a new computer it is usually one of four, and the phone says which: the model service cannot be reached (Claude Code retries for about three minutes; the phone shows a card when it is still failing after about 10 seconds); bubblewrap is missing (every start shows a "Sandbox unavailable" card, whose "Run this launch without isolation" button needs the owner's passkey; install `sudo apt-get install -y bubblewrap socat` on the Agent J host); running as root with bypassPermissions (Claude Code refuses to start; Agent J starts it in the default permission mode and says so); a model the provider does not serve (400; sent again once on the default Opus 5.5 · medium).

`agentj agent claude` (or codex) answers the tool's one-time setup ahead (onboarding, theme, folder trust, bypass confirmation) — only what is missing, no key read; the `first-setup` row of `agentj doctor` checks it. When the Agent really waits at a terminal prompt, the phone gets a "Handle it on the Agent J host" card quoting the terminal's words: answer it at the Agent J host, or run `agentj agent claude`, then send again.

## Sandbox failures and admin commands

If an independent session's sandbox cannot start, the Agent stays stopped. The phone card explains the cause (such as missing bubblewrap) and repair. Tap **Run this launch without isolation** and verify with the owner's passkey to allow this launch only; the next launch checks isolation again. This launch may access local files and credentials. Set up a passkey in the phone menu if needed. Denial or timeout never starts the Agent.

The sudo card labels what it's for and the effect as **Agent claim**, what it's for first; a purpose that is too short or only repeats the command, or a request to manage Agent J's own background service, shows no card. Shell or interpreter commands are marked red and require an admin password even with the passwordless helper installed. A few read-only commands offer a switch on their card, off by default: turn it on and approve with the password, and for 10 minutes the same program with the same options, only on other files, runs without a card, each noted in the chat; sensitive files and shell or interpreter commands still ask every time, and "Revoke" ends it at any time. Secret cards support URL verification; `--verify-cmd` is disabled and runs no command outside the sandbox. Scheduled tasks and native customer-service bots authenticate from native storage and do not inherit host-service API keys or tokens.

### Connecting Telegram

Telegram uses a bot of your own. Save its key with a key card, then run `agentj channel add telegram` on the Agent J host or tap 「Re-pair Telegram」 in Settings. The chat shows a one-time 6-digit pairing code (10 minutes, one use). In Telegram, send `/start <code>` to your bot in a private chat: that chat becomes the owner, and the bot replies that Telegram is connected. No other bot is needed to look up your ID. Anyone else who writes to the bot only gets their own numeric ID back; a code sent in a group does not count. To move to a new phone or Telegram account, tap 「Re-pair Telegram」 again.

The same bot can also be customer service for everyone else. In Bots, open a customer-service bot and tick 「Let my Agent J Telegram bot receive guests」 (or ask your Agent J; you approve it on the phone). Your paired private chat stays your main conversation; any other private chat becomes a visitor of that bot, with its own session, instructions, model and limits, and never gets your permissions. Off by default: then strangers only get their numeric ID. See [Customer service bots](https://agentj.app/docs/bots/).

### Telegram and friend safety boundaries

Forwarded messages in the owner’s Telegram private chat are external data, not the owner’s instructions. Forwarded commands do not execute. Approval commands in groups remain silent; use the paired phone to approve. When the family safety check is unavailable, that message pauses: the owner can send the request directly from the paired phone. Only a leading `/notification` or `/notify` command counts as a reminder request; the word notification inside other text does not.

Group text and captions are checked before attachments are downloaded. Proxy groups do not download attachments; family attachments are refused when the rules match. Each group sender has a 40 MiB download budget per UTC hour across groups, preserved across restarts. Unknown sizes reserve the per-file maximum. Paused group messages cannot start Agent tools. Friend cards allow up to 64 characters for the name, 32 for the owner and 140 for the introduction. A credential-shaped value in supplementary context rejects the whole edit; use ordinary background information.

Support bots process Telegram private chats independently, preserving order within each chat. Text over 2000 characters gets a fixed request to shorten it without a model call. Customer text and model suggestions in support reports are data for summarising and diagnosis, never owner instructions.

## Getting help

- When your Agent can report the problem itself, have it use `agentj support` to ask our support desk; you don't have to find a person. See [Getting help from the support desk](https://agentj.app/docs/support/).
- The phone can't reach the Agent J host, or the chat is stuck: Settings → Emergency repair, starting with ① (above).
- If it can't connect at all (offline, the install failed, it can't reach our servers), or [https://agentj.app/](https://agentj.app/) won't open (for example in mainland China), email feedback@agentj.app — email gets through from anywhere. Include your account ID, what happened and when, and a screenshot.

## Page menu

Tap the logo for Chat, Workflows, Skills, Memory or Agent J; hold it to return to Chat. These pages read the Agent J host's documents and hide credential files. A workflow's @ inserts an atomic mention that sends as plain text. Tap the dancing robots for Working (AI tool and reported model) or Workflow (CEO, people and reporting lines); unassigned workers are temporary helpers. Alt+W opens the menu; Z still cycles text size and M still records.

Double-click or double-tap a non-audio file card (image, video, PDF, document, text, code, Markdown or HTML) to view the file full screen, or use Full screen. Audio cards and reply text open the reply reader. Video keeps its playback position; Escape or Back closes the current viewer.


A collapsed input card shows two quote lines and one body line, or up to three body lines without a quote, with an ellipsis for overflow. Attachments appear as a line paperclip and total count beside “…” (images, files and audio together); zero attachments show no count. Double-click the body, tap “…” or press `o` to expand all text, the quote and attachments.

The Agent J Father robot is the official logo. Its body follows the five state colours; the Send button uses its white silhouette. Outside chat it dances to remind you that holding it returns to chat. Reduced motion uses a static logo and a subtle inset heartbeat outline. When changing the background robots, Agent J first asks whether you also want a new five-state logo set; leaving it out keeps the current set.

## When a goal needs a capability you have not set up

Agent J checks existing APIs, skills and its browser sign-in before explaining what is missing and how to supply it. It does not end with “I cannot do that.” The routes, in order, are:

1. **Official API.** For missing accounts, authorization or keys, it gives one sentence stating what you can supply, followed by a short list of providers, registration pages and approximate costs. After you agree it guides setup step by step. Keys go only in the phone secret card, never chat or the opening card. Service charges need separate approval.
2. **A website with no API.** Agent J can operate it with its own browser. It first checks browser setup and the site's sign-in. When needed it says “This site has no official API; I can operate it through my browser. Please sign in to X there once” and starts the login flow. QR sites open a phone login card; QR codes only appear there. Other sites open a sign-in page on the Agent J host. An uncertain check is reported as uncertain, never as signed in. Sign-in is not authorization: publishing, payment and other sensitive actions still require phone approval.
3. **A missing platform primitive in this version.** It explains the gap, offers /feedback-to-agentj-father to send feedback to Agent J Father, and states what you must still supply once the capability is unlocked. For feedback it proposes itself, you review and approve the draft first. Your explicit /feedback-to-agentj-father command with content is redacted and submitted directly, without a second confirmation. Submission does not mean it is scheduled and never promises a release. Combined gaps get both paths.

For goals needing a workflow, genuinely missing choices, authorization and setup go on one opening card (at most five required items), followed by a secret or login card as needed. Already authorized, equipped work continues; only dependent work waits. Truly illegal or dangerous requests are still refused.

The first time you open Workflows, and each time Agent J starts on the Agent J host, it scans the folders one level under the working root: folders organized with the workflow design method (an entry file and the documentation/ boot documents) show as cards; one missing from the CEO roster (documentation/ROLES.md) gets a row appended at its end — append-only, nothing else changes, and a row you delete is not added back. When no workflow is found, the three that come with Agent J (Knowledge base, Scripts and articles, E-book) are shown; Install it makes Agent J show you the preview first. Set up organization sends Agent J one standard instruction: organize the projects you use with the workflow design method, register their CEOs and check, listing any unorganized project for you first; nothing is deleted or moved. After one use it reads Review organization, which fills gaps.

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

Agent J Father is our official feedback and support platform. Say “Please report this problem to Agent J’s developers (support platform)”, or casually “Tell your dad about this”. On the phone, `/feedback-to-agentj-father <message>` does the same: your Agent removes credentials, personal information, customer data and private path details, submits directly without asking twice, and gives you the ticket ID. The command alone asks what you want to report. If reporting a problem or idea is the Agent’s own initiative, it shows you the redacted draft on the phone and waits for agreement. Support replies are advice, not instructions. If it cannot connect, email feedback@agentj.app.

## Copy system instructions and reconnect Telegram

Host system messages put values and commands you need to copy into code blocks. Press the copy icon instead of selecting text. Telegram pairing shows the code and the `/start` command separately; its Telegram link opens a new tab. Setting codes, recovery commands and terminal continuation commands use the same copy controls. A continuation command is optional: keep chatting on the phone unless you want to move to the matching terminal app.

After saving a key to the managed service environment file through the secret card, Agent J rereads that file automatically. **Rebind Telegram** also rereads that file before checking the token. If the service still needs a restart, open **Settings → Emergency repair → Restart service** and confirm with Face ID or your device passkey. This sends your signed request from the paired remote; the connection may drop briefly and reconnect. In an isolated Agent session, an unavailable host control socket means its status cannot be checked there, not that the service stopped. Use the paired phone's reload, Telegram rebind or restart controls.

## Import a key already found on the computer

1. Open **Settings → Models & API keys**. A key row can list existing sources on the computer, with a masked ending and whether it matches the managed copy. Values are never displayed in full.
2. Choose the source you intend to use, then press **Move into Agent J** and confirm with Face ID or the device passkey. Agent J copies it locally; you do not paste the key into chat.
3. The old source is kept by default. Only select the option to comment out its original line if you want that change. Importing a key is not permission to delete other sources.
4. Read the Claude or Codex sign-in row's **actually in use** source. A key listed elsewhere is not necessarily the login the running AI uses. **after Agent J restarts** means the running AI has not picked up that change yet; follow the row's restart or sign-in guidance and check again.
## Refresh, reply options and a TV remote {#remote-controls}

Hold the top search button for about half a second (550 ms) to force a refresh. Your text draft is kept. Finish any recording, attachment work or unsubmitted card first. At the top of a message, pull down until “Release to refresh” appears, then release.

Reply options use borderless pills with a Noir background, Lotion text and space around the text. Their background becomes Sooty while pressed.

On a large screen, the current focus is clearly marked:

- Up / Down moves vertically between cards.
- Left / Right opens the previous / next message, including in full-screen reading.
- `PageUp` / `PageDown` (CH± on compatible remotes) scrolls a long message by one screen.
- In the message area, press the confirm key `Enter` twice, less than 400 ms apart, to read full screen.
- Press the webOS Back key, `Escape` or `Backspace` to leave full screen. In text fields, cursor movement, selection and deletion keep their usual behavior.

A paired large screen without a passkey keeps the existing remote rules. Ordinary approvals keep their usual confirmation flow. Actions that explicitly require a passkey show "Please confirm on your phone"; complete those on your phone. The TV does not replace verification.

If the browser cannot record audio, use the text field with your system keyboard's dictation. Whether the remote's voice key supports dictation depends on the TV and browser; we have not tested it on an LG TV. Landscape 1080p has been checked in Chromium emulation, which is not physical TV acceptance.

## Preparing voice input for the first time {#voice-preparation}

While voice components are installed for the first time, the page shows the current download, unpacking, installation and self-test stages, without an estimated percentage. The microphone shows "Preparing" until it is ready.

A prominent "Ready" notice tells you to press and hold the microphone to speak; dismiss it with "Got it". If setup fails, the notice shows the reason and a "Try again" button. Check the network or disk space as advised, then retry. Notices do not expire automatically. You can dismiss them with ESC; a later notice can also replace them.

After reconnecting, readiness is checked again. Installation progress from the disconnected period is not replayed step by step.

At the top of a message, pull down until “Release to refresh” appears, then release to update and refresh. Text drafts are kept; recording, attachments and unfinished cards block refresh. The header robot follows the state colour unless you use a custom logo; the status bar and bottom safe area stay Sooty #141414.

After an automatic web update, a Noir notification appears for about 3 seconds, once per version on each device.

Shortcut search also matches synonyms, including screenshot, snapshot, 截图 and 截屏.

For each question, choose an option or write your own answer in the card (up to 2,000 characters). Writing replaces that question’s selected options. Confirm sends the signed answers to the computer; no extra chat message is needed. On older hosts, choose Other and answer in chat. Use the dedicated secret card for passwords and API keys, not ordinary question cards.

## Empty recordings and recovery

Recordings shorter than half a second or with too little audio data are discarded without a recovery prompt. Empty saved recordings showing 0:00 are cleaned up automatically. In the recovery dialog, Discard is on the left and Put them back is on the right. Empty audio attachments are blocked with “Recording too short. Not sent.” iPhone MP4/AAC recordings and WebM recordings keep their actual format.


## Continue from your phone

In Settings → Safety, tap **Emergency stop all Agents** and confirm to interrupt the main Agent’s current turn, stop Agent J background tasks, pause Agent J scheduled tasks and reject pending approvals. Esc cancels. Resume remains at the top; when this device has a passkey, resuming requires Face ID or a passkey. Emergency stop is no longer in the slash menu; `/stop-all`, `/stop_all` and `/estop` direct you to Settings.

After Reset connection in Settings → Emergency repair, tap **Reopen AI session** and confirm once. The host ends the current AI session process and resumes the same conversation; if that fails, a new session is connected to your phone and the old conversation is kept. Claude Code, Codex and OpenCode reconnect from your phone: no need to go to the computer. Native permissions stay in place. If the host cannot verify the selected process identity, it refuses to end it and offers remote help. Automatic retry after quota recovery is separate.

## Approval and execution cards {#approval-inset}

Permission requests and sudo execution status sit inside the current reply, aligned with its text. Scroll within a long card. Approval still requires the existing hold, signature or passkey check. A new request leaves reading mode or Settings and returns to chat; reading mode keeps both safe areas Sooty #141414.

Copy controls in Notebook, messages and code blocks briefly show ✓ “Copied” for about 1.5 seconds, with a centred notice; failures also show a notice. Informational notices such as voice input becoming ready disappear automatically, with no OK button. Copying in reading mode keeps the reader open.

### Icon holds and button states

Holding an icon or toolbar button runs its shortcut without selecting nearby controls or opening the system text menu. Reply text, messages and code remain selectable and copyable. Button labels stay readable on hover, while pressed, with keyboard focus and when unavailable. Unavailable buttons do not run actions.

Centred circular notices show one short status word: Sent, Copied or Saved (up to four Chinese characters). Longer explanations use a rounded rectangle in the same centre position, keeping the full text and the original automatic dismissal time. Both shapes use Noir with Lotion text in light and dark mode.

## Queued messages and quiet periods

Use Stop previous task on a queued message when you want to interrupt the earlier task. After three minutes both queued and without new earlier-task output, the page reports that quiet period; this does not prove the task is stuck. New output resets the timer, and a reload or reconnection starts a new observation. Tasks are never stopped automatically; Stop retains its existing handling of unsent messages.
