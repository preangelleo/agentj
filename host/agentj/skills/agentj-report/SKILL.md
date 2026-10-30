---
name: agentj-report
description: Recurring reports and mails from a fixed template — 「每天给我发市场调研邮件」「每周一把周报发我邮箱」「做成邮件发我」「用 R14 模板」「定期报告」「自定义报告模板」, "send me a daily market-research e-mail", "weekly report by e-mail", "make it an e-mail", "use template R14", "report template". You fill one JSON file with real data, `agentj report validate` checks it against the template's schema, `agentj report send` renders the mail version and mails it; never hand-write HTML.
---

The model fills data, the template owns the look: the same automation looks the same every day, and data that fails the
template's schema is never rendered or sent. Engine and templates come from the plaza package web-page-report.

Every installed report-engine invocation runs only after an action-bound approval passkey check. The host freezes the
engine, template and data bytes before the card; changing the originals while approval waits cannot change that run.
Missing confirmation means no script starts. Sending the rendered mail to anyone else has its own final
recipients/body/attachments card; mail only to the owner's own addresses (`--to-owner`) goes without a card.

## Steps
1. **Template.** The owner's pick from the gallery (https://agentj.app/web-report/) wins; remember it in the automation's
   brief. No pick → R15 Agent J Noir (the default, no `-t` needed); tech news → `-t R02`; research → `-t R14`.
   Only R15, R02 and R14 have a mail version; the others are web only (`send` refuses them). `agentj report templates` lists all.
2. **Fields.** `agentj report example <id> > report.json` (`agentj report schema <id>` for the exact rules).
3. **Fill** report.json with real data only — no invented figures, links or dates; leave optional fields out rather than guess.
   Unknown fields are errors. Links only `https://` or `mailto:`. Body text may use `- ` lists, `**bold**`, `[text](https://…)`.
4. **Validate** until OK: `agentj report validate report.json [-t <id>] --json`. Exit 2 lists every error by field path
   (`$.sections[0].items[2].url`) — fix those fields and run it again. Never write or patch HTML yourself.
5. **Send.** To the owner: `agentj report send report.json [-t <id>] --to-owner` (the owner mails self: no confirmation card;
   subject defaults to the data's `title`, or `--subject`). To others: `--to <address or contact name>` (`--cc`, `--bcc`, `--attach`) — mail's
   allowlist and phone approval apply, exactly as in `agentj mail send`. Preview without sending: `--dry-run -o mail.html`.
   Web page instead of mail: `agentj report render report.json -o <dir>`; to share it, `agentj report publish …`.
6. **Scheduled task.** Put these commands in the task's brief (template id, recipients, "fill JSON, validate, send"), so
   every run follows them; on exit 2 the run fixes the fields once and validates again.

## Reports go to the owner's mailbox by default
Any report-type file you produce for the owner (a report, a review, research findings, minutes, a `.md` / `.pdf`
deliverable) also goes to the owner's own mailbox by default: `agentj mail send --to-owner --attach <file>` (or
`agentj report send … --to-owner` for a template report). No confirmation card is involved — the owner mails self. Do it
only when the owner has a bound primary mailbox and mail is up; `no_owner_address` or any mail error → skip it silently
and keep the file in your reply as usual. A real key inside is masked in the mail by itself and comes as a pickup card.
默认把报告类文件也发到主人自己的邮箱（`--to-owner`，不弹确认卡）；主人没绑主邮箱或邮件不通就静默跳过，照常在回复里给文件。

## When something is missing
- Exit 3 = the package is not installed (or too old): ask the owner, then
  `agentj plaza install web-page-report --harness <claude_code|codex|opencode>`.
- Mail not set up → see agentj-config (邮件通道 / e-mail channel); keep the report in your reply meanwhile.
- The owner wants their own look: `agentj report new-template ~/agent-workspace/report-templates/<id>` creates a folder
  (template.json, schema.json, example.json, page.html, optional email.html); then use `-t <id>`. Templates run no
  scripts and load nothing from outside.

每次调用报告引擎都会先冻结脚本、模板与数据，再请求通行密钥确认。工作区模板名在弹卡前解析；数据文件不存在，或自定义模板在审批期间有变化，就拒绝执行。报告邮件发给外人时另有最终邮件确认；只发给主人本人（--to-owner）不弹卡。
Each engine invocation freezes scripts, templates and data before its passkey card. Workspace template names resolve before approval; missing data or a custom template changed during approval refuses execution. Sending the report to anyone else requires a separate final-message approval; mail only to the owner (`--to-owner`) needs none.
