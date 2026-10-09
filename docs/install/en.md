---
title: Installing Agent J
nav: Installing
summary: Send one sentence to the AI on your computer and it sets everything up; this page explains each step and the few you do yourself.
order: 2
---

# Installing Agent J

## The one thing you do

Open your AI coding tool on the computer (Claude Code, Codex or OpenCode) and send it this sentence, exactly as written:

> Please download https://agentj.app/install.md with curl, read the whole file with your file-reading tool, then follow it step by step, re-reading each section before you do it. Tell me when you need me to do something.

Why download it and read it whole: some AIs read web pages through a tool that only returns a summary, and a summary drops the safety rules and the points where the AI must stop and ask you. Before it starts, your AI may first tell you about the risks and ask whether to go on — that's normal.

If someone has already paid for a seat for you, they give you a longer sentence with a setup code starting with `ajt_`. Send that one instead.

From there the AI does the work. Whenever something needs you in person, it stops, tells you in plain words what to do, and waits until you say it's done.

`install.md` is the install guide written for your AI, which is why it is in English and fairly technical. This page is the same process for people, and the step numbers match.

## What only you can do

The AI will not do these for you, and it will never ask you to paste a password, a code or your passphrase into the chat. At each of these points it stops and asks you:

- typing your computer password (`sudo`, for example to install bubblewrap);
- signing in to Claude Code or Codex, or storing your model key in your own terminal (with OpenCode);
- signing in to the account dashboard: your email, the 6-digit code you receive, creating a passkey, opening the account;
- paying on Stripe's page and entering a promo code;
- tapping "Copy installation prompt" in the account dashboard and giving that sentence to the AI on your computer;
- naming your Agent;
- optionally setting a local approval passphrase for offline or unbound use;
- choosing the AI coding tool when more than one is available, and the folder the Agent works in;
- confirming Add a remote on the account page with your passkey, then scanning the QR code or pasting the pairing link on your phone;
- sending the first message from the phone and tapping "Approve" for the first time;
- saying yes or no when the AI wants to run `uv tool update-shell` (it adds a line to your shell's startup files);
- saying yes or no to a feedback report; and, if you want the AI to remember the "look it up first" rule, running one command yourself (Step 13).

Whenever it wants to report a problem to us, it first shows you the exact text and sends it only after you say yes. See [Plaza and feedback](/docs/plaza-feedback/).

## Step by step

The numbers are the same as in `install.md`. Steps marked ✋ are yours from start to finish; the others may still need you once or twice.

### Step 1: Verify the install guide

The AI downloads the install guide and checks two things: the fingerprint published on our site matches, and the copy on our site is identical to the public copy on GitHub. If they disagree it stops and tells you instead of going on.

### Step 2: Set up feedback

The AI opens a feedback channel first, so it can tell us if a later step goes wrong. Opening it sends no content; we only see your computer's IP address and the time. You see every report before it is sent.

### Step 3: Check the prerequisites

The AI checks your system, `curl`, `git`, `uv` and, on Linux, bubblewrap (a Mac has what it needs built in). It also looks at which AI coding tools are installed and signed in, and asks whether you use an iPhone or an Android phone.

On Ubuntu 24.04 the check also reads the AppArmor user-namespace restriction and starts a real isolation probe. A restriction value of `1` is compatible with a working bubblewrap profile. You may need to approve installing bubblewrap; if the probe is still blocked, the diagnostic gives the exact `/etc/apparmor.d/bwrap-agentj` profile for `/usr/bin/bwrap` only. It does not replace an existing profile or disable the system-wide restriction. The installation assistant checks before starting the background service, pauses on failure and keeps its resume record. Fix the policy and rerun the original command. The cloud-server section of the [installation guide](https://agentj.app/install.md) has the commands.

It may need you here:

- to type your computer password when it installs bubblewrap or another system package;
- to sign in to Claude Code or Codex in your own terminal if you haven't yet;
- in mainland China, or when none of the tools is usable, it installs OpenCode for you. You sign up with a model vendor yourself, top up and create a key there, then run `opencode auth login` in your own terminal to store the key. The AI never asks for your key and never touches it.

### Step 4: Install the `agentj` program

The AI installs `agentj`, the program on your computer, at one fixed version:

```bash
uv tool install "git+https://github.com/preangelleo/agentj@v0.16.7a1#subdirectory=host"
agentj doctor
```

`agentj doctor` lists, one line each, what is ready and what isn't. If GitHub is slow or blocked (mainland China, for example), the AI downloads the same program from our site instead and checks its fingerprint before installing it.

Your own terminal has to find `agentj` too, because you run it yourself in Steps 8 and 11. If a new terminal can't find it, the AI first asks whether it may run `uv tool update-shell`, which adds one line to your shell's startup files; only with your yes, and then you open a new terminal window. If you'd rather not, type `export PATH="$HOME/.local/bin:$PATH"` in the terminal you use, or try a new terminal.

### Step 5: Create this computer's identity

```bash
agentj init
```

This gives the computer its own keys. They stay on this computer, and nothing — the AI included — should touch them.

### Step 6: ✋ Open an Agent J account and buy a seat

If the sentence you sent contains a setup code (a seat someone paid for), **skip this step**: it's paid already, so you don't sign up or pay anything.

Otherwise the AI asks you to:

1. Open the account dashboard at https://agentj.app/account in a browser — on the computer or on your phone. Enter your email, tap "Send code", type the 6-digit code from the email into the page and tap "Sign in". The code works for 10 minutes; it comes from noreply@mail.agentj.app — check your spam folder if it doesn't arrive. The code goes into the page only, never into the chat with the AI.
2. Tap "Create a passkey on this device" when the page asks, and confirm with your fingerprint, face or screen lock — within 10 minutes of the email code. A browser on a Linux computer usually has no passkey of its own: in the window that pops up, choose to use your phone (it scans a QR code on the screen), or use a USB security key. Easier still: do this step on your phone.
3. Leave "Team name (optional)" empty if you like and tap "Open my account".
4. Buy one seat under "Billing": pick "Monthly" or "Yearly", put 1 in "Seats to buy", type your promo or invite code into "Promo or invite code (optional)" if you have one, tap "Buy seats" and pay on Stripe's page. Prices and the promo code rules: [Account and billing](/docs/account/).
5. Back in the account dashboard, on the "Empty seat" card tap "Copy installation prompt", and send that sentence to the AI on your computer. It carries on from there.

Stripe's payment page shows the price in US dollars, and the merchant is AgentJ.app. Change your mind within 7 days of your first payment and we refund it in full. Back in the account dashboard after paying and the page asks you to sign in? The payment went through: sign in again with your passkey or an email code, and don't pay a second time.

### Step 7: ✋ Add this computer to the Agent J account

First the AI asks what the Agent should be called: 1 to 32 characters — "Wren", "Shop assistant", whatever you like. Then it uses the setup code in the sentence to add this computer to that seat of your account, and tells you which account it joined. Please check the account ID; if it's wrong, the AI leaves that account right away. Afterwards it deletes the temporary file that held the setup code.

If setup instructions fail, copy the installation prompt from the same empty seat again. It issues a new seven-day single-use code and invalidates the previous unused code. The old eight-character login entry has retired; do not buy another seat to retry.

### Step 8: ✋ Human: approve with an account passkey

After this computer is bound, open its seat card on the account page and select Add a remote. Confirm once with your passkey and scan. No terminal approval passphrase is required. The same account page can add a phone to another bound, online computer.

A local approval passphrase is optional for offline or unbound use. Set it yourself with `agentj passphrase set`; the Agent must never choose or enter it. The local `agentj pair` alternative still requires it.

### Step 9: Connect the AI coding tool

The AI runs `agentj agent detect --json` to see which AI coding tools are usable. If there is exactly one, it uses that. If there are more, it asks you which one — it never picks for you.

It also asks which folder the Agent should work in. For a first try a new, empty folder such as `~/agentj-work` is best. Then:

```bash
agentj agent claude --dir ~/agentj-work
```

For Codex replace `claude` with `codex`. OpenCode also needs the model:

```bash
agentj agent opencode --dir ~/agentj-work --model zhipuai/glm-5.3
```

The Agent runs in a fenced-off space: it keeps your own sign-in and settings, but it cannot reach the keys and settings of `agentj` itself. The AI also looks for local services on your computer that already trust you (a browser's debugging port, for example) and tells you in one sentence what the fence can't stop. It installs the workflow design wizard too; once your phone is paired, say "帮我设计工作流" (help me design my workflows) on the phone to start it.

Two things in this step the AI always does, never skips: it asks you which folder the Agent works in, and it tells you, in a message of its own, what the fence can't stop. It goes on once you've read it.

### Step 10: Keep it running

```bash
agentj service install
```

As a service it starts by itself whenever the computer starts and you log in, so you never have to launch it by hand. Then the AI runs `agentj doctor` and tells you, in plain words, every line that starts with `!`. A common one: Claude Code logged in only through an environment variable, which the background service doesn't get. Run `claude` once in a terminal and log in there, then have the AI reinstall the service. On a cloud server the AI may ask you to run `loginctl enable-linger $USER` once (it may ask for your password), so it keeps running after you leave SSH.

### Step 11: ✋ Pair the phone

Agent J has to be running on the computer first (Step 10).

1. On the phone, open https://m.agentj.app, add it to your Home Screen and open it from that icon. The top of the page says "Not paired yet".
2. In the account dashboard, find this computer’s seat card and choose Add a remote. Confirm with your passkey once; confirmation is reused for ten minutes on this session. The account page can be open on another computer.
3. On the phone, tap Scan QR code and scan the code shown on the account page. If needed, use Copy pairing link and paste it into the Home Screen app under Or paste the pairing link. Do not paste it into an AI chat.
4. This account-approved pairing needs no terminal code or approval passphrase. Wait for the phone to connect.

For an offline or unbound computer, the local alternative remains `agentj pair` (or `agentj pair --link`): enter the phone’s six-digit code and your local approval passphrase on that computer.
The link is the pairing key: it works once, for 5 minutes. Never send it through WeChat, a group chat or email, and never to any AI.

### Step 12: Try it on the phone, then offer the completion feedback

The AI asks you to:

1. send "list the files in this folder" from the phone and check that a reply comes back;
2. ask it to create a test file and delete it. A card pops up on the phone: press and hold "Hold to approve" for about a second, until the button fills up. Deleting always needs your own OK, so that card's button is "Hold to approve this one". With Codex, the AI tells you what to expect;
3. tap "Turn on lock-screen alerts" if you want them.

Then the AI asks whether it may send us a short "finished" report — how long it took and what was awkward. You see the text first; say no, and it isn't sent.

### Step 13: Handover

Finally the AI runs `agentj handover --lang en` and passes on, word for word, the handover note the program writes with the real facts of this computer: the Agent's name, the account ID, the paired phones, https://m.agentj.app on the phone, what to do if you lose a phone (`agentj devices` to find its id, then `agentj revoke <id>`) or forget the passphrase (`agentj passphrase reset`, which unpairs every phone), updates, where "Resume", "Schedules" and "Commands" are, refunds, and that uninstalling doesn't cancel the billing. The program writes it; the AI doesn't change it or add commands.

It also asks whether you want the "look it up first" rule in its long-term memory. If you do, it runs `agentj docs-rule --write` itself; the program adds the rule once to the end of the AI's memory file and shows what it added. From then on, when you ask it about Agent J in a later conversation, it checks these pages first and asks us for you if they don't answer it. The Agent on your phone already knows that rule.

## Stopping halfway

Just tell the AI to stop. It asks whether to report which step you stopped at (and shows you that report first). Next time, send it the same sentence again; it checks everything from the top and carries on.

To remove it completely, run in a terminal:

```bash
agentj service uninstall
uv tool uninstall agentj
```

This removes only Agent J's own program. Your Claude Code, Codex or OpenCode and the Agent's work folder are left alone. Uninstalling doesn't stop the billing: cancel the seat and the subscription under "Manage billing" in the account dashboard — see [Account and billing](/docs/account/).


Every paid, unbound seat has its own one-line installation code. Two empty paid seats can claim two codes. Unused expired or revoked codes can be replaced, subject to the service’s daily allowance (midnight UTC reset).

## Add a remote for another computer

Once the target computer is installed, account-bound and online, find its seat card on your account page and choose Add a remote. Confirm with your passkey and scan with the phone. No target-computer terminal, passphrase setup or six-digit entry is needed. Confirmation is reused for ten minutes on the same session; the QR expires after five minutes and can add one remote. Review and approve pending remotes on that card too. Resume / enable scheduled tasks supplies owner approval for lifting Stop everything and enabling a specific task.

Account-page pairing is on by default. The target computer’s owner can disable it with `agentj remote-pair off`. Offline hosts are never queued; turn them on and try again. Older hosts must run `agentj update apply` or update from a paired phone. Devices record “Added from account page” and other remotes receive a notice.

This route trusts the signing control plane. Pairing material is encrypted to an ephemeral browser key and its plaintext is never stored by the backend; compromise of the signing service could still add a device. The local passphrase remains a local-only hash and is optional for bound hosts, available for offline/local approval and required on unbound hosts.


## macOS requirements

First run `opencode --version` (or your selected agent’s `--version`). If it works, continue installing; there is no macOS version-number gate. The one-line installer also probes its downloaded, pinned OpenCode binary. Only an actual failure such as `dyld: Symbol not found: _ubrk_clone` in `/usr/lib/libicucore.A.dylib` indicates incompatible system libraries. PATH changes or npm reinstalls cannot fix that; upgrade macOS, use another computer, or choose another working agent with the manual installation guide. The one-line installer downloads pinned uv from our site first, uses a managed-Python mirror, and retries package dependencies via a mirror; the obsolete preview path is no longer supported.


An installed computer can also use an AJI installation code. Rerun the account-page installation command to preserve its configuration, upgrade and link its seat. Alternatively, keep the code in a mode-0600 file and run `agentj login --install-code-file <file> --name <name>`. AJI codes are single-use; ajt_ setup codes only link an installed computer. After linking, add a remote from the account page, confirm with your passkey and scan; no terminal passphrase is required.

Intel Mac installations keep `cryptography<49`, including updates. If an older installer fails while compiling OpenSSL (`openssl-sys`), add `--with "cryptography<49"` to `uv tool install`.


[Set up AI command-line tools](/docs/setup-agents/) · [agentj command reference](/docs/cli/)

[Keep your computer awake](/docs/keep-awake/): commands, system settings and a request to send your Agent.
