<p align="center"><img src="https://agentj.app/brand/img/shield-128.png" width="96" height="96" alt="Agent J shield logo"></p>

<h1 align="center">Agent J</h1>

<p align="center"><strong>你的 AI Agent 幕僚长。</strong>你只跟一个 Agent 说话，其余的交给它。</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/python-%E2%89%A53.11-3776AB.svg" alt="Python 3.11 or newer">
  <a href="https://github.com/preangelleo/agentj/releases/tag/v0.25.7a1"><img src="https://img.shields.io/badge/release-v0.25.7a1-orange.svg" alt="Latest release: v0.25.7a1"></a>
  <img src="https://img.shields.io/badge/status-beta-yellow.svg" alt="Status: beta">
</p>

<p align="center"><a href="README.md">English</a> · <a href="https://agentj.app/docs/">文档</a> · <a href="https://agentj.app/install.md">安装</a> · <a href="https://agentj.app/security/">安全</a></p>

你只需要跟一个主 Agent 说话：Agent J。它替你调度、监控、维护几十甚至上百个 Agent 工作流，把繁杂的过程消化掉，
只把你需要知道、需要拍板的事送到你的屏幕上。

Agent J 跑的是**你自己的**编程 Agent（Claude Code、Codex、OpenCode、Kimi Code 或通义千问 Qwen Code），就在**你自己的**电脑上。你在手机上跟它说话，
人在哪儿都行，全程端到端加密：跟它聊天，批准或拒绝它想做的事。

## 它是什么

Agent J 由两部分组成，都由你自己运行，代码都在本仓库：

- **主机程序** `agentj`：一个小巧的 Python 程序，装在你的电脑上（macOS、Linux，Windows 通过 WSL2），
  跟你的 Agent 待在一起。它负责启动和看护 Agent，把密钥和历史留在本机，遇到有风险的操作先问你。
- **手机网页端** <https://m.agentj.app>：静态网页，没有任何依赖。跟主机配对一次，之后走加密通道和它通信。

两者之间是我们托管的中继。中继只转发密文，手里没有任何能读你消息的密钥。

## 支持的编程 Agent

下面任选一个装好，用你自己的账号或密钥登录；Agent J 负责驱动它，也能在它们之间切换。

| 编程 Agent | 出品方 | 官方教程 |
|---|---|---|
| Claude Code | Anthropic | <https://code.claude.com/docs/zh-CN/quickstart> |
| Codex | OpenAI | <https://developers.openai.com/codex/cli> |
| OpenCode | OpenCode | <https://opencode.ai/docs/zh-cn/> |
| Kimi Code | 月之暗面 Kimi | <https://www.kimi.com/code/docs/> |
| 通义千问 Qwen Code | 阿里云百炼 | <https://qwenlm.github.io/qwen-code-docs/zh/> |
| DeepSeek Harness（开发者预览版） | DeepSeek | <https://www.npmjs.com/package/@deepseek-ai/dsh> |

大陆常用的模型套餐都能接：Kimi 会员（Kimi Code）、阿里云百炼 Token Plan / Coding Plan（Qwen Code 或 OpenCode）、
火山方舟 Coding Plan、智谱 GLM、DeepSeek（OpenCode）。在海外，OpenCode 也可以接 OpenRouter。

## 功能

- **你的 Agent，你的账号。** Agent 用你自己的登录、设置和原生权限规则。模型费用你直接付给模型服务商。
- **扫码配对一次。** 手机扫二维码，再输入 6 位数字和一个只有你知道的口令，就跟主机配好了。
- **端到端加密。** 手机和电脑之间走 Noise 协议，经过一个只转发密文的盲中继。
- **签名审批。** Agent 申请权限时，手机上弹出一张卡片。每个决定都由手机签名，并记在你的电脑上。
  120 秒没回应就算拒绝。
- **给 Agent 加一道围栏。** Agent J 以隔离模式启动 OpenCode 时，围栏会把 Agent J 的密钥和已配对手机的数据藏起来：
  Linux 上用 bubblewrap，macOS 上用系统自带的 `sandbox-exec`。请求的隔离和实际生效的隔离分开报告。
- **Agent 好友**（可选）。对方在手机上同意后，你的 Agent 就能加对方的 Agent 为好友。两边经邮箱中继端到端加密通信；
  回答好友的是一个不带工具的替身，每段对话你都能在手机上看到。
- **客服机器人**（可选）。公开网址和网页嵌入、主机本地知识库和公司 HTTP 工具，另配独立的安全审核。
  审核配好、主人签名激活之后才对外服务。
- **长程任务。** 在聊天里把一件重复的事说一次（比如「做每周竞品简报」）。Agent 把只有你能定的事集中到一张开工卡上，
  你确认一次，它就按时自己跑、自己交付。
- **夜间签名升级**（默认开启）。只安装签过名、校验过哈希的版本，在本地 03:00–05:00 空闲时段进行；
  失败会恢复到上一个版本。用 `agentj config auto-update off` 关闭。

**零访问是产品铁律：** 我们的服务器上没有任何能读你消息、Agent 回复或凭据的密钥。我们看得到什么、看不到什么，
还有哪些没做完，逐条列在 <https://agentj.app/security/>。

## 快速开始

对你的 AI Agent 说：

> **read https://agentj.app/install.md and do it**

它会把安装和设置都做完，只在必须由人来做的步骤停下来等你。本仓库里的 [`install.md`](install.md) 跟网站上那份逐字节相同；
网站在 <https://agentj.app/install.md.sha256> 公布它的 SHA-256。

只装主机程序，锁定到发布标签（需要 Python 3.11 或更新；版本太旧的话 uv 会自己下载一个）：

```bash
uv tool install "git+https://github.com/preangelleo/agentj@v0.25.7a1#subdirectory=host"
agentj --version
```

然后在手机上打开 <https://m.agentj.app>。席位和付费：<https://agentj.app/account>。

文档：<https://agentj.app/docs/> · 给机器读的索引：<https://agentj.app/llms.txt>

## 核对你装的是什么

每个版本都是一个 git 标签（`v<版本号>`），同一份代码也以 wheel 和源码包的形式发布在我们的网站上。
它们的 SHA-256 在两个地方能查到：网站上文件旁边，以及 `install.md` 的 front matter
（`host_wheel_sha256`、`host_sdist_sha256`）。本仓库的 `install.md` 必须跟网站那份逐字节相同，
所以两边的哈希一定对得上。

```bash
W=$(curl -fsSL https://agentj.app/dl/latest.txt)          # wheel 文件名；源码包见 latest-sdist.txt
curl -fsSLO "https://agentj.app/dl/$W" && curl -fsSLO "https://agentj.app/dl/$W.sha256"
sha256sum -c "$W.sha256"                                  # macOS：shasum -a 256 -c "$W.sha256"   → OK
grep "^host_wheel_sha256:" install.md                     # 本仓库里的同一个值
```

发布的主机 wheel 带 minisign 签名；手动升级和夜间升级之前，都由编译进程序的受信签名者公钥校验。

手机网页端也能这样核对：`node web/build.mjs --print-hash` 打印网页端的合并哈希，它必须等于
<https://m.agentj.app> 徽章面板里显示的哈希。

## 架构

```
 手机（网页端）  ⇄  中继（只见密文）  ⇄  你电脑上的主机 `agentj`  ⇄  你的 Agent
                                                                  （Claude Code / Codex / OpenCode / Kimi Code / Qwen Code）
```

给贡献者的完整架构图（组件、安全模型、数据流）见 [ARCHITECTURE.md](ARCHITECTURE.md)（英文）。

- 密钥只在手机和主机手里；中继只看得到加密后的数据帧。
- 审批在手机上签名，由主机校验并记录。
- 线上格式、握手和消息类型写在 [`protocol/PROTOCOL.md`](protocol/PROTOCOL.md)，官方测试向量在 `protocol/vectors/`。
- 用户文档：<https://agentj.app/docs/>；主机命令参考：[`docs/CLI.md`](docs/CLI.md)。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `host/` | 主机命令行 `agentj`（Python ≥ 3.11）：配对、中继连接、Agent 桥接、审批、围栏；内置 skill 在 `host/agentj/skills/` |
| `web/` | 手机网页端（静态、无依赖）及其 Cloudflare Worker |
| `protocol/` | 协议规范（`PROTOCOL.md`）、共用的 JavaScript Noise 实现、官方测试向量 |
| `android/` | Android 应用源码 |
| `tools/sim-phone/` | 给没有真手机的测试环境用的模拟手机：无头 Chromium 跑真实的网页端，从不绕过配对 |
| `docs/` | 安装、Agent 设置、防休眠指南，以及命令行参考 |
| `install.md` | AI Agent 照着安装 Agent J 的说明书 |
| `setup-agents.sh` `setup-agents.ps1` | 在电脑上配置 AI Agent 的辅助脚本 |

## 哪些开源，哪些托管

| 开源，在本仓库 | 托管，不在本仓库 |
|---|---|
| `host/`，跑在你电脑上的程序 | 中继服务 |
| `web/`，手机网页端及其 Cloudflare Worker | 账户、席位和计费 |
| `protocol/`，规范、实现和测试向量 | Plaza 服务端 |
| 内置 skill（`host/agentj/skills/`） | 账户后台 |
| `install.md` 和配置脚本 | 网站实现 |
| `tools/sim-phone/` | 发布签名密钥 |
| `android/` 源码 | 生产数据 |

跑在你电脑和手机上的东西全在这里，你可以逐行审计。端到端加密意味着中继只转发密文。
订阅费 **$20 per seat per month**（每席位每月 20 美元）付的是托管服务。

## 测试

```bash
node --test protocol/test/*.test.mjs                                   # 用官方向量测 Noise
(cd host && uv sync && .venv/bin/python -m unittest discover -s tests)  # 主机命令行（Linux 上围栏测试需要 bubblewrap）
node web/build.mjs && node --test web/test/*.test.mjs                  # 网页端静态规则 + Worker
```

## 状态

**Beta。** 任何人都能注册。版本之间可能有不兼容的改动。Android 应用目前是供评审的 debug 版，还没上架应用商店。
Windows 上 Agent J 跑在 WSL2 里。

## 参与贡献

欢迎提 bug 和 pull request。请先读 [CONTRIBUTING.md](CONTRIBUTING.md) 和[行为准则](CODE_OF_CONDUCT.md)。

## 安全

请私下报告安全漏洞，见 [SECURITY.md](SECURITY.md)。不要为漏洞开公开 issue。

## 许可证

采用 [Apache License 2.0](LICENSE)。署名与随附的第三方代码见 [NOTICE](NOTICE)。

## 商标

代码是开源的；「Agent J」这个名字、盾牌标志和 agentj.app 域名不在开源范围内。详见 [TRADEMARKS.md](TRADEMARKS.md)。
