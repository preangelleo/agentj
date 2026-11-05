---
name: agentj-report
description: Recurring reports and mails from a fixed template — 「每天给我发市场调研邮件」「每周一把周报发我邮箱」「做成邮件发我」「用 R14 模板」「定期报告」「自定义报告模板」, "send me a daily market-research e-mail", "weekly report by e-mail", "make it an e-mail", "use template R14", "report template". You fill one JSON file with real data, `agentj report validate` checks it against the template's schema, `agentj report send` renders the mail version and mails it; never hand-write HTML.
---

主 Agent 是董事长助理：只派常驻 CEO 或临时子 Agent、写验收、跑验收命令、汇报；除立等可取的一句话小事，绝不亲自干活，主会话随时接主人消息。
Main Agent = chief of staff: dispatch to a resident CEO or temporary sub-Agent, write and run acceptance checks, report. Only immediate one-sentence tasks are yours; keep the main session ready for owner messages.

Fill one JSON file with real data only; never hand-write HTML. Engine/templates: plaza package web-page-report.
The owner's gallery pick (https://agentj.app/web-report/) wins. Default R15; tech news R02; research R14.
Only R15/R02/R14 and custom templates with email.html support mail.

1. `agentj report templates` lists built-ins. `agentj report example <id> > report.json` gives fields;
   `agentj report schema <id>` gives the exact schema. Fill actual facts, omit unknown optional fields.
2. `agentj report validate report.json [-t <id>] --json`: exit 2 gives field-path errors; fix and validate again.
3. `agentj report send report.json [-t <id>] --to-owner`: subject defaults to title. Other recipients use
   `--to`, `--cc`, `--bcc`, `--attach`; mail's allowlist and final-message approval or verified task grant apply.
   `--dry-run -o <fresh.html>` renders without sending. `agentj report render report.json -o <dir>` makes a web page;
   `agentj report publish …` shares it under the existing approval policy.
4. Store template id, recipients and these steps in the automation's brief. On exit 2 fix fields once and retry.

Linux local lane: a complete engine-package digest pinned in the signed host admits templates/schema/example/validate
and render --email in a mandatory offline namespace sandbox without repeated cards. Engine/template/data bytes are
frozen first. Fenced helper checks delegate bounded bytes to the same-user host's fixed offline operation. Host/helper,
integrity, input, resource and sandbox failures return receipts: give them to the workflow owner, resume original inputs,
and do not request another mail grant or claim a card was shown. Changed/unrecognized packages retain the existing
approval policy; sandbox failure never retries unconfined. Inputs and fresh outputs belong in working folders under HOME or
/tmp, without symlinks or hidden/credential paths. No existing output is overwritten. Custom HTML cannot run scripts
or load external resources. Built-in HTTPS cover-image references remain; the mail client may later fetch them.
Other actions, including install, scaffold, web render and publish, retain their approval boundary.

On the approval lane, Workspace template names resolve before approval; 自定义模板在审批期间有变化就拒绝执行。
只发给主人本人（--to-owner）不弹卡的邮件规则不变。Sending to others needs final-message approval unless covered by an exact verified task grant.

Reports go to the owner's mailbox by default: when a primary mailbox is bound and mail works, also send the report
file with `agentj mail send --to-owner --attach <file>`; errors/no_owner_address mean silently keep the file in the reply.

Missing/old package (exit 3): ask the owner, then `agentj plaza request web-page-report --harness <claude_code|codex|opencode>` (one card on their remote; the host installs).
Mail setup: agentj-config. Owner's custom look: `agentj report new-template ~/agent-workspace/report-templates/<id>`;
use `-t <id>` or its directory. Scripts and external resource loads are refused in custom local mail templates.
