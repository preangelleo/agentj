---
name: agentj-automation
description: Explain an automation selected on the read-only Automation card, including `/agentj-automation @<unique-name>`, 「这个自动化做什么」「这个任务几点跑」, "what does this automation do", "when does this job run". Resolves the source-qualified job identity on the Agent J host and answers from scheduler facts; never changes a job just because it was mentioned. Also continues a failed multi-step automation from the failed step — 「从失败处继续」「接着跑」「从第 N 步继续」, "continue from where it failed", "resume the automation".
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
