"""Fixed-language results shown on the existing main reply page."""
import os
import re

from . import privacy
from .opencode_binary import SYSTEM
from .opencode_paths import NATIVE_NPM_PATH

_NUMBER = r'[0-9]{1,6}'
_TRIPLE = rf'{_NUMBER}\.{_NUMBER}\.{_NUMBER}'
VERSION = re.compile(rf'v?{_NUMBER}(?:\.{_NUMBER}){{1,3}}(?:(?:a|b|rc){_NUMBER}|-(?:alpha|beta|rc)\.?{_NUMBER})?')
_SYSTEM_PREFIXES = tuple(os.path.dirname(directory) + '/' for directory in SYSTEM)
_CELLAR = rf'Cellar/opencode/{_TRIPLE}(?:_{_NUMBER})?/bin/opencode'
_PUBLIC_INSTALL = re.compile(rf'(?:bin/opencode|{re.escape(NATIVE_NPM_PATH)}|{_CELLAR})')
_SOURCES = {'mise','asdf','native','npm','path','explicit','system','external'}

# Home paths are private. Only known OpenCode installation layouts can be shown
# relative to the owner's home; the same representation can pass the next projection.
_HOME_INSTALL = re.compile(
    r'(?:\.(?:opencode|local)/bin/opencode'
    rf'|(?:\.local/share/mise|\.asdf)/installs/opencode/v?{_TRIPLE}/(?:bin/)?opencode'
    rf'|\.(?:local|npm-global)/(?:{re.escape(NATIVE_NPM_PATH)}|{_CELLAR}))'
)


def _path(value):
    if not isinstance(value, str) or len(value.encode('utf-8','replace')) > 512:
        return None
    # The fixed system prefix and finite package grammar contain no username,
    # arbitrary filename or caller-controlled free text. This includes Linuxbrew.
    for prefix in _SYSTEM_PREFIXES:
        if value.startswith(prefix) and _PUBLIC_INSTALL.fullmatch(value[len(prefix):]):
            return value
    home = os.path.expanduser('~').rstrip('/')
    if value.startswith('~/'):
        relative = value[2:]
    elif home and value.startswith(home + '/'):
        relative = value[len(home)+1:]
    else:
        return None
    if not _HOME_INSTALL.fullmatch(relative):
        return None
    display = '~/' + relative
    return display if privacy.redact(display) == display else None


def binary_meta(rec):
    result = {}
    executable = _path(rec.get('executable'))
    if executable:
        result['executable'] = executable
    source = rec.get('executable_source')
    if isinstance(source,str) and source in _SOURCES:
        result['executable_source'] = source
    rows = rec.get('binary_candidates')
    if isinstance(rows,list):
        candidates = []
        for row in rows[:16]:
            if not isinstance(row,dict):
                continue
            version, source, why = (row.get(k) for k in ('version','source','reason'))
            if not (version is None and why == 'version_unavailable') and (not isinstance(version,str) or not VERSION.fullmatch(version)):
                continue
            if (not isinstance(source,str) or source not in _SOURCES or not isinstance(why,str)
                    or why not in {'selected','older_version','native_timeout','probe_model_unconfirmed','native_failed','not_attempted','version_unavailable','binary_changed','start_failed','unsupported_version','native_spawn_failed','empty_probe_reply','provider_key','fence_unavailable','provider_unavailable','quota_exhausted','rate_limited','ark_route_conflict','model_missing'}):
                continue
            path = _path(row.get('path'))
            if path or version:
                projected = {'version':version,'source':source,'reason':why}
                if path:
                    projected['path'] = path
                candidates.append(projected)
        if candidates:
            result['binary_candidates'] = candidates
    return result


def binaries(rec,lang):
    if rec.get('target') != 'opencode':
        return ''
    meta = binary_meta(rec)
    path, version = meta.get('executable'), rec.get('version')
    en = lang == 'en'
    lines = []
    used = None
    candidates = meta.get('binary_candidates', [])
    if isinstance(version,str) and VERSION.fullmatch(version):
        used = f'OpenCode {version}' + (f' · {path}' if path else '')
        newer = any(row['reason'] == 'older_version' for row in candidates)
        label = ('Newer version selected · ' if en else '已选用新版 · ') if newer else ('Program used · ' if en else '实际使用 · ')
        lines.append(label + used)
    for row in candidates:
        candidate_path = row.get('path')
        if (path and candidate_path == path) or row['reason'] == 'selected':
            continue
        other = ' · '.join(value for value in (f"OpenCode {row['version']}" if row['version'] else '', candidate_path) if value)
        why = row['reason']
        if why == 'native_timeout':
            numeric = r'\d+\.\d+\.\d+'
            older = (used and re.fullmatch(numeric, version) and re.fullmatch(numeric, row['version'])
                     and tuple(map(int,row['version'].split('.'))) < tuple(map(int,version.split('.'))))
            label = ('Older version · ' if en else '较旧版本 · ') if older else ''
            lines.append(label + (f'{other} timed out during the model check.' if en else f'{other} 在实际模型检查中超时。'))
            if used and rec.get('status') in ('ok','probe_ok'):
                lines.append(f'The check passed with {used}.' if en else f'已改用通过检查的 {used}。')
        elif why == 'probe_model_unconfirmed':
            lines.append(f'{other} did not confirm the selected model.' if en else f'{other} 未能确认回复来自所选模型。')
        elif why == 'older_version':
            lines.append(f'Older installation · {other}' if en else f'另有较旧版本 · {other}')
        elif why == 'version_unavailable':
            lines.append(f'Could not read the version of {other}.' if en else f'无法读出 {other} 的版本。')
        elif why == 'unsupported_version':
            lines.append(f'Unsupported installation · {other}' if en else f'暂不支持该版本 · {other}')
        elif why in ('native_spawn_failed','native_failed','start_failed','empty_probe_reply','binary_changed'):
            lines.append(f'{other} did not complete its check.' if en else f'{other} 未完成本次检查。')
    return ('\n' + '\n'.join(lines)) if lines else ''


PLAIN = {
 'native_timeout': ('{x} 在模型检查的时限内没有返回回复。','{x} did not reply before the model check timed out.'),
 'start_timeout': ('{x} 的会话在启动时限内没有就绪。','The {x} session did not become ready before the startup deadline.'),
 'version_unavailable': ('找到 OpenCode，但无法读出它的版本。','OpenCode was found, but its version could not be read.'),
 'no_trusted_binary': ('主机未找到来源可核验的 OpenCode 程序。','No verifiable OpenCode installation was found on this host.'),
 'unsupported_version': ('已安装的 OpenCode 版本暂不受支持。','The installed OpenCode version is unsupported.'),
 'binary_changed': ('检查过的 OpenCode 程序发生了变化。','The checked OpenCode program changed.'),
 'fence_unavailable': ('主机暂时无法隔离模型检查所需的运行环境。','The host could not isolate the environment needed for the model check.'),
 'not_logged_in': ('{x} 的后台登录不可用。','The background service has no usable sign-in for {x}.'),
 'auth': ('{x} 的账号或密钥未通过服务验证。','The service rejected the current {x} credentials.'),
 'ambiguous_install': ('设备上有多份 {x}，未能选定可用程序。','Agent J could not choose a usable {x} installation.'),
 'no_service': ('掌枢后台服务未运行，无法完成切换所需的重启。','The Agent J service is unavailable for the required restart.'),
 'rollback_failed': ('原配置已恢复，但原来的 AI 服务尚未恢复。','The previous configuration was restored, but the previous AI has not recovered.'),
 'owner_unreachable': ('设置指定的外部 {x} 服务器无法连接。','The external {x} server in your settings is unreachable.'),
 'owner_password': ('设置指定的外部 {x} 服务器需要密码。','The external {x} server requires a password.'),
 'owner_session': ('指定的会话在外部 {x} 服务器上不可用。','The selected session is unavailable on the external {x} server.'),
 'free_tier': ('{x} 的免费通道暂不可用。','The {x} free tier is unavailable.'),
 'unknown_model': ('{x} 未提供所选模型。','{x} does not offer the selected model.'),
 'model_missing': ('{x} 找不到所选模型。','{x} could not find the selected model.'),
 'first_device': ('首次连接还没有唯一可核验的配对手机。','First connection requires one identifiable paired phone.'),
 'not_installed': ('主机未找到 {x}。','{x} was not found on this host.'),
 'stale_job': ('这次切换请求已过期。','This switch request has expired.'),
 'too_soon': ('刚检查过可用程序。','Agent J just checked the installed programs.'),
 'probe_pending': ('后台仍在核验目标程序。','Agent J is still checking the target program.'),
}

# Every action is available through the product, including recovery using the current chat.
ACTIONS = [
 (('not_logged_in','auth','provider_key','owner_provider_key','owner_password'),
  '在手机当前对话请掌枢帮你重新连接提供商；需要密钥时填写手机密钥卡。','Ask Agent J in this chat to reconnect the provider. Use the phone key card if a key is needed.'),
 (('bad_model','model_missing','unknown_model','provider_unavailable','invalid_provider','access','region','owner_model_unconfirmed','free_tier','native_tool_refused','probe_model_unconfirmed','ark_route_conflict'),
  '在「模型和 API Key」核对提供商及模型，再从当前对话请求切换。','Check the provider and model under Models & API keys, then request the switch in this chat.'),
 (('first_device','first_pair'), '在掌枢完成手机配对，再从配对手机选择目标模型。','Pair the phone in Agent J, then select the model on that phone.'),
 (('rate_limited','busy','too_soon','start_timeout','native_timeout','timeout','not_idle','probe_pending'),
  '稍后在当前对话说「检查切换结果」；若仍未切换，再请求重试。','Ask for the switch result in this chat shortly. Retry if the switch has not completed.'),
 (('plan',), '核对账号的套餐和组织权限，恢复后回到当前对话再请求切换。','Check your account plan and organization permissions, then request the switch again in this chat.'),
 (('native_cli_unsupported',), '在 Agent J 主机更新 Claude Code 后再请求切换。','Update Claude Code on your Agent J host, then request the switch again.'),
 (('quota_exhausted','not_supported','bad_target'), '在当前对话选择另一款可用模型或 Agent。','Select another available model or agent in this chat.'),
 (('network','not_running','no_service','restart_failed','rollback_failed'), '在手机「紧急修复」检查主机连接；恢复后再请求切换。','Check the host connection in Emergency repair on your phone. Request the switch again once connected.'),
 (('ambiguous_install','native_executable_selection_failed','native_executable_not_executable','native_spawn_failed','native_executable_missing','not_installed','version_unavailable','no_trusted_binary','unsupported_version','binary_changed'),
  '在当前对话说「检查可用安装并帮我连接」。','Ask Agent J in this chat to check the installed programs and connect a usable one.'),
 (('owner_unreachable','owner_session'), '在当前对话请掌枢检查外部服务器连接，或改用掌枢托管的会话。','Ask Agent J to check the external server or switch to a session managed by Agent J.'),
 (('same','nothing_to_undo'), '在当前对话继续使用现有 Agent，或选择其他目标。','Continue with your current agent in this chat, or choose another target.'),
 (('estop',), '请在已配对手机解除急停后，再请求切换。','Release the emergency stop on your paired phone, then request the switch again.'),
]
DEFAULT_ACTION = ('在当前对话说「检查这次切换并重试」；若仍失败，可请掌枢发送反馈给客服。',
                  'Ask Agent J in this chat to check this switch and retry. If it fails again, ask it to send feedback to support.')
DETAIL = {
 'starting':('会话还在启动，正在加载 MCP 或插件。','The session is still starting and loading MCP servers or plugins.'),
 'prompt':('启动时停在交互确认提示。','Startup is waiting for an interactive prompt.'),
 'approval':('正在等待手机批准卡，请先处理卡片。','A phone approval is pending. Respond to that card first.'),
 'failed':('会话未能正常回复。','The session did not complete its reply.'),
 'turn':('会话已打开但尚未回答，请检查网络或代理。','The session opened without a reply. Check the network or proxy.'),
 'idle':('会话没有接到自我介绍请求。','The session did not accept its introduction request.'),
 'empty':('会话已结束，但没有返回文字。','The session ended without returning text.'),
 'lost':('主机状态暂时无法确认。','The host status is temporarily unavailable.'),
}


def reason(code, target, lang, reasons, labels, detail=None, err=None, errors=None):
    en = lang == 'en'
    default = reasons['call_failed']
    if code == 'model_missing' and target == 'claude':
        phrase = ('{x} 找不到所选模型，或当前账号无权使用。', '{x} could not find the selected model or this account cannot access it.')[en]
    elif code in PLAIN:
        phrase = PLAIN[code][en]
    else:
        phrase = reasons.get(code, default)
    text = phrase.format(x=labels.get(target, target) or ('the requested agent' if en else '目标 Agent')).rstrip('。.')
    if detail in DETAIL:
        text += ('; ' if en else '。') + DETAIL[detail][en]
    if errors and err in errors:
        text += ' · ' + (('sign-in expired' if en else '登录失效') if err == 'auth' else errors[err])
    if err == 'auth':
        code = 'not_logged_in'
    if target == 'claude' and code == 'not_logged_in':
        action = ('在已配对的手机上打开「设置 → 紧急修复 → AI 登录救援」，选 Claude Code 重新登录，再回到对话请它切换。',
                  'On your paired phone, open Settings → Emergency repair → AI login rescue, sign in to Claude Code, then request the switch in this chat.')[en]
    elif target == 'claude' and code == 'auth':
        action = ('用订阅登录时，到手机「设置 → 紧急修复 → AI 登录救援」重登。用自配的服务商时，在「模型和 API Key」核对该服务商。',
                  'For a subscription sign-in, use Settings → Emergency repair → AI login rescue. For a custom provider, check it in Models & API keys.')[en]
    elif target == 'claude' and code == 'network':
        action = ('检查 Agent J 主机访问 Claude 服务的网络和代理，恢复后再请求切换。',
                  'Check the network and proxy from your Agent J host to Claude, then request the switch again.')[en]
    else:
        action = next(((zh,eng)[en] for codes,zh,eng in ACTIONS if code in codes),DEFAULT_ACTION[en])
    return text.rstrip('。.') + ('. ' if en else '。') + action
