# -*- coding: utf-8 -*-
"""模型供应商与模型配置：迁移、校验、脱敏、解析。

配置结构（config.json）：
{
  "providers": [
    {"id","name","base_url","api_format","api_key","enabled","timeout",
     "models": [{"id","model_id","name","context_window","max_output",
                 "reasoning_levels","reasoning_params","capabilities","enabled"}]}
  ],
  "active": {"provider_id","model_id","reasoning"}
}
"""
import copy
import json
import os
import re
import secrets
from urllib.parse import urlparse

from core import paths

FORMATS = ('openai', 'anthropic')
MAX_PROVIDERS = 20
MAX_MODELS = 30
MAX_KEY_LEN = 500

DEFAULT_CONFIG = {
    'app': {'host': '127.0.0.1', 'port': 8100, 'data_dir': 'data'},
    'embedding_model': 'BAAI/bge-small-zh-v1.5',
    'chunk_size': 400,
    'chunk_overlap': 50,
    'top_k': 5,
    'docs_dir': 'data/docs',
    'chroma_dir': 'data/chroma',
    'llm': {
        'base_url': '', 'model': '', 'api_key': '',
        'temperature': 0.2, 'max_tokens': 800, 'timeout': 60,
    },
    'providers': [],
    'active': {},
    'rag': {'top_k': 5, 'triage_top_k': 3},
    'repair': {
        'kb_mode': 'rag',
        'triage_top_k': 3,
        'fallback_rule': True,
        'sla_hours': {'高': 4, '中': 24, '低': 72},
        'admin_password': '',
        'admin_password_env': 'WORKBENCH_ADMIN_PASSWORD',
    },
    'field': {'assistant': '网络排障助手.html'},
    'hardware': {
        'enabled': False,
        'ingest_token': '',
        'ingest_token_env': 'WORKBENCH_INGEST_TOKEN',
        'auto_ticket': True,
        'alert_cooldown_minutes': 10,
        'offline_minutes': 5,
        'rules': [
            {'key': 'temp_high', 'metric': 'temperature', 'op': '>', 'value': 40,
             'level': '高', 'message': '设备温度过高'},
            {'key': 'current_zero', 'metric': 'current', 'op': '<=', 'value': 0.05,
             'level': '中', 'message': '设备电流为 0，可能断电'},
            {'key': 'door_open', 'metric': 'door', 'op': '>=', 'value': 1,
             'level': '中', 'message': '机柜门被打开'},
            {'key': 'water_leak', 'metric': 'water', 'op': '>=', 'value': 1,
             'level': '高', 'message': '检测到漏水'},
        ],
    },
}


def _deep_merge(base, extra):
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_cfg():
    """读取统一配置；缺失字段用默认值补齐，配置损坏时退回默认。"""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    path = paths.config_path()
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                cfg = _deep_merge(cfg, raw)
        except Exception:
            pass
    return cfg


def save_cfg(cfg):
    write_config(paths.config_path(), cfg)


_HOST_NAMES = (
    ('deepseek', 'DeepSeek'),
    ('openai', 'OpenAI'),
    ('dashscope', '阿里云百炼'),
    ('bigmodel', '智谱 GLM'),
    ('moonshot', 'Kimi'),
    ('siliconflow', '硅基流动'),
    ('openrouter', 'OpenRouter'),
    ('anthropic', 'Anthropic'),
    ('localhost', '本地模型'),
    ('127.0.0.1', '本地模型'),
)


def _new_id(prefix):
    return prefix + secrets.token_hex(4)


def _text(value, limit=200):
    return str(value or '').strip()[:limit]


def _bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    if value is None:
        return default
    return bool(value)


def _int(value, default, low, high):
    try:
        n = int(float(value))
    except Exception:
        n = default
    return max(low, min(high, n))


def _name_from_url(url):
    host = (urlparse(url).hostname or '').lower()
    for key, name in _HOST_NAMES:
        if key in host:
            return name
    return host or '默认供应商'


def is_local_url(url):
    host = (urlparse(str(url or '')).hostname or '').lower()
    return host in ('localhost', '127.0.0.1', '0.0.0.0', '::1')


def _capabilities(raw):
    src = raw if isinstance(raw, dict) else {}
    return {
        'text': _bool(src.get('text'), True),
        'image': _bool(src.get('image'), False),
        'video': _bool(src.get('video'), False),
        'pdf': _bool(src.get('pdf'), False),
        'structured_output': _bool(src.get('structured_output'), True),
        'web_search': _bool(src.get('web_search'), False),
        'system_messages': _bool(src.get('system_messages'), True),
    }


def default_capabilities():
    return _capabilities({})


def _levels(raw):
    if isinstance(raw, str):
        parts = re.split(r'[,，\n]', raw)
    elif isinstance(raw, list):
        parts = raw
    else:
        parts = []
    out = []
    for item in parts:
        value = _text(item, 40)
        if value and value not in out:
            out.append(value)
    return out[:8]


def _reasoning_params(raw):
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try:
            raw = json.loads(raw)
        except Exception:
            raise ValueError('推理参数映射必须是合法 JSON')
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError('推理参数映射必须是 JSON 对象')
    out = {}
    for key, value in list(raw.items())[:16]:
        level = _text(key, 40)
        if not level:
            continue
        if value is None:
            out[level] = {}
        elif isinstance(value, dict):
            out[level] = value
        else:
            raise ValueError('推理参数映射中每个等级必须是 JSON 对象')
    if len(json.dumps(out, ensure_ascii=False)) > 20000:
        raise ValueError('推理参数映射过大')
    return out


def _headers(raw):
    """供应商自定义请求头（如 OpenCode 的 x-opencode-session）。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in list(raw.items())[:20]:
        name = _text(key, 80)
        val = _text(value, 500)
        if not name or not val:
            continue
        if any(ch in name for ch in '\r\n') or any(ch in val for ch in '\r\n'):
            continue
        out[name] = val
    return out


def _model(raw, require_id=True):
    raw = raw if isinstance(raw, dict) else {}
    model_id = _text(raw.get('model_id'), 120)
    if require_id and not model_id:
        raise ValueError('模型 ID 不能为空')
    context = _int(raw.get('context_window'), 128000, 1000, 2000000)
    max_output = _int(raw.get('max_output'), 2048, 1, min(200000, context))
    if max_output > context:
        max_output = context
    return {
        'id': _text(raw.get('id'), 40) or _new_id('mod_'),
        'model_id': model_id,
        'name': _text(raw.get('name') or model_id or '未命名模型', 120),
        'context_window': context,
        'max_output': max_output,
        'reasoning_levels': _levels(raw.get('reasoning_levels')),
        'reasoning_params': _reasoning_params(raw.get('reasoning_params')),
        'capabilities': _capabilities(raw.get('capabilities')),
        'enabled': _bool(raw.get('enabled'), True),
    }


def _provider(raw, require_fields=True):
    raw = raw if isinstance(raw, dict) else {}
    base = _text(raw.get('base_url'), 300).rstrip('/')
    name = _text(raw.get('name'), 80) or _name_from_url(base)
    if require_fields:
        if not name:
            raise ValueError('供应商名称不能为空')
        if not base:
            raise ValueError('Base URL 不能为空')
    if base:
        parsed = urlparse(base)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('Base URL 必须以 http:// 或 https:// 开头')
    fmt = _text(raw.get('api_format'), 20).lower()
    if fmt not in FORMATS:
        fmt = 'openai'
    models = []
    seen = set()
    for item in (raw.get('models') or [])[:MAX_MODELS]:
        try:
            model = _model(item, require_id=False)
        except ValueError:
            continue
        if not model['model_id']:
            continue
        if model['id'] in seen:
            model['id'] = _new_id('mod_')
        seen.add(model['id'])
        models.append(model)
    return {
        'id': _text(raw.get('id'), 40) or _new_id('prov_'),
        'name': name or '未命名供应商',
        'base_url': base,
        'api_format': fmt,
        'api_key': str(raw.get('api_key') or '')[:MAX_KEY_LEN],
        'enabled': _bool(raw.get('enabled'), True),
        'timeout': _int(raw.get('timeout'), 60, 5, 300),
        'headers': _headers(raw.get('headers')),
        'models': models,
    }


def _legacy(cfg):
    """没有 providers 字段时，从旧版 llm 配置迁移出一个默认供应商。"""
    llm = cfg.get('llm') if isinstance(cfg.get('llm'), dict) else {}
    base = _text(llm.get('base_url') or 'https://api.deepseek.com', 300).rstrip('/')
    model_id = _text(llm.get('model') or 'deepseek-v4.1-flash', 120)
    model = {
        'id': 'default-model',
        'model_id': model_id,
        'name': model_id,
        'context_window': 128000,
        'max_output': _int(llm.get('max_tokens'), 800, 1, 200000),
        'reasoning_levels': [],
        'reasoning_params': {},
        'capabilities': _capabilities({'structured_output': True}),
        'enabled': True,
    }
    provider = {
        'id': 'default',
        'name': _name_from_url(base),
        'base_url': base,
        'api_format': 'openai',
        'api_key': str(llm.get('api_key') or '')[:MAX_KEY_LEN],
        'enabled': True,
        'timeout': _int(llm.get('timeout'), 60, 5, 300),
        'headers': {},
        'models': [model],
    }
    return [provider], {'provider_id': 'default', 'model_id': 'default-model', 'reasoning': ''}


def _active(providers, raw):
    raw = raw if isinstance(raw, dict) else {}
    pid = _text(raw.get('provider_id'), 40)
    mid = _text(raw.get('model_id'), 40)
    reason = _text(raw.get('reasoning'), 40)

    provider = next((p for p in providers if p['id'] == pid), None)
    model = None
    if provider:
        model = next((m for m in provider['models'] if m['id'] == mid), None)
    if not provider or not model or not provider['enabled'] or not model['enabled']:
        provider = next((p for p in providers
                         if p['enabled'] and any(m['enabled'] for m in p['models'])), None)
        model = next((m for m in (provider['models'] if provider else []) if m['enabled']), None)
    if not provider or not model:
        return {}
    levels = model['reasoning_levels']
    if levels:
        if reason not in levels:
            reason = levels[0]
    else:
        reason = ''
    return {'provider_id': provider['id'], 'model_id': model['id'], 'reasoning': reason}


def normalize(cfg):
    """返回 (providers, active)。providers 不存在时迁移旧 llm；空列表表示用户删光了。"""
    raw = cfg.get('providers')
    if not isinstance(raw, list):
        return _legacy(cfg)
    if not raw:
        return [], {}
    providers = []
    seen = set()
    for item in raw[:MAX_PROVIDERS]:
        try:
            provider = _provider(item, require_fields=False)
        except ValueError:
            continue
        if provider['id'] in seen:
            provider['id'] = _new_id('prov_')
        seen.add(provider['id'])
        providers.append(provider)
    return providers, _active(providers, cfg.get('active'))


def _find(providers, pid, mid):
    provider = next((p for p in providers if p['id'] == pid), None)
    model = None
    if provider:
        model = next((m for m in provider['models'] if m['id'] == mid), None)
    return provider, model


def _info(providers, active):
    provider, model = _find(providers, active.get('provider_id'), active.get('model_id'))
    if not provider or not model:
        return {'configured': False, 'provider_id': '', 'provider_name': '',
                'model_id': '', 'model': '', 'model_name': '', 'reasoning': '',
                'reasoning_levels': [], 'context_window': 0, 'max_output': 0}
    return {
        'provider_id': provider['id'],
        'provider_name': provider['name'],
        'model_id': model['id'],
        'model': model['model_id'],
        'model_name': model['name'],
        'reasoning': active.get('reasoning', ''),
        'reasoning_levels': model['reasoning_levels'],
        'context_window': model['context_window'],
        'max_output': model['max_output'],
        'configured': bool(provider['api_key'] or is_local_url(provider['base_url'])),
        'has_key': bool(provider['api_key']),
        'is_local': is_local_url(provider['base_url']),
        'base_url': provider['base_url'],
        'api_format': provider['api_format'],
    }


def active_info(cfg):
    providers, active = normalize(cfg)
    return _info(providers, active)


def public(cfg):
    """给前端看的数据：不返回 api_key，只返回 has_key。"""
    providers, active = normalize(cfg)
    out = []
    for provider in providers:
        item = copy.deepcopy(provider)
        item['has_key'] = bool(item.pop('api_key', ''))
        item['is_local'] = is_local_url(item['base_url'])
        out.append(item)
    return {'providers': out, 'active': active, 'active_info': _info(providers, active)}


def merge_for_save(cfg, incoming_providers, incoming_active):
    """合并前端提交：空 Key 表示沿用旧 Key，clear_key=true 表示清除。"""
    existing, _ = normalize(cfg)
    old_keys = {p['id']: p['api_key'] for p in existing}
    old_headers = {p['id']: p['headers'] for p in existing}

    providers = []
    seen = set()
    for item in (incoming_providers or [])[:MAX_PROVIDERS]:
        if not isinstance(item, dict):
            continue
        pid = _text(item.get('id'), 40)
        raw_key = item.get('api_key')
        if item.get('clear_key'):
            key = ''
        elif raw_key is None or str(raw_key).strip() in ('', '******'):
            key = old_keys.get(pid, '')
        else:
            key = str(raw_key).strip()[:MAX_KEY_LEN]
        payload = dict(item)
        payload['api_key'] = key
        if 'headers' not in payload:
            payload['headers'] = old_headers.get(pid, {})
        provider = _provider(payload, require_fields=True)
        if provider['id'] in seen:
            provider['id'] = _new_id('prov_')
        seen.add(provider['id'])
        providers.append(provider)
    return providers, _active(providers, incoming_active)


def set_active(providers, data):
    pid = _text((data or {}).get('provider_id'), 40)
    mid = _text((data or {}).get('model_id'), 40)
    reason = _text((data or {}).get('reasoning'), 40)
    provider, model = _find(providers, pid, mid)
    if not provider or not model:
        raise ValueError('模型不存在')
    if not provider['enabled'] or not model['enabled']:
        raise ValueError('该模型已禁用')
    levels = model['reasoning_levels']
    if levels:
        if reason not in levels:
            reason = levels[0]
    else:
        reason = ''
    return {'provider_id': provider['id'], 'model_id': model['id'], 'reasoning': reason}


def resolve(cfg, provider_id=None, model_id=None, reasoning=None):
    """解析本次请求使用的模型。无模型时返回空 Key 配置，走检索模式。"""
    providers, active = normalize(cfg)
    pid = _text(provider_id, 40) or active.get('provider_id', '')
    mid = _text(model_id, 40) or active.get('model_id', '')
    provider, model = _find(providers, pid, mid)
    if (not provider or not model or not provider['enabled'] or not model['enabled']):
        provider, model = _find(providers, active.get('provider_id'), active.get('model_id'))
    if not provider or not model or not provider['enabled'] or not model['enabled']:
        provider = next((p for p in providers
                         if p['enabled'] and any(m['enabled'] for m in p['models'])), None)
        model = next((m for m in (provider['models'] if provider else []) if m['enabled']), None)

    legacy_llm = cfg.get('llm') if isinstance(cfg.get('llm'), dict) else {}
    temperature = float(legacy_llm.get('temperature', 0.2) or 0.2)

    if not provider or not model:
        return {
            'llm': {'base_url': '', 'api_key': '', 'model': '', 'api_format': 'openai',
                    'max_tokens': 800, 'context_window': 0, 'reasoning': '',
                    'reasoning_params': {}, 'structured_output': False,
                    'temperature': temperature, 'timeout': 30},
            'meta': _info([], {}),
            'configured': False,
        }

    levels = model['reasoning_levels']
    level = _text(reasoning, 40)
    if levels:
        if level not in levels:
            level = active.get('reasoning') if active.get('reasoning') in levels else levels[0]
    else:
        level = ''
    key = provider['api_key'].strip()
    local = is_local_url(provider['base_url'])
    call_key = key or ('local' if local else '')
    llm = {
        'base_url': provider['base_url'],
        'api_key': call_key,
        'model': model['model_id'],
        'api_format': provider['api_format'],
        'max_tokens': model['max_output'],
        'context_window': model['context_window'],
        'reasoning': level,
        'reasoning_params': copy.deepcopy(model['reasoning_params'].get(level, {})) if level else {},
        'structured_output': bool(model['capabilities'].get('structured_output', True)),
        'system_messages': bool(model['capabilities'].get('system_messages', True)),
        'headers': copy.deepcopy(provider['headers']),
        'temperature': temperature,
        'timeout': provider['timeout'],
    }
    meta = {
        'provider_id': provider['id'], 'provider_name': provider['name'],
        'model_id': model['id'], 'model': model['model_id'], 'model_name': model['name'],
        'reasoning': level, 'reasoning_levels': levels,
        'context_window': model['context_window'], 'max_output': model['max_output'],
        'configured': bool(call_key), 'api_format': provider['api_format'],
    }
    return {'llm': llm, 'meta': meta, 'configured': bool(call_key)}


def legacy_llm(cfg, providers, active):
    """把当前选择写回旧版 llm 字段，保证旧脚本仍可运行。"""
    provider, model = _find(providers, active.get('provider_id'), active.get('model_id'))
    old = cfg.get('llm') if isinstance(cfg.get('llm'), dict) else {}
    if not provider or not model:
        return {'base_url': '', 'model': '', 'api_key': '',
                'temperature': old.get('temperature', 0.2),
                'max_tokens': old.get('max_tokens', 800),
                'timeout': old.get('timeout', 30)}
    return {
        'base_url': provider['base_url'],
        'model': model['model_id'],
        'api_key': provider['api_key'],
        'temperature': old.get('temperature', 0.2),
        'max_tokens': model['max_output'],
        'timeout': provider['timeout'],
    }


def write_config(path, cfg):
    """原子写入，避免断电/并发写坏 config.json。"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, path)
