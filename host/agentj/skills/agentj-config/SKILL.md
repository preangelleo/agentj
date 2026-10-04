---
name: agentj-config
description: REQUIRED for Agent J personalization and troubleshooting: wake word / 唤醒词, ASR / 语音识别, TTS / 播报 / 音色, channels / 通道, menus / 命令菜单, shortcuts / 快捷键, themes / 主题, language / 语言; whenever editing ~/.config/agentj/. Read-only reference for safety settings.
---

Use commands before editing files. Verify every change. Never weaken the safety floor.

1. Discover: `agentj config keys --search <keyword> --json` then `agentj config explain <key>`.
2. Read: `agentj config get <key> --source --json` and `agentj config diff`.
3. Change: prefer voice/theme/menu/key semantic commands, otherwise `agentj config set <key> <value> --dry-run --json`, then without `--dry-run`.
4. Verify `applied` and `verify.ok`. If `applied:false`, start/restart the host when the owner chooses; do not claim it has changed on the phone. Run `agentj config validate --json` and `agentj doctor --offline --json`.
5. If direct editing is necessary, only edit `~/.config/agentj/config.json5`. Preserve comments and other preferences. Run validate then `agentj config apply`.
6. Failed apply returns code 3 and retains/restores the last confirmed file; if an ACK is lost, inspect config_revision before retrying; a manually edited bad file leaves the running last-good preferences in use and reports a phone error. Use `agentj config rollback` after inspecting history.

Read-only references: package `config/schema.json`, `config/defaults.json5`, `config/locked.json5`. User overrides are sparse; upgrades never copy factory defaults over them. Use `agentj config migrate --pending` after upgrading; if IDs are pending, stop serve with the owner, run `agentj config migrate`, restart and doctor. Success markers are timestamped; a failure restores the prior file and is safe to retry. Key commands accept the bilingual aliases listed by config keys. No executable hooks. Menu entries insert text; they never execute shell.

Forbidden: hidden `~/.local/state/agentj/` credentials/devices/approval state, package files, tier=human and tier=locked keys. Exit code 2 means the owner must use the established human command; do not substitute another path, environment variable or direct file edit. A fenced Agent cannot access the configuration writer: direct its owner to phone/terminal. Never request a key in chat. Cloud provider keys belong in the owner's local credential mechanism; config only names the environment variable. Cloud ASR sends audio to OpenRouter/provider; cloud TTS sends reply text directly to OpenAI or ElevenLabs and returns audio via Noise. Explain this before switching. Never enable a cloud provider on behalf of the user without their choice.

| Request | Command and validation |
|---|---|
| 唤醒词改成嘿小J | `agentj voice wake 嘿小J`; `agentj voice test-wake --text 嘿小J`. This checks text only; acoustic wake acceptance is separate. |
| 跟随改名 | `agentj config unset voice.wake_word`; `agentj config get voice.wake_word` |
| 播报换成男声 | Discover installed voices on phone or host; ask owner to listen before selecting. `agentj voice set --tts <real voice ID>` |
| 不读回复 | `agentj config set voice.speak_replies false` |
| 通知播报 | `agentj config set voice.speak_notifications true`; test while unlocked |
| 换成深色 | `agentj theme set dark` |
| 说英语 | `agentj config set appearance.language en` |
| 菜单加日报 | `agentj menu add daily-report --label 日报 --run daily-report` |
| 快捷键打开菜单 | `agentj key bind ctrl+shift+j --run open-menu` |
| 改用云识别 | Explain cloud privacy; set `voice.asr.model`, set local key, then `voice.asr.mode cloud`; validate + real sample |
| 恢复全部默认 | Inspect `agentj config diff`; obtain owner confirmation; human `agentj config reset --all` |
| 关掉审批 | Refuse: five danger categories and signed paired-device approval are locked. |

Telegram is optional, own-bot, private owner text only. Enrollment is human-only. Do not promise Telegram media, group routing or approvals. iOS PWA cannot listen for a wake phrase in the background. Do not promise tested arbitrary acoustic wake detection based on a text-comparison test.

Diagnosis: `agentj doctor --json`, `agentj config validate --json`, `agentj config history`, `agentj config diff --against last-good`. Escalate unresolved failures to the community with sanitized diagnostic facts, never conversations or secrets; posting still needs owner approval. Do not claim "99%" is measured.
