---
title: Set up AI command-line tools
nav: AI tool setup
summary: Start on an empty computer: dependencies, four AI tools, PATH, sign-in and full-access settings.
order: 17
---


# Set up AI command-line tools

Choose your system, then follow the menu through dependencies and tools. Run commands in your own computer’s terminal.

Want to use OpenCode to finish installing Agent J? Once curl is available, go straight to the OpenCode section and its fastest start. You do not need Homebrew, Node or npm first. Node steps are for npm installation routes; the official Claude Code and Codex scripts also work without Node.

Full access reduces repeated approval dialogs during phone control. Risk: the AI can modify files and run commands with your user permissions; enable it only for trusted folders and tasks. Project or company policies may still require approval.

[Install Agent J](/docs/install/) · [On your computer](/docs/computer/) · [agentj command reference](/docs/cli/)

## One-command setup

First install curl in the system section below. The script detects prerequisites and asks which tools to install and whether to use AgentsRelay. It backs up and merges full-access settings by default; skip with `--no-full-access`. `--dry-run` downloads nothing and writes no files. Automatic installation supports macOS and Ubuntu/Debian/WSL2.


```bash
curl -fsSL https://agentj.app/setup-agents.sh | bash
```


To review first: download, inspect, compare SHA256, then execute. Copy each command separately.


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


Use `--mirror` for slow npm downloads in mainland China. The mirror changes package downloads, not model endpoints. The script installs to user directories and keeps system Node/npm intact.


```bash
bash setup-agents.sh --mirror
```


Rerun for full-access configuration; existing tools are not reinstalled:


```bash
bash setup-agents.sh --cli opencode,codex --no-relay
```


## macOS

Menu: check the system first; for OpenCode alone, go straight to its section below. For npm routes, continue through Homebrew → dependencies → Node → PATH. macOS includes `/usr/bin/curl`; use its full path if curl is missing.

Check what is already installed:


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


No Homebrew: its official installer may request your computer password and Command Line Tools.


```bash
/bin/bash -c "$(/usr/bin/curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```


Apple Silicon (arm64):


```bash
eval "$(/opt/homebrew/bin/brew shellenv)"
```



```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
```


Intel (x86_64):


```bash
eval "$(/usr/local/bin/brew shellenv)"
```



```bash
echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
```


Use `~/.zprofile` for default zsh, or `~/.bash_profile` for a bash login terminal. Add only your architecture’s command.


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


If Homebrew is unavailable, download the LTS installer for your architecture from [Node.js](https://nodejs.org/en/download).

## Linux

Menu: system check → install curl with apt → OpenCode below. Continue through Node → PATH only for npm routes. These commands target Ubuntu 24.04+ / Debian 12+; on other distributions use their package manager for equivalent prerequisites.


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


apt works before curl is installed. Enter sudo’s password on your computer; omit sudo when already root.


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


Missing Node/npm, or Node older than 22: install nvm, then Node 22 in your user directory.


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


For slow Node downloads, retry with this mirror:


```bash
NVM_NODEJS_ORG_MIRROR=https://npmmirror.com/mirrors/node nvm install 22
```


The setup script requires Python 3.11+. Upgrade the OS or install a distribution-supported Python 3.11+ if needed. Installing Node LTS also installs npm; do not install an old standalone npm.

## Windows

Menu: administrator PowerShell → WSL2 → Ubuntu → Linux username/password → Linux steps → four tools. Agent J on Windows uses WSL2 only. Requires Windows 10 build 19041+ or Windows 11 with BIOS virtualization enabled.

Check what is installed (administrator PowerShell):


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


If Ubuntu exists but shows VERSION=1:


```powershell
wsl --set-version Ubuntu 2
```


Restart Windows if asked, open Ubuntu and create a Linux username/password on first launch. Password input is hidden.


```powershell
wsl -d Ubuntu
```


Alternatively download and review the Windows helper:


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


If execution policy blocks the helper, use the manual wsl commands above. Run every subsequent bash command inside Ubuntu, including the Linux tab’s dependencies and Node steps. Keep projects under `~/projects`, rather than `/mnt/c`.


```bash
sudo apt-get update
```



```bash
sudo apt-get install -y curl ca-certificates
```



```bash
curl -fsSL https://agentj.app/setup-agents.sh | bash
```


See [Microsoft’s WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install).

## dependencies

The system menus above cover missing curl, npm, Python and build tools. Fix network/package-manager failures at that step, then rerun; the script links back to the relevant section.

## PATH

The npm route uses a user-owned prefix. First add it and the official installers’ directories to this terminal’s PATH.


```bash
export PATH="$HOME/.local/share/agentj-setup/npm/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
```


zsh: save and load once:


```bash
echo 'export PATH="$HOME/.local/share/agentj-setup/npm/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"' >> ~/.zshrc
```



```bash
source ~/.zshrc
```


bash: place the line before any noninteractive early-return in `~/.bashrc`. These commands prepend it, then load it.


```bash
touch ~/.bashrc
```



```bash
sed -i.bak '1i export PATH="$HOME/.local/share/agentj-setup/npm/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"' ~/.bashrc
```



```bash
source ~/.bashrc
```


macOS BSD sed uses different syntax; for mac bash, use an editor to put the same export line first in ~/.bashrc and load ~/.bashrc from ~/.bash_profile. Verify again in a new terminal.


```bash
command -v node
```



```bash
command -v npm
```


## OpenCode

Check what is already installed:


```bash
opencode --version
```



```bash
command -v opencode
```


Primary route (official script; macOS / Linux / WSL, no Node/npm required):

```bash
curl -fsSL https://opencode.ai/install | bash
```

If installation ends with `command not found`, use the commands for your shell. macOS defaults to zsh; `>>` creates `~/.zshrc` if it does not exist. Copy each command separately.

zsh (macOS default):

```bash
echo 'export PATH="$HOME/.opencode/bin:$PATH"' >> ~/.zshrc
```



```bash
source ~/.zshrc
```



```bash
opencode --version
```

bash (Linux / WSL; creates `~/.bashrc` if missing):

```bash
echo 'export PATH="$HOME/.opencode/bin:$PATH"' >> ~/.bashrc
```



```bash
source ~/.bashrc
```



```bash
opencode --version
```

Verify once more in a new terminal. Put the bash PATH line before any noninteractive early-return; for a macOS bash login terminal, also load `~/.bashrc` from `~/.bash_profile`.

Alternative npm route (install Node/npm and complete the PATH section above first):


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" opencode-ai
```


Alternative npm download mirror for mainland China:


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" opencode-ai --registry=https://registry.npmmirror.com
```


Check PATH, verify the installation, then sign in yourself. Enter keys only in your own terminal.


```bash
command -v opencode
```



```bash
opencode --version
```



```bash
opencode auth login
```


### Fastest start (recommended for new users)

1. Install OpenCode with the official script above; if the command is missing, use the PATH repair immediately below it.
2. Run `opencode` in your own terminal.
3. In OpenCode, enter `/connect`, search for DeepSeek and select it, then paste your own API key into the key prompt. Enter keys only in your own terminal; do not share them in chat, seat cards or screenshots. Alternatively, run `opencode auth login` in the terminal and select DeepSeek to sign in first.
4. Enter `/models` in OpenCode and select an available DeepSeek model.
5. Paste the installation prompt from your seat card into the OpenCode conversation and let it continue installing Agent J. Follow any steps it asks you to perform yourself.

[DeepSeek connection guide](https://opencode.ai/docs/providers/#deepseek)

Full-access launch:


```bash
opencode --auto
```


[OpenCode official guide](https://opencode.ai/docs/cli/)

Current --auto approves actions that are not explicitly denied. Upgrade if an older version reports unknown option. The permanent permission=allow configuration is below.

## Codex CLI

Check what is already installed:


```bash
codex --version
```



```bash
command -v codex
```


Primary route (official script; macOS / Linux / WSL, no Node/npm required):

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

If installation ends with `command not found`, use the commands for your shell. macOS defaults to zsh; `>>` creates `~/.zshrc` if it does not exist. Copy each command separately.

zsh (macOS default):

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```



```bash
source ~/.zshrc
```



```bash
codex --version
```

bash (Linux / WSL; creates `~/.bashrc` if missing):

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```



```bash
source ~/.bashrc
```



```bash
codex --version
```

Verify once more in a new terminal. Put the bash PATH line before any noninteractive early-return; for a macOS bash login terminal, also load `~/.bashrc` from `~/.bash_profile`.

Alternative npm route (install Node/npm and complete the PATH section above first):


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @openai/codex
```


Alternative route (brew commands are macOS only):


```bash
brew install --cask codex
```


Alternative npm download mirror for mainland China:


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @openai/codex --registry=https://registry.npmmirror.com
```


Check PATH, verify the installation, then sign in yourself. Enter keys only in your own terminal.


```bash
command -v codex
```



```bash
codex --version
```



```bash
codex login
```


Full-access launch:


```bash
codex --dangerously-bypass-approvals-and-sandbox
```


[Codex CLI official guide](https://developers.openai.com/codex/cli/reference/)

## Claude Code

Check what is already installed:


```bash
claude --version
```



```bash
command -v claude
```


Primary route (official script; macOS / Linux / WSL, no Node/npm required):

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

If installation ends with `command not found`, use the commands for your shell. macOS defaults to zsh; `>>` creates `~/.zshrc` if it does not exist. Copy each command separately.

zsh (macOS default):

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```



```bash
source ~/.zshrc
```



```bash
claude --version
```

bash (Linux / WSL; creates `~/.bashrc` if missing):

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```



```bash
source ~/.bashrc
```



```bash
claude --version
```

Verify once more in a new terminal. Put the bash PATH line before any noninteractive early-return; for a macOS bash login terminal, also load `~/.bashrc` from `~/.bash_profile`.

Alternative npm route (install Node/npm and complete the PATH section above first):


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @anthropic-ai/claude-code
```


Alternative npm download mirror for mainland China:


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @anthropic-ai/claude-code --registry=https://registry.npmmirror.com
```


Check PATH, verify the installation, then sign in yourself. Enter keys only in your own terminal.


```bash
command -v claude
```



```bash
claude --version
```



```bash
claude auth login
```


Full-access launch:


```bash
claude --dangerously-skip-permissions
```


[Claude Code official guide](https://code.claude.com/docs/en/setup)

## Gemini CLI

Check what is already installed:


```bash
gemini --version
```



```bash
command -v gemini
```


Primary route (npm; complete Node and PATH first):


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @google/gemini-cli
```


Alternative route (brew commands are macOS only):


```bash
brew install gemini-cli
```


Alternative npm download mirror for mainland China:


```bash
npm install --global --prefix "$HOME/.local/share/agentj-setup/npm" @google/gemini-cli --registry=https://registry.npmmirror.com
```


Check PATH, verify the installation, then sign in yourself. Enter keys only in your own terminal.


```bash
command -v gemini
```



```bash
gemini --version
```



```bash
gemini
```


Full-access launch:


```bash
gemini --yolo --sandbox=false
```


[Gemini CLI official guide](https://geminicli.com/docs/get-started/installation/)

On first launch, choose Google sign-in or your own Gemini API key. The defaultApprovalMode setting does not accept yolo; keep --yolo in the launch command. Agent J currently connects to Claude/Codex/OpenCode; Gemini works on the computer but is not yet an Agent J host adapter.

## full-access

Configuration files may contain keys: never paste their contents into chat. Back up each file, then merge these fields in an editor instead of overwriting the file. The script handles backups, merges and 0600 permissions; JSONC or unusual TOML syntax requires a manual merge.


```bash
mkdir -p ~/.claude
```



```bash
test ! -f ~/.claude/settings.json || cp -p ~/.claude/settings.json ~/.claude/settings.json.backup-$(date +%Y%m%d-%H%M%S)
```


Merge in your editor:


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


Merge in your editor:


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


Merge in your editor:


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


Merge in your editor:


```bash
nano ~/.gemini/settings.json
```



```json
{"tools":{"sandbox":false}}
```



```bash
chmod 600 ~/.gemini/settings.json
```


Put Codex’s two fields at the top, before any [table]. Set Claude’s mode in user ~/.claude/settings.json; bypassPermissions in project settings does not take effect. If OpenCode uses opencode.jsonc, merge there instead of creating conflicting JSON. Gemini’s sandbox:false disables sandboxing, but YOLO remains a launch flag; use the script’s gemini-full-access helper.


```bash
gemini-full-access
```


Gemini launch without the setup script:


```bash
gemini --yolo --sandbox=false
```


To undo, copy the matching .backup or .agentj-backup file over its original path and start a fresh session. --no-full-access skips future changes; it does not undo earlier settings.

## AgentsRelay

Optional: the existing official helper configures Claude/Codex/OpenCode. It reads hidden input, verifies keys/models, backs up and merges local credentials. Never provide keys in AI chat. Enter separate group keys when prompted. Gemini is outside this helper’s contract and keeps Google sign-in.


```bash
curl -fsSL https://agentsrelay.net/onboard/agentsrelay-setup.sh -o agentsrelay-setup.sh
```



```bash
sh agentsrelay-setup.sh --paste --only claude,codex,opencode --no-launch
```


Open a new terminal afterward to load the environment. Official rollback:


```bash
python3 ~/.config/agentsrelay/agentsrelay-setup.py --restore
```


[AgentsRelay official onboarding](https://agentsrelay.net/onboard/)

## requirements

macOS: first run `opencode --version` (or your selected agent’s `--version`). If it works, continue installing; there is no operating-system version gate. The one-line installer probes its downloaded, pinned OpenCode binary and reports actual dyld / _ubrk_clone or execution failures. PATH changes or npm reinstalls cannot repair system libraries; upgrade macOS, use another computer, or choose another working agent with the manual installation guide. Intel Mac uses x86_64 tools and /usr/local Homebrew; Apple Silicon uses arm64 and /opt/homebrew. Gemini currently lists macOS 15+. Physical Intel Mac and Windows installation still requires verification on those machines.

Mainland China: official Claude Code / Codex services are unavailable; npm mirrors help downloads, not service access. Use OpenCode with a domestic model, or an appropriate AgentsRelay plan/model, checking the provider’s terms and regional requirements. Windows uses WSL2.

command not found: revisit PATH, check the shell startup file and open a fresh terminal. EACCES: use the user-owned npm prefix above, rather than sudo npm. Sign-in failure: check account, region and your proxy without sharing keys.

## Agent J

In signed-in Claude/Codex/OpenCode running with full access, send the existing installation instruction:


```text
Please download https://agentj.app/install.md with curl, read the whole file with your file-reading tool, then follow it step by step, re-reading each section before you do it. Tell me when you need me to do something.
```


[Installation steps](/docs/install/) · [install.md](/install/) · [Command reference](/docs/cli/)

[Keep your computer awake](/docs/keep-awake/): commands, system settings and a request to send your Agent.
