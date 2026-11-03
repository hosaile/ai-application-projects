# -*- coding: utf-8 -*-
"""中文 Embedding：BAAI/bge-small-zh-v1.5（本地 CPU 推理）。"""
import os
import threading

os.environ.setdefault('USE_TF', '0')          # 绕开 Keras3 与 transformers 的冲突
os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')

_model = None
_model_name = None
_model_lock = threading.Lock()

# 打包/离线时优先使用项目内 models 目录（避免联网下载）
def _find_local_model():
    import sys
    candidates = []
    env = os.environ.get('WORKBENCH_MODELS_DIR')
    if env:
        candidates.append(os.path.join(env, 'bge-small-zh-v1.5'))
        candidates.append(env)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    bundle = getattr(sys, '_MEIPASS', root)
    candidates.append(os.path.join(bundle, 'models', 'bge-small-zh-v1.5'))
    candidates.append(os.path.join(root, 'models', 'bge-small-zh-v1.5'))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[0]


_LOCAL_MODEL = _find_local_model()


def get_model(name='BAAI/bge-small-zh-v1.5'):
    global _model, _model_name
    if _model is None or _model_name != name:
        with _model_lock:                     # 冷启动防并发重复加载
            if _model is None or _model_name != name:
                from sentence_transformers import SentenceTransformer
                path = _LOCAL_MODEL if os.path.isdir(_LOCAL_MODEL) else name
                _model = SentenceTransformer(path)
                _model_name = name
    return _model


def embed(texts, model_name='BAAI/bge-small-zh-v1.5'):
    """返回归一化向量列表（余弦相似度可直接用点积）。"""
    model = get_model(model_name)
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def embed_one(text, model_name='BAAI/bge-small-zh-v1.5'):
    return embed([text], model_name)[0]
