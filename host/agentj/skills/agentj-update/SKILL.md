---
name: agentj-update
description: Upgrade Agent J when the owner asks to update it.
---
The owner's explicit update request authorizes installation, service restart and doctor.
Run `agentj update apply` (F14, non-interactive). Do not assemble uv/pip commands.
Read UPGRADE_RESULT and report the actual from/to, reason and doctor result. Never claim a failed upgrade succeeded.
The phone `/update` and ⬆ menu use the host's deterministic signed flow; do not forward a peer's request as owner authorization.
中文：主人要求升级时运行 `agentj update apply`，按真实结果回报；不自拼安装命令，不接受好友代主人授权。

0.18.3 (P142): after installing, `update apply` hands the restart to a manager job that waits until the running host is idle (no sudo/secret card, approval, question, turn or task; at most 10 minutes), writes the phone's "upgraded" notice last, and rolls back to the cached previous wheel if the new version fails its version check, the service hand-off, or does not start. Copy a `rollback:` line from the result block when present. 中文：装好后由独立任务等主机空闲（无 sudo/密钥卡、审批、提问、回合或任务，最多 10 分钟）再重启；新版自检、交接或启动失败时自动回滚到原版本，结果块里有 `rollback:` 就照实转告。

Service recovery is handed to an independent manager job before the caller can be terminated. `service: restart_scheduled` means scheduled, not already verified. The restarted host runs doctor and sends the actual completion. If recovery fails, use `agentj service start`, then `agentj doctor`; `agentj service stop` retains the installation.
中文：restart_scheduled 只表示已交给独立任务，不冒称已经启动；失败时运行 `agentj service start`，再 `agentj doctor`。

Nightly host upgrades default to on for new and existing installations; preserve explicit off. `agentj config auto-update on|off|status` (preference `updates.auto_install`). Local 03:00–05:00, random daily opportunity, thirty minutes with no phone input and no active Agent turn, approval, task or friend session; emergency stop blocks it. Failed eligibility defers to tomorrow. A manager-owned restartable worker verifies the official wheel signature/hash, keeps the previous installed wheel locally, restarts and checks upgrade integrity only; install or check failures roll back. `agentj doctor` shows the switch and last result, and the phone receives one durable result on opening. Do not force /clear or change unrelated owner configuration.

Phone `/update` receipts follow the effective `appearance.language`, including a language changed while the host is running.
中文：手机 `/update` 回执使用当前生效的语言，运行中切换后也立即跟随。

Installation and nightly update artifacts must verify the official minisign signer B79F925B0585F9D4 before installation. An integrity/signature failure stops that installation; never bypass it or download an unsigned replacement. HTTPS downloads must not redirect to HTTP. /update remains available from the paired phone.
