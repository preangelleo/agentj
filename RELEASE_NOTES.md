# Agent J 0.25.15a1 release notes

Host 0.25.15a1 / install 0.27.15; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard support knowledge only, no D1 migration (app 0039-0041 are live since 0.25.13 and are never replayed); then A signed host/site/downloads, immediately followed by B phone web, in the same window. No relay change, no feedback D1 change. Preserve0036 revocation state,0037 action UV data,0038 checkout pending records,0041 seat transfer records and local security journals permanently. Never replay migrations or restore an older database snapshot. Use compatible rollback only, otherwise repair forward. Qualified by the affected suites of the one hotfix ticket under the owner's 10-27 rule (no fresh full parity, full host or real-install acceptance run for this release). Local preparation only; no push or deployment authorized.
## Changes

- Ask your stand-in for a picture on Telegram or WeChat and the image now comes back in the same chat; if a model is at capacity it switches to another one, and if drawing fails it says why right there instead of moving the request to the main conversation.
- Under "Full access", your stand-in in your own chats shows no approval cards, just like the main conversation; only spending money, deleting your data, unbinding or moving a seat and sending a key away still need Face ID / Touch ID on this device.
- The handoff confirmation card no longer asks for Face ID; a long press confirms it.
- Huawei / HarmonyOS tablets in desktop mode can confirm with this device's key. One name per device: Face ID on iPhone / iPad, Touch ID on Mac, Android and HarmonyOS, Windows Hello on Windows; that Settings row now looks like the rows above and below it.
- In a shared Codex session, web search, image generation, sub-agents and similar tools are no longer refused as unknown actions.
- 在 Telegram 或微信里让分身画图，图现在直接回到同一个通道；遇到模型满载会自动换一个模型再画，画不出来时也会在通道里说清原因，不再转到主对话。
- 「全放行」下，你本人通道里的分身和主对话一样不弹审批卡；只有花钱、删除你的数据、解绑或转移席位、密钥外发仍要在本机用 Face ID / Touch ID 确认。
- 转交确认卡不再要求 Face ID，长按确认就行。
- 华为 / 鸿蒙平板（桌面模式）可以用本机钥匙确认了。叫法统一：iPhone / iPad 叫 Face ID，Mac、安卓和鸿蒙叫 Touch ID，Windows 叫 Windows Hello；设置页这一行和上下两行同样式。
- Codex 共享会话里的联网搜索、生成图片、派子 Agent 等工具，不再被误判成未知动作而拒绝。

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
