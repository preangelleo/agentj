# Agent J 0.26.0a1 release notes

Host 0.26.0a1 / install 0.28.0; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard (support knowledge and the plaza redaction table), no D1 migration (app 0039-0041 are live since 0.25.13 and are never replayed); then A signed host/site/downloads together with the front Worker (credential scan table), immediately followed by B phone web, in the same window. No relay change, no feedback D1 change. Preserve0036 revocation state,0037 action UV data,0038 checkout pending records,0041 seat transfer records and local security journals permanently. Never replay migrations or restore an older database snapshot. Use compatible rollback only, otherwise repair forward. Qualified by one fresh full parity run on this tree plus the affected suites of the merged tickets under the owner's 10-27 rule (no full host or real-install acceptance run for this release). Local preparation only; no push or deployment authorized.
## Changes

- [All AI coding tools] The main Agent now writes down its key decisions as it works: what it decided, why, and what it based that on. To see why it did something, just ask it, or run `agentj decide list` in a terminal on your Agent J host. The log stays on your own Agent J host, and e-mail addresses and phone numbers are masked before they are written.
- [All AI coding tools] The main Agent hands out work more carefully: each task says what counts as done and what proof to bring back; with a batch of similar tasks it gets one working before starting the rest; when it needs your decision it gives its recommendation, reasons and risks; when you say "stop for now" it stops at a safe point, saves progress and notes how to continue.
- [All AI coding tools] Work the main Agent runs in the background is meant to be started with `agentj busy run`; only work started that way shows a background robot on your phone, anything else does not. From 0.26.1 this happens automatically.
- [All AI coding tools] The top of the web terminal used to say "Recording"; it now says the activity log is recording, and the English title fits in full on a narrow phone screen. Tap it for a page that explains what your Agent J host records (text only, no screen or sound recording), why, where on your Agent J host it is kept, who can read it and for how long.
- [All AI coding tools] When you move a seat to another Agent J host, the host's transfer code (starting with ajh_) was refused by the "Bind your seat" box on Olares; your Agent J host, the command line and Olares now accept it. While the code is hidden, the phone's transfer card shows its real beginning, and Copy always copies the whole code. On Olares, first update Agent J in the Olares Market.
- 【适用：所有 AI 编程工具】主 Agent 现在会边做边记下关键决定：定了什么、为什么、依据在哪。你想知道它为什么这么做，直接问它，或者在 Agent J 主机的终端里运行 `agentj decide list` 翻出来看。记录只存在你自己的 Agent J 主机上，邮箱和电话写进去之前会自动打码。
- 【适用：所有 AI 编程工具】主 Agent 派活更有章法：每次派活都写清楚做到什么样才算完成、要交什么证据；同一类活先跑通一单再铺开；需要你拍板时，会附上它的推荐、理由和风险；你说「先停」，它会停在安全的地方，存好进度，写清楚之后怎么接着做。
- 【适用：所有 AI 编程工具】主 Agent 放到后台跑的活，要用 `agentj busy run` 启动来登记；登记过的，你的手机上才会出现对应的后台小机器人，没登记的不显示。从 0.26.1 起改为自动登记。
- 【适用：所有 AI 编程工具】网页终端顶上原来写「录像中」，现在改成「操作日志记录中」，手机窄屏上的英文标题也能完整显示。点它会打开一页说明：Agent J 主机记录了什么（只有文字，不录屏、不录声音）、为什么要记、存在 Agent J 主机的哪里、谁能看、保留多久。
- 【适用：所有 AI 编程工具】把席位从一台 Agent J 主机转到另一台时，主机生成的转移码（ajh_ 开头）以前会被 Olares 的「绑定你的席位」框拒收；现在 Agent J 主机、命令行和 Olares 都认这种码。手机上的转移卡遮住码时会显示码本来的开头，点复制永远拿到完整的码。Olares 用户请先在 Olares 应用市场把 Agent J 升级到最新版，再粘贴。

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
