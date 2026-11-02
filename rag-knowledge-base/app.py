# -*- coding: utf-8 -*-
"""RAG 知识库问答系统 · Flask 服务

启动：python app.py  →  http://127.0.0.1:8100/
"""
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from flask import Flask, jsonify, request

from rag import generate, retrieve, store

app = Flask(__name__, static_folder='static', static_url_path='')
app.json.ensure_ascii = False

CONFIG = os.environ.get('RAG_CONFIG') or os.path.join(ROOT, 'config.json')
FEEDBACK = os.path.join(ROOT, 'data', 'feedback.jsonl')
DOCS_FALLBACK = os.path.join(ROOT, 'data', 'docs')
ALLOWED_EXT = ('.md', '.txt')
MAX_UPLOAD = 2 * 1024 * 1024  # 单文件 2MB
_REBUILD_LOCK = threading.Lock()


def data_root():
    """数据根目录：桌面版用 exe 同级 data（可写）；源码版用项目 data。"""
    return os.environ.get('RAG_DATA_DIR') or os.path.join(ROOT, 'data')


def docs_dir():
    """知识库目录：桌面版固定为 data/docs；源码版读 config.json。"""
    if os.environ.get('RAG_DATA_DIR'):
        return os.path.join(os.environ['RAG_DATA_DIR'], 'docs')
    try:
        cfg = load_cfg()
        return os.path.join(ROOT, cfg.get('docs_dir') or 'data/docs')
    except Exception:
        return DOCS_FALLBACK


def chroma_dir():
    if os.environ.get('RAG_DATA_DIR'):
        return os.path.join(os.environ['RAG_DATA_DIR'], 'chroma')
    return os.path.join(ROOT, load_cfg()['chroma_dir'])


def safe_doc_name(name):
    """只保留文件名，防路径穿越；含路径分隔符或 .. 直接拒绝。"""
    raw = str(name or '')
    if '..' in raw or '/' in raw or '\\' in raw:
        return None
    base = os.path.basename(raw)
    if not base or not base.lower().endswith(ALLOWED_EXT) or len(base) > 80:
        return None
    return base


def load_cfg():
    defaults = {
        'embedding_model': 'BAAI/bge-small-zh-v1.5',
        'chunk_size': 400, 'chunk_overlap': 50, 'top_k': 5,
        'docs_dir': 'data/docs', 'chroma_dir': 'data/chroma',
        'llm': {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash',
                'api_key': '', 'temperature': 0.2, 'max_tokens': 800, 'timeout': 30},
    }
    try:
        with open(CONFIG, encoding='utf-8') as f:
            cfg = json.load(f)
        defaults.update(cfg)
        return defaults
    except Exception:
        return defaults


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/api/ask', methods=['POST'])
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
    cfg = load_cfg()
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
    hits = retrieve.retrieve(search_q, top_k, chroma_dir(), cfg['embedding_model'])
    if hits:
        meta = hits[0].get('meta') or {}
        full = store.section_text(chroma_dir(), meta.get('source', ''), meta.get('heading', ''))
        if full:
            hits[0]['full_text'] = full
    result = generate.answer(q, hits, cfg, history=history)
    result['question'] = q
    result['top_k'] = top_k
    result['hits'] = [{
        'source': h['meta'].get('source', ''),
        'heading': h['meta'].get('heading', ''),
        'score': h.get('score', 0),
        'kw_bonus': h.get('kw_bonus', 0),
        'text': h.get('text', ''),
    } for h in hits]
    return jsonify(result)


@app.route('/api/docs', methods=['GET'])
def list_docs():
    d = docs_dir()
    os.makedirs(d, exist_ok=True)
    items = []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if os.path.isfile(p) and name.lower().endswith(ALLOWED_EXT):
            try:
                # 中文字符约 3 字节，按字节估算字数，避免整文件读入
                chars = max(1, os.path.getsize(p) // 3)
            except Exception:
                chars = 0
            items.append({'name': name, 'chars': chars})
    return jsonify({'docs': items, 'total': len(items)})


@app.route('/api/upload', methods=['POST'])
def upload_docs():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': '没有收到文件'}), 400
    d = docs_dir()
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


@app.route('/api/docs/<name>', methods=['DELETE'])
def delete_doc(name):
    safe = safe_doc_name(name)
    if not safe:
        return jsonify({'error': '文件名不合法'}), 400
    path = os.path.join(docs_dir(), safe)
    if not os.path.exists(path):
        return jsonify({'error': '文件不存在'}), 404
    os.remove(path)
    return jsonify({'ok': True, 'need_rebuild': True})


@app.route('/api/rebuild', methods=['POST'])
def rebuild_index():
    from rag import ingest
    if not _REBUILD_LOCK.acquire(blocking=False):
        return jsonify({'error': '索引重建中，请稍后再试'}), 409
    try:
        cfg = load_cfg()
        docs = ingest.load_docs(docs_dir())
        if not docs:
            return jsonify({'error': '知识库为空'}), 400
        chunks = ingest.build_chunks(docs, cfg['chunk_size'], cfg['chunk_overlap'])
        n = store.rebuild(chunks, chroma_dir(), cfg['embedding_model'])
        return jsonify({'ok': True, 'docs': len(docs), 'chunks': n})
    finally:
        _REBUILD_LOCK.release()


@app.route('/api/stats')
def stats():
    cfg = load_cfg()
    s = store.stats(chroma_dir())
    s['embedding_model'] = cfg['embedding_model']
    s['chunk_size'] = cfg['chunk_size']
    s['chunk_overlap'] = cfg['chunk_overlap']
    s['llm_configured'] = bool((cfg.get('llm') or {}).get('api_key', '').strip())
    return jsonify(s)


@app.route('/api/eval')
def eval_result():
    path = os.path.join(ROOT, 'data', 'tests', 'eval_result.json')
    if not os.path.exists(path):
        return jsonify({'error': '尚未运行评估，请执行 python tools/run_eval.py'}), 404
    with open(path, encoding='utf-8') as f:
        return jsonify(json.load(f))


@app.route('/api/feedback', methods=['POST'])
def feedback():
    data = request.get_json(silent=True) or {}
    os.makedirs(os.path.dirname(FEEDBACK), exist_ok=True)
    record = {
        'question': str(data.get('question') or '')[:300],
        'helpful': bool(data.get('helpful')),
        'mode': str(data.get('mode') or '')[:20],
        'top_source': str(data.get('top_source') or '')[:120],
    }
    with open(FEEDBACK, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return jsonify({'ok': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT') or 8100)
    host = os.environ.get('HOST') or '127.0.0.1'  # 默认仅本机；局域网访问需显式 HOST=0.0.0.0
    print('RAG 知识库问答系统：http://%s:%d/' % (host, port))
    app.run(host=host, port=port, debug=False)
