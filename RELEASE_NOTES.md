# Agent J 0.24.1a2 release notes

Host 0.24.1a2 / install 0.26.2; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.24.1a1 host/site/phone. Patch release: A signed host/site/downloads then B phone web immediately after, in the same 23:00–08:00 JST window; outside-window publication requires recorded owner authorization. No relay, Dashboard or D1 change. The read-only fallback model after a failed turn (optional local model on Apple Silicon), reply buttons that stay tappable, Delivered before Stop this turn, attachment de-duplication, friend answer signatures and upgrade failure diagnostics ship together. Physical iPhone/Android and real Apple Silicon local-model acceptance are not claimed.
## Changes

- When your own model fails a turn, a fallback model looks at the error, read-only, and tells you what to do next: free DeepSeek Flash first, then LongCat while its terms promise no retention or training, then a local model you chose to install. The reply starts with "This is a fallback model answer"; the task is not resent, and the cloud sees only a fixed error signal.
- Apple Silicon Macs with 16 GB of memory or more can add the local fallback model: install and open Ollama, then run `agentj local-model install` (or add `--local-model` to the one-command install); `agentj local-model status` checks it and `agentj local-model off` switches it off.
- Reply option buttons still work after you have sent something else in between; a tap goes out as a reply to that message, and the buttons only grey out once that question has been answered.
- Right after a message is delivered, the button in the message box shows "Delivered" for two seconds before it becomes "Stop this turn", so a tap meant to cancel no longer stops the new turn; after a reconnect, queued messages catch up with their real state.
- Pressing ⌘V after a screenshot has already been added to the attachments no longer adds it twice; the automatic screenshot switch now lives under Settings > Web app, and the note under the attachment opens it.
- The last row on Settings and menu pages (such as "Download Agent J Brand Kit") keeps a proper gap above the bottom edge of the screen.
- A failed automatic upgrade now records which step failed, the exit code and the reason (installer missing, permission denied, read-only disk or timeout) on the computer and in the automatic support report, and puts the previous version back by itself.
- When a friend asks a question, "let my agent answer" or a refusal sometimes did not take effect and the card kept its waiting colour: the signature now matches what the computer checks; if it still does not go through, the card says "Your confirmation did not take effect. Please try again.", and the waiting colour clears the moment you tap.
- A friend's question now expires after 2 minutes without an answer and you get a short notice, instead of waiting for a whole day.
- 你的模型这一轮出错时，Agent J 会请兜底模型只读地看一眼出错原因，告诉你下一步怎么办：先用免费的 DeepSeek Flash，没有就用条款写明不留存、不训练的 LongCat，最后是你自己选装的本地小模型。回复开头标明「这是兜底模型的回答」，原任务不会自动重发，云端只拿到固定的错误信号。
- Apple Silicon、内存 16GB 以上的 Mac 可以选装本地兜底模型：先装好并打开 Ollama，再运行 `agentj local-model install`（一键安装时加 `--local-model`）；`agentj local-model status` 查看，`agentj local-model off` 关掉。
- 回复里的选项按钮，中间发过别的消息再回头点也照样能点，点出去的是对那一条的回复；只有那个问题已经被回答过才会变灰。
- 刚发出的消息送达后，输入框的按钮先显示「已送达」，过两秒才变成「停止这一轮」，不会再一点「取消」就停掉新的一轮；断线重连后，排队中的消息状态会自动对齐。
- 截图自动放进附件后再按一次 ⌘V 不会重复添加；「截图自动进附件」开关挪到设置的「网页版 App」里，附件下方可以直接点过去。
- 设置和菜单页最底下一行（比如「下载 Agent J Brand Kit」）离屏幕底边留足空间，不再贴着圆角。
- 自动升级失败时，本机记录和自动客服报告会写明是哪一步、退出码和原因（找不到安装工具、没有权限、只读磁盘、超时分别说明），失败后会自动装回上一版。
- 好友来问问题时，选「让分身自己回」或拒绝有时不生效、卡片一直亮着等待色：现在签名和电脑核对一致；万一没生效，卡片会提示「确认没生效，请再点一次」；你一点确认或拒绝，等待色立刻消失。
- 好友的问题 2 分钟没回就自动过期，并告诉你一声，不再挂一整天。

## Material acceptance limits

- Android arbitrary-name wake recognition, false-accept/false-reject rates, background operation,
  battery behavior and speech-model licensing are not cleared for a released app.
- Paid-provider sound and cost are unverified; no complete voice catalogue or hardware benchmarks.
- Cross-format migration and power-loss recovery remain unproven; no 99% first-boot AI success claim.
- Telegram input can be dropped after an offset failure; output has no durable retry queue.
- iOS background hotword listening is unsupported.

## Known unfinished inventory entries

- `android-file-provider` (android; partial): Android：选中文件先复制到 App 缓存，相机照片直写. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-gboard-image` (android; partial): Android：Gboard 剪贴板图片粘贴. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-wake-word` (android; partial): Android：「hey jarvis」唤醒词. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-native-take` (android; partial): Android：唤醒录音交给页面转写. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-listen-orb` (android; partial): Android：唤醒后的聆听圆球. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-quick-tile` (android; partial): Android：快捷设置「JARVIS」开关. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-boot-listen` (android; partial): Android：开机/升级后恢复监听. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-status-settings` (android; partial): Android：监听状态与设置页. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-barband` (android; partial): Android：手势条颜色与水位. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-reply-notify` (android; partial): Android：新回复锁屏通知. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-deep-link` (android; partial): Android：点通知直达那一页. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-popup-on-wake` (android; partial): Android：唤醒后悬浮聆听球 / 把 App 拉到前台. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
