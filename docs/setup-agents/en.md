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

First install curl below. The setup asks which tools to install, checks official service connectivity with short requests that send no user data, and recommends a route. Reachable: native login. Unreachable: AgentsRelay, which provides an alternative service endpoint. You can change the recommendation with `--relay` / `--no-relay`. Connectivity does not prove regional eligibility or account access. Full-access settings are backed up and merged; `--no-full-access` keeps approval defaults. `--dry-run` downloads nothing and writes no files. Automatic setup supports macOS and Ubuntu/Debian/WSL2.


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


SHA256 sh: `971a371c49a9fe07364fcbcf072849d3973cbc7f5cc3554223be517c196994d4`


```bash
bash setup-agents.sh --dry-run
```



```bash
bash setup-agents.sh
```


Flow: choose tools → choose native login or AgentsRelay → on Relay, register and paste keys with hidden input, pass a real model request → install dependencies/CLIs → verify native binaries and offline help → add aliases → optionally run the official Agent J installation assistant → account linking and phone pairing. The official assistant verifies existing CLI sign-in with a real model call and reuses it for immediate chat after pairing. It asks for a new formal provider key only when no supported CLI can call.

npm failure or timeout automatically retries `registry.npmmirror.com`, including platform optional dependencies. No `--mirror` is needed; it selects the mirror first. Mirror root and platform-package SHA512 integrity must equal the official npmjs metadata, and downloaded tarball bytes must match. If official metadata is unavailable or hashes disagree, setup stops rather than trusting an unverified mirror. Node downloads also fall back and always verify SHA256: official SHASUMS when reachable, otherwise mirror SHASUMS (this trusts mirror HTTPS for the checksum). Mirrors change downloads, not model service access. CLI binaries are never downloaded from GitHub Releases by this script; neither Claude Code nor Codex is hosted on our site.

Each installed CLI must pass `--version` and offline `--help`. Native tools additionally check platform packages, CPU architecture and glibc/musl selection, execute their binaries and bundled ripgrep where provided. Missing components produce “安装不完整：缺 …” and a repair command, never success. Gemini also checks and loads its platform-native Node terminal addon. macOS has no version-number rejection; actual runtime errors explain when system libraries need an upgrade.

Successful tools get idempotent `cx` / `cc` / `oc` / `gx` aliases in bash/zsh; the Windows helper adds PowerShell functions for the verified WSL tools. Open a new terminal. `--no-aliases` disables this (`-NoAliases` in PowerShell); `--no-full-access` creates plain aliases. `cc` shadows the interactive C compiler command; use `command cc` or rename the alias to `ccx`.


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


SHA256 ps1: `0ef880556453c6a9853fc6f3e71228debec980f072b76688281c3cff58b12ab1`


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

## Dependencies {#dependencies}

The system menus above cover missing curl, npm, Python and build tools. Fix network/package-manager failures at that step, then rerun; the script links back to the relevant section.

## Command path {#path}

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

Google has discontinued personal Gemini CLI login. Use a paid Gemini API key, entered only in your own terminal. Gemini is optional: select it explicitly; setup does not recommend it by default. The defaultApprovalMode setting does not accept yolo; keep --yolo in the launch command. Agent J currently connects to Claude/Codex/OpenCode; Gemini works on the computer but is not yet an Agent J host adapter.

## Full-access launch (three ways) {#full-access}

Full access lets the Agent run commands and modify files without asking. Use it only on your own computer and in trusted directories. Managed policies and explicit deny rules can still apply.

### 1. Command flags

Run the line for your chosen tool in your terminal:

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

OpenCode `--auto` approves requests that would ask, while preserving explicit deny rules.

### 2. Configuration files

Paths and fields follow the tool order below. **Back up first, then merge in an editor; do not replace the whole file with an example or duplicate existing keys.** Files may contain credentials; never paste them into chat.

| Tool | macOS / Linux | Windows PowerShell |
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

Codex: put both keys at the root, before any `[table]`. Claude Code: merge into `permissions` in user settings; project settings cannot enable `bypassPermissions`. OpenCode: edit existing `opencode.jsonc` if present; setting `permission: "allow"` replaces granular permissions, so choose it only when you intend to allow everything. With `XDG_CONFIG_HOME`, use `opencode/opencode.json` under that directory. Gemini: `tools.sandbox: false` only disables the sandbox; `general.defaultApprovalMode` **does not accept `yolo`**. Configuration alone cannot enable full auto-approval; use the command flag or `gx` below.

#### macOS configuration {#macos-config}

Back up, then manually merge the fields above in the editor. Copy only your tool’s command. In WSL2 use the Linux tab. An existing OpenCode jsonc file is preferred.

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

#### Linux configuration {#linux-config}

Back up, then manually merge the fields above in the editor. Copy only your tool’s command. In WSL2 use the Linux tab. An existing OpenCode jsonc file is preferred.

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

#### Windows configuration {#windows-config}

Back up, then manually merge the fields above in the editor. Copy only your tool’s command. In WSL2 use the Linux tab. An existing OpenCode jsonc file is preferred.

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

Rollback: restore the matching `.backup-*` / `.agentj-backup` file. For a new file remove only the fields you added. Without a backup, use Codex `approval_policy = "on-request"` / `sandbox_mode = "workspace-write"`, Claude `defaultMode: "default"`, OpenCode `permission: "ask"`, and remove Gemini `tools.sandbox` and launch flags. Exit the Agent and start a fresh session.

Checked 2026-10-09 against local Codex 0.159.2, Claude Code 2.1.289, OpenCode 1.18.32 and Gemini 0.60.0 `--help` and official documentation. Managed policy and project configuration may override user settings.

[Codex configuration](https://developers.openai.com/codex/config-reference/) · [Claude Code settings](https://code.claude.com/docs/en/settings) · [OpenCode permissions](https://opencode.ai/docs/permissions/) · [Gemini configuration](https://geminicli.com/docs/reference/configuration/)

### 3. Short aliases

Add `gx` only if you explicitly choose Gemini and have a paid Gemini API key.

Repeating the command does not append the same line twice. Check existing aliases/functions with these names before replacing them; these definitions take over matching interactive commands. Add arguments after `cx` / `cc` / `oc` / `gx` as usual. `cc` is the system C compiler name on macOS/Linux: the alias affects interactive terminals, not ordinary build scripts. Use `ccx` instead by changing `cc` in the command if you prefer.

Rollback: delete the four added lines from the rc file or `$PROFILE` and open a new terminal. In the current Bash/zsh shell run `unalias cx cc oc gx`; in PowerShell run `Remove-Item Function:cx,Function:cc,Function:oc,Function:gx`.

#### macOS aliases {#macos-aliases}

zsh: append to `~/.zshrc`. The final `source` applies it now, or open a new terminal.

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

#### Linux aliases {#linux-aliases}

bash: append to `~/.bashrc`; zsh users use the macOS tab. The final `source` applies it now, or open a new terminal.

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

#### Windows aliases {#windows-aliases}

PowerShell: append functions to the current `$PROFILE`, then load it with the final line. Or open a new terminal of the same PowerShell edition. If policy blocks profile loading, use the direct launch commands; do not change company policy.

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

## AgentsRelay

Before installing any CLI, visit https://agentsrelay.net, register and buy a plan for your tools. The official helper reads group keys with hidden input and performs real model calls. A failed key can be pasted again, up to three attempts, or you can switch to native login. We issue no temporary allowance token. The helper owns model defaults; the setup script hardcodes no model name. Claude and OpenAI group keys differ: provide the groups needed by your selected tools. Gemini requires a paid Gemini API key; personal Google login has been discontinued. Without a terminal, Relay stops before installing tools and asks you to rerun interactively. Never send keys in AI chat.


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

## System requirements {#requirements}

macOS: first run `opencode --version` (or your selected agent’s `--version`). If it works, continue installing; there is no operating-system version gate. The one-line installer probes its downloaded, pinned OpenCode binary and reports actual dyld / _ubrk_clone or execution failures. PATH changes or npm reinstalls cannot repair system libraries; upgrade macOS, use another computer, or choose another working agent with the manual installation guide. Intel Mac uses x86_64 tools and /usr/local Homebrew; Apple Silicon uses arm64 and /opt/homebrew. Gemini currently lists macOS 15+. Physical Intel Mac and Windows installation still requires verification on those machines.

Mainland China: official Claude Code / Codex services are unavailable; npm mirrors help downloads, not service access. Use OpenCode with a domestic model, or an appropriate AgentsRelay plan/model, checking the provider’s terms and regional requirements. Windows uses WSL2.

command not found: revisit PATH, check the shell startup file and open a fresh terminal. EACCES: use the user-owned npm prefix above, rather than sudo npm. Sign-in failure: check account, region and your proxy without sharing keys.

## Agent J

At the end, setup asks “现在安装 Agent J 吗？[Y/n]”. Yes opens the existing official installation assistant; it needs your account-page installation code (`AJI-…`), entered locally with hidden input. `--with-agentj` selects this step; `--no-agentj` skips it. These flags choose the step; pairing and account consent still require you. Without a terminal, setup only prints the next step. If no login/key is detected, it explains: “Agent J 能装能配对，但要登录 AI 工具或填 key 后才能真正聊天”. Credential presence is not proof that a token is still valid.

The formal assistant verifies a tool-free model call with installed Claude Code, Codex and OpenCode. It reuses a callable CLI; if several work, you choose. After phone pairing, send Hello. It asks for a formal service URL/model and a secure phone key card only if none can call. AgentsRelay configuration works in the background service without opening a new terminal. Gemini is installed only when explicitly selected and cannot be selected as the main Agent in Agent J. If only Gemini is installed, the assistant asks you to sign in to Codex / Claude Code / OpenCode or use AgentsRelay.



```text
Please download https://agentj.app/install.md with curl, read the whole file with your file-reading tool, then follow it step by step, re-reading each section before you do it. Tell me when you need me to do something.
```


[Installation steps](/docs/install/) · [install.md](/install/) · [Command reference](/docs/cli/)

[Keep your computer awake](/docs/keep-awake/): commands, system settings and a request to send your Agent.
