<!-- generated from https://agentj.app/docs/share-screenshot/zh.md by agentjarvis/tools/manual_skill.mjs — do not edit -->
# 分享截图给 Agent J

截图不必先保存再找相册。分享后，图片会放进输入框；可以补一句话，再发送给电脑上的 Agent。分享文字和链接也不会自动发送。

## 安卓 {#android}

1. 用最新版 Chrome 打开 https://m.agentj.app ，从浏览器菜单添加到主屏幕，安装应用，再从图标打开并完成配对。只创建书签的浏览器不支持系统分享目标。
2. 截图后点系统分享按钮，选择 Agent J。也可以从相册分享多张图片。
3. 图片出现在输入框下方的附件栏，可补一句话，再发送。尚未配对时，请先配对，再分享一次。

（截图：安卓分享内容进入附件栏，Chromium 本地验收截图 — https://agentj.app/docs/share-screenshot/android.zh.png）
最多 10 张，总大小不超过 25 MiB。支持 PNG、JPEG、WebP、GIF、HEIC、HEIF；其他图片格式请先转换。图片只在手机本地暂存，五分钟未接收或系统关闭页面后，请重新分享。网络断开时先连接电脑，不会改走普通上传。

> **提示**：如果分享菜单里没有 Agent J，先从主屏幕打开一次并更新网页；已安装旧版时可能需要重新安装应用。先确认这份浏览器或主屏幕应用已有配对记录。

## iPhone {#iphone}

快捷指令只把第一张图片复制到这台 iPhone 的剪贴板，再打开 Agent J 网页。下文以 Safari 为例；若系统打开其他浏览器，请在实际打开的浏览器中配对。它不上传、不读取消息、不需要 token。Safari 与主屏幕应用分别保存配对；请在快捷指令实际打开的 Safari 中完成一次配对。

### 设置快捷指令 {#install-shortcut}

目前提供[快捷指令源文件](/shortcuts/Agent-J-Share-Screenshot.unsigned.shortcut)，**这是未签名文件，不能当作一键安装版**。已签名文件的从文件导入版本和 iCloud 一键安装链接即将提供，尚未发布。拿到签名版后，在 Safari 下载到「文件」，打开并按快捷指令的系统提示导入。现在可用下面三步在 iPhone 上建立同样的快捷指令：

1. 打开快捷指令，新建并命名为 Agent J · Share Screenshot。在详情中开启在共享表单中显示，只接收图像。
2. 按顺序添加：从列表中获取项目（快捷指令输入，第一项）→ 拷贝到剪贴板（仅本地）→ URL（`https://m.agentj.app/?from=share`）→ 打开 URL。检查只有这四个动作。
3. 截图后从分享菜单运行它，在打开的浏览器点「粘贴截图」，按系统提示完成粘贴，再补一句话发送。长按输入框使用系统粘贴也是备用方式。

（截图：iPhone 快捷指令入口的粘贴按钮，Chromium 模拟手机截图 — https://agentj.app/docs/share-screenshot/iphone.zh.png）
### 剪贴板权限 {#clipboard}

页面只在你点按钮后读取图片。WebKit 官方说明，跨应用的剪贴板读取可能显示系统粘贴菜单，需要再点一次粘贴。拒绝权限、剪贴板没有图片或浏览器不支持读取时，页面会提示；也可以长按输入框粘贴，或用相册按钮选图。

**验收范围**：按钮、允许/拒绝/空剪贴板和 加密附件路径已在 Chromium 验证；iOS Safari 的真实权限弹窗及快捷指令导入仍待 iPhone 实测。这里的图是本地测试页面截图，不是 iPhone 系统分享菜单。

## 隐私 {#privacy}

安卓分享由手机内的网页接收，截图不会以普通表单上传到网站服务器。快捷指令打开的网址只含入口标记，没有图片、正文或凭据。附件沿用手机与电脑之间的端到端加密路径；网页版的安全边界见[隐私与安全](https://agentj.app/security/)。快捷指令复制的图片会留在系统剪贴板，使用后可自行覆盖。

参考：[Chrome Web Share Target](https://developer.chrome.com/docs/capabilities/web-apis/web-share-target)、[WebKit 剪贴板说明](https://webkit.org/blog/10855/async-clipboard-api/)、[Apple 快捷指令签名](https://support.apple.com/zh-cn/guide/shortcuts-mac/apd455c82f02/mac)。
