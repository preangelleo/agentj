"""Stateless OpenAI Responses wire codec for the native Harness (P429 C9).

No SDK, remote conversation id or API-side storage. Session messages retain opaque
reasoning items locally, bound to the exact provider/model that returned them.
"""
from __future__ import annotations

import json


def inputs(messages, preset):
    out = []
    for m in messages:
        role = m.get('role')
        if role == 'assistant' and m.get('_response_model') == preset.name and m.get('_response_items'):
            out.extend(m['_response_items'])
            continue
        if role == 'tool':
            out.append({'type': 'function_call_output', 'call_id': m['tool_call_id'], 'output': m.get('content') or ''})
            continue
        if role not in ('system', 'developer', 'user', 'assistant'):
            continue
        if m.get('content'):
            out.append({'role': role, 'content': m['content']})
        if role == 'assistant':
            for c in m.get('tool_calls') or []:
                out.append({'type': 'function_call', 'call_id': c['id'], 'name': c['function']['name'],
                            'arguments': c['function']['arguments']})
    return out


def request(preset, messages, tools, max_tokens):
    body = {'model': preset.model, 'input': inputs(messages, preset), 'store': False,
            'include': ['reasoning.encrypted_content']}
    if tools:
        body['tools'] = [{'type': 'function', **t['function'], 'strict': False} for t in tools]
    if max_tokens:
        body['max_output_tokens'] = max_tokens
    return body


def parse(data, preset):
    from .llm import LLMError
    try:
        if not isinstance(data, dict) or data.get('status') not in ('completed',):
            # Never execute a tool from a truncated/in-progress/failed response.
            raise ValueError('not completed')
        items = data['output']
        if not isinstance(items, list):
            raise ValueError('output is not a list')
        text, calls, retained, ids = [], [], [], set()
        for item in items:
            kind = item['type']
            if kind == 'function_call':
                if item.get('status', 'completed') != 'completed':
                    raise ValueError('unfinished call')
                cid, name, args = item['call_id'], item['name'], item['arguments']
                if not all(isinstance(x, str) and x for x in (cid, name, args)) or cid in ids:
                    raise ValueError('bad call')
                if not isinstance(json.loads(args), dict):
                    raise ValueError('tool arguments must be an object')
                ids.add(cid)
                calls.append({'id': cid, 'name': name, 'arguments': args})
                retained.append({k: item[k] for k in ('type', 'id', 'call_id', 'name', 'arguments', 'status') if k in item})
            elif kind == 'message':
                if item.get('role') != 'assistant' or item.get('status', 'completed') != 'completed':
                    raise ValueError('bad message')
                content = []
                for c in item['content']:
                    if c['type'] == 'output_text':
                        if not isinstance(c['text'], str):
                            raise ValueError('text required')
                        text.append(c['text'])
                        content.append({'type': 'output_text', 'text': c['text'], 'annotations': []})
                    elif c['type'] == 'refusal':
                        if not isinstance(c['refusal'], str):
                            raise ValueError('refusal text required')
                        text.append(c['refusal'])
                        content.append({'type': 'refusal', 'refusal': c['refusal']})
                    else:
                        raise ValueError('unsupported content')
                retained.append({'type': 'message', 'role': 'assistant', 'content': content})
            elif kind == 'reasoning':
                # Opaque ciphertext and summaries only: no raw chain of thought requested or displayed.
                summary = item.get('summary') or []
                if not isinstance(summary, list) or not all(isinstance(s, dict) and s.get('type') == 'summary_text'
                        and isinstance(s.get('text'), str) for s in summary):
                    raise ValueError('bad reasoning summary')
                r = {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': s['text']} for s in summary]}
                for k in ('id', 'encrypted_content'):
                    if item.get(k) is not None:
                        if not isinstance(item[k], str):
                            raise ValueError('bad reasoning item')
                        r[k] = item[k]
                retained.append(r)
            else:
                # Built-in hosted tools are not enabled: accepting them would bypass the local gate.
                raise ValueError('unsupported output item')
        usage = data.get('usage') or {}
        counts = [usage.get('input_tokens', 0), usage.get('output_tokens', 0)]
        if not all(type(n) is int and n >= 0 for n in counts):
            raise ValueError('bad usage')
        return {'text': ''.join(text), 'tool_calls': calls, 'finish': 'completed', 'reasoning': None,
                'model': preset.name, 'usage': dict(zip(('input', 'output'), counts)), 'response_items': retained}
    except (KeyError, TypeError, ValueError, IndexError, RecursionError):
        raise LLMError('bad_reply', 200, 'invalid or incomplete Responses output') from None
