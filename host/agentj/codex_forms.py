"""Bounded MCP forms over the existing paired, signed question protocol. No secrets in form replies."""
import asyncio
import json
import math
import re
from . import approvals, privacy
from .policy_registry import harness_tool

FORM_MODES = ('form', 'openai/form', 'openaiForm')
SECRET = re.compile(r'password|passwd|secret|token|api.?key|credential|密钥|密码|口令', re.I)


class Unsupported(ValueError):
    pass


def card_for(p):
    schema = p.get('requestedSchema')
    if p.get('mode', 'form') not in FORM_MODES or not isinstance(schema, dict) or schema.get('type') != 'object':
        raise Unsupported('mode')
    props = schema.get('properties')
    if not isinstance(props, dict) or not 1 <= len(props) <= 4:
        raise Unsupported('fields')
    if len(json.dumps(schema, ensure_ascii=False)) > 8000:
        raise Unsupported('size')
    message = p.get('message')
    if not isinstance(message, str):
        raise Unsupported('message')
    if SECRET.search(message):
        raise Unsupported('secret')
    raw = []
    for key, f in props.items():
        if not isinstance(f, dict) or not isinstance(key, str):
            raise Unsupported('shape')
        text = f"{key}: {f.get('title') or key}"
        if f.get('description'):
            text += '\n' + str(f['description'])
        if SECRET.search(key + ' ' + text) or f.get('format') == 'password' or f.get('writeOnly') is True:
            raise Unsupported('secret')
        if f.get('type') not in ('string', 'boolean', 'integer', 'number'):
            raise Unsupported('type')
        # Unknown constraints are never silently dropped.
        if set(f) - {'type', 'title', 'description', 'default', 'enum', 'enumNames', 'oneOf', 'minLength', 'maxLength', 'minimum', 'maximum'}:
            raise Unsupported('constraint')
        for constraint in ('minLength', 'maxLength', 'minimum', 'maximum'):
            if constraint in f and type(f[constraint]) not in (int, float):
                raise Unsupported('constraint')
        values = options(f)
        if 'oneOf' in f and (not isinstance(f['oneOf'], list) or len(values) != len(f['oneOf'])):
            raise Unsupported('enum')
        opts = [{'label': str(v), 'description': ''} for v in values] if values else [{'label': '…', 'description': '请在下方填写 / Enter your answer below'}]
        raw.append({'question': message + '\n' + text, 'header': str(p.get('serverName', 'MCP')), 'options': opts})
    card = approvals.norm_questions(raw)
    if card is None:
        raise Unsupported('bounds')
    return card


def options(f):
    if f.get('type') == 'boolean':
        return [True, False]
    if isinstance(f.get('enum'), list):
        return f['enum']
    if isinstance(f.get('oneOf'), list):
        return [v['const'] for v in f['oneOf'] if isinstance(v, dict) and isinstance(v.get('const'), str)]
    return []


def content_for(p, picks, card):
    if not approvals.check_picks(card, picks):
        raise Unsupported('answer')
    out = {}
    for (key, f), pick in zip(p['requestedSchema']['properties'].items(), picks):
        vals = options(f)
        value = pick if isinstance(pick, str) else vals[pick[0] - 1] if vals else None
        typ = f['type']
        if typ == 'boolean' and isinstance(value, str):
            value = {'true': True, 'false': False}.get(value.lower())
        elif typ in ('integer', 'number') and isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                raise Unsupported('number') from None
        if typ == 'string':
            if not isinstance(value, str) or not f.get('minLength', 0) <= len(value) <= f.get('maxLength', 2000):
                raise Unsupported('length')
            if privacy.owner_text(value) != value:
                raise Unsupported('secret')
        elif typ == 'boolean':
            if type(value) is not bool:
                raise Unsupported('boolean')
        elif type(value) not in (int, float) or not math.isfinite(value) or (typ == 'integer' and type(value) is not int):
            raise Unsupported('number')
        if typ in ('integer', 'number') and not f.get('minimum', -math.inf) <= value <= f.get('maximum', math.inf):
            raise Unsupported('range')
        if vals and value not in vals:
            raise Unsupported('enum')
        out[key] = value
    return out


def notice(lang, why):
    if why == 'secret':
        return ('这张表需要密钥，不能通过普通表单或对话提交。请让 Agent 用 `agentj secret request` 发密钥卡，写入明确的本机配置后再重试；密钥不会作为 MCP 表单答案发送。' if lang != 'en' else
                'This form asks for a secret. Ask the Agent to open an `agentj secret request` card with an explicit local configuration destination, then retry. A secret is never returned as an MCP form answer.')
    if why == 'openai/userVerification':
        return ('Codex 要求它自己的设备身份验证，手机审批不能代替。请在 Agent J 主机上打开 Codex 完成验证后重试。' if lang != 'en' else
                'Codex requires its own device verification; phone approval cannot replace that proof. Open Codex on the Agent J host, complete verification and retry.')
    return ('Codex 的这张表暂不支持或答案不符合要求，未提交。请在 Agent J 主机上用 Codex 处理，或请 Agent 改用简单问题。' if lang != 'en' else
            'This Codex form is unsupported or an answer did not meet its requirements; nothing was submitted. Handle it in Codex on the Agent J host, or ask the Agent to use a simple question.')


async def answer(agent, rid, p, card, *, approval=None):
    gone = asyncio.get_running_loop().create_future()
    agent.pending[rid] = gone
    result = {'action': 'decline'}
    try:
        if approval is not None:
            tool, args, _item, bind = approval
            decision = await agent.host.ask(harness_tool(tool), args, gone, batch=False, **({'bind': bind} if bind else {}))
            if decision.get('behavior') != 'allow':
                return
        outcome, picks = await agent.host.question(card, gone, task=getattr(agent.host, 'task_label', None))
        if outcome == 'answer' and picks:
            result = {'action': 'accept', 'content': content_for(p, picks, card)}
        elif outcome == 'cancel':
            result = {'action': 'cancel'}
    except Unsupported as e:
        agent.local_only(notice(getattr(agent.host, 'lang', 'zh'), str(e)))
    except (TypeError, ValueError, KeyError, IndexError):
        agent.local_only(notice(getattr(agent.host, 'lang', 'zh'), 'shape'))
    finally:
        agent.pending.pop(rid, None)
        if not gone.done():
            agent._answer(rid, result)
