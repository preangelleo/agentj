#!/usr/bin/env python3
"""Local installation driver. AI sees public guide only; all native tools denied.
Never use remote shell, customer files or model credentials as conversational input.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import uuid
import urllib.error
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise InstallError('redirect_refused')
HTTP = urllib.request.build_opener(NoRedirect())
ORIGIN = 'https://install-assistant.agentj.app'
SITE = 'https://agentj.app'
# agentj.app's edge answers 403 to urllib's default "Python-urllib/x.y" agent (P73: that alone failed every one-line install).
USER_AGENT = 'agentj-install-assistant/1 (+https://agentj.app/install/)'
# No wheel is pinned in this file: /dl keeps only the current release. The wheel name comes from
# /dl/latest.txt and is accepted only when install.md (bytes == /install.md.sha256) pins that same name
# and SHA-256, and the /dl/<wheel>.sha256 sidecar agrees; the downloaded bytes must match it (ADR-A179).
WHEEL_RE = re.compile(r'agentj-[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?-py3-none-any\.whl')
LOCK = json.loads((Path(__file__).with_name('runtime-lock.json')).read_text())

class InstallError(Exception):
    """args[0] is a fixed reason token built here (never a code, lease, key or server text)."""
    @property
    def reason(self):
        return str(self.args[0]) if self.args else 'unknown'

def artifact_base():
    """Only the current production static origin is allowed."""
    value = os.environ.get('AGENTJ_INSTALL_ARTIFACT_BASE', SITE)
    if value not in (SITE, SITE+'/'):
        raise InstallError('unapproved_artifact_origin')
    return value.rstrip('/')

def runtime_url(platform_key, pin):
    base = artifact_base()
    return pin['url']

def service_reason(exc):
    """The installation service answers {"error": "<token>"}; keep only a bare token."""
    try:
        value = json.loads(exc.read(4097)).get('error')
    except Exception:
        value = None
    return value if isinstance(value, str) and re.fullmatch(r'[a-z_]{1,40}', value) else 'http_' + str(int(getattr(exc, 'code', 0) or 0))

def request(path, body=None, lease=None, origin=ORIGIN):
    headers = {'content-type': 'application/json', 'user-agent': USER_AGENT}
    if lease: headers['authorization'] = 'Bearer ' + lease
    req = urllib.request.Request(origin + path, data=json.dumps(body or {}).encode(), headers=headers)
    try:
        with HTTP.open(req, timeout=20) as r:
            raw = r.read(65537)
        if len(raw) > 65536: raise InstallError('assistant_response_too_large')
        return json.loads(raw)
    except InstallError:
        raise
    except urllib.error.HTTPError as exc:
        reason = service_reason(exc); exc.close()
        # 410 code_unavailable = this code was already redeemed, has expired or never existed.
        raise InstallError('code_unavailable' if reason == 'code_unavailable' else 'assistant_refused:' + reason) from None
    except Exception as exc:
        if hasattr(exc, "close"): exc.close()
        raise InstallError('assistant_request_failed') from None

# P328: a file the release has just uploaded can answer 404 (or a 5xx / no answer) for a few seconds while it propagates.
# A short retry keeps an install that starts in that minute from failing — after redeem a failed download spends the code.
FETCH_RETRY_DELAYS = (2, 4)   # 3 attempts, at most 6 s of waiting; only 404/408/425/429/5xx and no answer are retried
_retry_sleep = None           # tests inject a recorder; None = time.sleep

def _transient_http(code):
    return code in (404, 408, 425, 429) or 500 <= code <= 599

def _fetch_once(url, hostname, limit):
    """One GET. Returns bytes, or (reason, retryable) for a failure; InstallError for a refusal that is never retried."""
    name = urlparse(url).path.rsplit('/', 1)[-1][:80]
    try:
        with HTTP.open(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=30) as r:
            if urlparse(r.geturl()).hostname != hostname: raise InstallError('untrusted_redirect')
            data = r.read(limit + 1)
    except InstallError:
        raise
    except urllib.error.HTTPError as exc:
        exc.close()
        return 'artifact_http_' + str(exc.code) + ':' + name, _transient_http(exc.code)
    except Exception:
        return 'artifact_unreachable:' + name, True
    if len(data) > limit: raise InstallError('download_too_large')
    return data

def fetch(url, limit=200_000_000):
    """HTTPS GET without redirects; failures name the artifact and HTTP status only."""
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise InstallError('unsafe_artifact_url')
    waits = tuple(FETCH_RETRY_DELAYS) + (None,)
    for number, wait in enumerate(waits, 1):
        got = _fetch_once(url, parsed.hostname, limit)
        if isinstance(got, bytes):
            return got
        reason, transient = got
        if not transient or wait is None:
            raise InstallError(reason) from None
        what = reason.split(':', 1)
        print('下载 %s 暂时没成功（%s），%d 秒后自动重试（第 %d/%d 次）… / %s: %s, retrying in %d s'
              % (what[-1], what[0], wait, number + 1, len(waits), what[-1], what[0], wait), file=sys.stderr, flush=True)
        (_retry_sleep or time.sleep)(wait)

def front_matter(text):
    lines = text.split('\n')
    if not lines or lines[0].strip() != '---': raise InstallError('install_guide_invalid')
    out = {}
    for line in lines[1:]:
        if line.strip() == '---': return out
        key, sep, value = line.partition(':')
        if sep and re.fullmatch(r'[a-z0-9_]+', key.strip()): out[key.strip()] = value.strip()
    raise InstallError('install_guide_invalid')

def resolve_wheel(base, get=None):
    """Follow /dl/latest.txt, pinned by the published install.md hash chain. Returns (wheel, sha256)."""
    get = get or fetch
    name = get(base + '/dl/latest.txt', 4096).decode('ascii', 'replace').strip()
    if not WHEEL_RE.fullmatch(name): raise InstallError('latest_wheel_invalid')
    guide = get(base + '/install.md', 2_000_000)
    published = get(base + '/install.md.sha256', 4096).decode('ascii', 'replace').split()
    if published != [hashlib.sha256(guide).hexdigest(), 'install.md']: raise InstallError('install_guide_integrity_failed')
    meta = front_matter(guide.decode('utf-8', 'replace'))
    if meta.get('host_wheel') != name: raise InstallError('latest_wheel_not_pinned:' + name)
    sha = meta.get('host_wheel_sha256', '')
    if not re.fullmatch(r'[0-9a-f]{64}', sha): raise InstallError('install_guide_invalid')
    if get(base + '/dl/' + name + '.sha256', 4096).decode('ascii', 'replace').split() != [sha, name]:
        raise InstallError('wheel_sidecar_mismatch:' + name)
    return name, sha

# Same release signer as host/agentj/minisign.py; a site-side SHA cannot replace this trust anchor.
TRUSTED_SIGNERS = {"B79F925B0585F9D4": "RWTU+YUFW5KftwwG/MQxB1V0OEpzftjDN8udv1Id9oYK9+OUanc2uyb5"}
MAX_SIG = 1024

def verify_wheel(data, signature, filename):
    """Verify both minisign signatures and the exact host/version/SHA trusted comment."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        if len(signature) > MAX_SIG: raise ValueError()
        lines = signature.decode('utf-8').replace('\r\n', '\n').rstrip('\n').split('\n')
        if len(lines) != 4 or not lines[0].startswith('untrusted comment: ') or not lines[2].startswith('trusted comment: '): raise ValueError()
        raw = base64.b64decode(lines[1], validate=True)
        global_sig = base64.b64decode(lines[3], validate=True)
        if len(raw) != 74 or raw[:2] != b'ED' or len(global_sig) != 64: raise ValueError()
        kid = raw[2:10][::-1].hex().upper()
        pub = base64.b64decode(TRUSTED_SIGNERS[kid], validate=True)
        if len(pub) != 42 or pub[:2] != b'Ed' or pub[2:10] != raw[2:10]: raise ValueError()
        key = Ed25519PublicKey.from_public_bytes(pub[10:])
        key.verify(raw[10:], hashlib.blake2b(data, digest_size=64).digest())
        comment = lines[2][len('trusted comment: '):]
        key.verify(global_sig, raw[10:] + comment.encode('utf-8'))
        if not WHEEL_RE.fullmatch(filename): raise ValueError()
        version = filename[len('agentj-'):-len('-py3-none-any.whl')]
        if comment != f'agentj-host/v1 version={version} sha256={hashlib.sha256(data).hexdigest()}': raise ValueError()
    except Exception:
        # Never expose downloaded text or dependency error details.
        raise InstallError('wheel_signature_failed') from None


def download(url, output, expected, algorithm='sha256'):
    data = fetch(url)
    h = hashlib.new(algorithm, data).digest()
    good = h.hex() == expected if algorithm == 'sha256' else base64.b64encode(h).decode() == expected
    if not good: raise InstallError('integrity_failed:' + urlparse(url).path.rsplit('/', 1)[-1][:80])
    filename = urlparse(url).path.rsplit('/', 1)[-1]
    if filename.endswith('.whl'):
        verify_wheel(data, fetch(url + '.minisig', MAX_SIG), filename)
    output.write_bytes(data)

def extract_binary(archive, destination):
    # Read only the package's binary member: no archive path/symlink extraction.
    with tarfile.open(archive) as tf:
        candidates = [m for m in tf.getmembers() if m.isfile() and m.name in ('package/bin/opencode', 'package/bin/opencode.exe')]
        if len(candidates) != 1: raise InstallError('binary_missing')
        with tf.extractfile(candidates[0]) as f: destination.write_bytes(f.read())
    destination.chmod(0o700)

def assistant_config(lease, work):
    if not isinstance(lease.get('api_key'), str) or not lease['api_key']: raise InstallError('invalid_lease')
    if lease.get('base_url') != ORIGIN + '/v1': raise InstallError('invalid_assistant_origin')
    if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,120}', lease.get('model', '')): raise InstallError('invalid_model')
    try:
        expires = datetime.fromisoformat(lease['expires_at'].replace('Z','+00:00')).timestamp()
        import time
        if not time.time() < expires <= time.time()+86460 or lease.get('quota_usd') not in (2,10): raise ValueError()
    except (KeyError, ValueError, TypeError): raise InstallError('invalid_lease_limits') from None
    model = lease['model']
    disabled = {name: False for name in ('bash','read','write','edit','glob','grep','webfetch','websearch','codesearch','task','todowrite','todoread','question','skill','apply_patch','lsp')}
    return {'$schema': 'https://opencode.ai/config.json', 'model': 'installation/' + model,
      'provider': {'installation': {'npm': '@ai-sdk/openai-compatible', 'options': {'baseURL': lease['base_url'], 'apiKey': lease['api_key']}, 'models': {model: {'name': 'Installation assistant', 'limit': {'context': 16000, 'output': 1024}}}}},
      'permission': {'*': 'deny'}, 'tools': disabled, 'agent': {'installation': {'mode': 'primary',
        'permission': {'*': 'deny'}, 'tools': disabled, 'prompt': 'Explain only the public Agent J installation steps. You cannot inspect or modify this computer. Never request passwords, keys, private files or conversations. Formal model keys are entered only on the paired phone secret card. No general coding, distillation or business tasks.'}},
      'share': 'disabled', 'autoupdate': False}

def formal_metadata(value):
    """Persist only validated public setup hints, never the temporary model key/session."""
    out={}
    if value.get('registration_url') == ORIGIN+'/register': out['registration_url']=value['registration_url']
    base=value.get('formal_base_url');model=value.get('formal_model')
    if isinstance(base,str):
        u=urlparse(base)
        if u.scheme=='https' and u.hostname and not (u.username or u.password or u.query or u.fragment): out['formal_base_url']=base
    if isinstance(model,str) and re.fullmatch(r'[A-Za-z0-9._:/-]{1,120}',model): out['formal_model']=model
    return out

def consent(label):
    return input(label + ' [y/N]: ').strip().lower() == 'y'

def command(args, *, allow_failure=False, timeout=240, **kwargs):
    # Do not print subprocess output: errors can contain secrets. Credentials are never in argv.
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, **kwargs)
        if r.returncode and not allow_failure:
            if platform.system() == 'Darwin' and args[-1:] == ['--version']:
                reason = 'macos_agent_dyld' if any(token in r.stderr for token in (b'dyld', b'_ubrk_clone')) else 'macos_agent_unusable'
                raise InstallError(reason)
            raise InstallError('local_step_failed')
        return r.stdout
    except (OSError, subprocess.TimeoutExpired): raise InstallError('local_step_failed') from None

def validate_working_root(root, home=None):
    """Resolve aliases before any install writes or redemption; never scan a whole home or disk."""
    home = Path(home or Path.home()).resolve()
    root = Path(root).expanduser().resolve()
    if root == home or root == Path(root.anchor) or (root.exists() and not root.is_dir()):
        raise InstallError('working_root_required_choose_project_not_home')
    return root


def explain_installation(oc, work, child, *, run=command):
    # The pinned runtime's bundled/local model catalog works offline. Never block installation on models.dev.
    env = dict(child, OPENCODE_DISABLE_MODELS_FETCH='1')
    try:
        return run([str(oc), 'run', '--format', 'json', '--agent', 'installation',
                    'Explain that this installer will install verified Agent J, ask the owner to pair their phone and enter their formal key only in the secure phone card.'],
                   cwd=work, env=env, timeout=45)
    except (InstallError, subprocess.TimeoutExpired):
        print('OpenCode 初始化超过 45 秒或启动失败。已禁用 models.dev 在线拉取，使用本地模型目录；请检查网络，并选择专用工作目录，勿用用户主目录。Windows 桌面版可在 PowerShell 设置 $env:OPENCODE_DISABLE_MODELS_FETCH="1" 后重新启动；Agent J 请用 WSL2 安装。 / OpenCode startup failed or exceeded 45 seconds. Local model catalog is enabled; check the network and choose a project folder instead of your home. For Windows desktop, set $env:OPENCODE_DISABLE_MODELS_FETCH="1" in PowerShell before restarting; install Agent J through WSL2.', file=sys.stderr)
        raise InstallError('opencode_startup_timeout_or_failed') from None


def require_linux_isolation(agentj, run=command):
    """Use the installed host's pure diagnostic; never alter packages, policy or sysctl."""
    if platform.system() != 'Linux':
        return
    try:
        report = json.loads(run([str(agentj), 'doctor', '--isolation-only', '--json'], allow_failure=True))
        rows = report['checks']
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get('id') != 'fence':
            raise ValueError()
        row = rows[0]
        if report.get('ok') is True and row.get('status') == 'ok':
            return
        # This mode returns only public machine-capability diagnostics, no user state or secrets.
        for field in ('summary', 'hint'):
            text = row.get(field, '')
            if isinstance(text, str) and text:
                print(text[:12000], file=sys.stderr)
    except (ValueError, TypeError, KeyError):
        print('无法确认 Linux 隔离可用；请运行 agentj doctor --isolation-only。 / Linux isolation was not verified; run agentj doctor --isolation-only.', file=sys.stderr)
    raise InstallError('linux_isolation_not_ready')

def browser_setup(agentj, run=command):
    """P114 (ADR-A194): Agent J's own browser is part of the default install and never blocks it. The output is the
    command's own JSON state (no secrets); anything but ready / off becomes one line for the owner, fixed later by the Agent."""
    try:
        out = run([str(agentj), 'browser', 'setup', '--json'], allow_failure=True, timeout=420) or b''
        state = json.loads(out.decode(errors='replace').strip().splitlines()[-1]) if out.strip() else {}
    except (InstallError, ValueError, IndexError):
        state = {}
    result = state.get('result') if isinstance(state, dict) else None
    if result == 'ready':
        print('Agent J 专用浏览器已就绪。/ Agent J\'s own browser is ready.')
    elif result != 'disabled':
        print('Agent J 专用浏览器暂未就绪，不影响其他功能；之后让 Agent 运行 agentj browser setup。/ Agent J\'s browser is not ready yet; '
              'everything else works. Ask your Agent to run agentj browser setup later.')
    return result


def host_install_args():
    pin = LOCK.get(platform.system() + '-' + platform.machine(), {})
    return [arg for constraint in pin.get('host_constraints', []) for arg in ('--with', constraint)]

CLAUDE_DEFAULT_MODEL = 'claude-opus-5-5'   # P219/P220: what Agent J pins for Claude Code; relays serve it (REPORT-fable)


def configured_harness(agentj, *, run=command, prompt=input):
    """Installed host owns resolver and real call probes. No authentication output is printed."""
    # A background service cannot inherit a terminal's transient model keys.
    # Keep only public launch context; installed CLI loads official Relay secrets privately.
    keep = {'HOME', 'PATH', 'LANG', 'LC_ALL', 'TZ', 'SHELL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
            'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME'}
    probe_env = {k: v for k, v in os.environ.items() if k in keep or
                 (k.startswith('AGENTJ_') and k.endswith('_BIN'))}
    try:
        report = json.loads(run([str(agentj), 'agent', 'detect', '--probe', '--json'], timeout=300, env=probe_env))
    except (InstallError, ValueError, TypeError):
        return None
    if not isinstance(report, dict) or not isinstance(report.get('harnesses', []), list) or not isinstance(report.get('usable', []), list):
        return None
    rows = {r.get('name'): r for r in report.get('harnesses', []) if isinstance(r, dict) and isinstance(r.get('name'), str)}
    # Same priority as doctor/detect: Claude Code, Codex, OpenCode. Multiple means owner choice.
    ready = [k for k in ('claude', 'codex', 'opencode') if k in report.get('usable', [])
             and rows.get(k, {}).get('callable') is True and rows[k].get('executable')]
    model = None
    claude = rows.get('claude', {})
    if 'claude' not in ready and claude.get('installed') and claude.get('executable'):
        # P220 (HK owner 10-13): Claude Code picked a model the relay does not serve (400 "Claude Fable 5.1 is not
        # supported"). One more real call with Agent J's default model; it is pinned only when that call answers.
        try:
            again = json.loads(run([str(agentj), 'agent', 'detect', 'claude', '--probe', '--model', CLAUDE_DEFAULT_MODEL, '--json'],
                                   timeout=120, env=probe_env))
            row = next((r for r in again.get('harnesses', []) if isinstance(r, dict) and r.get('name') == 'claude'), {})
            if row.get('callable') is True and row.get('executable'):
                ready.insert(0, 'claude')
                rows['claude'] = row
                model = CLAUDE_DEFAULT_MODEL
                print('Claude Code 用默认模型 ' + CLAUDE_DEFAULT_MODEL + ' 回复成功，已选用它。/ Claude Code answered with ' + CLAUDE_DEFAULT_MODEL + '.')
        except (InstallError, ValueError, TypeError, AttributeError):
            pass
    if not ready:
        gemini = shutil.which('gemini', path=probe_env.get('PATH'))
        if gemini:
            try:
                if run([gemini, '--version'], timeout=15, env=probe_env).strip():
                    print('Agent J 主 Agent 暂不支持 Gemini，请再登录 Codex / Claude Code / OpenCode 之一或用 AgentsRelay。 / Agent J does not yet support Gemini as the main Agent. Sign in to Codex / Claude Code / OpenCode, or use AgentsRelay.')
            except InstallError:
                pass
        return None
    chosen = ready[0]
    if len(ready) > 1:
        print('已验证可用 / Verified: ' + ', '.join(ready))
        chosen = prompt('选择主 Agent / Choose main Agent [' + chosen + ']: ').strip() or chosen
        if chosen not in ready:
            raise InstallError('invalid_harness_choice')
    out = {'name': chosen, 'executable': rows[chosen]['executable']}
    if chosen == 'claude' and model:
        out['model'] = model
    return out


# P390 (Leo 10-18 21:31): one extra option, never the default — 「用 OpenCode 自带的免费模型先跑起来」. Zero-retention
# families first (OpenCode Zen docs, 10-18: DeepSeek Flash Free, LongCat 2.5 Preview Free — "does not use your data for
# model training"); the host's own pick (agentj agent detect → free_model) leads, at most FREE_TRIES real calls.
FREE_FIRST = ('deepseek-', 'longcat')
FREE_TRIES = 3
FREE_TERMS = (   # OpenCode Zen's own docs (10-18), the same families as the host's opencode_free.DATA_TERMS
    (FREE_FIRST, '按 OpenCode 官方说明，它的供应商不留存、不拿对话训练（免费期条款随时可能变）',
     "per OpenCode's docs its provider keeps no data and does not train on it (free-period terms can change)"),
    (('mimo', 'big-pickle'), '免费期间的对话可能被拿去改进模型', 'conversations may be used to improve the model during the free period'),
    (('muse-spark',), '对话会被拿去训练模型', 'conversations are used to train models'),
    (('nemotron',), '对话会被记录（官方要求别发个人或机密信息）', 'usage is logged (the provider asks for no personal or confidential data)'),
)
FREE_TERMS_UNKNOWN = ('数据条款以 OpenCode 官方页为准，可能用于训练', "check OpenCode's page for its data terms; it may be used for training")


def free_text(model):
    """P390: the plain free-model note — on the phone's first page, the terminal and the summary."""
    mid = model.split('/', 1)[-1]
    zh, en = next(((z, e) for fams, z, e in FREE_TERMS if mid.startswith(fams)), FREE_TERMS_UNKNOWN)
    return ('当前用的是 OpenCode 自带的免费模型 ' + model + '（不用 key）：对话和代码会发给 OpenCode 和它的模型供应商；' + zh +
            '；有限流，随时可能下线，敏感工作请换成自己的模型。'
            '换成自己的模型：手机 ≡ 菜单「模型与 Key」（或发 /model），也可以直接跟 Agent 说「换成我自己的模型」，'
            '它会弹出安全的密钥卡或登录引导；key 只在密钥卡或你自己的终端里输入。\n'
            'You are on one of OpenCode\'s own free models, ' + model + ' (no key): conversations and code go to OpenCode and '
            'its model provider; ' + en + '; it is rate-limited and '
            'may disappear, so switch to a model of your own for sensitive work — ≡ menu → Models & Key (or send /model), '
            'or tell the Agent "switch to my own model"; the key goes only into a key card or your own terminal.')


def free_candidates(first, listed):
    """Free model ids to try, best first: the host's pick, zero-retention families, the rest; big-pickle never."""
    ids = [m for m in [first, *listed] if isinstance(m, str) and re.fullmatch(r'opencode/[A-Za-z0-9._-]{1,80}', m)
           and not m.split('/', 1)[1].startswith(('space-bunny', 'minimax-m3.1-flash', 'big-pickle')) and m in listed and m.endswith('-free')]
    rank = lambda m: (m != first, next((i for i, f in enumerate(FREE_FIRST) if m.split('/', 1)[1].startswith(f)), len(FREE_FIRST)))
    return sorted(dict.fromkeys(ids), key=rank)[:FREE_TRIES]


def free_harness(agentj, oc, *, run=command):
    """P390: the owner chose OpenCode's own free model. The installed host lists, ranks and makes the real call (its
    probe takes the free tier's formal chat path and waits out the first-minute FreeTierError); None = clear fallback."""
    env = {k: v for k, v in os.environ.items() if k in ('HOME', 'PATH', 'LANG', 'LC_ALL', 'TZ', 'SHELL', 'SSL_CERT_FILE',
           'SSL_CERT_DIR', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME')}
    env['AGENTJ_OPENCODE_BIN'] = str(oc)
    try:
        report = json.loads(run([str(agentj), 'agent', 'detect', 'opencode', '--json'], timeout=120, env=env,
                                stdin=subprocess.DEVNULL))
        row = next((r for r in report.get('harnesses', []) if isinstance(r, dict) and r.get('name') == 'opencode'), {})
        first = row.get('free_model')
    except (InstallError, ValueError, TypeError, AttributeError):
        first = None
    try:
        listed = run([str(oc), 'models', 'opencode'], timeout=60, env=env, stdin=subprocess.DEVNULL).decode(errors='replace').split()
    except (InstallError, subprocess.CalledProcessError, AttributeError):
        listed = []
    for model in free_candidates(first, listed):
        print('试一下免费模型 ' + model + '（第一次可能要等一两分钟）… / Trying free model ' + model + '…')
        try:
            again = json.loads(run([str(agentj), 'agent', 'detect', 'opencode', '--probe', '--model', model, '--json'],
                                   timeout=300, env=env, stdin=subprocess.DEVNULL))
            row = next((r for r in again.get('harnesses', []) if isinstance(r, dict) and r.get('name') == 'opencode'), {})
        except (InstallError, ValueError, TypeError, AttributeError):
            continue
        if row.get('callable') is True:
            return {'name': 'opencode', 'executable': str(oc), 'model': model, 'free': True}
    print('OpenCode 的免费模型现在用不了（免费通道繁忙、限流或已下线）：继续原来的引导，接自己的 AI。'
          ' / OpenCode\'s free models do not answer right now: continuing with the usual way to connect your own AI.')
    return None


def free_notice(agentj, model, *, run=command):
    """P390: the phone's first page says plainly that this is one of OpenCode's free models, and where to switch. The owner's own
    `agentj notify` (stored, so a remote paired next sees it first), then the one-off registration is removed."""
    try:
        run([str(agentj), 'notify', '--allow', 'agentj-install'])
        for wait in (0, 5, 15):       # serve was (re)started a moment ago; its control socket may not answer yet
            time.sleep(wait)
            try:
                run([str(agentj), 'notify', '--from', 'agentj-install', '--title', 'OpenCode 免费模型 / OpenCode free model',
                     '--text', free_text(model)])
                break
            except InstallError:
                if wait == 15: raise
    except InstallError:
        print('免费模型的提示没发到手机。/ The free-model note did not reach the phone.')
    finally:
        try: run([str(agentj), 'notify', '--forget', 'agentj-install'])
        except InstallError: pass


def prepare_google(agentj, run=command):
    """P115 (P91): pre-install Agent J's pinned Google tool (gog). Optional: a failure or no network never fails the
    installation; the running service retries quietly, and `agentj google off` means the owner won't use it."""
    try:
        res = json.loads(run([str(agentj), 'google', 'install', '--json'], timeout=300) or b'{}')
    except (InstallError, ValueError, TypeError):
        res = {}
    ok = res.get('ok') and res.get('result') in ('installed', 'already', 'disabled')
    print('Google 工具已预装。/ Google tools pre-installed.' if ok else
          'Google 工具稍后在后台自动预装，不影响使用。/ Google tools will be pre-installed later in the background; nothing else is affected.')
    return bool(ok)


# P214: owners paste the whole install command / prompt, a Windows line end or a full-width hyphen into the code
# question. Take the first AJI code out of whatever arrived; nothing else of the pasted text is kept or printed.
_DASHES = {ord(c): '-' for c in '\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d\u30fc'}


def extract_code(text):
    """The first AJI-<32 hex> in `text` (NFKC, any dash, spaces / line breaks inside the code allowed), or ''."""
    s = unicodedata.normalize('NFKC', str(text or '')).translate(_DASHES)
    for t in (s, re.sub(r'\s+', '', s)):
        m = re.search(r'AJI-([0-9a-f]{32})(?![0-9a-f])', t, re.I)
        if m: return 'AJI-' + m.group(1).lower()
    return ''


def link_seat(agentj, code, *, show=None):
    """P220: the AJI installation code is also this seat's setup code (dashboard/src/installer.ts issues both at once), so the
    same code links the computer: no computer binding code, no second code. The code goes by a 0600 file, never argv.
    → True when linked; False (with one line of what to do) otherwise — never an installation failure."""
    show = show or (lambda args: subprocess.run(args, check=True, timeout=180))
    with tempfile.TemporaryDirectory(prefix='agentj-link-') as td:
        f = Path(td) / 'installation-code'
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as h:
            h.write(code)
        try:
            show([str(agentj), 'login', '--install-code-file', str(f)])
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            print('席位还没绑上：运行 agentj login，粘贴账户页「复制设置码」或「复制安装码」给的码（不显示）。'
                  ' / Not linked to a seat yet: run agentj login and paste the code from the account page.')
            return False


ACCOUNT_PAIR = ("首台遥控器：有账号→打开 https://agentj.app/account/，席位卡点「添加遥控器」，核对六位码并批准。"
                "只有席位→请你本人在终端敲 agentj pair，亲自设置并输入遥控器配对密码（至少 8 位）；AI 不得代设、读取或代输。"
                "账号是别人开的→请公司的账号负责人在账号页批准。\n"
                "First remote: own account → open https://agentj.app/account/, Add a remote, compare six digits and approve. "
                "Seat only → type agentj pair yourself in the terminal; set and enter your own remote pairing password "
                "(at least 8 characters); AI must never set, read or type it. Someone else opened the account → ask the account manager to approve. "
                "QR and link are both shown by default. / 默认同时显示二维码和链接。")


def pair_phone(agentj, *, show=None, run=None):
    """The pairing QR code on this screen. A cancelled or expired pairing is not an installation failure.
    P221: offer the optional remote pairing password first (Enter skips); a terminal pairing needs it, so without one the
    first remote is added from the account page (passkey) instead."""
    show = show or (lambda args: subprocess.run(args, check=True))
    print(ACCOUNT_PAIR)
    try:
        show([str(agentj), 'passphrase', 'offer'])
    except (subprocess.CalledProcessError, OSError, KeyboardInterrupt):
        pass
    chk = {}
    if run is not None:
        try:
            chk = json.loads(run([str(agentj), 'pair', '--check', '--json']) or b'{}')
        except (InstallError, ValueError, TypeError, subprocess.CalledProcessError, OSError):
            chk = {}
    if not (isinstance(chk, dict) and chk.get('passphrase_set')):
        return False
    print('用手机扫下面的二维码（Agent J 网页里点「扫二维码」，或手机相机）。截图发给别人前先遮住二维码。'
          ' / Scan the QR code with your phone; cover it before sharing a screenshot.')
    try:
        show([str(agentj), 'pair'])
        return True
    except (subprocess.CalledProcessError, OSError, KeyboardInterrupt):
        print('手机还没配上：随时在这台电脑运行 agentj pair 再扫一次。/ Phone not paired yet: run agentj pair any time.')
        return False


def write_result(home, **facts):
    """P220: what the one-command installer prints at the end comes from here (linked / paired / ai), never assumed."""
    try:
        d = Path(home) / '.local/share/agentj'; d.mkdir(parents=True, exist_ok=True)
        f = d / 'install-result.json'; tmp = d / '.install-result.tmp'
        tmp.write_text(json.dumps(facts)); tmp.chmod(0o600); os.replace(tmp, f)
    except OSError:
        pass


def install(code, uv, working_root, *, post=request, run=command, approve=consent, home=None, prompt=input, pair=None, resume=False,
            one_command=False, ai_later=False, free_model=False, local_model=False):
    static_base = artifact_base()  # Validate before consent, redemption or any external request.
    code = extract_code(code)
    if not code: raise InstallError('invalid_installation_code')
    home = Path(home or Path.home())
    working_root = validate_working_root(working_root, home)
    # Existing installations are preserved. F26 is fresh install only, not an implicit migration.
    agentj = home / '.local/bin/agentj'
    if agentj.exists() and not resume:
        if not approve('保留现有配置，升级 Agent J 并用这张安装码绑定席位？ / Keep existing configuration, upgrade Agent J and link this installation code?'): return False
        with tempfile.TemporaryDirectory(prefix='agentj-link-') as td:
            work=Path(td);work.chmod(0o700)
            wheel_name,wheel_sha=resolve_wheel(static_base)
            wheel=work/wheel_name;download(static_base+'/dl/'+wheel_name,wheel,wheel_sha)
            uv_env=os.environ.copy();uv_env.setdefault('UV_PYTHON_INSTALL_MIRROR','https://registry.npmmirror.com/-/binary/python-build-standalone')
            try: run([uv,'tool','install','--upgrade','--python','3.12.13',str(wheel),*host_install_args()],env=uv_env)
            except (InstallError,subprocess.CalledProcessError):
                uv_env['UV_DEFAULT_INDEX']='https://pypi.tuna.tsinghua.edu.cn/simple'
                run([uv,'tool','install','--upgrade','--python','3.12.13',str(wheel),*host_install_args()],env=uv_env)
            code_file=work/'installation-code';fd=os.open(code_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:f.write(code)
            run([str(agentj),'login','--install-code-file',str(code_file)])
        print('已绑定席位。下一步：在这台电脑运行 agentj pair，用手机扫二维码。 / Linked. Next: run agentj pair here and scan with your phone.')
        return True
    persistent_oc = home / '.local/share/agentj/installation-opencode'
    progress = persistent_oc.parent/'installation-progress.json'
    resuming=False
    if resume and progress.exists():
        if progress.is_symlink() or progress.stat().st_mode & 0o077: raise InstallError('unsafe_recovery_marker')
        marker=json.loads(progress.read_text())
        # Any installer-written release name is fine: an installed host is kept, a missing one gets the current latest.
        if not WHEEL_RE.fullmatch(str(marker.get('version', ''))) or marker.get('working_root') != str(working_root): raise InstallError('recovery_root_or_version_mismatch')
        resuming=True
    if persistent_oc.is_symlink() or persistent_oc.parent.is_symlink(): raise InstallError('opencode_destination_exists')
    if persistent_oc.exists() and not resuming: raise InstallError('opencode_destination_exists')
    if any(os.environ.get(k) for k in ('AGENTJ_STATE_DIR','AGENTJARVIS_STATE_DIR','AGENTJ_SERVICE_NAME','UV_TOOL_BIN_DIR','UV_TOOL_DIR','XDG_BIN_HOME')): raise InstallError('custom_state_requires_existing_install_flow')
    if (agentj.exists() or (home / '.local/state/agentj').exists() or (home / '.config/systemd/user/agentj.service').exists() or (home / 'Library/LaunchAgents/net.agentj.host.plist').exists()) and not resuming: raise InstallError('existing_installation_use_own_agent')
    if not one_command and not approve('安装 OpenCode 和 Agent J，并创建后台用户服务？ / Install OpenCode, Agent J and a user service?'): return False
    # P220: a paid seat's AJI code has ONE redemption, and linking the seat is it (dashboard consumeInstallBinding redeems
    # it). The one-command path therefore never redeems it for the installation assistant's temporary lease — that
    # double redemption is what sent owners to the 8-character computer binding code (Hong Kong, 10-13).
    lease = None if (resuming or one_command) else post('/v1/install/redeem', {'code': code})
    installed = service_started = created_oc = provider_created = False
    provider_id = marker.get("provider_id", "agentj-install-"+uuid.uuid4().hex[:12]) if resuming else "agentj-install-"+uuid.uuid4().hex[:12]
    if not re.fullmatch(r"agentj-install-[a-f0-9]{12}",provider_id): raise InstallError("invalid_recovery_provider")
    persistent_oc = home / '.local/share/agentj/installation-opencode'
    try:
        with tempfile.TemporaryDirectory(prefix='agentj-install-') as td:
            work = Path(td); work.chmod(0o700)
            cfg = {} if lease is None else assistant_config(lease, work)
            (work/'opencode.json').write_text(json.dumps(cfg)); (work/'opencode.json').chmod(0o600)
            key = platform.system() + '-' + platform.machine()
            if key not in LOCK: raise InstallError('unsupported_platform')
            oc_archive = work/'opencode.tgz'; pin = LOCK[key]
            download(runtime_url(key, pin), oc_archive, pin['integrity'].split('-',1)[1], 'sha512')
            oc = work/'opencode'; extract_binary(oc_archive, oc)
            # No customer HOME, auth files, plugins, project or state exposed to this explanatory agent — not even for
            # `--version`, which otherwise creates ~/.local/share/opencode logs in the owner's real HOME (P73 QA).
            child = {'PATH': os.environ.get('PATH',''), 'HOME': str(work), 'XDG_CONFIG_HOME': str(work/'config'),
                     'XDG_DATA_HOME': str(work/'data'), 'XDG_STATE_HOME': str(work/'state'), 'XDG_CACHE_HOME': str(work/'cache'),
                     'OPENCODE_CONFIG': str(work/'opencode.json'), 'OPENCODE_DISABLE_AUTOUPDATE': 'true', 'OPENCODE_DISABLE_MODELS_FETCH': '1', 'TMPDIR': str(work/'tmp')}
            (work/'tmp').mkdir(mode=0o700)   # the runtime unpacks ~5 MB native libraries into TMPDIR; gone with this directory
            try:
                version = run([str(oc), '--version'], cwd=work, env=child).decode().strip()
            except (InstallError, subprocess.CalledProcessError) as exc:
                if platform.system() != 'Darwin': raise
                reason = 'macos_agent_dyld' if str(exc) == 'macos_agent_dyld' else 'macos_agent_unusable'
                print('OpenCode 实际试运行失败；若为 dyld / _ubrk_clone，PATH 或 npm 重装不能修复系统库。请升级 macOS、换电脑，或选择能正常运行的其他 Agent 按手动路线安装。 / OpenCode failed its runtime probe. For dyld / _ubrk_clone, PATH changes or npm reinstalls cannot repair system libraries. Upgrade macOS, use another computer, or use another working agent with the manual installation guide.', file=sys.stderr)
                raise InstallError(reason) from None
            if version != '1.18.34': raise InstallError('runtime_version_mismatch')
            explanation = b'' if lease is None else explain_installation(oc, work, child, run=run)
            for line in explanation.decode(errors='replace').splitlines():
                try: event=json.loads(line)
                except ValueError: continue
                if event.get('type') == 'text':
                    text=event.get('part',{}).get('text','')
                    if isinstance(text,str): print(re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)[:2000])
            wheel_name = marker['version'] if resuming else None
            if not (resuming and agentj.exists()):
                wheel_name, wheel_sha = resolve_wheel(static_base)
                wheel = work/wheel_name; download(static_base+'/dl/'+wheel_name, wheel, wheel_sha)
                uv_env = os.environ.copy()
                uv_env.setdefault('UV_PYTHON_INSTALL_MIRROR', 'https://registry.npmmirror.com/-/binary/python-build-standalone')
                try:
                    run([uv, 'tool', 'install', '--python', '3.12.13', str(wheel), *host_install_args()], env=uv_env)
                except (InstallError, subprocess.CalledProcessError):
                    uv_env['UV_DEFAULT_INDEX'] = 'https://pypi.tuna.tsinghua.edu.cn/simple'
                    run([uv, 'tool', 'install', '--python', '3.12.13', str(wheel), *host_install_args()], env=uv_env)
            installed = True
            persistent_oc.parent.mkdir(parents=True, exist_ok=True)
            if not progress.exists():
                fd=os.open(progress,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w') as f: json.dump({'version':wheel_name,'working_root':str(working_root),'provider_id':provider_id,'formal_metadata':formal_metadata(lease or {})},f)
            # Keep the resume marker on failure; no service/model starts without an effective Linux fence.
            require_linux_isolation(agentj, run)
            if not persistent_oc.exists():
                fd=os.open(persistent_oc,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o700)
                created_oc=True
                with os.fdopen(fd,'wb') as f: f.write(oc.read_bytes())
            run([str(agentj), 'init', '--working-root', str(working_root)])
            if local_model:
                try:
                    run([str(agentj), 'local-model', 'install'], timeout=1800)
                except (InstallError, subprocess.CalledProcessError):
                    print('本地兜底未装好；Agent J 继续安装。需 Apple Silicon 16GB+、20GB 空闲和已运行的 Ollama；之后运行 agentj local-model install。 / Local fallback unavailable; continuing Agent J setup. Retry agentj local-model install after installing and starting Ollama.')
            # Start disconnected; never install temporary key into user's actual OpenCode auth.
            run([str(agentj), 'agent', 'off'])
            # P390: the extra option 「用 OpenCode 自带的免费模型先跑起来」 only when the owner chose it; else unchanged
            selected = (free_harness(agentj, persistent_oc, run=run) if free_model else None) or \
                configured_harness(agentj, run=run, prompt=prompt)
            formal_env = os.environ.copy()
            if selected:
                formal_env['AGENTJ_' + selected['name'].upper() + '_BIN'] = selected['executable']
            else:
                formal_env['AGENTJ_OPENCODE_BIN'] = str(persistent_oc)
            service_started = True
            run([str(agentj), 'service', 'install'], env=formal_env)
            run([str(agentj), 'service', 'status'])
            run([str(agentj), 'doctor'])
            browser_setup(agentj, run=run)          # P114: default on; a failure is an open item, never an install failure
            print('✓ Agent J 已装好，后台服务在运行。/ Agent J is installed and its background service is running.')
            if selected and selected.get('free'):
                free_notice(agentj, selected['model'], run=run)   # before pairing: the phone's first page
            # P220: the same AJI code links the paid seat (no device code, no Stripe detour); an already linked resume keeps it
            linked = paired = True
            if not (resuming and approve('本机已加入 Agent J 账号，保留绑定？ / Host already linked; keep its account binding?')):
                linked = link_seat(agentj, code, show=pair)
                if not linked:
                    if one_command or not sys.stdin.isatty():
                        print('Agent J 已装好，但还没绑定席位：运行 agentj login 后再运行 agentj pair。/ Installed, not linked yet.')
                        write_result(home, linked=False, paired=False, ai=None)
                        progress.unlink()
                        return True
                    (pair or (lambda args: subprocess.run(args, check=False)))([str(agentj), 'login'])
            # Pair UI is local and deliberately visible; it may display a bounded pairing capability.
            if not (resuming and approve('手机已经配对，保留原配对？ / Phone already paired; keep its pairing?')):
                paired = pair_phone(agentj, show=pair, run=run)
            if selected:
                # A fresh installation needs its own live session; a saved login is not a running desktop thread.
                run([str(agentj), 'config', 'set', 'agent.session_mode', 'independent'])
                connect = [str(agentj), 'agent', selected['name'], '--dir', str(working_root)]
                if selected.get('model'):
                    connect += ['--model', selected['model']]
                run(connect, env=formal_env)
                run([str(agentj), 'service', 'restart'], env=formal_env)
                run([str(agentj), 'service', 'status']); run([str(agentj), 'doctor'])
                prepare_google(agentj, run)
                label = {'claude': 'Claude Code', 'codex': 'Codex', 'opencode': 'OpenCode'}.get(selected['name'], selected['name'])
                print('✅ ' + label + ' 已接上，刚才真实回复过一次。去手机上的 Agent J 发一句「你好」。'
                      ' / ' + label + ' is connected and answered a real call. Say hello from your phone.')
                if selected.get('free'):
                    print(free_text(selected['model']))
                write_result(home, linked=linked, paired=paired, ai=selected['name'])
                progress.unlink()
                return True
            if ai_later or one_command:
                # P220: no formal-provider questions in the one-command path; say exactly what is missing and the one fix
                print('Agent J 已装好、手机可以配对，但还没接上能回复的 AI（没登录、key 不对，或模型不可用）。'
                      '接 AI：运行 curl -fsSL https://agentj.app/install.sh | bash（不用带码）选 AI；或 agentj agent claude（codex / opencode）后 agentj service restart。'
                      ' / Installed, but no AI answers yet: rerun the install command to choose one, or run agentj agent claude.')
                write_result(home, linked=linked, paired=paired, ai=None)
                progress.unlink()
                return True
            print('现在请注册正式套餐。不要在终端或对话贴 key。/ Register a formal plan; never paste its key here.')
            lease_metadata=formal_metadata(marker.get('formal_metadata',{})) if resuming else formal_metadata(lease or {})
            if lease_metadata.get('registration_url') == ORIGIN+'/register':
                print(lease_metadata['registration_url'])
                if approve('打开正式套餐注册页？ / Open the formal-plan registration page?'):
                    import webbrowser
                    webbrowser.open(lease_metadata['registration_url'])
            base_default = lease_metadata.get('formal_base_url', '')
            model_default = lease_metadata.get('formal_model', '')
            base = prompt('正式服务 API 地址（不含 key） / Formal API URL ['+base_default+']: ').strip() or base_default
            model = prompt('正式模型名称 / Formal model id ['+model_default+']: ').strip() or model_default
            parsed=urlparse(base)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,120}', model): raise InstallError('invalid_formal_provider')
            if not approve('正式 key 将提交到 '+base+'；模型 '+model+'。继续？ / Confirm formal provider and model?'): raise InstallError('formal_provider_not_confirmed')
            provider_created=True
            provider = json.loads(run([str(agentj), 'provider', 'add', provider_id, '--base-url', base, '--model', model, '--json']))
            # The secret request reads its value solely from a paired phone, verifies it and stores it without returning value.
            secret_command = shlex.split(provider['next'][0]); secret_command[0] = str(agentj)
            run(secret_command)
            run([str(agentj), 'agent', 'opencode', '--dir', str(working_root), '--model', provider_id+'/'+model], env=formal_env)
            run([str(agentj), 'service', 'restart'], env=formal_env)
            run([str(agentj), 'service', 'status']); run([str(agentj), 'doctor'])
            prepare_google(agentj, run)
            print('请在手机发一条测试消息；正式对话使用你自己的 key。/ Send a test message from your phone; conversations now use your own key.')
            progress.unlink()
            return True
    except BaseException as exc:
        # A redeemed code is spent even when this run fails; main() tells the owner so.
        if lease is not None:
            try: exc.code_spent = True
            except AttributeError: pass
        if provider_created:
            try: run([str(agentj), 'provider', 'remove', provider_id])
            except InstallError: pass
        if service_started and not progress.exists():
            try: run([str(agentj), 'service', 'uninstall'])
            except InstallError: pass
        if installed and not progress.exists():
            try: run([uv, 'tool', 'uninstall', 'agentj'])
            except InstallError: pass
        if created_oc and not progress.exists() and persistent_oc.exists(): persistent_oc.unlink()
        # Never delete initialized user state, root files or pairing: preserve for explicit recovery.
        raise
    finally:
        try:
            if lease is not None: post('/v1/install/complete', lease=lease['api_key'])
        except InstallError: print('临时授权回收未确认，将按硬到期失效。/ Revocation unconfirmed; hard expiry remains enforced.')


def upgrade_existing(uv, *, home=None, run=command):
    """P516: repair-forward via today's verified bootstrap, without the old worker or a seat code.
    Pin both uv destinations from the installed tool receipt; never create a parallel installation.
    """
    import tomllib
    if os.environ.get('AGENTJ_FENCED'):
        raise InstallError('upgrade_requires_host_terminal')
    base = artifact_base()
    home = Path(home or Path.home())
    found = shutil.which('agentj') or str(home/'.local/bin/agentj')
    executable = Path(found).resolve()
    prefix = executable.parent.parent
    try:
        receipt = tomllib.loads((prefix/'uv-receipt.toml').read_text())['tool']
        requirements = receipt['requirements']
        entries = [Path(e['install-path']) for e in receipt['entrypoints'] if e.get('name') == 'agentj']
        if (executable != prefix/'bin/agentj' or prefix.name != 'agentj'
                or not requirements or requirements[0].get('name') != 'agentj'
                or len(entries) != 1 or not entries[0].is_absolute()
                or entries[0].resolve() != executable):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        raise InstallError('upgrade_requires_uv_install') from None
    before = run([str(executable), '--version']).decode().strip()
    version_re = r'agentj (\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?'
    def version_key(text):
        match = re.fullmatch(version_re, text)
        if not match: raise InstallError('upgrade_version_invalid')
        major, minor, patch, pre, number = match.groups()
        return (int(major), int(minor), int(patch), {'a':0, 'b':1, 'rc':2, None:3}[pre], int(number or 0))
    old_key = version_key(before)
    with tempfile.TemporaryDirectory(prefix='agentj-upgrade-') as td:
        work = Path(td); work.chmod(0o700)
        name, sha = resolve_wheel(base)
        target = 'agentj '+name[len('agentj-'):-len('-py3-none-any.whl')]
        if version_key(target) < old_key: raise InstallError('upgrade_downgrade_refused')
        wheel = work/name
        download(base+'/dl/'+name, wheel, sha)
        verify_wheel(wheel.read_bytes(), fetch(base+'/dl/'+name+'.minisig', MAX_SIG), name)
        # The bootstrap uv lives in a temporary directory. Retain these already hash-verified bytes
        # so service install captures a PATH that remains valid after the bootstrap exits.
        directory = home/'.local/share/agentj/upgrade-bin'
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise InstallError('upgrade_runtime_symlink')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        persistent = directory/'uv'
        if persistent.is_symlink(): raise InstallError('upgrade_runtime_symlink')
        with tempfile.NamedTemporaryFile(prefix='.uv-', dir=directory, delete=False) as out:
            staging = Path(out.name)
            try:
                out.write(Path(uv).read_bytes()); out.flush()
                staging.chmod(0o700); os.replace(staging, persistent)
            finally:
                staging.unlink(missing_ok=True)
        env = {**os.environ, 'UV_TOOL_DIR': str(prefix.parent), 'UV_TOOL_BIN_DIR': str(entries[0].parent),
               'PATH': os.pathsep.join((str(directory), str(entries[0].parent), os.environ.get('PATH', '')))}
        python = os.path.realpath(getattr(sys, '_base_executable', sys.executable))
        run([str(persistent), 'tool', 'install', '--force', '--python', python, str(wheel), *host_install_args()], env=env)
        if run([str(executable), '--version'], env=env).decode().strip() != target:
            raise InstallError('upgrade_version_mismatch')
        run([str(executable), 'service', 'install'], env=env)
        run([str(executable), 'service', 'restart'], env=env)
        run([str(executable), 'doctor', '--upgrade-only', '--json'], env=env)
    print(before+' → '+target+'; 配置和绑定已保留。/ Configuration and pairing preserved.')
    return True


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description='Local installation assistant; no remote shell')
    p.add_argument('--upgrade-only', action='store_true', help='Repair an existing uv installation; preserve its seat and pairing')
    p.add_argument('code', nargs='?'); p.add_argument('--code-file', default=os.environ.get('AGENTJ_CODE_FILE'), help=argparse.SUPPRESS)
    p.add_argument('--one-command', action='store_true', help='Run by https://agentj.app/install.sh: no duplicate questions')
    p.add_argument('--ai-later', action='store_true', help='The owner chose to connect an AI later')
    p.add_argument('--local-model', action='store_true', help='Optional Ollama qwen3.5:9b fallback (Apple Silicon 16GB+)')
    p.add_argument('--free-model', action='store_true', help="The owner chose OpenCode's own free model to start with (P390)"); p.add_argument('--resume', action='store_true', help='Resume only this installer-owned partial installation; preserve pairing and credentials'); p.add_argument('--uv', default=shutil.which('uv')); p.add_argument('--working-root', type=Path)
    a=p.parse_args(argv)
    try:
        if not a.uv: raise InstallError('verified_uv_required')
        if a.upgrade_only:
            upgrade_existing(a.uv)
            return 0
        if not a.code and a.code_file:
            a.code = read_code_file(a.code_file)
        if not a.code: raise InstallError('invalid_installation_code')
        root=a.working_root or Path(input('主工作目录 / Working root (leave empty for ~/coding): ').strip() or str(Path.home()/'coding')).expanduser()
        recovery=Path.home()/'.local/share/agentj/installation-progress.json'
        resume=a.resume
        if recovery.exists() and not resume:
            resume=consent('发现未完成的本安装器任务，继续并保留数据？ / Resume this installer-owned incomplete installation and preserve data?')
        if resume and recovery.exists() and not recovery.is_symlink():
            root=Path(json.loads(recovery.read_text())['working_root'])
        install(a.code, a.uv, root, resume=resume, one_command=a.one_command, ai_later=a.ai_later, free_model=a.free_model, local_model=a.local_model)
    except (InstallError, KeyboardInterrupt, subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError) as exc:
        if a.upgrade_only:
            reason = exc.reason if isinstance(exc, InstallError) else type(exc).__name__
            print('升级修复未完成 / Upgrade repair incomplete: '+reason, file=sys.stderr)
            print('请核对 agentj --version 和 agentj doctor；保留配置，不要解绑。 / Check agentj --version and agentj doctor; preserve configuration and pairing.', file=sys.stderr)
        else:
            print(failure_message(exc, (Path.home()/'.local/share/agentj/installation-progress.json').exists()), file=sys.stderr)
        return 1
    return 0

def read_code_file(path):
    """install.sh hands the code over in a 0600 file it deletes afterwards; read it once, remove it, never print it."""
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077: raise InstallError('unsafe_code_file')
    text = p.read_text(errors='replace')[:4096]
    try: p.unlink()
    except OSError: pass
    return extract_code(text)


# P220: one line of what to do, in plain words, for the reasons an owner can fix (the token stays for support).
NEXT_STEP = {
    'linux_isolation_not_ready': '装上隔离组件：sudo apt-get install -y bubblewrap socat（Ubuntu 24.04 还要看 agentj doctor 的 AppArmor 一行），再重跑同一条命令',
    'existing_installation_use_own_agent': '这台电脑已装过 Agent J：运行 agentj login 绑定席位，再运行 agentj pair 配手机',
    'assistant_request_failed': '连不上 Agent J 的安装服务：检查网络（或代理）后重跑同一条命令',
    'local_step_failed': '本机某一步没成功：运行 ~/.local/bin/agentj doctor 看是哪一项，修好后重跑同一条命令',
    'unsupported_platform': '只支持 Linux / macOS（x64、arm64）；Windows 请用 https://agentj.app/install.ps1',
    # P328: already retried 3 times; a 404 that outlives that is almost always a release still propagating
    'artifact_http_404': '安装文件暂时下载不到（可能刚好赶上新版本发布，服务器还在同步）：等 2 分钟后重跑同一条命令；仍不行请到 https://agentj.app/contact/ 告诉我们',
    'artifact_http_5': 'agentj.app 的下载服务暂时出错：过几分钟重跑同一条命令',
}


def failure_message(exc, resumable):
    """Name the exact reason (fixed tokens only: never the code, lease or key) and the one next step."""
    reason = exc.reason if isinstance(exc, InstallError) else type(exc).__name__
    lines = ['安装未完成。原因：' + reason + ' / Installation incomplete. Reason: ' + reason]
    hint = NEXT_STEP.get(reason) or next((v for k, v in NEXT_STEP.items() if reason.startswith(k)), None) or \
        ('下载失败：检查网络后重跑同一条命令' if reason.startswith(('artifact_', 'integrity_failed')) else None)
    if hint:
        lines.append('怎么办：' + hint)
    if reason == 'invalid_installation_code':
        lines.append('请只粘贴 AJI- 开头的那串码：在账户页 https://agentj.app/account/ 点「复制安装码」。ajt_ 开头的是设置码，只给已经装好的电脑绑定席位用。'
                     ' / Paste only the code that starts with AJI-: on https://agentj.app/account/ choose Copy installation code. A code starting with ajt_ is a setup code; it only links a computer that already has Agent J.')
    elif reason == 'code_unavailable':
        lines.append('这张安装码已用掉或已失效（每张码只能兑换一次，有效期有限）。请重新获取一张新码：付费用户在账户页 https://agentj.app/account/ 取，朋友请向发码的人再要一张。'
                     ' / This installation code was already used or has expired (one redemption per code). Get a new one: paid users from https://agentj.app/account/, friends from whoever sent the code.')
    elif resumable:
        lines.append('已保留本机进度。再次运行原安装命令即可继续，续装不再消耗临时额度。 / Progress is saved; rerun the original command to resume without another allowance.')
    elif getattr(exc, 'code_spent', False):
        lines.append('临时授权已尝试回收；这张码已兑换过，不能再用。修好上面的原因后，请重新获取一张新安装码再运行。 / Temporary authorization revocation attempted. This code has been redeemed and cannot be reused; fix the reason above, then get a new installation code.')
    else:
        lines.append('安装码尚未使用，修好上面的原因后可以用同一条命令重试。 / The installation code was not used; fix the reason above and rerun the same command.')
    return '\n'.join(lines)
if __name__ == '__main__': sys.exit(main())
