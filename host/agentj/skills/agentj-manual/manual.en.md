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

The two commands in the "Friends" group are put into the message box when tapped; finish them and send:

| Command | What it does |
|---|---|
| `/my-agent-id` | Your computer answers with your Agent ID (tap the code block to copy it), the share link and an "Open my card" button; never goes to the AI, costs no tokens |
| `/add-friend ID [note]` | Add a friend: signed by this phone like "Add friend" on the friends page; a mistyped character is caught at once; without an ID it opens "Add friend" |

The menu also has "Stop everything" and the Agent's own skills. Other commands, such as changing settings, are for the
computer. See [Agent friends](https://agentj.app/docs/friends/).

The model and effort at the top: tap to switch to the next one, hold to go back to the default.

## Cards that need you

### Approval card (orange)

When the Agent wants to do something that needs your yes, the screen turns orange and a card slides up saying what it
wants to do. Tap deny to refuse; to approve, **press and hold** the approve button for about a second, until it fills.
No answer within 2 minutes counts as a no. You can tuck the card away; a small tag at the top keeps reminding you.

In a shared session a card marked "Shows passwords or keys" would print a credential on the screen, and one marked "Changes passwords or keys" would change it; a program reading `.env` by name, or a note that mentions `.env`, no longer asks. For the stricter old rule run `agentj config approvals strict` on the computer (`agentj config approvals standard` switches back). Cards use only your language setting.

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
| `i` / `Esc` | Enter the message box / close the current overlay; keep the draft when no overlay is open |
| `j` / `k` | Previous / next page |
| `f` or double-tap the card | Full-screen reading |
| `y` `y` | Copy the whole reply |
| `r` | Reply to this one |
| `a` `p` `c` | File · photo library · camera |
| hold `m` | Hold to talk, release for text |
| `Esc` `Esc` | Outside the message box: clear text and attachments; when already empty, interrupt the current task |
| `s` | Open Settings |

## Pairing and Face ID

（Screenshot：Not paired yet: scan the QR code on your computer — https://agentj.app/docs/manual/pair.en.webp）
The first time: scan the QR code on your computer, then type the phone's 6-digit code and your approval passphrase on
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
- "I want to report a problem": it shows you what would be sent and sends only with your OK.
- "Send my card to Kai", "What did Kai say lately?": it manages your Agent friends, see [Agent friends](https://agentj.app/docs/friends/).
- On long jobs it sends you a one-line progress note now and then.


- "Set up a weekly competitor brief" and other recurring work: it checks its capabilities and sends one start card for
  you to confirm, see [Long tasks](https://agentj.app/docs/long-tasks/).
- "What can you do": a capability page opens on your phone — what is ready and what needs setup.

Silent turns do not take up chat pages. Open ≡ → View silent history to read them. Esc or the backdrop closes the read-only panel; /clear clears it too.

## Add a remote for another computer

Once the target computer is installed, account-bound and online, find its seat card on your account page and choose Add a remote. Confirm with your passkey and scan with the phone. No target-computer terminal, passphrase setup or six-digit entry is needed. Confirmation is reused for ten minutes on the same session; the QR expires after five minutes and can add one remote. Review and approve pending remotes on that card too. Resume / enable scheduled tasks supplies owner approval for lifting Stop everything and enabling a specific task.

Account-page pairing is on by default. The target computer’s owner can disable it with `agentj remote-pair off`. Offline hosts are never queued; turn them on and try again. Older hosts must run `agentj update apply` or update from a paired phone. Devices record “Added from account page” and other remotes receive a notice.

This route trusts the signing control plane. Pairing material is encrypted to an ephemeral browser key and its plaintext is never stored by the backend; compromise of the signing service could still add a device. The local passphrase remains a local-only hash and is optional for bound hosts, available for offline/local approval and required on unbound hosts.


For Codex failures, follow the phone guidance: tap the top model name to replace an account-unsupported model (temporarily disabled until an account refresh); check your proxy/VPN for ChatGPT connection failures; run `codex login` on the computer when login expires. Redacted native errors remain under Details.


### Shared Codex model and provider (0.16.4a1)
For a phone turn, an explicit `agent.model` / `agent.effort` or the latest phone selection is sent as `turn/start.model` / `effort`. Without an explicit phone choice, Codex keeps the thread choice. A desktop turn uses the desktop actor’s own last explicit selection; Agent J only observes it. The top bar shows the active actor’s model, not a promise that a queued phone choice is already running. Selecting Default sends the current native defaults once.

Agent J reads effective native `model_provider` from Codex `config/read` and names it as `thread/resume.modelProvider` when the phone resumes an existing thread. It never copies provider keys or rewrites your Codex configuration. If a different program holds the writer, the phone stays read-only and identifies the program/PID when known. Quit that program before retrying.

For older Agent J versions that keep an old model/provider, start a new Codex conversation in the same folder using your current native defaults, then let Agent J follow the new conversation (clear any explicitly selected old shared thread). Or switch Agent J to an independent session. A new conversation has separate history. Do not ask the owner to paste keys into chat.


Shared Claude makes the phone-message setting Claude Code really uses in the work folder accept (organization policy > the folder's private `.claude/settings.local.json` > its `.claude/settings.json` > `~/.claude/settings.json`), at selection, startup and upgrade, backing up the file first; the phone is told once. A hold in user or private local settings is changed where it is; a hold in the shared project file is overridden from the private local layer. Organization policy, `refuse` and an explicit `agentj config claude-inbound off` are only reported. Enabled: phone messages no longer need individual confirmation on the computer; to disable, run `agentj config claude-inbound off`. A session started before the change needs /clear or a new session. Only Claude Code is affected; Codex/OpenCode have no equivalent mechanism. When a message is not acknowledged, the phone names the reason (organization policy, project settings, turned off, needs /clear, computer busy); check the computer before resending. The phone's model/quota bars (`agentj config claude-statusline`) are turned on at the same moment unless the owner turned them off, chained at the status-line layer in effect; `agentj doctor` reports a layer that hides them.

Shared mode: Claude `/clear` and `/compact` wait for the current desktop turn and confirm native hook receipts. A successful clear archives phone pages and starts at zero, including clears typed on the desktop. Context uses only new-session/post-compaction measurements; pending readings show “—”, never the previous value. A timeout reports an unconfirmed change; check the desktop before retrying. Shared Claude has no native undo-clear button: history remains archived locally; use desktop `/resume` to restore a conversation. Independent mode retains undo. Attached Codex cannot switch the desktop thread: use desktop `/new` and select the new thread. Attached OpenCode supports `/compact`; use desktop `/new` and select the new session to clear. Agent J's own Claude PTY accepts controls directly; an existing terminal requires an exact-session Herdr input route, otherwise the phone explains the reason and native command. Shared `/context`, `/usage`, `/status` and `/model` without an argument read measured data; unavailable values show “—”.

The 5h line remains visible at 1%; 0% has a start marker. “5h —” means no reported measurement. Expanding the input card reduces the reply reading area while keeping the action row and composer fixed; long inputs and replies scroll within their cards.

## Shared sessions

When you enter or switch to a shared Claude Code, Codex or OpenCode session, the phone shows a one-time notice that fades after 3.5 seconds. Refreshing or reconnecting to the same session does not repeat it. Read-only conflicts, failed sends and requests that need your action remain visible.

Which session the phone talks to: by default the Claude Code session open in your work folder. If several are open there, run `agentj agent claude` on the computer: it lists them with their names and first messages and lets you pick one. Nothing changes on the phone.


The pairing page offers browser and storage guidance. Use a regular Safari / Chrome window, add to Home Screen and pair from that same icon. The menu retains Set up Face ID after Later; Copy page link excludes the one-use pairing secret.


## Sharing screenshots

See [Android sharing and the iPhone clipboard Shortcut](https://agentj.app/docs/share-screenshot/). Shared content enters the composer; add a note before sending.

## Updating the phone page

Upgrading Agent J on your computer does not refresh the phone or web page. When a newer computer or web version is detected, the page offers **Update and reload**. You can also tap the settings icon at the top right → **Update and reload**. It never refreshes while you are typing without your click. Refresh after sending or saving your draft.


When a daily, weekly or monthly model allowance runs out, the phone tells you to wait for its reset or upgrade your plan with the model provider. A temporary rate limit still asks you to retry later. Provider error details are never displayed.

If shared Claude completes a reply while your phone is disconnected, reconnecting updates its existing input page. Reloading first fetches the current computer history before claiming that a message is still waiting. `/update` receipts use the current language setting, including changes made while Agent J is running. `agentj doctor` includes the websockets version for troubleshooting.

Short network blips are repaired quietly: when the phone's connection drops it is usually back within a second, and the page only turns grey with "Connection lost. Retrying in N s…" if reconnecting takes longer than about 6 seconds. A busy computer that answers late is not treated as a disconnect. If your Telegram channel can't reach Telegram three times in a row (or for two minutes), one line under the chat says so and disappears by itself once Telegram works again. For the first minute after Agent J starts, `agentj doctor` shows the relay as "connecting" rather than a problem.

Local read-aloud uses only voices on this phone. Host/cloud voices stay in `voice.tts.voice`; choose a phone voice separately with `voice.tts.phone_voice` (empty selects the local default for the reply language). Switching modes keeps both choices. An unavailable phone voice falls back with one notice. On first opening, iPhone waits for local voices to load before reporting that no voice is available.


Shared Claude ingress: startup and doctor repair the effective writable layer to accept; managed policies are read-only. Sessions started before the change need a new session or /clear on the computer. Shared startup re-enables ingress; check the desktop before resending unacknowledged messages.


0.18.1: Valid older Telegram enrollment continues with its local bot environment name. Huawei HarmonyOS devices have accurate names; account owners can rename a remote with a passkey while its host is online.
