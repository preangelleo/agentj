---
name: agentj-relay-check
description: Check whether locally configured Agents Relay background accounts for Claude Code, Codex and OpenCode can answer one short headless request. 「检查中转」「检查 Agents Relay」「check relay」. Never change the main Agent or expose a key.
---

# Check an Agents Relay background account

This skill checks only the owner's already-installed background CLIs, not the main Agent. Before running, explain that each installed CLI whose current base URL is the official `agentsrelay.net` or `api.agentsrelay.net` HTTPS domain will make **one short model request** (which may consume plan quota), up to 60 seconds per CLI. Do not run without the owner's instruction to check. No paid/live request is part of installation or automated tests.

Run `python3 -I <this skill directory>/check.py --json` on the host in the same environment that runs the background CLIs. The checker reads only the configured base URL to decide whether to call. It never prints keys, config content, model replies, command output or exception text; output is one fixed status per CLI. Do not paste a package, key, curl command, terminal export, raw diagnostics or CLI config into chat. Never source a key file to perform the check. No account restore, main-Agent switch or configuration write happens here.

Statuses: `ok` = the one call returned output (not proof that every model or plan feature works); `not_installed` = CLI missing; `not_relay` = selected configuration does not point to the official HTTPS domain; `auth` = 401/sign-in issue; `forbidden` = 403/plan or region; `model_or_endpoint` = 404; `quota` = 429/rate or plan; `timeout` = 60-second deadline; `empty_reply` / `call_failed` = no usable response. Do not retry automatically. For an unresolved status, ask the owner to check their plan and the vendor's configuration package; do not request the key in chat.

Use this only as a background account diagnostic; `/switch-harness` is a separate explicit owner decision. This check does not install or fully configure Agents Relay, and cannot verify fleet environments from a single machine.
