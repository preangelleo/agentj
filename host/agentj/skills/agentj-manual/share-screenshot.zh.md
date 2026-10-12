<!-- generated from https://agentj.app/docs/share-screenshot/zh.md by agentjarvis/tools/manual_skill.mjs — do not edit -->
# 分享截图和文件给 Agent J

录音、截图、Word / Excel / PPT、压缩包，任何文件都能发给电脑上的 Agent。先把文件放进输入框下方的附件栏，再补一句话（比如「帮我总结这段录音」），然后发送。分享不会自动发送。

## 能发什么 {#limits}

- 任意类型的文件，一次最多传 10 个。单个最大 200 MiB，够放 1 小时的手机录音。
- 音频和视频会在你的电脑上本地转写。Agent 同时收到转写文字和原件路径。很长的录音按 10 分钟一段转写，最长 2 小时；转写失败时原件照样送到，Agent 会看到失败原因。没有声音的视频只当普通文件。
- 文件从手机到电脑全程走端到端加密的附件通道。大文件大约每秒 1 MB，200 MiB 约 4 分钟。
- 电脑上的 Agent J 是旧版时，上限仍是 25 MiB、只收常见格式；升级后即可。

最简单的路：在任何 App 里先「存储到文件」，再在 Agent J 网页点输入框旁的「文件」按钮选它。下面两种方式能少走几步。

## 安卓 {#android}

1. 用最新版 Chrome 打开 https://m.agentj.app ，从浏览器菜单添加到主屏幕，安装应用，再从图标打开并完成配对。只创建书签的浏览器不支持系统分享目标。
2. 在录音、相册、文件管理等 App 里点系统分享按钮，选择 Agent J。可以一次分享多个文件。
3. 文件出现在输入框下方的附件栏，可补一句话，再发送。尚未配对时，请先配对，再分享一次。

（截图：安卓分享内容进入附件栏，Chromium 本地验收截图 — https://agentj.app/docs/share-screenshot/android.zh.png）
分享内容只在手机本地暂存。五分钟没接收，或者关了页面，就得重新分享。断网时先连电脑，不会走普通上传。

> **提示**：如果分享菜单里找不到 Agent J，或者只能分享图片，先从主屏幕打开一次并更新网页；已安装旧版时可能需要重新安装应用。先确认这份浏览器或主屏幕应用已有配对记录。

## iPhone {#iphone}

iPhone 网页收不到系统分享，得靠快捷指令接力。有两个快捷指令，可以都装上：

- **发给 Agent J**（推荐）：能收语音备忘录、录音、截图、文档、文字。文件会存进「文件」App 的 `快捷指令/Agent J` 文件夹，然后自动打开 Agent J 网页。你点「选择分享的文件」，在「最近项目」里选中它就行。
- **Agent J · Share Screenshot**：只收图片。复制到剪贴板后打开网页，点「粘贴截图」。

两个快捷指令都不上传、不读消息、不需要 token。下文以 Safari 为例；若系统打开其他浏览器，请在实际打开的浏览器中配对。Safari 与主屏幕应用分别保存配对。

### 设置「发给 Agent J」 {#send-file}

目前提供[快捷指令源文件](/shortcuts/Agent-J-Send-File.unsigned.shortcut)，**这是未签名文件，不能当作一键安装版**，签名版和 iCloud 一键安装链接尚未发布。现在可用下面几步在 iPhone 上建立：

1. 打开快捷指令，新建一个，命名为「发给 Agent J」。在详情里开启「在共享表单中显示」，接收类型选「任何」。
2. 按顺序加三个动作：存储文件（输入选「快捷指令输入」，关掉「询问存储位置」，子路径填 `Agent J/`，打开「覆盖现有文件」）→ URL（`https://m.agentj.app/?from=share&via=file`）→ 打开 URL。检查只有这三个动作。存储文件需要打开 iCloud 云盘；不用 iCloud 时，把「询问存储位置」打开，每次选「我的 iPhone」。
3. 在语音备忘录、相册或任何 App 里点分享，选「发给 Agent J」。在打开的网页点「选择分享的文件」，在「最近项目」里点刚存的文件，补一句话后发送。

（截图：快捷指令打开网页后的「选择分享的文件」按钮，Chromium 模拟手机截图 — https://agentj.app/docs/share-screenshot/iphone-file.zh.png）
### 设置截图快捷指令 {#install-shortcut}

[截图快捷指令源文件](/shortcuts/Agent-J-Share-Screenshot.unsigned.shortcut)同样未签名。手动建立：

1. 新建快捷指令，命名为 Agent J · Share Screenshot。在详情里开启“在共享表单中显示”，并设置为只接收图像。
2. 按顺序添加这四个动作：从列表中获取项目（快捷指令输入，第一项）→ 拷贝到剪贴板（仅本地）→ URL（`https://m.agentj.app/?from=share`）→ 打开 URL。检查确认只有这四个动作。
3. 截图后，从分享菜单运行它。在打开的浏览器点「粘贴截图」，按系统提示完成粘贴，再补一句话发送。如果不行，长按输入框使用系统粘贴也是备用方式。

（截图：iPhone 快捷指令入口的粘贴按钮，Chromium 模拟手机截图 — https://agentj.app/docs/share-screenshot/iphone.zh.png）
剪贴板只适合图片。大文件放进去，又慢又容易失败。录音和文件，一律走「发给 Agent J」。

### 剪贴板权限 {#clipboard}

页面只在你点按钮后读取图片。WebKit 官方说明，跨应用的剪贴板读取可能显示系统粘贴菜单，需要再点一次粘贴。拒绝权限、剪贴板没有图片或浏览器不支持读取时，页面会提示；也可以长按输入框粘贴，或用相册按钮选图。

**验收范围**：文件按钮收任意文件、「选择分享的文件」打开选择器、粘贴按钮和加密附件路径已在 Chromium 验证；iPhone 上的分享菜单、快捷指令的「存储文件」和系统文件选择器仍待 iPhone 实测。这里的图是本地测试页面截图，不是 iPhone 系统分享菜单。

## Telegram 发文件 {#telegram}

Telegram 机器人最多只能下载 20 MB 的文件（这是 Telegram 官方限制），20 多分钟的录音往往就超了。超过时，机器人会直接告诉你原因；请改用上面的网页「文件」按钮或「发给 Agent J」快捷指令。

## 隐私 {#privacy}

安卓分享由手机内的网页接收，文件不会以普通表单上传到网站服务器。快捷指令打开的网址只含入口标记，没有文件、正文或凭据；文件留在你自己的「文件」App 里，由你亲手选中。附件沿用手机与电脑之间的端到端加密路径；网页版的安全边界见[隐私与安全](https://agentj.app/security/)。快捷指令复制的图片会留在系统剪贴板，存进「文件」的文件会留在 `Agent J` 文件夹，用后可自行删除。

参考：[Chrome Web Share Target](https://developer.chrome.com/docs/capabilities/web-apis/web-share-target)、[WebKit 剪贴板说明](https://webkit.org/blog/10855/async-clipboard-api/)、[Apple 快捷指令签名](https://support.apple.com/zh-cn/guide/shortcuts-mac/apd455c82f02/mac)。
