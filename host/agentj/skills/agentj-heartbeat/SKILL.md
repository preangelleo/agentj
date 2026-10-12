---
name: agentj-heartbeat
description: The heartbeat — Agent J wakes you after an idle spell to check background work and the owner's to-dos. Use when a message starts with 〔Agent J 心跳〕 / [Agent J heartbeat], or the owner says 「以后每 30 分钟看一次」「心跳改成两小时」「别再定时叫醒了」「醒来以后先看备份任务」「心跳的时候帮我盯着 X」"heartbeat", "check every hour", "stop the heartbeat", "what does the heartbeat do". Changes the interval, turns it on/off and maintains the heartbeat prompt.
---

The heartbeat is Agent J's own wake-up call, the same on Claude Code, Codex and OpenCode. When nothing has happened
for the interval (default 60 minutes: no phone message, no turn, no desktop input), the computer sends you one message
starting with `〔Agent J 心跳〕` (English: `[Agent J heartbeat]`). It is not the owner's words and grants nothing new.
It never comes while you work, a message waits, an approval card is open, a compaction runs, the stop switch is on, or
in the nightly upgrade window (03:00–05:00).

## When a heartbeat arrives
1. The message names the heartbeat prompt `~/.config/agentj/heartbeat.md` and the first 8 hex of its hash.
   - It carries the full prompt the first time after a restart, after the prompt changed and after a compaction /
     clear: read it and follow it.
   - "Prompt unchanged — no need to re-read": follow the one you already have. If you no longer remember it, run
     `agentj heartbeat prompt show`.
2. Do what the prompt says — typically: background sub-Agents, headless jobs and scheduled tasks (`agentj busy list`,
   `agentj capability list`), the newest run reports (never trust an exit code), the owner's to-dos and promises, work that
   died half-way (pick it up or dispatch it to its CEO as usual).
3. Report only what the owner needs to know or decide, in a sentence or two. **Nothing to report → your final reply is
   exactly `〔不回群〕`** (nothing reaches the phone: no page, no notification).
4. Then go back to standby. A heartbeat is not a reason for a long turn; dispatch long work as usual.

If you also schedule your own wake-ups (Claude Code ScheduleWakeup, /loop), both are fine: the host heartbeat is the
safety net that works on every harness and after a crash or restart.

## The owner changes it (say 「记下了」/ "noted" when done)
| Owner says | Run |
|---|---|
| 「以后每 30 分钟看一次」"check every half hour" | `agentj heartbeat set 30m` (5m … 24h: `45m`, `2h`, `1h30m`, `1d`) |
| 「别定时叫醒了」"turn the heartbeat off" | `agentj heartbeat off` (`on` to resume) |
| 「醒来以后也看看 X」"on each heartbeat also check X" | edit the prompt (below) |
| 「心跳现在是什么设置」"how is the heartbeat set" | `agentj heartbeat status` |
| test it now | `agentj heartbeat now` |

## Editing the heartbeat prompt
The prompt is a short Markdown checklist in the owner's language. Change it as a whole:

```
agentj heartbeat prompt show                 # current text + hash
agentj heartbeat prompt set < new-prompt.md  # replace (stdin or --file)
agentj heartbeat prompt reset                # back to the default template
```

- Write it in the owner's language, as numbered steps, ending with "nothing to report → reply exactly 〔不回群〕".
- Keep the owner's instructions in their own words; merge, do not append duplicates; keep it short (it is read on
  every changed beat; at most 16 KiB).
- **Never** put a password, API key, token or other credential in it — the command refuses text that looks like one.
- These commands reach Agent J through its socket, so they work inside the isolated session too; while a message from a
  group member or another non-owner source is being handled, changes are refused.
- The next heartbeat carries the full new text (its hash changed).

Every heartbeat costs one model call; on a metered plan a longer interval costs less. The phone's Settings → Heartbeat
has the same switch and interval.
