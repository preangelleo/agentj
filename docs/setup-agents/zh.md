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

先在下面对应系统中装好 curl。脚本检测缺项，询问选择哪些工具、是否使用 AgentsRelay；默认备份并合并全权限配置，`--no-full-access` 可跳过；`--dry-run` 不下载、不写文件。macOS 和 Ubuntu/Debian/WSL2 支持自动安装。


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


SHA256 sh: `e34c6876fcb189321dd014960e7d8ca8a66e6eb088425a608edcd03249f9cde2`


```bash
bash setup-agents.sh --dry-run
```



```bash
bash setup-agents.sh
```


国内 npm 下载慢时加 `--mirror`；镜像仅改变包下载来源，不改变模型服务。自动脚本使用用户目录，不覆盖系统 Node/npm。


```bash
bash setup-agents.sh --mirror
```


仅配置全权限也可重跑，已有工具不会重装：


```bash
bash setup-agents.sh --cli opencode,codex --no-relay
```


## macOS

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

## Linux

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

## Windows

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


SHA256 ps1: `bab10c64042a1584178c0014586c7877d36b2859083a5c440dbb14199d277376`


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

## dependencies

上面的系统菜单解决缺少 curl / npm / Python / 编译工具。依赖安装失败时停在该步骤，修复网络或包管理器后重跑；脚本会给出本页对应锚点。

## PATH

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


## OpenCode

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

## Codex CLI

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

## Claude Code

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

## Gemini CLI

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

首次运行选择 Google 登录或自己的 Gemini API key；当前 Gemini 默认审批设置不能填 yolo，必须在启动命令里带 --yolo。Agent J 当前可接 Claude/Codex/OpenCode；Gemini 可在电脑上使用，但尚不能作为 Agent J 主 Agent。

## full-access

配置文件可能已有 key；不要把内容贴到聊天。先分别备份，再在编辑器里合并这些字段，不要整份覆盖。脚本会自动备份、合并，并设置 0600；JSONC/特殊 TOML 写法会停下让你手动合并。


```bash
mkdir -p ~/.claude
```



```bash
test ! -f ~/.claude/settings.json || cp -p ~/.claude/settings.json ~/.claude/settings.json.backup-$(date +%Y%m%d-%H%M%S)
```


用编辑器合并：


```bash
nano ~/.claude/settings.json
```



```json
{"permissions":{"defaultMode":"bypassPermissions"}}
```



```bash
chmod 600 ~/.claude/settings.json
```



```bash
mkdir -p ~/.codex
```



```bash
test ! -f ~/.codex/config.toml || cp -p ~/.codex/config.toml ~/.codex/config.toml.backup-$(date +%Y%m%d-%H%M%S)
```


用编辑器合并：


```bash
nano ~/.codex/config.toml
```



```toml
approval_policy = "never"
sandbox_mode = "danger-full-access"
```



```bash
chmod 600 ~/.codex/config.toml
```



```bash
mkdir -p ~/.config/opencode
```



```bash
test ! -f ~/.config/opencode/opencode.json || cp -p ~/.config/opencode/opencode.json ~/.config/opencode/opencode.json.backup-$(date +%Y%m%d-%H%M%S)
```


用编辑器合并：


```bash
nano ~/.config/opencode/opencode.json
```



```json
{"permission":"allow"}
```



```bash
chmod 600 ~/.config/opencode/opencode.json
```



```bash
mkdir -p ~/.gemini
```



```bash
test ! -f ~/.gemini/settings.json || cp -p ~/.gemini/settings.json ~/.gemini/settings.json.backup-$(date +%Y%m%d-%H%M%S)
```


用编辑器合并：


```bash
nano ~/.gemini/settings.json
```



```json
{"tools":{"sandbox":false}}
```



```bash
chmod 600 ~/.gemini/settings.json
```


Codex 两个字段放在文件顶部、任何 [table] 之前。Claude 必须是用户 ~/.claude/settings.json；项目配置中的 bypassPermissions 不生效。OpenCode 若用 opencode.jsonc，请合并到那一个文件，不同时创建冲突 JSON。Gemini 的 sandbox:false 关闭沙盒，YOLO 仍需每次命令行指定；可用脚本建立的 gemini-full-access。


```bash
gemini-full-access
```


未跑脚本时的 Gemini 启动替代：


```bash
gemini --yolo --sandbox=false
```


撤销：把对应 .backup 或 .agentj-backup 文件复制回原路径，然后新开会话；--no-full-access 仅跳过未来设置，不会撤销以前的设置。

## AgentsRelay

可选：由已上线的官方引导脚本配置 Claude/Codex/OpenCode。它会隐藏输入、核实 key 和模型、备份、合并，凭据保存在本机；不要在 AI 聊天里提供 key。需要不同分组的 key 时按脚本提示分别输入。Gemini 不在该脚本契约内，保留 Google 登录。


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

## requirements

macOS：先试运行 `opencode --version`（或所选 Agent 的 `--version`）；能正常运行就能继续安装，不按系统版本号拦截。一键安装会实际试运行它下载的固定版 OpenCode；若实际出现 dyld / _ubrk_clone 或无法运行，才提示失败原因。PATH 或 npm 重装不能修复系统库，请升级系统、换电脑，或用能正常运行的其他 Agent 按手动路线安装。Intel Mac 使用 x86_64 工具与 /usr/local Homebrew，Apple Silicon 使用 arm64 与 /opt/homebrew；不要混用架构。Gemini 当前官方列 macOS 15+。物理 Intel Mac/Windows 安装尚需对应机器验证。

中国大陆：Claude Code / Codex 官方服务不可用，npm 镜像只帮你下载，不能解锁服务。选择 OpenCode + 国产模型，或使用有对应套餐/模型的 AgentsRelay；需自己核对服务提供方和地区条款。Windows 只走 WSL2。

command not found：回 PATH 步骤，确认 shell 文件和新开终端。EACCES：使用本页用户目录 npm prefix，不 sudo npm。登录失败：核对账号、地区和自己的代理，别把 key 贴出来。

## Agent J

在已登录并以全权限启动的 Claude/Codex/OpenCode 中，发送现有安装指令：


```text
Please download https://agentj.app/install.md with curl, read the whole file with your file-reading tool, then follow it step by step, re-reading each section before you do it. Tell me when you need me to do something.
```


[安装流程](/docs/install/) · [install.md](/install/) · [命令参考](/docs/cli/)

[让电脑保持唤醒](/docs/keep-awake/)：命令、系统设置和可发给 Agent 的请求。
