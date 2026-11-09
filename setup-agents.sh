#!/usr/bin/env bash
# AI CLI setup with verified npm fallback and optional official Agent J handoff.
set -euo pipefail
set +x
umask 077
PAGE=https://agentj.app/docs/setup-agents/
DRY=0 FULL=1 ALIASES=1 AGENTJ=ask RELAY=ask GEMINI=0 SELECT='' REGISTRY=${npm_config_registry:-${NPM_CONFIG_REGISTRY:-https://registry.npmjs.org}}
STEP=dependencies
trap 'printf "Step failed. Manual instructions: %s#%s\n" "$PAGE" "$STEP" >&2' ERR
usage() { printf '%s\n' 'Usage: bash setup-agents.sh [--cli opencode,codex,claude,gemini] [--dry-run] [--no-full-access] [--relay|--no-relay|--gemini] [--mirror] [--no-aliases] [--with-agentj|--no-agentj]'; }
while (($#)); do
  case "$1" in
    --cli) SELECT=${2:?Missing CLI list}; shift ;;
    --dry-run) DRY=1 ;;
    --no-full-access) FULL=0 ;;
    --relay) RELAY=yes ;;
    --no-relay) RELAY=no ;;
    --gemini) GEMINI=1 ;;
    --no-aliases) ALIASES=0 ;;
    --with-agentj) AGENTJ=yes ;;
    --no-agentj) AGENTJ=no ;;
    --mirror) REGISTRY=https://registry.npmmirror.com ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done
OS=$(uname -s); ARCH=$(uname -m)
# Rosetta shells report x86_64 on Apple Silicon; install the hardware-native tools.
if [[ $OS == Darwin ]] && [[ $(sysctl -n hw.optional.arm64 2>/dev/null || true) == 1 ]]; then ARCH=arm64; fi
case "$ARCH" in x86_64) NODEARCH=x64 ;; *) NODEARCH=arm64 ;; esac
case "$OS/$ARCH" in
  Darwin/arm64|Darwin/x86_64|Linux/aarch64|Linux/arm64|Linux/x86_64) ;;
  *) printf 'Unsupported platform. See %s#requirements\n' "$PAGE" >&2; exit 2 ;;
esac
printf 'System: %s / %s\n' "$OS" "$ARCH"
for cli in curl git node npm python3 opencode codex claude gemini; do
  if command -v "$cli" >/dev/null 2>&1; then printf 'Found: %s\n' "$cli"; else printf 'Missing: %s\n' "$cli"; fi
done
has_tty() { ( : </dev/tty ) 2>/dev/null; }
ask() { has_tty || { printf 'No terminal: use --cli and --no-relay.\n' >&2; exit 2; }; printf '%s ' "$1" >/dev/tty; IFS= read -r REPLY </dev/tty; }
# P235: --gemini = OpenCode with the owner's own paid Google key (Vertex AI on Google Cloud: works in Hong Kong, where the
# AI Studio Gemini API and Agents Relay's official upstreams refuse). No relay; the key is checked by a real call first.
if ((GEMINI)); then
  [[ $RELAY != yes ]] || { printf -- '--gemini and --relay are two different routes: choose one.\n' >&2; exit 2; }
  RELAY=no; [[ -n $SELECT ]] || SELECT=opencode
  [[ ,$SELECT, == *,opencode,* ]] || { printf -- '--gemini runs on OpenCode: include opencode in --cli.\n' >&2; exit 2; }
fi
if [[ -z $SELECT ]]; then
  if has_tty && ! ((DRY)); then ask 'Choose CLI names, comma separated [opencode,codex]:'; SELECT=${REPLY:-opencode,codex}; else SELECT=opencode,codex; fi
fi
CLIS=(); IFS=, read -r -a CLIS <<< "$SELECT"
for cli in "${CLIS[@]}"; do case "$cli" in opencode|codex|claude|gemini) ;; *) printf 'Unknown CLI name\n' >&2; exit 2 ;; esac; done
if ((FULL)); then
  # P191 B24: full access stays the documented default (phone remote control needs tools that keep working without prompts),
  # but it is never silent.
  printf 'WARNING: full access is ON by default: selected tools run without approval prompts (Claude bypassPermissions, Codex danger-full-access, OpenCode allow, Gemini no sandbox) and can change files and run commands as you. Configs are backed up. Use --no-full-access to keep approval prompts. See %s#full-access\n' "$PAGE" >&2
fi
if ((DRY)); then
  printf 'PLAN: ensure curl, git, certificates, build tools, Python 3.11+, Node.js 22+ and npm; registry %s\n' "$REGISTRY"
  [[ $OS != Darwin ]] || printf 'PLAN: Homebrew shellenv in zprofile/bash_profile (Intel /usr/local, Apple Silicon /opt/homebrew)\n'
  [[ $OS != Linux ]] || printf 'PLAN: Agent isolation: bubblewrap + socat (apt-get when available, else the exact command)\n'
  printf 'PLAN: install missing CLIs: %s; user-owned npm prefix; PATH before bashrc early-return\n' "$SELECT"
  printf 'PLAN: full-access=%s; back up and merge configs, 0600, refuse symlinks/JSONC; Gemini launcher only when explicitly selected; personal login discontinued, paid Gemini API key required\n' "$FULL"
  printf 'PLAN: relay=%s; probe official services; official AgentsRelay --paste --only --no-launch verifies real model BEFORE CLI install, 3 attempts; Gemini unsupported\n' "$RELAY"
  ((GEMINI == 0)) || printf 'PLAN: gemini: hidden key prompt; real Vertex AI call (global endpoint, gemini-3.8-flash; AI Studio key accepted where Google serves it) BEFORE CLI install, 3 attempts; key only in OpenCode auth.json (0600); provider gemini + default model in opencode.json\n'
  printf 'PLAN: npm/Node timeout -> automatic npmmirror fallback; official/mirror SHA512 match, Node SHA256 from official nodejs.org required (mirror sums never trusted); platform binaries + help + ripgrep self-check\n'
  printf 'PLAN: aliases=%s (selected tools only; gx requires explicit gemini selection); agentj=%s; no tty -> next-step instructions, no prompt\n' "$ALIASES" "$AGENTJ"
  exit 0
fi
# PATH for this run; system installations remain untouched.
PREFIX="$HOME/.local/share/agentj-setup"
export PATH="$PREFIX/node/bin:$PREFIX/npm/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
root_run() { if [[ $(id -u) == 0 ]]; then "$@"; else sudo "$@"; fi; }
if command -v curl >/dev/null && command -v git >/dev/null && command -v python3 >/dev/null && command -v tar >/dev/null && command -v xz >/dev/null && command -v file >/dev/null; then
  : # Existing prerequisites work: no package manager / macOS version gate.
elif [[ $OS == Darwin ]]; then
  if ! command -v brew >/dev/null 2>&1; then
    BREWTMP=$(mktemp -d)
    trap 'rm -rf "$BREWTMP"' EXIT
    /usr/bin/curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$BREWTMP/install.sh"
    bash "$BREWTMP/install.sh"
    rm -rf "$BREWTMP"
  fi
  if command -v brew >/dev/null 2>&1; then BREW=$(command -v brew); elif [[ -x /opt/homebrew/bin/brew ]]; then BREW=/opt/homebrew/bin/brew; else BREW=/usr/local/bin/brew; fi
  eval "$("$BREW" shellenv)"
  brew install curl git python xz file
  xcode-select -p >/dev/null 2>&1 || { xcode-select --install; printf 'Finish Command Line Tools installation and rerun.\n'; exit 1; }
elif command -v apt-get >/dev/null 2>&1; then
  root_run apt-get update
  root_run apt-get install -y --no-install-recommends curl git ca-certificates build-essential python3 xz-utils file
else
  printf 'Automatic dependencies support macOS/Homebrew and Debian/Ubuntu/WSL only. See %s#dependencies\n' "$PAGE" >&2; exit 2
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' || { printf 'Install Python 3.11+; see %s#dependencies\n' "$PAGE"; exit 1; }
STEP=isolation
# P219: Agent J runs the phone's Agent inside bubblewrap; without it (stock WSL2 Ubuntu) every start waits for an owner passkey
# on the phone. socat is what Claude Code's own sandbox needs next to it. Installed here, never silently skipped.
if [[ $OS == Linux ]] && { ! command -v bwrap >/dev/null 2>&1 || ! command -v socat >/dev/null 2>&1; }; then
  if command -v apt-get >/dev/null 2>&1 && { [[ $(id -u) == 0 ]] || sudo -n true 2>/dev/null || has_tty; }; then
    printf 'Agent isolation: installing bubblewrap and socat.\n'
    root_run apt-get install -y --no-install-recommends bubblewrap socat || printf 'Could not install them. Run: sudo apt-get install -y bubblewrap socat\n'
  else
    printf 'Agent isolation needs bubblewrap and socat. Run: sudo apt-get install -y bubblewrap socat (Fedora: sudo dnf install -y bubblewrap socat; Arch: sudo pacman -S bubblewrap socat). See %s#isolation\n' "$PAGE"
  fi
fi
STEP=gemini
if ((GEMINI)); then
  has_tty || { printf 'No terminal: the Google key is entered only in your own terminal. Rerun --gemini there.\n'; exit 2; }
  printf 'Google Gemini: paste your Google Cloud API key (Vertex AI, bound to a service account). Steps and prices: %s#gemini\n' "$PAGE"
  printf 'Google Gemini：粘贴你在 Google Cloud 建的 API key（绑定服务账号的那种），只在这里粘贴，不显示。步骤和费用见上面的网址。\n'
  python3 - <<'GEMINI_SETUP' || { printf 'Gemini key not verified; nothing installed. 怎么办：按 %s#gemini 检查 key 后重跑。\n' "$PAGE"; exit 1; }
import getpass, json, os, pathlib, re, subprocess, sys, tempfile
# The key: read hidden from the terminal, sent only to Google (curl reads its header from stdin, never argv), stored only
# in OpenCode's own credential file. Google's answer is classified into one line; its body is never printed.
VERTEX = 'https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-3.8-flash:generateContent'
STUDIO = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent'
BODY = json.dumps({'contents': [{'role': 'user', 'parts': [{'text': 'Reply with one word: ok'}]}]})
def call(url, key):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'out'); body = os.path.join(d, 'body')
        pathlib.Path(body).write_text(BODY)
        try:
            r = subprocess.run(['curl', '--disable', '--proto', '=https', '--max-filesize', '1048576', '-sS', '--connect-timeout', '10', '--max-time', '90', '-o', out,
                                '-w', '%{http_code}', '-H', 'content-type: application/json', '--data-binary', '@' + body,
                                '--config', '-', url], input='header = "x-goog-api-key: ' + key + '"\n',
                               capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return 0, {}
        try: doc = json.loads(pathlib.Path(out).read_text())
        except (OSError, ValueError): doc = {}
        code = int(r.stdout.strip() or 0) if r.stdout.strip().isdigit() else 0
        return code, doc if isinstance(doc, dict) else {}
def text(doc):
    try: return ''.join(p.get('text', '') for p in doc['candidates'][0]['content']['parts'] if isinstance(p, dict) and not p.get('thought'))
    except (KeyError, IndexError, TypeError): return ''
def why(doc):
    e = doc.get('error') if isinstance(doc.get('error'), dict) else {}
    reasons = ' '.join(str(d.get('reason', '')) for d in e.get('details', []) if isinstance(d, dict))
    return str(e.get('status', '')), reasons, str(e.get('message', ''))
HELP = {
 'network': '连不上 Google（大陆的网络到不了 Google；香港可以）。 / Google is unreachable from this network.',
 'invalid': 'key 不对：重新复制完整的 key（通常以 AIza 开头）。 / The key is not valid: copy the whole key again.',
 'unbound': '这把 key 没有绑定服务账号：在 Google Cloud 建 key 时勾选「通过服务账号验证 API 调用」。 / Create the key with "Authenticate API calls through a service account".',
 'studio_region': '这是 AI Studio 的 Gemini API key，Google 不对香港 / 大陆开放它：改用 Google Cloud（Vertex AI）的 key。 / An AI Studio key is not served in your region: use a Google Cloud (Vertex AI) key.',
 'disabled': '项目里还没启用 Vertex AI API（或没开结算）：在 Google Cloud 控制台启用后等 1–2 分钟再试。 / Enable the Vertex AI API and billing in the project.',
 'role': '服务账号没有调用 Gemini 的权限：给它加「Vertex AI 用户」角色，等 1–2 分钟再试。 / Give the service account the Vertex AI User role, wait a minute.',
 'billing': '结算没开通或余额不足：在 Google Cloud 开通结算账户。 / Billing is not active.',
 'quota': 'key 有效，但额度用完或请求太快：等一会儿，或在 Google Cloud 提高配额。 / Valid key, but quota is exhausted.',
 'empty': 'Gemini 回了空回复：稍后重试。 / Gemini returned an empty reply; try again.',
 'other': 'Google 拒绝了这次调用：按帮助页逐项检查。 / Google refused the call; check the help page.',
}
def classify(code, doc):
    status, reasons, msg = why(doc)
    if code == 0: return 'network'
    if code == 200: return 'ok' if text(doc).strip() else 'empty'
    if 'API_KEY_INVALID' in reasons or 'API key not valid' in msg: return 'invalid'
    if code == 401 and 'API keys are not supported' in msg: return 'unbound'
    if 'User location is not supported' in msg: return 'studio_region'
    if 'SERVICE_DISABLED' in reasons or 'has not been used in project' in msg: return 'disabled'
    if 'BILLING' in reasons.upper() or 'billing' in msg.lower(): return 'billing'
    if code == 429 or status == 'RESOURCE_EXHAUSTED': return 'quota'
    if code == 403 and ('aiplatform.endpoints.predict' in msg or status == 'PERMISSION_DENIED'): return 'role'
    return 'other'
kind = None
for attempt in range(3):
    try: raw = getpass.getpass('Google Cloud API key (hidden): ')
    except (EOFError, KeyboardInterrupt): sys.exit(1)
    m = re.fullmatch(r'[A-Za-z0-9._-]{20,}', (raw or '').strip()); key = m.group(0) if m else ''; raw = ''
    if not key:
        print(HELP['invalid']); continue
    print('正在用这把 key 真实调用一次 Gemini 3.8 Flash… / Making one real Gemini 3.8 Flash call…')
    verdict = classify(*call(VERTEX, key))
    if verdict == 'ok': kind = 'vertex'; break
    if verdict == 'unbound':   # maybe an AI Studio key: usable only where Google serves that API
        studio = classify(*call(STUDIO, key))
        if studio == 'ok': kind = 'studio'; break
        verdict = studio if studio in ('studio_region', 'quota', 'billing') else verdict
    print('✗ ' + HELP[verdict])
    key = ''
if not kind: sys.exit(1)
home = pathlib.Path.home()
def safe_path(p):
    if any(part.is_symlink() for part in (p, *p.parents)):
        raise SystemExit('Refusing symlinked OpenCode file')
def secure_write(p, data):
    safe_path(p)
    safe_path(p.with_name(p.name + '.agentj-bak'))
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists(): (p.with_name(p.name + '.agentj-bak')).write_bytes(p.read_bytes()); os.chmod(p.with_name(p.name + '.agentj-bak'), 0o600)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix='.' + p.name + '.')
    with os.fdopen(fd, 'w') as f: f.write(data)
    os.chmod(tmp, 0o600); os.replace(tmp, p)
def load(p):
    safe_path(p)
    safe_path(p.with_name(p.name + '.agentj-bak'))
    if not p.exists(): return {}
    doc = json.loads(p.read_text() or '{}')
    if not isinstance(doc, dict): raise SystemExit('Expected a JSON object in ' + str(p))
    return doc
data = pathlib.Path(os.environ.get('XDG_DATA_HOME') or home / '.local/share') / 'opencode/auth.json'
conf = pathlib.Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config') / 'opencode/opencode.json'
if conf.with_suffix('.jsonc').exists(): raise SystemExit('OpenCode JSONC exists; add the gemini provider manually (see the help page)')
cfg = load(conf)
auth = load(data); auth['gemini'] = {'type': 'api', 'key': key}; key = ''
secure_write(data, json.dumps(auth, indent=2) + '\n'); auth = None
cfg.setdefault('$schema', 'https://opencode.ai/config.json')
prov = cfg.get('provider') if isinstance(cfg.get('provider'), dict) else {}
prov['gemini'] = {'npm': '@ai-sdk/google-vertex' if kind == 'vertex' else '@ai-sdk/google',
                  'name': 'Google Gemini' + (' (Vertex AI)' if kind == 'vertex' else ' (AI Studio)'),
                  'models': {'gemini-3.8-flash': {'name': 'Gemini 3.8 Flash'}, 'gemini-3.1-pro-preview': {'name': 'Gemini 3.1 Pro (preview)'}}}
cfg['provider'] = prov; cfg['model'] = 'gemini/gemini-3.8-flash'
secure_write(conf, json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
print('✓ Gemini 真实回复了。key 只存在 OpenCode 自己的凭据文件里（0600），模型 gemini/gemini-3.8-flash。 / Gemini answered; key stored only in OpenCode auth.json.')
GEMINI_SETUP
fi
STEP=agentsrelay
RELAY_OK=0
if [[ $RELAY == ask ]]; then
  REACHABLE=1
  for endpoint in https://chatgpt.com https://api.openai.com https://api.anthropic.com; do
    # HTTP errors still establish connectivity; never submit user data.
    curl --proto '=https' --connect-timeout 3 --max-time 5 -sS -I "$endpoint" -o /dev/null 2>/dev/null || REACHABLE=0
  done
  if ((REACHABLE)); then DEFAULT=native; printf 'Official services reachable: native login recommended.\n'
  else DEFAULT=relay; printf 'Official services unreachable: AgentsRelay recommended for this network.\n'; fi
  if has_tty; then
    ask "Route: native / relay [$DEFAULT]:"; ROUTE=${REPLY:-$DEFAULT}
    case "$ROUTE" in native) RELAY=no ;; relay) RELAY=yes ;; *) printf 'Choose native or relay.\n'; exit 2 ;; esac
  elif [[ $DEFAULT == relay ]]; then
    printf 'No terminal: open a terminal to paste your AgentsRelay key, or explicitly choose --no-relay. No CLI installed.\n'; exit 2
  else RELAY=no; fi
fi
if [[ $RELAY == yes ]]; then
  ONLY=
  for cli in "${CLIS[@]}"; do [[ $cli == gemini ]] || ONLY="${ONLY:+$ONLY,}$cli"; done
  [[ -n $ONLY ]] || { printf 'Gemini is unsupported by AgentsRelay; choose --no-relay.\n'; exit 2; }
  printf 'Register at https://agentsrelay.net and buy the plan for your chosen tools. Paste group keys only into the hidden local prompt. No temporary token. Gemini personal Google sign-in has been discontinued; use a paid Gemini API key.\n'
  has_tty || { printf 'No terminal: key entry must happen before CLI installation. Rerun --relay in your own terminal.\n'; exit 2; }
  DIR="$HOME/.config/agentsrelay"; mkdir -p "$DIR"; chmod 700 "$DIR"
  [[ ! -L $DIR/agentj-onboard.sh ]] || { printf 'Refusing onboarding symlink.\n'; exit 2; }
  curl --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 45 -fsSL https://agentsrelay.net/onboard/agentsrelay-setup.sh -o "$DIR/agentj-onboard.sh"
  chmod 600 "$DIR/agentj-onboard.sh"
  for attempt in 1 2 3; do
    # Official helper owns hidden input, real model request, defaults and config writes.
    RELAY_RESULT=$(mktemp)
    if sh "$DIR/agentj-onboard.sh" --paste --only "$ONLY" --no-launch </dev/tty | awk -v result="$RELAY_RESULT" 'index($0,"跳过：") {print "skipped" > result} {print; fflush()}'; then
      # Official helper may exit 0 after skipping a requested tool with no matching group key.
      if [[ ! -s $RELAY_RESULT ]] && python3 - "$ONLY" <<'RELAY_CHECK'
import json,os,pathlib,sys
h=pathlib.Path.home(); selected=sys.argv[1].split(',')
checks={'claude':lambda:bool(json.loads((h/'.claude/settings.json').read_text()).get('env',{}).get('ANTHROPIC_AUTH_TOKEN')),
        'codex':lambda: 'AGENTSRELAY_OPENAI_KEY=' in (h/'.config/agentsrelay/env.sh').read_text(),
        'opencode':lambda:any(k.startswith('agentsrelay-') for k in json.loads((pathlib.Path(os.environ.get('XDG_CONFIG_HOME',str(h/'.config')))/'opencode/opencode.json').read_text()).get('provider',{})) and bool((h/'.config/agentsrelay/env.sh').read_text())}
try: sys.exit(0 if all(checks[c]() for c in selected) else 1)
except (OSError,ValueError): sys.exit(1)
RELAY_CHECK
      then rm -f "$RELAY_RESULT"; RELAY_OK=1; break; fi
      printf 'A selected tool has no matching group key. Paste the required key again.\n'
    fi
    rm -f "$RELAY_RESULT"
    ((attempt < 3)) || break
    ask 'Retry key entry or switch to native? [retry/native]:'
    if [[ $REPLY == native ]]; then RELAY=no; break; fi
  done
  [[ $RELAY == no || $RELAY_OK == 1 ]] || { printf 'Relay verification failed after 3 attempts; no CLI installation.\n'; exit 1; }
fi
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1 || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 && process.arch === process.argv[1] ? 0 : 1)' "$NODEARCH"; then
  case "$ARCH" in x86_64) NODEARCH=x64 ;; *) NODEARCH=arm64 ;; esac
  case "$OS" in Darwin) NODEOS=darwin; EXT=tar.gz ;; *) NODEOS=linux; EXT=tar.xz ;; esac
  NODEBASE=https://nodejs.org/dist/latest-v22.x
  NODEMIRROR=https://registry.npmmirror.com/-/binary/node/latest-v22.x
  NODECURL=(curl --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 -fsSL)
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  # The checksum list always comes from the official nodejs.org (small file, 3 tries); a mirror may only supply the tarball.
  SUMS_OK=0
  for attempt in 1 2 3; do
    if "${NODECURL[@]}" --max-time 45 "$NODEBASE/SHASUMS256.txt" -o "$TMP/SHASUMS256.txt"; then SUMS_OK=1; break; fi
  done
  if ((SUMS_OK == 0)); then
    printf 'Node: official checksums (nodejs.org SHASUMS256.txt) unavailable; refusing an unverified download. Install Node.js 22+ yourself and rerun. See %s#dependencies\n' "$PAGE" >&2
    exit 1
  fi
  FILE=$(awk -v target="-$NODEOS-$NODEARCH.$EXT" 'index($2,target)>0 && index($2,target)==length($2)-length(target)+1 {print $2}' "$TMP/SHASUMS256.txt")
  [[ -n $FILE && $FILE != *$'\n'* && $FILE != */* ]] || { printf 'Node artifact selection failed. See %s#dependencies\n' "$PAGE" >&2; exit 1; }
  if ! "${NODECURL[@]}" --max-time 120 "$NODEBASE/$FILE" -o "$TMP/$FILE"; then
    printf 'Node: automatic npmmirror download fallback; verified against the official SHA256.\n'
    rm -f "$TMP/$FILE"
    "${NODECURL[@]}" --max-time 120 "$NODEMIRROR/$FILE" -o "$TMP/$FILE"
  fi
  python3 - "$TMP" "$FILE" <<'PY' || { printf 'Node checksum mismatch: download refused (expected the official nodejs.org SHA256). See %s#dependencies\n' "$PAGE" >&2; exit 1; }
import hashlib, pathlib, sys
p=pathlib.Path(sys.argv[1]); name=sys.argv[2]
expected=[r.split()[0] for r in (p/'SHASUMS256.txt').read_text().splitlines() if len(r.split())==2 and r.split()[1]==name]
sys.exit(0 if len(expected)==1 and hashlib.sha256((p/name).read_bytes()).hexdigest()==expected[0] else 1)
PY
  mkdir -p "$PREFIX/node"
  tar -xf "$TMP/$FILE" -C "$PREFIX/node" --strip-components=1
fi
mkdir -p "$PREFIX/npm"
FAILED=0 SUCCESS=
for cli in "${CLIS[@]}"; do
  STEP=$cli
  if python3 - "$cli" "$PREFIX/npm" "$REGISTRY" "$OS" "$ARCH" <<'P105_RUNTIME'
import base64, hashlib, io, json, os, pathlib, platform, re, shutil, subprocess, sys, tarfile, tempfile, urllib.request, zipfile
cli,prefix,registry,osname,arch=sys.argv[1:]; prefix=pathlib.Path(prefix)
OFFICIAL='https://registry.npmjs.org'; MIRROR='https://registry.npmmirror.com'
pkg={'codex':'@openai/codex','claude':'@anthropic-ai/claude-code','opencode':'@opencode/cli','gemini':'@google/gemini-cli'}[cli]
cpu='x64' if arch=='x86_64' else 'arm64'; system='darwin' if osname=='Darwin' else 'linux'
libc='musl' if system=='linux' and ('musl' in subprocess.run(['ldd','--version'],capture_output=True,text=True).stderr or list(pathlib.Path('/lib').glob('ld-musl-*'))) else 'glibc'
def run(args,timeout=40):
    return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,text=True)
def meta(base,name,version):
    req=urllib.request.Request(base+'/'+name+'/'+version,headers={'User-Agent':'agentj-setup'})
    with urllib.request.urlopen(req,timeout=12) as response: return json.load(response)
def canonical(name,version):
    if version.startswith('npm:'):
        name,version=version[4:].rsplit('@',1)
    return name,version

def native_deps(doc):
    matches=[]
    for name,spec in doc.get('optionalDependencies',{}).items():
        if system+'-'+cpu not in name: continue
        if ('musl' in name)!=(libc=='musl'): continue
        # Prefer portable baseline on x64; do not require AVX2.
        matches.append((name,spec))
    baseline=[d for d in matches if 'baseline' in d[0]]
    return baseline or matches

def verify(command):
    path=pathlib.Path(command).resolve()
    for args in ([command,'--version'],[command,'--help']):
        result=run(args)
        if result.returncode: raise ValueError('runtime --version/--help (macOS dyld / _ubrk_clone: upgrade system libraries)')
    # Native standalone installers, or npm wrapper + matching optional package.
    candidates=[]
    if path.suffix not in ('.js','.cjs','.mjs'):
        candidates.append(path)
    roots=[prefix/'lib/node_modules']
    for parent in path.parents:
        if parent.name=='node_modules': roots.append(parent); break
    for root in dict.fromkeys(roots):
        # Keep an existing npm v1 install usable; verify the package that owns its wrapper.
        owner_pkg='opencode-ai' if cli=='opencode' and (root/'opencode-ai') in path.parents else pkg
        parent=root/owner_pkg
        if not (parent/'package.json').exists(): continue
        doc=json.loads((parent/'package.json').read_text())
        deps=native_deps(doc)
        if not deps: raise ValueError('matching platform optionalDependency '+system+'-'+cpu+'-'+libc)
        for name,_ in deps:
            locations=[parent/'node_modules'/name,root/name]
            found=False
            for location in locations:
                if not location.exists(): continue
                found=True
                candidates.extend(p for p in location.rglob('*') if p.is_file() and (p.name in ('codex','claude','opencode','rg') or p.suffix=='.node'))
            if not found: raise ValueError(name)
    native=[]
    for path in dict.fromkeys(candidates):
        desc=run(['file','-b',str(path)]).stdout
        if 'ELF' not in desc and 'Mach-O' not in desc: continue
        expected=('x86-64','x86_64') if cpu=='x64' else ('aarch64','arm64','ARM64')
        if not any(x in desc for x in expected) or ('Mach-O' in desc)!=(system=='darwin'):
            raise ValueError('native architecture '+path.name)
        if not os.access(path,os.X_OK): raise ValueError('executable '+path.name)
        if path.name=='rg' and run([str(path),'--version']).returncode: raise ValueError('ripgrep')
        if path.suffix=='.node':
            if run(['node','-e','require(process.argv[1])',str(path)]).returncode: raise ValueError('native Node addon '+path.name)
            native.append(path)
        elif path.name!='rg':
            if run([str(path),'--version']).returncode: raise ValueError('native runtime '+path.name)
            native.append(path)
    if not native: raise ValueError('platform native binary '+system+'-'+cpu+'-'+libc)
    if cli=='codex' and not any(p.name=='rg' for p in candidates): raise ValueError('bundled ripgrep')


# Official immutable v2.2.5 release (9bbb8ce18c80f099e0e448775f59d965ead40a21).
# Never releases/latest: that endpoint follows the maintained v1 branch.
OPENCODE_VERSION='2.2.5'
OPENCODE_SHA256={
    'linux-x64': '09f44302c10fd036912f59c81fd1aef5df45dd06f649197f9dfc9bbe2a79614e',
    'linux-arm64': '5a528f42caf576c6cdece6a191eb273d3db54effa5518e0402684d8f8026aeb6',
    'linux-x64-musl': '889389e99ffa39fa41d512b2c9c15316522d97398fb912503ef667f80e22a827',
    'linux-arm64-musl': 'bbf927e3f7a92040f25fd78436c251f41deb25cefac1c906e79c01aa572bf73b',
    'darwin-x64': '4d4419022e6ffbd48afc97c5101bdf001d38b0499f516169b9a80a87a340fd56',
    'darwin-arm64': '25ba9908979fa7d764792c82589b4b5768ab1511154e0317d98aa77d216ce767',
}
def install_opencode():
    target=system+'-'+cpu+('-musl' if libc=='musl' else '')
    ext='.zip' if system=='darwin' else '.tar.gz'
    url='https://github.com/anomalyco/opencode/releases/download/v'+OPENCODE_VERSION+'/opencode-'+target+ext
    request=urllib.request.Request(url,headers={'User-Agent':'agentj-setup'})
    with urllib.request.urlopen(request,timeout=120) as response:
        archive=response.read(256*1024*1024+1)
    if len(archive)>256*1024*1024 or hashlib.sha256(archive).hexdigest()!=OPENCODE_SHA256[target]:
        raise ValueError('OpenCode official archive SHA256 mismatch')
    # Extract one known member only, never archive paths or symlinks.
    if ext=='.zip':
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            data=bundle.read('opencode')
    else:
        with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as bundle:
            member=bundle.getmember('opencode')
            if not member.isfile(): raise ValueError('OpenCode archive binary type')
            data=bundle.extractfile(member).read()
    bindir=prefix/'bin'; bindir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.opencode-',dir=bindir) as tmp:
        binary=pathlib.Path(tmp)/'opencode'; binary.write_bytes(data); binary.chmod(0o755)
        verify(str(binary))
        result=run([str(binary),'--version'])
        if result.returncode or not re.fullmatch(r'(?:opencode\s+)?v?'+re.escape(OPENCODE_VERSION),result.stdout.strip()):
            raise ValueError('OpenCode pinned version mismatch')
        os.replace(binary,bindir/'opencode')
    print('OpenCode '+OPENCODE_VERSION+': official native archive SHA256 verified',flush=True)

def integrity_pair(name,version):
    name,version=canonical(name,version)
    official=meta(OFFICIAL,name,version); mirror=meta(MIRROR,name,version)
    integrity=official['dist'].get('integrity','')
    if not integrity.startswith('sha512-') or mirror['dist'].get('integrity')!=integrity:
        raise ValueError('official/mirror SHA512 mismatch '+name)
    # Download mirror bytes separately: compare actual bytes, not just metadata.
    url=mirror['dist']['tarball']
    if not url.startswith('https://'): raise ValueError('non-HTTPS tarball')
    h=hashlib.sha512()
    with urllib.request.urlopen(url,timeout=30) as response:
        while True:
            block=response.read(1024*1024)
            if not block: break
            h.update(block)
    if base64.b64encode(h.digest()).decode()!=integrity.split('-',1)[1]: raise ValueError('mirror tarball SHA512 '+name)
    print('SHA512 official = mirror = downloaded bytes: '+name+'@'+official['version'],flush=True)

try:
    existing=shutil.which(cli)
    if existing:
        verify(existing)
    elif cli=='opencode':
        install_opencode()
    else:
        # The canonical endpoint is separate from a user-supplied download registry.
        # If official metadata cannot be obtained, fail closed: never assert equality without evidence.
        doc=meta(OFFICIAL,pkg,'latest'); version=doc['version']
        args=['npm','install','--cpu='+cpu,'--os='+system,'--global','--prefix',str(prefix),'--include=optional','--ignore-scripts=false','--fetch-retries=0','--fetch-timeout=20000','--no-audit','--no-fund',pkg+'@'+version]
        def install(source):
            if source.rstrip('/')==MIRROR:
                integrity_pair(pkg,version)
                for name,spec in native_deps(doc): integrity_pair(name,spec)
            # Ignore user scope registries/omit settings: all optional binaries use this source.
            env=dict(os.environ)
            for k in list(env):
                if k.lower().startswith('npm_config_'): del env[k]
            config_dir=tempfile.TemporaryDirectory(prefix='agentj-npm-config-')
            for name in ('user','global'): pathlib.Path(config_dir.name,name).touch(mode=0o600)
            env['NPM_CONFIG_USERCONFIG']=str(pathlib.Path(config_dir.name,'user')); env['NPM_CONFIG_GLOBALCONFIG']=str(pathlib.Path(config_dir.name,'global'))
            result=subprocess.run(args+['--registry',source],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240,text=True)
            config_dir.cleanup()
            if result.returncode: raise ValueError('npm installation failed or timed out')
            verify(str(prefix/'bin'/cli))
        try: install(registry)
        except (ValueError,subprocess.TimeoutExpired):
            if registry.rstrip('/')==MIRROR: raise
            print(cli+': automatic npmmirror fallback (including optional platform binaries)',flush=True)
            install(MIRROR)
    print(cli+': installation complete; platform binaries, bundled components and offline help passed',flush=True)
except Exception as exc:
    # Network/library exceptions may carry sensitive URL details; expose only controlled reasons.
    reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
    print('安装不完整：缺 '+reason+'; '+cli+'. Repair: npm install --global --include=optional --prefix "$HOME/.local/share/agentj-setup/npm" --registry=https://registry.npmjs.org '+pkg,file=sys.stderr)
    sys.exit(1)

P105_RUNTIME
  then SUCCESS="${SUCCESS:+$SUCCESS,}$cli"; else FAILED=1; fi
done
STEP=full-access
# One configuration writer, embedded so the downloaded shell script is standalone.
python3 - "$SUCCESS" "$FULL" "$OS" "$ALIASES" <<'PY'
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
    for rc in ('.zprofile','.bash_profile'): block(home/rc,'PATH',path)
if mac and (pathlib.Path('/opt/homebrew/bin/brew').exists() or pathlib.Path('/usr/local/bin/brew').exists()):
    brew='/opt/homebrew/bin/brew' if pathlib.Path('/opt/homebrew/bin/brew').exists() else '/usr/local/bin/brew'
    for rc in ('.zprofile','.bash_profile'): block(home/rc,'Homebrew',f'eval "$({brew} shellenv)"\n'+path)
if sys.argv[4]=='1':
    definitions={'codex':"alias cx='codex --dangerously-bypass-approvals-and-sandbox'",'claude':"alias cc='claude --dangerously-skip-permissions'",'opencode':"alias oc='opencode --auto'",'gemini':"alias gx='gemini --yolo --sandbox=false'"}
    if not full:
        definitions={k: 'alias '+{'codex':'cx','claude':'cc','opencode':'oc','gemini':'gx'}[k]+"='"+k+"'" for k in definitions}
    for rc in ('.bashrc','.zshrc'):
        p=home/rc; old=p.read_text() if p.exists() else ''; lines=old.splitlines()
        added=[v for k,v in definitions.items() if k in selected and v not in lines]
        if added: write(p,old+('\n' if old and not old.endswith('\n') else '')+'\n'.join(added)+'\n')
    if 'claude' in selected: print('cc alias shadows the interactive C compiler; use command cc for the compiler, or rename to ccx.')
if 'claude' in selected:
    # P219: Claude Code's one-time terminal screens (onboarding / theme, bypass-mode confirmation) answered ahead, so a
    # background session never waits at a terminal nobody sees. Merge only; folder trust is `agentj agent claude --dir`.
    def first_run(d):
        if d.get('hasCompletedOnboarding') is not True: d['hasCompletedOnboarding']=True
        d.setdefault('theme','dark')
    merge(home/'.claude.json',first_run)
    merge(home/'.claude/settings.json',lambda d:d.setdefault('skipDangerousModePermissionPrompt',True))
if full:
    if 'claude' in selected and os.geteuid()==0:
        # Claude Code refuses bypassPermissions for root and exits before its first word (P219, measured 2.1.296)
        print('root: Claude Code refuses bypassPermissions for root; left at its default mode. Use a normal user for full access.')
    elif 'claude' in selected:
        merge(home/'.claude/settings.json',lambda d:d.setdefault('permissions',{}).update(defaultMode='bypassPermissions'))
    if 'opencode' in selected:
        p=pathlib.Path(os.environ.get('XDG_CONFIG_HOME',str(home/'.config')))/'opencode/opencode.json'
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
printf 'Log in in your own terminal: opencode auth login / codex login / claude auth login.\n'
printf 'OpenCode 2.2.5 Claude Agent: install Claude Code 2.1.248+, run claude auth login yourself, then open opencode and choose /connect → Claude Agent. This differs from agentj agent claude. Login alone does not verify a model reply. / Claude Agent：先安装 Claude Code 2.1.248+，本人运行 claude auth login，再在 opencode 的 /connect 选 Claude Agent；与 agentj agent claude 是两个入口，登录不等于模型已验证可用。\n'
if [[ ,$SELECT, == *,gemini,* ]]; then printf 'Gemini: Google has discontinued personal login; a paid Gemini API key is required. Enter it only in your own terminal.\n'; fi
printf 'Launch your selected tool: opencode --auto; codex --dangerously-bypass-approvals-and-sandbox; claude --dangerously-skip-permissions\n'
if [[ ,$SELECT, == *,gemini,* ]]; then printf 'Optional Gemini launch: gemini-full-access\n'; fi
STEP=agent-j
if ((FAILED)); then printf 'Some CLI installations are incomplete. Repair before installing Agent J.\n'; exit 1; fi
# Do not infer that an installed CLI is authenticated. No credentials are read or printed.
if ! python3 - "$RELAY_OK" <<'AUTH_CHECK'
import json,os,pathlib,sys
h=pathlib.Path.home()
def credential(p):
    try:
        d=json.loads(p.read_text()); return isinstance(d,dict) and bool(d)
    except (OSError,ValueError): return False
# Presence is a hint, not a claim that tokens are still valid. Never print/read values into shell.
ok=sys.argv[1]=='1' or any(os.environ.get(k) for k in ('OPENAI_API_KEY','ANTHROPIC_API_KEY')) or any(credential(h/p) for p in ('.codex/auth.json','.claude/.credentials.json','.local/share/opencode/auth.json'))
sys.exit(0 if ok else 1)
AUTH_CHECK
then printf 'Agent J 能装能配对，但要登录 AI 工具或填 key 后才能真正聊天。 / Agent J can install and pair, but chat needs AI login or an API key.\n'; fi
if [[ $AGENTJ == ask ]]; then
  if has_tty; then ask '现在安装 Agent J 吗？[Y/n]'; [[ $REPLY == n || $REPLY == N ]] && AGENTJ=no || AGENTJ=yes
  else AGENTJ=no; fi
fi
if [[ $AGENTJ == yes ]]; then
  if has_tty; then
    printf '在账户页创建安装码；安装码只在本机终端输入，不要发进聊天。\n'
    printf '在账户页点「复制安装码」，粘贴到这里。 / On the account page choose Copy installation code and paste it here.\n'
    printf 'Installation code (AJI-…): ' >/dev/tty
    IFS= read -r -s INSTALL_CODE </dev/tty
    # P214: a pasted prompt or command spans lines; take the rest of the paste too, so it cannot answer a later question.
    while IFS= read -r -s -t 1 MORE </dev/tty; do INSTALL_CODE+=$'\n'$MORE; done; INSTALL_CODE+=$'\n'${MORE-}; unset MORE
    printf '\n' >/dev/tty
    # Keep only the first AJI code (full-width characters, any dash, spaces, CRLF); the input goes by stdin, never argv.
    INSTALL_CODE=$(printf '%s' "$INSTALL_CODE" | python3 -c "$(cat <<'EXTRACT_CODE'
import re,sys,unicodedata
s=unicodedata.normalize('NFKC',sys.stdin.read()).translate({ord(c):'-' for c in '\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d\u30fc'})
for t in (s,re.sub(r'\s+','',s)):
    m=re.search(r'AJI-([0-9a-f]{32})(?![0-9a-f])',t,re.I)
    if m: print('AJI-'+m.group(1).lower()); sys.exit()
print('ajt' if re.search(r'aj[th]_[A-Za-z0-9_-]{43}',s) else '')
EXTRACT_CODE
)")
    if [[ $INSTALL_CODE != AJI-* ]]; then
      printf '没找到安装码。请只粘贴 AJI- 开头的那串码：在账户页点「复制安装码」。 / No installation code found. Paste only the code that starts with AJI- (account page → Copy installation code).\n'
      [[ $INSTALL_CODE == ajt ]] && printf 'ajt_ / ajh_ 开头的是设置码，只给已经装好 Agent J 的 Agent J 主机绑定席位用。 / A code starting with ajt_ / ajh_ is a setup code; it only links an Agent J host that already has Agent J installed.\n'
      printf 'See %s#agent-j\n' "$PAGE"; exit 2
    fi
    AJTMP=$(mktemp -d); trap 'rm -rf "$AJTMP"' EXIT
    curl --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 90 -fsSL https://agentj.app/install-assistant.sh -o "$AJTMP/install-assistant.sh"
    python3 - "$AJTMP/install-assistant.sh" <<'VERIFY_INSTALLER'
import hashlib,pathlib,sys
if hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest() != 'bad892727c2e47038262c5ccfb632f00c597365cad3564a6860feb08e67d3d1f':
    sys.exit('Installer integrity check failed')
VERIFY_INSTALLER
    sh "$AJTMP/install-assistant.sh" "$INSTALL_CODE" </dev/tty
    unset INSTALL_CODE
  else printf 'No terminal: Agent J requires an installation code and local confirmation. Open a terminal and rerun with --with-agentj.\n'; fi
fi
printf 'Next: %s#agent-j\n' "$PAGE"
exit "$FAILED"
