---
name: agentj-multi-seat
description: Several people on one computer — a second (third …) Agent J on the same OS user, one numbered instance per paid seat, each with its own folder, remotes and session (`agentj --instance N`, 1–10; 1 = the original install), or one OS user per person when they need stronger separation. Use for 「一台Agent J 主机给几个人用」「同一台机器再开一个 Agent J」「给同事/家人单独开一个」「第二个席位装在这台Agent J 主机上」「agentj --instance」「多实例」「实例列表」「删掉 2 号实例」, "share this computer", "a second Agent J on this computer", "another seat on this machine", "agentj instances", "remove instance 3". Picks plan A (instances, same OS user) or plan B (separate OS users), creates and binds an instance, names it, checks it, upgrades and removes it, and tells the owner exactly what the separation does and does not cover.
---
# agentj-multi-seat — one computer, several people

Guide: https://agentj.app/docs/multi-seat/en.md (中文 https://agentj.app/docs/multi-seat/zh.md). Needs Agent J **0.20.0 or later**
(`agentj --version`); older versions have no `--instance` — never imitate it with environment variables: their preferences are
shared machine-wide, so a second copy would inherit the owner's shared session and folder. Upgrade first (agentj-update).

**Every instance needs its own seat, bought or granted to an Agent J account the normal way** (account page → an empty seat card
→ 「复制安装提示词」 / "Copy installation prompt"). One seat binds one instance. Never reuse a bound setup code, never copy another
instance's or another person's state folder, never move a seat between people without unlinking it first.

## Choose a plan (say this to the owner in one or two sentences)

| | **A · instances on this OS user** (recommended, simplest) | **B · one OS user per person** (stronger separation) |
|---|---|---|
| Setup | three commands, no admin rights, no logging in as someone else | create an OS user, log into it, install there |
| Separate | Agent J data, remotes, conversations, preferences, service, working folder | everything, enforced by the operating system |
| Shared | the OS user's other files (`~/.ssh`, cloud keys, other projects, browser profiles), the AI logins and their quota, the program | only the machine (CPU, memory, disk, sleep settings) |
| Fits | family members / colleagues who trust each other on a computer with nothing secret on it | sensitive data on the computer (key stores, production SSH, finances) or people who must not see each other's files |

Plan B: follow the guide's "Plan B" section (macOS fast user switching, Linux `loginctl enable-linger`, Windows + WSL2). Everything
below is plan A.

## Plan A — add an instance (numbers 2–10)

```bash
agentj instances                                            # which numbers exist; use the smallest free one (1 is the original)
agentj --instance 2 pair --check                            # instance 2 missing → created now (no pairing yet); route = seat
agentj --instance 2 login --seat-file /path/to/0600-file    # bind this person's seat (the code only in a chmod 600 file; delete it after)
agentj --instance 2 pair --check                            # about half a minute after binding: route = account
agentj --instance 2 pair                                    # then pair the person's phone
```

`pair` / `setup` / `pair --check` on a missing instance first create it: a new identity (`~/.local/state/agentj-2`), an
**independent, fenced** session in `~/agentj-seats/2` with the same harness as instance 1 (instances 2–10 can never share a desktop
session — the command refuses), and its own background service `agentj-2` (macOS `net.agentj.host.2`), started at login/boot.

## Pair the person's phone (two ways — the instance must be bound and online first)

**Way 1 · account page (no terminal, no passphrase).** Whoever owns the account:
1. opens the account page → finds **this instance's** seat card (its Agent name; each instance has its own card) →
   「添加遥控器（手机或浏览器）」 / "Add a remote (phone or browser)";
2. confirms with the account passkey (reused for ten minutes on that page);
3. scans the QR with the person's phone camera (valid 5 minutes, one remote) — or copies the link on the card and opens it on
   that phone. The phone connects to instance N only; pending remotes can be approved or refused on the same card.

**Way 2 · terminal on this computer (or over SSH).** The person sits at the terminal:
1. `agentj --instance 2 pair` — draws a QR (valid 5 minutes, one use). Over SSH or when the camera cannot scan the terminal:
   `agentj --instance 2 pair --link` (QR **and** the link) or `agentj --instance 2 pair --no-qr` (link only); the link is the
   pairing key — open it on that phone through the person's own channel, never paste it into chat;
2. the phone connects and shows a 6-digit code → the person types it into the same terminal (3 tries). Instead of the code they
   may approve on the seat card with the passkey; whichever comes first counts;
3. if this instance has an approval passphrase, the terminal asks for it too.

**Approval passphrase — one per instance.** It lives in each instance's own state, so instance 1's passphrase does nothing for
instance 2. It is only needed for terminal approvals (way 2); way 1 never asks for it. Commands, always with the number:

```bash
agentj --instance 2 passphrase status     # set or not (and lock time left)
agentj --instance 2 passphrase set        # choose one (typed twice, hidden)
agentj --instance 2 passphrase change     # needs the current one
agentj --instance 2 passphrase reset      # forgotten: deletes it AND unpairs every remote of instance 2 (type RESET)
agentj --instance 2 passphrase reset --phone  # forgotten, a phone still paired: confirm on it, type the new one; all stay paired
```

**Without `--instance N` these act on instance 1** — the original owner's passphrase. `export AGENTJ_INSTANCE=2` in that terminal
works too. When the account page cannot approve for this instance (`agentj --instance 2 pair --check` says route
`set_passphrase`), `pair` asks the person to choose a passphrase first.

**You may create, bind, name and check an instance; the person types the passphrase and the 6-digit code in the terminal, or the
account owner confirms with the passkey** — pairing approval is always a human's. Run `pair` / `passphrase` only when the person
is at that terminal; setup codes, passphrases and pairing links never go into chat.

```bash
agentj --instance 2 name Alex-J                             # the Agent name the phone shows
agentj --instance 2 label "Sales - Alex"                    # local note, shown only by `agentj instances`, never sent anywhere
agentj --instance 2 doctor                                  # report every ! / ✗ as it is
```

Another harness: `agentj --instance 2 agent codex --independent --dir ~/agentj-seats/2`, then `agentj --instance 2 service restart`.
Another seats root: `export AGENTJ_SEATS_ROOT=/data/seats` before creating (each instance remembers its own folder afterwards).

## Each instance needs a valid seat (enforced)

From 0.20.0 an instance whose seat is not usable — not bound, suspended (unpaid past grace) or not confirmed with the Agent J
account service for more than 72 hours — gets **no phone or Telegram messages into its Agent and cannot pair a remote**; the
host says why and where to fix it. The seat certificate is issued to each instance's own relay channel, so instance 1's seat
never covers instance 2. Check with `agentj --instance N status` (`seat`): `unbound` → bind a seat; `suspended` → renew or add
seats on the account page (it resumes within about 15 minutes); `expired` → network / account service, run
`agentj --instance N doctor`. The host log shows `relay_down reason=seat`.

## Layout

| | instance 1 (the original) | instance N (2–10) |
|---|---|---|
| state (keys, remotes, logs) | `~/.local/state/agentj` | `~/.local/state/agentj-N` |
| preferences | `~/.config/agentj/config.json5` | `~/.config/agentj-N/config.json5` |
| service | `agentj` / `net.agentj.host` | `agentj-N` / `net.agentj.host.N` |
| working folder | unchanged | `~/agentj-seats/N` (`$AGENTJ_SEATS_ROOT/N`) |

Every subcommand takes `--instance N` (before or after the subcommand): `pair devices revoke status doctor service … config …
agent login unlink name admin asr …`; or `export AGENTJ_INSTANCE=N` once. The service unit carries `AGENTJ_INSTANCE=N`, so the
service and the Agent it starts know their number. No option = instance 1.

`agentj instances` (`--json`): number · note / name · online · remotes · seat · service · harness → folder.

## Upgrades

All instances run one installed program. `agentj update apply` (or the phone's update button) on any instance installs the new
version, restarts that instance and **schedules a restart for every other running instance** (each waits for its own idle
moment, up to 10 minutes). The nightly auto-update runs only on instance 1 (or on each instance when there is no instance 1).
Afterwards `agentj instances`; one not back → `agentj --instance N service start` + `agentj --instance N doctor`.

## Unlink / remove an instance

```bash
agentj --instance 3 unlink              # give the seat back to the account first (it becomes an empty seat again)
agentj instances remove 3               # stops the service, deletes its state and preferences — the working folder stays
```
Delete `~/agentj-seats/3` only after the owner confirms the person's files are not needed. Instance 1 cannot be removed this way.
Only one phone to drop: `agentj --instance 3 devices` → `agentj --instance 3 revoke <id>`.

## Troubleshooting

| Symptom | Do |
|---|---|
| `--instance` "unknown argument" | version < 0.20.0: upgrade |
| a person's phone cannot connect | `agentj --instance N service status` → not active: `service start`; then `doctor`; then the seat (above) |
| the account page's "Add a remote" link says "not this computer" | one pairing-link handler per computer finds the instance by the link's channel: `agentj protocol install` |
| two instances use the same Telegram bot | a bot has one receiver (409): one bot per instance, or Telegram on one instance only |
| `agentj sudo-helper install` refused on instance N | one admin helper per OS user, owned by instance 1; other instances use the normal password card |
| local transcription missing on an instance | models live in each instance's state: `agentj --instance N asr install` (one copy each) |
| keep-awake | a whole-computer setting: manage it on instance 1 only |
| Claude / Codex quota used up | every instance shares this OS user's AI logins (`~/.claude`, `~/.codex`) and their quota. Separate Claude: the person runs `claude setup-token` with their own account and writes `CLAUDE_CODE_OAUTH_TOKEN=…` into the instance's service env file themselves (Linux `~/.config/systemd/user/agentj-N.env`, macOS `~/Library/LaunchAgents/net.agentj.host.N.env`, chmod 600), then `agentj --instance N service restart`; OpenCode: `agentj --instance N provider add …`. Tokens never go through chat |
| "the approval channel is not connected" | `agentj --instance N doctor --isolation-only` |

## What the separation covers (tell the owner plainly)

Plan A is **session and visibility separation, not a security boundary between people.**

Covered: each instance's Agent runs fenced (bubblewrap on Linux, sandbox-exec on macOS) and cannot see the other instances' state
(keys, remotes, chat history) or preferences, the other instances' working folders, or instance 1's folder (unless that is the
whole home or holds Agent J itself). Conversations are separate; one person's phone cannot connect to another's instance. Claude
Code, Codex and OpenCode are fenced the same way.

Not covered: the rest of the OS user's files (`~/.ssh`, `~/.aws`, other projects, browser data) stay visible to every instance's
Agent, and an Agent can manage this user's services, so a deliberately misbehaving Agent could leave the fence. If the computer
holds anything a person must not reach, or people must keep things from each other → **plan B**.

## Never

- Rename, move or "migrate" instance 1 — it stays exactly where it is.
- Give instance 2–10 a shared session, or two instances one seat / one setup code / one Telegram bot.
- Copy another instance's or another person's state folder.
- Paste passphrases, setup codes, pairing links or tokens into chat.
