---
name: agentj-report
description: Recurring reports and mails from a fixed template — 「每天给我发市场调研邮件」「每周一把周报发我邮箱」「做成邮件发我」「用 R14 模板」「定期报告」「自定义报告模板」, "send me a daily market-research e-mail", "weekly report by e-mail", "make it an e-mail", "use template R14", "report template". You fill one JSON file with real data, `agentj report validate` checks it against the template's schema, `agentj report send` renders the mail version and mails it; never hand-write HTML.
---

The model fills data, the template owns the look: the same automation looks the same every day, and data that fails the
template's schema is never rendered or sent. Engine and templates come from the plaza package web-page-report.

## Steps
1. **Template.** The owner's pick from the gallery (https://agentj.app/web-report/) wins; remember it in the automation's
   brief. No pick → R15 Agent J Noir (the default, no `-t` needed); tech news → `-t R02`; research → `-t R14`.
   Only R15, R02 and R14 have a mail version; the others are web only (`send` refuses them). `agentj report templates` lists all.
2. **Fields.** `agentj report example <id> > report.json` (`agentj report schema <id>` for the exact rules).
3. **Fill** report.json with real data only — no invented figures, links or dates; leave optional fields out rather than guess.
   Unknown fields are errors. Links only `https://` or `mailto:`. Body text may use `- ` lists, `**bold**`, `[text](https://…)`.
4. **Validate** until OK: `agentj report validate report.json [-t <id>] --json`. Exit 2 lists every error by field path
   (`$.sections[0].items[2].url`) — fix those fields and run it again. Never write or patch HTML yourself.
5. **Send.** To the owner: `agentj report send report.json [-t <id>] --to-owner` (no approval needed; subject defaults to the
   data's `title`, or `--subject`). To others: `--to <address or contact name>` (`--cc`, `--bcc`, `--attach`) — mail's
   allowlist and phone approval apply, exactly as in `agentj mail send`. Preview without sending: `--dry-run -o mail.html`.
   Web page instead of mail: `agentj report render report.json -o <dir>`; to share it, `agentj report publish …`.
6. **Scheduled task.** Put these commands in the task's brief (template id, recipients, "fill JSON, validate, send"), so
   every run follows them; on exit 2 the run fixes the fields once and validates again.

## When something is missing
- Exit 3 = the package is not installed (or too old): ask the owner, then
  `agentj plaza install web-page-report --harness <claude_code|codex|opencode>`.
- Mail not set up → see agentj-config (邮件通道 / e-mail channel); keep the report in your reply meanwhile.
- The owner wants their own look: `agentj report new-template ~/agent-workspace/report-templates/<id>` creates a folder
  (template.json, schema.json, example.json, page.html, optional email.html); then use `-t <id>`. Templates run no
  scripts and load nothing from outside.
