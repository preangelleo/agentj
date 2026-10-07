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
  small); hold it to reset.
- The small line at the top says the same state in words.
- The phone gives a short buzz whenever the state changes.

## Three lines: quotas and context

- **The thin line under the top bar** (marked weekly): how much of your AI plan's weekly quota is used. Longer = more used.
- **The thin line above the message box**: the 5-hour quota.
- **The rising water with waves in the background**: how much of the context — what the Agent can keep in mind at
  once — this conversation uses. Higher water = closer to full. After a compaction the water drops to the bottom.

Tap either line to see the exact numbers, for example week 58 %, 5 hours 34 %, context 64 %.

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

`/clear` wipes the whole conversation and starts fresh; it asks first and can be undone. To keep your progress, use
`/compact`, not `/clear`.

## One message per page — swipe through them

Each page shows what you said at the top and the Agent's reply below. Swipe right for older pages, left for newer ones;
1 / 1 in the corner is the page number and total.

The buttons under a reply, left to right:

| Button | What it does |
|---|---|
| Book | Full-screen reading (or double-tap the reply), with a text-size control |
| Speaker | Read it aloud with the phone's own voice — the text goes nowhere else |
| Arrow | Share it through the phone's share sheet |
| Two pages | Copy the whole reply |
| Curved arrow | Reply to this one; the quote appears above the message box |

Select some text in a reply to quote just that part.

Replies are laid out for you:

- **Code** is syntax-highlighted and scrolls sideways when wide.
- **Maths** (LaTeX) shows as formulas.
- **Diagrams** (mermaid) are drawn as pictures.
- **Images, audio, video, PDFs, web pages and other files the Agent mentions** show up right in the reply, as long as
  they are inside its working folder: tap an image to enlarge it, play audio and video in place, open PDFs, preview web
  pages in a safe frame (no scripts run there), and download or share other files to Files.
- Files that are too large, outside the working folder, or that may hold secrets (such as `.env` or `.pem`) are never
  sent to the phone — the reply gets one line saying why. Image links to the web are never opened automatically.

（Screenshot：A picture, a formula and highlighted code in a reply — https://agentj.app/docs/manual/rich.en.webp）
（Screenshot：Further down: a diagram, a file to download and a line about a file that was not sent — https://agentj.app/docs/manual/rich-more.en.webp）
## Sending messages

- Type and tap send, or press Enter. Until the Agent has it, you can cancel the send.
- You can send while the Agent is busy (blue); messages wait their turn.
- **It works offline, too.** With no network, send as usual: the message is kept encrypted on this phone and the page
  says "Will send when you're back online". When the network returns, queued messages go out in order, never twice.

（Screenshot：Two messages sent offline wait above the message box — https://agentj.app/docs/manual/offline-queue.en.webp）
- Type `/` at the start of the box to see the commands you can use.

### Sharing a Codex App session

While the Codex App holds a session, the phone follows its actual model from local records and shows read-only status. A native active-writer refusal leaves the message undelivered and never automatically starts another thread. Quit the Codex App completely, then resend to try continuing the same thread; switching to New chat or waiting for a reply does not guarantee release. Do not promise concurrent writes from separate clients, delete writer locks, inject the App’s private pipes or change permissions.

## Attachments: files, photos, camera

（Screenshot：Attachments and a quote above the message box — https://agentj.app/docs/manual/attach.en.webp）
- Three buttons above the message box: file, photo library, camera. You can also paste an image, or drag files in on a
  computer browser.
- Up to 10 attachments at a time, 25 MB each. Very long pasted text becomes an attachment automatically.
- Attachments travel encrypted straight to your computer, into the Agent's working folder, and are removed after 30 days.

## Voice: hold to talk, release for text

（Screenshot：Hold to talk: recording — https://agentj.app/docs/manual/voice.en.webp）
- Hold the microphone and speak; release and the words appear in the message box for you to check. Slide up to cancel.
- For a long take, double-tap the microphone to lock it; tap once more to finish.
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

The menu also has "Stop everything" and the Agent's own skills. Other commands, such as changing settings, are for the
computer.

The model and effort at the top: tap to switch to the next one, hold to go back to the default.

## Cards that need you

### Approval card (orange)

When the Agent wants to do something that needs your yes, the screen turns orange and a card slides up saying what it
wants to do. Tap deny to refuse; to approve, **press and hold** the approve button for about a second, until it fills.
No answer within 2 minutes counts as a no. You can tuck the card away; a small tag at the top keeps reminding you.

（Screenshot：Tucked away: a small tag stays at the top — https://agentj.app/docs/manual/approval-later.en.webp）
### Question card (purple)

The Agent wants you to choose: tap an answer and confirm. If none fits, cancel the question and type your answer.

### Admin-password card

When the Agent needs to run one command as administrator (installing a system package, say), this card shows the
command, why, and its effect. Type your computer's admin password on the card. It travels encrypted and is used for that
one command; **the Agent never sees your password**.

（Screenshot：Admin-password card — https://agentj.app/docs/manual/sudo.en.webp）
### Key card

When the Agent needs an API key, this card says which key, what for, and where it will be stored. Paste the key; it is
written straight there — **not into the chat, and the Agent never sees it**.

（Screenshot：Key card — https://agentj.app/docs/manual/secret.en.webp）
More in [Admin rights and keys from your phone](https://agentj.app/docs/phone-admin/).

## Stop everything

In ≡, tap "Stop everything" and confirm: the current work stops at once, every pending approval is denied, batch
approvals are withdrawn and scheduled tasks pause. The screen turns grey with a banner on top until you resume.

（Screenshot：Everything stopped — https://agentj.app/docs/manual/stopped.en.webp）
## Settings

（Screenshot：Settings — https://agentj.app/docs/manual/settings.en.webp）
The gear in the top-right corner opens Settings (on a computer browser: `s`, or `⌘,` / `Ctrl+,`): language, reply text
size, auto-read, wake word, appearance, lock-screen alerts, how updates happen; safety options (high-risk warnings,
session mode, isolation, Docker); the versions, the paired computer and phones, and unpairing. The phone must be
connected to the computer to change settings.

## Menu (top right)

（Screenshot：Menu — https://agentj.app/docs/manual/menu.en.webp）
The three lines in the top-right corner open the menu: Memory (rules the Agent keeps; you can delete them), Activity (what
it did recently and who approved what), Scheduled tasks, lock-screen alerts, language, appearance, and the security,
privacy and terms pages.

## Keyboard shortcuts

（Screenshot：Keyboard shortcuts — https://agentj.app/docs/manual/keys.en.webp）
On a computer browser or a tablet with a keyboard, press `?` for the full list (it is in Settings too). The ones you will
use most:

| Key | What it does |
|---|---|
| `i` / `Esc` | Enter / leave the message box |
| `j` / `k` | Previous / next page |
| `f` or double-tap the card | Full-screen reading |
| `y` `y` | Copy the whole reply |
| `r` | Reply to this one |
| `a` `p` `c` | File · photo library · camera |
| hold `m` | Hold to talk, release for text |
| `Esc` `Esc` | Clear the text and attachments; when already empty, interrupt the current task |
| `s` | Open Settings |

## Pairing and Face ID

（Screenshot：Not paired yet: scan the QR code on your computer — https://agentj.app/docs/manual/pair.en.webp）
The first time: scan the QR code on your computer, then type the phone's 6-digit code and your approval passphrase on
the computer (see [Using your phone](https://agentj.app/docs/phone/)).

（Screenshot：The 6-digit code during pairing — https://agentj.app/docs/manual/pair-code.en.webp）
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
- "I want to report a problem": it shows you what would be sent and sends only with your OK.
- On long jobs it sends you a one-line progress note now and then.
