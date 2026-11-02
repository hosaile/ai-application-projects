# -*- coding: utf-8 -*-
"""建立/重建索引：python tools/build_index.py"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rag import ingest, store


def main():
    with open(os.path.join(ROOT, 'config.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    docs_dir = os.path.join(ROOT, cfg['docs_dir'])
    chroma_dir = os.path.join(ROOT, cfg['chroma_dir'])

    docs = ingest.load_docs(docs_dir)
    if not docs:
        print('未找到文档：', docs_dir)
        return 1
    chunks = ingest.build_chunks(docs, cfg['chunk_size'], cfg['chunk_overlap'])
    print('文档 %d 篇 → 切分 %d 块（chunk_size=%d, overlap=%d）'
          % (len(docs), len(chunks), cfg['chunk_size'], cfg['chunk_overlap']))
    n = store.rebuild(chunks, chroma_dir, cfg['embedding_model'])
    print('索引完成：%s 条向量（模型 %s）' % (n, cfg['embedding_model']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
