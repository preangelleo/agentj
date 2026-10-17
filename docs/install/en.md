---
title: Installing Agent J
nav: Installing
summary: Pick the Agent J host (Windows, macOS, Linux), then one way to install: one command yourself, the AI on the Agent J host, the cloud installation assistant, or a helper.
order: 2
---

# Installing Agent J

All four ways install the same Agent J, and afterwards you talk to it from your phone. Not sure? Choose ①.

## Windows {#windows}

For Windows 10 and Windows 11 (64-bit). Agent J runs in Ubuntu inside Microsoft's Linux subsystem (WSL2); the install command sets that up first, so there is nothing to prepare.

### ① Install it yourself: one command {#windows-self}

**When to choose it:** the Agent J host has no AI tool yet, or you would rather do it yourself. You need one code from start to finish: the **installation code** (it starts with `AJI-`) on an empty seat card in your account.

**Time:** usually 15 to 30 minutes, mostly downloading; the first WSL install may need one restart. The parts that need you add up to a few minutes.

1. On https://agentj.app/account/ press "Get installation command" on the empty seat card, then "Copy installation command". Opened on Windows, the page gives you the PowerShell line; if not, press "Windows (PowerShell) command".
2. Open PowerShell (search Start for PowerShell; no administrator needed), paste and press Enter. The command looks like `$env:AGENTJ_CODE='AJI-…'; irm https://agentj.app/install.ps1 | iex`
3. Follow the screen. Every step starts with its number and what it is doing:

| On screen | What you do |
|---|---|
| Installation code | Taken from the command; if it is missing you paste it once (hidden) |
| WSL2 and Ubuntu | Allow the Windows administrator prompt; restart if asked, then paste the same command again |
| Ubuntu user | Pick a user name (Enter takes the suggestion) and a password (hidden; remember it) |
| System parts | Nothing; Windows does not ask for a password |
| AI | Reuses Claude Code or Codex if you are signed in. Otherwise pick a route: if the official services are unreachable choose the Agents Relay route (see [Hong Kong and mainland China](/docs/install/#hk-cn)); if they work, sign in with your own account in the browser |
| Agent J | Nothing: it installs, starts the background service and links this seat with the same code |
| Phone | A QR code appears: open https://m.agentj.app on **your own phone**, tap "Scan QR code" and scan it |
| Connect the AI | Nothing: the AI must answer one real request before it is connected |

4. When you see "✅ 全部完成" (all done), say hello to it from your phone. To use `agentj` on the Agent J host later, open "Ubuntu" from the Start menu, not PowerShell.

If something goes wrong:

- If a step fails, the screen says what to do. Fix it and **paste the same command again**; finished steps are skipped.
- The installation code works once and expires after 24 hours (the card shows the time).
- Only the AI is missing? In "Ubuntu" run `curl -fsSL https://agentj.app/install.sh | bash` again (no code): it does just that step.
- Confirm systemd is on: the background service runs on it. In "Ubuntu" run `ps -p 1 -o comm=`; it should print `systemd`. If not, in "Ubuntu" run `printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf` (skip it when the file already has `systemd=true` under `[boot]`), then in PowerShell run `wsl --shutdown`, reopen "Ubuntu" and run `agentj service install`. The install command does both steps itself; `agentj service install` and upgrades also name these two steps in one sentence when systemd is off.

### ② Let the AI on the Agent J host install it {#windows-ai}

**When to choose it:** you already use Claude Code, Codex or OpenCode inside Ubuntu on WSL and want it to do the work and explain as it goes. On Windows Agent J installs only inside WSL2, and an AI running on Windows itself stops and tells you so; without WSL yet, ① is simpler.

1. Already bought a seat: press "Copy installation prompt" on the empty seat card in your account and send it to the AI in Ubuntu.
2. Not yet: send it this sentence exactly as written, and it walks you through opening an account and buying a seat:

> Please download https://agentj.app/install.md with curl, read the whole file with your file-reading tool, then follow it step by step, re-reading each section before you do it. Tell me when you need me to do something.

What the AI does at each step, and where it stops for you: [When the AI installs it](/docs/install/#ai-steps).

### ③ Remote installation assistant (cloud) {#windows-assistant}

**When to choose it:** nobody is around to help, there is no AI on the Agent J host, and you are stuck halfway and want to ask someone.

How it works today:

1. Install with the command from ①. That command is the installation assistant: the screen says what each step is doing, and when something fails it says what to do. Fix it and paste the same command again to carry on.
2. Still stuck, and Agent J is already installed: run this command in "Ubuntu". It sends your question and a health check with the secrets removed to our support desk, which usually answers with concrete steps within a few minutes.

```bash
agentj support ask "<your question>" --attach-doctor
```

3. Not installed yet, or you cannot reach us: email feedback@agentj.app with the step, the exact error and a screenshot (cover the installation code, keys and QR code first).

> **Tip:** In a later planned release the cloud assistant suggests the next step right on your phone, and nothing runs until you tap it.

### ④ Get someone to help: beside you or remotely {#windows-helper}

**When to choose it:** you are not at home with computers or terminals, and a family member, friend or colleague can sit with you or help over a video call or screen sharing.

What to do today:

1. The helper walks you through ①. The code is copied only from **your own** account page and pasted only on your own computer; when helping remotely, you share your screen and the helper tells you what to do. Never put the code or a key into a chat.
2. A Windows administrator prompt appears along the way, and there may be one restart. A helper who warns you in advance saves you a fright.
3. Scan the pairing QR code with **your own phone**. If the helper's phone scans it, that phone becomes a remote for the Agent J host: afterwards run `agentj devices` to find its device ID, then `agentj revoke <device-id>` to remove it.
4. Phone not at hand, or the scan did not work? No need to reinstall: once it is installed, press "Add a remote" on the Agent J host's seat card in your account, confirm with your passkey, and scan with your phone.

> **Tip:** One-step helper pairing arrives in a later planned release: the helper connects their own phone for a while and guides you through the install from it; once your phone is paired and the AI is connected, the helper is disconnected automatically.

## macOS {#macos}

For Macs with Apple silicon and Intel processors.

### ① Install it yourself: one command {#macos-self}

**When to choose it:** the Agent J host has no AI tool yet, or you would rather do it yourself. You need one code from start to finish: the **installation code** (it starts with `AJI-`) on an empty seat card in your account.

**Time:** usually under 10 minutes. the Agent J host's own work takes a few minutes (about 2 on our test machine); the rest is you signing in to an AI and scanning a code.

1. On https://agentj.app/account/ press "Get installation command" on the empty seat card, then "Copy installation command". Opened on a Mac, the page gives you the Terminal line; if not, press "macOS / Linux command".
2. Open Terminal (press Command + Space and search for Terminal), paste and press Enter. The command looks like `curl -fsSL https://agentj.app/install.sh | bash -s -- AJI-…`
3. Follow the screen. Every step starts with its number and what it is doing:

| On screen | What you do |
|---|---|
| Installation code | Taken from the command; if it is missing you paste it once (hidden) |
| System parts | You may be asked for the Agent J host password once (hidden) |
| AI | Reuses Claude Code or Codex if you are signed in. Otherwise pick a route: if the official services are unreachable choose the Agents Relay route (see [Hong Kong and mainland China](/docs/install/#hk-cn)); if they work, sign in with your own account in the browser |
| Agent J | Nothing: it installs, starts the background service and links this seat with the same code |
| Phone | A QR code appears: open https://m.agentj.app on **your own phone**, tap "Scan QR code" and scan it |
| Connect the AI | Nothing: the AI must answer one real request before it is connected |

4. When you see "✅ 全部完成" (all done), say hello to it from your phone. Afterwards `agentj status` works in **the same Terminal window**.

If something goes wrong:

- If a step fails, the screen says what to do. Fix it and **paste the same command again**; finished steps are skipped.
- The installation code works once and expires after 24 hours (the card shows the time).
- Only the AI is missing? Run `curl -fsSL https://agentj.app/install.sh | bash` again (no code): it does just that step.

### ② Let the AI on the Agent J host install it {#macos-ai}

**When to choose it:** you already use Claude Code, Codex or OpenCode and want it to do the work and explain as it goes.

1. Already bought a seat: press "Copy installation prompt" on the empty seat card in your account and send it to the AI on the Agent J host.
2. Not yet: send it this sentence exactly as written, and it walks you through opening an account and buying a seat:

> Please download https://agentj.app/install.md with curl, read the whole file with your file-reading tool, then follow it step by step, re-reading each section before you do it. Tell me when you need me to do something.

What the AI does at each step, and where it stops for you: [When the AI installs it](/docs/install/#ai-steps).

### ③ Remote installation assistant (cloud) {#macos-assistant}

**When to choose it:** nobody is around to help, there is no AI on the Agent J host, and you are stuck halfway and want to ask someone.

How it works today:

1. Install with the command from ①. That command is the installation assistant: the screen says what each step is doing, and when something fails it says what to do. Fix it and paste the same command again to carry on.
2. Still stuck, and Agent J is already installed: run this command in Terminal. It sends your question and a health check with the secrets removed to our support desk, which usually answers with concrete steps within a few minutes.

```bash
agentj support ask "<your question>" --attach-doctor
```

3. Not installed yet, or you cannot reach us: email feedback@agentj.app with the step, the exact error and a screenshot (cover the installation code, keys and QR code first).

> **Tip:** In a later planned release the cloud assistant suggests the next step right on your phone, and nothing runs until you tap it.

### ④ Get someone to help: beside you or remotely {#macos-helper}

**When to choose it:** you are not at home with computers or terminals, and a family member, friend or colleague can sit with you or help over a video call or screen sharing.

What to do today:

1. The helper walks you through ①. The code is copied only from **your own** account page and pasted only on your own computer; when helping remotely, you share your screen and the helper tells you what to do. Never put the code or a key into a chat.
2. When the Agent J host asks for its password, you type it yourself; don't tell your helper.
3. Scan the pairing QR code with **your own phone**. If the helper's phone scans it, that phone becomes a remote for the Agent J host: afterwards run `agentj devices` to find its device ID, then `agentj revoke <device-id>` to remove it.
4. Phone not at hand, or the scan did not work? No need to reinstall: once it is installed, press "Add a remote" on the Agent J host's seat card in your account, confirm with your passkey, and scan with your phone.

> **Tip:** One-step helper pairing arrives in a later planned release: the helper connects their own phone for a while and guides you through the install from it; once your phone is paired and the AI is connected, the helper is disconnected automatically.

## Linux {#linux}

For Ubuntu, Debian and other common distributions, on desktops, laptops and cloud servers. A cloud server you reach only over SSH (no desktop) also needs the cloud-server section of [the install guide](https://agentj.app/install.md).

### ① Install it yourself: one command {#linux-self}

**When to choose it:** the Agent J host has no AI tool yet, or you would rather do it yourself. You need one code from start to finish: the **installation code** (it starts with `AJI-`) on an empty seat card in your account.

**Time:** usually under 10 minutes. the Agent J host's own work takes a few minutes (about 2 on our test machine); the rest is you signing in to an AI and scanning a code.

1. On https://agentj.app/account/ press "Get installation command" on the empty seat card, then "Copy installation command". Opened on Linux, the page gives you the terminal line; if not, press "macOS / Linux command".
2. Open a terminal, paste and press Enter. The command looks like `curl -fsSL https://agentj.app/install.sh | bash -s -- AJI-…`
3. Follow the screen. Every step starts with its number and what it is doing:

| On screen | What you do |
|---|---|
| Installation code | Taken from the command; if it is missing you paste it once (hidden) |
| System parts | Installs bubblewrap and socat (the Agent runs fenced); you may be asked for the Agent J host password once (hidden) |
| AI | Reuses Claude Code or Codex if you are signed in. Otherwise pick a route: if the official services are unreachable choose the Agents Relay route (see [Hong Kong and mainland China](/docs/install/#hk-cn)); if they work, sign in with your own account in the browser |
| Agent J | Nothing: it installs, starts the background service and links this seat with the same code |
| Phone | A QR code appears: open https://m.agentj.app on **your own phone**, tap "Scan QR code" and scan it |
| Connect the AI | Nothing: the AI must answer one real request before it is connected |

4. When you see "✅ 全部完成" (all done), say hello to it from your phone. Afterwards `agentj status` works in **the same terminal window**.

If something goes wrong:

- If a step fails, the screen says what to do. Fix it and **paste the same command again**; finished steps are skipped.
- The installation code works once and expires after 24 hours (the card shows the time).
- Only the AI is missing? Run `curl -fsSL https://agentj.app/install.sh | bash` again (no code): it does just that step.

### ② Let the AI on the Agent J host install it {#linux-ai}

**When to choose it:** you already use Claude Code, Codex or OpenCode and want it to do the work and explain as it goes.

1. Already bought a seat: press "Copy installation prompt" on the empty seat card in your account and send it to the AI on the Agent J host.
2. Not yet: send it this sentence exactly as written, and it walks you through opening an account and buying a seat:

> Please download https://agentj.app/install.md with curl, read the whole file with your file-reading tool, then follow it step by step, re-reading each section before you do it. Tell me when you need me to do something.

What the AI does at each step, and where it stops for you: [When the AI installs it](/docs/install/#ai-steps).

### ③ Remote installation assistant (cloud) {#linux-assistant}

**When to choose it:** nobody is around to help, there is no AI on the Agent J host, and you are stuck halfway and want to ask someone.

How it works today:

1. Install with the command from ①. That command is the installation assistant: the screen says what each step is doing, and when something fails it says what to do. Fix it and paste the same command again to carry on.
2. Still stuck, and Agent J is already installed: run this command in a terminal. It sends your question and a health check with the secrets removed to our support desk, which usually answers with concrete steps within a few minutes.

```bash
agentj support ask "<your question>" --attach-doctor
```

3. Not installed yet, or you cannot reach us: email feedback@agentj.app with the step, the exact error and a screenshot (cover the installation code, keys and QR code first).

> **Tip:** In a later planned release the cloud assistant suggests the next step right on your phone, and nothing runs until you tap it.

### ④ Get someone to help: beside you or remotely {#linux-helper}

**When to choose it:** you are not at home with computers or terminals, and a family member, friend or colleague can sit with you or help over a video call or screen sharing.

What to do today:

1. The helper walks you through ①. The code is copied only from **your own** account page and pasted only on your own computer; when helping remotely, you share your screen and the helper tells you what to do. Never put the code or a key into a chat.
2. When the Agent J host asks for its password, you type it yourself; don't tell your helper.
3. Scan the pairing QR code with **your own phone**. If the helper's phone scans it, that phone becomes a remote for the Agent J host: afterwards run `agentj devices` to find its device ID, then `agentj revoke <device-id>` to remove it.
4. Phone not at hand, or the scan did not work? No need to reinstall: once it is installed, press "Add a remote" on the Agent J host's seat card in your account, confirm with your passkey, and scan with your phone.

> **Tip:** One-step helper pairing arrives in a later planned release: the helper connects their own phone for a while and guides you through the install from it; once your phone is paired and the AI is connected, the helper is disconnected automatically.

## Olares box {#olares}

Agent J runs on an Olares box (Olares 1.12 or newer) too, and your data stays on the box.

- **Install it on the box's own Ubuntu, as a normal user** (not root), the same way as on a cloud server. Don't install it inside an Olares app: Agent J's safety fence cannot start in those containers.
- **Keep it running:** the AI asks you to run `sudo loginctl enable-linger <user>` once (you type your password), log in again, and then it installs the service.
- **The model:** use the box's own **Olares Router**. Create an API key in the Router app; the AI connects it through DeepSeek's dsh or OpenCode, and you paste the key only into the card on your phone. See [DeepSeek dsh](/docs/setup-agents/#dsh).
- **Remote maintenance:** use the official LarePass VPN. You import your own mnemonic in LarePass yourself; never give it to an AI. Your phone reaches Agent J without the VPN.
- **Box came with Agent J pre-installed?** Only the software is pre-installed. After activation you sign in to your own Agent J account and create your own seat.

## Hong Kong and mainland China: which AI {#hk-cn}

> **Note:** In Hong Kong and mainland China the official Claude Code and Codex services are usually unreachable. When the one-command install reaches the AI step it tests the network first and, if they are unreachable, recommends the **Agents Relay route**: sign up at https://agentsrelay.net/onboard/, get a key, then paste it into the terminal (hidden). Paste the key only into the terminal, never into a chat, and don't tell it to your helper.

In mainland China you can also use OpenCode with a Chinese model: see [Install the AI command-line tools](/docs/setup-agents/).

On Volcano Ark, Kimi, Alibaba Bailian or DeepSeek, or not sure which to buy: see [Choose by your model](/docs/models/).

## Cover the codes before a screenshot {#screenshots}

> **Note:** Before you send anyone a screenshot, your helper and our support desk included, cover the installation code (starting with `AJI-`), keys and QR codes on the screen. In someone else's hands they could be used to take your seat or to ask to connect to the Agent J host.

## When the AI installs it {#ai-steps}

Why download it and read it whole: some AIs read web pages through a tool that only returns a summary, and a summary drops the safety rules and the points where the AI must stop and ask you. Before it starts, your AI may first tell you about the risks and ask whether to go on — that's normal.

If someone has already paid for a seat for you, they give you a longer sentence with a setup code starting with `ajt_`. Send that one instead.

From there the AI does the work. Whenever something needs you in person, it stops, tells you in plain words what to do, and waits until you say it's done.

`install.md` is the install guide written for your AI, which is why it is in English and fairly technical. This page is the same process for people, and the step numbers match.

## What only you can do

The AI will not do these for you, and it will never ask you to paste a password, a code or your passphrase into the chat. At each of these points it stops and asks you:

- typing the Agent J host password (`sudo`, for example to install bubblewrap);
- signing in to Claude Code or Codex, or storing your model key in your own terminal (with OpenCode);
- signing in to the account dashboard: your email, the 6-digit code you receive, creating a passkey, opening the account;
- paying on Stripe's page and entering a promo code;
- tapping "Copy installation prompt" in the account dashboard and giving that sentence to the AI on the Agent J host;
- naming your Agent;
- setting a remote pairing password (asked once at the end of the installation; optional, recommended; required to pair at the terminal);
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

The AI opens a feedback channel first, so it can tell us if a later step goes wrong. Opening it sends no content; we only see the Agent J host's IP address and the time. You see every report before it is sent.

### Step 3: Check the prerequisites

The AI checks your system, `curl`, `git`, `uv` and, on Linux, bubblewrap (a Mac has what it needs built in). It also looks at which AI coding tools are installed and signed in. Whether you use an iPhone or an Android phone it asks only at Step 11, where the answer matters.

On Ubuntu 24.04 the check also reads the AppArmor user-namespace restriction and starts a real isolation probe. A restriction value of `1` is compatible with a working bubblewrap profile. You may need to approve installing bubblewrap; if the probe is still blocked, the diagnostic gives the exact `/etc/apparmor.d/bwrap-agentj` profile for `/usr/bin/bwrap` only. It does not replace an existing profile or disable the system-wide restriction. The installation assistant checks before starting the background service, pauses on failure and keeps its resume record. Fix the policy and rerun the original command. The cloud-server section of the [installation guide](https://agentj.app/install.md) has the commands.

It may need you here:

- to type the Agent J host password when it installs bubblewrap or another system package;
- to sign in to Claude Code or Codex in your own terminal if you haven't yet;
- in mainland China, or when none of the tools is usable, it installs OpenCode for you. You sign up with a model vendor yourself, top up and create a key there, then run `opencode auth login` in your own terminal to store the key. The AI never asks for your key and never touches it.

### Step 4: Install the `agentj` program

The AI installs `agentj`, the program on the Agent J host, at one fixed version:

```bash
uv tool install "git+https://github.com/preangelleo/agentj@v0.23.1a1#subdirectory=host"
agentj doctor
```

`agentj doctor` lists, one line each, what is ready and what isn't. If GitHub is slow or blocked (mainland China, for example), the AI downloads the same program from our site instead and checks its fingerprint before installing it.

Your own terminal has to find `agentj` too, because you run it yourself in Steps 8 and 11. If a new terminal can't find it, the AI first asks whether it may run `uv tool update-shell`, which adds one line to your shell's startup files; only with your yes, and then you open a new terminal window. If you'd rather not, type `export PATH="$HOME/.local/bin:$PATH"` in the terminal you use, or try a new terminal.

### Step 5: Create the Agent J host's identity

```bash
agentj init
```

This gives the Agent J host its own keys. They stay on the Agent J host, and nothing — the AI included — should touch them.

### Step 6: ✋ Open an Agent J account and buy a seat

If the sentence you sent contains a setup code (a seat someone paid for), **skip this step**: it's paid already, so you don't sign up or pay anything.

Otherwise the AI asks you to:

1. Open the account dashboard at https://agentj.app/account in a browser — on the Agent J host or on your phone. Enter your email, tap "Send code", type the 6-digit code from the email into the page and tap "Sign in". The code works for 10 minutes; it comes from noreply@mail.agentj.app — check your spam folder if it doesn't arrive. The code goes into the page only, never into the chat with the AI.
2. Tap "Create a passkey on this device" when the page asks, and confirm with your fingerprint, face or screen lock — within 10 minutes of the email code. A browser on a Linux computer usually has no passkey of its own: in the window that pops up, choose to use your phone (it scans a QR code on the screen), or use a USB security key. Easier still: do this step on your phone.
3. Leave "Team name (optional)" empty if you like and tap "Open my account".
4. Buy one seat under "Billing": pick "Monthly" or "Yearly", put 1 in "Seats to buy", type your promo or invite code into "Promo or invite code (optional)" if you have one, tap "Buy seats" and pay on Stripe's page. Prices and the promo code rules: [Account and billing](/docs/account/).
5. Back in the account dashboard, on the "Empty seat" card tap "Copy installation prompt", and send that sentence to the AI on the Agent J host. It carries on from there.

Stripe's payment page shows the price in US dollars, and the merchant is AgentJ.app. Change your mind within 7 days of your first payment and we refund it in full. Back in the account dashboard after paying and the page asks you to sign in? The payment went through: sign in again with your passkey or an email code, and don't pay a second time.

### Step 7: ✋ Add the Agent J host to the Agent J account

First the AI asks what the Agent should be called: 1 to 32 characters — "Wren", "Shop assistant", whatever you like. Then it uses the setup code in the sentence to add the Agent J host to that seat of your account, and tells you which account it joined. Please check the account ID; if it's wrong, the AI leaves that account right away. Afterwards it deletes the temporary file that held the setup code.

If setup instructions fail, copy the installation prompt from the same empty seat again. It issues a new seven-day single-use code and invalidates the previous unused code. The old eight-character login entry has retired; do not buy another seat to retry.

### Step 8: ✋ Human: approve with an account passkey

After the Agent J host is bound, open its seat card on the account page and select Add a remote. Confirm with your passkey (or the Agent J host's remote pairing password) and scan. The same account page can add a phone to another bound, online computer.

At the end of the installation you're asked whether to set a remote pairing password (optional, recommended): press Enter to skip, and set it any time later with `agentj passphrase set`. The Agent must never choose or enter it. Pairing at the terminal (`agentj pair`, `agentj pair --link`) requires it; without one, `agentj pair` asks you to set it first. `agentj pair --check` says which route works before any QR code.

### Step 9: Connect the AI coding tool

The AI first finds out what it can by itself, then asks you once, in one short message, only what you have to decide. Each line carries its recommendation: reply "OK" to take them all.

- **Which AI coding tool.** `agentj agent detect --json` shows which ones are usable. Exactly one → it uses that and doesn't ask. More than one → it lists them and you choose; this is the one question without a recommendation, because it never picks for you.
- **Which folder the Agent works in.** It looks for a folder you already use for your projects (such as `~/coding`) and recommends it; with none, it recommends creating `~/coding`. For a first try a new, empty folder such as `~/agentj-work` is fine too. Then:

```bash
agentj agent claude --dir ~/agentj-work
```

For Codex replace `claude` with `codex`. OpenCode also needs the model:

```bash
agentj agent opencode --dir ~/agentj-work --model zhipuai/glm-5.3
```

The Agent runs in a fenced-off space: it keeps your own sign-in and settings, but it cannot reach the keys and settings of `agentj` itself. The AI also looks for local services on the Agent J host that already trust you (a browser's debugging port, for example) and tells you in one sentence what the fence can't stop.

It doesn't ask whether your browsers are signed in to email, shops or your bank. Here is why that matters and why it needn't ask: the phone's Agent works as you, so if a browser on the Agent J host has a debugging port open, the Agent can drive it and use every site signed in there. But paying, placing an order or moving money on any website always stops and asks you on the phone first, whatever you are signed in to — so the answer would change nothing. If such a port is open, the AI names it.

It installs the workflow design wizard too; once your phone is paired, say "帮我设计工作流" (help me design my workflows) on the phone to start it.

Two things in this step the AI always does, never skips: it asks you which folder the Agent works in, and it tells you, in a message of its own, what the fence can't stop. It goes on once you've read it.

### Step 10: Keep it running

```bash
agentj service install
```

As a service it starts by itself whenever the Agent J host starts and you log in, so you never have to launch it by hand. Then the AI runs `agentj doctor` and tells you, in plain words, every line that starts with `!`. A common one: Claude Code logged in only through an environment variable, which the background service doesn't get. Run `claude` once in a terminal and log in there, then have the AI reinstall the service. On a cloud server the AI may ask you to run `loginctl enable-linger $USER` once (it may ask for your password), so it keeps running after you leave SSH.

### Step 11: ✋ Pair the phone

Agent J has to be running on the Agent J host first (Step 10).

1. On the phone, open https://m.agentj.app, add it to your Home Screen and open it from that icon. The top of the page says "Not paired yet".
2. In the account dashboard, find the Agent J host’s seat card and choose Add a remote. Confirm with your passkey once; confirmation is reused for ten minutes on this session. The account page can be open on another computer.
3. On the phone, tap Scan QR code and scan the code shown on the account page. If needed, use Copy pairing link and paste it into the Home Screen app under Or paste the pairing link. Do not paste it into an AI chat.
4. This account-approved pairing needs no terminal code. Wait for the phone to connect.

When the Agent J host is offline or account-page pairing is off (`agentj pair --check` tells you), the local alternative is `agentj pair` (or `agentj pair --link`): type the phone's six-digit code and the remote pairing password on that computer (it asks you to set one first if there is none).
The link is the pairing key: it works once, for 5 minutes. Never send it through WeChat, a group chat or email, and never to any AI.

### Step 12: Try it on the phone, then offer the completion feedback

The AI asks you to:

1. send "list the files in this folder" from the phone and check that a reply comes back;
2. ask it to create a test file and delete it. A card pops up on the phone: press and hold "Hold to approve" for about a second, until the button fills up. Deleting always needs your own OK, so that card is marked ⚠; the button is still "Hold to approve". With Codex, the AI tells you what to expect;
3. tap "Turn on lock-screen alerts" if you want them.

Then the AI asks whether it may send us a short "finished" report — how long it took and what was awkward. You see the text first; say no, and it isn't sent.

### Step 13: Handover

Finally the AI runs `agentj handover --lang en` and passes on, word for word, the handover note the program writes with the real facts of the Agent J host: the Agent's name, the account ID, the paired phones, https://m.agentj.app on the phone, what to do if you lose a phone (`agentj devices` to find its id, then `agentj revoke <id>`) or forget the passphrase (`agentj passphrase reset`, which unpairs every phone), updates, where "Resume", "Schedules" and "Commands" are, refunds, and that uninstalling doesn't cancel the billing. The program writes it; the AI doesn't change it or add commands.

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

Once the target Agent J host is installed, account-bound and online, find its seat card on your account page and choose Add a remote. Confirm it's you, then scan with the phone; the target Agent J host's terminal isn't needed. Confirm either way: with your passkey (none on this device? choose Use a phone or tablet in the prompt and confirm on your phone), or with that computer's remote pairing password. After a passkey confirmation, the same session doesn't ask again for ten minutes; the QR expires after five minutes and adds one device. Pending devices can also be reviewed and approved on the seat card. Under More on the seat card, See what the Agent J host is waiting on you for is only for two cases: resuming after Stop everything, or enabling a scheduled task the Agent created. It has nothing to do with seats or billing; an unpaid bill never stops the tasks on the Agent J host.

Account-page pairing is on by default. The target Agent J host’s owner can disable it with `agentj remote-pair off`. Offline hosts are never queued; turn them on and try again. Older hosts must run `agentj update apply` or update from a paired phone. Devices record “Added from account page” and other remotes receive a notice.

This route trusts the signing control plane. Pairing material is encrypted to an ephemeral browser key and its plaintext is never stored by the backend; compromise of the signing service could still add a device. The remote pairing password is stored only on that computer, as a hash. When you type it on the account page, the browser first encrypts it to a one-time public key that computer just handed out; only that computer can open and check it. Our servers pass on ciphertext they can't read and keep nothing. Five wrong tries in a row lock it on that computer for a while.


## macOS requirements

First run `opencode --version` (or your selected agent’s `--version`). If it works, continue installing; there is no macOS version-number gate. The one-line installer also probes its downloaded, pinned OpenCode binary. Only an actual failure such as `dyld: Symbol not found: _ubrk_clone` in `/usr/lib/libicucore.A.dylib` indicates incompatible system libraries. PATH changes or npm reinstalls cannot fix that; upgrade macOS, use another computer, or choose another working agent with the manual installation guide. The one-line installer downloads pinned uv from our site first, uses a managed-Python mirror, and retries package dependencies via a mirror; the obsolete preview path is no longer supported.


An installed computer can also use an AJI installation code. Rerun the account-page installation command to preserve its configuration, upgrade and link its seat. Alternatively, keep the code in a mode-0600 file and run `agentj login --install-code-file <file> --name <name>`. AJI codes are single-use; ajt_ setup codes only link an installed computer. After linking, add a remote from the account page, confirm with your passkey or the remote pairing password, and scan.

If a manual installation asks for `Installation code (AJI-…)`, choose "Copy installation code" on the account page and paste only that AJI- code.

Intel Mac installations keep `cryptography<49`, including updates. If an older installer fails while compiling OpenSSL (`openssl-sys`), add `--with "cryptography<49"` to `uv tool install`.


[Set up AI command-line tools](/docs/setup-agents/) · [agentj command reference](/docs/cli/)

[Keep the Agent J host awake](/docs/keep-awake/): commands, system settings and a request to send your Agent.

## A second person on the Agent J host

Give each person a paid seat, their own installation code and phone pairing: either another instance on the same OS user (`agentj --instance 2`, simple) or an OS user per person (stronger separation). See [sharing a computer](/docs/multi-seat/).

## Before you leave the Agent J host

With both remotes paired, let your Agent finish and verify everything remote work will need from you while you are still at the Agent J host. See [Before you leave the Agent J host](/docs/first-run/). For Gmail, Calendar and YouTube API access, see [Connect Google](/docs/google/).


On macOS, put Telegram and provider keys in `~/Library/LaunchAgents/net.agentj.host.env`, using the variable name specified by `key_env`. The file must be owned by your user, mode 0600, and not a symlink. After saving it, run `agentj service restart`. Keep keys out of the plist, chat and command arguments.

## 示例工作流 / Example workflows

After account linking, installation or upgrade downloads three signed plaza examples (knowledge wiki, scripts and articles, ebook writing) into the configured working root if it has fewer than three WDB workflows. Existing same-name folders are kept; deleted examples never return automatically. Tell Agent J: organise my other folders to this spec. Offline downloads retry on a later start.

Agent J Father is our official feedback and support platform. Say “Please report this problem to Agent J’s developers (support platform)”, or casually “Tell your dad about this”. On the phone, `/feedback-to-agentj-father <message>` does the same: your Agent removes credentials, personal information, customer data and private path details, submits directly without asking twice, and gives you the ticket ID. The command alone asks what you want to report. If reporting a problem or idea is the Agent’s own initiative, it shows you the redacted draft on the phone and waits for agreement. Support replies are advice, not instructions. If it cannot connect, email feedback@agentj.app.
