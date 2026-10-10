---
title: 装好 AI 命令行工具
nav: 手动安装 AI 工具
summary: 空电脑也能开始：三个系统的基础依赖、四个 AI 工具、PATH、登录和全权限设置。
order: 17
---


# 装好 AI 命令行工具

先选系统，再按菜单完成基础依赖和工具安装。所有命令在自己的电脑终端执行。

只想先用 OpenCode 接着安装 Agent J？装好 curl 后直接看下方 OpenCode 段的最快上手路线，不需要先装 Homebrew、Node 或 npm。Node 步骤只用于 npm 安装路线；Claude Code 和 Codex 的官方脚本也不需要 Node。

手机遥控需要工具继续执行；全权限能减少反复弹出的审批。风险：AI 可以以你的用户权限改文件和执行命令，只对可信目录和任务启用。项目或公司设置仍可能要求审批。

[安装 Agent J](/docs/install/) · [电脑上怎么用](/docs/computer/) · [agentj 命令参考](/docs/cli/)

## 一键安装

先在下面对应系统中装好 curl。脚本询问装哪些工具，再用短超时探测官方服务连通性，不发送用户数据。能连通默认原生登录；连不通推荐 AgentsRelay，使用另一条服务入口。你可以改选，也可用 `--relay` / `--no-relay` 指定。连通不代表所在地区或账号一定可用。默认备份并合并全权限配置；`--no-full-access` 保留默认审批。`--dry-run` 不下载、不写文件。macOS 和 Ubuntu/Debian/WSL2 支持自动安装。


```bash
curl -fsSL https://agentj.app/setup-agents.sh | bash
```


更稳妥：先下载、看内容、核对 SHA256，再执行（每条分别复制）。


```bash
curl -fsSL https://agentj.app/setup-agents.sh -o setup-agents.sh
```



```bash
less setup-agents.sh
```



```bash
shasum -a 256 setup-agents.sh
```


SHA256 sh: `971a371c49a9fe07364fcbcf072849d3973cbc7f5cc3554223be517c196994d4`


```bash
bash setup-agents.sh --dry-run
```



```bash
bash setup-agents.sh
```


流程：选工具 → 选原生登录或 AgentsRelay → Relay 路线先注册、隐藏输入 key 并通过真实模型请求 → 装依赖和 CLI → 检查原生二进制并试跑离线帮助 → 添加别名 → 可选启动 Agent J 正式安装助手 → 绑定账号、配对手机。正式助手会通过真实模型调用验证并复用已有 CLI 登录，配对后直接聊天；没有可调用的受支持 CLI 时才要求新服务 key。

npm 安装失败或超时会自动重试 `registry.npmmirror.com`，连同平台可选依赖一起下载，不必加 `--mirror`；该开关仅表示优先镜像。根包与平台包的 SHA512 必须和 npmjs 官方元数据一致，下载到的压缩包也要校验。官方元数据不可用或哈希不一致就停下，不把未核实的镜像当成功。Node 也有回退，每次都校验 SHA256：能取到官方 SHASUMS 就用官方值，否则用镜像 SHASUMS（此时信任镜像 HTTPS 提供的校验值）。镜像只解决下载，不改变模型服务的地区限制。脚本不从 GitHub Releases 下载 CLI 二进制；本站也不托管 Claude Code 或 Codex。

每个 CLI 都要通过 `--version` 和离线 `--help`。原生工具还会检查平台包、CPU 架构、glibc/musl 选择，试跑二进制和随包提供的 ripgrep。缺组件会报`安装不完整：缺 …`并给修复命令，不报成功。Gemini 还会检查并加载它的平台原生 Node 终端组件。macOS 不再按版本号拒绝，实际运行失败才说明是否需要升级系统库。

通过检查的工具会幂等添加 bash/zsh 的 `cx` / `cc` / `oc` / `gx`；Windows 助手为已检查的 WSL 工具添加 PowerShell 函数。打开新终端生效。`--no-aliases` 可关闭（PowerShell 为 `-NoAliases`）；`--no-full-access` 创建不带全权限参数的别名。`cc` 会覆盖交互终端里的 C 编译器命令；编译器可用 `command cc`，或把别名改成 `ccx`。


```bash
bash setup-agents.sh --mirror
```


仅配置全权限也可重跑，已有工具不会重装：


```bash
bash setup-agents.sh --cli opencode,codex --no-relay
```


## macOS 系统 {#macos}

菜单：先检查系统；只装 OpenCode 可直接跳到下方 OpenCode 段。需要 npm 路线时再按 Homebrew → 依赖 → Node → PATH 继续。macOS 自带 `/usr/bin/curl`；找不到 curl 时直接用完整路径。

检查是否已有：


```bash
sw_vers -productVersion
```



```bash
uname -m
```



```bash
/usr/bin/curl --version
```



```bash
command -v brew
```


没有 Homebrew：官方安装器可能要求电脑密码和 Command Line Tools。


```bash
/bin/bash -c "$(/usr/bin/curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```


Apple Silicon（arm64）：


```bash
eval "$(/opt/homebrew/bin/brew shellenv)"
```



```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
```


Intel（x86_64）：


```bash
eval "$(/usr/local/bin/brew shellenv)"
```



```bash
echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
```


默认 zsh 写 `~/.zprofile`；bash 登录终端改写 `~/.bash_profile`，不要两个架构都加。


```bash
xcode-select -p
```



```bash
xcode-select --install
```



```bash
git --version
```



```bash
python3 --version
```



```bash
brew install curl git python xz
```



```bash
node --version
```



```bash
npm --version
```



```bash
brew install node@22
```



```bash
export PATH="$(brew --prefix node@22)/bin:$PATH"
```



```bash
echo 'export PATH="$(brew --prefix node@22)/bin:$PATH"' >> ~/.zshrc
```



```bash
source ~/.zshrc
```


Homebrew 不可用时，从 [Node 官网](https://nodejs.org/en/download) 下载与你架构相符的 LTS 安装包。

## Linux 系统 {#linux}

菜单：系统检查 → apt 装 curl；然后可直接装下方 OpenCode。npm 路线才需要继续 Node → PATH。以下适用 Ubuntu 24.04+ / Debian 12+，其他发行版先用其包管理器装同名依赖。


```bash
uname -sm
```



```bash
command -v curl
```



```bash
git --version
```



```bash
python3 --version
```



```bash
cc --version
```



```bash
make --version
```



```bash
test -f /etc/ssl/certs/ca-certificates.crt
```



```bash
command -v nano
```


没有 curl 也能先执行 apt；sudo 要你在电脑上输入密码。容器/root 可去掉 sudo。


```bash
sudo apt-get update
```



```bash
sudo apt-get install -y curl git ca-certificates build-essential python3 xz-utils nano
```



```bash
node --version
```



```bash
npm --version
```


缺少或低于 Node 22：安装 nvm，再装 Node 22。命令把 Node/npm 放在用户目录。


```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
```



```bash
export NVM_DIR="$HOME/.nvm"
```



```bash
. "$NVM_DIR/nvm.sh"
```



```bash
nvm install 22
```



```bash
nvm alias default 22
```


Node 下载慢可用国内镜像重试：


```bash
NVM_NODEJS_ORG_MIRROR=https://npmmirror.com/mirrors/node nvm install 22
```


脚本需要 Python 3.11+；低版本先升级系统或安装发行版提供的 Python 3.11+。没有 npm 就安装 Node LTS，npm 随它一起安装；不要只装一个旧 npm。

## Windows 系统 {#windows}

菜单：管理员 PowerShell → WSL2 → Ubuntu → 用户名/密码 → Linux 步骤 → 四个工具。Agent J 在 Windows 上只支持 WSL2 路线。需 Windows 10 build 19041+ 或 Windows 11，BIOS 虚拟化开启。

检查是否已有（管理员 PowerShell）：


```powershell
wsl --status
```



```powershell
wsl --list --verbose
```



```powershell
wsl --install -d Ubuntu
```



```powershell
wsl --set-default-version 2
```


已有 Ubuntu 但 VERSION=1：


```powershell
wsl --set-version Ubuntu 2
```


按提示重启 Windows，打开 Ubuntu，首次建立 Linux 用户名/密码。密码输入不显示字符。


```powershell
wsl -d Ubuntu
```


也可以下载 Windows 引导脚本，查看后运行：


```powershell
Invoke-WebRequest https://agentj.app/setup-agents.ps1 -OutFile setup-agents.ps1
```



```powershell
Get-Content ./setup-agents.ps1
```



```powershell
Get-FileHash ./setup-agents.ps1 -Algorithm SHA256
```


SHA256 ps1: `0ef880556453c6a9853fc6f3e71228debec980f072b76688281c3cff58b12ab1`


```powershell
./setup-agents.ps1 -DryRun
```



```powershell
./setup-agents.ps1
```


如果执行策略阻止脚本，不改整机策略；按上面的 wsl 命令手动安装。接下来所有 bash 命令都在 Ubuntu 窗口执行，包括 Linux Tab 的依赖和 Node 指令。工作目录建议放 `~/projects`，不要放 `/mnt/c`。


```bash
sudo apt-get update
```



```bash
sudo apt-get install -y curl ca-certificates
```



```bash
curl -fsSL https://agentj.app/setup-agents.sh | bash
```


详见 [微软 WSL 官方指南](https://learn.microsoft.com/en-us/windows/wsl/install)。

## 安装依赖 {#dependencies}

上面的系统菜单解决缺少 curl / npm / Python / 编译工具。依赖安装失败时停在该步骤，修复网络或包管理器后重跑；脚本会给出本页对应锚点。

## 设置命令路径 {#path}

npm 路线统一用用户目录；下面先让当前终端能找到它，以及官方安装器使用的路径。


```bash
export PATH="$HOME/.local/share/agentj-setup/npm/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
```


zsh 写入并加载（首次执行一次）：


```bash
echo 'export PATH="$HOME/.local/share/agentj-setup/npm/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"' >> ~/.zshrc
```



```bash
source ~/.zshrc
```


bash：这一行必须放在 `~/.bashrc` 非交互 early-return 前面；下面用 sed 插在开头，再加载。


```bash
touch ~/.bashrc
```



```bash
sed -i.bak '1i export PATH="$HOME/.local/share/agentj-setup/npm/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"' ~/.bashrc
```



```bash
source ~/.bashrc
```


macOS 的 BSD sed 不接受这条 Linux 命令；mac bash 用编辑器把同一 export 行放在 ~/.bashrc 开头，并在 ~/.bash_profile 里加载 ~/.bashrc。新开终端后再次验证。


```bash
command -v node
```



```bash
command -v npm
```


## 安装 OpenCode {#opencode}

检查是否已有：


```bash
opencode --version
```



```bash
command -v opencode
```


主路线（官方脚本；macOS / Linux / WSL，不需要 Node/npm）：

```bash
curl -fsSL https://opencode.ai/install | bash
```

装完提示 `command not found`：按自己的 shell 选择下面一组。macOS 默认 zsh；`>>` 会自动创建不存在的 `~/.zshrc`。每条分别复制。

zsh（macOS 默认）：

```bash
echo 'export PATH="$HOME/.opencode/bin:$PATH"' >> ~/.zshrc
```



```bash
source ~/.zshrc
```



```bash
opencode --version
```

bash（Linux / WSL；没有 `~/.bashrc` 也会自动创建）：

```bash
echo 'export PATH="$HOME/.opencode/bin:$PATH"' >> ~/.bashrc
```



```bash
source ~/.bashrc
```



```bash
opencode --version
```

新开终端后再验证一次。bash 的 PATH 行应放在非交互 early-return 前；macOS 若使用 bash 登录终端，还需在 `~/.bash_profile` 中加载 `~/.bashrc`。

npm 备用路线（先安装 Node/npm，并完成上方 PATH 设置）：


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" opencode-ai
```


国内 npm 镜像备用：


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" opencode-ai --registry=https://registry.npmmirror.com
```


检查 PATH，验证安装，再自己登录；密钥只在自己的终端输入。


```bash
command -v opencode
```



```bash
opencode --version
```



```bash
opencode auth login
```


### 最快上手路线（推荐给新用户）

1. 用上方官方脚本安装 OpenCode；若找不到命令，先做紧跟脚本的 PATH 修复。
2. 在自己的终端运行 `opencode`。
3. 在 OpenCode 里输入 `/connect`，搜索并选择 DeepSeek，在密钥输入框粘贴自己的 API key。密钥只在自己的终端输入，不要发到聊天、席位卡或截图里。也可先在终端运行 `opencode auth login`，选择 DeepSeek 完成登录。
4. 在 OpenCode 里输入 `/models`，选择可用的 DeepSeek 模型。
5. 把席位卡上的安装提示词粘贴到 OpenCode 的对话中，让它接着安装 Agent J；按提示完成需要你亲自操作的步骤。

[DeepSeek 官方接入指南](https://opencode.ai/docs/providers/#deepseek)

全权限启动：


```bash
opencode --auto
```


[OpenCode 官方指南](https://opencode.ai/docs/cli/)

当前官方 --auto 自动批准未被明确 deny 的动作；旧版报 unknown option 时先升级。永久配置见下方 permission=allow。

## 安装 Codex CLI {#codex-cli}

检查是否已有：


```bash
codex --version
```



```bash
command -v codex
```


主路线（官方脚本；macOS / Linux / WSL，不需要 Node/npm）：

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

装完提示 `command not found`：按自己的 shell 选择下面一组。macOS 默认 zsh；`>>` 会自动创建不存在的 `~/.zshrc`。每条分别复制。

zsh（macOS 默认）：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```



```bash
source ~/.zshrc
```



```bash
codex --version
```

bash（Linux / WSL；没有 `~/.bashrc` 也会自动创建）：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```



```bash
source ~/.bashrc
```



```bash
codex --version
```

新开终端后再验证一次。bash 的 PATH 行应放在非交互 early-return 前；macOS 若使用 bash 登录终端，还需在 `~/.bash_profile` 中加载 `~/.bashrc`。

npm 备用路线（先安装 Node/npm，并完成上方 PATH 设置）：


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @openai/codex
```


备用路线（brew 仅 macOS）：


```bash
brew install --cask codex
```


国内 npm 镜像备用：


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @openai/codex --registry=https://registry.npmmirror.com
```


检查 PATH，验证安装，再自己登录；密钥只在自己的终端输入。


```bash
command -v codex
```



```bash
codex --version
```



```bash
codex login
```


全权限启动：


```bash
codex --dangerously-bypass-approvals-and-sandbox
```


[Codex CLI 官方指南](https://developers.openai.com/codex/cli/reference/)

## 安装 Claude Code {#claude-code}

检查是否已有：


```bash
claude --version
```



```bash
command -v claude
```


主路线（官方脚本；macOS / Linux / WSL，不需要 Node/npm）：

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

装完提示 `command not found`：按自己的 shell 选择下面一组。macOS 默认 zsh；`>>` 会自动创建不存在的 `~/.zshrc`。每条分别复制。

zsh（macOS 默认）：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```



```bash
source ~/.zshrc
```



```bash
claude --version
```

bash（Linux / WSL；没有 `~/.bashrc` 也会自动创建）：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```



```bash
source ~/.bashrc
```



```bash
claude --version
```

新开终端后再验证一次。bash 的 PATH 行应放在非交互 early-return 前；macOS 若使用 bash 登录终端，还需在 `~/.bash_profile` 中加载 `~/.bashrc`。

npm 备用路线（先安装 Node/npm，并完成上方 PATH 设置）：


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @anthropic-ai/claude-code
```


国内 npm 镜像备用：


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @anthropic-ai/claude-code --registry=https://registry.npmmirror.com
```


检查 PATH，验证安装，再自己登录；密钥只在自己的终端输入。


```bash
command -v claude
```



```bash
claude --version
```



```bash
claude auth login
```


全权限启动：


```bash
claude --dangerously-skip-permissions
```


[Claude Code 官方指南](https://code.claude.com/docs/en/setup)

## 安装 Gemini CLI {#gemini-cli}

Google 已停用 Gemini CLI 个人登录，只能使用付费 Gemini API key。默认不推荐安装，只有明确选择 Gemini 时才安装并添加 `gx` 别名。

检查是否已有：


```bash
gemini --version
```



```bash
command -v gemini
```


主路线（npm；先完成 Node 和 PATH）：


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @google/gemini-cli
```


备用路线（brew 仅 macOS）：


```bash
brew install gemini-cli
```


国内 npm 镜像备用：


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @google/gemini-cli --registry=https://registry.npmmirror.com
```


检查 PATH，验证安装，再自己登录；密钥只在自己的终端输入。


```bash
command -v gemini
```



```bash
gemini --version
```



```bash
gemini
```


全权限启动：


```bash
gemini --yolo --sandbox=false
```


[Gemini CLI 官方指南](https://geminicli.com/docs/get-started/installation/)

Google 已停用 Gemini CLI 个人登录，只能使用付费 Gemini API key；当前 Gemini 默认审批设置不能填 yolo，必须在启动命令里带 --yolo。Agent J 当前可接 Claude/Codex/OpenCode；Gemini 可在电脑上使用，但尚不能作为 Agent J 主 Agent。

## 全权限启动（三种方式） {#full-access}

全权限意味着 Agent 可不经询问执行命令、修改文件；只在自己的电脑和信任的目录里用。公司管理策略、显式禁止规则仍可能生效。

### 1. 命令参数

在终端运行所选工具的一行命令：

Codex

```bash
codex --dangerously-bypass-approvals-and-sandbox
```

Claude Code

```bash
claude --dangerously-skip-permissions
```

OpenCode

```bash
opencode --auto
```

Gemini

```bash
gemini --yolo --sandbox=false
```

OpenCode 的 `--auto` 自动批准原本需要询问的操作，保留显式 deny。

### 2. 修改配置文件

下面按工具顺序列出路径和字段。**先备份，在编辑器中合并；不要把整个文件替换成示例，也不要重复添加已有键。** 配置可能含 key，不要发到聊天。

| 工具 | macOS / Linux | Windows PowerShell |
| --- | --- | --- |
| Codex | `~/.codex/config.toml` | `$HOME\.codex\config.toml` |
| Claude Code | `~/.claude/settings.json` | `$HOME\.claude\settings.json` |
| OpenCode | `~/.config/opencode/opencode.json` | `$HOME\.config\opencode\opencode.json` |
| Gemini | `~/.gemini/settings.json` | `$HOME\.gemini\settings.json` |

Codex

```toml
approval_policy = "never"
sandbox_mode = "danger-full-access"
```

Claude Code

```json
{"permissions":{"defaultMode":"bypassPermissions"}}
```

OpenCode

```json
{"permission":"allow"}
```

Gemini

```json
{"tools":{"sandbox":false}}
```

Codex：两个键放在顶层、任何 `[table]` 之前。Claude Code：合并到用户配置的 `permissions`；项目配置里的 `bypassPermissions` 不生效。OpenCode：如果已有 `opencode.jsonc`，编辑它；改成 `permission: "allow"` 会替换原来的细分权限，只在确实希望全部放行时做。若设置了 `XDG_CONFIG_HOME`，使用该目录下的 `opencode/opencode.json`。Gemini：`tools.sandbox: false` 只关闭沙箱；`general.defaultApprovalMode` **不接受 `yolo`**，不能单靠配置做到全部免询问，用第一种命令或下面 `gx`。

#### macOS 配置编辑 {#macos-config}

先备份，再在编辑器中手动合并上方字段；只复制所用工具的命令。WSL2 用 Linux 标签。OpenCode 若已有 jsonc 文件会优先编辑它。

Codex

```bash
file="$HOME/.codex/config.toml"
mkdir -p "$(dirname "$file")"
if test -f "$file"; then cp -p "$file" "$file.backup-$(date +%Y%m%d-%H%M%S)"; fi
nano "$file"
```

Claude Code

```bash
file="$HOME/.claude/settings.json"
mkdir -p "$(dirname "$file")"
if test -f "$file"; then cp -p "$file" "$file.backup-$(date +%Y%m%d-%H%M%S)"; fi
nano "$file"
```

OpenCode

```bash
file="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.json"
test ! -f "${file}c" || file="${file}c"
mkdir -p "$(dirname "$file")"
if test -f "$file"; then cp -p "$file" "$file.backup-$(date +%Y%m%d-%H%M%S)"; fi
nano "$file"
```

Gemini

```bash
file="$HOME/.gemini/settings.json"
mkdir -p "$(dirname "$file")"
if test -f "$file"; then cp -p "$file" "$file.backup-$(date +%Y%m%d-%H%M%S)"; fi
nano "$file"
```

#### Linux 配置编辑 {#linux-config}

先备份，再在编辑器中手动合并上方字段；只复制所用工具的命令。WSL2 用 Linux 标签。OpenCode 若已有 jsonc 文件会优先编辑它。

Codex

```bash
file="$HOME/.codex/config.toml"
mkdir -p "$(dirname "$file")"
if test -f "$file"; then cp -p "$file" "$file.backup-$(date +%Y%m%d-%H%M%S)"; fi
nano "$file"
```

Claude Code

```bash
file="$HOME/.claude/settings.json"
mkdir -p "$(dirname "$file")"
if test -f "$file"; then cp -p "$file" "$file.backup-$(date +%Y%m%d-%H%M%S)"; fi
nano "$file"
```

OpenCode

```bash
file="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.json"
test ! -f "${file}c" || file="${file}c"
mkdir -p "$(dirname "$file")"
if test -f "$file"; then cp -p "$file" "$file.backup-$(date +%Y%m%d-%H%M%S)"; fi
nano "$file"
```

Gemini

```bash
file="$HOME/.gemini/settings.json"
mkdir -p "$(dirname "$file")"
if test -f "$file"; then cp -p "$file" "$file.backup-$(date +%Y%m%d-%H%M%S)"; fi
nano "$file"
```

#### Windows 配置编辑 {#windows-config}

先备份，再在编辑器中手动合并上方字段；只复制所用工具的命令。WSL2 用 Linux 标签。OpenCode 若已有 jsonc 文件会优先编辑它。

Codex

```powershell
$file = "$HOME\.codex\config.toml"
New-Item -ItemType Directory -Force (Split-Path -Parent $file) | Out-Null
if (Test-Path -LiteralPath $file) { Copy-Item -LiteralPath $file -Destination ($file + ".backup-" + (Get-Date -Format yyyyMMdd-HHmmss)) }
notepad $file
```

Claude Code

```powershell
$file = "$HOME\.claude\settings.json"
New-Item -ItemType Directory -Force (Split-Path -Parent $file) | Out-Null
if (Test-Path -LiteralPath $file) { Copy-Item -LiteralPath $file -Destination ($file + ".backup-" + (Get-Date -Format yyyyMMdd-HHmmss)) }
notepad $file
```

OpenCode

```powershell
$file = "$HOME\.config\opencode\opencode.json"
if ($env:XDG_CONFIG_HOME) { $file = Join-Path $env:XDG_CONFIG_HOME "opencode\opencode.json" }
if (Test-Path -LiteralPath ($file + "c")) { $file += "c" }
New-Item -ItemType Directory -Force (Split-Path -Parent $file) | Out-Null
if (Test-Path -LiteralPath $file) { Copy-Item -LiteralPath $file -Destination ($file + ".backup-" + (Get-Date -Format yyyyMMdd-HHmmss)) }
notepad $file
```

Gemini

```powershell
$file = "$HOME\.gemini\settings.json"
New-Item -ItemType Directory -Force (Split-Path -Parent $file) | Out-Null
if (Test-Path -LiteralPath $file) { Copy-Item -LiteralPath $file -Destination ($file + ".backup-" + (Get-Date -Format yyyyMMdd-HHmmss)) }
notepad $file
```

回退：从对应 `.backup-*` / `.agentj-backup` 恢复原文件；新建文件只移除本次添加的字段。未备份时，把 Codex 改为 `approval_policy = "on-request"` / `sandbox_mode = "workspace-write"`，Claude 改为 `defaultMode: "default"`，OpenCode 改为 `permission: "ask"`，Gemini 移除 `tools.sandbox` 并去掉启动参数。退出当前 Agent，重新启动。

核验日期 2026-10-09：本机 Codex 0.159.2、Claude Code 2.1.289、OpenCode 1.18.32、Gemini 0.60.0 的 `--help` 及官方文档；管理策略和项目配置可能覆盖用户值。

[Codex configuration](https://developers.openai.com/codex/config-reference/) · [Claude Code settings](https://code.claude.com/docs/en/settings) · [OpenCode permissions](https://opencode.ai/docs/permissions/) · [Gemini configuration](https://geminicli.com/docs/reference/configuration/)

### 3. 短别名

重复执行下列命令不会重复追加同一行。已有同名别名或函数时，先检查并决定是否替换；下列定义会接管同名交互命令。`cx` / `cc` / `oc` / `gx` 支持在后面继续加参数。`cc` 是 macOS/Linux 的系统 C 编译器名字；别名只影响交互终端，不影响普通编译脚本。不想覆盖可把命令中的 `cc` 改为 `ccx`。

回退：在对应 rc 文件或 `$PROFILE` 中删除本次四行；新开终端。当前 Bash/zsh 用 `unalias cx cc oc gx`，PowerShell 用 `Remove-Item Function:cx,Function:cc,Function:oc,Function:gx`。

#### macOS 短别名 {#macos-aliases}

zsh：追加到 `~/.zshrc`，命令最后的 `source` 立即生效，也可新开终端。

Codex (`cx`)

```bash
rc="$HOME/.zshrc"
touch "$rc"
line="alias cx='codex --dangerously-bypass-approvals-and-sandbox'"
grep -Fqx -- "$line" "$rc" || printf '\n%s\n' "$line" >> "$rc"
source "$rc"
```

Claude Code (`cc`)

```bash
rc="$HOME/.zshrc"
touch "$rc"
line="alias cc='claude --dangerously-skip-permissions'"
grep -Fqx -- "$line" "$rc" || printf '\n%s\n' "$line" >> "$rc"
source "$rc"
```

OpenCode (`oc`)

```bash
rc="$HOME/.zshrc"
touch "$rc"
line="alias oc='opencode --auto'"
grep -Fqx -- "$line" "$rc" || printf '\n%s\n' "$line" >> "$rc"
source "$rc"
```

Gemini (`gx`)

```bash
rc="$HOME/.zshrc"
touch "$rc"
line="alias gx='gemini --yolo --sandbox=false'"
grep -Fqx -- "$line" "$rc" || printf '\n%s\n' "$line" >> "$rc"
source "$rc"
```

#### Linux 短别名 {#linux-aliases}

bash：追加到 `~/.bashrc`；zsh 用户切到 macOS 标签。最后 `source` 生效，也可新开终端。

Codex (`cx`)

```bash
rc="$HOME/.bashrc"
touch "$rc"
line="alias cx='codex --dangerously-bypass-approvals-and-sandbox'"
grep -Fqx -- "$line" "$rc" || printf '\n%s\n' "$line" >> "$rc"
source "$rc"
```

Claude Code (`cc`)

```bash
rc="$HOME/.bashrc"
touch "$rc"
line="alias cc='claude --dangerously-skip-permissions'"
grep -Fqx -- "$line" "$rc" || printf '\n%s\n' "$line" >> "$rc"
source "$rc"
```

OpenCode (`oc`)

```bash
rc="$HOME/.bashrc"
touch "$rc"
line="alias oc='opencode --auto'"
grep -Fqx -- "$line" "$rc" || printf '\n%s\n' "$line" >> "$rc"
source "$rc"
```

Gemini (`gx`)

```bash
rc="$HOME/.bashrc"
touch "$rc"
line="alias gx='gemini --yolo --sandbox=false'"
grep -Fqx -- "$line" "$rc" || printf '\n%s\n' "$line" >> "$rc"
source "$rc"
```

#### Windows 短别名 {#windows-aliases}

PowerShell：追加函数到当前 `$PROFILE`，最后一行加载。也可新开同一种 PowerShell 终端；如果你的执行策略不允许加载 profile，直接用第一种启动命令，不修改公司策略。

Codex (`cx`)

```powershell
$dir = Split-Path -Parent $PROFILE
New-Item -ItemType Directory -Force $dir | Out-Null
if (!(Test-Path -LiteralPath $PROFILE)) { New-Item -ItemType File $PROFILE | Out-Null }
$line = 'function cx { codex --dangerously-bypass-approvals-and-sandbox @args }'
if (@(Get-Content -LiteralPath $PROFILE) -cnotcontains $line) { Add-Content -LiteralPath $PROFILE -Value ("`n" + $line) }
. $PROFILE
```

Claude Code (`cc`)

```powershell
$dir = Split-Path -Parent $PROFILE
New-Item -ItemType Directory -Force $dir | Out-Null
if (!(Test-Path -LiteralPath $PROFILE)) { New-Item -ItemType File $PROFILE | Out-Null }
$line = 'function cc { claude --dangerously-skip-permissions @args }'
if (@(Get-Content -LiteralPath $PROFILE) -cnotcontains $line) { Add-Content -LiteralPath $PROFILE -Value ("`n" + $line) }
. $PROFILE
```

OpenCode (`oc`)

```powershell
$dir = Split-Path -Parent $PROFILE
New-Item -ItemType Directory -Force $dir | Out-Null
if (!(Test-Path -LiteralPath $PROFILE)) { New-Item -ItemType File $PROFILE | Out-Null }
$line = 'function oc { opencode --auto @args }'
if (@(Get-Content -LiteralPath $PROFILE) -cnotcontains $line) { Add-Content -LiteralPath $PROFILE -Value ("`n" + $line) }
. $PROFILE
```

Gemini (`gx`)

```powershell
$dir = Split-Path -Parent $PROFILE
New-Item -ItemType Directory -Force $dir | Out-Null
if (!(Test-Path -LiteralPath $PROFILE)) { New-Item -ItemType File $PROFILE | Out-Null }
$line = 'function gx { gemini --yolo --sandbox=false @args }'
if (@(Get-Content -LiteralPath $PROFILE) -cnotcontains $line) { Add-Content -LiteralPath $PROFILE -Value ("`n" + $line) }
. $PROFILE
```

## 配置 AgentsRelay {#agentsrelay}

任何 CLI 安装之前，先到 https://agentsrelay.net 注册并购买对应工具的套餐。官方引导脚本隐藏输入分组 key，实际调用模型确认可用。失败可以重贴，最多三次，也可改走原生登录。我们不发临时额度 token；默认模型由官方脚本选择，我们不写死模型名。Claude 和 OpenAI 分组 key 不同，要提供所选工具需要的分组。Gemini 只能使用付费 API key，Google 个人登录已停用。无终端时 Relay 路线会在安装 CLI 前停下，提示回自己的终端重跑。key 不要发进 AI 对话。


```bash
curl -fsSL https://agentsrelay.net/onboard/agentsrelay-setup.sh -o agentsrelay-setup.sh
```



```bash
sh agentsrelay-setup.sh --paste --only claude,codex,opencode --no-launch
```


之后新开终端，让环境变量生效。官方回滚：


```bash
python3 ~/.config/agentsrelay/agentsrelay-setup.py --restore
```


[AgentsRelay 官方配置页](https://agentsrelay.net/onboard/)

## 运行要求 {#requirements}

macOS：先试运行 `opencode --version`（或所选 Agent 的 `--version`）；能正常运行就能继续安装，不按系统版本号拦截。一键安装会实际试运行它下载的固定版 OpenCode；若实际出现 dyld / _ubrk_clone 或无法运行，才提示失败原因。PATH 或 npm 重装不能修复系统库，请升级系统、换电脑，或用能正常运行的其他 Agent 按手动路线安装。Intel Mac 使用 x86_64 工具与 /usr/local Homebrew，Apple Silicon 使用 arm64 与 /opt/homebrew；不要混用架构。Gemini 当前官方列 macOS 15+。物理 Intel Mac/Windows 安装尚需对应机器验证。

中国大陆：Claude Code / Codex 官方服务不可用，npm 镜像只帮你下载，不能解锁服务。选择 OpenCode + 国产模型，或使用有对应套餐/模型的 AgentsRelay；需自己核对服务提供方和地区条款。Windows 只走 WSL2。

command not found：回 PATH 步骤，确认 shell 文件和新开终端。EACCES：使用本页用户目录 npm prefix，不 sudo npm。登录失败：核对账号、地区和自己的代理，别把 key 贴出来。

## 安装 Agent J {#agent-j}

结尾会问`现在安装 Agent J 吗？[Y/n]`。选 Y 就调用现有正式安装助手，需要账户页的安装码（`AJI-…`），在本机隐藏输入。`--with-agentj` 选择这一步，`--no-agentj` 跳过；账号确认和手机配对仍要你本人操作。无终端时只打印下一步。没检测到登录或 key 时，会先提示：`Agent J 能装能配对，但要登录 AI 工具或填 key 后才能真正聊天`。检测到凭据不等于确认它仍有效。

正式助手会实际调用已安装的 Claude Code、Codex 和 OpenCode，验证能回复后复用本机登录；多个可用时由你选择。手机配对后，发 Hello 就能开始聊天。都不能调用时，才继续填写正式服务地址、模型和手机密钥卡。AgentsRelay 已配好的凭据也能由后台服务读取，无需新开终端。Gemini 可以由本页安装，但当前还不能作为 Agent J 的主 Agent。

```text
Please download https://agentj.app/install.md with curl, read the whole file with your file-reading tool, then follow it step by step, re-reading each section before you do it. Tell me when you need me to do something.
```


[安装流程](/docs/install/) · [install.md](/install/) · [命令参考](/docs/cli/)

[让电脑保持唤醒](/docs/keep-awake/)：命令、系统设置和可发给 Agent 的请求。

仅有 Gemini 时，安装助手会提示：Agent J 主 Agent 暂不支持 Gemini，请再登录 Codex / Claude Code / OpenCode 之一或用 AgentsRelay。setup-agents 仍可安装 Gemini。
