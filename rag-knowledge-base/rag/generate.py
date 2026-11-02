# -*- coding: utf-8 -*-
"""生成层：提示词构造、多格式模型调用、解析与降级。"""
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

SYSTEM = (
    '你是设备运维知识库助手。只能依据用户提供的 <资料> 回答，禁止使用外部知识。\n'
    '规则：\n'
    '1. 只引用 <资料> 中的内容，并在 sources 中给出资料编号；\n'
    '2. 资料中没有答案时，输出 covered=false，回答“知识库未覆盖该问题，建议联系设备管理员”；\n'
    '3. 不得编造步骤、不得合并无关资料、不得给出“类似问题”的变通方案；\n'
    '4. 回答按“判断依据 → 处理步骤 → 升级条件”组织，步骤编号；\n'
    '5. 只输出 JSON，不要输出解释文字或代码块标记。\n'
    '输出格式：{"answer":"...","sources":[1,2],"confidence":0.0-1.0,"covered":true}'
)


def _max_prompt_chars(llm):
    """按模型上下文窗口估算可用的 prompt 字符数（中文按 1 字≈1 token 保守估算）。"""
    try:
        context = int(llm.get('context_window') or 0)
        output = int(llm.get('max_tokens') or 800)
    except Exception:
        return None
    if context <= 0:
        return None
    return max(1200, context - output - 600)


def build_context(hits, max_chars=None):
    parts = []
    used = 0
    for i, h in enumerate(hits, 1):
        meta = h.get('meta') or {}
        text = h.get('text') or ''
        if max_chars is not None:
            remain = max_chars - used
            if remain < 160:
                break
            if len(text) > remain - 100:
                text = text[:max(0, remain - 100)].rstrip() + '…'
        block = '[%d] 来源：%s｜%s\n%s' % (
            i, meta.get('source', ''), meta.get('heading', ''), text)
        parts.append(block)
        used += len(block)
    return '\n\n'.join(parts)


def build_prompt(question, hits, history=None, max_chars=None):
    hist = ''
    if history:
        parts = []
        for h in history[-2:]:
            q = str((h or {}).get('q', '')).strip()[:120]
            a = str((h or {}).get('a', '')).strip()[:200]
            if q:
                parts.append('用户：%s\n助手：%s' % (q, a or '（略）'))
        if parts:
            hist = ('历史对话（仅用于理解上下文，回答仍必须依据资料）：\n'
                    + '\n'.join(parts) + '\n\n')
    budget = None
    if max_chars:
        budget = max(600, max_chars - len(hist) - len(question) - 100)
    return ('%s资料：\n%s\n\n用户问题：%s\n\n请依据资料回答，输出 JSON。'
            % (hist, build_context(hits, budget), question))


def _parse(content):
    if not content:
        return None
    m = re.search(r'\{.*\}', content, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    answer = str(data.get('answer') or '').strip()
    if not answer:
        return None
    sources = data.get('sources') if isinstance(data.get('sources'), list) else []
    try:
        conf = float(data.get('confidence', 0.5))
    except Exception:
        conf = 0.5
    return {
        'answer': answer,
        'sources': [int(s) for s in sources if str(s).isdigit()],
        'confidence': max(0.0, min(1.0, conf)),
        'covered': bool(data.get('covered', True)),
    }


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


_OPENCODE_SESSION = 'rag-' + uuid.uuid4().hex


def _request_headers(llm, base_url):
    """自定义请求头；OpenCode Go 必须带 x-opencode-session。"""
    headers = dict(llm.get('headers') or {})
    if 'opencode.ai' in str(base_url or '').lower():
        if not any(k.lower() == 'x-opencode-session' for k in headers):
            headers['x-opencode-session'] = _OPENCODE_SESSION
    return headers


def _openai_call(llm, prompt, max_tokens=None, structured=None, system=SYSTEM):
    from openai import OpenAI
    base_url = (llm.get('base_url') or '').strip()
    if not base_url:
        raise RuntimeError('未配置 Base URL')
    timeout = float(llm.get('timeout', 60) or 60)
    headers = _request_headers(llm, base_url)
    client_kwargs = {'api_key': llm.get('api_key') or 'not-needed',
                     'base_url': base_url, 'timeout': timeout}
    if headers:
        client_kwargs['default_headers'] = headers
    if _is_local_base(base_url):
        # 本机 mock / Ollama 不能被系统代理劫持
        import httpx
        client_kwargs['http_client'] = httpx.Client(trust_env=False, timeout=timeout)
    client = OpenAI(**client_kwargs)
    tokens = int(max_tokens or llm.get('max_tokens') or 800)
    use_json = bool(llm.get('structured_output', True)) if structured is None else bool(structured)
    system_text = SYSTEM if system is None else system
    if llm.get('system_messages', True) and system_text:
        messages = [{'role': 'system', 'content': system_text},
                    {'role': 'user', 'content': prompt}]
    else:
        messages = [{'role': 'user', 'content': (system_text + '\n\n' if system_text else '') + prompt}]
    kwargs = {
        'model': llm.get('model') or 'deepseek-flash',
        'messages': messages,
        'temperature': float(llm.get('temperature', 0.2) or 0.2),
        'max_tokens': tokens,
    }
    if use_json:
        kwargs['response_format'] = {'type': 'json_object'}
    reasoning = llm.get('reasoning_params') or {}
    if isinstance(reasoning, dict) and reasoning:
        kwargs['extra_body'] = reasoning

    resp = None
    for _ in range(3):
        try:
            resp = client.chat.completions.create(**kwargs)
            break
        except Exception as e:
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
        raise RuntimeError('模型调用失败')
    content = ''
    if resp.choices:
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


def _anthropic_call(llm, prompt, max_tokens=None, structured=None, system=SYSTEM):
    base_url = (llm.get('base_url') or '').strip()
    if not base_url:
        raise RuntimeError('未配置 Base URL')
    system_text = SYSTEM if system is None else system
    body = {
        'model': llm.get('model') or 'claude-sonnet-4-5',
        'max_tokens': int(max_tokens or llm.get('max_tokens') or 800),
        'temperature': float(llm.get('temperature', 0.2) or 0.2),
        'messages': [{'role': 'user', 'content': prompt}],
    }
    if llm.get('system_messages', True) and system_text:
        body['system'] = system_text
    elif system_text:
        body['messages'][0]['content'] = system_text + '\n\n' + prompt
    reasoning = llm.get('reasoning_params') or {}
    if isinstance(reasoning, dict) and reasoning:
        body.update(reasoning)
    req = urllib.request.Request(
        _anthropic_url(base_url),
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers=dict(_request_headers(llm, base_url), **{
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'x-api-key': llm.get('api_key') or '',
            'anthropic-version': '2023-06-01',
        }),
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


def _call(llm, prompt, max_tokens=None, structured=None, system=SYSTEM):
    if (llm.get('api_format') or 'openai') == 'anthropic':
        return _anthropic_call(llm, prompt, max_tokens=max_tokens,
                               structured=structured, system=system)
    return _openai_call(llm, prompt, max_tokens=max_tokens,
                        structured=structured, system=system)


def answer(question, hits, cfg, history=None):
    """返回 {answer, sources, confidence, covered, mode, prompt, usage}。"""
    llm = cfg.get('llm') or {}
    key = (llm.get('api_key') or '').strip()
    prompt = build_prompt(question, hits, history=history,
                          max_chars=_max_prompt_chars(llm))

    if not hits:
        return {'answer': '知识库未检索到相关内容，建议联系设备管理员。', 'sources': [],
                'confidence': 0.0, 'covered': False, 'mode': 'empty', 'prompt': prompt}

    if not key:
        top = hits[0]
        return {
            'answer': '检索模式：以下为最相关资料原文（%s｜%s）。\n\n%s'
                      % (top['meta']['source'], top['meta']['heading'],
                         top.get('full_text') or top['text']),
            'sources': [1], 'confidence': round(top['score'], 3), 'covered': True,
            'mode': 'retrieval_only', 'prompt': prompt, 'usage': {},
        }

    last_err = ''
    for _ in range(2):
        try:
            content, usage = _call(llm, prompt)
            parsed = _parse(content)
            if parsed:
                parsed['usage'] = usage
                parsed['mode'] = 'llm'
                parsed['prompt'] = prompt
                return parsed
            last_err = '输出格式不合规'
        except Exception as e:
            last_err = str(e)[:300]
            if '401' in last_err or 'unauthorized' in last_err.lower():
                break
    top = hits[0]
    return {
        'answer': '模型调用失败（%s），已降级为检索结果：%s｜%s\n%s'
                  % (last_err, top['meta']['source'], top['meta']['heading'],
                     top.get('full_text') or top['text']),
        'sources': [1], 'confidence': round(top['score'], 3), 'covered': True,
        'mode': 'fallback', 'prompt': prompt, 'usage': {},
    }


def test_connection(llm):
    """发送一个最小请求，验证 Base URL / Key / 模型名是否可用。"""
    if not (llm.get('api_key') or '').strip():
        raise RuntimeError('未配置 API Key')
    started = time.time()
    content, usage = _call(
        llm, '请只回复 OK 两个字母，不要输出其他内容。',
        max_tokens=min(256, int(llm.get('max_tokens') or 256)),
        structured=False,
        system='你是连通性测试助手，只回复用户要求的内容。',
    )
    return {
        'reply': (content or '').strip()[:200],
        'usage': usage,
        'latency_ms': int((time.time() - started) * 1000),
    }
