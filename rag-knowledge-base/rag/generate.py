# -*- coding: utf-8 -*-
"""生成层：提示词构造、LLM 调用、解析与降级。"""
import json
import re

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


def build_context(hits):
    parts = []
    for i, h in enumerate(hits, 1):
        meta = h.get('meta') or {}
        parts.append('[%d] 来源：%s｜%s\n%s' % (i, meta.get('source', ''), meta.get('heading', ''), h.get('text', '')))
    return '\n\n'.join(parts)


def build_prompt(question, hits, history=None):
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
    return ('%s资料：\n%s\n\n用户问题：%s\n\n请依据资料回答，输出 JSON。'
            % (hist, build_context(hits), question))


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


def answer(question, hits, cfg, history=None):
    """返回 {answer, sources, confidence, covered, mode, prompt, usage}。"""
    llm = cfg.get('llm') or {}
    key = (llm.get('api_key') or '').strip()
    prompt = build_prompt(question, hits, history)

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

    from openai import OpenAI
    client = OpenAI(api_key=key, base_url=llm.get('base_url') or 'https://api.deepseek.com')
    last_err = ''
    for _ in range(2):
        try:
            resp = client.chat.completions.create(
                model=llm.get('model') or 'deepseek-flash',
                messages=[{'role': 'system', 'content': SYSTEM},
                          {'role': 'user', 'content': prompt}],
                temperature=float(llm.get('temperature', 0.2)),
                max_tokens=int(llm.get('max_tokens', 800)),
                response_format={'type': 'json_object'},
                timeout=float(llm.get('timeout', 30)),
            )
            content = resp.choices[0].message.content or ''
            parsed = _parse(content)
            if parsed:
                usage = getattr(resp, 'usage', None)
                parsed['usage'] = {
                    'prompt_tokens': int(getattr(usage, 'prompt_tokens', 0) or 0),
                    'completion_tokens': int(getattr(usage, 'completion_tokens', 0) or 0),
                    'total_tokens': int(getattr(usage, 'total_tokens', 0) or 0),
                } if usage else {}
                parsed['mode'] = 'llm'
                parsed['prompt'] = prompt
                return parsed
            last_err = '输出格式不合规'
        except Exception as e:
            last_err = str(e)[:200]
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
