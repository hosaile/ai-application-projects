# -*- coding: utf-8 -*-
"""QA 模块：RAG 问答工作台（对话 / 知识库 / 检索评估 / 模型设置）。"""
import json
import os
import threading

from flask import Blueprint, jsonify, request, send_from_directory

from core import config, paths
from modules.qa.rag import generate, retrieve, store

bp = Blueprint('qa', __name__)
QA_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

ALLOWED_EXT = ('.md', '.txt')
MAX_UPLOAD = 2 * 1024 * 1024  # 单文件 2MB
_REBUILD_LOCK = threading.Lock()
_SETTINGS_LOCK = threading.Lock()


def safe_doc_name(name):
    """只保留文件名，防路径穿越；含路径分隔符或 .. 直接拒绝。"""
    raw = str(name or '')
    if '..' in raw or '/' in raw or '\\' in raw:
        return None
    base = os.path.basename(raw)
    if not base or not base.lower().endswith(ALLOWED_EXT) or len(base) > 80:
        return None
    return base


@bp.route('/')
def index():
    return send_from_directory(QA_STATIC, 'index.html')


def _asset_hits(question):
    """问题里提到设备编号/名称/位置时，把设备档案与历史工单作为高优先资料。"""
    try:
        from modules.repair import db as repair_db
        conn = repair_db.get_db()
        assets = repair_db.list_assets(conn)
        q = (question or '').lower()
        matched = []
        for a in assets:
            score = 0
            code = (a.get('code') or '').lower()
            name = a.get('name') or ''
            location = a.get('location') or ''
            if code and code in q:
                score = 3
            elif name and name in question:
                score = 2
            elif len(location) >= 2 and location in question:
                score = 1
            if score:
                matched.append((score, a))
        matched.sort(key=lambda x: (-x[0], x[1].get('id') or 0))
        hits = []
        for _, a in matched[:2]:
            tickets = repair_db.asset_tickets(conn, a['id'], 5)
            lines = [
                '设备：%s（%s）' % (a.get('name'), a.get('code')),
                '类别/位置：%s / %s' % (a.get('category') or '其他',
                                      a.get('location') or '未填'),
                '状态/负责人：%s / %s' % (a.get('status') or '在用',
                                      a.get('owner') or '未填'),
                '保修至：%s' % (a.get('warranty_until') or '未填'),
            ]
            if tickets:
                lines.append('最近工单：')
                for t in tickets:
                    lines.append('- %s %s %s：%s（%s）' % (
                        t.get('ticket_no'), (t.get('created_at') or '')[:10],
                        t.get('status_label'), (t.get('description') or '')[:60],
                        t.get('note') or '无备注'))
            hits.append({
                'id': 'asset_%s' % a['id'],
                'text': '\n'.join(lines),
                'meta': {'source': '设备档案',
                         'heading': '%s（%s）' % (a.get('name'), a.get('code')),
                         'seq': 0, 'kind': 'asset'},
                'score': 1.0,
            })
        conn.close()
        return hits
    except Exception:
        return []


@bp.route('/api/ask', methods=['POST'])
def ask():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': '请求格式错误'}), 400
    q = data.get('question')
    if not isinstance(q, str) or not q.strip():
        return jsonify({'error': '请输入问题'}), 400
    q = q.strip()
    if len(q) > 300:
        return jsonify({'error': '问题最多 300 字'}), 400
    cfg = config.load_cfg()
    try:
        top_k = int(data.get('top_k') or cfg.get('top_k', 5))
    except Exception:
        top_k = cfg.get('top_k', 5)
    top_k = max(1, min(10, top_k))

    history = data.get('history')
    if not isinstance(history, list):
        history = []
    history = [h for h in history if isinstance(h, dict)][-2:]  # 只带最近 2 轮
    # 多轮指代处理：检索时拼上上一轮问题，避免“那重启之后呢”这类追问丢失实体词
    search_q = q
    if history:
        prev_q = str((history[-1] or {}).get('q') or '').strip()
        if prev_q:
            search_q = prev_q[:120] + ' ' + q
    doc_hits = retrieve.retrieve(search_q, top_k, paths.chroma_dir(),
                                 cfg['embedding_model'])
    asset_hits = _asset_hits(q)
    hits = asset_hits + doc_hits
    for h in hits:
        meta = h.get('meta') or {}
        if meta.get('kind') == 'asset':
            continue
        full = store.section_text(paths.chroma_dir(), meta.get('source', ''),
                                  meta.get('heading', ''))
        if full:
            h['full_text'] = full
        break
    resolved = config.resolve(cfg, data.get('provider_id'), data.get('model_id'),
                              data.get('reasoning'))
    call_cfg = dict(cfg)
    call_cfg['llm'] = resolved['llm']
    result = generate.answer(q, hits, call_cfg, history=history)
    result['model'] = resolved['meta']
    result['question'] = q
    result['top_k'] = top_k
    result['hits'] = [{
        'source': h['meta'].get('source', ''),
        'heading': h['meta'].get('heading', ''),
        'score': h.get('score', 0),
        'kw_bonus': h.get('kw_bonus', 0),
        'text': h.get('text', ''),
    } for h in hits]
    result['assets'] = [h['meta'].get('heading', '') for h in asset_hits]
    return jsonify(result)


@bp.route('/api/docs', methods=['GET'])
def list_docs():
    d = paths.docs_dir()
    os.makedirs(d, exist_ok=True)
    items = []
    for root, dirs, files in os.walk(d):
        dirs[:] = sorted(x for x in dirs if not x.startswith('.'))
        for name in sorted(files):
            if not name.lower().endswith(ALLOWED_EXT):
                continue
            p = os.path.join(root, name)
            rel = os.path.relpath(p, d).replace(os.sep, '/')
            try:
                # 中文字符约 3 字节，按字节估算字数，避免整文件读入
                chars = max(1, os.path.getsize(p) // 3)
            except Exception:
                chars = 0
            items.append({'name': rel, 'chars': chars})
    items.sort(key=lambda x: x['name'])
    return jsonify({'docs': items, 'total': len(items)})


@bp.route('/api/upload', methods=['POST'])
def upload_docs():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': '没有收到文件'}), 400
    d = paths.docs_dir()
    os.makedirs(d, exist_ok=True)
    saved, skipped = [], []
    for f in files:
        name = safe_doc_name(f.filename)
        if not name:
            skipped.append('%s(类型或文件名不合法)' % (f.filename or '未命名'))
            continue
        try:
            f.stream.seek(0, os.SEEK_END)
            size = f.stream.tell()
            f.stream.seek(0)
        except Exception:
            size = 0
        if size > MAX_UPLOAD:
            skipped.append('%s(超过2MB)' % name)
            continue
        try:
            f.save(os.path.join(d, name))
            saved.append(name)
        except Exception:
            skipped.append('%s(保存失败)' % name)
    return jsonify({'ok': True, 'saved': saved, 'skipped': skipped,
                    'need_rebuild': bool(saved)})


@bp.route('/api/docs/<name>', methods=['DELETE'])
def delete_doc(name):
    safe = safe_doc_name(name)
    if not safe:
        return jsonify({'error': '文件名不合法'}), 400
    path = os.path.join(paths.docs_dir(), safe)
    if not os.path.exists(path):
        return jsonify({'error': '文件不存在'}), 404
    os.remove(path)
    return jsonify({'ok': True, 'need_rebuild': True})


@bp.route('/api/rebuild', methods=['POST'])
def rebuild_index():
    from modules.qa.rag import ingest
    if not _REBUILD_LOCK.acquire(blocking=False):
        return jsonify({'error': '索引重建中，请稍后再试'}), 409
    try:
        cfg = config.load_cfg()
        docs = ingest.load_docs(paths.docs_dir())
        if not docs:
            return jsonify({'error': '知识库为空'}), 400
        chunks = ingest.build_chunks(docs, cfg['chunk_size'], cfg['chunk_overlap'])
        n = store.rebuild(chunks, paths.chroma_dir(), cfg['embedding_model'])
        return jsonify({'ok': True, 'docs': len(docs), 'chunks': n})
    finally:
        _REBUILD_LOCK.release()


@bp.route('/api/stats')
def stats():
    cfg = config.load_cfg()
    s = store.stats(paths.chroma_dir())
    s['embedding_model'] = cfg['embedding_model']
    s['chunk_size'] = cfg['chunk_size']
    s['chunk_overlap'] = cfg['chunk_overlap']
    info = config.active_info(cfg)
    s['active_model'] = info
    s['llm_configured'] = bool(info.get('configured'))
    return jsonify(s)


@bp.route('/api/eval')
def eval_result():
    path = os.path.join(paths.tests_dir(), 'eval_result.json')
    if not os.path.exists(path):
        return jsonify({'error': '尚未运行评估，请执行 python tools/run_eval.py'}), 404
    with open(path, encoding='utf-8') as f:
        return jsonify(json.load(f))


@bp.route('/api/settings/models', methods=['GET'])
def settings_models():
    return jsonify(config.public(config.load_cfg()))


@bp.route('/api/settings/models', methods=['PUT'])
def save_settings_models():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': '请求格式错误'}), 400
    with _SETTINGS_LOCK:
        cfg = config.load_cfg()
        try:
            provs, active = config.merge_for_save(
                cfg, data.get('providers'), data.get('active'))
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        cfg['providers'] = provs
        cfg['active'] = active
        cfg['llm'] = config.legacy_llm(cfg, provs, active)
        try:
            config.save_cfg(cfg)
        except Exception as e:
            return jsonify({'error': '保存配置失败：%s' % str(e)[:120]}), 500
    return jsonify(config.public(cfg))


@bp.route('/api/settings/active', methods=['POST'])
def set_active_model():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': '请求格式错误'}), 400
    with _SETTINGS_LOCK:
        cfg = config.load_cfg()
        provs, _ = config.normalize(cfg)
        try:
            active = config.set_active(provs, data)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        cfg['providers'] = provs
        cfg['active'] = active
        cfg['llm'] = config.legacy_llm(cfg, provs, active)
        try:
            config.save_cfg(cfg)
        except Exception as e:
            return jsonify({'error': '保存配置失败：%s' % str(e)[:120]}), 500
    return jsonify(config.public(cfg))


@bp.route('/api/settings/test', methods=['POST'])
def test_model():
    data = request.get_json(silent=True) or {}
    cfg = config.load_cfg()
    resolved = config.resolve(cfg, data.get('provider_id'), data.get('model_id'),
                              data.get('reasoning'))
    if not resolved.get('configured'):
        return jsonify({'ok': False, 'error': '该模型未配置 API Key，无法测试'})
    try:
        out = generate.test_connection(resolved['llm'])
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:300]})
    out['ok'] = True
    out['model'] = resolved['meta']
    return jsonify(out)


@bp.route('/api/feedback', methods=['POST'])
def feedback():
    data = request.get_json(silent=True) or {}
    path = paths.feedback_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        'question': str(data.get('question') or '')[:300],
        'helpful': bool(data.get('helpful')),
        'mode': str(data.get('mode') or '')[:20],
        'top_source': str(data.get('top_source') or '')[:120],
    }
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return jsonify({'ok': True})
