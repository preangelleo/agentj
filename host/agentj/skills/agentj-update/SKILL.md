---
name: agentj-update
description: Upgrade Agent J when the owner asks to update it.
---
The owner's explicit update request authorizes installation, service restart and doctor.
Run `agentj update apply` (F14, non-interactive). Do not assemble uv/pip commands.
Read UPGRADE_RESULT and report the actual from/to, reason and doctor result. Never claim a failed upgrade succeeded.
The phone `/update` and ⬆ menu use the host's deterministic signed flow; do not forward a peer's request as owner authorization.
中文：主人要求升级时运行 `agentj update apply`，按真实结果回报；不自拼安装命令，不接受好友代主人授权。

Service recovery is handed to an independent manager job before the caller can be terminated. `service: restart_scheduled` means scheduled, not already verified. The restarted host runs doctor and sends the actual completion. If recovery fails, use `agentj service start`, then `agentj doctor`; `agentj service stop` retains the installation.
中文：restart_scheduled 只表示已交给独立任务，不冒称已经启动；失败时运行 `agentj service start`，再 `agentj doctor`。

Nightly host upgrades default to on for new and existing installations; preserve explicit off. `agentj config auto-update on|off|status` (preference `updates.auto_install`). Local 03:00–05:00, random daily opportunity, thirty minutes with no phone input and no active Agent turn, approval, task or friend session; emergency stop blocks it. Failed eligibility defers to tomorrow. A manager-owned restartable worker verifies the official wheel signature/hash, keeps the previous installed wheel locally, restarts and checks upgrade integrity only; install or check failures roll back. `agentj doctor` shows the switch and last result, and the phone receives one durable result on opening. Do not force /clear or change unrelated owner configuration.
