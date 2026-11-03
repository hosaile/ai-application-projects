# -*- coding: utf-8 -*-
"""统一模型调用层：OpenAI Chat Completions / Anthropic Messages。

能力：
- 自定义 Base URL / Key / 请求头（OpenCode Go 自动补 x-opencode-session）
- 推理等级参数映射（extra_body）
- 不接受 temperature / response_format / max_tokens 时自动回退重试
- 本机地址绕过系统代理
- 统一 usage 结构：prompt_tokens / completion_tokens / total_tokens
"""
import json
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

_OPENCODE_SESSION = 'workbench-' + uuid.uuid4().hex


def _usage_dict(prompt_tokens=0, completion_tokens=0):
    prompt_tokens = int(prompt_tokens or 0)
    completion_tokens = int(completion_tokens or 0)
    return {
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'total_tokens': prompt_tokens + completion_tokens,
    }


def _is_local_base(base_url):
    host = (urlparse(str(base_url or '')).hostname or '').lower()
    return host in ('localhost', '127.0.0.1', '0.0.0.0', '::1')


def request_headers(llm, base_url):
    """自定义请求头；OpenCode Go 必须带 x-opencode-session。"""
    headers = dict(llm.get('headers') or {})
    if 'opencode.ai' in str(base_url or '').lower():
        if not any(k.lower() == 'x-opencode-session' for k in headers):
            headers['x-opencode-session'] = _OPENCODE_SESSION
    return headers


def _normalize_messages(messages):
    out = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get('role') or 'user').lower()
        if role not in ('system', 'user', 'assistant'):
            role = 'user'
        content = item.get('content')
        if isinstance(content, list):
            content = ''.join(
                str(p.get('text', '')) for p in content
                if isinstance(p, dict) and p.get('type') == 'text')
        content = str(content or '')
        if content:
            out.append({'role': role, 'content': content})
    return out


def _split_system(messages, system):
    msgs = _normalize_messages(messages)
    if system:
        system_text = str(system)
        rest = [m for m in msgs if m['role'] != 'system']
        return system_text, rest
    systems = [m['content'] for m in msgs if m['role'] == 'system']
    rest = [m for m in msgs if m['role'] != 'system']
    return '\n\n'.join(systems), rest


def _openai_chat(llm, messages, max_tokens=None, json_mode=True, system=None):
    from openai import OpenAI

    base_url = (llm.get('base_url') or '').strip()
    if not base_url:
        raise RuntimeError('未配置 Base URL')
    timeout = float(llm.get('timeout', 60) or 60)
    headers = request_headers(llm, base_url)
    client_kwargs = {
        'api_key': llm.get('api_key') or 'not-needed',
        'base_url': base_url,
        'timeout': timeout,
    }
    if headers:
        client_kwargs['default_headers'] = headers
    if _is_local_base(base_url):
        # 本机 mock / Ollama 不能被系统代理劫持
        import httpx
        client_kwargs['http_client'] = httpx.Client(trust_env=False, timeout=timeout)
    client = OpenAI(**client_kwargs)

    system_text, rest = _split_system(messages, system)
    use_system = bool(llm.get('system_messages', True))
    payload_messages = []
    if system_text and use_system:
        payload_messages.append({'role': 'system', 'content': system_text})
    elif system_text:
        if rest:
            rest[0] = dict(rest[0])
            rest[0]['content'] = system_text + '\n\n' + rest[0]['content']
        else:
            rest = [{'role': 'user', 'content': system_text}]
    payload_messages.extend(rest)

    tokens = int(max_tokens or llm.get('max_tokens') or 800)
    use_json = bool(json_mode) and bool(llm.get('structured_output', True))
    kwargs = {
        'model': llm.get('model') or '',
        'messages': payload_messages,
        'temperature': float(llm.get('temperature', 0.2) or 0.2),
        'max_tokens': tokens,
    }
    if use_json:
        kwargs['response_format'] = {'type': 'json_object'}
    reasoning = llm.get('reasoning_params') or {}
    if isinstance(reasoning, dict) and reasoning:
        kwargs['extra_body'] = reasoning

    resp = None
    last_err = None
    for _ in range(3):
        try:
            resp = client.chat.completions.create(**kwargs)
            break
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if use_json and ('response_format' in msg or 'json_object' in msg):
                kwargs.pop('response_format', None)
                use_json = False
                continue
            if 'temperature' in msg and ('only 1' in msg or 'unsupported' in msg
                                         or 'not supported' in msg):
                kwargs.pop('temperature', None)
                continue
            if 'max_tokens' in msg and 'max_completion_tokens' in msg:
                kwargs.pop('max_tokens', None)
                kwargs['max_completion_tokens'] = tokens
                continue
            raise
    if resp is None:
        raise last_err if last_err else RuntimeError('模型调用失败')

    content = ''
    if getattr(resp, 'choices', None):
        content = resp.choices[0].message.content or ''
    usage = getattr(resp, 'usage', None)
    return content, _usage_dict(
        getattr(usage, 'prompt_tokens', 0) if usage else 0,
        getattr(usage, 'completion_tokens', 0) if usage else 0,
    )


def _anthropic_url(base_url):
    base = (base_url or '').rstrip('/')
    if base.endswith('/messages'):
        return base
    if base.endswith('/v1'):
        return base + '/messages'
    return base + '/v1/messages'


def _anthropic_chat(llm, messages, max_tokens=None, json_mode=True, system=None):
    base_url = (llm.get('base_url') or '').strip()
    if not base_url:
        raise RuntimeError('未配置 Base URL')
    system_text, rest = _split_system(messages, system)
    body = {
        'model': llm.get('model') or '',
        'max_tokens': int(max_tokens or llm.get('max_tokens') or 800),
        'temperature': float(llm.get('temperature', 0.2) or 0.2),
        'messages': rest or [{'role': 'user', 'content': ''}],
    }
    if system_text:
        body['system'] = system_text
    reasoning = llm.get('reasoning_params') or {}
    if isinstance(reasoning, dict) and reasoning:
        body.update(reasoning)
    headers = dict(request_headers(llm, base_url), **{
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'x-api-key': llm.get('api_key') or '',
        'anthropic-version': '2023-06-01',
    })
    req = urllib.request.Request(
        _anthropic_url(base_url),
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    try:
        if _is_local_base(base_url):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            opener = urllib.request.build_opener()
        with opener.open(req, timeout=float(llm.get('timeout', 60) or 60)) as resp:
            data = json.loads(resp.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            pass
        raise RuntimeError('HTTP %s %s' % (e.code, detail.strip()))
    except urllib.error.URLError as e:
        raise RuntimeError('连接失败：%s' % (e.reason,))

    content = data.get('content') or []
    if isinstance(content, str):
        text = content
    else:
        text = ''.join(b.get('text', '') for b in content
                       if isinstance(b, dict) and b.get('type') == 'text')
    usage = data.get('usage') or {}
    return text, _usage_dict(usage.get('input_tokens'), usage.get('output_tokens'))


def chat(llm, messages, max_tokens=None, json_mode=True, system=None):
    """统一调用入口，返回 {'content': str, 'usage': dict}。"""
    if not (llm.get('api_key') or '').strip():
        raise RuntimeError('未配置 API Key')
    if (llm.get('api_format') or 'openai').lower() == 'anthropic':
        content, usage = _anthropic_chat(llm, messages, max_tokens, json_mode, system)
    else:
        content, usage = _openai_chat(llm, messages, max_tokens, json_mode, system)
    return {'content': content or '', 'usage': usage}


def test_connection(llm):
    """发送一个最小请求，验证 Base URL / Key / 模型名是否可用。"""
    started = time.time()
    result = chat(
        llm,
        [{'role': 'user', 'content': '请只回复 OK 两个字母，不要输出其他内容。'}],
        max_tokens=min(256, int(llm.get('max_tokens') or 256)),
        json_mode=False,
        system='你是连通性测试助手，只回复用户要求的内容。',
    )
    return {
        'reply': (result['content'] or '').strip()[:200],
        'usage': result['usage'],
        'latency_ms': int((time.time() - started) * 1000),
    }
