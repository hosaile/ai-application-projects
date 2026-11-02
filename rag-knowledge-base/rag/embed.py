# -*- coding: utf-8 -*-
"""中文 Embedding：BAAI/bge-small-zh-v1.5（本地 CPU 推理）。"""
import os
import threading

os.environ.setdefault('USE_TF', '0')          # 绕开 Keras3 与 transformers 的冲突
os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')

_model = None
_model_name = None
_model_lock = threading.Lock()


def get_model(name='BAAI/bge-small-zh-v1.5'):
    global _model, _model_name
    if _model is None or _model_name != name:
        with _model_lock:                     # 冷启动防并发重复加载
            if _model is None or _model_name != name:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(name)
                _model_name = name
    return _model


def embed(texts, model_name='BAAI/bge-small-zh-v1.5'):
    """返回归一化向量列表（余弦相似度可直接用点积）。"""
    model = get_model(model_name)
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def embed_one(text, model_name='BAAI/bge-small-zh-v1.5'):
    return embed([text], model_name)[0]
