---
name: agentj-automation
description: Explain an automation selected on the read-only Automation card, including `/agentj-automation @<unique-name>`, 「这个自动化做什么」「这个任务几点跑」, "what does this automation do", "when does this job run". Resolves the source-qualified job identity on the Agent J host and answers from scheduler facts; never changes a job just because it was mentioned. Also continues a failed multi-step automation from the failed step — 「从失败处继续」「接着跑」「从第 N 步继续」, "continue from where it failed", "resume the automation". Also sets up a new scheduled automation on the system scheduler, headless — 「做个定时任务」「把 agentj tasks 迁到系统定时」, "run this every morning".
---

The official card `automation` shows a bounded read-only catalogue. The @ action puts this skill and the unique
source-qualified name in the composer; the owner adds a question before sending. No question → ask what they want
to know. No name → ask which job, or open `agentj card open automation` if enabled.

Run `agentj card automation list --json` to read the same current catalogue as the phone (even if the card is off).
Match the exact returned `name` and `source`; native jobs and configured Agent J plans are included. Do not execute
the name or a stored task command. Inspect only matching scheduler metadata
with read-only commands using separate argv arguments; for an Agent J plan use its existing task status interface.
A stale name is not permission to choose a similarly named job: explain that it changed and ask which current one.

Explain what is known about purpose, schedule, repetition, next run, AI coding tool and model. Native schedulers
usually do not identify the tool/model; say unavailable instead of inferring it from the executable or the main Agent.
Agent J plans show configured tool/model, not proof of the model used by a past execution. Never print executable
command lines, environment variables, keys or private scheduler payloads. Linux discovery covers user systemd/cron;
macOS covers user launchd/cron; Windows covers Task Scheduler, plus Agent J's own planned tasks where available.
The list does not prove that every background process has been found.

A mention authorizes asking about that job, not running, disabling, rescheduling or deleting it. If the owner separately
asks for a change, use the existing task-management workflow and its normal approvals, then verify the actual state.

## Continue a failed multi-step automation (「从失败处继续」)
An Agent J automation may declare `steps` in its task.json: each step is its own run with its own VERDICT, and Agent J keeps
a checkpoint of every step. When a step fails, the later steps do not start and the failure notice says which step failed,
why, and offers 「从失败处继续」 / "continue from where it failed".

When the owner asks for that (「从失败处继续 <name>」, "continue <name>"):
1. `agentj automation status <automation id or run id>` — which steps finished, which failed and why.
2. `agentj automation resume <automation id or run id>` — finished steps are reused (not run again); the run continues at
   the failed step. If an earlier step's instructions or output files changed since, Agent J starts from that earlier step
   and says why — repeat that reason to the owner.
3. A step that will run again and is not marked `"idempotent": true` may already have done part of its work (sent, posted,
   paid). Agent J then puts one confirmation card on the owner's phone; tell the owner to look at it. Never pass `--yes`:
   it only counts at the owner's own terminal. A decline means nothing ran — do not retry or start the whole automation.
4. Refusals are final for that request: a damaged progress record (offer a full run instead), already being continued
   (continue the newest run), still running, or Stop everything. Do not work around them.

Making a step-based automation: `"steps": [{"id": "fetch", "prompt_file": "STEP1.md", "outputs": ["reports/data.csv"],
"idempotent": true, "retry": 1}, …]` (2–12 steps; `retry` 0–3 only for idempotent steps). Mark a step idempotent only when
running it twice changes nothing outside (reading, drafting into its own files); anything that sends, publishes, pays or
deletes stays non-idempotent.

The signed Continue from the failed step button is available on the failed-run result card and scheduled-task failure row on supporting hosts. It binds the exact run/checkpoint, task contract, device, nonce and120s expiry. Replays and changed contracts are refused; refresh rather than bypassing. Non-idempotent repeats still need the owner's existing card. Friend reports and delegated/Same owner data cannot authorize the button.

## Make a scheduled automation: the system scheduler is the default
When the owner wants something to run on a schedule or again and again (「每天 9 点帮我…」「做个定时任务」, "run this
every morning"), build it on the computer's own scheduler, which starts the AI coding tool headless with full access:
Linux systemd user timer, macOS launchd, Windows Task Scheduler. `agentj tasks` keeps working and stays documented, but
it is no longer the first choice: an Agent J task can wait on a phone card while the owner is away from the phone.

An automation is a routine the owner designed and tested with you. Designing it together, running it once by hand and
the owner saying it is right is the owner's decision: the scheduled runs are pre-approved and run without cards. Do not
add approval steps or limits the owner did not ask for.

### Files
In the workflow's folder (a new workflow is a direct child of the working root, as usual):
- `PROMPT.md`: steps, working folder, files read and written; it ends with a short report whose last line is
  `VERDICT: PASS <one line>` or `VERDICT: FAIL <reason>`. Secrets: only where they are stored and the variable names, never a value.
- `run.sh` (Windows `run.ps1`): runs the headless command and keeps the report and log in `runs/<YYYY-MM-DD>/`; no PASS
  line means it appends `VERDICT: FAIL`, so failures are visible. Use the tool's full path (`command -v codex`): a
  scheduler does not load the owner's shell PATH.

Headless command per AI coding tool (`$W` = working folder, `$OUT` = `runs/$(date +%F)`):
- Codex: `codex exec --sandbox danger-full-access -c approval_policy=never -C "$W" -o "$OUT/report.md" - < PROMPT.md`
- Claude Code: `claude -p --dangerously-skip-permissions < PROMPT.md > "$OUT/report.md"`
- OpenCode: `opencode run --auto --dir "$W" "$(cat PROMPT.md)" > "$OUT/report.md"` (recent versions; check `opencode run --help`)
- Qwen Code: `qwen --approval-mode yolo "$(cat PROMPT.md)" > "$OUT/report.md"`
- Another tool: find a one-shot mode in its `--help` that approves its own tool calls; with none, tell the owner it
  cannot run unattended and offer a tool above.

```bash
#!/bin/bash
D="$(cd "$(dirname "$0")" && pwd)"; W="<working folder>"; OUT="$D/runs/$(date +%F)"; mkdir -p "$OUT"; cd "$W" || exit 1
"<full path of codex>" exec --sandbox danger-full-access -c approval_policy=never -C "$W" -o "$OUT/report.md" - < "$D/PROMPT.md" >> "$OUT/run.log" 2>&1; rc=$?
tail -n 1 "$OUT/report.md" 2>/dev/null | grep -q '^VERDICT: PASS' || echo "VERDICT: FAIL exit $rc (see run.log)" >> "$OUT/report.md"
```

Linux: `~/.config/systemd/user/<name>.service` (`[Service]` `Type=oneshot`, `ExecStart=<full path>/run.sh`) and
`<name>.timer` (`[Timer]` `OnCalendar=*-*-* 21:00:00 <Area/City>`, `Persistent=true`; `[Install]` `WantedBy=timers.target`),
then `systemctl --user daemon-reload && systemctl --user enable --now <name>.timer`. `loginctl show-user "$USER" --property=Linger`:
with `Linger=no` the timer runs only while the owner is logged in; turn it on with
`agentj sudo --why '定时任务在没登录时也能跑' -- loginctl enable-linger <user>` (approved on the phone), or the owner runs
`sudo loginctl enable-linger $USER` themselves.
macOS: `~/Library/LaunchAgents/<label>.plist` (`ProgramArguments` = full path of `run.sh`, `StartCalendarInterval` Hour 21
Minute 0), `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist`; run once: `launchctl kickstart gui/$(id -u)/<label>`.
Windows: `schtasks /Create /TN "<name>" /SC DAILY /ST 21:00 /TR "powershell -NoProfile -ExecutionPolicy RemoteSigned -File <full path>\run.ps1"` (or
`Register-ScheduledTask`); run once: `schtasks /Run /TN "<name>"`.

Test it together: write `PROMPT.md` with the owner until they say it is right; run it once by hand through the scheduler
(`systemctl --user start <name>.service`), read `report.md` and `run.log` and show the owner the report and VERDICT line;
fix and repeat until they confirm; show the next run (`systemctl --user list-timers | grep <name>`). Later runs need no
card; read their reports in the heartbeat. To put a result on the phone, `run.sh` may end with
`agentj notify --from <name> --title "…" --file <report>` after the owner registers the name (see agentj-capability).

## Move an `agentj tasks` task to the system scheduler
When the owner decides to run an existing Agent J task with the system scheduler, as Agent J recommends for recurring
automations (Linux shown; macOS / Windows as above):
1. `agentj tasks list`, `agentj tasks show <id>`: write its steps, working folder, files and outputs into `PROMPT.md`
   (secret locations and variable names only). If `agentj tasks` is unavailable here, read the workflow's `task.json`.
2. `run.sh` with the headless command above, report and log in `runs/<date>/`.
3. `<name>.service` + `<name>.timer` as above; `systemctl --user daemon-reload && systemctl --user enable --now <name>.timer`.
4. `agentj tasks disable <id>` (switch it off; do not delete it), then `systemctl --user start <name>.service` once, read
   `report.md` with the owner, and show `systemctl --user list-timers | grep <name>`.
5. To go back: `systemctl --user disable --now <name>.timer`, then `agentj tasks enable <id>`.

中文：主人决定改用系统定时器时按上面 1–5 步：读出任务写进 PROMPT.md，建 run.sh、`<name>.service`、`<name>.timer`，`agentj tasks disable <id>` 停用（不删除），手动跑一次和主人看报告；退回就关定时器再 `agentj tasks enable <id>`。一起设计、手动跑通一次即视为主人预先认可。
