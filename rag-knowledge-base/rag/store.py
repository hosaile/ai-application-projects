# -*- coding: utf-8 -*-
"""向量库（Chroma）：建索引、检索、统计。"""
import chromadb

from rag import embed

_client_cache = {}


def _client(chroma_dir):
    if chroma_dir not in _client_cache:
        _client_cache[chroma_dir] = chromadb.PersistentClient(path=chroma_dir)
    return _client_cache[chroma_dir]


def get_collection(chroma_dir, name='kb_v1'):
    return _client(chroma_dir).get_or_create_collection(
        name, metadata={'hnsw:space': 'cosine'})


def rebuild(chunks, chroma_dir, model_name, name='kb_v1', batch=64):
    """重建索引：删除旧集合 → 批量写入（文本 + 向量 + 元数据）。"""
    client = _client(chroma_dir)
    try:
        client.delete_collection(name)
    except Exception:
        pass
    col = client.create_collection(name, metadata={'hnsw:space': 'cosine'})
    total = len(chunks)
    for i in range(0, total, batch):
        part = chunks[i:i + batch]
        vecs = embed.embed([c['text'] for c in part], model_name)
        col.add(
            ids=[c['id'] for c in part],
            documents=[c['text'] for c in part],
            metadatas=[c['meta'] for c in part],
            embeddings=vecs,
        )
    return col.count()


def query(question, top_k, chroma_dir, model_name, name='kb_v1'):
    """向量检索，返回 [{id, text, meta, distance, score}]。"""
    col = get_collection(chroma_dir, name)
    vec = embed.embed_one(question, model_name)
    res = col.query(query_embeddings=[vec], n_results=top_k,
                    include=['documents', 'metadatas', 'distances'])
    hits = []
    ids = res.get('ids') or [[]]
    for i in range(len(ids[0])):
        dist = res['distances'][0][i]
        hits.append({
            'id': ids[0][i],
            'text': res['documents'][0][i],
            'meta': res['metadatas'][0][i],
            'distance': round(float(dist), 4),
            'score': round(1.0 - float(dist), 4),
        })
    return hits


def stats(chroma_dir, name='kb_v1'):
    try:
        col = get_collection(chroma_dir, name)
        return {'count': col.count(), 'name': name}
    except Exception as e:
        return {'count': 0, 'error': str(e)}


def _merge_overlap(prev, nxt, max_overlap=80):
    """拼接相邻块时去掉 50 字重叠，避免同一段步骤出现两遍。"""
    limit = min(max_overlap, len(prev), len(nxt))
    for k in range(limit, 0, -1):
        if prev.endswith(nxt[:k]):
            return nxt[k:]
    return nxt


def section_text(chroma_dir, source, heading, name='kb_v1', limit=1500):
    """取同一章节的所有块并按 seq 拼接，供“检索模式”展示完整段落。"""
    try:
        col = get_collection(chroma_dir, name)
        res = col.get(where={'$and': [{'source': source}, {'heading': heading}]},
                      include=['documents', 'metadatas'])
        pairs = sorted(zip(res.get('metadatas') or [], res.get('documents') or []),
                       key=lambda p: int((p[0] or {}).get('seq', 0)))
        text = ''
        for _, doc in pairs:
            doc = (doc or '').strip()
            if not doc:
                continue
            if not text:
                text = doc
            else:
                text = text + '\n' + _merge_overlap(text, doc).lstrip('\n')
        return text[:limit]
    except Exception:
        return ''
