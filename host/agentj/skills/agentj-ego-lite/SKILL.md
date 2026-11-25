---
name: agentj-ego-lite
description: Help me install Ego Lite or connect it as Agent J's dedicated browser on macOS and Windows beta. Includes local browser tools.
description_zh: 在 Mac 和 Windows 测试版上用 Ego Lite 作为 Agent J 的专用浏览器。
usage_zh: 说「帮我装 Ego Lite」「连接 Ego Lite」或「用 Ego Lite 打开这个网页」。
---

默认派 CEO/子 Agent，写验收、复验、汇报；主人明确要求亲做就照办。指纹清单、凭据规则和 harness 硬限制不变。
Delegate by default; follow explicit owner requests to work personally. Verify and report; UV, credential rules and harness limits remain.

## 主人说「帮我装 Ego Lite」 / “Help me install Ego Lite”

Use Ego Lite for the first seat on macOS and Windows (beta). On Windows, Ego Lite is a native app and Agent J runs in WSL2. On Linux, follow `agentj-browser`. If the owner disabled the browser, leave it disabled.

1. On explicit request, download the host's installer from https://lite.ego.app/ if needed. Before installation, verify the app in the macOS disk image with `codesign --verify --deep --strict` and Gatekeeper (Citro Team ID `JGQLC6YQYJ`), or verify the Windows installer's Authenticode status is `Valid` and publisher is `CITRO LABS PTE. LIMITED`. Stop if verification fails.
2. With the owner's installation confirmation, install and open the verified app. Guide the owner through local prompts one at a time using existing system approvals. Have them create a profile named exactly `Agent J`, or select and import their intended Chrome profile, rename it to `Agent J` and verify the site account.
3. Ask the owner to turn off **Help improve ego(lite)’s features and performance** under **Settings → Profile → Citro Labs services** before connecting. This setting covers telemetry that can include task and page content.
4. Run `agentj browser connect` yourself. Ask the owner to confirm **Use Ego Lite** in the first-run checklist on their paired phone. Wait for `agentj browser status --json` to show `ready` with backend `ego-lite`. If the binding is missing, repeat connect and wait for confirmation.

主人开口后按需从官网代下载，先校验系统签名和上面的厂商身份，通过后请主人确认安装。由 Agent 安装并打开浏览器，遇到系统授权时逐步请主人在本机处理。引导主人创建名为 `Agent J` 的资料，或选择要导入的 Chrome 资料，导入后改名并核对网站账号。请主人关闭「产品改进」开关。Agent 自己运行 `agentj browser connect`，请主人在已配对手机上点「使用 Ego Lite」，再检查连接状态。

## Use Ego Lite through Agent J

After the owner confirms the dedicated profile, use the `agentj browser` commands. Run `agentj browser open <url>` to show a page on the computer for the owner. Use `check` and `login` for sign-in. If a site action needs a control these commands do not offer, ask the owner to do it on the computer.

主人在已配对手机上确认专用资料后，Agent 通过 `agentj browser` 打开网页、检查登录或发起登录。Ego Lite 的原生脚本由主人在本机终端自行运行。

## Page API reference for the owner's local terminal

Reserve the vendor CLI and direct scripting for the owner. Ego Lite runs those scripts in its app process environment. In the official Page API guide, `taskSpace` uses an explicitly selected dedicated profile, and a Page handles navigation, snapshots and element actions. Use page-level CDP only for an operation on that Page, such as reading its frame tree. Check the intended account and origin before acting, and close only pages you opened. Keep credentials, cookies and authorization headers out of scripts and output.

## Sign-in and site actions

Use `agentj browser check --site <site> --if-stale --json` for a registered site's sign-in status. Use `agentj browser login <site> --wait --json` when the owner needs to sign in. Show sign-in QR codes only through Agent J's login card on the paired phone. Keep QR images and payloads out of chat, Telegram, screenshots and logs. Let the owner handle passwords, codes and challenges on the site, then verify sign-in with `check`.

A signed-in account identifies the session. Publishing, sending, deleting, paying and changing account settings still require the applicable Agent J authorization. For purchases, orders and money movement, ask on the paired phone every time.

Official references for the owner and local API usage. [Download](https://lite.ego.app/) · [macOS setup](https://lite.ego.app/document/en/docs/quick-start) · [Windows setup](https://lite.ego.app/document/en/docs/windows/quick-start) · [Page API](https://lite.ego.app/document/en/docs/ego-browser) · [Privacy policy](https://lite.ego.app/privacy).
