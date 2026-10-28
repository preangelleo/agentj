# Agent J 0.25.16a1 release notes

Host 0.25.16a1 / install 0.27.16; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard support knowledge only, no D1 migration (app 0039-0041 are live since 0.25.13 and are never replayed); then A signed host/site/downloads, immediately followed by B phone web, in the same window. No relay change, no feedback D1 change. Preserve0036 revocation state,0037 action UV data,0038 checkout pending records,0041 seat transfer records and local security journals permanently. Never replay migrations or restore an older database snapshot. Use compatible rollback only, otherwise repair forward. Qualified by the affected suites of the one hotfix ticket under the owner's 10-27 rule (no fresh full parity, full host or real-install acceptance run for this release). Local preparation only; no push or deployment authorized.
## Changes

- Messages from your own WeChat, Telegram private chat and main mailbox go straight to the main conversation, just like messages from the Agent J web page; they wait in line when it is busy, the page shows which channel they came from, and the answer goes back to that channel.
- Actions that need confirmation are still confirmed on the Agent J web page only; the channel just says it was passed to the web page. An e-mail counts as yours only when the sender check passes.
- Ask for a picture in WeChat or another channel: when the main conversation's AI tool cannot draw, your Codex on the Agent J host draws it and sends it back to the channel, also shown on the web page; if it cannot, it says why and what to try instead.
- The confirmation card says the same and offers "Try again" and "Set up again", instead of one button that keeps failing.
- On a remote without Face ID / Touch ID set up, the confirmation card says so from the start and offers "Set up" and "Deny", instead of a button that cannot succeed.
- Settings has one place to set up Face ID / Touch ID, at the top; the web terminal's "Set up above first" takes you there.
- Short fields such as a remote's name are edited in place and saved with Enter or Save in one step, without the full-screen editor.
- Settings buttons sit under their text; the upgrade note says you can also send /update in the conversation.
- The number on the Settings button follows the current status: green on white when idle, the status colour on a dark circle while working, waiting, asking or offline, so it stays easy to read.
- The phone calls the Agent J host "Agent J host" everywhere; "Computer password" is now "Agent J host login password".
- Emergency stop has a shortcut, Alt+Shift+S (⌥+Shift+S on a Mac), shown on the Settings button and the shortcuts page; it still asks you to confirm first. The Skills card shortcut is now Alt+Shift+2.
- In the full-screen editor for multi-line text, ✓ always saves — for example ✓ on a contact's Note saves the whole contact card.
- On the card for a remote without Face ID, the explanation comes first and the "Set up" and "Deny" buttons sit below it.
- The word "key" for Face ID / fingerprint is gone from the screens; they now say Face ID / fingerprint or passkey.
- After a computer is reinstalled or uses a new browser, Settings no longer shows its Face ID / Touch ID as set up: the first failed check says "This remote's Face ID record can't be found (you can set it up again)", and "Set up again", approved on another device that is already set up, makes this one work again.
- Approvals no longer pass one time and fail the next when the passkey is kept in Google Password Manager or another place; Macs and Android tablets confirm card after card.
- After setting up again, Settings shows it as set up at once, and after a reload too.
- If the connection drops while you confirm with Face ID / Touch ID, the confirmation is sent once it reconnects, without tapping again.
- Safari on a Mac can set up Touch ID; if setup does not finish, you can try again without a new approval from another device.
- 你本人的微信、Telegram 私聊和主邮箱发来的消息，直接交给主对话处理，和在 Agent J 网页发消息一样；主对话正忙就排队，网页里标出是从哪个通道来的，回答自动回到原通道。
- 需要确认的操作照旧只在 Agent J 网页确认，通道里只提示「已转到 Agent J 网页，请在那里确认。」；邮件要通过发件人校验才算你本人。
- 在微信等通道里让它画图：主对话的 AI 工具自己不会画时，由 Agent J 主机上你的 Codex 画好发回原通道，网页同步显示；画不出来会说明原因和替代办法。
- 确认卡上也是同一句话，配「再试一次」「重新设置」两个按钮，不再只剩一个反复失败的按钮。
- 还没设置 Face ID / Touch ID 的遥控器，确认卡一开始就说明，并给「去设置」和「拒绝」，不再显示一个点了也过不去的按钮。
- 设置页只留一个设置 Face ID / Touch ID 的入口，在最上面；网页终端里点「先在上面设置」会直接带你过去。
- 给遥控器改名这类短输入直接在原处编辑，回车或点保存一步完成，不再弹出全屏编辑。
- 设置页的按钮统一放在说明文字下面；升级说明里写明也可以在对话里发 /update 升级。
- 设置按钮右上角的数字跟着当前状态变色：空闲是白底绿字，工作、等待、提问、离线时是深色圆底配状态色，字更清楚。
- 手机上指 Agent J 主机的地方都写「Agent J 主机」，「电脑密码」改叫「Agent J 主机登录密码」。
- 紧急停止有了快捷键 Alt+Shift+S（Mac 上 ⌥+Shift+S），设置页按钮和快捷键页都写着；按下后照样先弹确认。「技能」卡的快捷键改为 Alt+Shift+2。
- 多行文字进全屏编辑时，✓ 一律是保存，比如联系人卡片的「备注」点 ✓ 就连同整张卡片一起保存。
- 没设置 Face ID 的确认卡：说明在上，「去设置」「拒绝」按钮在下。
- 去掉了界面上「钥匙」的说法，统一写 Face ID / 指纹或通行密钥。
- 电脑重装系统或换了浏览器后，设置页的本机 Face ID / Touch ID 不再显示「已设好」：第一次确认没通过就提示「这台遥控器的 Face ID 记录找不到了（可以重新设置）」，点「重新设置」由另一台已设好的设备批准后，这台就能重新用上。
- Face ID / 指纹记录存在 Google 密码管理器或换了保存位置时，审批不再时好时坏，Mac 和安卓平板都能连续确认。
- 重新设置成功后，设置页马上显示「已设好」，刷新后也一样。
- 确认 Face ID / Touch ID 时如果连接断了一下，重新连上后这次确认会自动送到，不用再点一次。
- Mac 上的 Safari 也能设置 Touch ID；设置没成功时可以直接再试一次，不用另一台重新批准。

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
