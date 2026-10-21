---
name: agentj-google
description: Google tools on Agent J — the pinned gog CLI, what is ready, and how Gmail / Calendar / Drive / YouTube / Search Console access is prepared. Use when the owner asks to connect Gmail, Calendar, Drive, YouTube or Search Console, asks "连接 Google", "授权 Gmail", "YouTube 上传", "Google 授权过期", or when a task needs a Google API.
---
Check first, separately: `agentj google status --json` (API side, read-only) and the browser login check (website side). Gmail
website sign-in is not Gmail API access; YouTube API access does not sign in to YouTube Studio. Never report "Google is ready".

This version:
- Agent J pre-installs one pinned Google CLI, gog 0.43.0, in its own private home. It never touches the owner's own gog, gcloud or
  gws and never reads their tokens. `agentj google check` probes it; `agentj google install` retries a failed pre-install;
  `agentj google off` = the owner won't use Google tools (removes only Agent J's copy); `agentj google on` turns it back on.
- `existing` in the status lists tools already on the computer (presence only). Existing grants are "found, not verified".
- Purposes and their minimum scopes: `agentj google plan --purpose gmail-read --purpose youtube-publish --json`. Request only what
  the task needs; never all / user / full scopes (only exception: Calendar, below).
- Calendar (owner's own Google project): `agentj google connect --purpose calendar-write` prints the console steps; the owner does
  them in the browser on the Agent J host and downloads the Desktop client JSON there. Then `... --client-file <that path>`, then
  `... --account <their Google e-mail>`: the owner signs in and clicks through on the host. `agentj google connect --status`
  until `ready`; `why` is a fixed word. Say the scope note (full Calendar scope, used only for calendar events). `disconnect`.
- Gmail, Drive, YouTube, Search Console: not authorizable yet. Say so; do not improvise an OAuth flow or ask for any paste.

Rules that never change:
- Tokens, authorization codes, callback URLs, client JSON, client secrets and passwords never enter chat, arguments, stdout, logs,
  Telegram or the cloud. Do not ask for them; do not use `gog auth tokens export` or copy another tool's tokens.
- The owner signs in to Google, does two-step verification and accepts Google's terms and the unverified-app warning themselves.
  You never click "Continue" on that warning and never press "Submit for verification".
- Authorization never permits sending mail or publishing: each send / upload still needs its normal approval.
- YouTube: standard Data API upload, private + publishAt for scheduling; report published only after the scheduled time reads back
  public. A private result with no known reason is "needs attention", not proof of an audit problem. A truly locked video is not
  fixed by changing visibility in Studio; re-uploading needs explicit approval and never deletes the original automatically.
- Expired or revoked authorization pauses only the Google tasks; everything else continues.

中文要点：先分别查 `agentj google status --json`（API）和网页登录检查；网页登录≠API 授权，不报「Google 全部就绪」。本版预装固定版本 gog，
不碰主人自己的 gog/gcloud/gws。日历现在可引导授权：connect 给出控制台步骤 → 主人在本机下载桌面客户端 JSON → `--client-file 路径`
→ `--account 邮箱`，主人在本机浏览器里登录并点继续 → `--status` 到 ready；说明权限是整个日历（只做列出/新建/改/删日程）。
其他用途仍未开放，如实说明，不要用 shell 临时拼授权流程。只请求任务需要的最小权限。绝不索要或转述 token、授权码、回调网址、client JSON、密码；Google 未验证应用
的风险提示由主人自己点继续，你不点，也不点 Submit for verification。授权不等于可以发信或发布，每次发送/上传仍走审批。
