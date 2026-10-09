#!/usr/bin/env bash
# Agent J prerequisites only. No Agent J install, account, or service changes.
set -euo pipefail
set +x
umask 077
PAGE=https://agentj.app/docs/setup-agents/
DRY=0 FULL=1 RELAY=ask SELECT='' REGISTRY=https://registry.npmjs.org
STEP=dependencies
trap 'printf "Step failed. Manual instructions: %s#%s\n" "$PAGE" "$STEP" >&2' ERR
usage() { printf '%s\n' 'Usage: bash setup-agents.sh [--cli opencode,codex,claude,gemini] [--dry-run] [--no-full-access] [--relay|--no-relay] [--mirror]'; }
while (($#)); do
  case "$1" in
    --cli) SELECT=${2:?Missing CLI list}; shift ;;
    --dry-run) DRY=1 ;;
    --no-full-access) FULL=0 ;;
    --relay) RELAY=yes ;;
    --no-relay) RELAY=no ;;
    --mirror) REGISTRY=https://registry.npmmirror.com ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done
OS=$(uname -s); ARCH=$(uname -m)
case "$OS/$ARCH" in
  Darwin/arm64|Darwin/x86_64|Linux/aarch64|Linux/arm64|Linux/x86_64) ;;
  *) printf 'Unsupported platform. See %s#requirements\n' "$PAGE" >&2; exit 2 ;;
esac
if [[ $OS == Darwin ]]; then
  MAJOR=$(sw_vers -productVersion); MAJOR=${MAJOR%%.*}
  [[ $MAJOR -ge 13 ]] || { printf 'macOS 13+ required (OpenCode dyld _ubrk_clone). See %s#requirements\n' "$PAGE" >&2; exit 2; }
fi
printf 'System: %s / %s\n' "$OS" "$ARCH"
for cli in curl git node npm python3 opencode codex claude gemini; do
  if command -v "$cli" >/dev/null 2>&1; then printf 'Found: %s\n' "$cli"; else printf 'Missing: %s\n' "$cli"; fi
done
ask() { [[ -r /dev/tty ]] || { printf 'No terminal: use --cli and --no-relay.\n' >&2; exit 2; }; printf '%s ' "$1" >/dev/tty; IFS= read -r REPLY </dev/tty; }
if [[ -z $SELECT ]]; then
  if ((DRY)); then SELECT=opencode,codex,claude,gemini; else ask 'Choose CLI names, comma separated [opencode,codex]:'; SELECT=${REPLY:-opencode,codex}; fi
fi
CLIS=(); IFS=, read -r -a CLIS <<< "$SELECT"
for cli in "${CLIS[@]}"; do case "$cli" in opencode|codex|claude|gemini) ;; *) printf 'Unknown CLI name\n' >&2; exit 2 ;; esac; done
if ((DRY)); then
  printf 'PLAN: ensure curl, git, certificates, build tools, Python 3.11+, Node.js 22+ and npm; registry %s\n' "$REGISTRY"
  [[ $OS != Darwin ]] || printf 'PLAN: Homebrew shellenv in zprofile/bash_profile (Intel /usr/local, Apple Silicon /opt/homebrew)\n'
  printf 'PLAN: install missing CLIs: %s; user-owned npm prefix; PATH before bashrc early-return\n' "$SELECT"
  printf 'PLAN: full-access=%s; back up and merge configs, 0600, refuse symlinks/JSONC; Gemini dedicated gemini-full-access launcher\n' "$FULL"
  printf 'PLAN: relay=%s; official AgentsRelay --paste --only --no-launch; Gemini unsupported\n' "$RELAY"
  exit 0
fi
# PATH for this run; system installations remain untouched.
PREFIX="$HOME/.local/share/agentj-setup"
export PATH="$PREFIX/node/bin:$PREFIX/npm/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
root_run() { if [[ $(id -u) == 0 ]]; then "$@"; else sudo "$@"; fi; }
if [[ $OS == Darwin ]]; then
  if ! command -v brew >/dev/null 2>&1; then
    BREWTMP=$(mktemp -d)
    trap 'rm -rf "$BREWTMP"' EXIT
    /usr/bin/curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$BREWTMP/install.sh"
    bash "$BREWTMP/install.sh"
    rm -rf "$BREWTMP"
  fi
  if command -v brew >/dev/null 2>&1; then BREW=$(command -v brew); elif [[ -x /opt/homebrew/bin/brew ]]; then BREW=/opt/homebrew/bin/brew; else BREW=/usr/local/bin/brew; fi
  eval "$("$BREW" shellenv)"
  brew install curl git python xz
  xcode-select -p >/dev/null 2>&1 || { xcode-select --install; printf 'Finish Command Line Tools installation and rerun.\n'; exit 1; }
elif command -v apt-get >/dev/null 2>&1; then
  root_run apt-get update
  root_run apt-get install -y --no-install-recommends curl git ca-certificates build-essential python3 xz-utils
else
  printf 'Automatic dependencies support macOS/Homebrew and Debian/Ubuntu/WSL only. See %s#dependencies\n' "$PAGE" >&2; exit 2
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' || { printf 'Install Python 3.11+; see %s#dependencies\n' "$PAGE"; exit 1; }
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1 || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)'; then
  case "$ARCH" in x86_64) NODEARCH=x64 ;; *) NODEARCH=arm64 ;; esac
  case "$OS" in Darwin) NODEOS=darwin; EXT=tar.gz ;; *) NODEOS=linux; EXT=tar.xz ;; esac
  NODEBASE=https://nodejs.org/dist/latest-v22.x
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "$NODEBASE/SHASUMS256.txt" -o "$TMP/SHASUMS256.txt"
  FILE=$(awk -v target="-$NODEOS-$NODEARCH.$EXT" 'index($2,target)>0 && index($2,target)==length($2)-length(target)+1 {print $2}' "$TMP/SHASUMS256.txt")
  [[ -n $FILE && $FILE != *$'\n'* && $FILE != */* ]] || { printf 'Node artifact selection failed. See %s#dependencies\n' "$PAGE" >&2; exit 1; }
  curl -fsSL "$NODEBASE/$FILE" -o "$TMP/$FILE"
  python3 - "$TMP" "$FILE" <<'PY'
import hashlib, pathlib, sys
p=pathlib.Path(sys.argv[1]); name=sys.argv[2]
expected=[r.split()[0] for r in (p/'SHASUMS256.txt').read_text().splitlines() if r.split()[1]==name][0]
assert hashlib.sha256((p/name).read_bytes()).hexdigest()==expected, 'Node checksum mismatch'
PY
  mkdir -p "$PREFIX/node"
  tar -xf "$TMP/$FILE" -C "$PREFIX/node" --strip-components=1
fi
mkdir -p "$PREFIX/npm"
FAILED=0
for cli in "${CLIS[@]}"; do
  STEP=$cli
  if ! command -v "$cli" >/dev/null 2>&1; then
    case "$cli" in opencode) PKG=opencode-ai ;; codex) PKG=@openai/codex ;; claude) PKG=@anthropic-ai/claude-code ;; gemini) PKG=@google/gemini-cli ;; esac
    if ! npm install --global --prefix "$PREFIX/npm" --registry "$REGISTRY" "$PKG"; then
      printf 'Installation failed. See %s#%s\n' "$PAGE" "$cli" >&2; FAILED=1; continue
    fi
  fi
  "$cli" --version || { printf 'Verification failed. See %s#%s\n' "$PAGE" "$cli"; FAILED=1; }
done
STEP=full-access
# One configuration writer, embedded so the downloaded shell script is standalone.
python3 - "$SELECT" "$FULL" "$OS" <<'PY'
import json, os, pathlib, re, sys, tempfile, tomllib, uuid
home=pathlib.Path.home(); selected=set(sys.argv[1].split(',')); full=sys.argv[2]=='1'; mac=sys.argv[3]=='Darwin'
def write(p,text,mode=0o600):
    if p.is_symlink() or any(q.is_symlink() for q in p.parents if q != home.parent):
        raise ValueError('Refusing symlink; edit the configuration target manually')
    p.parent.mkdir(parents=True,exist_ok=True)
    old=p.read_bytes() if p.exists() else None; data=text.encode()
    if old==data:
        p.chmod(mode); return
    if old is not None:
        backup=p.with_name(p.name+'.agentj-backup-'+uuid.uuid4().hex)
        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f: f.write(old)
    fd,tmp=tempfile.mkstemp(dir=p.parent,prefix='.agentj-setup-')
    try:
        with os.fdopen(fd,'wb') as f: f.write(data)
        os.chmod(tmp,mode)
        if p.is_symlink() or (p.read_bytes() if p.exists() else None)!=old: raise ValueError('Concurrent change; rerun')
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def merge(p,change):
    if p.is_symlink(): raise ValueError('Refusing configuration symlink')
    doc=json.loads(p.read_text()) if p.exists() else {}
    if not isinstance(doc,dict): raise ValueError('Expected JSON object')
    before=json.dumps(doc,sort_keys=True); change(doc)
    if before!=json.dumps(doc,sort_keys=True): write(p,json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
    elif p.exists(): p.chmod(0o600)
def block(p,label,content):
    start='# BEGIN Agent J '+label; end='# END Agent J '+label
    old=p.read_text() if p.exists() else ''
    old=re.sub(re.escape(start)+r'\n[\s\S]*?'+re.escape(end)+r'\n?', '', old)
    write(p,start+'\n'+content+'\n'+end+'\n'+old)
path='export PATH="$HOME/.local/share/agentj-setup/node/bin:$HOME/.local/share/agentj-setup/npm/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"'
for rc in ('.bashrc','.zshrc'): block(home/rc,'PATH',path)
if mac:
    brew='/opt/homebrew/bin/brew' if pathlib.Path('/opt/homebrew/bin/brew').exists() else '/usr/local/bin/brew'
    for rc in ('.zprofile','.bash_profile'): block(home/rc,'Homebrew',f'eval "$({brew} shellenv)"\n'+path)
if full:
    if 'claude' in selected:
        merge(home/'.claude/settings.json',lambda d:d.setdefault('permissions',{}).update(defaultMode='bypassPermissions'))
    if 'opencode' in selected:
        p=home/'.config/opencode/opencode.json'
        if p.with_suffix('.jsonc').exists(): raise ValueError('OpenCode JSONC exists; merge permission manually')
        merge(p,lambda d:d.update(permission='allow'))
    if 'codex' in selected:
        p=home/'.codex/config.toml'; old=p.read_text() if p.exists() else ''; parsed=tomllib.loads(old)
        # Locate the first table outside multiline strings; keep every other byte.
        lines=old.splitlines(keepends=True); quote=None; boundary=len(lines)
        for i,line in enumerate(lines):
            if not quote and line.lstrip().startswith('['): boundary=i; break
            for token in re.findall(r"\"\"\"|'''",line.split('#',1)[0]):
                if quote is None: quote=token
                elif quote==token: quote=None
        head=''.join(lines[:boundary]); tail=''.join(lines[boundary:])
        for key,value in [('approval_policy','never'),('sandbox_mode','danger-full-access')]:
            pattern=r'(?m)^\s*'+key+r'\s*=.*(?:\n|$)'
            if key in parsed and len(re.findall(pattern,head))!=1: raise ValueError('Noncanonical Codex root setting; merge manually')
            head=re.sub(pattern,'',head)
            head=key+' = "'+value+'"\n'+head
        result=head+tail; result_doc=tomllib.loads(result)
        expected=dict(parsed); expected.update(approval_policy='never',sandbox_mode='danger-full-access')
        if result_doc!=expected: raise ValueError('Codex merge would change unrelated settings; merge manually')
        write(p,result)
    if 'gemini' in selected:
        merge(home/'.gemini/settings.json',lambda d:d.setdefault('tools',{}).update(sandbox=False))
        write(home/'.local/bin/gemini-full-access','#!/bin/sh\nexec gemini --yolo --sandbox=false "$@"\n',0o700)
print('PATH and selected settings ready. Backups stay beside changed files; open a new terminal.')
PY
STEP=agentsrelay
if [[ $RELAY == ask ]]; then ask 'Use AgentsRelay for Claude/Codex/OpenCode? [y/N]'; [[ $REPLY == y || $REPLY == Y ]] && RELAY=yes || RELAY=no; fi
if [[ $RELAY == yes ]]; then
  ONLY=
  for cli in "${CLIS[@]}"; do [[ $cli == gemini ]] || ONLY="${ONLY:+$ONLY,}$cli"; done
  printf 'Gemini is not supported by the official AgentsRelay onboarding contract; it keeps Google authentication.\n'
  if [[ -n $ONLY ]]; then
    [[ -r /dev/tty ]] || { printf 'Relay key entry requires your own terminal. See %s#agentsrelay\n' "$PAGE"; exit 2; }
    DIR="$HOME/.config/agentsrelay"; mkdir -p "$DIR"; chmod 700 "$DIR"
    curl -fsSL https://agentsrelay.net/onboard/agentsrelay-setup.sh -o "$DIR/agentj-onboard.sh"
    chmod 600 "$DIR/agentj-onboard.sh"
    # The official helper alone reads hidden key input, verifies endpoints, backs up and merges credentials.
    sh "$DIR/agentj-onboard.sh" --paste --only "$ONLY" --no-launch </dev/tty
  fi
fi
printf 'Log in in your own terminal: opencode auth login / codex login / claude / gemini.\n'
printf 'Launch: opencode --auto; codex --dangerously-bypass-approvals-and-sandbox; claude --dangerously-skip-permissions; gemini-full-access\n'
printf 'Next: %s#agent-j\n' "$PAGE"
exit "$FAILED"
